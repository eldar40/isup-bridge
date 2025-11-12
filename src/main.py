#!/usr/bin/env python3
"""ISUP Bridge Server - Production Ready"""
import asyncio
import signal
import json
import logging
import struct
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass, asdict
from logging.handlers import RotatingFileHandler
import yaml
import aiohttp
from aiohttp import web
from isup_protocol import ISUPv5Parser, ISUPAccessEvent
from tenant_manager import TenantManager

# Config dataclass (unchanged)
@dataclass
class ServerConfig:
    host: str
    port: int
    log_level: str
    storage_path: Path
    max_pending_days: int
    health_check_port: int

    @classmethod
    def from_yaml(cls, config_path: str = 'config/config.yaml'):
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls(
            host=data['server']['host'],
            port=data['server']['port'],
            log_level=data['server'].get('log_level', 'INFO'),
            storage_path=Path(data['server']['storage_path']),
            max_pending_days=data['server'].get('max_pending_days', 30),
            health_check_port=data['server'].get('health_check_port', 8081)
        )

def setup_logging(config: ServerConfig) -> logging.Logger:
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger('isup_bridge')
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = RotatingFileHandler(
        log_dir / 'isup_bridge.log', maxBytes=10*1024*1024, backupCount=10, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    error_handler = RotatingFileHandler(
        log_dir / 'errors.log', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)
    return logger

# (Остальные классы EventStorage, ServerMetrics, EventProcessor остаются по логике как в оригинале,
#  с теми же методами; ниже — ключевые места в TCP сервере с исправлениями.)

class ISUPTCPServer:
    def __init__(self, config: ServerConfig, processor, metrics, logger: logging.Logger):
        self.config = config
        self.processor = processor
        self.metrics = metrics
        self.logger = logger
        self.server = None

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_addr = writer.get_extra_info('peername')
        client_ip = client_addr[0] if client_addr else 'unknown'
        self.metrics.connections_total += 1
        self.logger.info(f'🔌 Новое подключение от {client_ip}')
        try:
            while True:
                data = await asyncio.wait_for(reader.read(1024), timeout=30.0)
                if not data:
                    self.logger.info(f'🔌 Соединение с {client_ip} закрыто клиентом')
                    break
                self.logger.debug(f'📥 Получены данные от {client_ip}: {len(data)} байт')

                # Попытка распарсить ISUP событие
                try:
                    event = self.processor.parser.parse(data)
                except Exception as e:
                    self.logger.debug(f'Ошибка парсинга ISUP: {e}', exc_info=True)
                    event = None

                if event:
                    await self.processor.process_access_event(event, client_ip)
                    # Создаём ответ по данным события
                    try:
                        response = self.processor.parser.create_response(
                            sequence_number=event.header.sequence_number,
                            device_id=event.header.device_id,
                            status=0
                        )
                    except Exception as e:
                        self.logger.debug(f'Ошибка создания ответа: {e}', exc_info=True)
                        response = None
                else:
                    # Heartbeat / нераспознанные пакеты: извлекаем seq/device
                    try:
                        if len(data) >= 8:
                            sequence_number = struct.unpack('>I', data[4:8])[0]
                        else:
                            sequence_number = 0
                        device_id_bytes = data[8:16] if len(data) >= 16 else b'UNKNOWN\x00'
                        device_id = device_id_bytes.hex().upper()
                        response = self.processor.parser.create_response(
                            sequence_number=sequence_number,
                            device_id=device_id,
                            status=0
                        )
                    except Exception as e:
                        self.logger.debug(f'Не удалось сформировать heartbeat-ответ: {e}', exc_info=True)
                        response = None

                # Отправляем ответ только если это байты
                if response and isinstance(response, (bytes, bytearray)):
                    try:
                        writer.write(response)
                        await writer.drain()
                        self.logger.debug('📤 Отправлен ответ контроллеру')
                    except Exception as e:
                        self.logger.error(f'Ошибка отправки ответа: {e}', exc_info=True)
                else:
                    self.logger.debug('Ответ отсутствует или не является байтами, пропускаем отправку')

        except asyncio.TimeoutError:
            self.logger.info(f'⏰ Таймаут соединения с {client_ip}')
        except ConnectionResetError:
            self.logger.info(f'🔌 Соединение с {client_ip} разорвано')
        except Exception as e:
            self.logger.error(f'❌ Ошибка обработки клиента {client_ip}: {e}', exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                self.logger.debug(f'Ошибка при закрытии соединения: {e}')
            self.logger.info(f'🔌 Соединение с {client_ip} закрыто')

    async def start(self):
        self.server = await asyncio.start_server(self.handle_client, self.config.host, self.config.port)
        addr = self.server.sockets[0].getsockname()
        self.logger.info(f'🚀 ISUP TCP сервер запущен на {addr[0]}:{addr[1]}')
        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info('🛑 TCP сервер остановлен')

# main() и остальная логика остаются, но при завершении аккуратно отменяем задачи
async def main():
    config = ServerConfig.from_yaml()
    logger = setup_logging(config)
    logger.info('=' * 70)
    logger.info('🏢 ISUP BRIDGE - PRODUCTION READY v1.0.0')
    logger.info('=' * 70)

    metrics = ServerMetrics()
    with open('config/config.yaml', 'r') as f:
        full_config = yaml.safe_load(f)
    tenant_manager = TenantManager(full_config)
    storage = EventStorage(config.storage_path, logger)
    processor = EventProcessor(tenant_manager, storage, metrics, logger)
    tcp_server = ISUPTCPServer(config, processor, metrics, logger)
    http_api = HTTPAPIServer(metrics, tenant_manager, storage, logger)

    retry_task = asyncio.create_task(processor.retry_pending_events())
    api_task = asyncio.create_task(http_api.start('0.0.0.0', config.health_check_port))
    tcp_task = asyncio.create_task(tcp_server.start())

    shutdown_event = asyncio.Event()
    def signal_handler():
        logger.info('🛑 Получен сигнал остановки...')
        shutdown_event.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows fallback
            pass

    await shutdown_event.wait()
    logger.info('🛑 Остановка сервисов...')
    for t in (retry_task, api_task, tcp_task):
        t.cancel()
    await tcp_server.stop()
    logger.info('👋 ISUP Bridge остановлен')

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n👋 Прервано пользователем')
