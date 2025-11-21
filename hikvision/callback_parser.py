"""XML parser for Hikvision EventNotificationAlert callbacks."""

import base64
import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


class CallbackParser:
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger(__name__)

    def parse(self, xml_payload: str, images: Optional[Dict[str, bytes]] = None) -> List[Dict]:
        try:
            root = ET.fromstring(xml_payload)
        except ET.ParseError:
            self.log.error("Failed to parse callback XML")
            return []

        nodes: List[ET.Element]
        if root.tag.endswith("EventNotificationAlert"):
            nodes = [root]
        else:
            nodes = list(root.findall(".//EventNotificationAlert"))
            if not nodes:
                nodes = [root]

        events: List[Dict] = []
        for node in nodes:
            event: Dict = {}
            event["eventDateTime"] = node.findtext("eventDateTime") or node.findtext("dateTime")
            event["majorEventType"] = node.findtext("majorEventType") or self._get_text(node, "AccessControllerEvent/majorEventType")
            event["minorEventType"] = node.findtext("minorEventType") or self._get_text(node, "AccessControllerEvent/minorEventType")
            event["deviceID"] = node.findtext("deviceID")
            event["channelID"] = node.findtext("channelID") or node.findtext("dynChannelID")

            pic_url = node.findtext("picURL") or self._get_text(node, "AccessControllerEvent/picURL")
            event["picURL"] = pic_url

            pic_data_text = node.findtext("picData") or self._get_text(node, "AccessControllerEvent/picData")
            if pic_data_text:
                try:
                    event["picData"] = base64.b64decode(pic_data_text)
                except Exception:
                    self.log.warning("Failed to decode picData")

            if images:
                event["images"] = images

            events.append(event)
        return events

    @staticmethod
    def _get_text(node: ET.Element, path: str) -> Optional[str]:
        element = node.find(path)
        return element.text if element is not None else None

