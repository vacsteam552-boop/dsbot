FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости для Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем Playwright браузеры
RUN playwright install chromium
RUN playwright install-deps

# Копируем код
COPY . .

# Запускаем бота
CMD ["python", "main.py"]