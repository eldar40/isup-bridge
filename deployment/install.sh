#!/bin/bash
# Скрипт установки ISUP Bridge на production сервер

set -e

echo "🚀 ISUP Bridge - Installation Script"
echo "====================================="

# Проверка прав root
if [[ $EUID -ne 0 ]]; then
   echo "❌ Этот скрипт должен быть запущен с правами root" 
   exit 1
fi

# Переменные
INSTALL_DIR="/opt/isup-bridge"
SERVICE_USER="isupuser"
PYTHON_VERSION="3.10"

echo "📋 Проверка системных требований..."

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "📥 Установка Python ${PYTHON_VERSION}..."
    apt-get update
    apt-get install -y python${PYTHON_VERSION} python3-pip python3-venv
fi

# Проверка Git
if ! command -v git &> /dev/null; then
    echo "📥 Установка Git..."
    apt-get install -y git
fi

# Создание пользователя
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "👤 Создание пользователя ${SERVICE_USER}..."
    useradd -m -s /bin/bash $SERVICE_USER
fi

# Создание директорий
echo "📁 Создание директорий..."
mkdir -p $INSTALL_DIR/{src,config,logs,pending_events}
chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR

# Клонирование репозитория (или копирование файлов)
echo "📦 Установка файлов..."
if [ -d "./src" ]; then
    # Локальная установка
    cp -r ./src $INSTALL_DIR/
    cp -r ./config $INSTALL_DIR/
    cp requirements.txt $INSTALL_DIR/
else
    # Установка из Git
    echo "Укажите URL репозитория:"
    read REPO_URL
    git clone $REPO_URL /tmp/isup-bridge
    cp -r /tmp/isup-bridge/src $INSTALL_DIR/
    cp -r /tmp/isup-bridge/config $INSTALL_DIR/
    cp /tmp/isup-bridge/requirements.txt $INSTALL_DIR/
    rm -rf /tmp/isup-bridge
fi

# Python виртуальное окружение
echo "🐍 Настройка Python окружения..."
sudo -u $SERVICE_USER python3 -m venv $INSTALL_DIR/venv
sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/pip install --upgrade pip
sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt

# Права доступа
echo "🔒 Настройка прав доступа..."
chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR
chmod +x $INSTALL_DIR/src/main.py

# Конфигурация
echo "⚙️  Настройка конфигурации..."
if [ ! -f "$INSTALL_DIR/config/config.yaml" ]; then
    echo "❗ Необходимо настроить config/config.yaml"
    echo "   Пример находится в config/config.yaml"
fi

# Systemd сервис
echo "🔧 Установка systemd сервиса..."
if [ -f "./deployment/systemd/isup-bridge.service" ]; then
    cp ./deployment/systemd/isup-bridge.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable isup-bridge.service
else
    echo "⚠️  Файл сервиса не найден"
fi

# Firewall
echo "🔥 Настройка firewall..."
if command -v ufw &> /dev/null; then
    ufw allow 8080/tcp comment 'ISUP Bridge'
    ufw allow 8081/tcp comment 'ISUP API'
fi

echo ""
echo "✅ Установка завершена!"
echo ""
echo "Следующие шаги:"
echo "1. Настройте конфигурацию: nano $INSTALL_DIR/config/config.yaml"
echo "2. Запустите сервис: systemctl start isup-bridge"
echo "3. Проверьте статус: systemctl status isup-bridge"
echo "4. Просмотр логов: journalctl -u isup-bridge -f"
echo ""
echo "HTTP API доступен на: http://localhost:8081/health"
