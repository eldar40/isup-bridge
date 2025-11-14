"""
ISAPI Client for Hikvision Terminal Integration
Поддержка HTTP Listening (webhook) для событий доступа
"""

import aiohttp
import xml.etree.ElementTree as ET
from typing import Dict, Optional, List
import logging
from datetime import datetime
import hmac
import hashlib
import base64

logger = logging.getLogger(__name__)


class ISAPIEvent:
    """ISAPI Event from Hikvision Terminal"""
    
    def __init__(self, raw_xml: str, headers: Dict = None):
        self.raw_xml = raw_xml
        self.headers = headers or {}
        self.parsed_data = self._parse_xml()
    
    def _parse_xml(self) -> Dict:
        """Parse ISAPI event XML"""
        try:
            root = ET.fromstring(self.raw_xml)
            
            # Основные поля события
            event_data = {
                'event_type': self._get_text(root, 'eventType'),
                'event_state': self._get_text(root, 'eventState'),
                'date_time': self._get_text(root, 'dateTime'),
                'ip_address': self._get_text(root, 'ipAddress'),
                'mac_address': self._get_text(root, 'macAddress'),
                'channel_id': self._get_text(root, 'channelID'),
                'card_number': self._get_text(root, 'cardNo'),
                'employee_no': self._get_text(root, 'employeeNo'),
                'door_no': self._get_text(root, 'doorNo'),
                'reader_no': self._get_text(root, 'readerNo'),
                'current_time': self._get_text(root, 'currentTime'),
                'raw_xml': self.raw_xml
            }
            
            logger.info(f"📋 ISAPI Event: {event_data['event_type']} - Card: {event_data['card_number']}")
            return event_data
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга ISAPI XML: {e}")
            return {}
    
    def _get_text(self, root, tag: str) -> Optional[str]:
        """Get text from XML tag"""
        try:
            element = root.find(tag)
            return element.text if element is not None else None
        except:
            return None
    
    def to_unified_format(self) -> Dict:
        """Convert to unified event format for 1C"""
        event_data = self.parsed_data
        
        # Определяем направление по reader_no и door_no
        direction = self._determine_direction()
        
        return {
            'event_source': 'ISAPI',
            'device_id': event_data.get('mac_address') or event_data.get('ip_address'),
            'device_type': 'Hikvision Terminal',
            'card_number': event_data.get('card_number'),
            'employee_number': event_data.get('employee_no'),
            'event_type': 'ACCESS',
            'direction': direction,
            'timestamp': event_data.get('current_time') or event_data.get('date_time') or datetime.now().isoformat(),
            'door_id': event_data.get('door_no', 1),
            'reader_id': event_data.get('reader_no', 1),
            'location': f"Терминал {event_data.get('ip_address')}",
            'raw_data': self.raw_xml[:500]  # Ограничиваем размер
        }
    
    def _determine_direction(self) -> str:
        """Determine direction based on reader configuration"""
        # Эвристика: если reader_no четный - выход, нечетный - вход
        reader_no = self.parsed_data.get('reader_no')
        if reader_no and reader_no.isdigit():
            return 'OUT' if int(reader_no) % 2 == 0 else 'IN'
        return 'UNKNOWN'
    
    def is_access_event(self) -> bool:
        """Check if this is an access control event"""
        return self.parsed_data.get('event_type') == 'access'


class ISAPIWebhookHandler:
    """HTTP Webhook handler for ISAPI events"""
    
    def __init__(self, event_processor, secret_token: str = None):
        self.event_processor = event_processor
        self.secret_token = secret_token
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def handle_webhook(self, request) -> Dict:
        """Handle incoming ISAPI webhook"""
        try:
            # Проверка IP если нужно
            client_ip = request.remote
            self.logger.info(f"🌐 ISAPI Webhook от {client_ip}")
            
            # Получаем XML данные
            xml_data = await request.text()
            
            # Валидация подписи если есть токен
            if self.secret_token and not self._validate_signature(request, xml_data):
                self.logger.warning(f"⚠️ Невалидная подпись от {client_ip}")
                return {'status': 'error', 'message': 'Invalid signature'}
            
            # Парсим событие
            event = ISAPIEvent(xml_data, dict(request.headers))
            
            if not event.is_access_event():
                self.logger.debug(f"⏭️ Пропущено событие типа: {event.parsed_data.get('event_type')}")
                return {'status': 'skipped', 'message': 'Not an access event'}
            
            # Конвертируем в унифицированный формат
            unified_event = event.to_unified_format()
            
            # Обрабатываем событие
            success = await self.event_processor.process_isapi_event(unified_event, client_ip)
            
            if success:
                self.logger.info(f"✅ ISAPI событие обработано: {unified_event['card_number']}")
                return {'status': 'success', 'message': 'Event processed'}
            else:
                self.logger.error(f"❌ Ошибка обработки ISAPI события")
                return {'status': 'error', 'message': 'Processing failed'}
                
        except Exception as e:
            self.logger.error(f"❌ Ошибка обработки webhook: {e}")
            return {'status': 'error', 'message': str(e)}
    
    def _validate_signature(self, request, body: str) -> bool:
        """Validate webhook signature if provided"""
        # Если используется подпись, можно добавить валидацию
        # Например, через HMAC или базовую аутентификацию
        return True  # Временно отключено


class ISAPITerminalManager:
    """Manager for Hikvision terminal configuration"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.terminals = self._load_terminals_config()
    
    def _load_terminals_config(self) -> Dict:
        """Load terminal configuration from config"""
        terminals = {}
        
        for obj_config in self.config.get('objects', []):
            for terminal_config in obj_config.get('terminals', []):
                terminal_id = terminal_config['terminal_id']
                terminals[terminal_id] = {
                    **terminal_config,
                    'object_id': obj_config['object_id'],
                    'object_name': obj_config['name']
                }
        
        self.logger.info(f"📟 Загружено {len(terminals)} терминалов")
        return terminals
    
    def get_terminal_info(self, ip_address: str, mac_address: str = None) -> Optional[Dict]:
        """Get terminal information by IP or MAC"""
        for terminal_id, terminal_info in self.terminals.items():
            if (terminal_info.get('ip_address') == ip_address or 
                terminal_info.get('mac_address') == mac_address):
                return terminal_info
        
        # Если не нашли, возвращаем default
        default_terminal = {
            'terminal_id': 'unknown',
            'object_id': 'default',
            'object_name': 'Неизвестный объект',
            'description': 'Автоопределенный терминал'
        }
        
        self.logger.warning(f"⚠️ Терминал не найден: IP={ip_address}, MAC={mac_address}")
        return default_terminal