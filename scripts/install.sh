#!/bin/bash
# ISUP Bridge Installation Script
# Автоматическая установка на Ubuntu/Debian сервере

set -e

echo "🚀 ISUP Bridge Installation Script"
echo "===================================="

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Please run as root (use sudo)"
    exit 1
fi

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Установка Docker
install_docker() {
    echo -e "${GREEN}📦 Installing Docker...${NC}"
    
    if command -v docker &> /dev/null; then
        echo "✅ Docker already installed"
        return
    fi
    
    apt-get update
    apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
        lsb-release
    
    # Docker GPG key
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    
    # Docker repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    echo "✅ Docker installed successfully"
}

# Установка Docker Compose
install_docker_compose() {
    echo -e "${GREEN}📦 Installing Docker Compose...${NC}"
    
    if command -v docker-compose &> /dev/null; then
        echo "✅ Docker Compose already installed"
        return
    fi
    
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
        -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    
    echo "✅ Docker Compose installed successfully"
}

# Создание пользователя
create_user() {
    echo -e "${GREEN}👤 Creating isupbridge user...${NC}"
    
    if id "isupbridge" &>/dev/null; then
        echo "✅ User already exists"
        return
    fi
    
    useradd -m -s /bin/bash isupbridge
    usermod -aG docker isupbridge
    
    echo "✅ User created"
}

# Создание директорий
create_directories() {
    echo -e "${GREEN}📁 Creating directories...${NC}"
    
    mkdir -p /opt/isup-bridge/{config,storage/pending_events,logs}
    chown -R isupbridge:isupbridge /opt/isup-bridge
    
    echo "✅ Directories created"
}

# Настройка firewall
setup_firewall() {
    echo -e "${GREEN}🔒 Configuring firewall...${NC}"
    
    if ! command -v ufw &> /dev/null; then
        apt-get install -y ufw
    fi
    
    ufw --force enable
    ufw allow 22/tcp comment "SSH"
    ufw allow 8080/tcp comment "ISUP Bridge"
    
    echo "✅ Firewall configured"
}

# Клонирование репозитория
clone_repo() {
    echo -e "${GREEN}📥 Cloning repository...${NC}"
    
    cd /opt/isup-bridge
    
    if [ -d ".git" ]; then
        echo "Updating existing repository..."
        sudo -u isupbridge git pull
    else
        echo "Enter GitHub repository URL:"
        read REPO_URL
        sudo -u isupbridge git clone "$REPO_URL" .
    fi
    
    echo "✅ Repository cloned"
}

# Конфигурация
configure() {
    echo -e "${GREEN}⚙️ Configuration...${NC}"
    
    cd /opt/isup-bridge
    
    # Копирование примеров конфигов
    if [ ! -f "config/tenants.yaml" ]; then
        cp config/tenants.yaml.example config/tenants.yaml 2>/dev/null || true
    fi
    
    if [ ! -f "config/card_mapping.yaml" ]; then
        cp config/card_mapping.yaml.example config/card_mapping.yaml 2>/dev/null || true
    fi
    
    echo ""
    echo -e "${YELLOW}⚠️ IMPORTANT: Edit configuration files:${NC}"
    echo "  1. nano /opt/isup-bridge/config/tenants.yaml"
    echo "  2. nano /opt/isup-bridge/config/card_mapping.yaml"
    echo ""
}

# Создание systemd сервиса
create_systemd_service() {
    echo -e "${GREEN}🔧 Creating systemd service...${NC}"
    
    cat > /etc/systemd/system/isup-bridge.service <<EOF
[Unit]
Description=ISUP Bridge Service
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/isup-bridge
User=isupbridge
Group=isupbridge
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable isup-bridge.service
    
    echo "✅ Systemd service created"
}

# Финальные инструкции
final_instructions() {
    echo ""
    echo -e "${GREEN}=============================================${NC}"
    echo -e "${GREEN}✅ Installation completed successfully!${NC}"
    echo -e "${GREEN}=============================================${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Edit configuration:"
    echo "     nano /opt/isup-bridge/config/tenants.yaml"
    echo ""
    echo "  2. Start the service:"
    echo "     systemctl start isup-bridge"
    echo ""
    echo "  3. Check status:"
    echo "     systemctl status isup-bridge"
    echo ""
    echo "  4. View logs:"
    echo "     docker-compose -f /opt/isup-bridge/docker-compose.yml logs -f"
    echo ""
    echo "  5. Test connection:"
    echo "     curl http://localhost:8080/health"
    echo ""
    echo -e "${YELLOW}⚠️ Don't forget to configure your Hikvision controller!${NC}"
    echo ""
}

# Основной процесс установки
main() {
    echo "Starting installation..."
    echo ""
    
    install_docker
    install_docker_compose
    create_user
    create_directories
    setup_firewall
    
    echo ""
    echo -e "${YELLOW}Do you want to clone from GitHub? (y/n)${NC}"
    read -r CLONE_REPO
    
    if [ "$CLONE_REPO" = "y" ]; then
        clone_repo
    fi
    
    configure
    create_systemd_service
    final_instructions
}

# Запуск
main
