#!/usr/bin/env python3
"""
Скрипт для автоматической настройки терминалов Hikvision
"""

import asyncio
import yaml
import logging
from pathlib import Path

# Добавляем путь к src
import sys
sys.path.append(str(Path(__file__).parent.parent / 'src'))

from isapi_server import ISAPIDeviceManager


async def main():
    """Основная функция"""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('setup_isapi')
    
    # Загрузка конфигурации
    config_path = Path('config/config.yaml')
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Создание менеджера устройств
    device_manager = ISAPIDeviceManager(config, logger)
    
    # Базовый URL для webhook
    isapi_config = config.get('isapi', {})
    webhook_base_url = isapi_config.get('webhook_base_url')
    
    if not webhook_base_url:
        host = isapi_config.get('host', '0.0.0.0')
        port = isapi_config.get('port', 8082)
        webhook_base_url = f"http://{host}:{port}"
        logger.warning(f"⚠️ webhook_base_url не указан, используется: {webhook_base_url}")
    
    # Автоматическая настройка терминалов
    logger.info("🚀 Запуск автоматической настройки терминалов...")
    results = await device_manager.auto_configure_terminals(webhook_base_url)
    
    # Вывод результатов
    logger.info("\n📊 РЕЗУЛЬТАТЫ НАСТРОЙКИ:")
    for result in results:
        status = "✅ УСПЕХ" if result['success'] else "❌ ОШИБКА"
        logger.info(f"   {status} {result['terminal_id']} ({result['ip_address']})")
        if not result['success']:
            logger.info(f"      Ошибка: {result.get('error', 'Unknown error')}")


if __name__ == '__main__':
    asyncio.run(main())