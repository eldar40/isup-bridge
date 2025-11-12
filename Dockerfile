FROM python:3.11-slim

LABEL maintainer="your-email@example.com"
LABEL description="ISUP Bridge - Hikvision to 1C integration"

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/
COPY config/ ./config/

# Создание необходимых директорий
RUN mkdir -p logs storage/pending_events

# Создание непривилегированного пользователя
RUN useradd -m -u 1000 isupuser && \
    chown -R isupuser:isupuser /app

USER isupuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import socket; s = socket.socket(); s.connect(('localhost', 8080)); s.close()" || exit 1

EXPOSE 8080 9090

CMD ["python", "src/main.py"]
