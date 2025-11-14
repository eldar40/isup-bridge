cat > /opt/isup_bridge/src/isapi_client.py << 'EOF'
"""
ISAPI Client for Hikvision Terminal Integration
"""

import aiohttp
import xml.etree.ElementTree as ET
from typing import Dict, Optional, List, Any
import logging
from datetime import datetime
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
    terminal_type: str
    direction: str
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
                'verify_type': self._get_text(root, 'verifyType'),
                'door_name': self._get_text(root, 'doorName'),
                'reader_name': self._get_text(root, 'readerName'),
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
    
    def to_unified_format(self) -> Dict[str, Any]:
        """Convert to unified event format for 1C"""
        event_data = self.parsed_data
        
        if not event_data:
            return {}
        
        access_type = self._determine_access_type()
        direction = self._determine_direction()
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
            'success': event_data.get('event_state') != 'false',
            'raw_data': self.raw_xml[:500]
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
        
        if reader_no and reader_no.isdigit():
            return 'OUT' if int(reader_no) % 2 == 0 else 'IN'
        
        if 'out' in door_name or 'exit' in door_name:
            return 'OUT'
        elif 'in' in door_name or 'entrance' in door_name:
            return 'IN'
        
        return 'UNKNOWN'
    
    def _parse_timestamp(self) -> str:
        """Parse and format timestamp from event"""
        try:
            for time_field in ['current_time', 'date_time']:
                time_str = self.parsed_data.get(time_field)
                if time_str:
                    return time_str
        except:
            pass
        
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
            client_ip = request.remote
            content_type = request.headers.get('Content-Type', '')
            
            self.logger.info(f"🌐 ISAPI Webhook от {client_ip}")
            
            if 'xml' not in content_type.lower():
                self.logger.warning(f"⚠️ Неподдерживаемый Content-Type: {content_type}")
                return {'status': 'error', 'message': 'Unsupported Content-Type'}
            
            xml_data = await request.text()
            
            if not xml_data.strip():
                self.logger.warning("⚠️ Пустое тело запроса")
                return {'status': 'error', 'message': 'Empty body'}
            
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
            self.logger.error(f"❌ Ошибка обработки webhook: {e}")
            return {'status': 'error', 'message': str(e)}


class ISAPITerminalManager:
    """Manager for Hikvision terminal configuration"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.terminals = self._load_terminals_config()
    
    def _load_terminals_config(self) -> Dict[str, ISAPITerminalConfig]:
        """Load terminal configuration from config"""
        terminals = {}
        
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
        return terminals
    
    def get_terminal_by_ip(self, ip_address: str) -> Optional[ISAPITerminalConfig]:
        """Get terminal by IP address"""
        for terminal in self.terminals.values():
            if terminal.ip_address == ip_address:
                return terminal
        return None
    
    def get_terminal_info(self, ip_address: str, mac_address: str = None) -> ISAPITerminalConfig:
        """Get terminal information by IP or MAC"""
        terminal = self.get_terminal_by_ip(ip_address)
        if terminal:
            return terminal
        
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
        
        self.logger.warning(f"⚠️ Терминал не найден: IP={ip_address}")
        return default_terminal
    
    def get_all_terminals(self) -> List[ISAPITerminalConfig]:
        """Get all configured terminals"""
        return list(self.terminals.values())
EOF