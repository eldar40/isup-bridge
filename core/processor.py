import json
import logging

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.storage import EventStorage


class EventProcessor:
    def __init__(self, tenant_manager, terminal_manager, storage: EventStorage, metrics, logger, isup_parser):
        self.tm = tenant_manager
        self.terminal_manager = terminal_manager
        self.storage = storage
        self.metrics = metrics
        self.log = logger
        self.isup_parser = isup_parser

    async def process_isup_packet(self, raw_packet: bytes, ip: str):
        event = self.isup_parser.parse(raw_packet)
        self.metrics.events_received += 1
        if event:
            self.metrics.events_parsed += 1
            self.metrics.last_event_time = event.timestamp
            await self._dispatch_event(
                {
                    "source": "ISUP",
                    "device_id": event.header.device_id,
                    "ip": ip,
                    "timestamp": event.timestamp.isoformat(),
                    "card": event.card_number,
                    "direction": event.direction.name,
                    "result": event.verify_result,
                }
            )
        else:
            self.log.warning("Failed to parse ISUP packet from %s", ip)

    async def process_isapi_event(self, event: dict, ip: str):
        await self._dispatch_event(
            {
                "source": "ISAPI",
                **event,
                "ip": ip,
            }
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError),
    )
    async def _send_to_1c(self, url: str, payload: dict, auth):
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, auth=auth) as resp:
                if resp.status >= 400:
                    txt = await resp.text()
                    self.log.error("1C Error %s: %s", resp.status, txt)
                    resp.raise_for_status()
                self.metrics.events_sent_to_1c += 1

    async def _dispatch_event(self, event_data: dict):
        # TODO: improve matching logic between device/IP and tenant
        tenant = next(iter(self.tm.tenants.values()), None)
        if not tenant:
            self.log.error("No tenant found for event")
            return

        c1_cfg = tenant.get("c1", {})
        url = f"{c1_cfg.get('base_url')}{c1_cfg.get('endpoint')}"
        auth = aiohttp.BasicAuth(c1_cfg.get("username"), c1_cfg.get("password"))

        try:
            await self._send_to_1c(url, event_data, auth)
            self.log.info("Event sent to 1C: %s", event_data)
        except Exception as e:
            self.metrics.events_failed += 1
            self.log.error("Failed to send to 1C, saving to storage: %s", e)
            await self.storage.save_event(event_data, tenant.get("object_id", "unknown"))

    async def retry_pending_events(self):
        self.log.info("Checking pending events...")
        files = await self.storage.get_pending_events()
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as file:
                    event = json.load(file)
                await self._dispatch_event(event)
                await self.storage.delete_event(f)
            except Exception as e:  # pragma: no cover - defensive
                self.log.error("Retry failed for %s: %s", f, e)
