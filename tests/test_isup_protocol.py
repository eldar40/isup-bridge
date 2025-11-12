"""
Unit тесты для ISUP v5 протокола
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import unittest
from datetime import datetime
import struct

from isup_protocol import (
    ISUPv5Parser,
    ISUPHeader,
    ISUPAccessEvent,
    ISUPMessageType,
    ISUPAccessType,
    ISUPDirection,
    ISUPAccessResult
)


class TestISUPv5Parser(unittest.TestCase):
    """Тесты парсера ISUP v5"""
    
    def setUp(self):
        """Настройка перед каждым тестом"""
        self.parser = ISUPv5Parser()
    
    def test_parse_heartbeat(self):
        """Тест парсинга heartbeat пакета"""
        # Короткий пакет
        data = b'\x00\x01\x02\x03'
        result = self.parser.parse(data)
        self.assertIsNone(result)
    
    def test_parse_header(self):
        """Тест парсинга заголовка"""
        # Создаем тестовый заголовок
        protocol_version = struct.pack('>H', 5)
        message_type = struct.pack('>H', ISUPMessageType.ACCESS_EVENT)
        sequence = struct.pack('>I', 12345)
        device_id = b'ABCDEFGH'
        timestamp = struct.pack('>I', int(datetime.now().timestamp()))
        
        header_data = protocol_version + message_type + sequence + device_id + timestamp
        
        # Парсинг
        header = ISUPHeader.from_bytes(header_data)
        
        self.assertEqual(header.protocol_version, 5)
        self.assertEqual(header.message_type, ISUPMessageType.ACCESS_EVENT)
        self.assertEqual(header.sequence_number, 12345)
        self.assertEqual(len(header.device_id), 16)  # HEX строка
    
    def test_extract_card_number_ascii(self):
        """Тест извлечения ASCII номера карты"""
        # Реальный пример из логов
        data = bytes.fromhex('104c010100094741343831383733390d462d4b442d33333233')
        
        parser = ISUPv5Parser()
        card_number = parser._find_card_number_anywhere(data)
        
        self.assertIsNotNone(card_number)
        self.assertGreaterEqual(len(card_number), 5)
    
    def test_parse_access_event_full(self):
        """Тест полного события доступа"""
        # Создаем полный пакет события
        # Заголовок (20 байт)
        header_data = (
            struct.pack('>H', 5) +              # Protocol version
            struct.pack('>H', ISUPMessageType.ACCESS_EVENT) +
            struct.pack('>I', 1) +              # Sequence
            b'12345678' +                        # Device ID
            struct.pack('>I', int(datetime.now().timestamp()))
        )
        
        # Данные события
        event_data = (
            struct.pack('>H', 1) +              # Door ID
            struct.pack('>H', 1) +              # Reader ID
            struct.pack('B', ISUPAccessType.CARD) +  # Access type
            struct.pack('B', ISUPDirection.IN) +     # Direction
            struct.pack('B', ISUPAccessResult.SUCCESS) +  # Result
            struct.pack('B', 0) +               # Verification mode
            struct.pack('>H', 0) +              # Alarm status
            b'GA4818739\x00\x00\x00\x00\x00\x00' +  # Card number (16 bytes)
            b'EMP001\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'  # Employee (16 bytes)
        )
        
        full_packet = header_data + event_data
        
        # Парсинг
        event = self.parser.parse(full_packet)
        
        self.assertIsNotNone(event)
        self.assertIsInstance(event, ISUPAccessEvent)
        self.assertEqual(event.door_id, 1)
        self.assertEqual(event.access_type, ISUPAccessType.CARD)
        self.assertEqual(event.direction, ISUPDirection.IN)
    
    def test_determine_access_type(self):
        """Тест определения типа доступа"""
        parser = ISUPv5Parser()
        
        # Card
        self.assertEqual(
            parser._determine_access_type(0x01),
            ISUPAccessType.CARD
        )
        
        # Fingerprint
        self.assertEqual(
            parser._determine_access_type(0x02),
            ISUPAccessType.FINGERPRINT
        )
        
        # Face
        self.assertEqual(
            parser._determine_access_type(0x04),
            ISUPAccessType.FACE
        )
    
    def test_event_to_dict(self):
        """Тест конвертации события в словарь"""
        # Создаем минимальный event
        header = ISUPHeader(
            protocol_version=5,
            message_type=ISUPMessageType.ACCESS_EVENT,
            sequence_number=1,
            device_id='TEST12345678',
            timestamp=datetime.now(),
            data_length=100
        )
        
        event = ISUPAccessEvent(
            header=header,
            card_number='GA4818739',
            employee_number='EMP001',
            access_type=ISUPAccessType.CARD,
            direction=ISUPDirection.IN,
            access_result=ISUPAccessResult.SUCCESS,
            door_id=1,
            reader_id=1,
            alarm_status=0,
            verification_mode=0,
            raw_data=b'\x00' * 50
        )
        
        # Конвертация
        event_dict = event.to_dict()
        
        self.assertIn('device_id', event_dict)
        self.assertIn('card_number', event_dict)
        self.assertIn('direction', event_dict)
        self.assertEqual(event_dict['card_number'], 'GA4818739')
        self.assertTrue(event_dict['success'])


class TestISUPResponse(unittest.TestCase):
    """Тесты создания ответов"""
    
    def test_create_response(self):
        """Тест создания ответного пакета"""
        parser = ISUPv5Parser()
        response = parser.create_response(sequence_number=123, status=0)
        
        self.assertIsInstance(response, bytes)
        self.assertGreater(len(response), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
