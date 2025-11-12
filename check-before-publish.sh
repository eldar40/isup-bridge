#!/bin/bash
# Скрипт проверки проекта перед публикацией

echo "🔍 Проверка проекта ISUP Bridge перед публикацией"
echo "=================================================="
echo ""

ERRORS=0
WARNINGS=0

# Цвета для вывода
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Функции проверки
check_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✅${NC} $1 существует"
        return 0
    else
        echo -e "${RED}❌${NC} $1 отсутствует"
        ((ERRORS++))
        return 1
    fi
}

check_dir() {
    if [ -d "$1" ]; then
        echo -e "${GREEN}✅${NC} Директория $1 существует"
        return 0
    else
        echo -e "${RED}❌${NC} Директория $1 отсутствует"
        ((ERRORS++))
        return 1
    fi
}

warn() {
    echo -e "${YELLOW}⚠️${NC} $1"
    ((WARNINGS++))
}

# Проверка структуры проекта
echo "📁 Проверка структуры проекта..."
check_dir "src"
check_dir "config"
check_dir "deployment"
check_dir "tests"
check_dir "docs"
echo ""

# Проверка основных файлов
echo "📄 Проверка основных файлов..."
check_file "README.md"
check_file "LICENSE"
check_file ".gitignore"
check_file "requirements.txt"
check_file "GETTING_STARTED.md"
check_file "DEPLOYMENT_CHECKLIST.md"
echo ""

# Проверка исходного кода
echo "🐍 Проверка исходного кода..."
check_file "src/main.py"
check_file "src/isup_protocol.py"
check_file "src/tenant_manager.py"
echo ""

# Проверка конфигурации
echo "⚙️ Проверка конфигурации..."
check_file "config/config.yaml"
echo ""

# Проверка deployment файлов
echo "🚀 Проверка deployment файлов..."
check_file "deployment/Dockerfile"
check_file "deployment/docker-compose.yml"
check_file "deployment/install.sh"
check_file "deployment/systemd/isup-bridge.service"
echo ""

# Проверка тестов
echo "🧪 Проверка тестов..."
check_file "tests/test_isup_protocol.py"
echo ""

# Проверка документации
echo "📚 Проверка документации..."
check_file "docs/INSTALLATION.md"
echo ""

# Проверка Python синтаксиса
echo "🔍 Проверка Python синтаксиса..."
if command -v python3 &> /dev/null; then
    for file in src/*.py tests/*.py; do
        if [ -f "$file" ]; then
            if python3 -m py_compile "$file" 2>/dev/null; then
                echo -e "${GREEN}✅${NC} $file - синтаксис OK"
            else
                echo -e "${RED}❌${NC} $file - ошибка синтаксиса"
                ((ERRORS++))
            fi
        fi
    done
else
    warn "Python3 не установлен, пропуск проверки синтаксиса"
fi
echo ""

# Проверка YAML файлов
echo "📝 Проверка YAML конфигурации..."
if command -v python3 &> /dev/null; then
    for file in config/*.yaml; do
        if [ -f "$file" ]; then
            if python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>/dev/null; then
                echo -e "${GREEN}✅${NC} $file - валидный YAML"
            else
                echo -e "${RED}❌${NC} $file - невалидный YAML"
                ((ERRORS++))
            fi
        fi
    done
else
    warn "Python3 не установлен, пропуск проверки YAML"
fi
echo ""

# Проверка размера файлов
echo "📊 Проверка размера файлов..."
LARGE_FILES=$(find . -type f -size +1M ! -path "./.git/*" ! -path "./venv/*")
if [ -n "$LARGE_FILES" ]; then
    warn "Найдены большие файлы (>1MB):"
    echo "$LARGE_FILES"
else
    echo -e "${GREEN}✅${NC} Больших файлов не найдено"
fi
echo ""

# Проверка чувствительных данных
echo "🔐 Проверка на чувствительные данные..."
SENSITIVE_PATTERNS=("password.*=.*[^CHANGE_ME]" "secret" "api_key" "token.*=")
for pattern in "${SENSITIVE_PATTERNS[@]}"; do
    FOUND=$(grep -r -i "$pattern" config/*.yaml 2>/dev/null | grep -v "CHANGE_ME" | grep -v "example")
    if [ -n "$FOUND" ]; then
        warn "Возможно найдены реальные пароли в конфигурации!"
        echo "$FOUND"
    fi
done
echo ""

# Проверка .gitignore
echo "🚫 Проверка .gitignore..."
if grep -q "config.yaml" .gitignore 2>/dev/null; then
    echo -e "${GREEN}✅${NC} config.yaml в .gitignore"
else
    warn "config.yaml должен быть в .gitignore"
fi

if grep -q "*.log" .gitignore 2>/dev/null; then
    echo -e "${GREEN}✅${NC} *.log в .gitignore"
else
    warn "*.log должен быть в .gitignore"
fi
echo ""

# Проверка прав исполнения
echo "🔧 Проверка прав исполнения скриптов..."
for script in deployment/install.sh publish-to-github.sh; do
    if [ -f "$script" ]; then
        if [ -x "$script" ]; then
            echo -e "${GREEN}✅${NC} $script исполняемый"
        else
            warn "$script не имеет прав на исполнение (chmod +x $script)"
        fi
    fi
done
echo ""

# Проверка TODO и FIXME
echo "📝 Проверка TODO и FIXME..."
TODO_COUNT=$(grep -r "TODO\|FIXME" src/ tests/ 2>/dev/null | wc -l)
if [ "$TODO_COUNT" -gt 0 ]; then
    warn "Найдено $TODO_COUNT TODO/FIXME комментариев"
    grep -r "TODO\|FIXME" src/ tests/ 2>/dev/null | head -5
else
    echo -e "${GREEN}✅${NC} TODO/FIXME не найдено"
fi
echo ""

# Итоги
echo "=================================================="
echo "📊 РЕЗУЛЬТАТЫ ПРОВЕРКИ"
echo "=================================================="
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✅ Все проверки пройдены успешно!${NC}"
    echo ""
    echo "🚀 Проект готов к публикации на GitHub!"
    echo ""
    echo "Следующие шаги:"
    echo "1. Запустите: ./publish-to-github.sh"
    echo "2. Создайте репозиторий на GitHub"
    echo "3. Push код командами из скрипта"
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠️ Проверка завершена с предупреждениями: $WARNINGS${NC}"
    echo ""
    echo "Рекомендуется исправить предупреждения перед публикацией."
    echo "Но проект технически готов к публикации."
    exit 0
else
    echo -e "${RED}❌ Обнаружено ошибок: $ERRORS${NC}"
    echo -e "${YELLOW}⚠️ Предупреждений: $WARNINGS${NC}"
    echo ""
    echo "Необходимо исправить ошибки перед публикацией!"
    exit 1
fi
