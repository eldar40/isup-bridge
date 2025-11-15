"""
ISUP v5 Protocol Parser for Hikvision Access Control
"""

import struct
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ISUPAccessType(Enum):
    """Типы доступа ISUP"""
    CARD = 1
    FINGERPRINT = 2
    FACE = 3
    PIN_CODE = 4
    QR_CODE = 5
    COMBINED = 6
    UNKNOWN = 99


class ISUPDirection(Enum):
    """Направление прохода"""
    IN = 1
    OUT = 2
    UNKNOWN = 0


@dataclass
class ISUPHeader:
    """Заголовок ISUP пакета"""
    start_marker: bytes
    version: int
    command_type: int
    data_length: int
    device_id: str
    sequence_number: int
    checksum: int


@dataclass
class ISUPAccessEvent:
    """Событие доступа ISUP"""
    header: ISUPHeader
    card_number: str
    access_type: ISUPAccessType
    direction: ISUPDirection
    timestamp: datetime
    door_number: int
    reader_number: int
    user_id: str
    verify_result: int


class ISUPv5Parser:
    """Парсер ISUP v5 протокола"""
    
    def __init__(self, strict_mode: bool = False):
        self.strict_mode = strict_mode
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def parse(self, data: bytes) -> Optional[ISUPAccessEvent]:
        """Парсинг сырых данных ISUP"""
        try:
            if len(data) < 20:
                self.logger.debug("Слишком короткий пакет для ISUP")
                return None
            
            # Проверка маркера начала пакета
            if data[0:2] != b'##':
                self.logger.debug("Неверный маркер начала пакета")
                return None
            
            # Парсинг заголовка
            header = self._parse_header(data)
            if not header:
                return None
            
            # Проверка контрольной суммы
            if not self._verify_checksum(data, header):
                self.logger.warning("Неверная контрольная сумма")
                if self.strict_mode:
                    return None
            
            # Парсинг данных события
            event_data = data[20:20 + header.data_length]
            return self._parse_event_data(header, event_data)
            
        except Exception as e:
            self.logger.error(f"Ошибка парсинга ISUP пакета: {e}")
            return None
    
    def _parse_header(self, data: bytes) -> Optional[ISUPHeader]:
        """Парсинг заголовка пакета"""
        try:
            # ## + версия + тип команды + длина данных
            version = data[2]
            command_type = data[3]
            data_length = struct.unpack('>H', data[4:6])[0]
            
            # Device ID (8 байт)
            device_id_bytes = data[6:14]
            device_id = device_id_bytes.decode('ascii', errors='ignore').rstrip('\x00')
            
            # Sequence number + checksum
            sequence_number = struct.unpack('>H', data[14:16])[0]
            checksum = struct.unpack('>H', data[16:18])[0]
            
            return ISUPHeader(
                start_marker=data[0:2],
                version=version,
                command_type=command_type,
                data_length=data_length,
                device_id=device_id,
                sequence_number=sequence_number,
                checksum=checksum
            )
        except Exception as e:
            self.logger.error(f"Ошибка парсинга заголовка: {e}")
            return None
    
    def _parse_event_data(self, header: ISUPHeader, data: bytes) -> Optional[ISUPAccessEvent]:
        """Парсинг данных события"""
        try:
            if len(data) < 20:
                return None
            
            # Парсинг основных полей события
            card_number = self._parse_card_number(data)
            access_type = self._parse_access_type(data)
            direction = self._parse_direction(data)
            timestamp = self._parse_timestamp(data)
            
            # Дополнительные поля
            door_number = data[16] if len(data) > 16 else 1
            reader_number = data[17] if len(data) > 17 else 1
            user_id = self._parse_user_id(data)
            verify_result = data[19] if len(data) > 19 else 1
            
            event = ISUPAccessEvent(
                header=header,
                card_number=card_number,
                access_type=access_type,
                direction=direction,
                timestamp=timestamp,
                door_number=door_number,
                reader_number=reader_number,
                user_id=user_id,
                verify_result=verify_result
            )
            
            self.logger.debug(f"Парсинг события: {card_number}, {access_type}, {direction}")
            return event
            
        except Exception as e:
            self.logger.error(f"Ошибка парсинга данных события: {e}")
            return None
    
    def _parse_card_number(self, data: bytes) -> str:
        """Парсинг номера карты"""
        try:
            # Номер карты обычно в первых 8 байтах
            card_bytes = data[0:8]
            
            # Пробуем разные кодировки
            try:
                # ASCII декодинг
                card_str = card_bytes.decode('ascii', errors='ignore').rstrip('\x00')
                if card_str and any(c.isalnum() for c in card_str):
                    return card_str
            except:
                pass
            
            # HEX представление
            hex_repr = card_bytes.hex().upper()
            if hex_repr and hex_repr != '0000000000000000':
                return hex_repr
            
            return "UNKNOWN"
            
        except Exception as e:
            self.logger.error(f"Ошибка парсинга номера карты: {e}")
            return "ERROR"
    
    def _parse_access_type(self, data: bytes) -> ISUPAccessType:
        """Определение типа доступа"""
        try:
            if len(data) > 8:
                verify_type = data[8]
                return {
                    1: ISUPAccessType.CARD,
                    2: ISUPAccessType.FINGERPRINT,
                    3: ISUPAccessType.FACE,
                    4: ISUPAccessType.PIN_CODE,
                    5: ISUPAccessType.QR_CODE,
                    6: ISUPAccessType.COMBINED
                }.get(verify_type, ISUPAccessType.UNKNOWN)
            return ISUPAccessType.UNKNOWN
        except:
            return ISUPAccessType.UNKNOWN
    
    def _parse_direction(self, data: bytes) -> ISUPDirection:
        """Определение направления"""
        try:
            if len(data) > 9:
                direction_byte = data[9]
                if direction_byte == 1:
                    return ISUPDirection.IN
                elif direction_byte == 2:
                    return ISUPDirection.OUT
            return ISUPDirection.UNKNOWN
        except:
            return ISUPDirection.UNKNOWN
    
    def _parse_timestamp(self, data: bytes) -> datetime:
        """Парсинг временной метки"""
        try:
            if len(data) >= 16:
                # Временная метка обычно в байтах 10-15
                timestamp_bytes = data[10:16]
                if len(timestamp_bytes) == 6:
                    # Формат: YY MM DD HH MM SS
                    year = timestamp_bytes[0] + 2000
                    month = timestamp_bytes[1]
                    day = timestamp_bytes[2]
                    hour = timestamp_bytes[3]
                    minute = timestamp_bytes[4]
                    second = timestamp_bytes[5]
                    
                    return datetime(year, month, day, hour, minute, second)
        except Exception as e:
            self.logger.debug(f"Ошибка парсинга временной метки: {e}")
        
        return datetime.now()
    
    def _parse_user_id(self, data: bytes) -> str:
        """Парсинг ID пользователя"""
        try:
            if len(data) > 20:
                user_bytes = data[20:28]
                user_str = user_bytes.decode('ascii', errors='ignore').rstrip('\x00')
                if user_str:
                    return user_str
        except:
            pass
        return ""
    
    def _verify_checksum(self, data: bytes, header: ISUPHeader) -> bool:
        """Проверка контрольной суммы"""
        try:
            if len(data) < 20 + header.data_length:
                return False
            
            # Вычисляем контрольную сумму
            calculated = 0
            for byte in data[0:16]:  # Все байты кроме checksum
                calculated = (calculated + byte) & 0xFFFF
            
            return calculated == header.checksum
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки контрольной суммы: {e}")
            return not self.strict_mode
    
    def create_response(self, sequence_number: int) -> bytes:
        """Создание ответа контроллеру"""
        try:
            response = b'##'  # Start marker
            response += bytes([1, 1])  # Version + command type
            response += struct.pack('>H', 0)  # Data length = 0
            response += b'\x00' * 8  # Device ID (zeros for response)
            response += struct.pack('>H', sequence_number)  # Sequence number
            
            # Calculate checksum
            checksum = 0
            for byte in response:
                checksum = (checksum + byte) & 0xFFFF
            
            response += struct.pack('>H', checksum)
            return response
            
        except Exception as e:
            self.logger.error(f"Ошибка создания ответа: {e}")
            # Возвращаем минимальный ответ при ошибке
            return struct.pack('>HHI', 5, 0xFFFF, sequence_number)


