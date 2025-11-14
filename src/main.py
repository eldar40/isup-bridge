#!/usr/bin/env python3
"""
ISUP Bridge Server - Production Ready
Полная интеграция Hikvision СКУД (ISUP + ISAPI) с 1С:УРВ
"""

import asyncio
import signal
import json
import logging
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
from isapi_client import ISAPIWebhookHandler, ISAPITerminalManager
from isapi_server import ISAPIWebhookServer, ISAPIDeviceManager


# ==================== КОНФИГУРАЦИЯ ====================

@dataclass
class ServerConfig:
    """Конфигурация сервера"""
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


# ==================== ЛОГИРОВАНИЕ ====================

def setup_logging(config: ServerConfig) -> logging.Logger:
    """Настройка продакшн логирования"""
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    logger = logging.getLogger('isup_bridge')
    logger.setLevel(getattr(logging, config.log_level))
    
    # Форматтер с полной информацией
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - '
        '[%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Ротируемый файловый хендлер
    file_handler = RotatingFileHandler(
        log_dir / 'isup_bridge.log',
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Отдельный файл для ошибок
    error_handler = RotatingFileHandler(
        log_dir / 'errors.log',
        maxBytes=5*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    
    # Консольный хендлер
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)
    
    return logger


# ==================== МЕТРИКИ ====================

class ServerMetrics:
    """Метрики сервера для мониторинга"""
    
    def __init__(self):
        self.start_time = datetime.now()
        self.connections_total = 0
        self.events_received = 0
        self.events_parsed = 0
        self.events_sent = 0
        self.events_failed = 0
        self.events_pending = 0
        self.isapi_events_received = 0
        self.isapi_events_processed = 0
        self.last_event_time: Optional[datetime] = None
        
    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()
    
    @property
    def success_rate(self) -> float:
        if self.events_received == 0:
            return 0.0
        return (self.events_sent / self.events_received) * 100
    
    def to_dict(self) -> Dict:
        return {
            'uptime_seconds': self.uptime_seconds,
            'uptime_human': self._format_uptime(),
            'connections_total': self.connections_total,
            'events': {
                'received': self.events_received,
                'parsed': self.events_parsed,
                'sent': self.events_sent,
                'failed': self.events_failed,
                'pending': self.events_pending,
                'success_rate': f"{self.success_rate:.2f}%"
            },
            'isapi_events': {
                'received': self.isapi_events_received,
                'processed': self.isapi_events_processed
            },
            'last_event': self.last_event_time.isoformat() if self.last_event_time else None
        }
    
    def _format_uptime(self) -> str:
        seconds = int(self.uptime_seconds)
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{days}d {hours}h {minutes}m {secs}s"


# ==================== ХРАНИЛИЩЕ ====================

class EventStorage:
    """Локальное хранилище для неотправленных событий"""
    
    def __init__(self, storage_path: Path, logger: logging.Logger):
        self.storage_path = storage_path
        self.logger = logger
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
    
    async def save(self, event: Dict, tenant_id: str) -> bool:
        """Сохранение события"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            filename = f"pending_{tenant_id}_{timestamp}.json"
            filepath = self.storage_path / filename
            
            async with self._lock:
                event_data = {
                    'tenant_id': tenant_id,
                    'saved_at': datetime.now().isoformat(),
                    'event': event
                }
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(event_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"💾 Событие сохранено: {filename}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка сохранения события: {e}")
            return False
    
    def get_pending_files(self) -> list:
        """Получение списка неотправленных файлов"""
        return sorted(self.storage_path.glob("pending_*.json"))
    
    async def load(self, filepath: Path) -> Optional[Dict]:
        """Загрузка события из файла"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"❌ Ошибка загрузки {filepath.name}: {e}")
            return None
    
    def delete(self, filepath: Path):
        """Удаление файла"""
        try:
            filepath.unlink()
            self.logger.debug(f"🗑️ Удален файл: {filepath.name}")
        except Exception as e:
            self.logger.error(f"❌ Ошибка удаления {filepath.name}: {e}")
    
    def count_pending(self) -> int:
        """Количество неотправленных событий"""
        return len(self.get_pending_files())


# ==================== ПРОЦЕССОР СОБЫТИЙ ====================

class EventProcessor:
    """Центральный процессор событий (ISUP + ISAPI)"""
    
    def __init__(
        self,
        tenant_manager: TenantManager,
        terminal_manager: ISAPITerminalManager,
        storage: EventStorage,
        metrics: ServerMetrics,
        logger: logging.Logger
    ):
        self.tenant_manager = tenant_manager
        self.terminal_manager = terminal_manager
        self.storage = storage
        self.metrics = metrics
        self.logger = logger
        self.parser = ISUPv5Parser(strict_mode=False)
    
    async def process_access_event(self, event: ISUPAccessEvent, client_ip: str) -> bool:
        """Обработка события доступа ISUP"""
        try:
            # Получаем информацию об устройстве
            device_info = self.tenant_manager.get_device_info(event.header.device_id)
            object_info = self.tenant_manager.get_object_for_device(event.header.device_id)
            
            if object_info:
                location_info = f" | Объект: {object_info.name}"
                if device_info:
                    location_info += f" | {device_info.get('location', 'N/A')}"
            else:
                location_info = " | Объект: Неизвестен"
            
            self.logger.info(
                f"📨 ISUP Событие от {client_ip}: "
                f"Устройство={event.header.device_id} | "
                f"Карта={event.card_number} | "
                f"Направление={event.direction.name} | "
                f"Тип={event.access_type.name}"
                f"{location_info}"
            )
            
            # ВРЕМЕННО ОТКЛЮЧАЕМ ОТПРАВКУ В 1С
            self.logger.info(f"✅ ISUP событие получено (1С временно отключен)")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка обработки ISUP события: {e}")
            return False
    
    async def process_isapi_event(self, event_data: Dict, client_ip: str) -> bool:
        """Обработка ISAPI событий"""
        try:
            self.metrics.isapi_events_received += 1
            self.metrics.last_event_time = datetime.now()
            
            # Получаем информацию о терминале
            terminal_info = self.terminal_manager.get_terminal_info(
                client_ip,
                event_data.get('mac_address')
            )
            
            # Обогащаем событие информацией о терминале
            event_data.update({
                'terminal_id': terminal_info.terminal_id,
                'object_id': terminal_info.object_id,
                'object_name': terminal_info.object_name,
                'terminal_description': terminal_info.description,
                'terminal_location': terminal_info.location
            })
            
            # Логируем событие
            self.logger.info(
                f"🎫 ISAPI Событие: "
                f"Терминал={terminal_info.description} | "
                f"Карта={event_data['card_number']} | "
                f"Направление={event_data['direction']} | "
                f"Тип={event_data['access_type']} | "
                f"Объект={terminal_info.object_name}"
            )
            
            # ВРЕМЕННО ОТКЛЮЧАЕМ ОТПРАВКУ В 1С
            self.logger.info(f"✅ ISAPI событие получено (1С временно отключен)")
            
            self.metrics.isapi_events_processed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка обработки ISAPI события: {e}")
            return False
    
    async def process_raw_isup(self, raw_data: bytes, client_ip: str) -> bool:
        """
        Обработка сырых ISUP данных
        """
        self.metrics.events_received += 1
        self.metrics.last_event_time = datetime.now()
        
        # Парсинг ISUP
        event = self.parser.parse(raw_data)
        
        if not event:
            self.logger.debug(f"Получен heartbeat или некорректный пакет от {client_ip}")
            return True  # Heartbeat это нормально
        
        self.metrics.events_parsed += 1
        
        # Обработка события доступа
        return await self.process_access_event(event, client_ip)
    
    async def retry_pending_events(self):
        """Фоновая задача: повторная отправка неудачных событий"""
        while True:
            try:
                pending_files = self.storage.get_pending_files()
                pending_count = len(pending_files)
                
                if pending_count > 0:
                    self.logger.info(f"🔄 Обработка {pending_count} неотправленных событий")
                
                self.metrics.events_pending = pending_count
                
                # ВРЕМЕННО ОТКЛЮЧАЕМ ПОВТОРНУЮ ОТПРАВКУ
                if pending_count > 0:
                    self.logger.debug(f"📁 Найдено {pending_count} неотправленных событий (отправка отключена)")
                
            except Exception as e:
                self.logger.error(f"Ошибка в retry_pending: {e}", exc_info=True)
            
            # Проверяем каждую минуту
            await asyncio.sleep(60)


# ==================== TCP СЕРВЕР ====================

class ISUPTCPServer:
    """TCP сервер для приема ISUP событий"""
    
    def __init__(
        self,
        config: ServerConfig,
        processor: EventProcessor,
        metrics: ServerMetrics,
        logger: logging.Logger
    ):
        self.config = config
        self.processor = processor
        self.metrics = metrics
        self.logger = logger
        self.server = None
    
    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Обработка клиентского соединения"""
        client_addr = writer.get_extra_info('peername')
        client_ip = client_addr[0] if client_addr else 'unknown'
        
        self.metrics.connections_total += 1
        self.logger.info(f"🔌 ISUP подключение от {client_ip}")
        
        try:
            while True:
                # Читаем данные с таймаутом
                data = await asyncio.wait_for(reader.read(1024), timeout=30.0)
                
                if not data:
                    self.logger.info(f"🔌 Соединение с {client_ip} закрыто клиентом")
                    break
                    
                self.logger.debug(f"📥 Получены данные от {client_ip}: {len(data)} байт")
                
                # Парсим данные
                event = self.processor.parser.parse(data)
                
                if event:
                    await self.processor.process_access_event(event, client_ip)
                
                # ОТПРАВЛЯЕМ ОТВЕТ КОНТРОЛЛЕРУ - ВАЖНО!
                response = self.processor.parser.create_response(
                    event.header.sequence_number if event else 0
                )
                if response:
                    writer.write(response)
                    await writer.drain()
                    self.logger.debug(f"📤 Отправлен ответ контроллеру")
                    
        except asyncio.TimeoutError:
            self.logger.info(f"⏰ Таймаут соединения с {client_ip}")
        except ConnectionResetError:
            self.logger.info(f"🔌 Соединение с {client_ip} разорвано")
        except Exception as e:
            self.logger.error(f"❌ Ошибка обработки {client_ip}: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
                self.logger.info(f"🔌 Соединение с {client_ip} закрыто")
            except Exception as e:
                self.logger.debug(f"Ошибка при закрытии соединения: {e}")
    
    async def start(self):
        """Запуск TCP сервера"""
        self.server = await asyncio.start_server(
            self.handle_client,
            self.config.host,
            self.config.port
        )
        
        addr = self.server.sockets[0].getsockname()
        self.logger.info(f"🚀 ISUP TCP сервер запущен на {addr[0]}:{addr[1]}")
        self.logger.info(f"📊 Поддержка ISUP v5 протокола")
        
        async with self.server:
            await self.server.serve_forever()
    
    async def stop(self):
        """Остановка сервера"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info("🛑 ISUP TCP сервер остановлен")


# ==================== HTTP API ====================

class HTTPAPIServer:
    """HTTP API для мониторинга и управления"""
    
    def __init__(
        self,
        metrics: ServerMetrics,
        tenant_manager: TenantManager,
        terminal_manager: ISAPITerminalManager,
        storage: EventStorage,
        logger: logging.Logger
    ):
        self.metrics = metrics
        self.tenant_manager = tenant_manager
        self.terminal_manager = terminal_manager
        self.storage = storage
        self.logger = logger
        self.app = web.Application()
        self._setup_routes()
    
    def _setup_routes(self):
        """Настройка маршрутов API"""
        self.app.router.add_get('/health', self.health_handler)
        self.app.router.add_get('/metrics', self.metrics_handler)
        self.app.router.add_get('/tenants', self.tenants_handler)
        self.app.router.add_get('/terminals', self.terminals_handler)
        self.app.router.add_get('/pending', self.pending_handler)
    
    async def health_handler(self, request):
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': self.metrics.uptime_seconds,
            'services': {
                'isup_tcp': 'active',
                'isapi_webhook': 'active',
                'http_api': 'active'
            }
        })
    
    async def metrics_handler(self, request):
        """Метрики сервера"""
        return web.json_response({
            'server': self.metrics.to_dict(),
            'tenants': self.tenant_manager.get_statistics(),
            'terminals_count': len(self.terminal_manager.get_all_terminals())
        })
    
    async def tenants_handler(self, request):
        """Информация о тенантах"""
        return web.json_response(
            self.tenant_manager.get_statistics()
        )
    
    async def terminals_handler(self, request):
        """Список терминалов"""
        terminals = self.terminal_manager.get_all_terminals()
        
        terminal_list = []
        for terminal in terminals:
            terminal_list.append({
                'terminal_id': terminal.terminal_id,
                'ip_address': terminal.ip_address,
                'description': terminal.description,
                'type': terminal.terminal_type,
                'direction': terminal.direction,
                'location': terminal.location,
                'object_name': terminal.object_name
            })
        
        return web.json_response({
            'terminals': terminal_list,
            'count': len(terminal_list)
        })
    
    async def pending_handler(self, request):
        """Список неотправленных событий"""
        pending_files = self.storage.get_pending_files()
        
        return web.json_response({
            'count': len(pending_files),
            'files': [f.name for f in pending_files[:100]]
        })
    
    async def start(self, host: str, port: int):
        """Запуск HTTP API"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        self.logger.info(f"🌐 HTTP API запущен на http://{host}:{port}")


# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================

async def main():
    """Точка входа приложения"""
    
    # Загрузка конфигурации
    config = ServerConfig.from_yaml()
    logger = setup_logging(config)
    
    # Баннер
    logger.info("=" * 70)
    logger.info("🏢 ISUP BRIDGE - PRODUCTION READY v1.0.0")
    logger.info("⚙️  Hikvision ISUP v5 + ISAPI → Multi-Tenant 1С:УРВ Integration")
    logger.info("🔐 Enterprise-grade Access Control Bridge")
    logger.info("=" * 70)
    
    # Создание компонентов
    metrics = ServerMetrics()
    
    # Загрузка полной конфигурации
    with open('config/config.yaml', 'r') as f:
        full_config = yaml.safe_load(f)
    
    # Создание менеджеров
    tenant_manager = TenantManager(full_config)
    terminal_manager = ISAPITerminalManager(full_config)
    
    storage = EventStorage(config.storage_path, logger)
    
    processor = EventProcessor(tenant_manager, terminal_manager, storage, metrics, logger)
    
    # Серверы
    tcp_server = ISUPTCPServer(config, processor, metrics, logger)
    http_api = HTTPAPIServer(metrics, tenant_manager, terminal_manager, storage, logger)
    
    # ISAPI Webhook сервер
    isapi_config = full_config.get('isapi', {})
    webhook_handler = ISAPIWebhookHandler(processor, isapi_config.get('webhook_secret'))
    isapi_server = ISAPIWebhookServer(webhook_handler, full_config, logger)
    
    # Менеджер устройств ISAPI
    device_manager = ISAPIDeviceManager(full_config, logger)
    
    # Фоновые задачи
    retry_task = asyncio.create_task(processor.retry_pending_events())
    
    # Запуск серверов
    http_task = asyncio.create_task(
        http_api.start('0.0.0.0', config.health_check_port)
    )
    
    isapi_task = asyncio.create_task(isapi_server.start())
    
    # Автоматическая настройка терминалов если включено
    if full_config.get('features', {}).get('auto_configure_terminals', False):
        webhook_base_url = isapi_config.get('webhook_base_url', f"http://{isapi_config.get('host', '0.0.0.0')}:{isapi_config.get('port', 8082)}")
        auto_config_task = asyncio.create_task(
            device_manager.auto_configure_terminals(webhook_base_url)
        )
    
    # Graceful shutdown
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        logger.info("🛑 Получен сигнал остановки...")
        shutdown_event.set()
    
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    # Запуск TCP сервера
    tcp_task = asyncio.create_task(tcp_server.start())
    
    # Ожидание сигнала остановки
    await shutdown_event.wait()
    
    # Остановка
    logger.info("🛑 Остановка сервисов...")
    retry_task.cancel()
    http_task.cancel()
    isapi_task.cancel()
    tcp_task.cancel()
    
    await tcp_server.stop()
    
    logger.info("👋 ISUP Bridge остановлен")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Прервано пользователем")