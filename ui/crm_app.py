"""Окно мини-CRM на Tkinter.

Само в базу не лезет: все действия уходят HTTP-запросами на локальный бэкенд.
Сначала запустите сервер, затем это окно:

    python backend/crm.py
    python ui/crm_app.py
"""

from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

_INTEGRATIONS = Path(__file__).resolve().parent.parent / "integrations"
if str(_INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS))

from google_drive import GoogleDrive, GoogleDriveError
from google_sheet import GoogleSheet, GoogleSheetError

BASE_URL = "http://127.0.0.1:8000"

CLIENT_STATUSES = (("активный", "active"), ("архив", "archived"))
DEAL_STATUSES = (
    ("новая", "new"),
    ("в работе", "in_progress"),
    ("успешная", "won"),
    ("проигранная", "lost"),
)
CLIENT_STATUS_LABEL = {code: label for label, code in CLIENT_STATUSES}
DEAL_STATUS_LABEL = {code: label for label, code in DEAL_STATUSES}
NONE_CLIENT = "без клиента"
NONE_DEAL = "без сделки"
GOOGLE_SETTINGS_FILE = Path(__file__).with_name("google_settings.txt")
GOOGLE_SETTING_KEYS = ("credentials_path", "client_secrets_path", "folder_id")
_DEFAULT_CREDENTIALS = _INTEGRATIONS / "zerocoderlessons-480509-df826ffcdec9.json"


class ApiError(Exception):
    """Ответ сервера с ошибкой или отсутствие связи."""


