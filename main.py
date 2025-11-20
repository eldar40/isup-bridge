#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — точка входа для ISUP <-> ISAPI Bridge
Вариант B: production-ready, multi-tenant, webhook + ISUP TCP + Hikvision callback
"""

import asyncio
import logging
import signal
from pathlib import Path

import yaml
from aiohttp import web

from core.metrics import ServerMetrics
from core.storage import EventStorage
from core.tenant_manager import TenantManager
from core.processor import EventProcessor
from isup.isup_protocol import ISUPv5Parser
from isup.isup_server import ISUPTCPServer
from isapi.isapi_server import (
    ISAPIWebhookServer,
    ISAPIWebhookHandler,
    ISAPIDeviceManager,
    ISAPITerminalManager,
)

# NEW — callback only
from hikvision import HikvisionEventDispatcher, create_hikvision_callback_app

from utils.logging_setup import setup_logging

# ================ Пути ==================
PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
HIKVISION_CONFIG_PATH = PROJECT_ROOT / "config" / "hikvision.yaml"


# ================ Конфигурация =============================
class ServerConfig:
    def __init__(self, cfg: dict):
        server = cfg.get("server", {})
        self.host = server.get("host", "0.0.0.0")
        self.port = server.get("port", 8080)
        self.health_check_port = server.get("health_check_port", 8081)
        self.log_level = server.get("log_level", "INFO")
        self.storage_path = Path(server.get("storage_path", "/tmp/isup_bridge/storage"))
        self.max_pending_days = server.get("max_pending_days", 30)
        self.isapi = cfg.get("isapi", {})
        self.features = cfg.get("features", {})

    @classmethod
    def load_from_file(cls, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cls(cfg), cfg


# ================ Main =========================
async def main():
    # Load configuration
    server_cfg, full_cfg = ServerConfig.load_from_file(CONFIG_PATH)
    logger = setup_logging(server_cfg.log_level)
    logger.info("🚀 Запуск ISUP / ISAPI Bridge (вариант B)")

    # Load Hikvision config
    hikvision_cfg = {}
    if HIKVISION_CONFIG_PATH.exists():
        with open(HIKVISION_CONFIG_PATH, "r", encoding="utf-8") as f:
            hikvision_cfg = yaml.safe_load(f) or {}
    hikvision_settings = hikvision_cfg.get("hikvision", {})

    # Core components
    metrics = ServerMetrics()
    storage = EventStorage(server_cfg.storage_path, server_cfg.max_pending_days, logger)
    tenant_manager = TenantManager(full_cfg)
    terminal_manager = ISAPITerminalManager(full_cfg)
    isup_parser = ISUPv5Parser(strict_mode=False)

    processor = EventProcessor(
        tenant_manager=tenant_manager,
        terminal_manager=terminal_manager,
        storage=storage,
        metrics=metrics,
        logger=logger,
        isup_parser=isup_parser,
    )

    # ISUP TCP server
    tcp_server = ISUPTCPServer(
        host=server_cfg.host,
        port=server_cfg.port,
        processor=processor,
        metrics=metrics,
        parser=isup_parser,
        logger=logger,
    )

    # ISAPI Webhook
    isapi_cfg = full_cfg.get("isapi", {})
    webhook_secret = isapi_cfg.get("webhook_secret")
    isapi_handler = ISAPIWebhookHandler(processor, secret_token=webhook_secret, logger=logger)
    isapi_server = ISAPIWebhookServer(isapi_handler, full_cfg, logger)
    device_manager = ISAPIDeviceManager(full_cfg, logger)

    # ================ HIKVISION CALLBACK MODE ====================
    hikvision_runner = None
    callback_cfg = hikvision_settings.get("callback", {})
    allowed_devices = hikvision_settings.get("allowed_device_ids", [])

    if callback_cfg:
        dispatcher = HikvisionEventDispatcher(
            processor=processor,
            allowed_device_ids=allowed_devices,
            logger=logger,
        )

        host = callback_cfg.get("host", "0.0.0.0")
        port = callback_cfg.get("port", 8099)

        logger.info("🌐 Запуск Hikvision callback listener на %s:%s", host, port)

        hk_app = create_hikvision_callback_app(dispatcher, hikvision_settings, logger)
        hikvision_runner = web.AppRunner(hk_app)
        await hikvision_runner.setup()
        site = web.TCPSite(hikvision_runner, host, port)
        await site.start()

    # Background tasks
    retry_task = asyncio.create_task(processor.retry_pending_events())
    tcp_task = asyncio.create_task(tcp_server.start())
    isapi_task = asyncio.create_task(isapi_server.start())
    api_task = asyncio.create_task(
        isapi_server.start_api(host="0.0.0.0", port=server_cfg.health_check_port)
    )

    background_tasks = [retry_task, tcp_task, isapi_task, api_task]

    # Graceful shutdown
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal():
        logger.info("🛑 Получен сигнал остановки, запускаем graceful shutdown...")
        shutdown.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _on_signal)
        except NotImplementedError:
            pass

    try:
        await shutdown.wait()
    finally:
        logger.info("🧹 Остановка задач...")

        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)

        if hikvision_runner:
            await hikvision_runner.cleanup()

        await tcp_server.stop()
        await isapi_server.stop()
        await storage.close()

    logger.info("👋 ISUP Bridge остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
