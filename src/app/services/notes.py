"""Сценарии работы с заметками."""

from app.domain.errors import NotFoundError
from app.domain.notes import Note
from app.domain.ports import NoteRepository


class NoteService:
    """Хранилище приходит через конструктор, поэтому в тестах его легко подменить."""

    def __init__(self, repository: NoteRepository) -> None:
        self._repository = repository

    def create(self, title: str, text: str = "") -> Note:
        return self._repository.add(Note(title=title, text=text))

    def get(self, note_id: int) -> Note:
        note = self._repository.get(note_id)
        if note is None:
            raise NotFoundError(f"Заметка {note_id} не найдена")
        return note

    def list_all(self) -> list[Note]:
        return list(self._repository.list_all())
