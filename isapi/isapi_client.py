# -*- coding: utf-8 -*-
"""
ISAPI Client & Parser

Includes:
- ISAPIEventParser: parses <EventNotificationAlert> XML into normalized ISAPIEvent
- DigestAuth (RFC 7616): minimal production-grade Digest auth helper (qop=auth)
- ISAPIDeviceClient: async client for configuring devices (httpHosts / event trigger, etc.)

Notes:
- Hikvision firmwares commonly require Digest Auth (RFC 7616), Basic is often rejected.
- aiohttp does not provide a stable public DigestAuth helper, so we implement it.
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


# ============================================================================
# ISAPI Event Structures
# ============================================================================

class ISAPIEvent:
    """
    Normalized ISAPI event for downstream processing.
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
        raw_xml: str,
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
            "event_state": self.event_state,
            "device_id": self.device_id,
            "mac": self.mac_address,
            "ip": self.ip_address,
            "timestamp": self.timestamp,
            "card": self.card_number,
            "employee": self.employee_number,
            "door_id": self.door_id,
            "reader_id": self.reader_id,
            "direction": self.direction,
            "major_event_type": self.major_event_type,
            "minor_event_type": self.minor_event_type,
            "success": self.success,
            "images": self.image_ids,
        }


class ISAPIEventParser:
    """
    Parses XML payloads from Hikvision ISAPI notifications.
    Focus: AccessControllerEvent embedded in EventNotificationAlert.
    """
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger("isapi.parser")

    def parse(self, xml_text: str, images: Optional[Dict[str, bytes]] = None) -> Optional[ISAPIEvent]:
        if not xml_text:
            return None

        xml_text = xml_text.strip()
        if not xml_text:
            return None

        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
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
            card_no = access_node.findtext("cardNo") or access_node.findtext("cardNoHex")
            employee_no = access_node.findtext("employeeNo")
            door_id = access_node.findtext("doorID")
            reader_id = access_node.findtext("readerID")
            major_event_type = access_node.findtext("majorEventType")
            minor_event_type = access_node.findtext("minorEventType")

            # Direction heuristic (project-specific)
            try:
                if reader_id:
                    rid = int(reader_id)
                    direction = "IN" if rid % 2 == 1 else "OUT"
            except Exception:
                direction = "UNKNOWN"

            # Success heuristic (adjust according to your event dictionary)
            # Some devices use "1" for success; others use boolean-ish fields.
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
            raw_xml=xml_text,
        )


# ============================================================================
# Digest Auth (RFC 7616)
# ============================================================================

_TOKEN_RE = re.compile(r'(\w+)=(".*?"|[^,]+)')


