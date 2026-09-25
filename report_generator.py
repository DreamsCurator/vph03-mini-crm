"""Окно генерации случайного отчёта и записи его в новый лист Google Таблицы."""

from __future__ import annotations

import os
import random
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from dotenv import load_dotenv

from google_sheet import GoogleSheet, GoogleSheetError

COLUMNS = ("№", "Категория", "Операция", "Оплата", "Сумма", "Комментарий")
CATEGORIES = (
    "Обычная стрижка",
    "Стрижка старшего мастера",
    "Окрашивание",
    "Укладка",
    "Продажа товара",
    "Закупка расходников",
)
INCOME_CATEGORIES = CATEGORIES[:5]
COMMENTS = (
    "Постоянный клиент",
    "Новая запись",
    "Акция недели",
    "Повторный визит",
    "Срочный заказ",
    "Плановая закупка",
)
COLUMN_WIDTHS = (50, 240, 110, 120, 120, 220)


def rgb(hex_color: str) -> dict[str, float]:
    color = hex_color.lstrip("#")
    return {
        "red": int(color[0:2], 16) / 255,
        "green": int(color[2:4], 16) / 255,
        "blue": int(color[4:6], 16) / 255,
    }


def build_report(row_count: int) -> tuple[list[list], str]:
    """Собирает строки отчёта и имя нового листа."""
    created = datetime.now()
    sheet_title = created.strftime("Отчет %Y-%m-%d %H-%M-%S")
    rows: list[list] = [
        ["Отчёт по операциям", "", "", "", "", ""],
        [f"Сформирован {created.strftime('%d.%m.%Y %H:%M')}", "", "", "", "", ""],
        list(COLUMNS),
    ]
    for number in range(1, row_count + 1):
        operation = random.choice(("Доход", "Расход"))
        if operation == "Доход":
            category = random.choice(INCOME_CATEGORIES)
            amount = random.randint(8, 70) * 100
        else:
            category = "Закупка расходников"
            amount = random.randint(2, 25) * 100
        rows.append(
            [
                number,
                category,
                operation,
                random.choice(("Наличные", "Безнал")),
                amount,
                random.choice(COMMENTS),
            ]
        )
    total_row = len(rows) + 1
    rows.append(["Итого", "", "", "", f"=SUM(E4:E{total_row - 1})", ""])
    return rows, sheet_title


def _range(sheet_id: int, start_row: int, end_row: int, start_col: int, end_col: int) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_col,
        "endColumnIndex": end_col,
    }


def _repeat(
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    cell_format: dict,
    fields: str,
) -> dict:
    return {
        "repeatCell": {
            "range": _range(sheet_id, start_row, end_row, start_col, end_col),
            "cell": {"userEnteredFormat": cell_format},
            "fields": fields,
        }
    }


