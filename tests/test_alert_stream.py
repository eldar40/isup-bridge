import asyncio

import pytest

from hikvision.alert_stream import HikvisionAlertStream
from hikvision.event_dispatcher import HikvisionEventDispatcher


class DummyDispatcher(HikvisionEventDispatcher):
    def __init__(self):
        super().__init__()
        self.events = []

    def handle_event(self, event_dict):
        self.events.append(event_dict)


@pytest.mark.asyncio
async def test_alert_stream_process_buffer_parses_events():
    dispatcher = DummyDispatcher()
    stream = HikvisionAlertStream("127.0.0.1", "user", "pass", dispatcher)

    boundary = "mybnd"
    xml = b"<EventNotificationAlert><eventType>heartBeat</eventType></EventNotificationAlert>"
    data = (
        b"--" + boundary.encode() + b"\r\nContent-Type: application/xml\r\n\r\n" + xml + b"\r\n"
        b"--" + boundary.encode() + b"--"
    )

    remainder = await stream._process_buffer(data, boundary)
    assert remainder == b""
    assert dispatcher.events
    assert dispatcher.events[0].get("eventType") == "heartBeat"
