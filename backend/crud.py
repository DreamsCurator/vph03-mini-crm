"""CRUD-операции мини-CRM на встроенном модуле sqlite3.

При создании экземпляра класс сам создаёт файл базы, если его ещё нет,
и выполняет CREATE TABLE IF NOT EXISTS для каждой модели из models.py.

Поиск идёт через SQL-функцию ilike(значение, шаблон). В SQLite нет
оператора ILIKE, поэтому функция регистрируется на каждом соединении.
Шаблон как у LIKE: «%» — любая последовательность, «_» — один символ.
Сравнение без учёта регистра, в том числе для кириллицы.
"""

from __future__ import annotations

import math
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from models import (
    CLIENT_STATUSES,
    CLIENTS,
    DEAL_STATUSES,
    DEALS,
    TASKS,
    Client,
    Deal,
    Page,
    Table,
    Task,
    schema_statements,
)


class NotFoundError(Exception):
    """Запись или связанная запись не найдена."""


class ValidationError(Exception):
    """Поля запроса не проходят проверку."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _required_text(value: object, field: str, max_length: int = 200) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} должно быть строкой")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} обязательно")
    if len(text) > max_length:
        raise ValidationError(f"{field} длиннее {max_length} символов")
    return text


def _optional_text(
    value: object,
    field: str,
    max_length: int = 5000,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} должно быть строкой")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValidationError(f"{field} длиннее {max_length} символов")
    return text


def _optional_email(value: object) -> str | None:
    text = _optional_text(value, "Email", max_length=200)
    if text is None:
        return None
    if text.count("@") != 1 or text.startswith("@") or text.endswith("@"):
        raise ValidationError("Некорректный email")
    return text


def _choice(value: object, allowed: tuple[str, ...], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        options = ", ".join(allowed)
        raise ValidationError(f"{field} должно быть одним из: {options}")
    return value


def _optional_id(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field} должен быть положительным целым числом")
    return value


def _optional_amount(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Сумма должна быть числом")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError("Сумма должна быть конечным числом")
    if value < 0:
        raise ValidationError("Сумма не может быть отрицательной")
    return float(value)


def _done_flag(value: object) -> int:
    if type(value) is bool or (type(value) is int and value in (0, 1)):
        return int(value)
    raise ValidationError("is_done должен быть логическим значением")


def _optional_datetime(value: object) -> str | None:
    text = _optional_text(value, "Срок", max_length=40)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("Срок должен быть датой в формате ISO 8601") from error
    return parsed.isoformat()


def _bounds(limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValidationError("limit должен быть от 1 до 200")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValidationError("offset должен быть неотрицательным целым")
    return limit, offset


def contains_pattern(query: str) -> str:
    """Шаблон «содержит текст» для ilike. Знаки % и _ экранируются."""
    escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _like_to_regex(pattern: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            parts.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        if char == "%":
            parts.append(".*")
        elif char == "_":
            parts.append(".")
        else:
            parts.append(re.escape(char))
        index += 1
    return "".join(parts)


@lru_cache(maxsize=256)
def _compiled_like(pattern: str) -> re.Pattern[str]:
    return re.compile(_like_to_regex(pattern), re.IGNORECASE | re.DOTALL)


def sql_ilike(value: object, pattern: object) -> int:
    """SQL-функция ilike(значение, шаблон). 1 — совпало, 0 — нет."""
    if value is None or pattern is None:
        return 0
    return 1 if _compiled_like(str(pattern)).fullmatch(str(value)) else 0


def _integrity_message(error: sqlite3.IntegrityError) -> str:
    text = str(error).lower()
    if "foreign key" in text:
        return "Связанная запись не найдена"
    if "check constraint" in text:
        return "Значение не проходит ограничение таблицы"
    return "Нарушено ограничение базы данных"


def _where(clauses: list[str]) -> str:
    if not clauses:
        return ""
    return "WHERE " + " AND ".join(clauses)


def _ilike_clause(expressions: tuple[str, ...]) -> str:
    return "(" + " OR ".join(f"ilike({expression}, ?)" for expression in expressions) + ")"


def _select_columns(table: Table, alias: str | None = None) -> str:
    prefix = f'"{alias}".' if alias else ""
    return ", ".join(f'{prefix}"{column.name}"' for column in table.columns)


_CLIENT_NORMALIZERS = {
    "name": lambda value: _required_text(value, "Имя"),
    "phone": lambda value: _optional_text(value, "Телефон", max_length=50),
    "email": _optional_email,
    "company": lambda value: _optional_text(value, "Компания"),
    "notes": lambda value: _optional_text(value, "Заметки"),
    "status": lambda value: _choice(value, CLIENT_STATUSES, "Статус"),
}

_DEAL_NORMALIZERS = {
    "title": lambda value: _required_text(value, "Название"),
    "client_id": lambda value: _optional_id(value, "Клиент"),
    "amount": _optional_amount,
    "status": lambda value: _choice(value, DEAL_STATUSES, "Статус"),
    "notes": lambda value: _optional_text(value, "Заметки"),
}

_TASK_NORMALIZERS = {
    "title": lambda value: _required_text(value, "Название"),
    "notes": lambda value: _optional_text(value, "Заметки"),
    "due_at": _optional_datetime,
    "is_done": _done_flag,
    "client_id": lambda value: _optional_id(value, "Клиент"),
    "deal_id": lambda value: _optional_id(value, "Сделка"),
}

_DEAL_FROM = """
FROM "deals"
LEFT JOIN "clients" ON "clients"."id" = "deals"."client_id"
"""

_TASK_FROM = """
FROM "tasks"
LEFT JOIN "clients" ON "clients"."id" = "tasks"."client_id"
LEFT JOIN "deals" ON "deals"."id" = "tasks"."deal_id"
"""


class CRMCrud:
    """Создание, чтение, изменение и удаление клиентов, сделок и задач."""

    def __init__(self, db_path: str | Path) -> None:
        if not str(db_path).strip():
            raise ValueError("Укажите файл базы данных")
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_tables()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.create_function("ilike", 2, sql_ilike, deterministic=True)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create_tables(self) -> None:
        with self._connect() as connection:
            for statement in schema_statements():
                connection.execute(statement)

    def table_names(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT "name"
                FROM "sqlite_master"
                WHERE "type" = 'table' AND "name" NOT LIKE 'sqlite_%'
                ORDER BY "name"
                """
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def _exists(
        self,
        connection: sqlite3.Connection,
        table: Table,
        row_id: int,
        missing: str,
    ) -> None:
        row = connection.execute(
            f'SELECT 1 FROM "{table.name}" WHERE "id" = ?',
            (row_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(missing)

    def _assert_links(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        client_id = payload.get("client_id")
        if client_id is not None and "client_id" in payload:
            self._exists(
                connection,
                CLIENTS,
                client_id,
                f"Клиент {client_id} не найден",
            )
        deal_id = payload.get("deal_id")
        if deal_id is not None and "deal_id" in payload:
            self._exists(
                connection,
                DEALS,
                deal_id,
                f"Сделка {deal_id} не найдена",
            )

    def _insert(self, table: Table, values: dict[str, Any]) -> int:
        now = _utc_now()
        payload = {
            key: value
            for key, value in values.items()
            if key not in {"id", "created_at", "updated_at"}
        }
        payload["created_at"] = now
        payload["updated_at"] = now
        self._check_columns(table, payload)
        columns = list(payload)
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        sql = f'INSERT INTO "{table.name}" ({quoted}) VALUES ({placeholders})'
        with self._connect() as connection:
            self._assert_links(connection, payload)
            try:
                cursor = connection.execute(
                    sql,
                    tuple(payload[column] for column in columns),
                )
            except sqlite3.IntegrityError as error:
                raise ValidationError(_integrity_message(error)) from error
            if cursor.lastrowid is None:
                raise ValidationError("База не вернула id новой записи")
            return int(cursor.lastrowid)

    def _update(
        self,
        table: Table,
        row_id: int,
        values: dict[str, Any],
        missing: str,
    ) -> None:
        if not values:
            raise ValidationError("Нет полей для обновления")
        payload = dict(values)
        payload["updated_at"] = _utc_now()
        self._check_columns(table, payload)
        assignments = ", ".join(f'"{column}" = ?' for column in payload)
        with self._connect() as connection:
            self._exists(connection, table, row_id, missing)
            self._assert_links(connection, payload)
            try:
                connection.execute(
                    f'UPDATE "{table.name}" SET {assignments} WHERE "id" = ?',
                    (*payload.values(), row_id),
                )
            except sqlite3.IntegrityError as error:
                raise ValidationError(_integrity_message(error)) from error

    def _delete(self, table: Table, row_id: int, missing: str) -> None:
        with self._connect() as connection:
            self._exists(connection, table, row_id, missing)
            connection.execute(
                f'DELETE FROM "{table.name}" WHERE "id" = ?',
                (row_id,),
            )

    def _check_columns(self, table: Table, payload: dict[str, Any]) -> None:
        allowed = {
            column.name
            for column in table.columns
            if column.name not in {"id"}
        }
        unknown = set(payload) - allowed
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValidationError(f"Нельзя записывать поля: {names}")

    def _normalize(
        self,
        fields: dict[str, Any],
        normalizers: dict[str, Any],
    ) -> dict[str, Any]:
        if not fields:
            raise ValidationError("Нет полей для обновления")
        unknown = set(fields) - set(normalizers)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValidationError(f"Неизвестные поля: {names}")
        return {key: normalizers[key](value) for key, value in fields.items()}

    def create_client(
        self,
        *,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        company: str | None = None,
        notes: str | None = None,
        status: str = "active",
    ) -> Client:
        payload = self._normalize(
            {
                "name": name,
                "phone": phone,
                "email": email,
                "company": company,
                "notes": notes,
                "status": status,
            },
            _CLIENT_NORMALIZERS,
        )
        client_id = self._insert(CLIENTS, payload)
        return self.get_client(client_id)

    def get_client(self, client_id: int) -> Client:
        with self._connect() as connection:
            row = connection.execute(
                f'SELECT {_select_columns(CLIENTS)} FROM "clients" WHERE "id" = ?',
                (client_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Клиент {client_id} не найден")
        return Client.from_row(row)

    def list_clients(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Client]:
        limit, offset = _bounds(limit, offset)
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append('"status" = ?')
            params.append(_choice(status, CLIENT_STATUSES, "Статус"))
        text = _optional_text(query, "Запрос") if query is not None else None
        if text:
            pattern = contains_pattern(text)
            clauses.append(
                _ilike_clause(
                    ('"name"', '"phone"', '"email"', '"company"', '"notes"')
                )
            )
            params.extend([pattern] * 5)
        where = _where(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f'SELECT COUNT(*) AS "count" FROM "clients" {where}',
                params,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT {_select_columns(CLIENTS)}
                FROM "clients"
                {where}
                ORDER BY "updated_at" DESC, "id" DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return Page(items=[Client.from_row(row) for row in rows], total=int(total))

    def search_clients(
        self,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Client]:
        text = _optional_text(query, "Запрос")
        if text is None:
            raise ValidationError("Пустой поисковый запрос")
        return self.list_clients(query=text, limit=limit, offset=offset)

    def update_client(self, client_id: int, fields: dict[str, Any]) -> Client:
        payload = self._normalize(fields, _CLIENT_NORMALIZERS)
        self._update(CLIENTS, client_id, payload, f"Клиент {client_id} не найден")
        return self.get_client(client_id)

    def archive_client(self, client_id: int) -> Client:
        return self.update_client(client_id, {"status": "archived"})

    def restore_client(self, client_id: int) -> Client:
        return self.update_client(client_id, {"status": "active"})

    def delete_client(self, client_id: int) -> None:
        """Удаляет клиента. Сделки и задачи остаются, ссылка на него обнуляется."""
        self._delete(CLIENTS, client_id, f"Клиент {client_id} не найден")

    def create_deal(
        self,
        *,
        title: str,
        client_id: int | None = None,
        amount: float | None = None,
        status: str = "new",
        notes: str | None = None,
    ) -> Deal:
        payload = self._normalize(
            {
                "title": title,
                "client_id": client_id,
                "amount": amount,
                "status": status,
                "notes": notes,
            },
            _DEAL_NORMALIZERS,
        )
        deal_id = self._insert(DEALS, payload)
        return self.get_deal(deal_id)

    def get_deal(self, deal_id: int) -> Deal:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT {_select_columns(DEALS, "deals")},
                       "clients"."name" AS client_name
                {_DEAL_FROM}
                WHERE "deals"."id" = ?
                """,
                (deal_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Сделка {deal_id} не найдена")
        return Deal.from_row(row)

    def list_deals(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        client_id: int | None = None,
        unassigned: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Deal]:
        limit, offset = _bounds(limit, offset)
        if unassigned and client_id is not None:
            raise ValidationError(
                "Нельзя одновременно фильтровать по клиенту и по сделкам без клиента"
            )
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append('"deals"."status" = ?')
            params.append(_choice(status, DEAL_STATUSES, "Статус"))
        if client_id is not None:
            clauses.append('"deals"."client_id" = ?')
            params.append(_optional_id(client_id, "Клиент"))
        if unassigned:
            clauses.append('"deals"."client_id" IS NULL')
        text = _optional_text(query, "Запрос") if query is not None else None
        if text:
            pattern = contains_pattern(text)
            clauses.append(
                _ilike_clause(
                    (
                        '"deals"."title"',
                        '"deals"."notes"',
                        '"clients"."name"',
                        '"clients"."company"',
                        '"clients"."phone"',
                    )
                )
            )
            params.extend([pattern] * 5)
        where = _where(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f'SELECT COUNT(*) AS "count" {_DEAL_FROM} {where}',
                params,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT {_select_columns(DEALS, "deals")},
                       "clients"."name" AS client_name
                {_DEAL_FROM}
                {where}
                ORDER BY "deals"."updated_at" DESC, "deals"."id" DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return Page(items=[Deal.from_row(row) for row in rows], total=int(total))

    def search_deals(
        self,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Deal]:
        text = _optional_text(query, "Запрос")
        if text is None:
            raise ValidationError("Пустой поисковый запрос")
        return self.list_deals(query=text, limit=limit, offset=offset)

    def update_deal(self, deal_id: int, fields: dict[str, Any]) -> Deal:
        payload = self._normalize(fields, _DEAL_NORMALIZERS)
        self._update(DEALS, deal_id, payload, f"Сделка {deal_id} не найдена")
        return self.get_deal(deal_id)

    def attach_client(self, deal_id: int, client_id: int | None) -> Deal:
        """Прикрепляет клиента к сделке. None отвязывает клиента."""
        return self.update_deal(deal_id, {"client_id": client_id})

    def delete_deal(self, deal_id: int) -> None:
        """Удаляет сделку. Задачи остаются, ссылка на сделку обнуляется."""
        self._delete(DEALS, deal_id, f"Сделка {deal_id} не найдена")

    def create_task(
        self,
        *,
        title: str,
        notes: str | None = None,
        due_at: str | None = None,
        is_done: bool = False,
        client_id: int | None = None,
        deal_id: int | None = None,
    ) -> Task:
        payload = self._normalize(
            {
                "title": title,
                "notes": notes,
                "due_at": due_at,
                "is_done": is_done,
                "client_id": client_id,
                "deal_id": deal_id,
            },
            _TASK_NORMALIZERS,
        )
        task_id = self._insert(TASKS, payload)
        return self.get_task(task_id)

    def get_task(self, task_id: int) -> Task:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT {_select_columns(TASKS, "tasks")},
                       "clients"."name" AS client_name,
                       "deals"."title" AS deal_title
                {_TASK_FROM}
                WHERE "tasks"."id" = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Задача {task_id} не найдена")
        return Task.from_row(row)

    def list_tasks(
        self,
        *,
        query: str | None = None,
        client_id: int | None = None,
        deal_id: int | None = None,
        is_done: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Task]:
        limit, offset = _bounds(limit, offset)
        clauses: list[str] = []
        params: list[Any] = []
        if client_id is not None:
            clauses.append('"tasks"."client_id" = ?')
            params.append(_optional_id(client_id, "Клиент"))
        if deal_id is not None:
            clauses.append('"tasks"."deal_id" = ?')
            params.append(_optional_id(deal_id, "Сделка"))
        if is_done is not None:
            clauses.append('"tasks"."is_done" = ?')
            params.append(_done_flag(is_done))
        text = _optional_text(query, "Запрос") if query is not None else None
        if text:
            pattern = contains_pattern(text)
            clauses.append(
                _ilike_clause(
                    (
                        '"tasks"."title"',
                        '"tasks"."notes"',
                        '"clients"."name"',
                        '"deals"."title"',
                    )
                )
            )
            params.extend([pattern] * 4)
        where = _where(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f'SELECT COUNT(*) AS "count" {_TASK_FROM} {where}',
                params,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT {_select_columns(TASKS, "tasks")},
                       "clients"."name" AS client_name,
                       "deals"."title" AS deal_title
                {_TASK_FROM}
                {where}
                ORDER BY "tasks"."is_done" ASC,
                         "tasks"."due_at" IS NULL,
                         "tasks"."due_at" ASC,
                         "tasks"."id" DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return Page(items=[Task.from_row(row) for row in rows], total=int(total))

    def update_task(self, task_id: int, fields: dict[str, Any]) -> Task:
        payload = self._normalize(fields, _TASK_NORMALIZERS)
        self._update(TASKS, task_id, payload, f"Задача {task_id} не найдена")
        return self.get_task(task_id)

    def set_task_done(self, task_id: int, is_done: bool) -> Task:
        return self.update_task(task_id, {"is_done": is_done})

    def delete_task(self, task_id: int) -> None:
        self._delete(TASKS, task_id, f"Задача {task_id} не найдена")

    def search(self, query: str, *, limit: int = 20) -> dict[str, Page[Any]]:
        """Ищет клиентов и сделки одним запросом пользователя."""
        return {
            "clients": self.search_clients(query, limit=limit),
            "deals": self.search_deals(query, limit=limit),
        }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database = CRMCrud(Path(__file__).with_name("crm.db"))
    print(f"База: {database.db_path}")
    print("Таблицы:", ", ".join(database.table_names()))


if __name__ == "__main__":
    main()
