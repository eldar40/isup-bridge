import logging

import pytest

from isapi.isapi_client import DeviceInfo, ISAPIDeviceClient
from isapi.isapi_device_manager import ISAPIDeviceManager


class FakeClient:
    def __init__(self):
        self.http_payload_xml = None
        self.enabled_events = None

    async def is_reachable(self):
        return True

    async def get_device_info(self):
        return DeviceInfo(device_id="AABBCCDDEE11", model="TEST")

    async def configure_http_host(self, callback_url: str, host_id: int = 1):
        builder = ISAPIDeviceClient("127.0.0.1", 80, "", "", logging.getLogger())
        self.http_payload_xml = builder.build_http_host_payload(callback_url, host_id)
        return True

    async def enable_events(self, events, host_id: int = 1):
        self.enabled_events = list(events)
        return True


class FakeFactory:
    def __init__(self):
        self.clients = []

    def __call__(self, terminal):
        client = FakeClient()
        self.clients.append(client)
        return client


@pytest.mark.asyncio
async def test_auto_configure_builds_correct_xml():
    cfg = {
        "objects": [
            {
                "object_id": "obj1",
                "terminals": [
                    {
                        "terminal_id": "t1",
                        "ip_address": "192.168.1.10",
                        "username": "admin",
                        "password": "pass",
                    }
                ],
            }
        ],
        "isapi": {"username": "admin", "password": "pass"},
    }

    factory = FakeFactory()
    manager = ISAPIDeviceManager(cfg, logging.getLogger(), client_factory=factory)

    await manager.auto_configure_terminals("http://1.2.3.4:8002")

    assert factory.clients
    client = factory.clients[0]
    assert "<ipAddress>1.2.3.4</ipAddress>" in client.http_payload_xml
    assert "<portNo>8002</portNo>" in client.http_payload_xml
    assert "<url>/hikvision/callback</url>" in client.http_payload_xml
    assert client.enabled_events == manager.EVENT_TYPES
