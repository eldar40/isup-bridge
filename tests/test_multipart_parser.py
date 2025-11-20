from hikvision.multipart_parser import MultipartParser


def test_multipart_parser_parses_xml_and_image():
    boundary = "boundary123"
    xml_payload = b"<EventNotificationAlert><eventType>motion</eventType></EventNotificationAlert>"
    image_bytes = b"binaryjpegdata"

    body = (
        b"--" + boundary.encode() + b"\r\n"
        b"Content-Type: application/xml\r\n\r\n" + xml_payload + b"\r\n"
        b"--" + boundary.encode() + b"\r\n"
        b"Content-Type: image/jpeg\r\nContent-Disposition: attachment; filename=\"img.jpg\"\r\n\r\n"
        + image_bytes
        + b"\r\n"
        b"--" + boundary.encode() + b"--"
    )

    parts = MultipartParser.parse(body, boundary)
    assert len(parts) == 2
    assert parts[0].type == "xml"
    assert xml_payload in parts[0].body
    assert parts[1].type == "image"
    assert parts[1].body == image_bytes


def test_multipart_parser_detects_json():
    boundary = "abc"
    json_payload = b"{\"eventType\":\"faceMatch\"}"
    body = (
        b"--" + boundary.encode() + b"\r\nContent-Type: application/json\r\n\r\n" + json_payload + b"\r\n"
        b"--" + boundary.encode() + b"--"
    )
    parts = MultipartParser.parse(body, boundary)
    assert parts[0].type == "json"
    assert parts[0].body == json_payload
