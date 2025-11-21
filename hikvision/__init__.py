"""
Hikvision ISAPI integration package (helpers).

This package provides alertStream client, aiohttp listener, a lightweight multipart parser and a simple event dispatcher
for Hikvision devices using ISAPI EventNotificationAlert format.
"""

from .alert_stream import HikvisionAlertStream
from .listener import (
    HikvisionCallbackHandler as LegacyHikvisionCallbackHandler,
    create_hikvision_listener as create_legacy_listener,
)
from .callback_listener import HikvisionCallbackHandler, create_hikvision_listener
from .multipart_parser import MultipartParser, Part
from .event_dispatcher import HikvisionEventDispatcher

__all__ = [
    "HikvisionAlertStream",
    "create_hikvision_listener",
    "HikvisionCallbackHandler",
    "create_legacy_listener",
    "LegacyHikvisionCallbackHandler",
    "MultipartParser",
    "Part",
    "HikvisionEventDispatcher",
]
