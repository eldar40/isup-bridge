"""
Hikvision ISAPI integration package (helpers).

This package provides alertStream client, aiohttp listener, a lightweight multipart parser and a simple event dispatcher
for Hikvision devices using ISAPI EventNotificationAlert format.
"""

from .alert_stream import HikvisionAlertStream
from .listener import create_hikvision_listener, HikvisionCallbackHandler
from .multipart_parser import MultipartParser, Part
from .event_dispatcher import HikvisionEventDispatcher

__all__ = [
    "HikvisionAlertStream",
    "create_hikvision_listener",
    "HikvisionCallbackHandler",
    "MultipartParser",
    "Part",
    "HikvisionEventDispatcher",
]