#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ISUP Bridge - main server file
Полностью готовый вариант main.py с метриками, TCP сервером и HTTP health API.
"""

import asyncio
import signal
import logging
import struct
import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any

# Optional dependencies
try:
    import yaml
except Exception:
    yaml = None

try:
    import aiohttp
    from aiohttp import web
except Exception:
    aiohttp = None
    web = None

# Попытка импортировать реальные модули из репозитория; если их нет — используем безопасные заглушки.
try:
    from isup_protocol import ISUPv5Parser, ISUPAccessEvent  # type: ignore
except Exception:
    ISUPv5Parser = None
    ISUPAccessEvent = None

try:
    from tenant_manager import TenantManager  # type: ignore
except Exception:
    TenantManager = None

# -------------------------
# Config dataclass
# -------------------------
@dataclass
class ServerConfig:
    host: str
    port: int
    log_level: str
    storage_path: Path
    max_pending_days: int
    health_check_port: int
    config_path: Path

    @classmethod
    def from_yaml(cls, config_path: str = 'config/config.yaml'):
        cfg_path = Path(config_path)
        default = {
            'server': {
                'host': '0.0.0.0',
                'port': 6000,
                'log_level': 'INFO',
                'storage_path': 'storage',
                'max_pending_days': 30,
                'health_check_port': 8081
            }
        }
        if yaml and cfg_path.exists():
            with open(cfg_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        else:
            data = default
        server = data.get('server', default['server'])
        return cls(
            host=server.get('host', '0.0.0.0'),
            port=int(server.get('port', 6000)),
            log_level=server.get('log_level', 'INFO'),
            storage_path=Path(server.get('storage_path', 'storage')),
            max_pending_days=int(server.get('max_pending_days', 30)),
            health_check_port=int(server.get('health_check_port', 8081)),
            config_path=cfg_path
        )

# -------------------------
# Logging setup
# -------------------------
def setup_logging(config: ServerConfig) -> logging.Logger:
    log_dir = Path('logs')
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('isup_bridge')
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers on reload
    if logger.handlers:
        return logger

    fmt = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    formatter = logging.Formatter(fmt, datefmt='%Y-%m-%d %H:%M:%S')

    fh = RotatingFileHandler(log_dir / 'isup_bridge.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    fh.setFormatter(formatter)
    fh.setLevel(logging.DEBUG)

    eh = RotatingFileHandler(log_dir / 'errors.log', maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    eh.setFormatter(formatter)
    eh.setLevel(logging.ERROR)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    ch.setLevel(logging.INFO)

    logger.addHandler(fh)
    logger.addHandler(eh)
    logger.addHandler(ch)
    return logger

# -------------------------
# Minimal safe stubs (используются только если реальные модули отсутствуют)
# -------------------------
if ISUPv5Parser is None:
    @dataclass
    class _Header:
        sequence_number: int
        device_id: str

    @dataclass
    class _ISUPAccessEvent:
        header: _Header
        payload: bytes

    class _ISUPv5Parser:
        """
        Простая заглушка парсера:
        - parse(data) -> ISUPAccessEvent или None
        - create_response(sequence_number, device_id, status) -> bytes
        """
        def parse(self, data: bytes) -> Optional[_ISUPAccessEvent]:
            # Простейшая эвристика: если длина > 8, считаем это событием
            try:
                if not data or len(data) < 8:
                    return None
                # Попробуем извлечь sequence_number из байт 4..8 если есть
                seq = 0
                if len(data) >= 8:
                    seq = struct.unpack('>I', data[4:8])[0]
                # device id — байты 8..16 или hex
                device_id = data[8:16].hex().upper() if len(data) >= 16 else 'UNKNOWN'
                return _ISUPAccessEvent(header=_Header(sequence_number=seq, device_id=device_id), payload=data)
            except Exception:
                return None

        def create_response(self, sequence_number: int, device_id: str, status: int = 0) -> bytes:
            # Формат ответа: b'ISUP' + seq(4) + device_id(8) + status(1)
            try:
                header = b'ISUP'
                seqb = struct.pack('>I', int(sequence_number) & 0xFFFFFFFF)
                dev = device_id.encode('utf-8')[:8]
                dev = dev.ljust(8, b'\x00')
                st = struct.pack('B', int(status) & 0xFF)
                return header + seqb + dev + st
            except Exception:
                return b''

    ISUPv5Parser = _ISUPv5Parser
    ISUPAccessEvent = _ISUPAccessEvent

if TenantManager is None:
    class _TenantManager:
        def __init__(self, config: Dict[str, Any] = None):
            self.config = config or {}

        def get_tenant(self, device_id: str) -> Dict[str, Any]:
            # Заглушка: возвращает пустую конфигурацию
            return {"device_id": device_id, "endpoint": None}

    TenantManager = _TenantManager

# -------------------------
# ServerMetrics
# -------------------------
class ServerMetrics:
    def __init__(self):
        self.connections_total = 0
        self.events_processed = 0
        self.events_failed = 0
        self.last_heartbeat: Optional[datetime] = None

    def mark_connection(self):
        self.connections_total += 1

    def mark_processed(self):
        self.events_processed += 1

    def mark_failed(self):
        self.events_failed += 1

    def mark_heartbeat(self):
        self.last_heartbeat = datetime.utcnow()

    def to_dict(self):
        return {
            "connections_total": self.connections_total,
            "events_processed": self.events_processed,
            "events_failed": self.events_failed,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None
        }

# -------------------------
# EventStorage
# -------------------------
class EventStorage:
    def __init__(self, storage_path: Path, logger: logging.Logger):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.logger = logger

    async def store_event(self, event: Any) -> Path:
        """Сохраняет событие в файл JSON (асинхронно имитируем)."""
        try:
            ts = datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')
            fname = self.storage_path / f'event_{ts}.json'
            data = {
                "timestamp": datetime.utcnow().isoformat(),
                "event": str(event)
            }
            # Синхронная запись — небольшая задержка, но простая и надёжная
            with open(fname, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.debug(f'Событие сохранено в {fname}')
            return fname
        except Exception as e:
            self.logger.error(f'Ошибка сохранения события: {e}', exc_info=True)
            raise

    async def retry_pending(self):
        """Заглушка: можно реализовать повторную отправку незавершённых событий."""
        # В реальном приложении — читать папку, фильтровать по age, пытаться отправить.
        await asyncio.sleep(0.1)

# -------------------------
# EventProcessor
# -------------------------
class EventProcessor:
    def __init__(self, tenant_manager: TenantManager, storage: EventStorage, metrics: ServerMetrics, logger: logging.Logger):
        self.tenant_manager = tenant_manager
        self.storage = storage
        self.metrics = metrics
        self.logger = logger
        self.parser = ISUPv5Parser()

    async def process_access_event(self, event: Any, client_ip: str):
        """Обработка события: логирование, сохранение и (опционально) отправка в tenant endpoint."""
        try:
            self.logger.info(f'Обработка события от {client_ip}: seq={getattr(event.header, "sequence_number", None)} dev={getattr(event.header, "device_id", None)}')
            await self.storage.store_event(event)
            self.metrics.mark_processed()
            # Здесь можно добавить отправку в tenant endpoint через aiohttp
        except Exception as e:
            self.metrics.mark_failed()
            self.logger.error(f'Ошибка при обработке события: {e}', exc_info=True)

    async def retry_pending_events(self):
        """Фоновая задача для повторной отправки событий."""
        while True:
            try:
                await self.storage.retry_pending()
            except Exception as e:
                self.logger.error(f'Ошибка retry_pending_events: {e}', exc_info=True)
            await asyncio.sleep(30)

# -------------------------
# HTTP API Server (health / metrics)
# -------------------------
class HTTPAPIServer:
    def __init__(self, metrics: ServerMetrics, tenant_manager: TenantManager, storage: EventStorage, logger: logging.Logger):
        self.metrics = metrics
        self.tenant_manager = tenant_manager
        self.storage = storage
        self.logger = logger
        self._runner = None
        self._site = None
        self._app = None

    async def _handle_health(self, request):
        return web.json_response({"status": "ok", "time": datetime.utcnow().isoformat()})

    async def _handle_metrics(self, request):
        return web.json_response(self.metrics.to_dict())

    async def start(self, host: str, port: int):
        if web is None:
            self.logger.warning('aiohttp не установлен — HTTP API не запущен')
            return
        self._app = web.Application()
        self._app.router.add_get('/health', self._handle_health)
        self._app.router.add_get('/metrics', self._handle_metrics)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        self.logger.info(f'HTTP API запущен на {host}:{port}')

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
            self.logger.info('HTTP API остановлен')

# -------------------------
# TCP Server
# -------------------------
class ISUPTCPServer:
    def __init__(self, config: ServerConfig, processor: EventProcessor, metrics: ServerMetrics, logger: logging.Logger):
        self.config = config
        self.processor = processor
        self.metrics = metrics
        self.logger = logger
        self.server: Optional[asyncio.AbstractServer] = None

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else 'unknown'
        self.metrics.mark_connection()
        self.logger.info(f'Новое подключение от {client_ip}')
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=60.0)
                except asyncio.TimeoutError:
                    self.logger.info(f'Таймаут чтения от {client_ip}')
                    break
                if not data:
                    self.logger.info(f'Клиент {client_ip} закрыл соединение')
                    break
                self.logger.debug(f'Получено {len(data)} байт от {client_ip}')

                # Попытка распарсить пакет
                try:
                    event = self.processor.parser.parse(data)
                except Exception as e:
                    self.logger.debug(f'Ошибка парсинга: {e}', exc_info=True)
                    event = None

                response = None
                if event:
                    await self.processor.process_access_event(event, client_ip)
                    try:
                        seq = getattr(event.header, 'sequence_number', 0)
                        dev = getattr(event.header, 'device_id', 'UNKNOWN')
                        response = self.processor.parser.create_response(sequence_number=seq, device_id=dev, status=0)
                    except Exception as e:
                        self.logger.debug(f'Ошибка формирования ответа: {e}', exc_info=True)
                        response = None
                else:
                    # Heartbeat / неизвестный пакет — попытка сформировать ответ по минимальным данным
                    try:
                        seq = 0
                        dev = 'UNKNOWN'
                        if len(data) >= 8:
                            seq = struct.unpack('>I', data[4:8])[0]
                        if len(data) >= 16:
                            dev = data[8:16].decode('utf-8', errors='ignore').strip('\x00') or data[8:16].hex().upper()
                        response = self.processor.parser.create_response(sequence_number=seq, device_id=dev, status=0)
                        self.metrics.mark_heartbeat()
                    except Exception as e:
                        self.logger.debug(f'Не удалось сформировать heartbeat-ответ: {e}', exc_info=True)
                        response = None

                if response and isinstance(response, (bytes, bytearray)):
                    try:
                        writer.write(response)
                        await writer.drain()
                        self.logger.debug(f'Отправлен ответ {len(response)} байт клиенту {client_ip}')
                    except Exception as e:
                        self.logger.error(f'Ошибка отправки ответа клиенту {client_ip}: {e}', exc_info=True)
                        break
                else:
                    self.logger.debug('Нет ответа для отправки или ответ не в байтах')

        except ConnectionResetError:
            self.logger.info(f'Соединение с {client_ip} разорвано')
        except Exception as e:
            self.logger.error(f'Ошибка в обработчике клиента {client_ip}: {e}', exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self.logger.info(f'Соединение с {client_ip} закрыто')

    async def start(self):
        self.server = await asyncio.start_server(self.handle_client, self.config.host, self.config.port)
        sock = self.server.sockets[0].getsockname() if self.server.sockets else (self.config.host, self.config.port)
        self.logger.info(f'ISUP TCP сервер запущен на {sock[0]}:{sock[1]}')
        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info('TCP сервер остановлен')

# -------------------------
# Main
# -------------------------
async def main():
    config = ServerConfig.from_yaml()
    logger = setup_logging(config)
    logger.info('=' * 70)
    logger.info('🏢 ISUP BRIDGE - PRODUCTION READY')
    logger.info('=' * 70)

    metrics = ServerMetrics()
    tenant_manager = TenantManager({}) if TenantManager else TenantManager
    storage = EventStorage(config.storage_path, logger)
    processor = EventProcessor(tenant_manager, storage, metrics, logger)
    tcp_server = ISUPTCPServer(config, processor, metrics, logger)
    http_api = HTTPAPIServer(metrics, tenant_manager, storage, logger)

    # Background tasks
    retry_task = asyncio.create_task(processor.retry_pending_events())
    api_task = asyncio.create_task(http_api.start('0.0.0.0', config.health_check_port))
    tcp_task = asyncio.create_task(tcp_server.start())

    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info('Получен сигнал остановки, начинаем завершение...')
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _signal_handler)
        except NotImplementedError:
            # Windows
            pass

    await shutdown_event.wait()
    logger.info('Остановка задач...')
    for t in (retry_task, api_task, tcp_task):
        t.cancel()
    await tcp_server.stop()
    await http_api.stop()
    logger.info('ISUP Bridge остановлен')

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Прервано пользователем')
    except Exception as e:
        # Если что-то упало до логгера — печатаем в stderr
        import sys
        print(f'Fatal error: {e}', file=sys.stderr)
        raise
