# -*- coding: utf-8 -*-
"""
Event Processor — единый центр обработки событий ISUP/ISAPI.
Преобразует события, определяет tenant, отправляет в 1С,
сохраняет неудачные события, выполняет повторные попытки.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional


class EventProcessor:

    def __init__(
        self,
        tenant_manager,
        terminal_manager,
        storage,
        metrics,
        logger: logging.Logger,
        isup_parser
    ):
        self.tenant_manager = tenant_manager
        self.terminal_manager = terminal_manager
        self.storage = storage
        self.metrics = metrics
        self.log = logger
        self.isup_parser = isup_parser

        self.retry_interval = 10  # seconds

    # =====================================================================
    # PUBLIC METHODS
    # =====================================================================

    async def process_isapi_event(self, event: Dict[str, Any], client_ip: str) -> bool:
        """
        События от Hikvision терминалов (лицо, карта, QR).
        Получены через ISAPI Webhook.
        """

        event["event_source"] = "ISAPI"
        event["client_ip"] = client_ip
        event["timestamp"] = normalize_ts(event.get("timestamp"))

        # Mac used to identify tenant
        mac = event.get("mac_address")
        tenant = self.tenant_manager.find_tenant_by_mac(mac)

        if not tenant:
            self.log.error(f"❌ Unknown device with MAC {mac}. Cannot map to tenant.")
            return False

        event = self.tenant_manager.enrich_event(tenant, event)

        success = await self.tenant_manager.send_to_1c(tenant, event)

        if success:
            self.metrics.events_ok += 1
            return True
        else:
            # save to pending queue
            await self.storage.save_pending(event)
            self.metrics.events_failed += 1
            return False

    async def enqueue_event(self, event: Dict[str, Any]) -> None:
        """Push callback-parsed event into the unified processing pipeline."""

        normalized = self._normalize_callback_event(event)
        await self.process_isapi_event(normalized, event.get("client_ip", "callback"))

    # =====================================================================

    async def process_isup_packet(self, packet: bytes, client_ip: str) -> bool:
        """
        Обработка бинарного ISUP пакета от турникета/контроллера.
        """

        event = self.isup_parser.parse(packet)
        if not event:
            self.log.warning("ISUP parse returned None (skip)")
            return False

        # Convert parsed object to dict
        unified = self._unify_isup_event(event, client_ip)

        mac_or_device = unified.get("device_id")

        tenant = self.tenant_manager.find_tenant_by_mac(mac_or_device)
        if not tenant:
            self.log.error(f"Unknown ISUP device: {mac_or_device}")
            return False

        unified = self.tenant_manager.enrich_event(tenant, unified)

        success = await self.tenant_manager.send_to_1c(tenant, unified)

        if success:
            self.metrics.events_ok += 1
            return True
        else:
            await self.storage.save_pending(unified)
            self.metrics.events_failed += 1
            return False

    # =====================================================================
    # UNIFICATION METHODS
    # =====================================================================

    def _unify_isup_event(self, event, client_ip: str) -> Dict[str, Any]:
        """
        Преобразует ISUPAccessEvent → единый формат как ISAPI.
        """

        return {
            "event_source": "ISUP",
            "device_id": event.header.device_id,
            "timestamp": event.timestamp.isoformat(),
            "card_number": event.card_number,
            "employee_number": event.user_id,
            "direction": event.direction.name,
            "success": bool(event.verify_result == 1),
            "raw_binary": event.raw_packet.hex(),
            "door_id": event.door_number,
            "reader_id": event.reader_number,
            "client_ip": client_ip,
        }

    def _normalize_callback_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(event)
        normalized["event_source"] = normalized.get("event_source") or "HIKVISION_CALLBACK"
        normalized["timestamp"] = (
            normalized.get("timestamp")
            or normalized.get("eventDateTime")
            or normalized.get("dateTime")
        )

        device_id = normalized.get("deviceID") or normalized.get("device_id")
        if device_id and "device_id" not in normalized:
            normalized["device_id"] = device_id

        if not normalized.get("mac_address") and device_id:
            normalized["mac_address"] = device_id

        major = normalized.get("majorEventType") or normalized.get("major_event_type")
        minor = normalized.get("minorEventType") or normalized.get("minor_event_type")
        if major:
            normalized["major_event_type"] = major
        if minor:
            normalized["minor_event_type"] = minor

        if "picData" in normalized and isinstance(normalized["picData"], (bytes, bytearray)):
            normalized["image_buffer"] = normalized["picData"]
        return normalized

    # =====================================================================
    # RETRY PENDING EVENTS
    # =====================================================================

    async def retry_pending_events(self):
        """
        Бесконечно повторяет отправку накопившихся событий.
        """
        self.log.info("♻️ Pending-event retry loop started")

        while True:
            try:
                pending = await self.storage.load_all()

                if not pending:
                    await asyncio.sleep(self.retry_interval)
                    continue

                self.log.info(f"Retrying {len(pending)} pending events...")

                for ev in pending:
                    tenant_name = ev.get("tenant")
                    tenant = self.tenant_manager.get_tenant(tenant_name)

                    if not tenant:
                        self.log.error(f"No tenant {tenant_name} for pending event, skipping")
                        continue

                    ok = await self.tenant_manager.send_to_1c(tenant, ev)

                    if ok:
                        await self.storage.remove(ev)
                        self.metrics.events_retried_ok += 1
                    else:
                        self.metrics.events_retried_fail += 1

            except Exception as e:
                self.log.error(f"Retry pending error: {e}")

            await asyncio.sleep(self.retry_interval)


# =====================================================================
# Helpers
# =====================================================================

def normalize_ts(value: Optional[str]) -> str:
    """
    Нормализует timestamp из ISAPI формата.
    """
    if not value:
        return datetime.now().isoformat()

    try:
        # Example: 2024-09-12T14:23:10+08:00
        return value
    except Exception:
        return datetime.now().isoformat()