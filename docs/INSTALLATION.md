# Руководство по деплою ISUP Bridge на production сервер

## 📋 Предварительные требования

### Сервер
- **ОС**: Ubuntu 20.04/22.04 LTS (или аналог)
- **RAM**: Минимум 1GB, рекомендуется 2GB+
- **CPU**: 1 core минимум, 2+ рекомендуется
- **Диск**: 10GB свободного места
- **Network**: Статический IP, открытые порты 8080, 8081

### Доступы
- Root или sudo доступ
- Доступ к 1С серверам по HTTP/HTTPS
- Учетные данные 1С для HTTP-сервисов

## 🚀 Метод 1: Автоматическая установка

```bash
# Скачать проект
git clone https://github.com/your-username/isup-bridge-production.git
cd isup-bridge-production

# Запустить скрипт установки
sudo chmod +x deployment/install.sh
sudo ./deployment/install.sh
```

Скрипт автоматически:
- ✅ Установит Python и зависимости
- ✅ Создаст пользователя isupuser
- ✅ Настроит виртуальное окружение
- ✅ Установит systemd сервис
- ✅ Настроит firewall

## 🔧 Метод 2: Ручная установка

### Шаг 1: Обновление системы

```bash
sudo apt update && sudo apt upgrade -y
```

### Шаг 2: Установка Python

```bash
sudo apt install -y python3.10 python3-pip python3-venv git
```

### Шаг 3: Создание пользователя

```bash
sudo useradd -m -s /bin/bash isupuser
```

### Шаг 4: Установка проекта

```bash
# Создание директории
sudo mkdir -p /opt/isup-bridge
cd /opt/isup-bridge

# Клонирование репозитория
sudo git clone https://github.com/your-username/isup-bridge-production.git .

# Права доступа
sudo chown -R isupuser:isupuser /opt/isup-bridge
```

### Шаг 5: Python окружение

```bash
# Создание venv
sudo -u isupuser python3 -m venv /opt/isup-bridge/venv

# Установка зависимостей
sudo -u isupuser /opt/isup-bridge/venv/bin/pip install -r requirements.txt
```

### Шаг 6: Конфигурация

```bash
# Копирование примера
cd /opt/isup-bridge/config
sudo -u isupuser cp config.yaml config.yaml.example

# Редактирование конфигурации
sudo -u isupuser nano config.yaml
```

Заполните:
- URL ваших 1С серверов
- Учетные данные
- Device IDs ваших контроллеров

### Шаг 7: Systemd сервис

```bash
# Копирование unit файла
sudo cp /opt/isup-bridge/deployment/systemd/isup-bridge.service /etc/systemd/system/

# Перезагрузка systemd
sudo systemctl daemon-reload

# Включение автозапуска
sudo systemctl enable isup-bridge.service

# Запуск сервиса
sudo systemctl start isup-bridge.service
```

### Шаг 8: Firewall

```bash
# UFW
sudo ufw allow 8080/tcp comment 'ISUP TCP'
sudo ufw allow 8081/tcp comment 'ISUP API'
sudo ufw enable

# iptables (альтернатива)
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8081 -j ACCEPT
sudo iptables-save > /etc/iptables/rules.v4
```

## 📊 Проверка установки

### 1. Статус сервиса

```bash
sudo systemctl status isup-bridge
```

Должно быть: **Active: active (running)**

### 2. Логи

```bash
# Последние логи
sudo journalctl -u isup-bridge -n 50

# Следить за логами в реальном времени
sudo journalctl -u isup-bridge -f
```

### 3. HTTP API

```bash
# Health check
curl http://localhost:8081/health

# Метрики
curl http://localhost:8081/metrics | jq

# Тенанты
curl http://localhost:8081/tenants | jq
```

### 4. Тест с контроллером

```bash
# Симуляция события от контроллера
echo "test" | nc localhost 8080
```

## 🔐 Настройка 1С

### 1. Создание HTTP-сервиса

В конфигураторе 1С:

1. **Конфигурация → Общие → HTTP-сервисы → Создать**
2. **Имя**: AccessControl
3. **Корневой URL**: access
4. **Шаблон URL**: events (метод POST)

### 2. Код обработчика

```bsl
// В модуле HTTP-сервиса
Процедура ОбработатьСобытиеДоступа(Запрос, Ответ) Экспорт
    
    Попытка
        // Парсинг JSON
        ЧтениеJSON = Новый ЧтениеJSON;
        ЧтениеJSON.УстановитьСтроку(Запрос.ПолучитьТелоКакСтроку());
        Данные = ПрочитатьJSON(ЧтениеJSON);
        
        // Поиск сотрудника
        Сотрудник = СправочникСотрудники.НайтиПоКоду(Данные["employee_code"]);
        
        Если Сотрудник.Пустая() Тогда
            ВызватьИсключение "Сотрудник не найден";
        КонецЕсли;
        
        // Создание записи УРВ
        Запись = РегистрыСведений.УчетРабочегоВремени.СоздатьЗапись();
        Запись.Сотрудник = Сотрудник;
        Запись.ДатаВремя = XMLЗначение(Тип("Дата"), Данные["event_timestamp"]);
        Запись.ТипСобытия = Данные["event_type"];
        Запись.Устройство = Данные["device_id"];
        Запись.Записать();
        
        // Успешный ответ
        Ответ.КодСостояния = 200;
        Ответ.УстановитьТелоИзСтроки("OK");
        
    Исключение
        // Ошибка
        Ответ.КодСостояния = 500;
        Ответ.УстановитьТелоИзСтроки(ОписаниеОшибки());
    КонецПопытки;
    
КонецПроцедуры
```

