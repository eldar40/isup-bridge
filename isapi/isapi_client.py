# -*- coding: utf-8 -*-
"""
ISAPI Client & Parser — полная версия с парсингом событий и Digest Auth.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Dict, Any
from urllib.parse import urlsplit

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

        # Access fields
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
            self.log.error(f"ISAPI XML parse error: {e}")
            return None

        # Base fields
        event_type = root.findtext("eventType") or "unknown"
        event_state = root.findtext("eventState") or "unknown"
        device_id = root.findtext("deviceID")
        mac_address = root.findtext("macAddress")
        ip_address = root.findtext("ipAddress")
        timestamp = root.findtext("dateTime") or datetime.now().isoformat()

        # Fallback device ID to MAC if needed
        device_id_final = mac_address or device_id or "unknown"

        # Access node parsing
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

            # Direction mapping (Hikvision logic: 1=IN, 2=OUT usually, or odd/even reader)
            try:
                if reader_id:
                    rid = int(reader_id)
                    direction = "IN" if rid % 2 == 1 else "OUT"
                else:
                    direction = "UNKNOWN"
            except Exception:
                direction = "UNKNOWN"

            # Success mapping according to ISAPI spec (minor=1 usually means success/normal)
            success = (minor_event_type == "1")

        # Images extracted by the multipart parser
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
# ISAPI Device HTTP client
# ============================================================

@dataclass
class DeviceInfo:
    device_id: Optional[str]
    model: Optional[str]


class ISAPIDeviceClient:
    """Lightweight async client for configuring Hikvision terminals over ISAPI."""

    def __init__(self, host: str, port: int, username: str, password: str, logger: logging.Logger):
        self.host = host
        self.port = port
        # ВАЖНО: ISAPI требует Digest Auth
        self.auth = aiohttp.DigestAuth(username or "", password or "")  # type: ignore
        self.base_url = f"http://{host}:{port}"
        self.log = logger
        self._owned_session = True
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self._owned_session and self.session and not self.session.closed:
            await self.session.close()

    async def is_reachable(self) -> bool:
        """Checks whether device responds to System/deviceInfo request."""
        if not self.session:
             return False
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with self.session.get(url, auth=self.auth, timeout=timeout) as resp:
                # 401 is also "reachable" (just needs auth handshake)
                return resp.status < 400
        except Exception:
            return False

    async def get_device_info(self) -> Optional[DeviceInfo]:
        if not self.session: return None
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            async with self.session.get(url, auth=self.auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status >= 400:
                    self.log.error("Failed to read deviceInfo from %s (HTTP %s)", self.host, resp.status)
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
        if not self.session: return False
        payload = self.build_http_host_payload(callback_url, host_id)
        url = f"{self.base_url}/ISAPI/Event/notification/httpHosts/{host_id}"
        try:
            async with self.session.put(url, data=payload, auth=self.auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
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
            f"    </EventTriggerNotification>" for idx, evt in enumerate(event_types)
        )
        payload = f"""
<EventTriggerNotificationList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
{entries}
</EventTriggerNotificationList>
"""
        return payload.strip()

    async def enable_events(self, event_types: Sequence[str], host_id: int = 1) -> bool:
        if not self.session: return False
        payload = self.build_event_subscription_payload(event_types, host_id)
        url = f"{self.base_url}/ISAPI/Event/notification/trigger"
        try:
            async with self.session.put(url, data=payload, auth=self.auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status in (200, 201, 204):
                    self.log.info("Enabled event types on %s: %s", self.host, ",".join(event_types))
                    return True
                self.log.error("Failed to enable events on %s: HTTP %s", self.host, resp.status)
                return False
        except Exception as e:
            self.log.error("Error enabling events on %s: %s", self.host, e)
            return False