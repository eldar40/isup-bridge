## Короткие инструкции для AI-агента (isup-bridge)

Цель: быстро вывести агента в продуктивное состояние при работе с этим репозиторием. Ниже — концентрированное руководство с привязкой к реальным файлам и паттернам проекта.

1) Что читать в первую очередь
   - `README.md` — общий сценарий использования, быстрый старт и Docker примеры.
   - `src/main.py` — точка входа сервера: инициализация логов, конфигов, запуск TCP-сервера и HTTP API.
   - `src/isup_protocol.py` — парсер ISUP v5: см. `ISUPv5Parser.parse` и `create_response` для примеров формата пакетов.
   - `src/tenant_manager.py` — логика multi-tenant (привязка device_id → endpoint/1С).
   - `config/config.yaml` и `config/tenants.yaml` — реальные примеры конфигурации (tenants, devices, retry/backoff).

2) Быстрая разведка — практические grep-подсказки
   - Поиск точки входа: `grep -R "if __name__ == '__main__'" -n src || true`
   - Где формируется ответ контроллеру: `grep -R "create_response" -n src || true` (см. `isup_protocol.py` и обработчик в `main.py`).
   - Где хранятся неподтверждённые события: ищите `storage_path` в `config/config.yaml` и класс `EventStorage` в `src/main.py`.

3) Ключевые архитектурные паттерны (конкретно для этого проекта)
   - Multi-tenant: `TenantManager` разрешает device_id → tenant (1С endpoint). Изменения в мульти-tenant логике требуют обновления `config/tenants.yaml` и тестов.
   - Парсинг пакетов: `ISUPv5Parser.parse` возвращает `ISUPAccessEvent`; код безопасно захватывает stdout/stderr парсера (см. `contextlib.redirect_stdout/redirect_stderr` в `src/main.py`).
   - Фолбэки: при невозможности распарсить пакет сервер формирует fallback-ответ и сохраняет событие в `storage` (см. `EventStorage.store_event`).
   - HTTP API: лёгкий aiohttp-сервер для health/metrics на порту `health_check_port` (конфиг в `config/config.yaml`).

4) Быстрые команды (локально)
   - Установка зависимостей: `pip install -r requirements.txt`
   - Запуск в режиме разработки: `python -m src.main` (или `python src/main.py` из корня)
   - Тесты: `python -m pytest -q`
   - Docker (deployment/): `docker-compose -f deployment/docker-compose.yml up --build -d`

5) Проектные конвенции и PR-практики
   - Малые PR: одна функциональная цель + тесты. Изменения в `src/main.py` → обновить README + config примеры.
   - Логи: используйте `logging.getLogger('isup_bridge')` или `logger = logging.getLogger(__name__)`. Файлы логов лежат в `logs/` (см. `setup_logging`).
   - Конфиги: добавляйте параметры в `config/config.yaml`; новые чувствительные значения — через env vars и document в README.

6) Интеграционные точки и тесты
   - Внешние интеграции: 1С HTTP endpoint — конфигурация в tenant entry (`base_url`, `endpoint`, `username`, `password`).
   - При добавлении внешнего клиента (aiohttp) покрывайте unit-тестом и мокируйте сетевые вызовы.
   - Unit тесты хранятся в `tests/` (например, `test_isup_protocol.py` показывает работу парсера).

7) Что можно править автоматически и чего избегать
   - Можно: улучшать парсер, добавлять unit-тесты, расширять `tenants.yaml` примерами.
   - Избегать: ручных правок миграций/данных (в случае использования БД), изменения формата Device ID без обратной совместимости.

Если нужно — внесу правки в этот файл (корректные команды запуска/контракты функций). Напишите, какие разделы хотите расширить — добавлю примеры кода или тесты.
