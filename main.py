#!/usr/bin/env python3
import asyncio
import signal
from pathlib import Path

import yaml
from aiohttp import web

from core.metrics import ServerMetrics
from core.processor import EventProcessor
from core.storage import EventStorage
from core.tenant_manager import TenantManager
from hikvision import HikvisionEventDispatcher, create_hikvision_listener  # noqa: F401
from isapi.isapi_device_manager import ISAPIDeviceManager
from isapi.isapi_server import ISAPITerminalManager, ISAPIWebhookHandler, ISAPIWebhookServer
from isup.isup_protocol import ISUPv5Parser
from isup.isup_server import ISUPTCPServer
from utils.logging_setup import setup_logging

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class ServerConfig:
    def __init__(self, cfg: dict):
        s = cfg.get("server", {})
        self.host = s.get("host", "0.0.0.0")
        self.port = s.get("port", 8001)
        self.health_port = s.get("health_check_port", 8081)
        self.log_level = s.get("log_level", "INFO")
        self.storage_path = Path(s.get("storage_path", "./data/storage"))
        self.max_pending_days = s.get("max_pending_days", 30)
        self.isapi = cfg.get("isapi", {})
        self.features = cfg.get("features", {})

    @classmethod
    def load(cls, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data), data


async def _periodic_pending(processor: EventProcessor, stop_event: asyncio.Event, interval: int = 30):
    while not stop_event.is_set():
        await processor.retry_pending_events()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def main():
    cfg, cfg_dict = ServerConfig.load(CONFIG_PATH)
    logger = setup_logging(cfg.log_level)
    logger.debug("🚀 ISUP/ISAPI Bridge starting...")

    metrics = ServerMetrics()
    storage = EventStorage(cfg.storage_path, cfg.max_pending_days, logger)
    tenant_mgr = TenantManager(cfg_dict)
    term_mgr = ISAPITerminalManager(cfg_dict)
    parser = ISUPv5Parser()

    processor = EventProcessor(
        tenant_manager=tenant_mgr,
        terminal_manager=term_mgr,
        storage=storage,
        metrics=metrics,
        logger=logger,
        isup_parser=parser,
    )

    tcp_server = ISUPTCPServer(
        host=cfg.host, port=cfg.port, processor=processor, metrics=metrics, parser=parser, logger=logger
    )

    isapi_handler = ISAPIWebhookHandler(processor, secret_token=cfg.isapi.get("webhook_secret"), logger=logger)
    isapi_server = ISAPIWebhookServer(isapi_handler, cfg_dict, logger)
    device_mgr = ISAPIDeviceManager(cfg_dict, logger)

    await tcp_server.start()
    await isapi_server.start()
    await isapi_server.start_api(host="0.0.0.0", port=cfg.health_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("Shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    tasks = [asyncio.create_task(_periodic_pending(processor, stop_event))]

    try:
        if cfg.features.get("auto_configure_terminals"):
            base_url = cfg.isapi.get("webhook_base_url", f"http://{cfg.host}:8002")
            await device_mgr.auto_configure_terminals(base_url)

        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await tcp_server.stop()
        await isapi_server.stop()
        await storage.close()
        await device_mgr.close()
        logger.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())
