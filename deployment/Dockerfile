FROM python:3.10-slim

LABEL maintainer="your-email@example.com"
LABEL description="ISUP Bridge for Hikvision to 1C Integration"

# Переменные окружения
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Рабочая директория
WORKDIR /app

# Установка зависимостей системы
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/
COPY config/ ./config/

# Создание директорий
RUN mkdir -p /app/logs /app/pending_events

# Непривилегированный пользователь
RUN useradd -m -u 1000 isupuser && \
    chown -R isupuser:isupuser /app

USER isupuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

# Порты
EXPOSE 8080 8081

# Запуск
CMD ["python", "src/main.py"]
