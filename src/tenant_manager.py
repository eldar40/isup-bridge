"""
Tenant Manager для новой структуры конфигурации
"""

import fnmatch
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class ObjectConfig:
    """Конфигурация объекта"""
    
    def __init__(self, config: Dict):
        self.object_id = config['object_id']
        self.name = config['name']
        self.description = config.get('description', '')
        self.address = config.get('address', '')
        
        # 1C сервер
        c1_config = config['c1_server']
        self.c1_base_url = c1_config['base_url']
        self.c1_endpoint = c1_config.get('endpoint', '/hs/access/events')
        self.c1_username = c1_config.get('username')
        self.c1_password = c1_config.get('password')
        self.c1_timeout = c1_config.get('timeout', 30)
        self.c1_max_retries = c1_config.get('max_retries', 3)
        self.c1_retry_delay = c1_config.get('retry_delay', 5)
        
        # Устройства
        self.devices = {}
        for device_config in config.get('devices', []):
            device_id = device_config['device_id']
            self.devices[device_id] = device_config


class TenantManager:
    """Менеджер объектов и устройств"""
    
    def __init__(self, config: Dict):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.objects = {}
        self.device_to_object_map = {}
        self.default_object = None
        
        self.load_configuration(config)
    
    def load_configuration(self, config: Dict):
        """Загрузка конфигурации объектов"""
        # Загрузка объектов
        for obj_config in config.get('objects', []):
            obj = ObjectConfig(obj_config)
            self.objects[obj.object_id] = obj
            
            # Создание маппинга устройств к объектам
            for device_id in obj.devices.keys():
                self.device_to_object_map[device_id] = obj
        
        # Загрузка default объекта
        default_config = config.get('default_object', {})
        if default_config:
            self.default_object = ObjectConfig(default_config)
        
        self.logger.info(f"✅ Загружено {len(self.objects)} объектов и {len(self.device_to_object_map)} устройств")
        
        # Логирование структуры
        for obj_id, obj in self.objects.items():
            self.logger.info(f"🏢 Объект '{obj.name}': {len(obj.devices)} устройств")
            for device_id, device in obj.devices.items():
                self.logger.info(f"   📟 Устройство {device_id}: {device.get('description', 'N/A')}")
    
    def get_object_for_device(self, device_id: str) -> Optional[ObjectConfig]:
        """Получение объекта для устройства"""
        # Прямое соответствие
        if device_id in self.device_to_object_map:
            return self.device_to_object_map[device_id]
        
        # Wildcard поиск (если нужно)
        for known_device_id, obj in self.device_to_object_map.items():
            if fnmatch.fnmatch(device_id, known_device_id):
                return obj
        
        # Default объект
        if self.default_object:
            self.logger.warning(f"⚠️ Устройство {device_id} не найдено, используется default объект")
            return self.default_object
        
        self.logger.error(f"❌ Устройство {device_id} не найдено и нет default объекта")
        return None
    
    def get_device_info(self, device_id: str) -> Optional[Dict]:
        """Получение информации об устройстве"""
        obj = self.get_object_for_device(device_id)
        if obj and device_id in obj.devices:
            return obj.devices[device_id]
        return None
    
    def get_statistics(self) -> Dict:
        """Статистика по объектам и устройствам"""
        return {
            'total_objects': len(self.objects),
            'total_devices': len(self.device_to_object_map),
            'objects': {
                obj_id: {
                    'name': obj.name,
                    'device_count': len(obj.devices),
                    'devices': list(obj.devices.keys())
                }
                for obj_id, obj in self.objects.items()
            },
            'has_default_object': self.default_object is not None
        }
