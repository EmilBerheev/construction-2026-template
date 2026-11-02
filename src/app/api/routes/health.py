"""Проверка живости сервиса и доступности базы."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import SessionDep

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


@router.get("/health")
def health(session: SessionDep) -> Health:
    session.execute(text("SELECT 1"))
    return Health(status="ok", database="ok")
