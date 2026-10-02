"""Мини-CRM: HTTP API на FastAPI поверх SQLite.

Запуск:

    python crm.py

Документация запросов: http://127.0.0.1:8000/docs

Файл базы задаётся переменной CRM_DB_PATH. Если её нет, используется crm.db
рядом с этим модулем.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from crud import CRMCrud, NotFoundError, ValidationError
from models import Page

ClientStatus = Literal["active", "archived"]
DealStatus = Literal["new", "in_progress", "won", "lost"]

load_dotenv(Path(__file__).with_name(".env"))


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200, description="Имя или название")
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    status: ClientStatus = "active"


class ClientUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    status: ClientStatus | None = None


class ClientOut(ApiModel):
    id: int
    name: str
    phone: str | None
    email: str | None
    company: str | None
    notes: str | None
    status: ClientStatus
    created_at: str
    updated_at: str


class ClientPageOut(ApiModel):
    items: list[ClientOut]
    total: int


class DealCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    client_id: int | None = Field(
        default=None,
        gt=0,
        description="Клиент необязателен: сделку можно завести саму по себе",
    )
    amount: float | None = Field(default=None, ge=0)
    status: DealStatus = "new"
    notes: str | None = Field(default=None, max_length=5000)


class DealUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    client_id: int | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, ge=0)
    status: DealStatus | None = None
    notes: str | None = Field(default=None, max_length=5000)


class DealClientLink(ApiModel):
    client_id: int | None = Field(
        default=None,
        gt=0,
        description="Число прикрепляет клиента, null отвязывает",
    )


class DealOut(ApiModel):
    id: int
    title: str
    client_id: int | None
    client_name: str | None
    amount: float | None
    status: DealStatus
    notes: str | None
    created_at: str
    updated_at: str


class DealPageOut(ApiModel):
    items: list[DealOut]
    total: int


class TaskCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    due_at: str | None = Field(
        default=None,
        description="Срок или время напоминания в ISO 8601",
    )
    is_done: bool = False
    client_id: int | None = Field(default=None, gt=0)
    deal_id: int | None = Field(default=None, gt=0)


class TaskUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    due_at: str | None = None
    is_done: bool | None = None
    client_id: int | None = Field(default=None, gt=0)
    deal_id: int | None = Field(default=None, gt=0)


class TaskOut(ApiModel):
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


class TaskPageOut(ApiModel):
    items: list[TaskOut]
    total: int


class SearchOut(ApiModel):
    query: str
    clients: ClientPageOut
    deals: DealPageOut


def _dump_page(page: Page[Any]) -> dict[str, Any]:
    return {"items": [asdict(item) for item in page.items], "total": page.total}


def _changed_fields(payload: ApiModel) -> dict[str, Any]:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise ValidationError("Нет полей для обновления")
    return fields


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Собирает API и сразу создаёт таблицы в указанном файле базы."""
    if db_path is None:
        env_path = os.getenv("CRM_DB_PATH", "").strip()
        db_path = env_path or Path(__file__).with_name("crm.db")

    app = FastAPI(
        title="Мини-CRM",
        version="1.0.0",
        summary="Клиенты, сделки и задачи взаимодействия",
        description=(
            "Локальная CRM на SQLite. Поиск клиентов и сделок выполняет "
            "SQL-функция ilike: без учёта регистра, включая кириллицу. "
            "В шаблоне % — любая последовательность, _ — один символ."
        ),
        openapi_tags=[
            {
                "name": "Клиенты",
                "description": "Карточки клиентов: создание, правка, архив и удаление",
            },
            {
                "name": "Сделки",
                "description": "Сделки и заказы. Клиент может быть не указан",
            },
            {
                "name": "Задачи",
                "description": "Задачи и напоминания со связью на клиента или сделку",
            },
            {
                "name": "Поиск",
                "description": "Общий поиск по клиентам и сделкам",
            },
        ],
    )
    app.state.db = CRMCrud(db_path)

    def get_db(request: Request) -> CRMCrud:
        return request.app.state.db

    @app.exception_handler(NotFoundError)
    async def handle_not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def handle_validation(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/")
    def root() -> dict[str, str]:
        return {"name": "Мини-CRM", "docs": "/docs"}

    @app.get("/search", response_model=SearchOut, tags=["Поиск"])
    def search(
        q: str = Query(min_length=1, max_length=200, description="Текст для ilike"),
        limit: int = Query(default=20, ge=1, le=100),
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        found = db.search(q, limit=limit)
        return {
            "query": q.strip(),
            "clients": _dump_page(found["clients"]),
            "deals": _dump_page(found["deals"]),
        }

    @app.post(
        "/clients",
        response_model=ClientOut,
        status_code=status.HTTP_201_CREATED,
        tags=["Клиенты"],
    )
    def create_client(payload: ClientCreate, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.create_client(**payload.model_dump()))

    @app.get("/clients", response_model=ClientPageOut, tags=["Клиенты"])
    def list_clients(
        q: str | None = Query(default=None, description="Поиск ilike по имени, телефону, email, компании и заметкам"),
        status_filter: ClientStatus | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return _dump_page(
            db.list_clients(query=q, status=status_filter, limit=limit, offset=offset)
        )

    @app.get("/clients/{client_id}", response_model=ClientOut, tags=["Клиенты"])
    def get_client(client_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.get_client(client_id))

    @app.patch("/clients/{client_id}", response_model=ClientOut, tags=["Клиенты"])
    def update_client(
        client_id: int,
        payload: ClientUpdate,
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return asdict(db.update_client(client_id, _changed_fields(payload)))

    @app.post(
        "/clients/{client_id}/archive",
        response_model=ClientOut,
        tags=["Клиенты"],
        summary="Архивировать клиента",
    )
    def archive_client(client_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.archive_client(client_id))

    @app.post(
        "/clients/{client_id}/restore",
        response_model=ClientOut,
        tags=["Клиенты"],
        summary="Вернуть клиента из архива",
    )
    def restore_client(client_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.restore_client(client_id))

    @app.delete(
        "/clients/{client_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        tags=["Клиенты"],
        summary="Удалить клиента",
        description="Карточка удаляется. Сделки и задачи остаются, связь с клиентом очищается.",
    )
    def delete_client(client_id: int, db: CRMCrud = Depends(get_db)) -> None:
        db.delete_client(client_id)

    @app.post(
        "/deals",
        response_model=DealOut,
        status_code=status.HTTP_201_CREATED,
        tags=["Сделки"],
    )
    def create_deal(payload: DealCreate, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.create_deal(**payload.model_dump()))

    @app.get("/deals", response_model=DealPageOut, tags=["Сделки"])
    def list_deals(
        q: str | None = Query(
            default=None,
            description="Поиск ilike по названию, заметкам, имени, компании и телефону клиента",
        ),
        status_filter: DealStatus | None = Query(default=None, alias="status"),
        client_id: int | None = Query(default=None, gt=0),
        unassigned: bool = Query(default=False, description="Только сделки без клиента"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return _dump_page(
            db.list_deals(
                query=q,
                status=status_filter,
                client_id=client_id,
                unassigned=unassigned,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/deals/{deal_id}", response_model=DealOut, tags=["Сделки"])
    def get_deal(deal_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.get_deal(deal_id))

    @app.patch("/deals/{deal_id}", response_model=DealOut, tags=["Сделки"])
    def update_deal(
        deal_id: int,
        payload: DealUpdate,
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return asdict(db.update_deal(deal_id, _changed_fields(payload)))

    @app.put(
        "/deals/{deal_id}/client",
        response_model=DealOut,
        tags=["Сделки"],
        summary="Прикрепить или отвязать клиента",
    )
    def attach_client(
        deal_id: int,
        payload: DealClientLink,
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return asdict(db.attach_client(deal_id, payload.client_id))

    @app.delete(
        "/deals/{deal_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        tags=["Сделки"],
        description="Сделка удаляется. Задачи остаются, связь со сделкой очищается.",
    )
    def delete_deal(deal_id: int, db: CRMCrud = Depends(get_db)) -> None:
        db.delete_deal(deal_id)

    @app.post(
        "/tasks",
        response_model=TaskOut,
        status_code=status.HTTP_201_CREATED,
        tags=["Задачи"],
    )
    def create_task(payload: TaskCreate, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.create_task(**payload.model_dump()))

    @app.get("/tasks", response_model=TaskPageOut, tags=["Задачи"])
    def list_tasks(
        q: str | None = Query(default=None, description="Поиск ilike по тексту задачи, клиенту и сделке"),
        client_id: int | None = Query(default=None, gt=0),
        deal_id: int | None = Query(default=None, gt=0),
        is_done: bool | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return _dump_page(
            db.list_tasks(
                query=q,
                client_id=client_id,
                deal_id=deal_id,
                is_done=is_done,
                limit=limit,
                offset=offset,
            )
        )

    @app.get("/tasks/{task_id}", response_model=TaskOut, tags=["Задачи"])
    def get_task(task_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.get_task(task_id))

    @app.patch("/tasks/{task_id}", response_model=TaskOut, tags=["Задачи"])
    def update_task(
        task_id: int,
        payload: TaskUpdate,
        db: CRMCrud = Depends(get_db),
    ) -> dict[str, Any]:
        return asdict(db.update_task(task_id, _changed_fields(payload)))

    @app.post(
        "/tasks/{task_id}/complete",
        response_model=TaskOut,
        tags=["Задачи"],
        summary="Отметить задачу выполненной",
    )
    def complete_task(task_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.set_task_done(task_id, True))

    @app.post(
        "/tasks/{task_id}/reopen",
        response_model=TaskOut,
        tags=["Задачи"],
        summary="Вернуть задачу в невыполненные",
    )
    def reopen_task(task_id: int, db: CRMCrud = Depends(get_db)) -> dict[str, Any]:
        return asdict(db.set_task_done(task_id, False))

    @app.delete(
        "/tasks/{task_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        tags=["Задачи"],
    )
    def delete_task(task_id: int, db: CRMCrud = Depends(get_db)) -> None:
        db.delete_task(task_id)

    return app


app = create_app()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import uvicorn

    uvicorn.run("crm:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
