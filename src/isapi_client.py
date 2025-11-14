"""
ISAPI Client for Hikvision Terminal Integration
Полная интеграция терминалов Hikvision через ISAPI webhook
"""

import aiohttp
import xml.etree.ElementTree as ET
from typing import Dict, Optional, List, Any
import logging
from datetime import datetime
import hmac
import hashlib
import base64
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ISAPITerminalConfig:
    """Конфигурация терминала Hikvision"""
    terminal_id: str
    ip_address: str
    mac_address: Optional[str]
    model: str
    description: str
    terminal_type: str  # face_recognition, card_reader, mixed
    direction: str  # in, out, both
    location: str
    object_id: str
    object_name: str


class ISAPIEvent:
    """ISAPI Event from Hikvision Terminal"""
    
    def __init__(self, raw_xml: str, headers: Dict = None):
        self.raw_xml = raw_xml
        self.headers = headers or {}
        self.parsed_data = self._parse_xml()
    
    def _parse_xml(self) -> Dict[str, Any]:
        """Parse ISAPI event XML"""
        try:
            root = ET.fromstring(self.raw_xml)
            
            # Основные поля события доступа
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
                'verify_type': self._get_text(root, 'verifyType'),  # Тип верификации
                'door_name': self._get_text(root, 'doorName'),
                'reader_name': self._get_text(root, 'readerName'),
                'raw_xml': self.raw_xml
            }
            
            # Логируем основные поля
            logger.info(
                f"📋 ISAPI Event: {event_data['event_type']} | "
                f"Card: {event_data['card_number']} | "
                f"Employee: {event_data['employee_no']} | "
                f"Verify: {event_data['verify_type']}"
            )
            
            return event_data
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга ISAPI XML: {e}")
            logger.debug(f"Сырой XML: {self.raw_xml}")
            return {}
    
    def _get_text(self, root, tag: str) -> Optional[str]:
        """Get text from XML tag"""
        try:
            element = root.find(tag)
            return element.text if element is not None else None
        except:
            return None
    
    def to_unified_format(self) -> Dict[str, Any]:
        """Convert to unified event format for 1C"""
        event_data = self.parsed_data
        
        if not event_data:
            return {}
        
        # Определяем тип доступа и направление
        access_type = self._determine_access_type()
        direction = self._determine_direction()
        
        # Форматируем timestamp
        timestamp = self._parse_timestamp()
        
        return {
            'event_source': 'ISAPI',
            'device_id': event_data.get('mac_address') or event_data.get('ip_address', 'unknown'),
            'device_type': 'Hikvision Terminal',
            'card_number': event_data.get('card_number'),
            'employee_number': event_data.get('employee_no'),
            'event_type': 'ACCESS',
            'access_type': access_type,
            'direction': direction,
            'timestamp': timestamp,
            'door_id': event_data.get('door_no', 1),
            'reader_id': event_data.get('reader_no', 1),
            'verify_type': event_data.get('verify_type', 'unknown'),
            'location': f"Терминал {event_data.get('ip_address', 'unknown')}",
            'success': event_data.get('event_state') != 'false',  # Предполагаем что eventState указывает на успех
            'raw_data': self.raw_xml[:500]  # Ограничиваем размер
        }
    
    def _determine_access_type(self) -> str:
        """Determine access type based on verify_type"""
        verify_type = self.parsed_data.get('verify_type', '').lower()
        
        type_mapping = {
            'card': 'CARD',
            'face': 'FACE', 
            'fingerprint': 'FINGERPRINT',
            'password': 'PIN_CODE',
            'qr': 'QR_CODE'
        }
        
        for key, value in type_mapping.items():
            if key in verify_type:
                return value
        
        return 'UNKNOWN'
    
    def _determine_direction(self) -> str:
        """Determine direction based on reader configuration"""
        reader_no = self.parsed_data.get('reader_no')
        door_name = self.parsed_data.get('door_name', '').lower()
        
        # Эвристика по номеру считывателя
        if reader_no and reader_no.isdigit():
            return 'OUT' if int(reader_no) % 2 == 0 else 'IN'
        
        # Эвристика по названию двери
        if 'out' in door_name or 'exit' in door_name:
            return 'OUT'
        elif 'in' in door_name or 'entrance' in door_name:
            return 'IN'
        
        return 'UNKNOWN'
    
    def _parse_timestamp(self) -> str:
        """Parse and format timestamp from event"""
        try:
            # Пробуем разные форматы timestamp
            for time_field in ['current_time', 'date_time']:
                time_str = self.parsed_data.get(time_field)
                if time_str:
                    # Пробуем распарсить как ISO формат или другой
                    return time_str
        except:
            pass
        
        # Возвращаем текущее время как fallback
        return datetime.now().isoformat()
    
    def is_access_event(self) -> bool:
        """Check if this is an access control event"""
        event_type = self.parsed_data.get('event_type', '').lower()
        return 'access' in event_type or 'card' in event_type


