# ISUP Bridge - Deployment Guide

## 📋 Pre-requisites

- Ubuntu 20.04+ or Debian 11+ server
- Root or sudo access
- Minimum 2GB RAM, 10GB disk space
- Open port 8080 for ISUP protocol
- Network access to your 1C servers

---

## 🚀 Quick Deployment

### Step 1: Prepare Server

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install git
sudo apt install git -y
```

### Step 2: Clone Repository

```bash
# Clone your repository
cd /opt
sudo git clone https://github.com/YOUR_USERNAME/isup-bridge-production.git
cd isup-bridge-production
```

### Step 3: Run Installation Script

```bash
# Make script executable
sudo chmod +x scripts/install.sh

# Run installation
sudo ./scripts/install.sh
```

The script will:
- Install Docker and Docker Compose
- Create system user `isupbridge`
- Setup firewall rules
- Create systemd service
- Prepare directory structure

### Step 4: Configure

```bash
# Edit tenants configuration
sudo nano /opt/isup-bridge-production/config/tenants.yaml

# Configure card mapping
sudo nano /opt/isup-bridge-production/config/card_mapping.yaml
```

**Important**: Change passwords and URLs in `tenants.yaml`!

### Step 5: Start Service

```bash
# Start with Docker Compose
cd /opt/isup-bridge-production
sudo docker-compose up -d

# Or use systemd
sudo systemctl start isup-bridge
sudo systemctl enable isup-bridge
```

### Step 6: Verify

```bash
# Check if running
sudo docker-compose ps

# View logs
sudo docker-compose logs -f isup-bridge

# Test health endpoint
curl http://localhost:8080/health
```

---

## 🔧 Configuration Details

### Tenants Configuration

File: `config/tenants.yaml`

```yaml
tenants:
  - tenant_id: office_moscow           # Unique ID
    tenant_name: "Moscow Office"       # Display name
    base_url: "https://1c-moscow.company.ru"  # 1C server URL
    endpoint: "/hs/access/events"      # HTTP service endpoint
    username: "rest_user"              # 1C username
    password: "strong_password"        # 1C password
    enabled: true                      # Enable/disable tenant
    is_default: true                   # Default tenant for unmatched devices
    
    # Optional: Route specific devices to this tenant
    device_serials:
      - "HIKVISION001"
      - "HIKVISION002"
    
    # Optional: Route specific doors to this tenant
    door_numbers: [1, 2, 3]
    
    # Retry settings
    max_retries: 3
    retry_delay: 5
    timeout: 30
```

### Card Mapping

File: `config/card_mapping.yaml`

```yaml
card_mapping:
  "GA4818739": "EMP001"     # Employee Ivanov
  "123456": "EMP002"        # Employee Petrov
  "789012": "EMP003"        # Employee Sidorov
```

---

## 🏢 Hikvision Controller Configuration

### Web Interface Setup

1. **Login** to controller web interface (default: admin/12345)

2. **Navigate**: Configuration → Network → Advanced Settings → ISUP

3. **Configure ISUP**:
   - Protocol Version: `ISUP 5.0`
   - Enable: `✓ Checked`
   - Server Address: `YOUR_SERVER_IP`
   - Server Port: `8080`
   - Device Key: *(optional, any value)*

4. **Save** and **reboot** controller

5. **Test** connection:
   ```bash
   # On server, check logs
   sudo docker-compose logs -f | grep "TCP connection"
   ```

### Common Issues

**Problem**: Controller can't connect

**Solution**:
```bash
# Check firewall
sudo ufw status

# Allow controller IP
sudo ufw allow from CONTROLLER_IP to any port 8080

# Check if service is listening
sudo netstat -tulpn | grep 8080
```

---

## 🔒 1C HTTP Service Setup

### Create HTTP Service in 1C

1. **Open** Configuration in 1C Designer

2. **Create HTTP Service**:
   - Name: `AccessControl`
   - Root URL: `access`
   - Create URL template: `events` (POST method)

3. **Add Module Code**:

```bsl
// Module: HTTPСервис.AccessControl
#Область ПрограммныйИнтерфейс

