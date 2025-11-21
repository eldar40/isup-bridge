import base64

import pytest

from hikvision.callback_parser import CallbackParser


def test_callback_parser_handles_multiple_events():
    img_b64 = base64.b64encode(b"image-bytes").decode()
    xml = f"""
    <Events>
        <EventNotificationAlert>
            <deviceID>DEV1</deviceID>
            <eventDateTime>2024-01-01T00:00:00</eventDateTime>
            <majorEventType>major</majorEventType>
            <minorEventType>minor</minorEventType>
            <picData>{img_b64}</picData>
        </EventNotificationAlert>
        <EventNotificationAlert>
            <deviceID>DEV2</deviceID>
            <eventDateTime>2024-01-01T00:00:01</eventDateTime>
            <picURL>http://example.com/img.jpg</picURL>
        </EventNotificationAlert>
    </Events>
    """

    parser = CallbackParser()
    events = parser.parse(xml)

    assert len(events) == 2
    assert events[0]["deviceID"] == "DEV1"
    assert events[0]["picData"] == b"image-bytes"
    assert events[1]["picURL"] == "http://example.com/img.jpg"
