"""aiohttp listener for Hikvision EventNotificationAlert callbacks."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from aiohttp import web

from .callback_parser import CallbackParser


class HikvisionCallbackHandler:
    def __init__(
        self,
        dispatcher,
        secret: Optional[str],
        path: str,
        logger: Optional[logging.Logger] = None,
    ):
        self.dispatcher = dispatcher
        self.secret = secret
        self.path = path
        self.log = logger or logging.getLogger(__name__)
        self.parser = CallbackParser(self.log)

    async def handle(self, request: web.Request) -> web.Response:
        if self.secret and request.headers.get("X-Webhook-Secret") != self.secret:
            self.log.warning("Unauthorized webhook request from %s", request.remote)
            return web.Response(status=401)

        content_type = request.headers.get("Content-Type", "").lower()
        images: Dict[str, bytes] = {}
        xml_payload = None

        if "multipart" in content_type:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if not part:
                    break
                ctype = part.headers.get("Content-Type", "").lower()
                if "xml" in ctype:
                    xml_payload = await part.text()
                elif "jpeg" in ctype or "jpg" in ctype:
                    filename = part.filename or "image.jpg"
                    images[filename] = await part.read()
        else:
            xml_payload = await request.text()

        if not xml_payload:
            return web.json_response({"status": "error", "message": "xml not found"}, status=400)

        events = self.parser.parse(xml_payload, images or None)
        await self.dispatcher.dispatch(events)
        return web.json_response({"status": "ok", "received": len(events)})


def create_hikvision_listener(dispatcher, cfg: dict, logger: Optional[logging.Logger] = None) -> web.Application:
    hikvision_cfg = cfg.get("hikvision", cfg)
    callback_cfg = hikvision_cfg.get("callback", {})
    path = callback_cfg.get("path", "/hikvision/callback")
    secret = callback_cfg.get("secret")

    handler = HikvisionCallbackHandler(dispatcher, secret, path, logger)
    app = web.Application()
    app.router.add_post(path, handler.handle)
    return app


__all__ = ["create_hikvision_listener", "HikvisionCallbackHandler"]

