"""Модели таблиц мини-CRM и их типы в SQLite.

SQLite хранит значения в классах NULL, INTEGER, REAL, TEXT и BLOB.
Отдельных типов BOOLEAN и DATETIME нет, поэтому:

- флаги хранятся как INTEGER со значениями 0 и 1;
- дата и время хранятся как TEXT в формате ISO 8601;
- деньги хранятся как REAL;
- статусы и прочий текст — как TEXT.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

SQLITE_INTEGER = "INTEGER"
SQLITE_REAL = "REAL"
SQLITE_TEXT = "TEXT"
SQLITE_BLOB = "BLOB"
SQLITE_STORAGE_CLASSES = (
    "NULL",
    SQLITE_INTEGER,
    SQLITE_REAL,
    SQLITE_TEXT,
    SQLITE_BLOB,
)

# active — клиент в работе, archived — карточка в архиве и не удалена.
CLIENT_STATUSES = ("active", "archived")
# new — новая, in_progress — в работе, won — успешная, lost — проигранная.
DEAL_STATUSES = ("new", "in_progress", "won", "lost")

_ALLOWED_ON_DELETE = {"CASCADE", "SET NULL", "RESTRICT", "NO ACTION"}
_ALLOWED_SQLITE_TYPES = set(SQLITE_STORAGE_CLASSES) - {"NULL"}

T = TypeVar("T")


def _sql_in(values: tuple[str, ...]) -> str:
    """Список строковых констант для CHECK (... IN (...))."""
    if not values or any(not value.isidentifier() for value in values):
        raise ValueError("Список значений для CHECK должен состоять из идентификаторов")
    return ", ".join(f"'{value}'" for value in values)


def _quote(name: str) -> str:
    if not name.isidentifier():
        raise ValueError(f"Некорректное имя SQL: {name}")
    return f'"{name}"'


@dataclass(frozen=True, slots=True)
class Column:
    """Колонка таблицы: имя, тип SQLite и ограничения."""

    name: str
    sqlite_type: str
    primary_key: bool = False
    autoincrement: bool = False
    not_null: bool = False
    default_sql: str | None = None
    references_table: str | None = None
    on_delete: str | None = None
    check_sql: str | None = None

    def __post_init__(self) -> None:
        _quote(self.name)
        if self.sqlite_type not in _ALLOWED_SQLITE_TYPES:
            raise ValueError(f"Неизвестный тип SQLite у колонки {self.name}")
        if self.autoincrement and not (
            self.primary_key and self.sqlite_type == SQLITE_INTEGER
        ):
            raise ValueError(f"{self.name}: AUTOINCREMENT только у INTEGER PRIMARY KEY")
        if self.on_delete and not self.references_table:
            raise ValueError(f"{self.name}: ON DELETE задан без ссылки на таблицу")
        if self.on_delete and self.on_delete not in _ALLOWED_ON_DELETE:
            raise ValueError(f"{self.name}: недопустимый ON DELETE")
        if self.references_table:
            _quote(self.references_table)

    def ddl(self) -> str:
        parts = [_quote(self.name), self.sqlite_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
            if self.autoincrement:
                parts.append("AUTOINCREMENT")
        elif self.not_null:
            parts.append("NOT NULL")
        if self.default_sql is not None:
            parts.append(f"DEFAULT {self.default_sql}")
        if self.references_table:
            parts.append(f"REFERENCES {_quote(self.references_table)} ({_quote('id')})")
            if self.on_delete:
                parts.append(f"ON DELETE {self.on_delete}")
        if self.check_sql:
            parts.append(f"CHECK ({self.check_sql})")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class Table:
    """Описание таблицы, из которого собирается CREATE TABLE."""

    name: str
    columns: tuple[Column, ...]
    indexes: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        _quote(self.name)
        known = {column.name for column in self.columns}
        if "id" not in known:
            raise ValueError(f"У таблицы {self.name} нет колонки id")
        if len(known) != len(self.columns):
            raise ValueError(f"В таблице {self.name} повторяется имя колонки")
        for columns in self.indexes:
            if not columns:
                raise ValueError(f"Пустой индекс у таблицы {self.name}")
            for column in columns:
                if column not in known:
                    raise ValueError(
                        f"Индекс {self.name} ссылается на неизвестную колонку {column}"
                    )

    def create_sql(self) -> str:
        body = ",\n    ".join(column.ddl() for column in self.columns)
        return f"CREATE TABLE IF NOT EXISTS {_quote(self.name)} (\n    {body}\n)"

    def index_statements(self) -> tuple[str, ...]:
        statements: list[str] = []
        for columns in self.indexes:
            index_name = "idx_" + self.name + "_" + "_".join(columns)
            quoted_columns = ", ".join(_quote(column) for column in columns)
            statements.append(
                "CREATE INDEX IF NOT EXISTS "
                f"{_quote(index_name)} ON {_quote(self.name)} ({quoted_columns})"
            )
        return tuple(statements)

    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


CLIENTS = Table(
    name="clients",
    columns=(
        Column("id", SQLITE_INTEGER, primary_key=True, autoincrement=True),
        Column("name", SQLITE_TEXT, not_null=True),
        Column("phone", SQLITE_TEXT),
        Column("email", SQLITE_TEXT),
        Column("company", SQLITE_TEXT),
        Column("notes", SQLITE_TEXT),
        Column(
            "status",
            SQLITE_TEXT,
            not_null=True,
            default_sql="'active'",
            check_sql=f"{_quote('status')} IN ({_sql_in(CLIENT_STATUSES)})",
        ),
        Column("created_at", SQLITE_TEXT, not_null=True),
        Column("updated_at", SQLITE_TEXT, not_null=True),
    ),
    indexes=(("status",),),
)

DEALS = Table(
    name="deals",
    columns=(
        Column("id", SQLITE_INTEGER, primary_key=True, autoincrement=True),
        Column("title", SQLITE_TEXT, not_null=True),
        Column(
            "client_id",
            SQLITE_INTEGER,
            references_table="clients",
            on_delete="SET NULL",
        ),
        Column(
            "amount",
            SQLITE_REAL,
            check_sql=f"{_quote('amount')} IS NULL OR {_quote('amount')} >= 0",
        ),
        Column(
            "status",
            SQLITE_TEXT,
            not_null=True,
            default_sql="'new'",
            check_sql=f"{_quote('status')} IN ({_sql_in(DEAL_STATUSES)})",
        ),
        Column("notes", SQLITE_TEXT),
        Column("created_at", SQLITE_TEXT, not_null=True),
        Column("updated_at", SQLITE_TEXT, not_null=True),
    ),
    indexes=(("client_id",), ("status",)),
)

TASKS = Table(
    name="tasks",
    columns=(
        Column("id", SQLITE_INTEGER, primary_key=True, autoincrement=True),
        Column("title", SQLITE_TEXT, not_null=True),
        Column("notes", SQLITE_TEXT),
        Column("due_at", SQLITE_TEXT),
        Column(
            "is_done",
            SQLITE_INTEGER,
            not_null=True,
            default_sql="0",
            check_sql=f"{_quote('is_done')} IN (0, 1)",
        ),
        Column(
            "client_id",
            SQLITE_INTEGER,
            references_table="clients",
            on_delete="SET NULL",
        ),
        Column(
            "deal_id",
            SQLITE_INTEGER,
            references_table="deals",
            on_delete="SET NULL",
        ),
        Column("created_at", SQLITE_TEXT, not_null=True),
        Column("updated_at", SQLITE_TEXT, not_null=True),
    ),
    indexes=(("client_id",), ("deal_id",), ("is_done",)),
)

TABLES: tuple[Table, ...] = (CLIENTS, DEALS, TASKS)


def schema_statements() -> tuple[str, ...]:
    """CREATE TABLE и CREATE INDEX в порядке, безопасном для внешних ключей."""
    created: set[str] = set()
    statements: list[str] = []
    for table in TABLES:
        for column in table.columns:
            if column.references_table and column.references_table not in created:
                raise ValueError(
                    f"{table.name}.{column.name} ссылается на "
                    f"{column.references_table} до её создания"
                )
        statements.append(table.create_sql())
        statements.extend(table.index_statements())
        created.add(table.name)
    return tuple(statements)


def _mapping(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


@dataclass(slots=True)
class Client:
    """Карточка клиента. status: active или archived."""

    id: int
    name: str
    phone: str | None
    email: str | None
    company: str | None
    notes: str | None
    status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> Client:
        data = _mapping(row)
        return cls(
            id=data["id"],
            name=data["name"],
            phone=data["phone"],
            email=data["email"],
            company=data["company"],
            notes=data["notes"],
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(slots=True)
class Deal:
    """Сделка или заказ. client_id может быть пустым.

    client_name не хранится в таблице deals: оно подставляется соединением
    с clients при чтении.
    """

    id: int
    title: str
    client_id: int | None
    client_name: str | None
    amount: float | None
    status: str
    notes: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> Deal:
        data = _mapping(row)
        amount = data["amount"]
        return cls(
            id=data["id"],
            title=data["title"],
            client_id=data["client_id"],
            client_name=data.get("client_name"),
            amount=None if amount is None else float(amount),
            status=data["status"],
            notes=data["notes"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(slots=True)
class Task:
    """Задача или напоминание. Связь с клиентом и со сделкой необязательна.

    client_name и deal_title заполняются при чтении и не являются колонками tasks.
    is_done в базе — INTEGER 0/1, в модели — bool.
    """

    id: int
    title: str
    notes: str | None
    due_at: str | None
    is_done: bool
    client_id: int | None
    client_name: str | None
    deal_id: int | None
    deal_title: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> Task:
        data = _mapping(row)
        return cls(
            id=data["id"],
            title=data["title"],
            notes=data["notes"],
            due_at=data["due_at"],
            is_done=bool(data["is_done"]),
            client_id=data["client_id"],
            client_name=data.get("client_name"),
            deal_id=data["deal_id"],
            deal_title=data.get("deal_title"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(slots=True)
class Page(Generic[T]):
    """Страница списка: записи и общее число без учёта limit."""

    items: list[T]
    total: int