# Дополнительные утилиты для работы с ISUP
class ISUPUtils:
    """Утилиты для работы с протоколом ISUP"""
    
    @staticmethod
    def validate_device_id(device_id: str) -> bool:
        """Проверка валидности Device ID согласно ISAPI"""
        if len(device_id) == 11:  # Старый формат
            return device_id.isdigit()
        elif '#' in device_id:    # Новый формат
            parts = device_id.split('#')
            return len(parts) == 4
        else:
            return len(device_id) >= 8
    
    @staticmethod
    def convert_to_isapi_format(event: ISUPAccessEvent) -> Dict[str, Any]:
        """Конвертация события в формат ISAPI для интеграции"""
        isapi_event = {
            "AccessControllerEvent": {
                "employeeNoString": event.employee_number or "",
                "cardNo": event.card_number or "",
                "doorNo": event.door_id,
                "readerNo": event.reader_id,
                "currentTime": event.header.timestamp.strftime('%Y-%m-%dT%H:%M:%S%z'),
                "type": "accessEvent",
                "status": "active" if event.access_result == ISUPAccessResult.SUCCESS else "inactive",
                "deviceID": event.header.device_id,
                "verificationMode": event.verification_mode,
                "description": f"Access event from device {event.header.device_id}"
            }
        }
        
        # Добавляем результат доступа
        if event.access_result != ISUPAccessResult.SUCCESS:
            isapi_event["AccessControllerEvent"]["errorCode"] = event.access_result.value
        
        return isapi_event


# Пример использования
if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(level=logging.INFO)
    
    # Создание парсера
    parser = ISUPv5Parser()
    
    # Пример обработки пакета
    sample_data = b'\x00\x05\x00\x04\x00\x00\x00\x01' + b'\x00' * 16 + struct.pack('>I', int(datetime.now().timestamp()))
    
    event = parser.parse(sample_data)
    if event:
        print("Обработано событие:", event.to_dict())
        
        # Конвертация в ISAPI формат
        isapi_format = ISUPUtils.convert_to_isapi_format(event)
        print("ISAPI формат:", json.dumps(isapi_format, indent=2))