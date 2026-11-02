"""Репозиторий заметок поверх SQLAlchemy. Реализует порт app.domain.ports.NoteRepository."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NoteRow
from app.domain.notes import Note


def as_utc(moment: datetime) -> datetime:
    """SQLite теряет часовой пояс. Без этого API отдаст время без смещения, и это не RFC 3339."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _to_domain(row: NoteRow) -> Note:
    return Note(id=row.id, title=row.title, text=row.text, created_at=as_utc(row.created_at))


class SqlNoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, note: Note) -> Note:
        row = NoteRow(title=note.title, text=note.text, created_at=note.created_at)
        self._session.add(row)
        self._session.flush()
        return _to_domain(row)

    def get(self, note_id: int) -> Note | None:
        row = self._session.get(NoteRow, note_id)
        return None if row is None else _to_domain(row)

    def list_all(self) -> list[Note]:
        rows = self._session.scalars(select(NoteRow).order_by(NoteRow.id))
        return [_to_domain(row) for row in rows]