class Api:
    """Тонкий клиент локального FastAPI."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            pairs = []
            for key, value in query.items():
                if value is None or value == "":
                    continue
                if isinstance(value, bool):
                    text = "true" if value else "false"
                else:
                    text = str(value)
                pairs.append((key, text))
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            raise ApiError(_error_text(raw, error.code)) from error
        except urllib.error.URLError as error:
            raise ApiError(
                "Сервер не отвечает. Запустите его командой: python backend/crm.py"
            ) from error

        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def get(self, path: str, *, query: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, query=query)


def _error_text(raw: str, status: int) -> str:
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return raw or f"Ошибка сервера ({status})"
    detail = payload.get("detail", raw)
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts) or f"Ошибка сервера ({status})"
    return str(detail or f"Ошибка сервера ({status})")


def _optional(value: str) -> str | None:
    text = value.strip()
    return text or None


def _amount(value: str) -> float | None:
    text = value.strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as error:
        raise ValueError("Сумма должна быть числом") from error
    if number < 0:
        raise ValueError("Сумма не может быть отрицательной")
    return number


def _money(value: Any) -> str:
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def _when(value: Any) -> str:
    if not value:
        return ""
    return str(value).replace("T", " ")[:16]


def _choice_code(label: str, options: tuple[tuple[str, str], ...], field: str) -> str:
    for title, code in options:
        if title == label:
            return code
    raise ValueError(f"Выберите {field}")


class RecordDialog(tk.Toplevel):
    """Модальное окно с полями записи."""

    def __init__(self, master: tk.Misc, title: str, on_save: Callable[[dict[str, Any]], None]) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_save = on_save
        self.saved = False
        self.form = ttk.Frame(self, padding=16)
        self.form.grid(row=0, column=0, sticky="nsew")
        buttons = ttk.Frame(self, padding=(16, 0, 16, 16))
        buttons.grid(row=1, column=0, sticky="e")
        ttk.Button(buttons, text="Сохранить", command=self._save).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Отмена", command=self.destroy).grid(row=0, column=1)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def collect(self) -> dict[str, Any]:
        raise NotImplementedError

    def _save(self) -> None:
        try:
            body = self.collect()
            self.on_save(body)
        except ValueError as error:
            messagebox.showerror("Проверьте поля", str(error), parent=self)
            return
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self.saved = True
        self.destroy()

    def _row(self, row: int, caption: str, widget: tk.Widget) -> None:
        ttk.Label(self.form, text=caption).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        self.form.columnconfigure(1, weight=1)


class ClientDialog(RecordDialog):
    def __init__(
        self,
        master: tk.Misc,
        on_save: Callable[[dict[str, Any]], None],
        record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(master, "Клиент" if record else "Новый клиент", on_save)
        record = record or {}
        self.name = ttk.Entry(self.form, width=42)
        self.phone = ttk.Entry(self.form, width=42)
        self.email = ttk.Entry(self.form, width=42)
        self.company = ttk.Entry(self.form, width=42)
        self.notes = tk.Text(self.form, width=42, height=4, wrap="word")
        self.status = ttk.Combobox(
            self.form,
            width=39,
            state="readonly",
            values=[label for label, _code in CLIENT_STATUSES],
        )
        self.name.insert(0, record.get("name") or "")
        self.phone.insert(0, record.get("phone") or "")
        self.email.insert(0, record.get("email") or "")
        self.company.insert(0, record.get("company") or "")
        self.notes.insert("1.0", record.get("notes") or "")
        current = CLIENT_STATUS_LABEL.get(record.get("status") or "active", "активный")
        self.status.set(current)
        self._row(0, "Имя", self.name)
        self._row(1, "Телефон", self.phone)
        self._row(2, "Email", self.email)
        self._row(3, "Компания", self.company)
        self._row(4, "Заметки", self.notes)
        self._row(5, "Статус", self.status)
        self.name.focus_set()

    def collect(self) -> dict[str, Any]:
        name = self.name.get().strip()
        if not name:
            raise ValueError("Укажите имя клиента")
        return {
            "name": name,
            "phone": _optional(self.phone.get()),
            "email": _optional(self.email.get()),
            "company": _optional(self.company.get()),
            "notes": _optional(self.notes.get("1.0", "end")),
            "status": _choice_code(self.status.get(), CLIENT_STATUSES, "статус"),
        }


class DealDialog(RecordDialog):
    def __init__(
        self,
        master: tk.Misc,
        clients: list[dict[str, Any]],
        on_save: Callable[[dict[str, Any]], None],
        record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(master, "Сделка" if record else "Новая сделка", on_save)
        record = record or {}
        self.client_ids = {NONE_CLIENT: None}
        labels = [NONE_CLIENT]
        for client in clients:
            label = f"{client['name']} (#{client['id']})"
            self.client_ids[label] = client["id"]
            labels.append(label)
        self.title_entry = ttk.Entry(self.form, width=42)
        self.amount = ttk.Entry(self.form, width=42)
        self.status = ttk.Combobox(
            self.form,
            width=39,
            state="readonly",
            values=[label for label, _code in DEAL_STATUSES],
        )
        self.client = ttk.Combobox(self.form, width=39, state="readonly", values=labels)
        self.notes = tk.Text(self.form, width=42, height=4, wrap="word")
        self.title_entry.insert(0, record.get("title") or "")
        if record.get("amount") is not None:
            self.amount.insert(0, _money(record["amount"]))
        self.status.set(DEAL_STATUS_LABEL.get(record.get("status") or "new", "новая"))
        selected = NONE_CLIENT
        if record.get("client_id"):
            for label, client_id in self.client_ids.items():
                if client_id == record["client_id"]:
                    selected = label
                    break
            if selected == NONE_CLIENT:
                selected = f"{record.get('client_name') or 'Клиент'} (#{record['client_id']})"
                self.client_ids[selected] = record["client_id"]
                labels.append(selected)
                self.client.configure(values=labels)
        self.client.set(selected)
        self.notes.insert("1.0", record.get("notes") or "")
        self._row(0, "Название", self.title_entry)
        self._row(1, "Сумма", self.amount)
        self._row(2, "Статус", self.status)
        self._row(3, "Клиент", self.client)
        self._row(4, "Заметки", self.notes)
        self.title_entry.focus_set()

    def collect(self) -> dict[str, Any]:
        title = self.title_entry.get().strip()
        if not title:
            raise ValueError("Укажите название сделки")
        if self.client.get() not in self.client_ids:
            raise ValueError("Выберите клиента или оставьте сделку без клиента")
        return {
            "title": title,
            "amount": _amount(self.amount.get()),
            "status": _choice_code(self.status.get(), DEAL_STATUSES, "статус"),
            "client_id": self.client_ids[self.client.get()],
            "notes": _optional(self.notes.get("1.0", "end")),
        }


class TaskDialog(RecordDialog):
    def __init__(
        self,
        master: tk.Misc,
        clients: list[dict[str, Any]],
        deals: list[dict[str, Any]],
        on_save: Callable[[dict[str, Any]], None],
        record: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(master, "Задача" if record else "Новая задача", on_save)
        record = record or {}
        self.client_ids = {NONE_CLIENT: None}
        client_labels = [NONE_CLIENT]
        for client in clients:
            label = f"{client['name']} (#{client['id']})"
            self.client_ids[label] = client["id"]
            client_labels.append(label)
        self.deal_ids = {NONE_DEAL: None}
        deal_labels = [NONE_DEAL]
        for deal in deals:
            label = f"{deal['title']} (#{deal['id']})"
            self.deal_ids[label] = deal["id"]
            deal_labels.append(label)

        self.title_entry = ttk.Entry(self.form, width=42)
        self.due_at = ttk.Entry(self.form, width=42)
        self.client = ttk.Combobox(self.form, width=39, state="readonly", values=client_labels)
        self.deal = ttk.Combobox(self.form, width=39, state="readonly", values=deal_labels)
        self.done = tk.BooleanVar(value=bool(record.get("is_done")))
        self.done_check = ttk.Checkbutton(self.form, text="Выполнена", variable=self.done)
        self.notes = tk.Text(self.form, width=42, height=4, wrap="word")
        self.title_entry.insert(0, record.get("title") or "")
        self.due_at.insert(0, record.get("due_at") or "")
        self.notes.insert("1.0", record.get("notes") or "")
        self.client.set(
            self._known_label(
                self.client,
                self.client_ids,
                client_labels,
                record.get("client_id"),
                record.get("client_name") or "Клиент",
                NONE_CLIENT,
            )
        )
        self.deal.set(
            self._known_label(
                self.deal,
                self.deal_ids,
                deal_labels,
                record.get("deal_id"),
                record.get("deal_title") or "Сделка",
                NONE_DEAL,
            )
        )
        self._row(0, "Название", self.title_entry)
        self._row(1, "Срок", self.due_at)
        self._row(2, "Клиент", self.client)
        self._row(3, "Сделка", self.deal)
        self._row(4, "Состояние", self.done_check)
        self._row(5, "Заметки", self.notes)
        ttk.Label(self.form, text="Дата в формате 2026-10-03T10:00:00, можно пустой").grid(
            row=6, column=1, sticky="w"
        )
        self.title_entry.focus_set()

    def _known_label(
        self,
        widget: ttk.Combobox,
        mapping: dict[str, int | None],
        labels: list[str],
        row_id: int | None,
        name: str,
        empty: str,
    ) -> str:
        if not row_id:
            return empty
        for label, value in mapping.items():
            if value == row_id:
                return label
        label = f"{name} (#{row_id})"
        mapping[label] = row_id
        labels.append(label)
        widget.configure(values=labels)
        return label

    def collect(self) -> dict[str, Any]:
        title = self.title_entry.get().strip()
        if not title:
            raise ValueError("Укажите название задачи")
        if self.client.get() not in self.client_ids:
            raise ValueError("Выберите клиента или оставьте задачу без клиента")
        if self.deal.get() not in self.deal_ids:
            raise ValueError("Выберите сделку или оставьте задачу без сделки")
        return {
            "title": title,
            "due_at": _optional(self.due_at.get()),
            "client_id": self.client_ids[self.client.get()],
            "deal_id": self.deal_ids[self.deal.get()],
            "is_done": bool(self.done.get()),
            "notes": _optional(self.notes.get("1.0", "end")),
        }


def _table(
    parent: ttk.Frame,
    columns: tuple[str, ...],
    headings: tuple[str, ...],
    widths: tuple[int, ...],
) -> tuple[ttk.Frame, ttk.Treeview]:
    frame = ttk.Frame(parent)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    for key, title, width in zip(columns, headings, widths, strict=True):
        tree.heading(key, text=title)
        tree.column(key, width=width, stretch=True, anchor="w")
    tree.tag_configure("muted", foreground="#667085")
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    return frame, tree


def _clear(tree: ttk.Treeview) -> None:
    children = tree.get_children()
    if children:
        tree.delete(*children)


def _default_client_secrets() -> str:
    matches = sorted(_INTEGRATIONS.glob("client_secret*.json"))
    return str(matches[0]) if matches else ""


def load_google_settings() -> dict[str, str]:
    settings = {key: "" for key in GOOGLE_SETTING_KEYS}
    if GOOGLE_SETTINGS_FILE.is_file():
        for line in GOOGLE_SETTINGS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in settings:
                settings[key] = value.strip()
    if not settings["credentials_path"] and _DEFAULT_CREDENTIALS.is_file():
        settings["credentials_path"] = str(_DEFAULT_CREDENTIALS)
    if not settings["client_secrets_path"]:
        settings["client_secrets_path"] = _default_client_secrets()
    return settings


def save_google_settings(settings: dict[str, str]) -> None:
    lines = [f"{key}={settings.get(key, '').strip()}" for key in GOOGLE_SETTING_KEYS]
    GOOGLE_SETTINGS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _folder_id_from_text(text: str) -> str:
    """Берёт id папки из буфера: либо голый id, либо ссылку Google Диска."""
    value = text.strip().strip('"').strip("'")
    if "/folders/" in value:
        tail = value.split("/folders/", 1)[1]
        return tail.split("?", 1)[0].split("/", 1)[0].strip()
    return value


def _rgb(hex_color: str) -> dict[str, float]:
    color = hex_color.lstrip("#")
    return {
        "red": int(color[0:2], 16) / 255,
        "green": int(color[2:4], 16) / 255,
        "blue": int(color[4:6], 16) / 255,
    }


def _sheet_range(sheet_id: int, start_row: int, end_row: int, start_col: int, end_col: int) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_col,
        "endColumnIndex": end_col,
    }


def _repeat_cell(
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
            "range": _sheet_range(sheet_id, start_row, end_row, start_col, end_col),
            "cell": {"userEnteredFormat": cell_format},
            "fields": fields,
        }
    }


def _table_matrix(tree: ttk.Treeview) -> tuple[list[str], list[list[Any]]]:
    headings = [str(tree.heading(column)["text"]) for column in tree["columns"]]
    rows: list[list[Any]] = []
    for iid in tree.get_children():
        values = list(tree.item(iid)["values"])
        if len(values) < len(headings):
            values.extend([""] * (len(headings) - len(values)))
        rows.append(values[: len(headings)])
    return headings, rows


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _cell(row: list[Any], headings: list[str], name: str) -> str:
    if name not in headings:
        return ""
    value = row[headings.index(name)]
    return "" if value is None else str(value)


def _prepare_rows(headings: list[str], rows: list[list[Any]]) -> list[list[Any]]:
    prepared: list[list[Any]] = []
    for row in rows:
        line: list[Any] = []
        for heading, value in zip(headings, row, strict=True):
            if heading == "Сумма":
                number = _as_number(value)
                line.append("" if number is None else number)
            else:
                line.append("" if value is None else value)
        prepared.append(line)
    return prepared


def _analysis(kind: str, headings: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "В таблице нет строк."
    if kind == "clients":
        active = sum(1 for row in rows if _cell(row, headings, "Статус") == "активный")
        archived = sum(1 for row in rows if _cell(row, headings, "Статус") == "архив")
        return f"Всего {len(rows)}. Активных {active}, в архиве {archived}."
    if kind == "deals":
        counts: dict[str, int] = {}
        total = 0.0
        for row in rows:
            status = _cell(row, headings, "Статус") or "без статуса"
            counts[status] = counts.get(status, 0) + 1
            amount = _as_number(_cell(row, headings, "Сумма"))
            if amount is not None:
                total += amount
        parts = [f"Всего {len(rows)}", f"сумма {_money(total)}"]
        parts.extend(f"{status}: {count}" for status, count in counts.items())
        return ". ".join(parts) + "."
    if kind == "tasks":
        done = sum(1 for row in rows if _cell(row, headings, "Готово") == "да")
        return f"Всего {len(rows)}. Выполнено {done}, открыто {len(rows) - done}."
    return f"Всего строк: {len(rows)}."


def build_report(
    title: str,
    headings: list[str],
    rows: list[list[Any]],
    kind: str,
) -> list[list[Any]]:
    """Собирает массив отчёта: заголовок, итог анализа и строки таблицы."""
    width = max(len(headings), 1)
    prepared = _prepare_rows(headings, rows) if headings else []
    summary = (
        f"Сформирован {datetime.now().strftime('%d.%m.%Y %H:%M')}. "
        f"{_analysis(kind, headings, prepared)}"
    )
    blank = [""] * (width - 1)
    report = [
        [title, *blank],
        [summary, *blank],
        list(headings) if headings else ["Нет колонок"],
    ]
    for row in prepared:
        report.append(list(row))
    return report


def _row_color(headings: list[str], row: list[Any]) -> str | None:
    for name in ("Статус", "Готово"):
        text = _cell(row, headings, name)
        if not text:
            continue
        colors = {
            "активный": "#E2F0D9",
            "архив": "#F2F2F2",
            "новая": "#D6E3F0",
            "в работе": "#FFF2CC",
            "успешная": "#E2F0D9",
            "проигранная": "#FCE4D6",
            "да": "#E2F0D9",
            "нет": "#FFF2CC",
        }
        return colors.get(text)
    return None


def report_formatting(sheet_id: int, rows: list[list[Any]], headings: list[str]) -> list[dict]:
    """Пакет оформления для GoogleSheet.batch_update."""
    width = max(len(headings), 1)
    last_row = len(rows)
    header_row = 2
    data_start = 3
    requests: list[dict] = [
        {
            "mergeCells": {
                "range": _sheet_range(sheet_id, 0, 1, 0, width),
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "mergeCells": {
                "range": _sheet_range(sheet_id, 1, 2, 0, width),
                "mergeType": "MERGE_ALL",
            }
        },
        _repeat_cell(
            sheet_id,
            0,
            1,
            0,
            width,
            {
                "backgroundColor": _rgb("#1F4E79"),
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {
                    "bold": True,
                    "fontSize": 16,
                    "foregroundColor": _rgb("#FFFFFF"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        ),
        _repeat_cell(
            sheet_id,
            1,
            2,
            0,
            width,
            {
                "backgroundColor": _rgb("#D6E3F0"),
                "horizontalAlignment": "LEFT",
                "textFormat": {
                    "italic": True,
                    "fontSize": 11,
                    "foregroundColor": _rgb("#1F4E79"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        ),
        _repeat_cell(
            sheet_id,
            header_row,
            data_start,
            0,
            width,
            {
                "backgroundColor": _rgb("#2E75B6"),
                "horizontalAlignment": "CENTER",
                "textFormat": {
                    "bold": True,
                    "fontSize": 11,
                    "foregroundColor": _rgb("#FFFFFF"),
                    "fontFamily": "Arial",
                },
            },
            "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
        ),
        {
            "updateBorders": {
                "range": _sheet_range(sheet_id, header_row, last_row, 0, width),
                "top": {"style": "SOLID", "color": _rgb("#8FA8C4")},
                "bottom": {"style": "SOLID", "color": _rgb("#8FA8C4")},
                "left": {"style": "SOLID", "color": _rgb("#8FA8C4")},
                "right": {"style": "SOLID", "color": _rgb("#8FA8C4")},
                "innerHorizontal": {"style": "SOLID", "color": _rgb("#D0D7E2")},
                "innerVertical": {"style": "SOLID", "color": _rgb("#D0D7E2")},
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
    widths = {
        "Имя": 220,
        "Телефон": 160,
        "Email": 240,
        "Компания": 200,
        "Статус": 140,
        "Название": 280,
        "Клиент": 220,
        "Сумма": 140,
        "Задача": 280,
        "Срок": 170,
        "Сделка": 220,
        "Готово": 110,
    }
    for index, heading in enumerate(headings):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": index,
                        "endIndex": index + 1,
                    },
                    "properties": {"pixelSize": widths.get(heading, 160)},
                    "fields": "pixelSize",
                }
            }
        )
    if "Сумма" in headings and last_row > data_start:
        column = headings.index("Сумма")
        requests.append(
            _repeat_cell(
                sheet_id,
                data_start,
                last_row,
                column,
                column + 1,
                {"numberFormat": {"type": "NUMBER", "pattern": '#,##0.00" ₽"'}},
                "userEnteredFormat.numberFormat",
            )
        )
    for offset, row in enumerate(rows[data_start:]):
        color = _row_color(headings, row)
        if not color:
            continue
        requests.append(
            _repeat_cell(
                sheet_id,
                data_start + offset,
                data_start + offset + 1,
                0,
                width,
                {
                    "backgroundColor": _rgb(color),
                    "textFormat": {"fontFamily": "Arial", "fontSize": 10},
                    "verticalAlignment": "MIDDLE",
                },
                "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
            )
        )
    return requests


class CrmApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Мини-CRM")
        self.geometry("1080x680")
        self.minsize(860, 520)
        self.api = Api()
        self.google_settings = load_google_settings()
        self._exporting = False
        self.status = tk.StringVar(value="Проверяю сервер...")
        self._apply_style()
        self._build()
        self.after(100, self.refresh_all)

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(12, 6))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 0))
        self.notebook = notebook

        self.clients_tab = self._tab("Клиенты")
        self.deals_tab = self._tab("Сделки")
        self.tasks_tab = self._tab("Задачи")
        self.search_tab = self._tab("Поиск")

        self.client_query = tk.StringVar()
        self.client_filter = tk.StringVar(value="все")
        self._toolbar(
            self.clients_tab,
            self.client_query,
            self.client_filter,
            ("все", "активные", "архив"),
            self.reload_clients,
        )
        clients_table, self.clients_tree = _table(
            self.clients_tab,
            ("name", "phone", "email", "company", "status"),
            ("Имя", "Телефон", "Email", "Компания", "Статус"),
            (220, 150, 200, 180, 110),
        )
        clients_table.grid(row=1, column=0, sticky="nsew")
        self._actions(
            self.clients_tab,
            [
                ("Создать", self.create_client),
                ("Изменить", self.edit_client),
                ("В архив", self.archive_client),
                ("Из архива", self.restore_client),
                ("Удалить", self.delete_client),
                ("Выгрузить отчёт", lambda: self.export_table(self.clients_tree, "Клиенты", "clients")),
            ],
        )
        self.clients_tree.bind("<Double-1>", lambda _event: self.edit_client())

        self.deal_query = tk.StringVar()
        self.deal_filter = tk.StringVar(value="все")
        self._toolbar(
            self.deals_tab,
            self.deal_query,
            self.deal_filter,
            ("все", "новая", "в работе", "успешная", "проигранная", "без клиента"),
            self.reload_deals,
        )
        deals_table, self.deals_tree = _table(
            self.deals_tab,
            ("title", "client", "amount", "status"),
            ("Название", "Клиент", "Сумма", "Статус"),
            (280, 240, 120, 140),
        )
        deals_table.grid(row=1, column=0, sticky="nsew")
        self._actions(
            self.deals_tab,
            [
                ("Создать", self.create_deal),
                ("Изменить", self.edit_deal),
                ("Удалить", self.delete_deal),
                ("Выгрузить отчёт", lambda: self.export_table(self.deals_tree, "Сделки", "deals")),
            ],
        )
        self.deals_tree.bind("<Double-1>", lambda _event: self.edit_deal())

        self.task_query = tk.StringVar()
        self.task_filter = tk.StringVar(value="все")
        self._toolbar(
            self.tasks_tab,
            self.task_query,
            self.task_filter,
            ("все", "открытые", "выполненные"),
            self.reload_tasks,
        )
        tasks_table, self.tasks_tree = _table(
            self.tasks_tab,
            ("title", "due", "client", "deal", "done"),
            ("Задача", "Срок", "Клиент", "Сделка", "Готово"),
            (260, 150, 200, 220, 90),
        )
        tasks_table.grid(row=1, column=0, sticky="nsew")
        self._actions(
            self.tasks_tab,
            [
                ("Создать", self.create_task),
                ("Изменить", self.edit_task),
                ("Выполнена", self.complete_task),
                ("Не выполнена", self.reopen_task),
                ("Удалить", self.delete_task),
                ("Выгрузить отчёт", lambda: self.export_table(self.tasks_tree, "Задачи", "tasks")),
            ],
        )
        self.tasks_tree.bind("<Double-1>", lambda _event: self.edit_task())

        search_bar = ttk.Frame(self.search_tab, padding=(0, 0, 0, 8))
        search_bar.grid(row=0, column=0, sticky="ew")
        self.search_query = tk.StringVar()
        ttk.Label(search_bar, text="Текст").grid(row=0, column=0, padx=(0, 8))
        entry = ttk.Entry(search_bar, textvariable=self.search_query, width=40)
        entry.grid(row=0, column=1, padx=(0, 8))
        entry.bind("<Return>", lambda _event: self.run_search())
        ttk.Button(search_bar, text="Найти", command=self.run_search).grid(row=0, column=2)
        ttk.Label(
            search_bar,
            text="Ищет клиентов и сделки на сервере, без учёта регистра",
        ).grid(row=0, column=3, padx=(16, 0))

        panes = ttk.Panedwindow(self.search_tab, orient=tk.VERTICAL)
        panes.grid(row=1, column=0, sticky="nsew")
        clients_pane = ttk.Labelframe(panes, text="Клиенты", padding=8)
        deals_pane = ttk.Labelframe(panes, text="Сделки", padding=8)
        panes.add(clients_pane, weight=1)
        panes.add(deals_pane, weight=1)
        clients_pane.rowconfigure(0, weight=1)
        clients_pane.columnconfigure(0, weight=1)
        deals_pane.rowconfigure(0, weight=1)
        deals_pane.columnconfigure(0, weight=1)
        search_clients_table, self.search_clients = _table(
            clients_pane,
            ("name", "phone", "company", "status"),
            ("Имя", "Телефон", "Компания", "Статус"),
            (240, 160, 200, 120),
        )
        search_clients_table.grid(row=0, column=0, sticky="nsew")
        ttk.Button(
            clients_pane,
            text="Выгрузить отчёт",
            command=lambda: self.export_table(self.search_clients, "Поиск: клиенты", "clients"),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        search_deals_table, self.search_deals = _table(
            deals_pane,
            ("title", "client", "amount", "status"),
            ("Название", "Клиент", "Сумма", "Статус"),
            (280, 220, 120, 140),
        )
        search_deals_table.grid(row=0, column=0, sticky="nsew")
        ttk.Button(
            deals_pane,
            text="Выгрузить отчёт",
            command=lambda: self.export_table(self.search_deals, "Поиск: сделки", "deals"),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.search_clients.bind("<Double-1>", lambda _event: self.edit_client(self.search_clients))
        self.search_deals.bind("<Double-1>", lambda _event: self.edit_deal(self.search_deals))

        bottom = ttk.Frame(self, padding=(12, 8))
        bottom.grid(row=1, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status).grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="Настройки Google", command=self.open_google_settings).grid(
            row=0, column=1, sticky="e"
        )

    def _tab(self, title: str) -> ttk.Frame:
        frame = ttk.Frame(self.notebook, padding=12)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text=title)
        return frame

    def _toolbar(
        self,
        parent: ttk.Frame,
        query: tk.StringVar,
        current: tk.StringVar,
        filters: tuple[str, ...],
        command: Callable[[], None],
    ) -> None:
        bar = ttk.Frame(parent, padding=(0, 0, 0, 8))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="Поиск").grid(row=0, column=0, padx=(0, 8))
        entry = ttk.Entry(bar, textvariable=query, width=28)
        entry.grid(row=0, column=1, padx=(0, 8))
        entry.bind("<Return>", lambda _event: command())
        ttk.Label(bar, text="Фильтр").grid(row=0, column=2, padx=(8, 8))
        box = ttk.Combobox(bar, textvariable=current, values=filters, state="readonly", width=16)
        box.grid(row=0, column=3, padx=(0, 8))
        box.bind("<<ComboboxSelected>>", lambda _event: command())
        ttk.Button(bar, text="Обновить", command=command).grid(row=0, column=4)

    def _actions(self, parent: ttk.Frame, buttons: list[tuple[str, Callable[[], None]]]) -> None:
        bar = ttk.Frame(parent, padding=(0, 8, 0, 0))
        bar.grid(row=2, column=0, sticky="w")
        for index, (title, command) in enumerate(buttons):
            ttk.Button(bar, text=title, command=command).grid(row=0, column=index, padx=(0, 8))

    def refresh_all(self) -> None:
        failed = False
        for action in (self._load_clients, self._load_deals, self._load_tasks):
            try:
                action()
            except ApiError as error:
                self.status.set(str(error))
                failed = True
                break
        if not failed:
            self.status.set(f"Сервер на связи: {BASE_URL}")

    def reload_clients(self) -> None:
        self._guard(self._load_clients)

    def reload_deals(self) -> None:
        self._guard(self._load_deals)

    def reload_tasks(self) -> None:
        self._guard(self._load_tasks)

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except ApiError as error:
            self.status.set(str(error))

    def _load_clients(self) -> None:
        status_filter = {"активные": "active", "архив": "archived"}.get(self.client_filter.get())
        page = self.api.get(
            "/clients",
            query={"q": _optional(self.client_query.get()), "status": status_filter, "limit": 200},
        )
        self._fill_clients(self.clients_tree, page["items"])
        self.status.set(f"Клиенты: {page['total']}")

    def _load_deals(self) -> None:
        chosen = self.deal_filter.get()
        status_code = {label: code for label, code in DEAL_STATUSES}.get(chosen)
        page = self.api.get(
            "/deals",
            query={
                "q": _optional(self.deal_query.get()),
                "status": status_code,
                "unassigned": True if chosen == "без клиента" else None,
                "limit": 200,
            },
        )
        self._fill_deals(self.deals_tree, page["items"])
        self.status.set(f"Сделки: {page['total']}")

    def _load_tasks(self) -> None:
        done = {"открытые": False, "выполненные": True}.get(self.task_filter.get())
        page = self.api.get(
            "/tasks",
            query={"q": _optional(self.task_query.get()), "is_done": done, "limit": 200},
        )
        self._fill_tasks(self.tasks_tree, page["items"])
        self.status.set(f"Задачи: {page['total']}")

    def run_search(self) -> None:
        text = self.search_query.get().strip()
        if not text:
            messagebox.showinfo("Поиск", "Введите текст для поиска.", parent=self)
            return
        try:
            found = self.api.get("/search", query={"q": text, "limit": 50})
        except ApiError as error:
            self.status.set(str(error))
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self._fill_clients(self.search_clients, found["clients"]["items"])
        self._fill_deals(self.search_deals, found["deals"]["items"])
        self.status.set(
            f"Поиск «{text}»: клиентов {found['clients']['total']}, "
            f"сделок {found['deals']['total']}"
        )

    def _fill_clients(self, tree: ttk.Treeview, items: list[dict[str, Any]]) -> None:
        _clear(tree)
        for item in items:
            tags = ("muted",) if item["status"] == "archived" else ()
            tree.insert(
                "",
                "end",
                iid=str(item["id"]),
                tags=tags,
                values=(
                    item["name"],
                    item.get("phone") or "",
                    item.get("email") or "",
                    item.get("company") or "",
                    CLIENT_STATUS_LABEL.get(item["status"], item["status"]),
                ),
            )

    def _fill_deals(self, tree: ttk.Treeview, items: list[dict[str, Any]]) -> None:
        _clear(tree)
        for item in items:
            tree.insert(
                "",
                "end",
                iid=str(item["id"]),
                values=(
                    item["title"],
                    item.get("client_name") or "",
                    _money(item.get("amount")),
                    DEAL_STATUS_LABEL.get(item["status"], item["status"]),
                ),
            )

    def _fill_tasks(self, tree: ttk.Treeview, items: list[dict[str, Any]]) -> None:
        _clear(tree)
        for item in items:
            tags = ("muted",) if item["is_done"] else ()
            tree.insert(
                "",
                "end",
                iid=str(item["id"]),
                tags=tags,
                values=(
                    item["title"],
                    _when(item.get("due_at")),
                    item.get("client_name") or "",
                    item.get("deal_title") or "",
                    "да" if item["is_done"] else "нет",
                ),
            )

    def _selected(self, tree: ttk.Treeview, title: str) -> int | None:
        chosen = tree.selection()
        if not chosen:
            messagebox.showinfo(title, "Сначала выберите строку в таблице.", parent=self)
            return None
        return int(chosen[0])

    def _clients(self) -> list[dict[str, Any]]:
        return self.api.get("/clients", query={"limit": 200})["items"]

    def _deals(self) -> list[dict[str, Any]]:
        return self.api.get("/deals", query={"limit": 200})["items"]

    def _open(self, dialog: RecordDialog) -> None:
        self.wait_window(dialog)
        if dialog.saved:
            self.refresh_all()

    def create_client(self) -> None:
        self._open(ClientDialog(self, lambda body: self.api.request("POST", "/clients", body=body)))

    def edit_client(self, tree: ttk.Treeview | None = None) -> None:
        row_id = self._selected(tree or self.clients_tree, "Клиент")
        if row_id is None:
            return
        try:
            record = self.api.get(f"/clients/{row_id}")
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self._open(
            ClientDialog(
                self,
                lambda body: self.api.request("PATCH", f"/clients/{row_id}", body=body),
                record,
            )
        )

    def archive_client(self) -> None:
        self._post_client("/archive", "Клиент отправлен в архив.")

    def restore_client(self) -> None:
        self._post_client("/restore", "Клиент возвращён из архива.")

    def _post_client(self, suffix: str, done: str) -> None:
        row_id = self._selected(self.clients_tree, "Клиент")
        if row_id is None:
            return
        try:
            self.api.request("POST", f"/clients/{row_id}{suffix}")
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self.status.set(done)
        self.refresh_all()

    def delete_client(self) -> None:
        row_id = self._selected(self.clients_tree, "Клиент")
        if row_id is None:
            return
        if not messagebox.askyesno(
            "Удалить клиента",
            "Карточка будет удалена. Сделки и задачи останутся, связь с клиентом очистится.",
            parent=self,
        ):
            return
        try:
            self.api.request("DELETE", f"/clients/{row_id}")
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self.refresh_all()

    def create_deal(self) -> None:
        try:
            clients = self._clients()
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self._open(
            DealDialog(self, clients, lambda body: self.api.request("POST", "/deals", body=body))
        )

    def edit_deal(self, tree: ttk.Treeview | None = None) -> None:
        row_id = self._selected(tree or self.deals_tree, "Сделка")
        if row_id is None:
            return
        try:
            record = self.api.get(f"/deals/{row_id}")
            clients = self._clients()
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self._open(
            DealDialog(
                self,
                clients,
                lambda body: self.api.request("PATCH", f"/deals/{row_id}", body=body),
                record,
            )
        )

    def delete_deal(self) -> None:
        row_id = self._selected(self.deals_tree, "Сделка")
        if row_id is None:
            return
        if not messagebox.askyesno(
            "Удалить сделку",
            "Сделка будет удалена. Задачи останутся, связь со сделкой очистится.",
            parent=self,
        ):
            return
        try:
            self.api.request("DELETE", f"/deals/{row_id}")
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self.refresh_all()

    def create_task(self) -> None:
        try:
            clients = self._clients()
            deals = self._deals()
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self._open(
            TaskDialog(
                self,
                clients,
                deals,
                lambda body: self.api.request("POST", "/tasks", body=body),
            )
        )

    def edit_task(self) -> None:
        row_id = self._selected(self.tasks_tree, "Задача")
        if row_id is None:
            return
        try:
            record = self.api.get(f"/tasks/{row_id}")
            clients = self._clients()
            deals = self._deals()
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self._open(
            TaskDialog(
                self,
                clients,
                deals,
                lambda body: self.api.request("PATCH", f"/tasks/{row_id}", body=body),
                record,
            )
        )

    def complete_task(self) -> None:
        self._post_task("/complete", "Задача отмечена выполненной.")

    def reopen_task(self) -> None:
        self._post_task("/reopen", "Задача снова открыта.")

    def _post_task(self, suffix: str, done: str) -> None:
        row_id = self._selected(self.tasks_tree, "Задача")
        if row_id is None:
            return
        try:
            self.api.request("POST", f"/tasks/{row_id}{suffix}")
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self.status.set(done)
        self.refresh_all()

    def delete_task(self) -> None:
        row_id = self._selected(self.tasks_tree, "Задача")
        if row_id is None:
            return
        if not messagebox.askyesno("Удалить задачу", "Удалить выбранную задачу?", parent=self):
            return
        try:
            self.api.request("DELETE", f"/tasks/{row_id}")
        except ApiError as error:
            messagebox.showerror("Сервер", str(error), parent=self)
            return
        self.refresh_all()


    def open_google_settings(self) -> None:
        GoogleSettingsDialog(self)

    def export_table(self, tree: ttk.Treeview, title: str, kind: str) -> None:
        if self._exporting:
            messagebox.showinfo("Отчёт", "Выгрузка уже идёт.", parent=self)
            return
        settings = load_google_settings()
        self.google_settings = settings
        missing = []
        if not settings["credentials_path"]:
            missing.append("JSON сервисного аккаунта")
        if not settings["client_secrets_path"]:
            missing.append("JSON OAuth-клиента")
        if not settings["folder_id"]:
            missing.append("id папки")
        if missing:
            messagebox.showinfo(
                "Настройки Google",
                "Сначала укажите: " + ", ".join(missing) + ".",
                parent=self,
            )
            self.open_google_settings()
            return
        headings, data = _table_matrix(tree)
        rows = build_report(title, headings, data, kind)
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M")
        file_name = f"{title} {stamp}"
        self._exporting = True
        self.status.set("Выгружаю отчёт в Google Таблицу...")
        threading.Thread(
            target=self._publish_report,
            args=(dict(settings), file_name, rows, headings),
            daemon=True,
        ).start()

    def _publish_report(
        self,
        settings: dict[str, str],
        file_name: str,
        rows: list[list[Any]],
        headings: list[str],
    ) -> None:
        try:
            drive = GoogleDrive(
                folder_id=settings["folder_id"],
                credentials_path=settings["credentials_path"],
                client_secrets_path=settings["client_secrets_path"],
            )
            created = drive.create_spreadsheet(file_name)
            spreadsheet_id = created["id"]
            sheet = GoogleSheet(
                spreadsheet_id,
                credentials_path=settings["credentials_path"],
            )
            sheet.update("A1", rows)
            sheet.batch_update(report_formatting(sheet.worksheet_id, rows, headings))
        except (GoogleDriveError, GoogleSheetError, OSError, KeyError) as error:
            self.after(0, self._export_failed, str(error))
            return
        link = created.get("webViewLink") or (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
        )
        self.after(0, self._export_done, file_name, spreadsheet_id, link)

    def _export_done(self, file_name: str, spreadsheet_id: str, link: str) -> None:
        self._exporting = False
        self.status.set(f"Отчёт создан: {file_name}")
        ExportReadyDialog(self, file_name, spreadsheet_id, link)

    def _export_failed(self, message: str) -> None:
        self._exporting = False
        self.status.set("Не удалось выгрузить отчёт.")
        messagebox.showerror("Google", message, parent=self)


class ExportReadyDialog(tk.Toplevel):
    """Сообщение об успешной выгрузке с кнопкой открытия таблицы в браузере."""

    def __init__(self, master: tk.Misc, file_name: str, spreadsheet_id: str, link: str) -> None:
        super().__init__(master)
        self.title("Отчёт готов")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.link = link

        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frame, text=f"Таблица «{file_name}» создана.").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(frame, text=f"id: {spreadsheet_id}").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Label(frame, text=link, wraplength=420).grid(
            row=2, column=0, sticky="w", pady=(4, 12)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="e")
        ttk.Button(buttons, text="Открыть в браузере", command=self._open).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(buttons, text="Закрыть", command=self.destroy).grid(row=0, column=1)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _open(self) -> None:
        webbrowser.open(self.link)
        self.destroy()


class GoogleSettingsDialog(tk.Toplevel):
    """Пути к JSON и id папки. Значения пишутся в текстовый файл."""

    def __init__(self, master: CrmApp) -> None:
        super().__init__(master)
        self.title("Настройки Google")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.settings = dict(load_google_settings())
        form = ttk.Frame(self, padding=16)
        form.grid(row=0, column=0, sticky="nsew")

        ttk.Label(form, text="JSON сервисного аккаунта").grid(row=0, column=0, sticky="w")
        self.credentials_label = ttk.Label(form, text=self._short("credentials_path"), width=52)
        self.credentials_label.grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Button(form, text="Выбрать", command=self._pick_credentials).grid(
            row=1, column=1, sticky="e", padx=(8, 0), pady=(0, 8)
        )

        ttk.Label(form, text="JSON OAuth-клиента").grid(row=2, column=0, sticky="w")
        self.secrets_label = ttk.Label(form, text=self._short("client_secrets_path"), width=52)
        self.secrets_label.grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Button(form, text="Выбрать", command=self._pick_secrets).grid(
            row=3, column=1, sticky="e", padx=(8, 0), pady=(0, 8)
        )

        ttk.Label(form, text="ID папки на Google Диске").grid(row=4, column=0, sticky="w")
        folder_row = ttk.Frame(form)
        folder_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 12))
        self.folder_id = ttk.Entry(folder_row, width=48)
        self.folder_id.grid(row=0, column=0, sticky="ew")
        self.folder_id.insert(0, self.settings["folder_id"])
        ttk.Button(folder_row, text="Вставить", command=self._paste_folder).grid(
            row=0, column=1, padx=(8, 0)
        )
        folder_row.columnconfigure(0, weight=1)

        ttk.Label(
            form,
            text=(
                "Папку откройте на редактирование для сервисного аккаунта.\n"
                "При первой выгрузке откроется вход в личный аккаунт Google."
            ),
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 12))

        buttons = ttk.Frame(form)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Сохранить", command=self._save).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Закрыть", command=self.destroy).grid(row=0, column=1)
        self.bind("<Escape>", lambda _event: self.destroy())

    def _short(self, key: str) -> str:
        value = self.settings.get(key, "")
        if not value:
            return "файл не выбран"
        return value

    def _pick_credentials(self) -> None:
        self._pick("credentials_path", "JSON сервисного аккаунта", self.credentials_label)

    def _pick_secrets(self) -> None:
        self._pick("client_secrets_path", "JSON OAuth-клиента", self.secrets_label)

    def _pick(self, key: str, title: str, label: ttk.Label) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title=title,
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        self.settings[key] = path
        self.settings["folder_id"] = self.folder_id.get().strip()
        label.configure(text=path)
        save_google_settings(self.settings)

    def _paste_folder(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Буфер обмена", "В буфере нет текста.", parent=self)
            return
        folder_id = _folder_id_from_text(text)
        if not folder_id:
            messagebox.showinfo("Буфер обмена", "В буфере нет id папки.", parent=self)
            return
        self.folder_id.delete(0, tk.END)
        self.folder_id.insert(0, folder_id)
        self.settings["folder_id"] = folder_id
        save_google_settings(self.settings)

    def _save(self) -> None:
        self.settings["folder_id"] = _folder_id_from_text(self.folder_id.get())
        self.folder_id.delete(0, tk.END)
        self.folder_id.insert(0, self.settings["folder_id"])
        if not self.settings["credentials_path"]:
            messagebox.showinfo("Настройки Google", "Выберите JSON сервисного аккаунта.", parent=self)
            return
        if not self.settings["client_secrets_path"]:
            messagebox.showinfo("Настройки Google", "Выберите JSON OAuth-клиента.", parent=self)
            return
        if not self.settings["folder_id"]:
            messagebox.showinfo("Настройки Google", "Укажите id рабочей папки.", parent=self)
            return
        save_google_settings(self.settings)
        master = self.master
        if isinstance(master, CrmApp):
            master.google_settings = dict(self.settings)
            master.status.set("Настройки Google сохранены.")
        self.destroy()


def main() -> None:
    CrmApp().mainloop()


if __name__ == "__main__":
    main()
