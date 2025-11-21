import asyncio

import pytest

from hikvision.alert_stream import HikvisionAlertStream
from hikvision.event_dispatcher import HikvisionEventDispatcher


class DummyProcessor:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_alert_stream_process_buffer_parses_events():
    processor = DummyProcessor()
    dispatcher = HikvisionEventDispatcher(processor)
    stream = HikvisionAlertStream("127.0.0.1", "user", "pass", dispatcher)

    boundary = "mybnd"
    xml = b"<EventNotificationAlert><eventType>heartBeat</eventType></EventNotificationAlert>"
    data = (
        b"--" + boundary.encode() + b"\r\nContent-Type: application/xml\r\n\r\n" + xml + b"\r\n"
        b"--" + boundary.encode() + b"--"
    )

    remainder = await stream._process_buffer(data, boundary)
    await asyncio.sleep(0)  # allow handle_event task to run

    assert remainder == b""
    assert processor.events
    assert processor.events[0].get("eventType") == "heartBeat"
