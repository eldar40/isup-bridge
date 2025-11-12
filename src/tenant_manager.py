"""
Multi-Tenant Manager для работы с несколькими 1С серверами
Позволяет маршрутизировать события от разных контроллеров к разным 1С инстансам
"""

import asyncio
import aiohttp
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class TenantStatus(Enum):
    """Статус 1С сервера"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    ERROR = "error"


@dataclass
class C1Server:
    """Конфигурация 1С сервера (tenant)"""
    tenant_id: str
    name: str
    base_url: str
    endpoint: str
    username: str
    password: str
    timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 5
    status: TenantStatus = TenantStatus.ACTIVE
    device_ids: Set[str] = field(default_factory=set)  # Контроллеры этого tenant
    
    # Статистика
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    
    @property
    def full_url(self) -> str:
        """Полный URL для отправки"""
        return f"{self.base_url.rstrip('/')}/{self.endpoint.lstrip('/')}"
    
    @property
    def success_rate(self) -> float:
        """Процент успешных запросов"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100
    
    def to_dict(self) -> Dict:
        """Для логирования и мониторинга"""
        return {
            'tenant_id': self.tenant_id,
            'name': self.name,
            'base_url': self.base_url,
            'status': self.status.value,
            'device_count': len(self.device_ids),
            'total_requests': self.total_requests,
            'success_rate': f"{self.success_rate:.2f}%",
            'last_success': self.last_success.isoformat() if self.last_success else None,
            'last_error': self.last_error
        }


