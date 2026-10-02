FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000

# Watchdog перезапускает uvicorn, когда в смонтированной папке меняется Python-файл.
# Опрос каталога нужен, потому что события файловой системы с диска Windows в контейнер не доходят.
CMD ["watchmedo", "auto-restart", "--debug-force-polling", "--interval", "1", "-d", "/app", "-p", "*.py", "-R", "--", "python", "-m", "uvicorn", "crm:app", "--host", "0.0.0.0", "--port", "8000"]
