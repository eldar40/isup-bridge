import aiohttp
import pytest

from hikvision.listener import create_hikvision_listener
from hikvision.event_dispatcher import HikvisionEventDispatcher


class Recorder(HikvisionEventDispatcher):
    def __init__(self):
        super().__init__()
        self.received = []

    def handle_event(self, event_dict):
        self.received.append(event_dict)


@pytest.mark.asyncio
async def test_listener_accepts_multipart(aiohttp_client):
    dispatcher = Recorder()
    app = create_hikvision_listener(dispatcher)

    client = await aiohttp_client(app)

    xml_payload = "<EventNotificationAlert><eventType>motion</eventType></EventNotificationAlert>"
    data = aiohttp.FormData()
    data.add_field("xml", xml_payload, content_type="application/xml")
    data.add_field("image", b"data", filename="img.jpg", content_type="image/jpeg")

    resp = await client.post("/hikvision/event", data=data)
    assert resp.status == 200
    assert dispatcher.received
    assert dispatcher.received[0].get("EventNotificationAlert", {}).get("eventType") == "motion"
