# -*- coding: utf-8 -*-
"""
Tenant Manager — управление организациями (тенантами), настройками интеграции 1С,
маппинг устройств/терминалов по MAC или device_id.
"""

import logging
import aiohttp
from typing import Dict, Optional


class Tenant:
    """
    Представляет одного заказчика / объект / организацию.
    """
    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params

        # 1C configuration
        self.api_url = params.get("api_url")
        self.api_token = params.get("api_token")

        # Local caches
        self.employee_cache: Dict[str, dict] = {}

    def __repr__(self):
        return f"Tenant({self.name})"


# ======================================================================
# TENANT MANAGER
# ======================================================================

class TenantManager:

    def __init__(self, cfg: dict):
        """
        cfg → полный config.yaml.
        """
        self.log = logging.getLogger("TenantManager")

        self.tenants: Dict[str, Tenant] = {}
        self.devices_by_mac: Dict[str, str] = {}

        self._load(cfg)

    # ------------------------------------------------------------------
    # Load config
    # ------------------------------------------------------------------

    def _load(self, cfg: dict):
        tenant_cfg = cfg.get("tenants", {})
        terminal_cfg = cfg.get("terminals", [])

        # Создаём тенантов
        for name, params in tenant_cfg.items():
            self.tenants[name] = Tenant(name, params)

        # Маппинг MAC → tenant
        for t in terminal_cfg:
            mac = (t.get("mac") or "").upper()
            tenant = t.get("tenant")
            if mac and tenant:
                self.devices_by_mac[mac] = tenant

        self.log.info(f"Loaded {len(self.tenants)} tenants, {len(self.devices_by_mac)} terminal bindings")

    # ------------------------------------------------------------------
    # Tenant resolution
    # ------------------------------------------------------------------

    def find_tenant_by_mac(self, mac: str) -> Optional[Tenant]:
        if not mac:
            return None
        name = self.devices_by_mac.get(mac.upper())
        if name:
            return self.tenants.get(name)
        return None

    # Fallback variant
    def get_tenant(self, name: str) -> Optional[Tenant]:
        return self.tenants.get(name)

    # ------------------------------------------------------------------
    # Send event to 1C
    # ------------------------------------------------------------------

    async def send_to_1c(self, tenant: Tenant, event: dict) -> bool:
        """
        Формат отправки в 1С (унифицированный POST):
        {
            "employee": "...",
            "card_number": "...",
            "timestamp": "...",
            "direction": "IN/OUT",
            "success": true/false,
            "device": "...",
            "source": "ISUP/ISAPI"
        }
        """

        if not tenant.api_url:
            self.log.error(f"Tenant {tenant.name} has no 1C API URL configured")
            return False

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {tenant.api_token}"
        }

        url = tenant.api_url

        payload = {
            "employee": event.get("employee_number") or event.get("user_id"),
            "card": event.get("card_number"),
            "timestamp": event.get("timestamp"),
            "direction": event.get("direction"),
            "success": event.get("success"),
            "device": event.get("device_id"),
            "raw": event.get("raw_xml") or event.get("raw_binary"),
            "source": event.get("event_source"),
        }

        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=headers, timeout=5) as r:
                    if r.status in (200, 201, 204):
                        self.log.debug(f"Event delivered to 1C ({tenant.name})")
                        return True
                    else:
                        text = await r.text()
                        self.log.error(f"1C refused event ({tenant.name}): HTTP {r.status} → {text}")
                        return False

        except Exception as e:
            self.log.error(f"Failed to send event to 1C for {tenant.name}: {e}")
            return False

    # ------------------------------------------------------------------
    # Enrich event with tenant data
    # ------------------------------------------------------------------

    def enrich_event(self, tenant: Tenant, event: dict) -> dict:
        """
        Добавляет tenant_name и другие расширения.
        """
        event["tenant"] = tenant.name
        return event