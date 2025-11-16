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
from typing import Optional

import yaml

from metrics import ServerMetrics
from storage import EventStorage
from tenant_manager import TenantManager
from isup_protocol import ISUPv5Parser
from event_processor import EventProcessor
from tcp_server import ISUPTCPServer
from isapi_server import ISAPIWebhookServer, ISAPIWebhookHandler, ISAPIDeviceManager, ISAPITerminalManager
from isapi_client import ISAPIEvent

# ================ Константы и базовые пути ==================
PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True, parents=True)

# ================ Логирование ==============================
def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("isup_bridge")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(ch)

    # File
    fh = logging.FileHandler(LOG_DIR / "isup_bridge.log", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    # Errors file
    eh = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
    eh.setFormatter(fmt)
    eh.setLevel(logging.ERROR)
    logger.addHandler(eh)

    return logger

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

    # Metrics & Storage
    metrics = ServerMetrics()
    storage = EventStorage(server_cfg.storage_path, logger)

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
        isup_parser=isup_parser
    )

    # TCP (ISUP) Server
    tcp_server = ISUPTCPServer(
        host=server_cfg.host,
        port=server_cfg.port,
        processor=processor,
        metrics=metrics,
        logger=logger
    )

    # ISAPI Webhook components
    isapi_cfg = full_cfg.get("isapi", {})
    webhook_secret = isapi_cfg.get("webhook_secret")
    webhook_handler = ISAPIWebhookHandler(processor, secret_token=webhook_secret, logger=logger)
    isapi_server = ISAPIWebhookServer(webhook_handler, full_cfg, logger)
    device_manager = ISAPIDeviceManager(full_cfg, logger)

    # Background tasks
    retry_task = asyncio.create_task(processor.retry_pending_events())
    tcp_task = asyncio.create_task(tcp_server.start())
    isapi_task = asyncio.create_task(isapi_server.start())
    api_task = asyncio.create_task(isapi_server.start_api(host="0.0.0.0", port=server_cfg.health_check_port))

    # Optional auto-configure terminals
    if server_cfg.features.get("auto_configure_terminals", False):
        webhook_base = isapi_cfg.get("webhook_base_url", f"http://{isapi_cfg.get('host','0.0.0.0')}:{isapi_cfg.get('port',8082)}")
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

    await shutdown.wait()

    # Shutdown sequence
    logger.info("🧹 Остановка задач...")
    for task in (retry_task, tcp_task, isapi_task, api_task):
        task.cancel()
    await tcp_server.stop()
    await isapi_server.stop()
    await storage.close()

    logger.info("👋 ISUP Bridge остановлен")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")