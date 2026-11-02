"""Пример предметной области — заметки.

Это заглушка, чтобы шаблон сразу работал. В ЛР2 замените её своими сущностями.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.errors import DomainError

TITLE_MAX_LENGTH = 200


@dataclass(slots=True)
class Note:
    title: str
    text: str = ""
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        title = self.title.strip()
        if not title:
            raise DomainError("Заголовок заметки не может быть пустым")
        if len(title) > TITLE_MAX_LENGTH:
            raise DomainError(f"Заголовок длиннее {TITLE_MAX_LENGTH} символов")
        self.title = title
