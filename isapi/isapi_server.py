# -*- coding: utf-8 -*-
"""
ISAPI Webhook Server — продакшен-версия с "толерантным" multipart-парсингом для Hikvision.

Ключевые фиксы:
- Multipart parsing: XML часть может прийти без Content-Type (Hikvision/прошивки встречаются такие кейсы).
  Достаём XML по:
  - Content-Disposition.name (если похоже на XML-метаданные)
  - сигнатурам XML в payload (<?xml, <EventNotificationAlert, и т.п.)
- Heartbeat/Keep-Alive: пустые multipart-пакеты / пустое тело — это валидно, отвечаем 200 OK без Error-логов.
"""

import logging
from typing import Dict, Optional, List, Tuple

from aiohttp import web

from isapi.isapi_client import ISAPIEventParser, ISAPIEvent


# ============================================================
# Low-level multipart parser (robust for Hikvision quirks)
# ============================================================

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
            k = k.strip().lower()
            v = v.strip().strip('"')
            params[k] = v
    return mime, params


def _parse_content_disposition(value: str) -> Dict[str, str]:
    """
    Parse Content-Disposition like:
      form-data; name="EventNotificationAlert"; filename="img.jpg"
    """
    if not value:
        return {}
    out: Dict[str, str] = {}
    parts = [p.strip() for p in value.split(";") if p.strip()]
    # parts[0] is disposition type (form-data)
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip().strip('"')
    return out


def _looks_like_xml(data: bytes) -> bool:
    s = data.lstrip()
    if not s:
        return False
    # tolerate BOM / whitespace
    if s.startswith(b"\xef\xbb\xbf"):
        s = s[3:].lstrip()
    return (
        s.startswith(b"<?xml")
        or s.startswith(b"<EventNotificationAlert")
        or b"<EventNotificationAlert" in s[:4096]
        or (s.startswith(b"<") and b"</" in s[:4096])
    )


def _guess_filename(cd_params: Dict[str, str], fallback: str) -> str:
    fn = cd_params.get("filename")
    if fn:
        return fn
    name = cd_params.get("name")
    if name:
        # name может быть UUID/pid или contentID; для файла это ок как имя
        return f"{name}"
    return fallback


def _robust_parse_multipart_formdata(body: bytes, boundary: str) -> List[Tuple[Dict[str, str], bytes]]:
    """
    Very tolerant multipart/form-data parser.

    Returns list of (headers, payload) per part.
    - headers keys are lowercase
    - payload excludes trailing CRLF
    """
    if not boundary:
        return []

    bnd = boundary.encode("utf-8", errors="ignore")
    delim = b"--" + bnd
    end_delim = b"--" + bnd + b"--"

    # Fast path: if body contains only boundary markers / whitespace -> treat as empty
    if not body or not body.strip():
        return []

    # Split by delimiter; RFC: body begins with --boundary
    chunks = body.split(delim)
    parts: List[Tuple[Dict[str, str], bytes]] = []

    for chunk in chunks:
        if not chunk:
            continue

        # chunk can start with -- (closing) or \r\n
        if chunk.startswith(b"--"):
            # closing marker or end
            continue

        # strip leading CRLF
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]

        # strip possible final end marker artifacts
        if chunk.endswith(b"\r\n"):
            # keep one strip; we will also strip payload trailing CRLF later
            pass

        # Each part: headers until \r\n\r\n, then payload until CRLF + next boundary
        header_end = chunk.find(b"\r\n\r\n")
        if header_end == -1:
            # No headers; could be heartbeat/garbage. Ignore as non-fatal.
            continue

        header_blob = chunk[:header_end].decode("utf-8", errors="replace")
        payload = chunk[header_end + 4 :]

        # payload may end with \r\n (before next boundary); strip one trailing CRLF
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]

        headers: Dict[str, str] = {}
        for line in header_blob.split("\r\n"):
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

        parts.append((headers, payload))

    # Remove empty tail parts (common for heartbeats)
    parts = [(h, p) for (h, p) in parts if p and p.strip()]
    return parts


# ============================================================
# ISAPI Webhook Handler
# ============================================================

