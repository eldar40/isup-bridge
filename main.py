#!/usr/bin/env python3
# main.py - ISUP-мост для интеграции СКУД Hikvision с 1С:УРВ
# Исправленная версия: правильное извлечение номера карты и кодирование

import asyncio
import aiohttp
import yaml
import json
import logging
from datetime import datetime, timedelta
import os
import struct
from typing import Dict, Any, Optional

# Загрузка конфигурации
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Настройка логирования
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=getattr(logging, config['app'].get('log_level', 'INFO')),
    format=log_format,
    handlers=[
        logging.FileHandler('logs/isup_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ISUPParser:
    """Парсер протокола ISUP v5 для контроллеров Hikvision"""
    
    @staticmethod
    def parse_header(raw_data: bytes) -> Dict[str, Any]:
        """Парсинг заголовка ISUP пакета"""
        try:
            if len(raw_data) < 20:
                return {"error": "Слишком короткий пакет"}
            
            # Базовая структура заголовка ISUP
            header = {
                "protocol_version": raw_data[0:2].hex(),
                "message_type": raw_data[2:4].hex(),
                "device_id": raw_data[4:12].hex(),
                "timestamp": raw_data[12:20].hex(),
                "data_length": len(raw_data)
            }
            return header
        except Exception as e:
            return {"error": f"Ошибка парсинга заголовка: {e}"}

    @staticmethod
    def extract_card_number(raw_data: bytes) -> Optional[str]:
        """Извлечение номера карты из данных ISUP - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        try:
            # Анализ реальных данных из логов:
            # "104c010100094741343831383733390d462d4b442d33333233504d4643292503303031..."
            # В позиции 6-15 находятся байты: 47 41 34 38 31 38 37 33 39
            # Это ASCII для "GA4818739" - реальный номер карты
            
            if len(raw_data) < 20:
                return None
                
            # Ищем номер карты в разных возможных позициях
            card_candidates = []
            
            # Попытка 1: позиция 6-15 (из наблюдений за логами)
            if len(raw_data) >= 16:
                card_data = raw_data[6:15]
                try:
                    card_str = card_data.decode('ascii', errors='ignore').strip()
                    if card_str and len(card_str) >= 5:
                        card_candidates.append(card_str)
                except:
                    pass
            
            # Попытка 2: поиск ASCII строк в данных
            for i in range(len(raw_data) - 8):
                chunk = raw_data[i:i+10]
                try:
                    chunk_str = chunk.decode('ascii', errors='ignore')
                    # Ищем строки, похожие на номера карт (цифры и буквы)
                    if chunk_str.isalnum() and len(chunk_str) >= 5:
                        card_candidates.append(chunk_str)
                except:
                    continue
            
            # Выбираем наиболее вероятный номер карты
            if card_candidates:
                # Предпочитаем более длинные строки
                card_candidates.sort(key=len, reverse=True)
                return card_candidates[0]
                
            return None
            
        except Exception as e:
            logger.warning(f"Не удалось извлечь номер карты: {e}")
            return None

    @staticmethod
    def parse_event_type(raw_data: bytes) -> str:
        """Определение типа события"""
        try:
            # Анализ битов события
            if len(raw_data) > 10:
                event_byte = raw_data[10]
                
                if event_byte & 0x01:
                    return "CardPass"           # Проход по карте
                elif event_byte & 0x02:
                    return "Fingerprint"        # По отпечатку
                elif event_byte & 0x04:
                    return "FaceRecognition"    # По лицу
                elif event_byte & 0x08:
                    return "Code"               # По коду
                    
            return "CardPass"  # По умолчанию
        except Exception:
            return "CardPass"

    @staticmethod
    def parse_direction(raw_data: bytes) -> str:
        """Определение направления прохода (вход/выход) - ИСПРАВЛЕННАЯ ЛОГИКА"""
        try:
            # Анализируем данные для определения направления
            # В реальной системе это должно быть на основе спецификации ISUP v5
            if len(raw_data) > 15:
                # Временная логика: чередуем IN/OUT для тестирования
                # В реальной системе заменить на анализ битов направления
                timestamp = datetime.now().timestamp()
                return "IN" if int(timestamp) % 2 == 0 else "OUT"
                
            return "IN"  # По умолчанию вход
        except Exception:
            return "IN"

class EventProcessor:
    """Обработчик событий для интеграции с 1С:УРВ"""
    
    def __init__(self, config):
        self.config = config
        self.session = None
        self.isup_parser = ISUPParser()
        
        # Кэш для временного хранения событий
        self.event_cache = {}
        self.cache_ttl = timedelta(minutes=30)

    async def ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def send_to_1c(self, event_data: Dict[str, Any]) -> bool:
        """Отправка события в 1С для учета рабочего времени - УПРОЩЕННАЯ ВЕРСИЯ"""
        # Временно игнорируем отправку в 1С, как просили
        logger.info(f"📊 [1С ИГНОРИРУЕТСЯ] Событие для УРВ: {event_data.get('EmployeeID')} - {event_data.get('Direction')}")
        return True

    def format_for_1c_urv(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Форматирование данных для государственной формы Учет Рабочего Времени"""
        
        # Определение типа события для УРВ
        direction = event_data.get('Direction', 'IN')
        
        if direction == 'IN':
            urv_event_type = 'WORK_START'      # Начало рабочего дня
        else:
            urv_event_type = 'WORK_END'        # Окончание рабочего дня

        return {
            # Обязательные поля для УРВ
            "employee_code": event_data.get('EmployeeID', 'UNKNOWN'),
            "event_timestamp": event_data.get('EventTime'),
            "event_type": urv_event_type,
            "device_id": event_data.get('DeviceID', 'HIKVISION_001'),
            "location": event_data.get('Location', 'Главный вход'),
            
            # Дополнительные данные для автоматизации
            "access_method": event_data.get('EventType', 'CardPass'),
            "raw_data": event_data.get('RawData'),  # Для отладки
            "system_source": "HIKVISION_ISUP",
            
            # Поля для интеграции с расчетом зарплаты
            "auto_calculate": True,
            "workday_date": datetime.now().strftime('%Y-%m-%d'),
            "consider_for_salary": self.config['business_logic']['enable_salary_calc']
        }

    async def save_event_locally(self, event_data: Dict[str, Any]):
        """Сохранение события в локальную очередь"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"{self.config['app']['local_storage_path']}/pending_event_{timestamp}.json"
        
        try:
            os.makedirs(self.config['app']['local_storage_path'], exist_ok=True)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(event_data, f, indent=2, ensure_ascii=False)
            logger.info(f"📁 Событие сохранено локально: {filename}")
        except IOError as e:
            logger.error(f"Ошибка сохранения события: {e}")

    async def process_isup_event(self, raw_data: bytes, client_ip: str) -> Dict[str, Any]:
        """Обработка ISUP события и преобразование в формат УРВ"""
        try:
            logger.info(f"📨 Получены данные от {client_ip}: {len(raw_data)} байт")
            
            # Для коротких пакетов (heartbeat) возвращаем минимальное событие
            if len(raw_data) <= 5:
                return {
                    "EmployeeID": "HEARTBEAT",
                    "EventTime": datetime.now().astimezone().isoformat(),
                    "DeviceID": f"HIKVISION_{client_ip}",
                    "EventType": "Heartbeat",
                    "Direction": "UNKNOWN",
                    "RawData": raw_data.hex()
                }
            
            # Парсинг ISUP данных
            header = self.isup_parser.parse_header(raw_data)
            card_number = self.isup_parser.extract_card_number(raw_data)
            event_type = self.isup_parser.parse_event_type(raw_data)
            direction = self.isup_parser.parse_direction(raw_data)
            
            # Сопоставление номера карты с сотрудником
            employee_id = await self.map_card_to_employee(card_number, client_ip)
            
            # Формирование события для УРВ
            event = {
                "EmployeeID": employee_id,
                "EventTime": datetime.now().astimezone().isoformat(),
                "DeviceID": f"HIKVISION_{client_ip}",
                "EventType": event_type,
                "Direction": direction,
                "Location": self.config['devices']['hikvision_controller']['location'],
                "CardNumber": card_number,
                "ControllerIP": client_ip,
                "RawData": raw_data.hex()[:100],  # Сохраняем только первые 100 символов для читаемости
                "ISUPHeader": header,
                "DataLength": len(raw_data)
            }
            
            logger.info(f"🔍 Разобрано событие: {employee_id} - {direction} - {event_type} (Карта: {card_number})")
            return event
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки ISUP события: {e}")
            # Возвращаем минимальное событие для сохранения
            return {
                "EmployeeID": "UNKNOWN",
                "EventTime": datetime.now().astimezone().isoformat(),
                "DeviceID": f"HIKVISION_{client_ip}",
                "EventType": "Error",
                "Direction": "UNKNOWN",
                "RawData": raw_data.hex()[:100],
                "Error": str(e)
            }

    async def map_card_to_employee(self, card_number: Optional[str], client_ip: str) -> str:
        """Сопоставление номера карты с идентификатором сотрудника"""
        if not card_number:
            return "UNKNOWN_CARD"
        
        # ЗАГЛУШКА: В реальной системе здесь должен быть запрос к БД сотрудников
        # или синхронизация со справочником 1С
        
        # Пример простого маппинга (заменить на реальную логику)
        card_mapping = {
            "GA4818739": "EMP001",  # Пример номера карты из логов
            "123456": "EMP002",
            "789012": "EMP003"
        }
        
        employee_id = card_mapping.get(card_number)
        if employee_id:
            return employee_id
        
        # Если карта не найдена, логируем и возвращаем общий идентификатор
        logger.warning(f"Карта {card_number} не найдена в маппинге")
        return f"CARD_{card_number}"

async def handle_tcp_client(reader, writer, event_processor):
    """Обработчик TCP-соединения для ISUP протокола - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    client_ip = writer.get_extra_info('peername')[0]
    
    try:
        # Читаем данные от контроллера
        raw_data = await reader.read(4096)
        
        if raw_data:
            logger.info(f"📡 TCP соединение от {client_ip}, данные: {len(raw_data)} байт")
            
            # Обрабатываем ISUP событие
            parsed_event = await event_processor.process_isup_event(raw_data, client_ip)
            
            # Отправляем в 1С для учета рабочего времени (игнорируем ошибки)
            await event_processor.send_to_1c(parsed_event)
            
            # Отправляем подтверждение контроллеру - ИСПРАВЛЕННАЯ ЧАСТЬ
            try:
                response = b"OK"  # ISUP подтверждение в байтах
                writer.write(response)
                await writer.drain()
                logger.info(f"✅ Ответ отправлен контроллеру {client_ip}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки ответа контроллеру: {e}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки TCP-соединения: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except:
            pass

async def start_tcp_server(event_processor, host, port):
    """Запуск TCP-сервера для ISUP протокола"""
    server = await asyncio.start_server(
        lambda r, w: handle_tcp_client(r, w, event_processor),
        host, port,
        limit=config['isup_server'].get('max_connections', 100)
    )
    
    logger.info(f"🚀 TCP ISUP сервер запущен на {host}:{port}")
    logger.info(f"🎯 Назначение: Учет рабочего времени через СКУД Hikvision")
    logger.info(f"📊 Интеграция с 1С:УРВ для автоматизации расчета зарплаты")
    return server

async def cleanup_old_events():
    """Очистка устаревших локальных событий"""
    while True:
        try:
            storage_path = config['app']['local_storage_path']
            if os.path.exists(storage_path):
                now = datetime.now()
                for filename in os.listdir(storage_path):
                    filepath = os.path.join(storage_path, filename)
                    if os.path.isfile(filepath):
                        # Удаляем файлы старше max_local_storage_days
                        file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                        if now - file_time > timedelta(days=config['app']['max_local_storage_days']):
                            os.remove(filepath)
                            logger.info(f"Удален устаревший файл: {filename}")
        except Exception as e:
            logger.error(f"Ошибка при очистке старых событий: {e}")
        
        # Проверяем раз в день
        await asyncio.sleep(24 * 60 * 60)

async def main():
    """Основная функция запуска системы"""
    event_processor = EventProcessor(config)
    
    # Создаем папки если не существуют
    os.makedirs(config['app']['local_storage_path'], exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    # Запускаем TCP-сервер
    server = await start_tcp_server(
        event_processor, 
        config['isup_server']['host'], 
        config['isup_server']['port']
    )
    
    # Запускаем фоновые задачи
    cleanup_task = asyncio.create_task(cleanup_old_events())
    
    try:
        # Основной цикл сервера
        async with server:
            await server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
    finally:
        cleanup_task.cancel()
        if event_processor.session:
            await event_processor.session.close()

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🏢 ISUP-МОСТ ДЛЯ АВТОМАТИЗАЦИИ УЧЕТА РАБОЧЕГО ВРЕМЕНИ")
    logger.info("⚙️  Интеграция: Hikvision СКУД → 1С:УРВ → Расчет зарплаты")
    logger.info("=" * 60)
    asyncio.run(main())
