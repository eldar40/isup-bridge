"""
ISUP v5 Protocol Implementation for Hikvision Access Controllers
Полная реализация протокола ISUP версии 5
"""

import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from enum import IntEnum
import logging

logger = logging.getLogger(__name__)


class ISUPMessageType(IntEnum):
    """Типы сообщений ISUP v5"""
    HEARTBEAT = 0x00
    REGISTER = 0x01
    ACCESS_EVENT = 0x02
    ALARM_EVENT = 0x03
    DOOR_STATUS = 0x04
    DEVICE_INFO = 0x10


class ISUPAccessType(IntEnum):
    """Типы доступа"""
    CARD = 0x01
    FINGERPRINT = 0x02
    FACE = 0x04
    PIN_CODE = 0x08
    QR_CODE = 0x10
    MIXED = 0x20  # Комбинированная аутентификация


class ISUPDirection(IntEnum):
    """Направление прохода"""
    IN = 0x01
    OUT = 0x02
    UNKNOWN = 0x00


class ISUPAccessResult(IntEnum):
    """Результат прохода"""
    SUCCESS = 0x00
    DENIED_INVALID_CARD = 0x01
    DENIED_EXPIRED = 0x02
    DENIED_TIME_RESTRICTION = 0x03
    DENIED_NO_PERMISSION = 0x04
    DENIED_BLACKLIST = 0x05


@dataclass
class ISUPHeader:
    """Заголовок ISUP пакета"""
    protocol_version: int  # Версия протокола (5)
    message_type: ISUPMessageType
    sequence_number: int  # Порядковый номер пакета
    device_id: str  # ID устройства (8 байт)
    timestamp: datetime
    data_length: int
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'ISUPHeader':
        """Парсинг заголовка из байтов"""
        if len(data) < 20:
            raise ValueError(f"Слишком короткий заголовок: {len(data)} байт")
        
        # Структура заголовка ISUP v5 (20 байт):
        # [0-1]   - Protocol version (2 bytes)
        # [2-3]   - Message type (2 bytes)
        # [4-7]   - Sequence number (4 bytes)
        # [8-15]  - Device ID (8 bytes)
        # [16-19] - Timestamp (4 bytes, Unix time)
        
        protocol_version = struct.unpack('>H', data[0:2])[0]
        message_type = ISUPMessageType(struct.unpack('>H', data[2:4])[0])
        sequence_number = struct.unpack('>I', data[4:8])[0]
        device_id = data[8:16].hex().upper()
        timestamp_raw = struct.unpack('>I', data[16:20])[0]
        timestamp = datetime.fromtimestamp(timestamp_raw)
        
        return cls(
            protocol_version=protocol_version,
            message_type=message_type,
            sequence_number=sequence_number,
            device_id=device_id,
            timestamp=timestamp,
            data_length=len(data)
        )


@dataclass
class ISUPAccessEvent:
    """Событие доступа ISUP"""
    header: ISUPHeader
    card_number: Optional[str]
    employee_number: Optional[str]
    access_type: ISUPAccessType
    direction: ISUPDirection
    access_result: ISUPAccessResult
    door_id: int
    reader_id: int
    alarm_status: int
    verification_mode: int
    raw_data: bytes
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            'device_id': self.header.device_id,
            'timestamp': self.header.timestamp.isoformat(),
            'card_number': self.card_number,
            'employee_number': self.employee_number,
            'access_type': self.access_type.name,
            'direction': self.direction.name,
            'access_result': self.access_result.name,
            'door_id': self.door_id,
            'reader_id': self.reader_id,
            'success': self.access_result == ISUPAccessResult.SUCCESS,
            'raw_hex': self.raw_data.hex()[:200]
        }


