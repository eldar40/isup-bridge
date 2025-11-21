import pytest

from hikvision.event_dispatcher import HikvisionEventDispatcher


class DummyProcessor:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_dispatcher_filters_and_forwards():
    processor = DummyProcessor()
    dispatcher = HikvisionEventDispatcher(processor, allowed_device_ids=["ALLOWED"])

    events = [
        {"deviceID": "BLOCKED", "eventDateTime": "ts"},
        {"deviceID": "ALLOWED", "eventDateTime": "ts2"},
    ]

    await dispatcher.dispatch(events)

    assert len(processor.events) == 1
    assert processor.events[0]["deviceID"] == "ALLOWED"
