import logging
from typing import List

from isapi.isapi_client import ISAPIDeviceClient


class ISAPIDeviceManager:
    def __init__(self, cfg: dict, logger: logging.Logger):
        self.cfg = cfg
        self.log = logger
        self.clients: List[ISAPIDeviceClient] = []

    async def auto_configure_terminals(self, callback_base_url: str):
        callback_path = self.cfg.get("isapi", {}).get("webhook_path", "/isapi/webhook")
        callback_url = f"{callback_base_url.rstrip('/')}{callback_path}"
        event_types = self.cfg.get("isapi", {}).get("event_types", ["accessControllerEvent"])

        for obj in self.cfg.get("objects", []):
            for term in obj.get("terminals", []):
                host = term.get("ip") or term.get("host")
                port = term.get("port", 80)
                username = term.get("username", "admin")
                password = term.get("password", "")

                client = ISAPIDeviceClient(host, port, username, password, self.log)
                self.clients.append(client)

                if not await client.is_reachable():
                    self.log.warning("Device %s is not reachable, skip auto configure", host)
                    continue

                await client.configure_http_host(callback_url)
                await client.enable_events(event_types)

    async def close(self):
        for c in self.clients:
            await c.close()
