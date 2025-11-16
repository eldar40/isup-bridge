# -*- coding: utf-8 -*-
"""
ISAPI Event Parser — полностью корректная реализация для терминалов Hikvision
Работает с EventNotificationAlert, AccessControllerEvent, multipart, изображениями.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional


class ISAPIEvent:
    """
    Унифицированная структура ISAPI-события, преобразованная из EventNotificationAlert.
    """
    def __init__(
            self,
            event_type: str,
            event_state: str,
            device_id: str,
            mac_address: Optional[str],
            ip_address: Optional[str],
            timestamp: str,
            card_number: Optional[str],
            employee_number: Optional[str],
            door_id: Optional[str],
            reader_id: Optional[str],
            direction: str,
            major_event_type: Optional[str],
            minor_event_type: Optional[str],
            success: bool,
            image_ids: Optional[list],
            raw_xml: str
    ):
        self.event_type = event_type
        self.event_state = event_state
        self.device_id = device_id
        self.mac_address = mac_address
        self.ip_address = ip_address
        self.timestamp = timestamp

        # Access fields
        self.card_number = card_number
        self.employee_number = employee_number
        self.door_id = door_id
        self.reader_id = reader_id
        self.direction = direction

        self.major_event_type = major_event_type
        self.minor_event_type = minor_event_type
        self.success = success

        self.image_ids = image_ids or []
        self.raw_xml = raw_xml

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


class ISAPIEventParser:
    """
    Разбор XML <EventNotificationAlert> в единый формат ISAPIEvent.
    """

    def __init__(self, logger: logging.Logger = None):
        self.log = logger or logging.getLogger("ISAPIEventParser")

    # ----------------------------------------------------------------------
    # Public entry
    # ----------------------------------------------------------------------

    def parse(self, xml_text: str, images=None) -> Optional[ISAPIEvent]:
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            self.log.error(f"ISAPI XML parse error: {e}", exc_info=False)
            return None

        # Base fields
        event_type = root.findtext("eventType")
        event_state = root.findtext("eventState")
        device_id = root.findtext("deviceID")
        mac_address = root.findtext("macAddress")
        ip_address = root.findtext("ipAddress")
        timestamp = root.findtext("dateTime")

        # If missing deviceID, fallback to MAC
        device_id_final = mac_address or device_id or "unknown"

        # Access node
        access_node = root.find("AccessControllerEvent")

        card_no = None
        employee_no = None
        door_id = None
        reader_id = None
        major_event_type = None
        minor_event_type = None
        direction = "UNKNOWN"
        success = False

        if access_node is not None:
            card_no = access_node.findtext("cardNo")
            employee_no = access_node.findtext("employeeNo")
            door_id = access_node.findtext("doorID")
            reader_id = access_node.findtext("readerID")
            major_event_type = access_node.findtext("majorEventType")
            minor_event_type = access_node.findtext("minorEventType")

            # Direction mapping (Hikvision logic: 1=IN, 2=OUT)
            try:
                rid = int(reader_id)
                direction = "IN" if rid % 2 == 1 else "OUT"
            except Exception:
                direction = "UNKNOWN"

            # Success mapping according to ISAPI spec (minor=1)
            success = (minor_event_type == "1")

        # Images extracted by the multipart parser
        image_ids = list(images.keys()) if images else []

        return ISAPIEvent(
            event_type=event_type,
            event_state=event_state,
            device_id=device_id_final,
            mac_address=mac_address,
            ip_address=ip_address,
            timestamp=timestamp,
            card_number=card_no,
            employee_number=employee_no,
            door_id=door_id,
            reader_id=reader_id,
            direction=direction,
            major_event_type=major_event_type,
            minor_event_type=minor_event_type,
            success=success,
            image_ids=image_ids,
            raw_xml=xml_text
        )