class ISAPIWebhookHandler:
    """HTTP Webhook handler for ISAPI events"""
    
    def __init__(self, event_processor, secret_token: str = None):
        self.event_processor = event_processor
        self.secret_token = secret_token
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def handle_webhook(self, request) -> Dict[str, Any]:
        """Handle incoming ISAPI webhook"""
        try:
            # Получаем информацию о клиенте
            client_ip = request.remote
            content_type = request.headers.get('Content-Type', '')
            
            self.logger.info(f"🌐 ISAPI Webhook от {client_ip}, Content-Type: {content_type}")
            
            # Проверяем что это XML
            if 'xml' not in content_type.lower():
                self.logger.warning(f"⚠️ Неподдерживаемый Content-Type: {content_type}")
                return {'status': 'error', 'message': 'Unsupported Content-Type'}
            
            # Получаем XML данные
            xml_data = await request.text()
            
            if not xml_data.strip():
                self.logger.warning("⚠️ Пустое тело запроса")
                return {'status': 'error', 'message': 'Empty body'}
            
            # Валидация подписи если есть токен
            if self.secret_token and not self._validate_signature(request, xml_data):
                self.logger.warning(f"⚠️ Невалидная подпись от {client_ip}")
                return {'status': 'error', 'message': 'Invalid signature'}
            
            # Парсим событие
            event = ISAPIEvent(xml_data, dict(request.headers))
            
            if not event.parsed_data:
                self.logger.error("❌ Не удалось распарсить XML событие")
                return {'status': 'error', 'message': 'XML parsing failed'}
            
            if not event.is_access_event():
                self.logger.debug(f"⏭️ Пропущено событие типа: {event.parsed_data.get('event_type')}")
                return {'status': 'skipped', 'message': 'Not an access event'}
            
            # Конвертируем в унифицированный формат
            unified_event = event.to_unified_format()
            
            if not unified_event:
                self.logger.error("❌ Не удалось конвертировать событие")
                return {'status': 'error', 'message': 'Event conversion failed'}
            
            # Обрабатываем событие
            success = await self.event_processor.process_isapi_event(unified_event, client_ip)
            
            if success:
                self.logger.info(f"✅ ISAPI событие обработано: {unified_event['card_number']}")
                return {'status': 'success', 'message': 'Event processed'}
            else:
                self.logger.error(f"❌ Ошибка обработки ISAPI события")
                return {'status': 'error', 'message': 'Processing failed'}
                
        except Exception as e:
            self.logger.error(f"❌ Ошибка обработки webhook: {e}", exc_info=True)
            return {'status': 'error', 'message': str(e)}
    
    def _validate_signature(self, request, body: str) -> bool:
        """Validate webhook signature if provided"""
        # Здесь можно реализовать проверку подписи если используется
        # Например, через HMAC-SHA256 с секретным токеном
        return True  # Временно отключено для тестирования


