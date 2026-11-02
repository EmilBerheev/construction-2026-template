"""Юнит-тесты примера: домен и сервис без базы данных."""

import pytest

from app.adapters.memory import InMemoryNoteRepository
from app.domain.errors import DomainError, NotFoundError
from app.domain.notes import TITLE_MAX_LENGTH, Note
from app.services.notes import NoteService


@pytest.fixture
def service() -> NoteService:
    return NoteService(InMemoryNoteRepository())


def test_note_title_is_stripped() -> None:
    assert Note(title="  План  ").title == "План"


@pytest.mark.parametrize("title", ["", "   ", "x" * (TITLE_MAX_LENGTH + 1)])
def test_note_rejects_bad_title(title: str) -> None:
    with pytest.raises(DomainError):
        Note(title=title)


def test_create_assigns_id(service: NoteService) -> None:
    note = service.create("Купить молоко")
    assert note.id == 1
    assert service.get(1) == note


def test_get_missing_note_raises(service: NoteService) -> None:
    with pytest.raises(NotFoundError):
        service.get(42)


def test_list_returns_notes_in_creation_order(service: NoteService) -> None:
    service.create("Первая")
    service.create("Вторая")
    assert [note.title for note in service.list_all()] == ["Первая", "Вторая"]
