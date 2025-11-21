import logging
import re
from typing import Callable, Dict, List, Optional

from isapi.isapi_client import ISAPIDeviceClient


class ISAPIDeviceManager:
    """Configures Hikvision terminals automatically using ISAPI."""

    EVENT_TYPES: List[str] = [
        "faceMatch",
        "cardSwipe",
        "qrCode",
        "AccessGranted",
        "AccessDenied",
        "MinorEvent",
        "CaptureUpload",
    ]

    def __init__(
        self,
        cfg: dict,
        logger: logging.Logger,
        client_factory: Optional[Callable[[Dict], ISAPIDeviceClient]] = None,
    ):
        self.cfg = cfg
        self.log = logger
        self.isapi_cfg = cfg.get("isapi", {})
        self.terminals = self._collect_terminals(cfg)
        self.client_factory = client_factory or self._default_client_factory

    # ------------------------------------------------------------------
    def _collect_terminals(self, cfg: dict) -> List[Dict]:
        terminals: List[Dict] = []
        for obj in cfg.get("objects", []):
            for terminal in obj.get("terminals", []):
                entry = terminal.copy()
                entry.setdefault("object_id", obj.get("object_id"))
                terminals.append(entry)
        return terminals

    def _default_client_factory(self, terminal: Dict) -> ISAPIDeviceClient:
        username = terminal.get("username") or self.isapi_cfg.get("username", "")
        password = terminal.get("password") or self.isapi_cfg.get("password", "")
        port = terminal.get("isapi_port") or terminal.get("port") or 80
        return ISAPIDeviceClient(terminal.get("ip_address"), int(port), username, password, self.log)

    # ------------------------------------------------------------------
    async def auto_configure_terminals(self, webhook_base: str):
        callback_base = (webhook_base or "").rstrip("/")
        callback_url = f"{callback_base}/hikvision/callback"

        for terminal in self.terminals:
            terminal_id = terminal.get("terminal_id") or terminal.get("id") or "unknown"
            ip_address = terminal.get("ip_address")
            if not ip_address:
                self.log.warning("Terminal %s missing IP address, skipping", terminal_id)
                continue

            self.log.info("Configuring terminal %s at %s", terminal_id, ip_address)
            client = self.client_factory(terminal)

            if not await client.is_reachable():
                self.log.warning("Terminal %s unreachable, skipping auto configuration", terminal_id)
                continue

            info = await client.get_device_info()
            if not info or not info.device_id:
                self.log.error("Unable to read deviceInfo for %s", terminal_id)
                continue

            if not self._is_valid_device_id(info.device_id):
                self.log.error("Invalid deviceID %s for terminal %s", info.device_id, terminal_id)
                continue

            await client.configure_http_host(callback_url)
            await client.enable_events(self.EVENT_TYPES)
            self.log.info("Enabled event types: %s", ", ".join(self.EVENT_TYPES))

    # ------------------------------------------------------------------
    def _is_valid_device_id(self, device_id: str) -> bool:
        if not device_id:
            return False
        return bool(re.fullmatch(r"[A-Fa-f0-9]{8,32}", device_id))

