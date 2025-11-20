"""HTTP listener for Hikvision callbacks.

Provides `/hikvision/event` endpoint that accepts multipart/form-data with XML and
image attachments or raw XML/JSON payloads.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from aiohttp import web

from .event_dispatcher import HikvisionEventDispatcher


class HikvisionCallbackHandler:
    """aiohttp handler for Hikvision camera callbacks."""

    def __init__(self, dispatcher: HikvisionEventDispatcher, logger: Optional[logging.Logger] = None):
        self.dispatcher = dispatcher
        self.log = logger or logging.getLogger(__name__)

    async def handle(self, request: web.Request) -> web.Response:  # pragma: no cover - thin wrapper
        try:
            content_type = request.headers.get("Content-Type", "").lower()
            if "multipart" in content_type:
                await self._handle_multipart(request)
            elif "xml" in content_type:
                body = await request.text()
                self._dispatch(self.dispatcher.parse_xml(body))
            elif "json" in content_type:
                body = await request.text()
                self._dispatch(self.dispatcher.parse_json(body))
            else:
                # attempt to parse as XML by default
                body = await request.text()
                self._dispatch(self.dispatcher.parse_xml(body))
        except Exception:
            self.log.exception("Failed to process Hikvision callback")
        return web.Response(status=200)

    async def _handle_multipart(self, request: web.Request) -> None:
        reader = await request.multipart()

        xml_payload = None
        json_payload = None
        images: Dict[str, bytes] = {}

        while True:
            part = await reader.next()
            if not part:
                break

            ctype = part.headers.get("Content-Type", "").lower()
            if "xml" in ctype:
                xml_payload = await part.text()
            elif "json" in ctype:
                json_payload = await part.text()
            elif "jpeg" in ctype or "jpg" in ctype:
                filename = part.filename or "image.jpg"
                images[filename] = await part.read()

        if xml_payload:
            event = self.dispatcher.parse_xml(xml_payload)
        elif json_payload:
            event = self.dispatcher.parse_json(json_payload)
        else:
            self.log.warning("Multipart without XML/JSON body received")
            event = {}

        if images:
            event["images"] = images
        if event:
            self._dispatch(event)

    def _dispatch(self, event: dict) -> None:
        if not event:
            return
        if len(event) == 1 and isinstance(next(iter(event.values())), dict):
            payload = next(iter(event.values()))
        else:
            payload = event
        self.dispatcher.handle_event(payload)


def create_hikvision_listener(
    dispatcher: HikvisionEventDispatcher,
    logger: Optional[logging.Logger] = None,
    path: str = "/hikvision/event",
) -> web.Application:
    """Create aiohttp application for Hikvision callbacks."""

    app = web.Application()
    handler = HikvisionCallbackHandler(dispatcher, logger)
    app.router.add_post(path, handler.handle)
    return app


__all__ = ["create_hikvision_listener", "HikvisionCallbackHandler"]