def formatting_requests(sheet_id: int, rows: list[list]) -> list[dict]:
    """Цвета, шрифты, границы, ширина колонок и объединение заголовка."""
    last_row = len(rows)
    header_row = 2
    data_start = 3
    total_row = last_row - 1
    requests: list[dict] = [
        {
            "mergeCells": {
                "range": _range(sheet_id, 0, 1, 0, 6),
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "mergeCells": {
                "range": _range(sheet_id, 1, 2, 0, 6),
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "mergeCells": {
                "range": _range(sheet_id, total_row, last_row, 0, 4),
                "mergeType": "MERGE_ALL",
            }
        },
        _repeat(
            sheet_id,
            0,
            1,
            0,
            6,
            {
                "backgroundColor": rgb("#1F4E79"),
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {
                    "bold": True,
                    "fontSize": 18,
                    "foregroundColor": rgb("#FFFFFF"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        ),
        _repeat(
            sheet_id,
            1,
            2,
            0,
            6,
            {
                "backgroundColor": rgb("#D6E3F0"),
                "horizontalAlignment": "CENTER",
                "textFormat": {
                    "italic": True,
                    "fontSize": 11,
                    "foregroundColor": rgb("#1F4E79"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        ),
        _repeat(
            sheet_id,
            header_row,
            data_start,
            0,
            6,
            {
                "backgroundColor": rgb("#2E75B6"),
                "horizontalAlignment": "CENTER",
                "textFormat": {
                    "bold": True,
                    "fontSize": 11,
                    "foregroundColor": rgb("#FFFFFF"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        ),
        _repeat(
            sheet_id,
            total_row,
            last_row,
            0,
            6,
            {
                "backgroundColor": rgb("#FFF2CC"),
                "horizontalAlignment": "RIGHT",
                "textFormat": {
                    "bold": True,
                    "fontSize": 12,
                    "foregroundColor": rgb("#1F4E79"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        ),
        _repeat(
            sheet_id,
            data_start,
            total_row,
            4,
            5,
            {"numberFormat": {"type": "NUMBER", "pattern": '#,##0" ₽"'}},
            "userEnteredFormat.numberFormat",
        ),
        _repeat(
            sheet_id,
            total_row,
            last_row,
            4,
            5,
            {"numberFormat": {"type": "NUMBER", "pattern": '#,##0" ₽"'}},
            "userEnteredFormat.numberFormat",
        ),
        {
            "updateBorders": {
                "range": _range(sheet_id, header_row, last_row, 0, 6),
                "top": {"style": "SOLID", "color": rgb("#8FA8C4")},
                "bottom": {"style": "SOLID", "color": rgb("#8FA8C4")},
                "left": {"style": "SOLID", "color": rgb("#8FA8C4")},
                "right": {"style": "SOLID", "color": rgb("#8FA8C4")},
                "innerHorizontal": {"style": "SOLID", "color": rgb("#D0D7E2")},
                "innerVertical": {"style": "SOLID", "color": rgb("#D0D7E2")},
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 3},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 36},
                "fields": "pixelSize",
            }
        },
    ]
    for index, width in enumerate(COLUMN_WIDTHS):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": index,
                        "endIndex": index + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )
    for offset, row in enumerate(rows[data_start:total_row]):
        background = "#E2F0D9" if row[2] == "Доход" else "#FCE4D6"
        requests.append(
            _repeat(
                sheet_id,
                data_start + offset,
                data_start + offset + 1,
                0,
                6,
                {
                    "backgroundColor": rgb(background),
                    "textFormat": {"fontFamily": "Arial", "fontSize": 10},
                    "verticalAlignment": "MIDDLE",
                },
                "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
            )
        )
    return requests


def publish_report(row_count: int) -> str:
    """Создаёт новый лист, записывает отчёт и применяет оформление."""
    load_dotenv(Path(__file__).with_name(".env"))
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise GoogleSheetError("Укажите SPREADSHEET_ID в файле .env.")

    rows, sheet_title = build_report(row_count)
    sheet = GoogleSheet(spreadsheet_id)
    sheet.add_worksheet(sheet_title)
    sheet.update("A1", rows)
    sheet.batch_update(formatting_requests(sheet.worksheet_id, rows))
    return sheet_title


class ReportApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Генератор отчёта")
        self.resizable(False, False)
        self.configure(bg="#F4F7FB")

        frame = ttk.Frame(self, padding=20)
        frame.grid(row=0, column=0)

        ttk.Label(frame, text="Случайный отчёт в Google Таблицу").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(frame, text="Число строк").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.row_count = tk.IntVar(value=8)
        ttk.Spinbox(
            frame,
            from_=3,
            to=30,
            textvariable=self.row_count,
            width=8,
        ).grid(row=1, column=1, sticky="w", pady=(12, 0))

        self.generate_button = ttk.Button(
            frame,
            text="Сгенерировать отчёт",
            command=self._start,
        )
        self.generate_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 8))

        self.status = ttk.Label(frame, text="Отчёт появится на новом листе.")
        self.status.grid(row=3, column=0, columnspan=2, sticky="w")

    def _start(self) -> None:
        try:
            count = int(self.row_count.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Ошибка", "Укажите число строк от 3 до 30.")
            return
        if not 3 <= count <= 30:
            messagebox.showerror("Ошибка", "Укажите число строк от 3 до 30.")
            return

        self.generate_button.state(["disabled"])
        self.status.configure(text="Записываю отчёт в таблицу...")
        threading.Thread(target=self._publish, args=(count,), daemon=True).start()

    def _publish(self, count: int) -> None:
        try:
            title = publish_report(count)
        except (GoogleSheetError, OSError) as error:
            self.after(0, self._failed, str(error))
            return
        self.after(0, self._done, title)

    def _done(self, title: str) -> None:
        self.generate_button.state(["!disabled"])
        self.status.configure(text=f"Готово. Лист «{title}».")

    def _failed(self, message: str) -> None:
        self.generate_button.state(["!disabled"])
        self.status.configure(text="Не удалось записать отчёт.")
        messagebox.showerror("Ошибка", message)


def main() -> None:
    ReportApp().mainloop()


if __name__ == "__main__":
    main()
