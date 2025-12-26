import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web


class ISAPITerminalManager:
    def __init__(self, cfg: dict):
        self.terminals_by_ip: Dict[str, Dict[str, Any]] = {}
        for obj in cfg.get("objects", []):
            for term in obj.get("terminals", []):
                ip = term.get("ip") or term.get("host")
                if ip:
                    self.terminals_by_ip[ip] = term

    def get_terminal(self, ip: str) -> Optional[Dict[str, Any]]:
        return self.terminals_by_ip.get(ip)


class ISAPIWebhookHandler:
    def __init__(self, processor, secret_token: str | None, logger: logging.Logger):
        self.processor = processor
        self.secret_token = secret_token
        self.log = logger

    async def handle(self, request: web.Request) -> web.Response:
        if self.secret_token:
            token = request.headers.get("X-Webhook-Token") or request.query.get("token")
            if token != self.secret_token:
                return web.Response(status=403, text="Forbidden")

        try:
            payload = await request.json()
        except Exception as exc:
            self.log.error("Failed to parse webhook payload: %s", exc)
            return web.Response(status=400, text="Invalid payload")

        asyncio.create_task(self.processor.process_isapi_event(payload, request.remote or "unknown"))
        return web.json_response({"status": "ok"})


class ISAPIWebhookServer:
    def __init__(self, handler: ISAPIWebhookHandler, cfg: dict, logger: logging.Logger):
        self.handler = handler
        self.cfg = cfg
        self.log = logger
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.health_runner: Optional[web.AppRunner] = None
        self.health_site: Optional[web.TCPSite] = None

    async def start(self):
        app = web.Application()
        path = self.cfg.get("isapi", {}).get("webhook_path", "/isapi/webhook")
        app.router.add_post(path, self.handler.handle)

        self.runner = web.AppRunner(app)
        await self.runner.setup()

        host = self.cfg.get("isapi", {}).get("host", "0.0.0.0")
        port = self.cfg.get("isapi", {}).get("port", 8002)
        self.site = web.TCPSite(self.runner, host=host, port=port)
        await self.site.start()
        self.log.info("ISAPI webhook listening on %s:%s%s", host, port, path)

    async def start_api(self, host: str = "0.0.0.0", port: int = 8081):
        app = web.Application()

        async def health(_):
            return web.json_response({"status": "ok"})

        app.router.add_get("/health", health)
        self.health_runner = web.AppRunner(app)
        await self.health_runner.setup()
        self.health_site = web.TCPSite(self.health_runner, host=host, port=port)
        await self.health_site.start()
        self.log.info("Health endpoint listening on %s:%s/health", host, port)

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        if self.health_site:
            await self.health_site.stop()
        if self.health_runner:
            await self.health_runner.cleanup()
