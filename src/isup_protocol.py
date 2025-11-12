"""
ISUP v5 Protocol Implementation for Hikvision Access Controllers
Полная реализация протокола ISUP версии 5 с учетом спецификаций ISAPI
"""

import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import IntEnum
import logging
import json

logger = logging.getLogger(__name__)


class ISUPMessageType(IntEnum):
    """Типы сообщений ISUP v5 согласно ISAPI"""
    HEARTBEAT = 0x0001
    REGISTER = 0x0002
    ACCESS_EVENT = 0x0004
    ALARM_EVENT = 0x0008
    DOOR_STATUS = 0x0010
    DEVICE_INFO = 0x0020
    CARD_EVENT = 0x0040
    FACE_EVENT = 0x0080
    FINGERPRINT_EVENT = 0x0100


class ISUPAccessType(IntEnum):
    """Типы доступа согласно ISAPI 2.3.13"""
    CARD = 0x01
    FINGERPRINT = 0x02
    FACE = 0x04
    PIN_CODE = 0x08
    QR_CODE = 0x10
    IRIS = 0x20
    PALM = 0x40
    MULTI_FACTOR = 0x80  # Многофакторная аутентификация


class ISUPDirection(IntEnum):
    """Направление прохода"""
    IN = 0x01
    OUT = 0x02
    UNKNOWN = 0x00


class ISUPAccessResult(IntEnum):
    """Результат прохода согласно ISAPI"""
    SUCCESS = 0x00
    DENIED_INVALID_CARD = 0x01
    DENIED_EXPIRED = 0x02
    DENIED_TIME_RESTRICTION = 0x03
    DENIED_NO_PERMISSION = 0x04
    DENIED_BLACKLIST = 0x05
    DENIED_ANTI_PASSBACK = 0x06  # Нарушение антипассбэка
    DENIED_INTERLOCK = 0x07      # Нарушение межблокировки


class ISUPPersonType(IntEnum):
    """Типы персонажей согласно ISAPI 2.3.11"""
    NORMAL = 0x01      # Обычный сотрудник
    VISITOR = 0x02     # Посетитель
    BLACKLIST = 0x03   # Черный список
    VIP = 0x04         # VIP персона


@dataclass
class ISUPHeader:
    """Заголовок ISUP пакета с учетом ISAPI спецификаций"""
    protocol_version: int  # Версия протокола (5)
    message_type: ISUPMessageType
    sequence_number: int  # Порядковый номер пакета
    device_id: str  # ID устройства (16 байт согласно ISAPI 2.3.20)
    timestamp: datetime
    data_length: int
    encryption_flag: bool  # Флаг шифрования
    compression_flag: bool  # Флаг сжатия
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'ISUPHeader':
        """Парсинг заголовка из байтов с учетом ISAPI"""
        if len(data) < 24:
            raise ValueError(f"Слишком короткий заголовок: {len(data)} байт")
        
        # Структура заголовка ISUP v5 (24 байта):
        # [0-1]   - Protocol version (2 bytes)
        # [2-3]   - Message type (2 bytes)
        # [4-7]   - Sequence number (4 bytes)
        # [8-23]  - Device ID (16 bytes согласно новому формату)
        # [24-27] - Timestamp (4 bytes, Unix time)
        # [28]    - Flags (1 byte: бит 0 - шифрование, бит 1 - сжатие)
        
        protocol_version = struct.unpack('>H', data[0:2])[0]
        message_type = ISUPMessageType(struct.unpack('>H', data[2:4])[0])
        sequence_number = struct.unpack('>I', data[4:8])[0]
        
        # Новый формат Device ID (16 байт) согласно ISAPI 2.3.20
        device_id_bytes = data[8:24]
        device_id = cls._parse_device_id(device_id_bytes)
        
        timestamp_raw = struct.unpack('>I', data[24:28])[0]
        timestamp = datetime.fromtimestamp(timestamp_raw)
        
        flags = data[28] if len(data) > 28 else 0
        encryption_flag = bool(flags & 0x01)
        compression_flag = bool(flags & 0x02)
        
        return cls(
            protocol_version=protocol_version,
            message_type=message_type,
            sequence_number=sequence_number,
            device_id=device_id,
            timestamp=timestamp,
            data_length=len(data),
            encryption_flag=encryption_flag,
            compression_flag=compression_flag
        )
    
    @staticmethod
    def _parse_device_id(device_id_bytes: bytes) -> str:
        """Парсинг Device ID согласно ISAPI 2.3.20"""
        try:
            # Формат: industry#device_type#version#device_id
            device_id_hex = device_id_bytes.hex()
            
            # Для обратной совместимости с 11-значным ID
            if len(device_id_bytes) == 16:
                # Новый 16-байтный формат
                return f"{device_id_bytes[0:2].hex()}#{device_id_bytes[2:4].hex()}#{device_id_bytes[4:6].hex()}#{device_id_bytes[6:].hex()}"
            else:
                # Старый формат
                return device_id_bytes.hex().upper()
        except:
            return device_id_bytes.hex().upper()


