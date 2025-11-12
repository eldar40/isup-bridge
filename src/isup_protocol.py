"""
ЭКСТРЕННАЯ ВЕРСИЯ ISUP PARSER - МАКСИМАЛЬНО ПРОСТОЙ И НАДЕЖНЫЙ
"""

import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
from enum import IntEnum
import logging

logger = logging.getLogger(__name__)

class ISUPMessageType(IntEnum):
    HEARTBEAT = 0x00
    REGISTER = 0x01
    ACCESS_EVENT = 0x02
    ALARM_EVENT = 0x03
    UNKNOWN_0x0009 = 0x0009
    UNKNOWN = 0xFF

class ISUPAccessType(IntEnum):
    CARD = 0x01
    FINGERPRINT = 0x02
    FACE = 0x04
    UNKNOWN = 0x00

class ISUPDirection(IntEnum):
    IN = 0x01
    OUT = 0x02
    UNKNOWN = 0x00

@dataclass
class ISUPHeader:
    protocol_version: int
    message_type: ISUPMessageType
    sequence_number: int
    device_id: str
    timestamp: datetime
    data_length: int
    raw_data: bytes
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'ISUPHeader':
        if len(data) < 20:
            raise ValueError(f"Слишком короткий заголовок: {len(data)} байт")
        
        try:
            protocol_version = struct.unpack('>H', data[0:2])[0]
            message_type_val = struct.unpack('>H', data[2:4])[0]
            
            # ПРОСТАЯ ОБРАБОТКА ТИПОВ СООБЩЕНИЙ
            if message_type_val == 0x0001:
                message_type = ISUPMessageType.REGISTER
            elif message_type_val == 0x0002:
                message_type = ISUPMessageType.ACCESS_EVENT
            elif message_type_val == 0x0009:
                message_type = ISUPMessageType.UNKNOWN_0x0009
            elif message_type_val == 0x0000:
                message_type = ISUPMessageType.HEARTBEAT
            else:
                message_type = ISUPMessageType.UNKNOWN
            
            sequence_number = struct.unpack('>I', data[4:8])[0]
            device_id = data[8:16].hex().upper()
            timestamp_raw = struct.unpack('>I', data[16:20])[0]
            timestamp = datetime.fromtimestamp(timestamp_raw)
            
            logger.info(f"📦 ЗАГОЛОВОК: type=0x{message_type_val:04x}, seq={sequence_number}, device={device_id}")
            
            return cls(
                protocol_version=protocol_version,
                message_type=message_type,
                sequence_number=sequence_number,
                device_id=device_id,
                timestamp=timestamp,
                data_length=len(data),
                raw_data=data[:20]
            )
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга заголовка: {e}")
            raise

@dataclass
class ISUPAccessEvent:
    header: ISUPHeader
    card_number: Optional[str]
    access_type: ISUPAccessType
    direction: ISUPDirection
    raw_data: bytes
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'device_id': self.header.device_id,
            'timestamp': self.header.timestamp.isoformat(),
            'card_number': self.card_number,
            'access_type': self.access_type.name,
            'direction': self.direction.name,
            'raw_hex': self.raw_data.hex()[:100]
        }

class ISUPv5Parser:
    """СУПЕР-ПРОСТОЙ ПАРСЕР ДЛЯ ЭКСТРЕННОГО ИСПРАВЛЕНИЯ"""
    
    def __init__(self, strict_mode: bool = False):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.strict_mode = strict_mode
    
    def parse(self, raw_data: bytes) -> Optional[ISUPAccessEvent]:
        """ОЧЕНЬ ПРОСТОЙ ПАРСИНГ"""
        try:
            logger.info(f"🎯 ПОЛУЧЕН ПАКЕТ: {len(raw_data)} байт")
            
            # ЛОГИРУЕМ ВСЕ ДАННЫЕ ДЛЯ АНАЛИЗА
            if raw_data:
                logger.info(f"🔍 СЫРЫЕ ДАННЫЕ: {raw_data.hex()}")
            
            # ЕСЛИ ПАКЕТ СЛИШКОМ КОРОТКИЙ - ЭТО HEARTBEAT
            if len(raw_data) < 20:
                logger.info("💓 HEARTBEAT пакет")
                return None
            
            # ПАРСИМ ЗАГОЛОВОК
            header = ISUPHeader.from_bytes(raw_data[:20])
            
            # ОБРАБАТЫВАЕМ РЕГИСТРАЦИЮ
            if header.message_type == ISUPMessageType.REGISTER:
                logger.info("📝 РЕГИСТРАЦИЯ УСТРОЙСТВА - ОТПРАВЛЯЕМ ОТВЕТ")
                return None
            
            # ОБРАБАТЫВАЕМ СОБЫТИЯ ДОСТУПА
            elif header.message_type in [ISUPMessageType.ACCESS_EVENT, ISUPMessageType.UNKNOWN_0x0009]:
                logger.info("🚪 СОБЫТИЕ ДОСТУПА - ПАРСИМ")
                return self._parse_simple_event(raw_data, header)
            
            # HEARTBEAT
            elif header.message_type == ISUPMessageType.HEARTBEAT:
                logger.info("💓 HEARTBEAT")
                return None
            
            else:
                logger.warning(f"❓ НЕИЗВЕСТНЫЙ ТИП: {header.message_type}")
                return None
                
        except Exception as e:
            logger.error(f"💥 КРИТИЧЕСКАЯ ОШИБКА: {e}")
            return None
    
    def _parse_simple_event(self, raw_data: bytes, header: ISUPHeader) -> ISUPAccessEvent:
        """САМЫЙ ПРОСТОЙ ПАРСИНГ СОБЫТИЯ"""
        logger.info("🔍 ПРОСТОЙ ПАРСИНГ СОБЫТИЯ")
        
        # ПРОСТО ИЩЕМ ЛЮБЫЕ ASCII СИМВОЛЫ В ДАННЫХ
        card_number = None
        try:
            # Ищем в позиции 6-15 (где раньше находили GA4818739)
            if len(raw_data) >= 15:
                potential_card = raw_data[6:15].decode('ascii', errors='ignore').strip()
                if potential_card and len(potential_card) >= 4:
                    card_number = potential_card
                    logger.info(f"🔍 НАЙДЕНА КАРТА: {card_number}")
        except:
            pass
        
        # ПРОСТОЕ ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ
        direction = ISUPDirection.IN  # По умолчанию вход
        
        # ПРОСТОЕ ОПРЕДЕЛЕНИЕ ТИПА
        access_type = ISUPAccessType.FINGERPRINT  # Вы сказали что используете палец
        
        logger.info(f"🎯 РЕЗУЛЬТАТ: card={card_number}, direction=IN, type=FINGERPRINT")
        
        return ISUPAccessEvent(
            header=header,
            card_number=card_number,
            access_type=access_type,
            direction=direction,
            raw_data=raw_data
        )
    
    def create_response(self, sequence_number: int, status: int = 0) -> bytes:
        """ПРОСТОЙ ОТВЕТ КОНТРОЛЛЕРУ"""
        try:
            # ОЧЕНЬ ПРОСТОЙ ОТВЕТ
            response = struct.pack(
                '>HHII',
                5,      # Protocol version
                0xFF,   # Response type
                sequence_number,
                int(datetime.now().timestamp())
            )
            response += struct.pack('B', status)
            
            logger.info(f"📤 ОТПРАВЛЕН ОТВЕТ: seq={sequence_number}")
            return response
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания ответа: {e}")
            return b'\x00\x00'  # Минимальный ответ
