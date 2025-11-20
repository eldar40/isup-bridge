# ISUP-Bridge Wiki

## Overview
ISUP-Bridge объединяет события Hikvision из ISUP и ISAPI в единый сервис, нормализующий события в формате JSON и пересылающий их в интеграционные системы (например, 1С). Этот раздел вики содержит пошаговые инструкции по настройке конфигурации, запуску и диагностике сервиса.

### Основные возможности
- Приём событий по протоколам ISUP (TCP) и ISAPI (HTTP callback и alertStream).
- Унификация событий через централизованный диспетчер `hikvision.event_dispatcher.HikvisionEventDispatcher`.
- Автопереподключение к alertStream и обработка multipart потока из камер.
- Возможность запуска в режимах alertStream и callback для каждого устройства, описанного в `config/hikvision.yaml`.

### Быстрый старт
1. Установите зависимости: `pip install -r requirements.txt`.
2. Настройте устройства в `config/hikvision.yaml`, указав IP, логин/пароль и режим работы (`alert_stream` или `callback`).
3. Запустите сервис: `python main.py`. Для режима callback убедитесь, что камера отправляет HTTP POST на `/hikvision/event`.
4. Проверяйте логи для подтверждения подключений, событий и возможных ошибок аутентификации.

### Структура Hikvision-модулей
- `hikvision/alert_stream.py` — клиент ISAPI alertStream с Digest аутентификацией и разбором multipart потока.
- `hikvision/listener.py` — FastAPI эндпоинт `/hikvision/event` для приёма multipart уведомлений от камер.
- `hikvision/multipart_parser.py` — парсер multipart/form-data и multipart/mixed с поддержкой бинарных вложений.
- `hikvision/event_dispatcher.py` — диспетчер событий для нормализации и маршрутизации событий Hikvision.

### Тестирование
В репозитории присутствуют тесты (`tests/`) для потокового чтения alertStream, парсинга multipart и callback-обработчика. Запустите `pytest` для проверки.
