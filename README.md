# Мини-CRM на вайбкодинге

Локальная CRM для личного бизнеса: клиенты, сделки и задачи. Бэкенд на FastAPI и SQLite, окно на Tkinter, выгрузка отчётов в Google Таблицы через личный OAuth-аккаунт.

## Структура

```text
backend/         FastAPI, модели и CRUD на sqlite3
ui/              Окно Tkinter, ходит в API по HTTP
integrations/    Google Drive и Google Sheets
docker-compose.yml
```

## Что умеет

- Клиенты: создание, правка, архив, удаление, поиск без учёта регистра
- Сделки и заказы: со статусом, с клиентом или без него
- Задачи и напоминания: связь с клиентом и/или сделкой, отметка «выполнено»
- Выгрузка любой видимой таблицы в новую Google Таблицу с оформлением
- Запуск API в Docker с автоперезапуском при правках кода (Watchdog)

## Подготовка

Нужен Python 3.13.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Секреты в репозиторий не входят. Положите в `integrations/`:

- JSON сервисного аккаунта
- JSON OAuth-клиента (`client_secret*.json`)

В корневом `.env` при необходимости:

```text
FOLDER_ID=id_рабочей_папки_на_диске
SPREADSHEET_ID=id_таблицы
SHEET_NAME=
CRM_DB_PATH=
```

Папку на Google Диске откройте на редактирование для сервисного аккаунта.

## Запуск API

### Docker Compose

```powershell
docker compose up --build -d
```

API: http://127.0.0.1:8000  
Документация: http://127.0.0.1:8000/docs

Папка `backend/` смонтирована в контейнер. Watchdog сам перезапускает uvicorn при изменении `.py` файлов.

Остановка:

```powershell
docker compose down
```

### Локально без Docker

```powershell
python backend\crm.py
```

## Запуск окна CRM

Сервер должен уже работать.

```powershell
python ui\crm_app.py
```

Внизу окна — **Настройки Google**: пути к JSON и id папки. Значения сохраняются в `ui/google_settings.txt`. Рядом с полем id папки есть кнопка **Вставить**.

На каждой вкладке с таблицей есть **Выгрузить отчёт**: создаётся Google Таблица через Drive API, данные записываются через Sheets API. При первой выгрузке откроется вход в личный Google-аккаунт.

## Google-интеграции отдельно

```powershell
python integrations\google_drive.py
python integrations\create_file.py
python integrations\report_generator.py
```

## API кратко

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/search?q=` | Поиск по клиентам и сделкам |
| POST/GET/PATCH/DELETE | `/clients`, `/clients/{id}` | Клиенты |
| POST | `/clients/{id}/archive`, `/restore` | Архив |
| POST/GET/PATCH/DELETE | `/deals`, `/deals/{id}` | Сделки |
| PUT | `/deals/{id}/client` | Прикрепить или отвязать клиента |
| POST/GET/PATCH/DELETE | `/tasks`, `/tasks/{id}` | Задачи |
| POST | `/tasks/{id}/complete`, `/reopen` | Статус задачи |

## Зависимости

См. `requirements.txt`: FastAPI, Uvicorn, Watchdog, Google API-клиенты, python-dotenv. Tkinter входит в стандартную поставку Python.
