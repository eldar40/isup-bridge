#!/usr/bin/env python3
# Скрипт для повторной отправки накопленных событий в 1С

import asyncio
import aiohttp
import json
import os
import yaml
from datetime import datetime

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

async def retry_pending_events():
    storage_path = config['app']['local_storage_path']
    success_count = 0
    error_count = 0
    
    async with aiohttp.ClientSession() as session:
        for filename in os.listdir(storage_path):
            if filename.startswith('pending_event_'):
                filepath = os.path.join(storage_path, filename)
                try:
                    with open(filepath, 'r') as f:
                        event_data = json.load(f)
                    
                    # Отправка в 1С
                    url = f"{config['1c_rest_api']['base_url']}{config['1c_rest_api']['endpoint']}"
                    auth = aiohttp.BasicAuth(
                        config['1c_rest_api']['username'],
                        config['1c_rest_api']['password']
                    )
                    
                    async with session.post(url, json=event_data, auth=auth) as response:
                        if response.status == 200:
                            os.remove(filepath)
                            success_count += 1
                            print(f"✅ Отправлено: {filename}")
                        else:
                            error_count += 1
                            print(f"❌ Ошибка: {filename} - HTTP {response.status}")
                            
                except Exception as e:
                    error_count += 1
                    print(f"❌ Ошибка обработки {filename}: {e}")
    
    print(f"\n📊 Итог: Успешно - {success_count}, Ошибок - {error_count}")

if __name__ == '__main__':
    asyncio.run(retry_pending_events())
