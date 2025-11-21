# ISUP-Bridge — мост для передачи событий с устройств Hikvision в 1С

## Описание

ISUP-Bridge принимает события с контроллеров и терминалов Hikvision (ISUP v5 по TCP и ISAPI по HTTP), приводит их к единому JSON-формату и пересылает в 1С. Сервис сохраняет непросланные события локально и автоматически повторяет отправку при ошибках. Поддерживается базовая авторизация, настраиваемые endpoint'ы 1С и работа с несколькими объектами/филиалами. Есть HTTP API для мониторинга (/health, /metrics, /tenants) и фильтрация по белому списку IP. Проект легко расширяется (хранение в БД, отправка в Kafka, загрузка фото в S3 и т.п.).

### Ключевые возможности

- Приём ISUP (TCP) и ISAPI (HTTP webhook) от устройств Hikvision.
- Унификация событий в единый JSON-формат (время, устройство, направление, номер карты/сотрудника, результат).
- Отправка событий в 1С через HTTP(S) с retry-логикой и Basic Auth.
- Локальное хранилище непереданных событий и автоматический ретрай.
- HTTP-эндпоинты для мониторинга и метрик (/health, /metrics, /tenants).
- Белый список IP для приёма событий.

### Быстрый старт

Требования: Python 3.10+, зависимости указаны в requirements.txt.

Установка зависимостей:

```
pip install -r requirements.txt
```

Запуск:

```
python3 main.py
```

По умолчанию сервисы:
- ISUP TCP сервер — порт 8001
- ISAPI Webhook — порт 8002
- HTTP API (health/metrics) — порт 8081
- Hikvision callback listener — порт 8099 (если включён режим callback)

### Пример конфигурации (config.yaml)

```
objects:
  - object_id: "moscow_hq"
    name: "Главный офис Москва"
    c1:
      base_url: "https://1c.company.ru"
      endpoint: "/hs/access/events"
      username: "bridge"
      password: "pass"
    terminals:
      - terminal_id: "hq_main_entrance"
        ip_address: "192.168.10.10"
        direction: "in"
```

### Hikvision (callback + auto configuration)

Файл `config/hikvision.yaml` включает настройки callback-listener'а и список устройств. Пример конфигурации по умолчанию для работы через callback (NAT-friendly):

```
hikvision:
  callback:
    host: "0.0.0.0"
    port: 8099
    path: "/hikvision/callback"
    secret: "CHANGE_ME"

  devices:
    - name: "terminal_1"
      ip: "192.168.10.50"   # если доступен напрямую, будет выполнена автоконфигурация
      port: 80
      username: "admin"
      password: "Pass"
      mode: "callback"       # callback — предпочтительный режим; alert_stream поддерживается как легаси

  allowed_device_ids: []
```

- Если хотя бы одно устройство использует `mode: callback`, приложение автоматически поднимает `aiohttp`-сервер по указанным `host`/`port`/`path` и принимает XML `EventNotificationAlert`.
- При включённой опции `server.features.auto_configure_terminals = true` терминалы, доступные по IP, будут автоматически настроены на отправку событий на `http://<bridge_host>:<port>/hikvision/callback`.
- Режим `alert_stream` остаётся поддержанным как легаси, но по умолчанию рекомендуется callback для работы за NAT.

---

Далее в README находится подробное описание архитектуры, инструкций по настройке устройств Hikvision, формата config.yaml, тестирования и рекомендаций по развертыванию.
