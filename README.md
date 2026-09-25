# Автоматический отчёт в Google Таблице

Модуль для работы с Google Таблицей через сервисный аккаунт и окно, которое записывает случайный отчёт на новый лист.

## Что внутри

- `google_sheet.py` — класс `GoogleSheet` с операциями чтения, добавления, изменения и очистки ячеек. Его можно импортировать в другое приложение.
- `report_generator.py` — окно на tkinter: создаёт новый лист, записывает случайные строки и оформляет заголовок, шапку, цвета доходов и расходов, объединённые ячейки и сумму.

## Подготовка

Нужен Python 3.13.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Рядом с кодом положите JSON-ключ сервисного аккаунта и назовите файл `zerocoderlessons-480509-df826ffcdec9.json`. Этот файл в репозиторий не входит. Таблицу откройте на редактирование для почты сервисного аккаунта из этого ключа.

В файле `.env` укажите таблицу:

```text
SPREADSHEET_ID=часть_ссылки_между_/d/_и_/edit
SHEET_NAME=
```

`SHEET_NAME` можно оставить пустым: тогда `google_sheet.py` читает первый лист.

## Запуск

Проверка чтения:

```powershell
python google_sheet.py
```

Генератор отчёта:

```powershell
python report_generator.py
```

## Использование модуля

```python
from google_sheet import GoogleSheet

sheet = GoogleSheet(
    spreadsheet_id="id_из_ссылки",
    credentials_path="zerocoderlessons-480509-df826ffcdec9.json",
    sheet_name="Лист1",
)
rows = sheet.read()
sheet.create(["Новая", "строка"])
sheet.update("A2:B2", [["Значение", 10]])
sheet.delete("A2:B2")
```