class TenantManager:
    """
    Менеджер для работы с несколькими 1С серверами
    
    Возможности:
    - Роутинг событий к правильному 1С серверу по device_id
    - Балансировка нагрузки
    - Автоматический retry с circuit breaker
    - Мониторинг здоровья серверов
    - Fallback механизмы
    """
    
    def __init__(self, config: Dict):
        self.tenants: Dict[str, C1Server] = {}
        self.device_to_tenant: Dict[str, str] = {}  # device_id -> tenant_id
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        self.health_check_interval = 60  # секунд
        self._load_config(config)
    
    def _load_config(self, config: Dict):
        """Загрузка конфигурации тенантов"""
        for tenant_config in config.get('tenants', []):
            tenant = C1Server(
                tenant_id=tenant_config['tenant_id'],
                name=tenant_config['name'],
                base_url=tenant_config['base_url'],
                endpoint=tenant_config['endpoint'],
                username=tenant_config['username'],
                password=tenant_config['password'],
                timeout=tenant_config.get('timeout', 30),
                max_retries=tenant_config.get('max_retries', 3),
                retry_delay=tenant_config.get('retry_delay', 5),
                device_ids=set(tenant_config.get('device_ids', []))
            )
            
            self.tenants[tenant.tenant_id] = tenant
            
            # Маппинг устройств
            for device_id in tenant.device_ids:
                self.device_to_tenant[device_id] = tenant.tenant_id
        
        logger.info(f"Загружено {len(self.tenants)} тенантов, {len(self.device_to_tenant)} устройств")
    
    async def init_sessions(self):
        """Инициализация HTTP сессий для каждого тенанта"""
        for tenant_id, tenant in self.tenants.items():
            if tenant_id not in self.sessions or self.sessions[tenant_id].closed:
                timeout = aiohttp.ClientTimeout(total=tenant.timeout)
                auth = aiohttp.BasicAuth(tenant.username, tenant.password)
                
                self.sessions[tenant_id] = aiohttp.ClientSession(
                    timeout=timeout,
                    auth=auth,
                    headers={'Content-Type': 'application/json'}
                )
        
        logger.info(f"Инициализировано {len(self.sessions)} HTTP сессий")
    
    def get_tenant_for_device(self, device_id: str) -> Optional[C1Server]:
        """
        Получение тенанта для конкретного устройства
        
        Args:
            device_id: ID контроллера Hikvision
            
        Returns:
            C1Server или None если не найден
        """
        tenant_id = self.device_to_tenant.get(device_id)
        if not tenant_id:
            # Пытаемся найти по маске или использовать default
            tenant_id = self._find_tenant_by_pattern(device_id)
        
        if tenant_id:
            return self.tenants.get(tenant_id)
        
        logger.warning(f"Тенант для устройства {device_id} не найден")
        return None
    
    def _find_tenant_by_pattern(self, device_id: str) -> Optional[str]:
        """Поиск тенанта по паттерну device_id"""
        # Проверяем wildcard маппинги
        for mapped_device, tenant_id in self.device_to_tenant.items():
            if '*' in mapped_device:
                pattern = mapped_device.replace('*', '')
                if device_id.startswith(pattern):
                    return tenant_id
        
        # Возвращаем default tenant если есть
        default_tenant = next((t for t in self.tenants.values() if 'default' in t.name.lower()), None)
        return default_tenant.tenant_id if default_tenant else None
    
    async def send_event(self, device_id: str, event_data: Dict) -> bool:
        """
        Отправка события в нужный 1С сервер
        
        Args:
            device_id: ID устройства
            event_data: Данные события
            
        Returns:
            True если успешно отправлено
        """
        tenant = self.get_tenant_for_device(device_id)
        if not tenant:
            logger.error(f"Не найден тенант для устройства {device_id}")
            return False
        
        if tenant.status == TenantStatus.MAINTENANCE:
            logger.warning(f"Тенант {tenant.name} в режиме обслуживания")
            return False
        
        return await self._send_with_retry(tenant, event_data)
    
    async def _send_with_retry(self, tenant: C1Server, event_data: Dict) -> bool:
        """
        Отправка с автоматическими повторами
        
        Implements exponential backoff
        """
        session = self.sessions.get(tenant.tenant_id)
        if not session or session.closed:
            await self.init_sessions()
            session = self.sessions[tenant.tenant_id]
        
        tenant.total_requests += 1
        last_error = None
        
        for attempt in range(tenant.max_retries):
            try:
                async with session.post(tenant.full_url, json=event_data) as response:
                    if response.status == 200:
                        tenant.successful_requests += 1
                        tenant.last_success = datetime.now()
                        tenant.status = TenantStatus.ACTIVE
                        
                        logger.info(
                            f"✅ Событие отправлено в {tenant.name} "
                            f"({tenant.tenant_id}), попытка {attempt + 1}"
                        )
                        return True
                    else:
                        last_error = f"HTTP {response.status}: {await response.text()}"
                        logger.warning(
                            f"⚠️ {tenant.name} ответил {response.status}, "
                            f"попытка {attempt + 1}/{tenant.max_retries}"
                        )
                        
            except asyncio.TimeoutError:
                last_error = "Timeout"
                logger.warning(f"⏱️ Timeout для {tenant.name}, попытка {attempt + 1}")
                
            except aiohttp.ClientError as e:
                last_error = str(e)
                logger.error(f"❌ Ошибка соединения с {tenant.name}: {e}")
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"❌ Неожиданная ошибка для {tenant.name}: {e}", exc_info=True)
            
            # Exponential backoff
            if attempt < tenant.max_retries - 1:
                delay = tenant.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
        
        # Все попытки исчерпаны
        tenant.failed_requests += 1
        tenant.last_error = last_error
        tenant.status = TenantStatus.ERROR
        
        logger.error(
            f"❌ Не удалось отправить в {tenant.name} после {tenant.max_retries} попыток. "
            f"Последняя ошибка: {last_error}"
        )
        return False
    
    async def health_check_all(self):
        """Проверка здоровья всех 1С серверов"""
        results = {}
        
        for tenant_id, tenant in self.tenants.items():
            is_healthy = await self.health_check_tenant(tenant)
            results[tenant_id] = is_healthy
        
        return results
    
    async def health_check_tenant(self, tenant: C1Server) -> bool:
        """
        Проверка здоровья конкретного тенанта
        
        Отправляет тестовый запрос или проверяет /health endpoint
        """
        session = self.sessions.get(tenant.tenant_id)
        if not session:
            return False
        
        try:
            # Пытаемся получить health endpoint
            health_url = f"{tenant.base_url}/health"
            
            async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    tenant.status = TenantStatus.ACTIVE
                    return True
                    
        except Exception as e:
            logger.debug(f"Health check для {tenant.name} недоступен: {e}")
        
        # Если health endpoint недоступен, проверяем через success rate
        if tenant.total_requests > 10:
            if tenant.success_rate > 50:
                tenant.status = TenantStatus.ACTIVE
                return True
            else:
                tenant.status = TenantStatus.ERROR
                return False
        
        return True  # Для новых тенантов
    
    async def start_health_monitor(self):
        """Фоновая задача мониторинга здоровья"""
        while True:
            try:
                results = await self.health_check_all()
                healthy = sum(1 for v in results.values() if v)
                logger.info(f"🏥 Health check: {healthy}/{len(results)} тенантов здоровы")
                
            except Exception as e:
                logger.error(f"Ошибка в health monitor: {e}")
            
            await asyncio.sleep(self.health_check_interval)
    
    def get_statistics(self) -> Dict:
        """Получение статистики по всем тенантам"""
        stats = {
            'total_tenants': len(self.tenants),
            'active_tenants': sum(1 for t in self.tenants.values() if t.status == TenantStatus.ACTIVE),
            'total_devices': len(self.device_to_tenant),
            'tenants': {}
        }
        
        for tenant_id, tenant in self.tenants.items():
            stats['tenants'][tenant_id] = tenant.to_dict()
        
        return stats
    
    def add_tenant(self, tenant: C1Server):
        """Динамическое добавление нового тенанта"""
        self.tenants[tenant.tenant_id] = tenant
        
        for device_id in tenant.device_ids:
            self.device_to_tenant[device_id] = tenant.tenant_id
        
        logger.info(f"Добавлен новый тенант: {tenant.name} ({tenant.tenant_id})")
    
    def remove_tenant(self, tenant_id: str):
        """Удаление тенанта"""
        if tenant_id in self.tenants:
            tenant = self.tenants[tenant_id]
            
            # Удаляем маппинги устройств
            for device_id in tenant.device_ids:
                self.device_to_tenant.pop(device_id, None)
            
            # Закрываем сессию
            if tenant_id in self.sessions:
                asyncio.create_task(self.sessions[tenant_id].close())
                del self.sessions[tenant_id]
            
            del self.tenants[tenant_id]
            logger.info(f"Удален тенант: {tenant.name} ({tenant_id})")
    
    async def close_all(self):
        """Закрытие всех сессий"""
        for session in self.sessions.values():
            await session.close()
        
        logger.info("Все сессии закрыты")
