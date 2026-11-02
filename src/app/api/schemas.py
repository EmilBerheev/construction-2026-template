"""Схемы запросов и ответов (Pydantic). Из них FastAPI строит OpenAPI."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Заголовок из одних пробелов отклоняет домен (Note.__post_init__), а не regex в схеме:
    # «пробел» в JSON Schema (ECMA-262) и в Pydantic (Rust regex) понимается по-разному.
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=10_000)


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    text: str
    created_at: datetime
