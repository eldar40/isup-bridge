import logging
import xml.etree.ElementTree as ET

from aiohttp import web


class HikvisionEventDispatcher:
    def __init__(self, processor, allowed_device_ids, logger: logging.Logger):
        self.processor = processor
        self.allowed = set(allowed_device_ids)
        self.log = logger

    async def dispatch(self, request: web.Request):
        if request.content_type.startswith("multipart"):
            reader = await request.multipart()
            xml_data = None
            while True:
                part = await reader.next()
                if not part:
                    break
                if "xml" in part.headers.get("Content-Type", "").lower():
                    xml_data = await part.text()
            if xml_data:
                return await self._process_xml(xml_data)

        elif "xml" in request.content_type:
            xml_data = await request.text()
            return await self._process_xml(xml_data)

        return web.Response(status=400, text="Unsupported Content-Type")

    async def _process_xml(self, xml_text: str):
        try:
            root = ET.fromstring(xml_text)
            device_id = root.findtext("deviceID")
            event_type = root.findtext("eventType")

            if self.allowed and device_id not in self.allowed:
                self.log.warning("Event from unauthorized device %s", device_id)
                return web.Response(status=403)

            await self.processor.process_isapi_event(
                {"device_id": device_id, "event_type": event_type, "raw": xml_text},
                "callback",
            )

            return web.Response(status=200, text="OK")
        except Exception as e:  # pragma: no cover - defensive
            self.log.error("XML parsing error: %s", e)
            return web.Response(status=500)


def create_hikvision_listener(dispatcher: HikvisionEventDispatcher, cfg: dict, logger: logging.Logger):
    app = web.Application()
    path = cfg.get("callback", {}).get("path", "/hikvision/callback")
    app.router.add_post(path, dispatcher.dispatch)
    logger.info("Hikvision callback listening on path %s", path)
    return app
