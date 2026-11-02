"""Хранилище заметок в памяти процесса."""

from dataclasses import replace

from app.domain.notes import Note


class InMemoryNoteRepository:
    """Реализует порт NoteRepository без наследования: Protocol проверяется по форме."""

    def __init__(self) -> None:
        self._items: dict[int, Note] = {}
        self._next_id = 1

    def add(self, note: Note) -> Note:
        stored = replace(note, id=self._next_id)
        self._items[self._next_id] = stored
        self._next_id += 1
        return stored

    def get(self, note_id: int) -> Note | None:
        return self._items.get(note_id)

    def list_all(self) -> list[Note]:
        return list(self._items.values())