@dataclass
class ISUPAccessEvent:
    """Событие доступа ISUP с полным соответствием ISAPI"""
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
    person_type: ISUPPersonType
    group_id: int  # ID группы для многофакторной аутентификации
    temperature: Optional[float]  # Температура для терминалов с термометрией
    mask_status: Optional[bool]   # Статус маски
    raw_data: bytes
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь для совместимости с ISAPI"""
        event_data = {
            'deviceID': self.header.device_id,
            'dateTime': self.header.timestamp.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'cardNo': self.card_number,
            'employeeNo': self.employee_number,
            'accessMethod': self.access_type.name,
            'direction': self.direction.name,
            'result': self.access_result.name,
            'doorNo': self.door_id,
            'readerNo': self.reader_id,
            'personType': self.person_type.name,
            'groupId': self.group_id,
            'success': self.access_result == ISUPAccessResult.SUCCESS,
        }
        
        # Добавляем опциональные поля
        if self.temperature is not None:
            event_data['temperature'] = self.temperature
        if self.mask_status is not None:
            event_data['maskStatus'] = self.mask_status
            
        return event_data
    
    def to_isapi_json(self) -> str:
        """Конвертация в JSON формат ISAPI"""
        event_dict = self.to_dict()
        return json.dumps(event_dict, ensure_ascii=False)


class ISUPv5Parser:
    """
    Полный парсер протокола ISUP v5 с учетом спецификаций ISAPI
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.supported_versions = [5]  # Поддерживаемые версии протокола
    
    def parse(self, raw_data: bytes) -> Optional[ISUPAccessEvent]:
        """
        Главный метод парсинга ISUP пакета
        
        Args:
            raw_data: Сырые байты от контроллера
            
        Returns:
            ISUPAccessEvent или None при ошибке
        """
        try:
            # Проверка минимальной длины
            if len(raw_data) < 24:
                self.logger.debug(f"Короткий пакет ({len(raw_data)} байт)")
                return None
            
            # Парсинг заголовка
            header = ISUPHeader.from_bytes(raw_data[:29])  # 24 байта + 5 байт флагов
            
            # Проверка версии протокола
            if header.protocol_version not in self.supported_versions:
                self.logger.warning(f"Неподдерживаемая версия протокола: {header.protocol_version}")
                return None
            
            # Обработка по типу сообщения
            if header.message_type == ISUPMessageType.ACCESS_EVENT:
                return self._parse_access_event(raw_data, header)
            elif header.message_type == ISUPMessageType.HEARTBEAT:
                self.logger.debug(f"Heartbeat от {header.device_id}")
                return self._parse_heartbeat(raw_data, header)
            elif header.message_type == ISUPMessageType.CARD_EVENT:
                return self._parse_card_event(raw_data, header)
            elif header.message_type == ISUPMessageType.FACE_EVENT:
                return self._parse_face_event(raw_data, header)
            else:
                self.logger.info(f"Тип сообщения {header.message_type} от {header.device_id}")
                return None
                
        except Exception as e:
            self.logger.error(f"Ошибка парсинга ISUP: {e}", exc_info=True)
            return None
    
    def _parse_access_event(self, raw_data: bytes, header: ISUPHeader) -> ISUPAccessEvent:
        """
        Парсинг события доступа согласно ISAPI спецификации
        
        Структура данных события (после заголовка 29 байт):
        [29-30] - Door ID (2 bytes)
        [31-32] - Reader ID (2 bytes)
        [33]    - Access type (1 byte)
        [34]    - Direction (1 byte)
        [35]    - Access result (1 byte)
        [36]    - Verification mode (1 byte)
        [37-38] - Alarm status (2 bytes)
        [39]    - Person type (1 byte)
        [40-41] - Group ID (2 bytes)
        [42-57] - Card number (16 bytes, ASCII)
        [58-73] - Employee number (16 bytes, ASCII)
        [74-77] - Temperature (4 bytes, float, опционально)
        [78]    - Mask status (1 byte, опционально)
        """
        
        if len(raw_data) < 58:
            return self._parse_access_event_basic(raw_data, header)
        
        # Парсинг основных полей
        door_id = struct.unpack('>H', raw_data[29:31])[0]
        reader_id = struct.unpack('>H', raw_data[31:33])[0]
        access_type = ISUPAccessType(raw_data[33])
        direction = ISUPDirection(raw_data[34])
        access_result = ISUPAccessResult(raw_data[35])
        verification_mode = raw_data[36]
        alarm_status = struct.unpack('>H', raw_data[37:39])[0]
        person_type = ISUPPersonType(raw_data[39])
        group_id = struct.unpack('>H', raw_data[40:42])[0]
        
        # Извлечение строковых данных
        card_number = self._extract_string(raw_data[42:58])
        employee_number = self._extract_string(raw_data[58:74])
        
        # Обработка опциональных полей
        temperature = None
        mask_status = None
        
        if len(raw_data) >= 78:
            try:
                temperature = struct.unpack('>f', raw_data[74:78])[0]
                mask_status = bool(raw_data[78])
            except:
                pass
        
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
            person_type=person_type,
            group_id=group_id,
            temperature=temperature,
            mask_status=mask_status,
            raw_data=raw_data
        )
    
    def _parse_access_event_basic(self, raw_data: bytes, header: ISUPHeader) -> ISUPAccessEvent:
        """
        Базовый парсинг для коротких пакетов
        """
        self.logger.debug("Использован базовый парсинг для короткого пакета")
        
        # Минимальный набор полей
        door_id = 1
        reader_id = 1
        access_type = ISUPAccessType.CARD
        direction = ISUPDirection.IN
        access_result = ISUPAccessResult.SUCCESS
        
        if len(raw_data) >= 34:
            door_id = struct.unpack('>H', raw_data[29:31])[0] if len(raw_data) >= 31 else 1
            reader_id = struct.unpack('>H', raw_data[31:33])[0] if len(raw_data) >= 33 else 1
            access_type = ISUPAccessType(raw_data[33]) if len(raw_data) >= 34 else ISUPAccessType.CARD
        
        # Поиск номеров карт и сотрудников
        card_number = self._find_credential_data(raw_data, min_length=4)
        employee_number = self._find_employee_data(raw_data)
        
        return ISUPAccessEvent(
            header=header,
            card_number=card_number,
            employee_number=employee_number,
            access_type=access_type,
            direction=direction,
            access_result=access_result,
            door_id=door_id,
            reader_id=reader_id,
            alarm_status=0,
            verification_mode=0,
            person_type=ISUPPersonType.NORMAL,
            group_id=0,
            temperature=None,
            mask_status=None,
            raw_data=raw_data
        )
    
    def _parse_heartbeat(self, raw_data: bytes, header: ISUPHeader) -> None:
        """Обработка heartbeat сообщений"""
        # Логируем heartbeat для мониторинга
        self.logger.debug(f"Heartbeat от устройства {header.device_id}")
        return None
    
    def _parse_card_event(self, raw_data: bytes, header: ISUPHeader) -> Optional[ISUPAccessEvent]:
        """Парсинг событий с картами"""
        # Аналогично access event, но с акцентом на карты
        return self._parse_access_event(raw_data, header)
    
    def _parse_face_event(self, raw_data: bytes, header: ISUPHeader) -> Optional[ISUPAccessEvent]:
        """Парсинг событий с распознаванием лиц"""
        event = self._parse_access_event(raw_data, header)
        if event:
            event.access_type = ISUPAccessType.FACE
        return event
    
    def _extract_string(self, data: bytes, encoding: str = 'ascii') -> Optional[str]:
        """Извлечение строки из байтов с обработкой нулевых байт"""
        try:
            # Обрезаем по первому нулевому байту
            null_pos = data.find(b'\x00')
            if null_pos >= 0:
                data = data[:null_pos]
            
            string = data.decode(encoding, errors='ignore').strip()
            return string if string else None
        except:
            return None
    
    def _find_credential_data(self, data: bytes, min_length: int = 4) -> Optional[str]:
        """Поиск данных учетных записей в пакете"""
        candidates = []
        
        # Поиск ASCII последовательностей
        for i in range(len(data) - min_length):
            for j in range(i + min_length, min(i + 32, len(data))):
                chunk = data[i:j]
                try:
                    text = chunk.decode('ascii', errors='ignore').strip()
                    if (len(text) >= min_length and 
                        text.isalnum() and 
                        not any(c in text for c in ['\x00', '\xff'])):
                        candidates.append((text, len(text), i))
                except:
                    continue
        
        if candidates:
            # Выбираем самую длинную последовательность
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]
        
        return None
    
    def _find_employee_data(self, data: bytes) -> Optional[str]:
        """Специализированный поиск номеров сотрудников"""
        # Номера сотрудников обычно короче и имеют специфический формат
        return self._find_credential_data(data, min_length=2)
    
    def create_response(self, 
                       sequence_number: int, 
                       device_id: str,
                       status: int = 0,
                       additional_data: Dict[str, Any] = None) -> bytes:
        """
        Создание ответного пакета для контроллера
        
        Args:
            sequence_number: Номер пакета для подтверждения
            device_id: ID устройства
            status: Статус обработки (0 = успех)
            additional_data: Дополнительные данные ответа
            
        Returns:
            Байты ответного пакета
        """
        # Базовая структура ответа ISUP v5
        response = struct.pack(
            '>HH',
            5,  # Protocol version
            0xFFFF  # Response message type
        )
        
        response += struct.pack('>I', sequence_number)
        
        # Device ID (16 байт)
        device_id_bytes = device_id.encode('ascii')[:16].ljust(16, b'\x00')
        response += device_id_bytes
        
        # Timestamp
        response += struct.pack('>I', int(datetime.now().timestamp()))
        
        # Flags
        flags = 0x00  # Без шифрования и сжатия
        response += struct.pack('B', flags)
        
        # Status
        response += struct.pack('B', status)
        
        # Дополнительные данные
        if additional_data:
            try:
                additional_json = json.dumps(additional_data).encode('ascii')
                response += struct.pack('>H', len(additional_json))
                response += additional_json
            except:
                response += struct.pack('>H', 0)
        else:
            response += struct.pack('>H', 0)
        
        return response


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