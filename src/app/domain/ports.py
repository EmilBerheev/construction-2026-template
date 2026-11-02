"""Порты — абстракции, от которых зависят сервисы (принцип инверсии зависимостей).

Реализации лежат во внешних слоях: app.adapters (память) и app.db (SQLAlchemy).
"""

from collections.abc import Sequence
from typing import Protocol

from app.domain.notes import Note


class NoteRepository(Protocol):
    def add(self, note: Note) -> Note: ...

    def get(self, note_id: int) -> Note | None: ...

    # ЛР4: метод возвращает всё сразу. Добавьте limit/offset или курсор.
    def list_all(self) -> Sequence[Note]: ...