class ISAPIWebhookHandler:
    """
    Обработка HTTP POST запросов от терминалов Hikvision по ISAPI.
    """

    def __init__(self, processor, secret_token: Optional[str] = None, logger: Optional[logging.Logger] = None):
        self.processor = processor
        self.secret_token = secret_token
        self.log = logger or logging.getLogger("ISAPIWebhookHandler")
        self.xml_parser = ISAPIEventParser(self.log)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        client_ip = request.remote or "unknown"
        content_type_raw = request.headers.get("Content-Type", "")
        mime, params = _parse_content_type_header(content_type_raw)

        # Optional shared-secret authentication for webhook (not Digest; webhook is inbound)
        if self.secret_token:
            if request.headers.get("X-Webhook-Secret") != self.secret_token:
                self.log.warning("Unauthorized webhook request from %s", client_ip)
                return web.json_response({"status": "unauthorized"}, status=401)

        # Heartbeat: some devices send empty body with or without headers
        if request.content_length in (0, None):
            # For chunked transfer it can be None; still check actual data
            raw = await request.read()
            if not raw or not raw.strip():
                self.log.debug("Received empty request (heartbeat) from %s", client_ip)
                return web.Response(status=200, text="OK")
            # if not empty, continue processing by mime detection
            body = raw
        else:
            body = await request.read()
            if not body or not body.strip():
                self.log.debug("Received empty request (heartbeat) from %s", client_ip)
                return web.Response(status=200, text="OK")

        if mime.startswith("multipart/"):
            return await self._handle_multipart_bytes(body, params.get("boundary", ""), client_ip)

        # XML-only payloads
        if "xml" in mime or body.lstrip().startswith(b"<") or _looks_like_xml(body):
            try:
                xml_text = body.decode("utf-8", errors="replace")
            except Exception:
                xml_text = body.decode(errors="replace")
            if not xml_text.strip():
                self.log.debug("Received empty XML packet (heartbeat) from %s", client_ip)
                return web.Response(status=200, text="OK")
            return await self._process_event(xml_text, images=None, client_ip=client_ip)

        # Unsupported but non-empty
        self.log.warning("Unsupported content type '%s' from %s", content_type_raw, client_ip)
        return web.json_response({"status": "error", "message": "unsupported content type"}, status=400)

    async def _handle_multipart_bytes(self, body: bytes, boundary: str, client_ip: str) -> web.StreamResponse:
        """
        Robust multipart handler:
        - tolerates missing/incorrect part Content-Type
        - tolerates multipart heartbeat frames (empty parts)
        """
        if not boundary:
            # Some buggy clients omit boundary; treat as heartbeat if body doesn't contain xml
            if not body.strip() or not _looks_like_xml(body):
                self.log.debug("Multipart without boundary treated as heartbeat from %s", client_ip)
                return web.Response(status=200, text="OK")
            # last resort: try to parse as XML
            xml_text = body.decode("utf-8", errors="replace")
            return await self._process_event(xml_text, images=None, client_ip=client_ip)

        parts = _robust_parse_multipart_formdata(body, boundary)

        # Heartbeat: body had only boundary markers or empty parts
        if not parts:
            self.log.debug("Received empty multipart (heartbeat) from %s", client_ip)
            return web.Response(status=200, text="OK")

        xml_data: Optional[str] = None
        images: Dict[str, bytes] = {}

        # Heuristics: find XML part and image parts
        for headers, payload in parts:
            ct = (headers.get("content-type") or "").lower()
            cd = headers.get("content-disposition") or ""
            cd_params = _parse_content_disposition(cd)
            part_name = (cd_params.get("name") or "").lower()
            filename = _guess_filename(cd_params, "blob.bin")

            # 1) XML by explicit Content-Type
            if "xml" in ct or ct in ("text/xml", "application/xml"):
                if payload and payload.strip():
                    xml_data = payload.decode("utf-8", errors="replace")
                    continue

            # 2) XML by name hint (часто у Hikvision метаданные идут как отдельная form-data часть)
            #    даже если Content-Type отсутствует/неверный
            if payload and payload.strip():
                if ("event" in part_name) or ("notification" in part_name) or ("alert" in part_name):
                    if _looks_like_xml(payload):
                        xml_data = payload.decode("utf-8", errors="replace")
                        continue

            # 3) XML by sniffing payload signature
            if payload and payload.strip() and _looks_like_xml(payload):
                xml_data = payload.decode("utf-8", errors="replace")
                continue

            # Images: explicit content-type OR filename extension hints
            if payload and payload.strip():
                if ct.startswith("image/") or filename.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
                    images[filename] = payload
                    continue

        # If we extracted nothing meaningful -> treat as heartbeat (do not error-log)
        if not xml_data and not images:
            self.log.debug("Multipart contained no XML/images (heartbeat) from %s", client_ip)
            return web.Response(status=200, text="OK")

        # If images exist but xml missing: Hikvision event payload should contain metadata;
        # but do not create retry loop: accept with 200 and log at WARNING (not ERROR).
        if not xml_data:
            self.log.warning("Multipart received with images but no XML from %s (accepted to avoid retry loop)", client_ip)
            return web.Response(status=200, text="OK")

        return await self._process_event(xml_data, images=images, client_ip=client_ip)

    async def _process_event(self, xml_data: str, images: Optional[Dict[str, bytes]], client_ip: str) -> web.StreamResponse:
        event: Optional[ISAPIEvent] = self.xml_parser.parse(xml_data, images)
        if not event:
            # Это не heartbeat: есть тело, но XML не распарсили.
            # Возвращаем 400, но логируем WARNING (а не ERROR), чтобы не захламлять прод.
            self.log.warning("Failed to parse ISAPI XML from %s", client_ip)
            return web.json_response({"status": "parse_error"}, status=400)

        ok = await self.processor.process_isapi_event(event.to_dict(), client_ip)
        return web.json_response({"status": "success" if ok else "error"})


