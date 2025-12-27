# -*- coding: utf-8 -*-
"""
ISAPI Client & Parser — версия для Python 3.10+ с корректным Digest Auth (RFC 7616) и парсингом событий.

Важно:
- Hikvision ISAPI требует Digest Authentication и ссылается на RFC 7616 :contentReference[oaicite:3]{index=3}
- aiohttp не предоставляет готовый DigestAuth как часть публичного API.
  Поэтому реализуем Digest (qop=auth) сами и делаем transparent retry на 401 challenge.
"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Dict, Any, Tuple
from urllib.parse import urlsplit, urlparse

import aiohttp
import xml.etree.ElementTree as ET


# ============================================================
# ISAPI Event Structures
# ============================================================

@dataclass
class ISAPIEvent:
    """
    Унифицированная структура ISAPI-события.
    """
    def __init__(
        self,
        event_type: str,
        event_state: str,
        device_id: str,
        mac_address: Optional[str],
        ip_address: Optional[str],
        timestamp: str,
        card_number: Optional[str],
        employee_number: Optional[str],
        door_id: Optional[str],
        reader_id: Optional[str],
        direction: str,
        major_event_type: Optional[str],
        minor_event_type: Optional[str],
        success: bool,
        image_ids: Optional[list],
        raw_xml: str
    ):
        self.event_type = event_type
        self.event_state = event_state
        self.device_id = device_id
        self.mac_address = mac_address
        self.ip_address = ip_address
        self.timestamp = timestamp

        self.card_number = card_number
        self.employee_number = employee_number
        self.door_id = door_id
        self.reader_id = reader_id
        self.direction = direction

        self.major_event_type = major_event_type
        self.minor_event_type = minor_event_type
        self.success = success

        self.image_ids = image_ids or []
        self.raw_xml = raw_xml

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "card": self.card_number,
            "employee": self.employee_number,
            "door_id": self.door_id,
            "reader_id": self.reader_id,
            "direction": self.direction,
            "success": self.success
        }


class ISAPIEventParser:
    """
    Разбор XML <EventNotificationAlert> в единый формат ISAPIEvent.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger("ISAPIEventParser")

    def parse(self, xml_text: str, images=None) -> Optional[ISAPIEvent]:
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            # Ошибка парсинга — это не обязательно "авария", устройства иногда шлют неожиданные payload.
            self.log.warning("ISAPI XML parse error: %s", e)
            return None

        event_type = root.findtext("eventType") or "unknown"
        event_state = root.findtext("eventState") or "unknown"
        device_id = root.findtext("deviceID")
        mac_address = root.findtext("macAddress")
        ip_address = root.findtext("ipAddress")
        timestamp = root.findtext("dateTime") or datetime.now().isoformat()

        device_id_final = mac_address or device_id or "unknown"

        access_node = root.find("AccessControllerEvent")

        card_no = None
        employee_no = None
        door_id = None
        reader_id = None
        major_event_type = None
        minor_event_type = None
        direction = "UNKNOWN"
        success = False

        if access_node is not None:
            card_no = access_node.findtext("cardNo")
            employee_no = access_node.findtext("employeeNo")
            door_id = access_node.findtext("doorID")
            reader_id = access_node.findtext("readerID")
            major_event_type = access_node.findtext("majorEventType")
            minor_event_type = access_node.findtext("minorEventType")

            # Direction heuristic: often odd=IN, even=OUT for some deployments.
            try:
                if reader_id:
                    rid = int(reader_id)
                    direction = "IN" if rid % 2 == 1 else "OUT"
            except Exception:
                direction = "UNKNOWN"

            # Success heuristic (project-specific; adjust to your event dictionary if needed)
            success = (minor_event_type == "1")

        image_ids = list(images.keys()) if images else []

        return ISAPIEvent(
            event_type=event_type,
            event_state=event_state,
            device_id=device_id_final,
            mac_address=mac_address,
            ip_address=ip_address,
            timestamp=timestamp,
            card_number=card_no,
            employee_number=employee_no,
            door_id=door_id,
            reader_id=reader_id,
            direction=direction,
            major_event_type=major_event_type,
            minor_event_type=minor_event_type,
            success=success,
            image_ids=image_ids,
            raw_xml=xml_text
        )


# ============================================================
# Digest Auth (RFC 7616) — minimal, production-friendly
# ============================================================

_TOKEN_RE = re.compile(r'(\w+)=(".*?"|[^,]+)')

def _parse_www_authenticate(header_value: str) -> Dict[str, str]:
    """
    Parse WWW-Authenticate: Digest realm="...", nonce="...", qop="auth", algorithm=MD5, opaque="..."
    """
    if not header_value:
        return {}
    hv = header_value.strip()
    if hv.lower().startswith("digest "):
        hv = hv[7:]
    out: Dict[str, str] = {}
    for m in _TOKEN_RE.finditer(hv):
        k = m.group(1).lower()
        v = m.group(2).strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k] = v
    return out