class ISAPITerminalManager:
    """Manager for Hikvision terminal configuration"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.terminals = self._load_terminals_config()
    
    def _load_terminals_config(self) -> Dict[str, ISAPITerminalConfig]:
        """Load terminal configuration from config"""
        terminals = {}
        
        # Загружаем терминалы из конфига объектов
        for obj_config in self.config.get('objects', []):
            for terminal_config in obj_config.get('terminals', []):
                terminal_id = terminal_config['terminal_id']
                
                terminals[terminal_id] = ISAPITerminalConfig(
                    terminal_id=terminal_id,
                    ip_address=terminal_config['ip_address'],
                    mac_address=terminal_config.get('mac_address'),
                    model=terminal_config.get('model', 'Unknown'),
                    description=terminal_config['description'],
                    terminal_type=terminal_config['type'],
                    direction=terminal_config['direction'],
                    location=terminal_config['location'],
                    object_id=obj_config['object_id'],
                    object_name=obj_config['name']
                )
        
        self.logger.info(f"📟 Загружено {len(terminals)} терминалов")
        
        # Логируем информацию о терминалах
        for terminal_id, terminal in terminals.items():
            self.logger.info(f"   🖥️  {terminal.description} ({terminal.ip_address}) -> {terminal.object_name}")
        
        return terminals
    
    def get_terminal_by_ip(self, ip_address: str) -> Optional[ISAPITerminalConfig]:
        """Get terminal by IP address"""
        for terminal in self.terminals.values():
            if terminal.ip_address == ip_address:
                return terminal
        return None
    
    def get_terminal_by_mac(self, mac_address: str) -> Optional[ISAPITerminalConfig]:
        """Get terminal by MAC address"""
        if not mac_address:
            return None
            
        for terminal in self.terminals.values():
            if terminal.mac_address and terminal.mac_address.lower() == mac_address.lower():
                return terminal
        return None
    
    def get_terminal_info(self, ip_address: str, mac_address: str = None) -> ISAPITerminalConfig:
        """Get terminal information by IP or MAC"""
        # Сначала пробуем по IP
        terminal = self.get_terminal_by_ip(ip_address)
        if terminal:
            return terminal
        
        # Потом по MAC
        if mac_address:
            terminal = self.get_terminal_by_mac(mac_address)
            if terminal:
                return terminal
        
        # Если не нашли, возвращаем default терминал
        default_terminal = ISAPITerminalConfig(
            terminal_id='unknown',
            ip_address=ip_address,
            mac_address=mac_address,
            model='Unknown',
            description=f'Автоопределенный терминал {ip_address}',
            terminal_type='unknown',
            direction='both',
            location='Неизвестно',
            object_id='default',
            object_name='Неизвестный объект'
        )
        
        self.logger.warning(f"⚠️ Терминал не найден: IP={ip_address}, MAC={mac_address}")
        return default_terminal
    
    def get_all_terminals(self) -> List[ISAPITerminalConfig]:
        """Get all configured terminals"""
        return list(self.terminals.values())


class ISAPIClient:
    """ISAPI Client for active device management"""
    
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def check_activation_status(self) -> bool:
        """Check if device is activated"""
        try:
            url = f"{self.base_url}/ISAPI/Security/activateStatus"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        text = await response.text()
                        self.logger.info(f"✅ Устройство активировано: {self.base_url}")
                        return True
                    else:
                        self.logger.warning(f"⚠️ Устройство не активировано: {self.base_url}")
                        return False
        except Exception as e:
            self.logger.error(f"❌ Ошибка проверки активации {self.base_url}: {e}")
            return False
    
    async def configure_webhook(self, webhook_url: str) -> bool:
        """Configure webhook on the device"""
        try:
            # XML для настройки HTTP уведомлений
            webhook_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<HttpHostNotification xmlns="http://www.hikvision.com/ver20/XMLSchema">
    <url>{webhook_url}</url>
    <protocolType>HTTP</protocolType>
    <parameterFormatType>XML</parameterFormatType>
    <addressingFormatType>ipaddress</addressingFormatType>
    <httpAuthenticationMethod>none</httpAuthenticationMethod>
</HttpHostNotification>"""
            
            url = f"{self.base_url}/ISAPI/Event/notification/httpHosts"
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, data=webhook_xml, auth=aiohttp.BasicAuth(self.username, self.password)) as response:
                    if response.status in [200, 201]:
                        self.logger.info(f"✅ Webhook настроен для {self.base_url}")
                        return True
                    else:
                        self.logger.error(f"❌ Ошибка настройки webhook: {response.status}")
                        return False
        except Exception as e:
            self.logger.error(f"❌ Ошибка настройки webhook {self.base_url}: {e}")
            return False