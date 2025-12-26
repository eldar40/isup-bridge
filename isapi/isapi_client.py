# -*- coding: utf-8 -*-
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import urlsplit

import aiohttp


@dataclass
class DeviceInfo:
    device_id: Optional[str]
    model: Optional[str]


class ISAPIDeviceClient:
    """Hikvision ISAPI client using DigestAuth (per ISAPI documentation)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        logger: logging.Logger,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.host = host
        self.port = port
        self.auth = aiohttp.DigestAuth(username or "", password or "")
        self.base_url = f"http://{host}:{port}"
        self.log = logger
        self._owned_session = session is None
        self.session = session if session else aiohttp.ClientSession()

    async def close(self):
        if self._owned_session and self.session and not self.session.closed:
            await self.session.close()

    async def is_reachable(self) -> bool:
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with self.session.get(url, auth=self.auth, timeout=timeout) as resp:
                return resp.status in (200, 401)
        except Exception as e:  # pragma: no cover - defensive
            self.log.warning("Reachability check failed for %s: %s", self.host, e)
            return False

    async def get_device_info(self) -> Optional[DeviceInfo]:
        url = f"{self.base_url}/ISAPI/System/deviceInfo"
        try:
            async with self.session.get(url, auth=self.auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status >= 400:
                    self.log.error("Failed to read deviceInfo from %s (HTTP %s)", self.host, resp.status)
                    return None
                xml = await resp.text()
        except Exception as e:  # pragma: no cover - defensive
            self.log.error("deviceInfo request failed for %s: %s", self.host, e)
            return None

        try:
            root = ET.fromstring(xml)
            device_id = root.findtext("deviceID")
            model = root.findtext("model")
            return DeviceInfo(device_id=device_id, model=model)
        except Exception as e:  # pragma: no cover - defensive
            self.log.error("Failed to parse deviceInfo for %s: %s", self.host, e)
            return None

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
            async with self.session.put(url, data=payload, auth=self.auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status in (200, 201, 204):
                    self.log.info("HTTP host notification configured for %s", self.host)
                    return True
                body = await resp.text()
                self.log.error("Failed to configure httpHost on %s: HTTP %s → %s", self.host, resp.status, body)
                return False
        except Exception as e:  # pragma: no cover - defensive
            self.log.error("Error configuring httpHost on %s: %s", self.host, e)
            return False

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
            async with self.session.put(url, data=payload, auth=self.auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status in (200, 201, 204):
                    self.log.info("Enabled event types on %s: %s", self.host, ",".join(event_types))
                    return True
                self.log.error("Failed to enable events on %s: HTTP %s", self.host, resp.status)
                return False
        except Exception as e:  # pragma: no cover - defensive
            self.log.error("Error enabling events on %s: %s", self.host, e)
            return False
