#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — Точка входа для ISUP <-> ISAPI Bridge
Полная сборка (вариант B): production-ready, multi-tenant, webhook + ISUP TCP
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
from hikvision import (
    HikvisionAlertStream,
    HikvisionEventDispatcher,
    create_hikvision_listener,
)
from utils.logging_setup import setup_logging

# ================ Константы и базовые пути ==================
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
        self.security = cfg.get("security", {})

    @classmethod
    def load_from_file(cls, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cls(cfg), cfg


# ================ Main async entry =========================
async def main():
    # Load configuration
    config_path = CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    server_cfg, full_cfg = ServerConfig.load_from_file(config_path)
    logger = setup_logging(server_cfg.log_level)
    logger.info("🚀 Запуск ISUP / ISAPI Bridge (вариант B)")

    # Optional Hikvision configuration
    hikvision_cfg = {}
    if HIKVISION_CONFIG_PATH.exists():
        with open(HIKVISION_CONFIG_PATH, "r", encoding="utf-8") as f:
            hikvision_cfg = yaml.safe_load(f) or {}

    # Metrics & Storage
    metrics = ServerMetrics()
    storage = EventStorage(server_cfg.storage_path, server_cfg.max_pending_days, logger)

    # Tenant & Terminal managers
    tenant_manager = TenantManager(full_cfg)
    terminal_manager = ISAPITerminalManager(full_cfg)

    # Parser & Processor
    isup_parser = ISUPv5Parser(strict_mode=False)
    processor = EventProcessor(
        tenant_manager=tenant_manager,
        terminal_manager=terminal_manager,
        storage=storage,
        metrics=metrics,
        logger=logger,
        isup_parser=isup_parser,
    )

    # TCP (ISUP) Server
    tcp_server = ISUPTCPServer(
        host=server_cfg.host,
        port=server_cfg.port,
        processor=processor,
        metrics=metrics,
        parser=isup_parser,
        logger=logger,
    )

    # ISAPI Webhook components
    isapi_cfg = full_cfg.get("isapi", {})
    webhook_secret = isapi_cfg.get("webhook_secret")
    webhook_handler = ISAPIWebhookHandler(processor, secret_token=webhook_secret, logger=logger)
    isapi_server = ISAPIWebhookServer(webhook_handler, full_cfg, logger)
    device_manager = ISAPIDeviceManager(full_cfg, logger)

    # Hikvision dispatcher and runtime containers
    hikvision_dispatcher = HikvisionEventDispatcher(logger)
    hikvision_streams = []
    hikvision_tasks = []
    hikvision_runner = None

    devices_cfg = hikvision_cfg.get("devices", [])
    if devices_cfg:
        listener_cfg = hikvision_cfg.get("listener", {})
        listener_host = listener_cfg.get("host", "0.0.0.0")
        listener_port = listener_cfg.get("port", 8099)

        # Start callback listener if any device requires callback mode
        if any(d.get("mode", "alert_stream") == "callback" for d in devices_cfg):
            logger.info(
                "🌐 Запуск Hikvision callback listener на %s:%s",
                listener_host,
                listener_port,
            )
            hk_app = create_hikvision_listener(hikvision_dispatcher, logger)
            hikvision_runner = web.AppRunner(hk_app)
            await hikvision_runner.setup()
            site = web.TCPSite(hikvision_runner, listener_host, listener_port)
            await site.start()

        # Start alert streams
        for dev in devices_cfg:
            if dev.get("mode", "alert_stream") != "alert_stream":
                continue
            stream = HikvisionAlertStream(
                ip=dev.get("ip"),
                username=dev.get("username", "admin"),
                password=dev.get("password", ""),
                dispatcher=hikvision_dispatcher,
                name=dev.get("name"),
                logger=logger,
            )
            hikvision_streams.append(stream)
            hikvision_tasks.append(asyncio.create_task(stream.run()))

    # Background tasks
    retry_task = asyncio.create_task(processor.retry_pending_events())
    tcp_task = asyncio.create_task(tcp_server.start())
    isapi_task = asyncio.create_task(isapi_server.start())
    api_task = asyncio.create_task(isapi_server.start_api(host="0.0.0.0", port=server_cfg.health_check_port))
    background_tasks = [retry_task, tcp_task, isapi_task, api_task, *hikvision_tasks]

    # Optional auto-configure terminals
    if server_cfg.features.get("auto_configure_terminals", False):
        webhook_base = isapi_cfg.get(
            "webhook_base_url",
            f"http://{isapi_cfg.get('host','0.0.0.0')}:{isapi_cfg.get('port',8082)}",
        )
        logger.info(f"🔧 Автонастройка терминалов включена, webhook_base={webhook_base}")
        _ = asyncio.create_task(device_manager.auto_configure_terminals(webhook_base))

    # Graceful shutdown handling
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal():
        logger.info("🛑 Получен сигнал остановки, запускаем graceful shutdown...")
        shutdown.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _on_signal)
        except NotImplementedError:
            # Windows compatibility
            pass

    try:
        await shutdown.wait()
    except Exception:
        logger.exception("❌ Необработанное исключение, инициируем остановку")
    finally:
        # Shutdown sequence
        logger.info("🧹 Остановка задач...")
        for stream in hikvision_streams:
            await stream.stop()
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