def _parse_www_authenticate(header_value: str) -> Dict[str, str]:
    """
    Parse: WWW-Authenticate: Digest realm="...", nonce="...", qop="auth", algorithm=MD5, opaque="..."
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
    # fallback
    return hashlib.md5(data.encode("utf-8")).hexdigest()


class DigestAuth:
    """
    RFC 7616 Digest auth helper.
    Supports qop=auth and algorithms: MD5 / MD5-sess / SHA-256 / SHA-256-sess.
    """

    def __init__(self, username: str, password: str, logger: Optional[logging.Logger] = None):
        self.username = username
        self.password = password
        self.log = logger or logging.getLogger("isapi.digest")

        self.realm: Optional[str] = None
        self.nonce: Optional[str] = None
        self.opaque: Optional[str] = None
        self.algorithm: str = "MD5"
        self.qop: Optional[str] = None

        self._nc = 0
        self._cnonce: Optional[str] = None

    def _new_cnonce(self) -> str:
        return os.urandom(8).hex()

    def _select_qop(self, qop_value: Optional[str]) -> Optional[str]:
        if not qop_value:
            return None
        items = [x.strip() for x in qop_value.split(",") if x.strip()]
        if "auth" in items:
            return "auth"
        # Not implementing auth-int by default; can be extended if a device requires it.
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
            self._nc = 0
        return True

    def build_authorization_header(self, method: str, url: str) -> str:
        if not (self.realm and self.nonce):
            raise RuntimeError("DigestAuth not initialized from server challenge")

        parsed = urlparse(url)
        uri = parsed.path or "/"
        if parsed.query:
            uri += "?" + parsed.query

        self._nc += 1
        nc_value = f"{self._nc:08x}"
        cnonce = self._cnonce or self._new_cnonce()
        self._cnonce = cnonce

        qop = self.qop
        alg = self.algorithm

        ha1 = _hash(alg, f"{self.username}:{self.realm}:{self.password}")
        if alg.endswith("-SESS"):
            ha1 = _hash(alg, f"{ha1}:{self.nonce}:{cnonce}")

        ha2 = _hash(alg, f"{method.upper()}:{uri}")

        if qop:
            response = _hash(alg, f"{ha1}:{self.nonce}:{nc_value}:{cnonce}:{qop}:{ha2}")
        else:
            response = _hash(alg, f"{ha1}:{self.nonce}:{ha2}")

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
            items.append(f"qop={qop}")
            items.append(f"nc={nc_value}")
            items.append(f'cnonce="{cnonce}"')

        return "Digest " + ", ".join(items)


# ============================================================================
# ISAPI Device Client
# ============================================================================

@dataclass
class DeviceInfo:
    device_id: Optional[str]
    model: Optional[str]


class ISAPIDeviceClient:
    """
    Async client for Hikvision ISAPI device configuration with RFC7616 Digest.
    Transparent retry on 401 Digest challenge.
    """

    def __init__(self, host: str, port: int, username: str, password: str, logger: Optional[logging.Logger] = None):
        self.host = host
        self.port = int(port)
        self.base_url = f"http://{host}:{self.port}"
        self.log = logger or logging.getLogger("isapi.client")

        self._digest = DigestAuth(username or "", password or "", logger=self.log)
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """
        Performs request with Digest auth retry.
        """
        kwargs.pop("auth", None)  # ensure we don't pass aiohttp auth
        resp = await self.session.request(method, url, **kwargs)
        if resp.status != 401:
            return resp

        www_auth = resp.headers.get("WWW-Authenticate", "")
        await resp.release()

        if not www_auth or "digest" not in www_auth.lower():
            return resp

        if not self._digest.update_from_challenge(www_auth):
            return resp

        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = self._digest.build_authorization_header(method, url)
        kwargs["headers"] = headers

        return await self.session.request(method, url, **kwargs)

    async def is_reachable(self) -> bool:
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            resp = await self._request("GET", url, timeout=aiohttp.ClientTimeout(total=3))
            await resp.release()
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
                    self.log.error("deviceInfo failed %s HTTP %s: %s", self.host, resp.status, body)
                    return None
                xml = await resp.text()
        except Exception as e:
            self.log.error("deviceInfo request error %s: %s", self.host, e)
            return None

        try:
            root = ET.fromstring(xml)
            return DeviceInfo(
                device_id=root.findtext("deviceID"),
                model=root.findtext("model"),
            )
        except Exception as e:
            self.log.error("deviceInfo parse error %s: %s", self.host, e)
            return None

    # ---------------------------------------------------------------------
    # httpHost configuration
    # ---------------------------------------------------------------------

    def build_http_host_payload(self, callback_url: str, host_id: int = 1) -> str:
        parsed = urlsplit(callback_url)
        ip_addr = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

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
""".strip()
        return payload

    async def configure_http_host(self, callback_url: str, host_id: int = 1) -> bool:
        url = f"{self.base_url}/ISAPI/Event/notification/httpHosts/{host_id}"
        payload = self.build_http_host_payload(callback_url, host_id)

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
                    self.log.info("Configured httpHost on %s (id=%s)", self.host, host_id)
                    return True
                body = await resp.text()
                self.log.error("Configure httpHost failed %s HTTP %s: %s", self.host, resp.status, body)
                return False
        except Exception as e:
            self.log.error("Configure httpHost error %s: %s", self.host, e)
            return False

    # ---------------------------------------------------------------------
    # Event trigger enabling
    # ---------------------------------------------------------------------

    def build_event_subscription_payload(self, event_types: Sequence[str], host_id: int = 1) -> str:
        entries = []
        for idx, evt in enumerate(event_types, start=1):
            entries.append(
                f"""
  <EventTriggerNotification>
    <id>{idx}</id>
    <eventType>{evt}</eventType>
    <eventDescription>auto</eventDescription>
    <protocolType>HTTP</protocolType>
    <httpHostId>{host_id}</httpHostId>
    <triggerState>true</triggerState>
  </EventTriggerNotification>
""".rstrip()
            )

        payload = f"""
<EventTriggerNotificationList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
{''.join(entries)}
</EventTriggerNotificationList>
""".strip()
        return payload

    async def enable_events(self, event_types: Sequence[str], host_id: int = 1) -> bool:
        url = f"{self.base_url}/ISAPI/Event/notification/trigger"
        payload = self.build_event_subscription_payload(event_types, host_id)

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
                    self.log.info("Enabled events on %s: %s", self.host, ",".join(event_types))
                    return True
                body = await resp.text()
                self.log.error("Enable events failed %s HTTP %s: %s", self.host, resp.status, body)
                return False
        except Exception as e:
            self.log.error("Enable events error %s: %s", self.host, e)
            return False