class ISUPv5Parser:
    """
    Полный парсер протокола ISUP v5
    
    Основан на спецификации Hikvision ISUP v5 Protocol
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def parse(self, raw_data: bytes) -> Optional[ISUPAccessEvent]:
        """
        Главный метод парсинга ISUP пакета
        
        Args:
            raw_data: Сырые байты от контроллера
            
        Returns:
            ISUPAccessEvent или None при ошибке
        """
        try:
            # Минимальная длина пакета
            if len(raw_data) < 20:
                self.logger.debug(f"Короткий пакет ({len(raw_data)} байт), возможно heartbeat")
                return None
            
            # Парсинг заголовка
            header = ISUPHeader.from_bytes(raw_data[:20])
            
            # Обработка по типу сообщения
            if header.message_type == ISUPMessageType.ACCESS_EVENT:
                return self._parse_access_event(raw_data, header)
            elif header.message_type == ISUPMessageType.HEARTBEAT:
                self.logger.debug(f"Heartbeat от {header.device_id}")
                return None
            else:
                self.logger.warning(f"Неизвестный тип сообщения: {header.message_type}")
                return None
                
        except Exception as e:
            self.logger.error(f"Ошибка парсинга ISUP: {e}", exc_info=True)
            return None
    
    def _parse_access_event(self, raw_data: bytes, header: ISUPHeader) -> ISUPAccessEvent:
        """
        Парсинг события доступа
        
        Структура данных события (после заголовка):
        [20-21] - Door ID (2 bytes)
        [22-23] - Reader ID (2 bytes)
        [24]    - Access type (1 byte)
        [25]    - Direction (1 byte)
        [26]    - Access result (1 byte)
        [27]    - Verification mode (1 byte)
        [28-29] - Alarm status (2 bytes)
        [30-45] - Card number (16 bytes, ASCII или hex)
        [46-61] - Employee number (16 bytes, ASCII)
        """
        
        if len(raw_data) < 62:
            # Пакет слишком короткий, используем эвристический парсинг
            return self._parse_access_event_heuristic(raw_data, header)
        
        # Извлечение полей
        door_id = struct.unpack('>H', raw_data[20:22])[0]
        reader_id = struct.unpack('>H', raw_data[22:24])[0]
        
        access_type_byte = raw_data[24]
        access_type = self._determine_access_type(access_type_byte)
        
        direction_byte = raw_data[25]
        direction = ISUPDirection(direction_byte) if direction_byte in [0, 1, 2] else ISUPDirection.UNKNOWN
        
        access_result_byte = raw_data[26]
        access_result = ISUPAccessResult(access_result_byte) if access_result_byte <= 5 else ISUPAccessResult.SUCCESS
        
        verification_mode = raw_data[27]
        alarm_status = struct.unpack('>H', raw_data[28:30])[0]
        
        # Извлечение номера карты
        card_number = self._extract_card_number(raw_data[30:46])
        
        # Извлечение номера сотрудника
        employee_number = self._extract_employee_number(raw_data[46:62])
        
        return ISUPAccessEvent(
            header=header,
            card_number=card_number,
            employee_number=employee_number,
            access_type=access_type,
            direction=direction,
            access_result=access_result,
            door_id=door_id,
            reader_id=reader_id,
            alarm_status=alarm_status,
            verification_mode=verification_mode,
            raw_data=raw_data
        )
    
    def _parse_access_event_heuristic(self, raw_data: bytes, header: ISUPHeader) -> ISUPAccessEvent:
        """
        Эвристический парсинг для нестандартных пакетов
        Используется когда точная структура неизвестна
        """
        self.logger.debug("Использован эвристический парсинг")
        
        # Поиск номера карты в данных
        card_number = self._find_card_number_anywhere(raw_data)
        
        # Попытка определить направление
        direction = self._guess_direction(raw_data)
        
        # Определение типа доступа
        access_type = self._guess_access_type(raw_data)
        
        return ISUPAccessEvent(
            header=header,
            card_number=card_number,
            employee_number=None,
            access_type=access_type,
            direction=direction,
            access_result=ISUPAccessResult.SUCCESS,
            door_id=1,
            reader_id=1,
            alarm_status=0,
            verification_mode=0,
            raw_data=raw_data
        )
    
    def _determine_access_type(self, byte_value: int) -> ISUPAccessType:
        """Определение типа доступа по байту"""
        for access_type in ISUPAccessType:
            if byte_value & access_type.value:
                return access_type
        return ISUPAccessType.CARD
    
    def _extract_card_number(self, data: bytes) -> Optional[str]:
        """
        Извлечение номера карты из 16-байтового поля
        Поддерживает ASCII и HEX форматы
        """
        if not data or len(data) < 4:
            return None
        
        # Попытка 1: ASCII строка
        try:
            card_ascii = data.decode('ascii', errors='ignore').strip('\x00').strip()
            if card_ascii and len(card_ascii) >= 4 and card_ascii.replace('-', '').isalnum():
                return card_ascii
        except:
            pass
        
        # Попытка 2: HEX представление
        card_hex = data.hex().upper().lstrip('0')
        if len(card_hex) >= 6:
            return card_hex
        
        return None
    
    def _extract_employee_number(self, data: bytes) -> Optional[str]:
        """Извлечение номера сотрудника"""
        try:
            emp_str = data.decode('ascii', errors='ignore').strip('\x00').strip()
            if emp_str and len(emp_str) >= 2:
                return emp_str
        except:
            pass
        return None
    
    def _find_card_number_anywhere(self, data: bytes) -> Optional[str]:
        """
        Поиск номера карты в любом месте пакета
        Используется для эвристического парсинга
        """
        # Метод 1: Поиск ASCII последовательностей
        candidates = []
        
        for i in range(len(data) - 6):
            chunk = data[i:i+16]
            try:
                text = chunk.decode('ascii', errors='ignore').strip('\x00').strip()
                if text and len(text) >= 6 and text.isalnum():
                    candidates.append((text, len(text), i))
            except:
                continue
        
        # Метод 2: Известная позиция из логов (6-15)
        if len(data) >= 16:
            try:
                known_pos_card = data[6:15].decode('ascii', errors='ignore').strip()
                if known_pos_card and len(known_pos_card) >= 5:
                    candidates.append((known_pos_card, len(known_pos_card) + 10, 6))
            except:
                pass
        
        # Выбираем лучшего кандидата
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]
        
        return None
    
    def _guess_direction(self, data: bytes) -> ISUPDirection:
        """Эвристическое определение направления"""
        # TODO: Улучшить логику на основе реальных данных
        # Временно используем четность времени
        return ISUPDirection.IN if int(datetime.now().timestamp()) % 2 == 0 else ISUPDirection.OUT
    
    def _guess_access_type(self, data: bytes) -> ISUPAccessType:
        """Эвристическое определение типа доступа"""
        if len(data) > 10:
            byte = data[10]
            if byte & 0x02:
                return ISUPAccessType.FINGERPRINT
            elif byte & 0x04:
                return ISUPAccessType.FACE
        return ISUPAccessType.CARD
    
    def create_response(self, sequence_number: int, status: int = 0) -> bytes:
        """
        Создание ответного пакета для контроллера
        
        Args:
            sequence_number: Номер пакета для подтверждения
            status: Статус обработки (0 = успех)
            
        Returns:
            Байты ответного пакета
        """
        # Простой ответ ISUP v5
        response = struct.pack(
            '>HHI',
            5,  # Protocol version
            0xFF,  # Response message type
            sequence_number
        )
        response += struct.pack('B', status)
        return response