# ============================================================
# ISAPI Webhook Server (aiohttp)
# ============================================================

class ISAPIWebhookServer:
    def __init__(self, handler: ISAPIWebhookHandler, cfg: dict, logger: Optional[logging.Logger] = None):
        self.cfg = cfg.get("isapi", {})
        self.host = self.cfg.get("host", "0.0.0.0")
        self.port = self.cfg.get("port", 8002)
        self.handler = handler
        self.log = logger or logging.getLogger("ISAPIWebhookServer")
        self.runner: Optional[web.AppRunner] = None
        self.http_server: Optional[web.TCPSite] = None
        self.app: Optional[web.Application] = None

    async def start(self):
        self.log.info("Starting ISAPI Webhook Server on %s:%s", self.host, self.port)

        self.app = web.Application()

        # Webhook endpoint (per config)
        path = self.cfg.get("webhook_path", "/ISAPI/Event/notification/alert")
        self.app.router.add_post(path, self.handler.handle)

        # Fallback route
        self.app.router.add_post("/", self.handler.handle)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.http_server = web.TCPSite(self.runner, self.host, self.port)
        await self.http_server.start()

        self.log.info("ISAPI Webhook server started")

    async def start_api(self, host="0.0.0.0", port=8081):
        """
        Отдельный health-check endpoint.
        """
        app = web.Application()

        async def health(_):
            return web.json_response({"status": "ok"})

        app.router.add_get("/health", health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        self.log.info("API /health started on %s:%s", host, port)

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
        self.log.info("ISAPI Webhook server stopped")


# ============================================================
# ISAPI Terminal Manager
# ============================================================

class ISAPITerminalManager:
    """
    Управляет терминалами и проверяет принадлежность устройства тенанту.
    """

    def __init__(self, cfg: dict):
        self.terminals: List[Dict] = cfg.get("terminals", [])
        self.tenant_map = self._map_by_mac()

    def _map_by_mac(self) -> Dict[str, str]:
        mapping = {}
        for t in self.terminals:
            mac = t.get("mac")
            tenant = t.get("tenant")
            if mac:
                mapping[mac.upper()] = tenant
        return mapping

    def get_tenant_by_mac(self, mac: str) -> Optional[str]:
        if not mac:
            return None
        return self.tenant_map.get(mac.upper())
