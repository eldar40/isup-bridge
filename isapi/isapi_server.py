# -*- coding: utf-8 -*-
"""
ISAPI Webhook Server (aiohttp)

Key behaviors for Hikvision ISAPI inbound notifications:
- Devices send multipart/form-data for /ISAPI/Event/notification/alert
- XML part (<EventNotificationAlert>) may come WITHOUT Content-Type: application/xml
- Devices can send heartbeat/keep-alive multipart frames with empty parts
- Devices can send image-only multipart frames (valid in some firmwares / modes)
  => must NOT be treated as error or flood WARN logs
- Server must always reply 200 OK for heartbeats and non-actionable frames
  to avoid device retry loops.

This server:
- Robustly parses multipart with tolerant header separators (\r\n\r\n OR \n\n)
- Detects XML by part name/disposition OR payload sniffing
- Falls back to scanning raw body for <EventNotificationAlert> ... </EventNotificationAlert>
- Caches last XML per client IP for correlating image-only frames
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple, Any

from aiohttp import web

from isapi.isapi_client import ISAPIEventParser, ISAPIEvent


# ============================================================================
# Helpers: headers parsing
# ============================================================================

def _parse_content_type_header(value: str) -> Tuple[str, Dict[str, str]]:
    """
    Parse Content-Type header into (mime, params).
    Example: 'multipart/form-data; boundary=abc' -> ('multipart/form-data', {'boundary': 'abc'})
    """
    if not value:
        return "", {}
    parts = [p.strip() for p in value.split(";") if p.strip()]
    mime = parts[0].lower() if parts else ""
    params: Dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().lower()] = v.strip().strip('"')
    return mime, params


def _parse_content_disposition(value: str) -> Dict[str, str]:
    """
    Parse Content-Disposition:
      form-data; name="EventNotificationAlert"; filename="img.jpg"
    Returns dict keys lowercased.
    """
    if not value:
        return {}
    out: Dict[str, str] = {}
    parts = [p.strip() for p in value.split(";") if p.strip()]
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip().strip('"')
    return out


def _strip_leading_noise(data: bytes) -> bytes:
    """
    Hikvision sometimes prefixes XML with NULs/BOM/whitespace.
    We strip common leading control bytes (safe for XML detection only).
    """
    if not data:
        return b""
    s = data
    s = s.lstrip(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f \t\r\n")
    if s.startswith(b"\xef\xbb\xbf"):
        s = s[3:].lstrip()
    return s


def _looks_like_xml(data: bytes) -> bool:
    if not data:
        return False
    s = _strip_leading_noise(data)
    if not s:
        return False
    return (
        s.startswith(b"<?xml")
        or s.startswith(b"<EventNotificationAlert")
        or b"<EventNotificationAlert" in s[:8192]
        or (s.startswith(b"<") and b"</" in s[:8192])
    )


def _guess_filename(cd_params: Dict[str, str], fallback: str = "blob.bin") -> str:
    if cd_params.get("filename"):
        return cd_params["filename"]
    if cd_params.get("name"):
        return cd_params["name"]
    return fallback


# ============================================================================
# Robust multipart parser (tolerant)
# ============================================================================

def _robust_parse_multipart_formdata(body: bytes, boundary: str) -> List[Tuple[Dict[str, str], bytes]]:
    """
    Very tolerant multipart/form-data parser.

    Returns list of (headers, payload)
      - headers keys are lowercased
      - payload is raw bytes (without trailing CRLF)
    Supports:
      - header separator: \r\n\r\n OR \n\n
      - header lines: \r\n OR \n
    """
    if not boundary or not body:
        return []

    bnd = boundary.encode("utf-8", errors="ignore")
    delim = b"--" + bnd

    chunks = body.split(delim)
    parts: List[Tuple[Dict[str, str], bytes]] = []

    for chunk in chunks:
        if not chunk:
            continue

        # Closing marker or preamble can start with '--'
        if chunk.startswith(b"--"):
            continue

        # Strip leading newlines
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]

        # Find end of headers
        header_end = chunk.find(b"\r\n\r\n")
        sep_len = 4
        if header_end == -1:
            header_end = chunk.find(b"\n\n")
            sep_len = 2
        if header_end == -1:
            # No headers => heartbeat/empty frame or malformed; ignore
            continue

        header_blob = chunk[:header_end].decode("utf-8", errors="replace")
        payload = chunk[header_end + sep_len:]

        # Strip one trailing CRLF/LF that precedes boundary
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        elif payload.endswith(b"\n"):
            payload = payload[:-1]

        headers: Dict[str, str] = {}
        for line in header_blob.replace("\r\n", "\n").split("\n"):
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

        parts.append((headers, payload))

    # Filter out empty payload parts (heartbeat frames)
    parts = [(h, p) for (h, p) in parts if p and p.strip()]
    return parts


def _extract_xml_from_raw_body(body: bytes) -> Optional[str]:
    """
    Fallback: scan raw body for <EventNotificationAlert ... </EventNotificationAlert>.
    Handles NUL bytes by removing them for scanning only (does not damage binary payload usage elsewhere).
    """
    if not body:
        return None
    try:
        scan = body.replace(b"\x00", b"")
        start = scan.find(b"<EventNotificationAlert")
        if start == -1:
            return None
        end = scan.find(b"</EventNotificationAlert>", start)
        if end == -1:
            return None
        end = end + len(b"</EventNotificationAlert>")
        return scan[start:end].decode("utf-8", errors="replace")
    except Exception:
        return None


# ============================================================================
# Correlation cache for image-only frames
# ============================================================================

@dataclass
class _LastEventCacheItem:
    xml: str
    ts: float


class _LastEventCache:
    """
    Keep last XML per client IP for correlating image-only multipart frames.
    TTL prevents unbounded growth / stale associations.
    """
    def __init__(self, ttl_seconds: int = 30):
        self._ttl = ttl_seconds
        self._items: Dict[str, _LastEventCacheItem] = {}

    def set(self, client_ip: str, xml: str):
        self._items[client_ip] = _LastEventCacheItem(xml=xml, ts=time.time())

    def get(self, client_ip: str) -> Optional[str]:
        item = self._items.get(client_ip)
        if not item:
            return None
        if (time.time() - item.ts) > self._ttl:
            self._items.pop(client_ip, None)
            return None
        return item.xml

    def cleanup(self):
        now = time.time()
        dead = [k for k, v in self._items.items() if (now - v.ts) > self._ttl]
        for k in dead:
            self._items.pop(k, None)


# ============================================================================
# ISAPI Webhook Handler
# ============================================================================

class ISAPIWebhookHandler:
    """
    Handles Hikvision ISAPI inbound POST notifications.
    """

    def __init__(
        self,
        processor,
        secret_token: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        cache_ttl_seconds: int = 30,
    ):
        self.processor = processor
        self.secret_token = secret_token
        self.log = logger or logging.getLogger("isapi.webhook")
        self.xml_parser = ISAPIEventParser(self.log)
        self.last_xml_cache = _LastEventCache(ttl_seconds=cache_ttl_seconds)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        client_ip = request.remote or "unknown"
        content_type_raw = request.headers.get("Content-Type", "")
        mime, params = _parse_content_type_header(content_type_raw)

        # Optional shared-secret header (not Digest; webhook is inbound)
        if self.secret_token:
            if request.headers.get("X-Webhook-Secret") != self.secret_token:
                self.log.warning("Unauthorized webhook request from %s", client_ip)
                return web.json_response({"status": "unauthorized"}, status=401)

        # Read request body (POST per event is common; some firmwares keep-alive with empty body)
        body = await request.read()
        if not body or not body.strip():
            # Heartbeat / empty frame
            self.log.debug("Heartbeat/empty request from %s", client_ip)
            return web.Response(status=200, text="OK")

        if mime.startswith("multipart/"):
            boundary = params.get("boundary", "")
            return await self._handle_multipart_bytes(body, boundary, client_ip)

        # Non-multipart: sometimes device sends pure XML
        if ("xml" in mime) or _looks_like_xml(body):
            xml_text = body.decode("utf-8", errors="replace").strip()
            if not xml_text:
                self.log.debug("Empty XML (heartbeat) from %s", client_ip)
                return web.Response(status=200, text="OK")
            return await self._process_event(xml_text, images=None, client_ip=client_ip)

        self.log.debug("Unsupported content type '%s' from %s", content_type_raw, client_ip)
        return web.json_response({"status": "error", "message": "unsupported content type"}, status=400)

    async def _handle_multipart_bytes(self, body: bytes, boundary: str, client_ip: str) -> web.StreamResponse:
        """
        Multipart handler tolerant to missing/incorrect per-part headers.
        """
        # If boundary missing: treat as heartbeat unless raw XML is detectable
        if not boundary:
            xml_fallback = _extract_xml_from_raw_body(body) if _looks_like_xml(body) else None
            if xml_fallback:
                return await self._process_event(xml_fallback, images=None, client_ip=client_ip)
            self.log.debug("Multipart without boundary treated as heartbeat from %s", client_ip)
            return web.Response(status=200, text="OK")

        parts = _robust_parse_multipart_formdata(body, boundary)

        # Heartbeat: boundary-only or empty parts
        if not parts:
            self.log.debug("Empty multipart (heartbeat) from %s", client_ip)
            return web.Response(status=200, text="OK")

        xml_data: Optional[str] = None
        images: Dict[str, bytes] = {}

        for headers, payload in parts:
            ct = (headers.get("content-type") or "").lower()
            cd = headers.get("content-disposition") or ""
            cd_params = _parse_content_disposition(cd)
            part_name = (cd_params.get("name") or "").lower()
            filename = _guess_filename(cd_params, "blob.bin")

            # XML by explicit content-type
            if "xml" in ct or ct in ("text/xml", "application/xml"):
                if payload and payload.strip():
                    xml_data = payload.decode("utf-8", errors="replace")
                    continue

            # XML by name hint + sniffing
            if payload and payload.strip():
                if any(x in part_name for x in ("event", "notification", "alert", "metadata", "xml")):
                    if _looks_like_xml(payload):
                        xml_data = payload.decode("utf-8", errors="replace")
                        continue

            # XML by payload sniffing
            if payload and payload.strip() and _looks_like_xml(payload):
                xml_data = payload.decode("utf-8", errors="replace")
                continue

            # Images by content-type or filename hint
            if payload and payload.strip():
                if ct.startswith("image/") or filename.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
                    images[filename] = payload
                    continue

        # If still no XML, try raw fallback scan
        if not xml_data:
            xml_data = _extract_xml_from_raw_body(body)

        # If we got XML, cache it (for correlating next image-only frame)
        if xml_data:
            self.last_xml_cache.set(client_ip, xml_data)
            return await self._process_event(xml_data, images=images if images else None, client_ip=client_ip)

        # No XML:
        # - This can be heartbeat or image-only stream chunk. Both are VALID and must return 200.
        if images:
            # Try to correlate with last XML (if recent)
            last_xml = self.last_xml_cache.get(client_ip)
            if last_xml:
                # Attach images to the last event XML
                self.log.debug(
                    "Image-only multipart from %s correlated with last XML (images=%d)",
                    client_ip,
                    len(images),
                )
                return await self._process_event(last_xml, images=images, client_ip=client_ip)

            # Otherwise accept silently (debug only)
            self.log.debug(
                "Image-only multipart from %s without XML and no cached XML (images=%d) -> accepted",
                client_ip,
                len(images),
            )
            return web.Response(status=200, text="OK")

        # Neither XML nor images -> heartbeat/empty multipart frame
        self.log.debug("Multipart contained no actionable parts from %s -> accepted", client_ip)
        return web.Response(status=200, text="OK")

    async def _process_event(self, xml_data: str, images: Optional[Dict[str, bytes]], client_ip: str) -> web.StreamResponse:
        event: Optional[ISAPIEvent] = self.xml_parser.parse(xml_data, images)
        if not event:
            # Body was non-empty but XML parse failed — warn, but avoid ERROR spam.
            self.log.warning("Failed to parse ISAPI XML from %s", client_ip)
            return web.json_response({"status": "parse_error"}, status=400)

        try:
            ok = await self.processor.process_isapi_event(event.to_dict(), client_ip)
        except Exception as e:
            self.log.exception("Processor failed for event from %s: %s", client_ip, e)
            # Still respond 200 to avoid device retry storms; your processor can requeue internally.
            return web.json_response({"status": "accepted"}, status=200)

        return web.json_response({"status": "success" if ok else "accepted"}, status=200)


# ============================================================================
# ISAPI Webhook Server (aiohttp)
# ============================================================================

class ISAPIWebhookServer:
    def __init__(self, handler: ISAPIWebhookHandler, cfg: dict, logger: Optional[logging.Logger] = None):
        self.cfg = cfg.get("isapi", {}) if isinstance(cfg, dict) else {}
        self.host = self.cfg.get("host", "0.0.0.0")
        self.port = int(self.cfg.get("port", 8002))
        self.handler = handler
        self.log = logger or logging.getLogger("isapi.server")
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.app: Optional[web.Application] = None

    async def start(self):
        self.app = web.Application()
        path = self.cfg.get("webhook_path", "/ISAPI/Event/notification/alert")
        self.app.router.add_post(path, self.handler.handle)
        # optional fallback:
        self.app.router.add_post("/", self.handler.handle)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        self.log.info("ISAPI Webhook server started on %s:%s", self.host, self.port)

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
        self.log.info("ISAPI Webhook server stopped")

    async def start_api(self, host="0.0.0.0", port=8081):
        """
        Minimal health endpoint.
        """
        api = web.Application()

        async def health(_):
            return web.json_response({"status": "ok"})

        api.router.add_get("/health", health)

        runner = web.AppRunner(api)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        self.log.info("Health endpoint started on %s:%s", host, port)


# ============================================================================
# Optional: terminal manager (kept for compatibility)
# ============================================================================

class ISAPITerminalManager:
    def __init__(self, cfg: dict):
        self.terminals: List[Dict[str, Any]] = cfg.get("terminals", []) if isinstance(cfg, dict) else []
        self.tenant_map = self._map_by_mac()

    def _map_by_mac(self) -> Dict[str, Optional[str]]:
        mapping: Dict[str, Optional[str]] = {}
        for t in self.terminals:
            mac = (t.get("mac") or "").upper()
            tenant = t.get("tenant")
            if mac:
                mapping[mac] = tenant
        return mapping

    def get_tenant_by_mac(self, mac: str) -> Optional[str]:
        return self.tenant_map.get((mac or "").upper())
