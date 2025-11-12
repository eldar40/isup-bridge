#!/bin/bash
# Скрипт для публикации проекта на GitHub

echo "📦 Подготовка к публикации на GitHub"
echo "======================================"

# Переход в директорию проекта
cd /Users/el/Downloads/isup-bridge-production

# Инициализация Git
if [ ! -d ".git" ]; then
    echo "🔧 Инициализация Git репозитория..."
    git init
    git branch -M main
fi

# Добавление файлов
echo "📝 Добавление файлов..."
git add .

# Первый коммит
echo "💾 Создание коммита..."
git commit -m "Initial commit: ISUP Bridge v1.0.0 - Production Ready

Features:
- Full ISUP v5 protocol support
- Multi-tenant architecture for multiple 1C servers  
- Automatic retry with exponential backoff
- Local storage for failed events
- Health monitoring and metrics
- Production-ready with systemd service
- Docker support
- Comprehensive documentation

Ready for enterprise deployment"

echo ""
echo "✅ Локальный репозиторий готов!"
echo ""
echo "Следующие шаги:"
echo ""
echo "1. Создайте новый репозиторий на GitHub:"
echo "   https://github.com/new"
echo "   Название: isup-bridge-production"
echo "   Описание: Production-ready ISUP Bridge for Hikvision to 1C Integration"
echo "   Public/Private: на ваше усмотрение"
echo ""
echo "2. Выполните команды:"
echo "   git remote add origin https://github.com/YOUR-USERNAME/isup-bridge-production.git"
echo "   git push -u origin main"
echo ""
echo "3. Создайте первый релиз:"
echo "   git tag -a v1.0.0 -m 'Production ready release'"
echo "   git push origin v1.0.0"
echo ""
echo "4. На GitHub создайте Release из этого тега"
echo ""