def _hash(algorithm: str, data: str) -> str:
    algo = (algorithm or "MD5").upper()
    if algo in ("MD5", "MD5-SESS"):
        return hashlib.md5(data.encode("utf-8")).hexdigest()
    if algo in ("SHA-256", "SHA-256-SESS"):
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
    # Fallback to MD5 for compatibility
    return hashlib.md5(data.encode("utf-8")).hexdigest()


class DigestAuth:
    """
    RFC 7616 Digest auth helper.

    Поддержка:
    - algorithm: MD5 / MD5-sess / SHA-256 / SHA-256-sess
    - qop: auth (наиболее типично для Hikvision)
    """

    def __init__(self, username: str, password: str, logger: Optional[logging.Logger] = None):
        self.username = username
        self.password = password
        self.log = logger or logging.getLogger("DigestAuth")

        self.realm: Optional[str] = None
        self.nonce: Optional[str] = None
        self.opaque: Optional[str] = None
        self.algorithm: str = "MD5"
        self.qop: Optional[str] = None

        self._nc = 0  # nonce-count
        self._cnonce = None

    def _new_cnonce(self) -> str:
        return os.urandom(8).hex()

    def _select_qop(self, qop_value: Optional[str]) -> Optional[str]:
        if not qop_value:
            return None
        # could be: "auth,auth-int"
        items = [x.strip() for x in qop_value.split(",") if x.strip()]
        if "auth" in items:
            return "auth"
        # not implementing auth-int here; can be added if device requires it
        return items[0] if items else None

    def update_from_challenge(self, www_authenticate: str) -> bool:
        params = _parse_www_authenticate(www_authenticate)
        if not params or "nonce" not in params or "realm" not in params:
            return False

        stale = (params.get("stale") or "").lower() == "true"

        self.realm = params.get("realm")
        self.nonce = params.get("nonce")
        self.opaque = params.get("opaque")
        self.algorithm = (params.get("algorithm") or "MD5").upper()
        self.qop = self._select_qop(params.get("qop"))

        if stale:
            # nonce is stale, reset nonce count
            self._nc = 0

        return True

    def build_authorization_header(self, method: str, url: str) -> str:
        if not (self.realm and self.nonce):
            raise RuntimeError("DigestAuth not initialized with challenge")

        parsed = urlparse(url)
        uri = parsed.path or "/"
        if parsed.query:
            uri += "?" + parsed.query

        self._nc += 1
        nc_value = f"{self._nc:08x}"
        cnonce = self._cnonce or self._new_cnonce()
        self._cnonce = cnonce

        qop = self.qop  # usually "auth"
        alg = self.algorithm

        ha1 = _hash(alg, f"{self.username}:{self.realm}:{self.password}")
        if alg.endswith("-SESS"):
            ha1 = _hash(alg, f"{ha1}:{self.nonce}:{cnonce}")

        ha2 = _hash(alg, f"{method}:{uri}")

        if qop:
            response = _hash(alg, f"{ha1}:{self.nonce}:{nc_value}:{cnonce}:{qop}:{ha2}")
        else:
            response = _hash(alg, f"{ha1}:{self.nonce}:{ha2}")

        # Compose header
        # Note: RFC 7616 allows token/quoted-string. We quote most fields for compatibility.
        items = [
            f'username="{self.username}"',
            f'realm="{self.realm}"',
            f'nonce="{self.nonce}"',
            f'uri="{uri}"',
            f'response="{response}"',
        ]
        if self.opaque:
            items.append(f'opaque="{self.opaque}"')
        if alg:
            items.append(f"algorithm={alg}")
        if qop:
            items.append(f'qop={qop}')
            items.append(f'nc={nc_value}')
            items.append(f'cnonce="{cnonce}"')

        return "Digest " + ", ".join(items)


# ============================================================
# ISAPI Device HTTP client
# ============================================================

@dataclass
class DeviceInfo:
    device_id: Optional[str]
    model: Optional[str]