### 3. Публикация

1. **Администрирование → Публикация → Добавить**
2. Выбрать HTTP-сервис AccessControl
3. Указать URL: `/hs/access`

### 4. Тест

```bash
curl -X POST http://your-1c-server.ru/hs/access/events \
  -H "Content-Type: application/json" \
  -u username:password \
  -d '{
    "employee_code": "EMP001",
    "event_timestamp": "2024-01-15T10:30:00+03:00",
    "event_type": "WORK_START",
    "device_id": "DS-K1T671MF001"
  }'
```

## 🏢 Настройка контроллера Hikvision

### Веб-интерфейс

1. **Configuration → Network → Advanced Settings**
   - Center Server IP: `<IP_вашего_сервера>`
   - Center Server Port: `8080`
   - Protocol Type: `ISUP v5.0`

2. **Configuration → Event → Event Upload**
   - Upload Method: `Center Group`
   - Enable: ✅
   - Interval: `5` seconds

3. **Сохранить и перезагрузить**

### Проверка подключения

На сервере:

```bash
# Проверка соединений
sudo netstat -anp | grep 8080

# Проверка логов
tail -f /opt/isup-bridge/logs/isup_bridge.log
```

## 🔍 Мониторинг

### Grafana Dashboard (опционально)

```bash
# Запуск через Docker Compose
cd /opt/isup-bridge/deployment
docker-compose up -d grafana

# Доступ: http://your-server:3000
# Login: admin / admin
```

### Alarms

Настройка email оповещений в `config/config.yaml`:

```yaml
monitoring:
  email_alerts:
    enabled: true
    smtp_host: "smtp.gmail.com"
    from_email: "alerts@yourcompany.ru"
    to_emails:
      - "admin@yourcompany.ru"
```

## 🆘 Troubleshooting

### Сервис не запускается

```bash
# Проверка синтаксиса Python
/opt/isup-bridge/venv/bin/python /opt/isup-bridge/src/main.py

# Проверка конфигурации
python3 -c "import yaml; yaml.safe_load(open('/opt/isup-bridge/config/config.yaml'))"

# Проверка прав
ls -la /opt/isup-bridge
```

### События не приходят

```bash
# Проверка порта
sudo netstat -tlnp | grep 8080

# Проверка firewall
sudo ufw status
sudo iptables -L -n

# Тест соединения
telnet your-server-ip 8080
```

### 1С не отвечает

```bash
# Проверка доступности
curl -I https://your-1c-server.ru/hs/access/events

# Проверка учетных данных
curl -u username:password https://your-1c-server.ru/hs/access/events

# Проверка сертификатов
curl -v https://your-1c-server.ru
```

### Высокая нагрузка

```bash
# Мониторинг ресурсов
htop
iotop

# Логи ошибок
tail -f /opt/isup-bridge/logs/errors.log

# Pending события
ls -l /opt/isup-bridge/pending_events/
```

## 🔄 Обновление

```bash
# Остановка сервиса
sudo systemctl stop isup-bridge

# Бэкап
sudo cp -r /opt/isup-bridge /opt/isup-bridge.backup.$(date +%Y%m%d)

# Обновление кода
cd /opt/isup-bridge
sudo -u isupuser git pull origin main

# Обновление зависимостей
sudo -u isupuser /opt/isup-bridge/venv/bin/pip install -r requirements.txt --upgrade

# Запуск
sudo systemctl start isup-bridge

# Проверка
sudo systemctl status isup-bridge
```

## 🗑️ Удаление

```bash
# Остановка и отключение сервиса
sudo systemctl stop isup-bridge
sudo systemctl disable isup-bridge
sudo rm /etc/systemd/system/isup-bridge.service
sudo systemctl daemon-reload

# Удаление файлов
sudo rm -rf /opt/isup-bridge

# Удаление пользователя
sudo userdel -r isupuser

# Firewall
sudo ufw delete allow 8080/tcp
sudo ufw delete allow 8081/tcp
```

## 📞 Поддержка

При проблемах обращайтесь:
- 📧 Email: support@yourcompany.ru
- 🐛 Issues: https://github.com/your-username/isup-bridge-production/issues
- 📚 Wiki: https://github.com/your-username/isup-bridge-production/wiki
