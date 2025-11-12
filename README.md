# ISUP Bridge для интеграции Hikvision СКУД с 1С:УРВ

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![ISUP v5](https://img.shields.io/badge/ISUP-v5-green.svg)](https://www.hikvision.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Production Ready](https://img.shields.io/badge/status-production--ready-brightgreen.svg)]()

**Production-ready** мост для автоматизации учета рабочего времени через интеграцию контроллеров Hikvision с 1С:Предприятие.

## 🎯 Возможности

### ✨ Основные функции
- ✅ **Полная поддержка ISUP v5** - официальный протокол Hikvision
- ✅ **Multi-tenant архитектура** - поддержка множества 1С серверов
- ✅ **Автоматический учет рабочего времени** в соответствии с государственной формой УРВ
- ✅ **Локальное хранилище** при недоступности 1С
- ✅ **Автоматические повторы** с exponential backoff
- ✅ **Мониторинг здоровья** всех 1С серверов
- ✅ **HTTP API** для управления и мониторинга

### 🔐 Типы доступа
- 🎴 **Карта (RFID/Mifare)**
- 👆 **Отпечаток пальца**
- 👤 **Распознавание лица**
- 🔢 **PIN-код**
- 📱 **QR-код**
- 🔐 **Комбинированная аутентификация**

### 🏢 Multi-Tenant режим
```
Контроллер Офис 1 → ISUP Bridge → 1С Сервер Офис 1
Контроллер Офис 2 → ISUP Bridge → 1С Сервер Офис 2
Контроллер Цех    → ISUP Bridge → 1С Сервер Производство
```

## 🚀 Быстрый старт

### Требования
- Python 3.8+
- 1С:Предприятие 8.3 с HTTP-сервисами
- Контроллеры Hikvision с ISUP v5

### Установка

```bash
# Клонирование репозитория
git clone https://github.com/your-username/isup-bridge-production.git
cd isup-bridge-production

# Установка зависимостей
pip install -r requirements.txt

# Настройка конфигурации
cp config/config.yaml config/config.yaml.local
nano config/config.yaml.local

# Запуск
cd src
python main.py
```

### Docker (рекомендуется)

```bash
docker-compose up -d
```

## ⚙️ Конфигурация

### Базовая настройка

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  log_level: "INFO"

tenants:
  - tenant_id: "main_office"
    name: "Главный офис"
    base_url: "https://your-1c-server.ru"
    endpoint: "/hs/access/events"
    username: "isup_user"
    password: "secure_password"
    device_ids:
      - "DS-K1T671MF001"
      - "OFFICE-*"  # Wildcard поддержка
```

### Multi-Tenant настройка

Добавьте столько тенантов, сколько у вас 1С серверов:

```yaml
tenants:
  - tenant_id: "office1"
    base_url: "https://1c-office1.ru"
    device_ids: ["OFFICE1-*"]
  
  - tenant_id: "office2"
    base_url: "https://1c-office2.ru"
    device_ids: ["OFFICE2-*"]
  
  - tenant_id: "default"
    base_url: "https://1c-main.ru"
    device_ids: ["*"]  # Catch-all
```

## 🔧 Настройка контроллера Hikvision

### Веб-интерфейс контроллера

1. **Configuration → Network → Advanced**
   - Enable ISUP v5 Protocol
   - Server IP: `<IP_вашего_сервера>`
   - Port: `8080`
   - Device Key: (любой, для авторизации)

2. **Access Control → Event Upload**
   - Upload Method: `ISUP`
   - Protocol Version: `v5`

### Проверка соединения

```bash
# Проверка доступности сервера
telnet your-server-ip 8080

# Просмотр логов
tail -f logs/isup_bridge.log
```

## 📊 Мониторинг

### HTTP API

```bash
# Health check
curl http://localhost:8081/health

# Метрики
curl http://localhost:8081/metrics

# Информация о тенантах
curl http://localhost:8081/tenants

# Неотправленные события
curl http://localhost:8081/pending
```

### Ответ метрик

```json
{
  "server": {
    "uptime_seconds": 3600,
    "events": {
      "received": 1250,
      "sent": 1200,
      "failed": 50,
      "pending": 10,
      "success_rate": "96.00%"
    }
  },
  "tenants": {
    "main_office": {
      "status": "active",
      "success_rate": "98.50%",
      "device_count": 5
    }
  }
}
```

## 📁 Структура проекта

```
isup-bridge-production/
├── src/
│   ├── main.py                 # Главный сервер
│   ├── isup_protocol.py        # ISUP v5 парсер
│   └── tenant_manager.py       # Multi-tenant логика
├── config/
│   └── config.yaml             # Конфигурация
├── deployment/
│   ├── docker-compose.yml      # Docker Compose
│   ├── Dockerfile              # Docker образ
│   └── systemd/                # Systemd сервис
├── tests/
│   └── test_isup_protocol.py   # Тесты
├── docs/
│   ├── INSTALLATION.md         # Детальная установка
│   ├── ISUP_PROTOCOL.md        # Документация протокола
│   └── API.md                  # HTTP API документация
├── requirements.txt
├── README.md
└── LICENSE
```

## 🔍 Логи

### Просмотр логов

```bash
# Все логи
tail -f logs/isup_bridge.log

# Только ошибки
tail -f logs/errors.log

# Systemd логи (если используется)
journalctl -u isup-bridge -f
```

### Пример лога события

```
2024-01-15 10:30:45 - isup_bridge - INFO - 📨 Событие от 192.168.1.100:
  Устройство=DS-K1T671MF001 | Карта=GA4818739 | Направление=IN | Тип=CARD
2024-01-15 10:30:45 - isup_bridge - INFO - ✅ Событие успешно отправлено в Главный офис
```

## 🐛 Устранение неисправностей

### Контроллер не подключается

```bash
# Проверка порта
sudo netstat -tlnp | grep 8080

# Проверка firewall
sudo ufw status
sudo ufw allow 8080/tcp

# Проверка логов
tail -f logs/isup_bridge.log
```

### События не отправляются в 1С

```bash
# Проверка конфигурации тенанта
curl http://localhost:8081/tenants

# Тест подключения к 1С
curl -u username:password https://your-1c-server.ru/hs/access/events

# Просмотр неотправленных событий
ls -la pending_events/
```

### Высокая нагрузка

```bash
# Мониторинг ресурсов
htop

# Статистика событий
curl http://localhost:8081/metrics | jq '.server.events'

# Оптимизация: увеличить retry_batch_size в config.yaml
```

## 🚀 Деплой на production сервер

### Через systemd

```bash
# Копирование файлов
sudo cp deployment/systemd/isup-bridge.service /etc/systemd/system/

# Активация
sudo systemctl enable isup-bridge
sudo systemctl start isup-bridge

# Проверка статуса
sudo systemctl status isup-bridge
```

### Через Docker

```bash
docker-compose -f deployment/docker-compose.yml up -d
```

## 🔐 Безопасность

### Рекомендации
1. ✅ Используйте **сильные пароли** для 1С
2. ✅ Настройте **белый список IP** в `config.yaml`
3. ✅ Включите **TLS** для соединений с 1С
4. ✅ Регулярно **обновляйте зависимости**
5. ✅ Ограничьте **доступ к логам**

### Пример настройки firewall

```bash
# UFW
sudo ufw allow from 192.168.1.0/24 to any port 8080
sudo ufw allow 8081  # HTTP API для локальной сети

# iptables
sudo iptables -A INPUT -p tcp -s 192.168.1.0/24 --dport 8080 -j ACCEPT
```

## 📈 Масштабирование

### Горизонтальное масштабирование

Запустите несколько инстансов за балансировщиком:

```yaml
# docker-compose.yml
services:
  isup-bridge-1:
    image: isup-bridge:latest
    ports:
      - "8080:8080"
  
  isup-bridge-2:
    image: isup-bridge:latest
    ports:
      - "8081:8080"
  
  nginx:
    image: nginx:latest
    # ... балансировка между инстансами
```

### Вертикальное масштабирование

Увеличьте лимиты в `config.yaml`:

```yaml
performance:
  max_connections: 5000
  connection_pool_size: 200
  retry_batch_size: 100
```

## 🤝 Участие в разработке

Мы приветствуем вклад сообщества!

1. Fork репозитория
2. Создайте feature branch (`git checkout -b feature/amazing`)
3. Commit изменений (`git commit -m 'Add amazing feature'`)
4. Push в branch (`git push origin feature/amazing`)
5. Откройте Pull Request

## 📄 Лицензия

Распространяется под лицензией MIT. См. [LICENSE](LICENSE) для подробностей.

## 📞 Поддержка

- 🐛 **Issues**: [GitHub Issues](https://github.com/your-username/isup-bridge-production/issues)
- 📧 **Email**: support@yourcompany.ru
- 💬 **Telegram**: [@your_support_bot](https://t.me/your_support_bot)
- 📚 **Документация**: [Wiki](https://github.com/your-username/isup-bridge-production/wiki)

## 🌟 Благодарности

- Hikvision за документацию ISUP протокола
- 1С:Предприятие за API интеграции
- Сообществу Python за отличные библиотеки

---

**Made with ❤️ for enterprise access control automation**

⭐ Если проект полезен - поставьте звезду на GitHub!
