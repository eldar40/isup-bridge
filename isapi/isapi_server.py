# -*- coding: utf-8 -*-
"""
ISAPI Webhook Server — полностью переписанная продакшен-версия.
Поддерживает multipart/form-data, XML, автонастройку устройств.
"""

import asyncio
import aiohttp
from aiohttp import web
import logging
from typing import Dict, Any, Optional

from isapi.isapi_client import ISAPIEventParser


# ============================================================
# ISAPI Webhook Handler
# ============================================================

class ISAPIWebhookHandler:
    """
    Обработка HTTP POST запросов от терминалов Hikvision по ISAPI.
    """

    def __init__(self, processor, secret_token: str = None, logger: logging.Logger = None):
        self.processor = processor
        self.secret_token = secret_token
        self.log = logger or logging.getLogger("ISAPIWebhookHandler")
        self.xml_parser = ISAPIEventParser(self.log)

    # --------------------------------------------------------
    # Entry for aiohttp server
    # --------------------------------------------------------

    async def handle(self, request: web.Request):
        client_ip = request.remote
        content_type = request.headers.get("Content-Type", "").lower()

        # Optional authentication
        if self.secret_token:
            if request.headers.get("X-Webhook-Secret") != self.secret_token:
                self.log.warning(f"Unauthorized webhook request from {client_ip}")
                return web.json_response({"status": "unauthorized"}, status=401)

        # ----------- Multipart (с картинками) ----------------
        if "multipart" in content_type:
            return await self._handle_multipart(request, client_ip)

        # ----------- XML only --------------------------------
        if "xml" in content_type:
            body = await request.text()
            return await self._handle_xml(body, client_ip)

        self.log.warning(f"Unsupported content type: {content_type} from {client_ip}")
        return web.json_response({"status": "error", "message": "unsupported content type"}, status=400)

    # --------------------------------------------------------
    # Multipart processing
    # --------------------------------------------------------

    async def _handle_multipart(self, request: web.Request, client_ip: str):
        reader = await request.multipart()

        xml_data = None
        images = {}

        while True:
            part = await reader.next()
            if not part:
                break

            ctype = part.headers.get("Content-Type", "")

            # XML block
            if "xml" in ctype.lower():
                xml_data = await part.text()

            # JPEG block
            elif "jpeg" in ctype.lower() or "jpg" in ctype.lower():
                filename = part.filename or "image.jpg"
                data = await part.read()
                images[filename] = data

        if not xml_data:
            return web.json_response({"status": "error", "message": "xml not found"}, status=400)

        return await self._process_event(xml_data, images, client_ip)

    # --------------------------------------------------------
    # XML processing
    # --------------------------------------------------------

    async def _handle_xml(self, xml_data: str, client_ip: str):
        return await self._process_event(xml_data, None, client_ip)

    # --------------------------------------------------------
    # Unified event processing
    # --------------------------------------------------------

    async def _process_event(self, xml_data: str, images: Optional[Dict[str, bytes]], client_ip: str):
        event = self.xml_parser.parse(xml_data, images)
        if not event:
            self.log.error("Failed to parse ISAPI XML")
            return web.json_response({"status": "parse_error"}, status=400)

        ok = await self.processor.process_isapi_event(event.to_dict(), client_ip)
        return web.json_response({"status": "success" if ok else "error"})


# ============================================================
# ISAPI Webhook Server (aiohttp)
# ============================================================

class ISAPIWebhookServer:
    def __init__(self, handler: ISAPIWebhookHandler, cfg: dict, logger: logging.Logger):
        self.cfg = cfg.get("isapi", {})
        self.host = self.cfg.get("host", "0.0.0.0")
        self.port = self.cfg.get("port", 8082)
        self.handler = handler
        self.log = logger
        self.runner = None
        self.http_server = None
        self.app = None

    async def start(self):
        self.log.info(f"🌐 Запуск ISAPI Webhook Server на {self.host}:{self.port}")

        self.app = web.Application()

        # Webhook endpoint
        self.app.router.add_post("/", self.handler.handle)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.http_server = web.TCPSite(self.runner, self.host, self.port)
        await self.http_server.start()

        self.log.info("ISAPI Webhook server started")

    async def start_api(self, host="0.0.0.0", port=8081):
        """
        Отдельный health-check / metrics endpoint.
        """
        app = web.Application()

        async def health(_):
            return web.json_response({"status": "ok"})

        app.router.add_get("/health", health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        self.log.info(f"📊 API /health запущено на {host}:{port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
        self.log.info("ISAPI Webhook server stopped")


# ============================================================
# ISAPI Device Manager
# ============================================================

class ISAPIDeviceManager:
    """
    Управление устройствами Hikvision:
    - проверка активации
    - включение событий
    - регистрация webhooks
    """
    def __init__(self, cfg: dict, logger: logging.Logger):
        self.cfg = cfg.get("isapi", {})
        self.terminals = cfg.get("terminals", [])
        self.log = logger

    # --------------------------------------------------------
    # Activation check
    # --------------------------------------------------------

    async def check_activation(self, ip: str) -> bool:
        url = f"http://{ip}/SDK/activateStatus"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=3) as r:
                    text = await r.text()
                    return "<activated>true</activated>" in text
        except Exception:
            return False

    # --------------------------------------------------------
    # Register webhook URL
    # --------------------------------------------------------

    async def configure_webhook(self, ip: str, webhook_url: str) -> bool:
        """
        Конфигурирует push-event нотификацию:
        POST /ISAPI/Event/notification/subscribe
        """
        payload = f"""
        <EventNotificationAlert>
            <id>1</id>
            <addressingMode>ipaddress</addressingMode>
            <ipAddress>{webhook_url}</ipAddress>
            <protocolType>HTTP</protocolType>
            <httpAuthentication>anonymous</httpAuthentication>
            <eventTypes>
                <eventType>AccessControllerEvent</eventType>
            </eventTypes>
        </EventNotificationAlert>
        """

        url = f"http://{ip}/ISAPI/Event/notification/subscribe"

        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(url, data=payload, timeout=5) as r:
                    ok = (r.status in (200, 201, 204))
                    if ok:
                        self.log.info(f"Webhook for {ip} set to {webhook_url}")
                    else:
                        text = await r.text()
                        self.log.error(f"Webhook configure failed for {ip}: {text}")
                    return ok
        except Exception as e:
            self.log.error(f"Webhook configure error: {ip} → {e}")
            return False

    # --------------------------------------------------------
    # Auto configure
    # --------------------------------------------------------

    async def auto_configure_terminals(self, webhook_base: str):
        """
        Автоматически настраивает все терминалы из config.yaml.
        """
        for t in self.terminals:
            ip = t.get("ip")
            if not ip:
                continue

            webhook_url = f"{webhook_base}/"
            activated = await self.check_activation(ip)

            if not activated:
                self.log.warning(f"⚠️ Терминал {ip} не активирован (activateStatus=false)")
                continue

            ok = await self.configure_webhook(ip, webhook_url)
            if ok:
                self.log.info(f"Терминал {ip} настроен успешно")
            else:
                self.log.error(f"Не удалось настроить терминал {ip}")


# ============================================================
# ISAPI Terminal Manager
# ============================================================

class ISAPITerminalManager:
    """
    Управляет терминалами и проверяет принадлежность устройства тенанту.
    """

    def __init__(self, cfg: dict):
        self.terminals = cfg.get("terminals", [])
        self.tenant_map = self._map_by_mac()

    def _map_by_mac(self):
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