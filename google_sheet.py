"""Клиент Google Таблиц для подключения в другие приложения.

Пример:

    from google_sheet import GoogleSheet

    sheet = GoogleSheet(
        spreadsheet_id="id_из_ссылки",
        credentials_path="путь/к/ключу.json",
        sheet_name="Лист1",
    )
    rows = sheet.read()
    sheet.create(["Новая", "строка"])
    sheet.update("A2:B2", [["Значение", 10]])
    sheet.delete("A2:B2")
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_CREDENTIALS = Path(__file__).with_name(
    "zerocoderlessons-480509-df826ffcdec9.json"
)


class GoogleSheetError(Exception):
    """Ошибка чтения или изменения Google Таблицы."""


def _prefer_ipv4() -> None:
    """httplib2 сначала выбирает IPv6, а он здесь не отвечает."""
    if getattr(socket.getaddrinfo, "_google_sheet_ipv4", False):
        return

    original = socket.getaddrinfo

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        results = original(host, port, family, type, proto, flags)
        ipv4 = [item for item in results if item[0] == socket.AF_INET]
        return ipv4 or results

    getaddrinfo._google_sheet_ipv4 = True
    socket.getaddrinfo = getaddrinfo


class GoogleSheet:
    """CRUD-операции с одним листом Google Таблицы через Sheets API."""

    def __init__(
        self,
        spreadsheet_id: str,
        credentials_path: str | Path | None = None,
        sheet_name: str | None = None,
    ) -> None:
        if not spreadsheet_id or not str(spreadsheet_id).strip():
            raise GoogleSheetError("Не указан spreadsheet_id.")

        credentials_file = Path(credentials_path or DEFAULT_CREDENTIALS)
        if not credentials_file.is_file():
            raise GoogleSheetError(f"JSON-ключ не найден: {credentials_file}")

        self.spreadsheet_id = str(spreadsheet_id).strip()
        self.sheet_name = sheet_name.strip() if sheet_name else None
        self._sheet_id: int | None = None

        _prefer_ipv4()
        credentials = Credentials.from_service_account_file(
            str(credentials_file),
            scopes=SCOPES,
        )
        self._service = build(
            "sheets",
            "v4",
            credentials=credentials,
            cache_discovery=False,
        )
        self._values = self._service.spreadsheets().values()

    def read(self, range_name: str | None = None) -> list[list]:
        """Возвращает ячейки диапазона. Без диапазона читает весь лист."""
        response = self._execute(
            self._values.get(
                spreadsheetId=self.spreadsheet_id,
                range=self._a1(range_name),
                valueRenderOption="FORMATTED_VALUE",
            )
        )
        return response.get("values", [])

    def create(self, values: list) -> dict:
        """Добавляет одну строку или несколько строк в конец листа."""
        response = self._execute(
            self._values.append(
                spreadsheetId=self.spreadsheet_id,
                range=self._a1(),
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": self._as_rows(values)},
            )
        )
        return response

    def update(self, range_name: str, values: list) -> dict:
        """Записывает значения в указанный диапазон, например 'B2' или 'A2:C4'."""
        if not range_name:
            raise GoogleSheetError("Для update нужен диапазон ячеек.")
        response = self._execute(
            self._values.update(
                spreadsheetId=self.spreadsheet_id,
                range=self._a1(range_name),
                valueInputOption="USER_ENTERED",
                body={"values": self._as_rows(values)},
            )
        )
        return response

    def delete(self, range_name: str) -> dict:
        """Очищает значения в диапазоне, не удаляя сами строки."""
        if not range_name:
            raise GoogleSheetError("Для delete нужен диапазон ячеек.")
        response = self._execute(
            self._values.clear(
                spreadsheetId=self.spreadsheet_id,
                range=self._a1(range_name),
                body={},
            )
        )
        return response

    def delete_rows(self, start_row: int, count: int = 1) -> dict:
        """Удаляет строки листа. Номер строки считается с 1, как в таблице."""
        if start_row < 1:
            raise GoogleSheetError("Номер строки должен быть больше нуля.")
        if count < 1:
            raise GoogleSheetError("Количество строк должно быть больше нуля.")

        response = self._execute(
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [
                        {
                            "deleteDimension": {
                                "range": {
                                    "sheetId": self._worksheet_id(),
                                    "dimension": "ROWS",
                                    "startIndex": start_row - 1,
                                    "endIndex": start_row - 1 + count,
                                }
                            }
                        }
                    ]
                },
            )
        )
        return response

    def add_worksheet(self, title: str) -> int:
        """Создаёт новый лист и переключает клиент на него."""
        if not title or not title.strip():
            raise GoogleSheetError("Не указано имя нового листа.")
        response = self._execute(
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [
                        {"addSheet": {"properties": {"title": title.strip()}}}
                    ]
                },
            )
        )
        properties = response["replies"][0]["addSheet"]["properties"]
        self.sheet_name = properties["title"]
        self._sheet_id = properties["sheetId"]
        return self._sheet_id

    def batch_update(self, requests: list[dict]) -> dict:
        """Отправляет пакет запросов Sheets API: формат, объединение, ширину колонок."""
        if not requests:
            raise GoogleSheetError("Список запросов пуст.")
        return self._execute(
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            )
        )

    @property
    def worksheet_id(self) -> int:
        return self._worksheet_id()

    def _worksheet(self) -> dict:
        if self._sheet_id is not None and self.sheet_name:
            return {"sheetId": self._sheet_id, "title": self.sheet_name}

        metadata = self._execute(
            self._service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(sheetId,title)",
            )
        )
        sheets = [
            item["properties"]
            for item in metadata.get("sheets", [])
            if "properties" in item
        ]
        if not sheets:
            raise GoogleSheetError("В таблице нет листов.")

        if self.sheet_name:
            for properties in sheets:
                if properties.get("title") == self.sheet_name:
                    self._sheet_id = properties["sheetId"]
                    return properties
            available = ", ".join(item.get("title", "") for item in sheets)
            raise GoogleSheetError(
                f"Лист «{self.sheet_name}» не найден. Доступные листы: {available}."
            )

        properties = sheets[0]
        self.sheet_name = properties["title"]
        self._sheet_id = properties["sheetId"]
        return properties

    def _worksheet_id(self) -> int:
        return self._worksheet()["sheetId"]

    def _a1(self, cell_range: str | None = None) -> str:
        title = str(self._worksheet()["title"]).replace("'", "''")
        if not cell_range:
            return f"'{title}'"
        if "!" in cell_range:
            return cell_range
        return f"'{title}'!{cell_range}"

    @staticmethod
    def _as_rows(values: list) -> list[list]:
        if not values:
            raise GoogleSheetError("Нет данных для записи.")
        if isinstance(values[0], (list, tuple)):
            return [list(row) for row in values]
        return [list(values)]

    @staticmethod
    def _execute(request):
        try:
            return request.execute()
        except HttpError as error:
            details = getattr(error, "reason", None) or str(error)
            raise GoogleSheetError(details) from error
        except (TimeoutError, OSError) as error:
            raise GoogleSheetError(
                "Нет соединения с Google API. Проверьте интернет или VPN."
            ) from error


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    sheet_name = os.getenv("SHEET_NAME", "").strip() or None
    if not spreadsheet_id:
        raise SystemExit(
            "Укажите SPREADSHEET_ID в файле .env. "
            "Это часть ссылки между /d/ и /edit."
        )

    sheet = GoogleSheet(spreadsheet_id, sheet_name=sheet_name)
    rows = sheet.read()
    print(f"Лист: {sheet.sheet_name}")
    if not rows:
        print("Таблица пуста.")
        return

    for index, row in enumerate(rows, start=1):
        print(f"{index}: {row}")


if __name__ == "__main__":
    main()
