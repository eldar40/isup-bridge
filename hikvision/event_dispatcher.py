"""Unified Hikvision event dispatcher.

This dispatcher converts raw EventNotificationAlert XML/JSON payloads into
Python dictionaries and routes them by event type.
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional


class HikvisionEventDispatcher:
    """Dispatches Hikvision events parsed from alert stream or callbacks."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger(__name__)

    # Public API -------------------------------------------------------------
    def handle_event(self, event_dict: Dict[str, Any]) -> None:
        """Handle already parsed event dictionary.

        The method inspects ``eventType``/``eventTypeName`` keys (commonly used in
        EventNotificationAlert) and routes them to specialized handlers. Unknown
        event types are logged but still accepted.
        """

        if not event_dict:
            self.log.warning("Received empty event payload")
            return

        event_type = (
            event_dict.get("eventType")
            or event_dict.get("eventTypeName")
            or event_dict.get("type")
            or "unknown"
        )
        self.log.info("📥 Hikvision event received: %s", event_type)

        handler_name = f"on_{event_type}"
        handler = getattr(self, handler_name, None)
        if callable(handler):
            handler(event_dict)
        else:
            self.log.debug("No dedicated handler for event type %s", event_type)

    # Helpers ---------------------------------------------------------------
    def parse_xml(self, xml_payload: str) -> Dict[str, Any]:
        """Parse EventNotificationAlert XML into a Python dictionary."""

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

    # Event-specific stubs --------------------------------------------------
    def on_heartBeat(self, event: Dict[str, Any]) -> None:  # noqa: N802
        self.log.debug("Heartbeat event: %s", event)

    def on_faceMatch(self, event: Dict[str, Any]) -> None:  # noqa: N802
        self.log.info("Face match event: %s", event)

    def on_accessControl(self, event: Dict[str, Any]) -> None:  # noqa: N802
        self.log.info("Access control event: %s", event)

    def on_motion(self, event: Dict[str, Any]) -> None:  # noqa: N802
        self.log.info("Motion detection event: %s", event)

    def on_temperature(self, event: Dict[str, Any]) -> None:  # noqa: N802
        self.log.info("Temperature event: %s", event)

    # Internal utilities ----------------------------------------------------
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