Процедура events(Запрос, Ответ) Экспорт
    
    Попытка
        // Чтение JSON из тела запроса
        ТелоЗапроса = Запрос.ПолучитьТелоКакСтроку();
        
        Если ПустаяСтрока(ТелоЗапроса) Тогда
            ВызватьИсключение "Пустое тело запроса";
        КонецЕсли;
        
        // Парсинг JSON
        ЧтениеJSON = Новый ЧтениеJSON;
        ЧтениеJSON.УстановитьСтроку(ТелоЗапроса);
        Данные = ПрочитатьJSON(ЧтениеJSON);
        ЧтениеJSON.Закрыть();
        
        // Обработка события
        ОбработатьСобытиеДоступа(Данные);
        
        // Успешный ответ
        Ответ.КодСостояния = 200;
        Ответ.Заголовки.Вставить("Content-Type", "application/json");
        Ответ.УстановитьТелоИзСтроки("{""status"":""ok""}");
        
    Исключение
        // Обработка ошибки
        ТекстОшибки = ПодробноеПредставлениеОшибки(ИнформацияОбОшибке());
        
        ЗаписьЖурналаРегистрации(
            "AccessControl.Error",
            УровеньЖурналаРегистрации.Ошибка,
            ,
            ,
            ТекстОшибки
        );
        
        Ответ.КодСостояния = 500;
        Ответ.Заголовки.Вставить("Content-Type", "application/json");
        Ответ.УстановитьТелоИзСтроки(
            "{""status"":""error"",""message"":""" + ТекстОшибки + """}"
        );
    КонецПопытки;
    
КонецПроцедуры

#КонецОбласти

#Область СлужебныеПроцедурыИФункции

Процедура ОбработатьСобытиеДоступа(Данные)
    
    // Получение данных из JSON
    КодСотрудника = Данные.Получить("employee_code");
    ВремяСобытия = XMLЗначение(Тип("Дата"), Данные.Получить("event_timestamp"));
    ТипСобытия = Данные.Получить("event_type"); // WORK_START или WORK_END
    
    // Поиск сотрудника
    Сотрудник = НайтиСотрудникаПоКоду(КодСотрудника);
    
    Если Сотрудник = Неопределено Тогда
        ВызватьИсключение "Сотрудник с кодом " + КодСотрудника + " не найден";
    КонецЕсли;
    
    // Запись в регистр учета рабочего времени
    НаборЗаписей = РегистрыСведений.УчетРабочегоВремени.СоздатьНаборЗаписей();
    
    Запись = НаборЗаписей.Добавить();
    Запись.Сотрудник = Сотрудник;
    Запись.Дата = ВремяСобытия;
    Запись.ТипСобытия = ?(ТипСобытия = "WORK_START", 
        Перечисления.ТипыСобытийРабочегоВремени.Приход,
        Перечисления.ТипыСобытийРабочегоВремени.Уход);
    Запись.Источник = "СКУД Hikvision";
    
    НаборЗаписей.Записать();
    
    // Логирование
    ЗаписьЖурналаРегистрации(
        "AccessControl.Event",
        УровеньЖурналаРегистрации.Информация,
        ,
        Сотрудник,
        СтрШаблон("Событие: %1, Время: %2", ТипСобытия, ВремяСобытия)
    );
    
КонецПроцедуры

Функция НайтиСотрудникаПоКоду(КодСотрудника)
    
    Запрос = Новый Запрос;
    Запрос.Текст = 
    "ВЫБРАТЬ ПЕРВЫЕ 1
    |    Сотрудники.Ссылка КАК Сотрудник
    |ИЗ
    |    Справочник.Сотрудники КАК Сотрудники
    |ГДЕ
    |    Сотрудники.Код = &КодСотрудника
    |    И НЕ Сотрудники.ПометкаУдаления";
    
    Запрос.УстановитьПараметр("КодСотрудника", КодСотрудника);
    
    Результат = Запрос.Выполнить();
    Выборка = Результат.Выбрать();
    
    Если Выборка.Следующий() Тогда
        Возврат Выборка.Сотрудник;
    КонецЕсли;
    
    Возврат Неопределено;
    
КонецФункции

#КонецОбласти
```

4. **Publish** configuration to 1C server

5. **Enable** HTTP service in IIS/Apache

6. **Test** endpoint:
```bash
curl -X POST https://your-1c-server.ru/hs/access/events \
  -u username:password \
  -H "Content-Type: application/json" \
  -d '{
    "employee_code": "TEST",
    "event_timestamp": "2024-01-15T09:00:00+03:00",
    "event_type": "WORK_START"
  }'
```

---

## 📊 Monitoring Setup

### Enable Prometheus + Grafana

```bash
# Start with monitoring profile
sudo docker-compose --profile monitoring up -d

# Access Grafana
open http://YOUR_SERVER_IP:3000
# Login: admin / admin
```

### Import Dashboard

1. Open Grafana
2. Navigate: Dashboards → Import
3. Upload: `deploy/grafana/dashboards/isup-bridge.json`

---

## 🔄 Updates and Maintenance

### Update Bridge

```bash
cd /opt/isup-bridge-production
sudo git pull
sudo docker-compose down
sudo docker-compose build
sudo docker-compose up -d
```

### Backup Configuration

```bash
# Backup script
sudo tar -czf /backup/isup-bridge-config-$(date +%Y%m%d).tar.gz \
  /opt/isup-bridge-production/config/
```

### View Logs

```bash
# Real-time logs
sudo docker-compose logs -f

# Last 100 lines
sudo docker-compose logs --tail=100

# Specific service
sudo docker-compose logs isup-bridge
```

---

## 🆘 Troubleshooting

### Check Service Status

```bash
sudo systemctl status isup-bridge
sudo docker-compose ps
```

### Test Connectivity

```bash
# Test from server
curl http://localhost:8080/health

# Test from external
curl http://YOUR_SERVER_IP:8080/health

# Test 1C connectivity
curl -X POST https://1c-server.ru/hs/access/events \
  -u username:password \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

### Common Issues

1. **Port 8080 already in use**
   ```bash
   sudo netstat -tulpn | grep 8080
   sudo systemctl stop conflicting-service
   ```

2. **Docker not starting**
   ```bash
   sudo systemctl restart docker
   sudo docker-compose down
   sudo docker-compose up -d
   ```

3. **1C connection refused**
   - Check firewall on 1C server
   - Verify credentials in `tenants.yaml`
   - Check 1C HTTP service is published

---

## 📞 Support

For issues or questions:
- GitHub Issues: https://github.com/YOUR_USERNAME/isup-bridge-production/issues
- Email: support@your-domain.com

---

**Last updated**: 2024-01-15
