# -*- coding: utf-8 -*-
"""
ISAPI Webhook Server — полностью переписанная продакшен-версия.
"""

import asyncio
from aiohttp import web
import logging
from typing import Dict, Any, Optional, List

from isapi.isapi_client import ISAPIEventParser, ISAPIEvent


# ============================================================
# ISAPI Webhook Handler
# ============================================================

class ISAPIWebhookHandler:
    """
    Обработка HTTP POST запросов от терминалов Hikvision по ISAPI.
    """

    def __init__(self, processor, secret_token: Optional[str] = None, logger: Optional[logging.Logger] = None):
        self.processor = processor
        self.secret_token = secret_token
        self.log = logger or logging.getLogger("ISAPIWebhookHandler")
        self.xml_parser = ISAPIEventParser(self.log)

    # --------------------------------------------------------
    # Entry for aiohttp server
    # --------------------------------------------------------

    async def handle(self, request: web.Request):
        client_ip = request.remote or "unknown"
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
            return await self._handle_xml(request, client_ip)

        # ----------- Generic Heartbeat (пустые пакеты) -------
        # Иногда устройства шлют пустые пакеты без явного content-type или с другим
        raw_body = await request.text()
        if not raw_body or not raw_body.strip():
            self.log.debug(f"Received empty heartbeat packet from {client_ip}")
            return web.Response(status=200, text="OK")

        self.log.warning(f"Unsupported content type: {content_type} from {client_ip}")
        return web.json_response({"status": "error", "message": "unsupported content type"}, status=400)

    # --------------------------------------------------------
    # Multipart processing
    # --------------------------------------------------------

    async def _handle_multipart(self, request: web.Request, client_ip: str):
        reader = await request.multipart()

        xml_data: Optional[str] = None
        images = {}

        while True:
            part = await reader.next()
            if not part:
                break

            ctype = part.headers.get("Content-Type", "")

            # XML block
            if "xml" in ctype.lower():
                xml_data = await part.text()  # type: ignore

            # JPEG block
            elif "jpeg" in ctype.lower() or "jpg" in ctype.lower():
                filename = part.filename or "image.jpg"  # type: ignore
                data = await part.read()  # type: ignore
                images[filename] = data

        # <ИСПРАВЛЕНИЕ> Обработка Multipart Heartbeat
        # Устройства Hikvision часто шлют пустые multipart пакеты (только boundary-строки).
        # Если нет ни XML, ни картинок — считаем это пустым пакетом (heartbeat) и отвечаем OK.
        if not xml_data and not images:
            self.log.debug(f"Received empty multipart (heartbeat) from {client_ip}")
            return web.Response(status=200, text="OK")
        # </ИСПРАВЛЕНИЕ>

        if not xml_data:
            # Если картинки есть, а XML нет - это ошибка пакета (по спецификации ISAPI данные всегда в XML)
            self.log.warning("Multipart received with images but no XML from %s", client_ip)
            return web.json_response({"status": "error", "message": "xml not found"}, status=400)

        return await self._process_event(xml_data, images, client_ip)

    # --------------------------------------------------------
    # XML processing
    # --------------------------------------------------------

    async def _handle_xml(self, request: web.Request, client_ip: str):
        # Читаем тело XML запроса
        xml_data = await request.text()

        # Проверка на пустое тело (Heartbeat)
        # Устройства Hikvision иногда шлют пустые пакеты для удержания соединения.
        if not xml_data or not xml_data.strip():
            self.log.debug(f"Received empty XML packet (heartbeat) from {client_ip}")
            return web.Response(status=200, text="OK")

        return await self._process_event(xml_data, None, client_ip)

    # --------------------------------------------------------
    # Unified event processing
    # --------------------------------------------------------

    async def _process_event(self, xml_data: str, images: Optional[Dict[str, bytes]], client_ip: str):
        event: Optional[ISAPIEvent] = self.xml_parser.parse(xml_data, images)
        if not event:
            self.log.error("Failed to parse ISAPI XML from %s", client_ip)
            return web.json_response({"status": "parse_error"}, status=400)

        ok = await self.processor.process_isapi_event(event.to_dict(), client_ip)
        return web.json_response({"status": "success" if ok else "error"})


# ============================================================
# ISAPI Webhook Server (aiohttp)
# ============================================================

class ISAPIWebhookServer:
    def __init__(self, handler: ISAPIWebhookHandler, cfg: dict, logger: Optional[logging.Logger] = None):
        self.cfg = cfg.get("isapi", {})
        self.host = self.cfg.get("host", "0.0.0.0")
        self.port = self.cfg.get("port", 8002)
        self.handler = handler
        self.log = logger or logging.getLogger("ISAPIWebhookServer")
        self.runner = None
        self.http_server = None
        self.app = None

    async def start(self):
        self.log.info(f"🌐 Запуск ISAPI Webhook Server на {self.host}:{self.port}")

        self.app = web.Application()

        # Webhook endpoint
        path = self.cfg.get("webhook_path", "/ISAPI/Event/notification/alert")
        self.app.router.add_post(path, self.handler.handle)

        # Резервный путь на корне
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
# ISAPI Terminal Manager
# ============================================================

class ISAPITerminalManager:
    """
    Управляет терминалами и проверяет принадлежность устройства тенанту.
    """

    def __init__(self, cfg: dict):
        self.terminals: List[Dict] = cfg.get("terminals", [])
        self.tenant_map = self._map_by_mac()

    def _map_by_mac(self) -> Dict[str, str]:
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