class ISAPIDeviceClient:
    """
    Lightweight async client for configuring Hikvision terminals over ISAPI with RFC7616 Digest.

    Strategy:
    - First request may return 401 with WWW-Authenticate: Digest ...
    - We parse challenge and transparently retry with Authorization header.
    """

    def __init__(self, host: str, port: int, username: str, password: str, logger: logging.Logger):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.log = logger

        self._digest = DigestAuth(username or "", password or "", logger=self.log)
        self._owned_session = True
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self._owned_session and self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """
        Perform request with Digest auth retry.
        Returns aiohttp response (caller must read body inside context OR ensure closing).
        """
        # Ensure we don't pass aiohttp auth kwarg (we do Digest ourselves)
        kwargs.pop("auth", None)

        # First attempt (may 401)
        resp = await self.session.request(method, url, **kwargs)
        if resp.status != 401:
            return resp

        www_auth = resp.headers.get("WWW-Authenticate", "")
        await resp.release()

        if not www_auth or "digest" not in www_auth.lower():
            return resp  # not digest challenge

        if not self._digest.update_from_challenge(www_auth):
            return resp

        # Retry with Authorization header
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = self._digest.build_authorization_header(method.upper(), url)
        kwargs["headers"] = headers

        return await self.session.request(method, url, **kwargs)

    async def is_reachable(self) -> bool:
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            resp = await self._request("GET", url, timeout=timeout)
            await resp.release()
            # 401 после ретрая тоже возможно при неверных кредах; но сеть/хост reachable
            return resp.status < 500
        except Exception:
            return False

    async def get_device_info(self) -> Optional[DeviceInfo]:
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            resp = await self._request("GET", url, timeout=aiohttp.ClientTimeout(total=5))
            async with resp:
                if resp.status >= 400:
                    body = await resp.text()
                    self.log.error("Failed to read deviceInfo from %s (HTTP %s): %s", self.host, resp.status, body)
                    return None
                xml = await resp.text()
        except Exception as e:
            self.log.error("deviceInfo request failed for %s: %s", self.host, e)
            return None

        try:
            root = ET.fromstring(xml)
            device_id = root.findtext("deviceID")
            model = root.findtext("model")
            return DeviceInfo(device_id=device_id, model=model)
        except Exception as e:
            self.log.error("Failed to parse deviceInfo for %s: %s", self.host, e)
            return None

    # ----------------------------------------------------------------
    # HTTP host configuration
    # ----------------------------------------------------------------
    def build_http_host_payload(self, callback_url: str, host_id: int = 1) -> str:
        parsed = urlsplit(callback_url)
        ip_addr = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"

        payload = f"""
<HttpHostNotification version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
    <id>{host_id}</id>
    <enabled>true</enabled>
    <addressingFormatType>ipaddress</addressingFormatType>
    <ipAddress>{ip_addr}</ipAddress>
    <portNo>{port}</portNo>
    <protocolType>HTTP</protocolType>
    <url>{path}</url>
    <httpAuthenticationMethod>digest</httpAuthenticationMethod>
</HttpHostNotification>
"""
        return payload.strip()

    async def configure_http_host(self, callback_url: str, host_id: int = 1) -> bool:
        payload = self.build_http_host_payload(callback_url, host_id)
        url = f"{self.base_url}/ISAPI/Event/notification/httpHosts/{host_id}"
        try:
            resp = await self._request(
                "PUT",
                url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": 'application/xml; charset="UTF-8"'},
                timeout=aiohttp.ClientTimeout(total=5),
            )
            async with resp:
                if resp.status in (200, 201, 204):
                    self.log.info("HTTP host notification configured for %s", self.host)
                    return True
                body = await resp.text()
                self.log.error("Failed to configure httpHost on %s: HTTP %s → %s", self.host, resp.status, body)
                return False
        except Exception as e:
            self.log.error("Error configuring httpHost on %s: %s", self.host, e)
            return False

    # ----------------------------------------------------------------
    # Event enabling
    # ----------------------------------------------------------------
    def build_event_subscription_payload(self, event_types: Sequence[str], host_id: int = 1) -> str:
        entries = "\n".join(
            f"    <EventTriggerNotification>\n"
            f"        <id>{idx + 1}</id>\n"
            f"        <eventType>{evt}</eventType>\n"
            f"        <eventDescription>auto</eventDescription>\n"
            f"        <protocolType>HTTP</protocolType>\n"
            f"        <httpHostId>{host_id}</httpHostId>\n"
            f"        <triggerState>true</triggerState>\n"
            f"    </EventTriggerNotification>"
            for idx, evt in enumerate(event_types)
        )
        payload = f"""
<EventTriggerNotificationList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
{entries}
</EventTriggerNotificationList>
"""
        return payload.strip()

    async def enable_events(self, event_types: Sequence[str], host_id: int = 1) -> bool:
        payload = self.build_event_subscription_payload(event_types, host_id)
        url = f"{self.base_url}/ISAPI/Event/notification/trigger"
        try:
            resp = await self._request(
                "PUT",
                url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": 'application/xml; charset="UTF-8"'},
                timeout=aiohttp.ClientTimeout(total=5),
            )
            async with resp:
                if resp.status in (200, 201, 204):
                    self.log.info("Enabled event types on %s: %s", self.host, ",".join(event_types))
                    return True
                body = await resp.text()
                self.log.error("Failed to enable events on %s: HTTP %s → %s", self.host, resp.status, body)
                return False
        except Exception as e:
            self.log.error("Error enabling events on %s: %s", self.host, e)
            return False
