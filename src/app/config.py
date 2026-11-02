"""Настройки приложения. Всё, что зависит от окружения, читается здесь и только здесь."""

import os
from dataclasses import dataclass
from typing import Self

DEFAULT_DATABASE_URL = "sqlite:///./app.db"


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL

    @classmethod
    def from_env(cls) -> Self:
        return cls(database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
