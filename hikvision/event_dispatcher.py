"""Unified dispatcher for Hikvision callback events."""

from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, Optional, Set


class HikvisionEventDispatcher:
    def __init__(
        self,
        processor,
        allowed_device_ids: Optional[Iterable[str]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.processor = processor
        self.allowed_device_ids: Set[str] = set(allowed_device_ids or [])
        self.log = logger or logging.getLogger(__name__)

    async def dispatch(self, events: Iterable[Dict[str, Any]]):
        for idx, event in enumerate(events, start=1):
            device_id = event.get("deviceID") or event.get("device_id")
            if self.allowed_device_ids and device_id not in self.allowed_device_ids:
                self.log.debug("Skipping event from unauthorized deviceID=%s", device_id)
                continue

            self.log.info("Received callback event #%s from deviceID=%s", idx, device_id)
            await self.processor.enqueue_event(event)

    # Legacy support for alertStream
    def handle_event(self, event_dict: Dict[str, Any]) -> None:
        asyncio.create_task(self.dispatch([event_dict]))

    # Legacy parsers -----------------------------------------------------
    def parse_xml(self, xml_payload: str) -> Dict[str, Any]:
        try:
            root = ET.fromstring(xml_payload)
            return self._element_to_dict(root)
        except ET.ParseError:
            self.log.exception("Failed to parse Hikvision XML payload")
            return {}

    def parse_json(self, json_payload: str) -> Dict[str, Any]:
        try:
            return json.loads(json_payload)
        except json.JSONDecodeError:
            self.log.exception("Failed to parse Hikvision JSON payload")
            return {}

    def _element_to_dict(self, elem: ET.Element) -> Dict[str, Any]:
        children = list(elem)
        if not children:
            return {elem.tag: (elem.text or "").strip()}

        result: Dict[str, Any] = {elem.tag: {}}
        for child in children:
            child_dict = self._element_to_dict(child)
            for k, v in child_dict.items():
                if k in result[elem.tag]:
                    existing = result[elem.tag][k]
                    if isinstance(existing, list):
                        existing.append(v)
                    else:
                        result[elem.tag][k] = [existing, v]
                else:
                    result[elem.tag][k] = v
        return result

