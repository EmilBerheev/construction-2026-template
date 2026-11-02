"""Зависимости FastAPI: сессия на запрос и сборка сервисов (composition root)."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.db.repository import SqlNoteRepository
from app.services.notes import NoteService


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    # begin(): commit, если обработчик завершился без исключения, иначе rollback.
    with factory.begin() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_note_service(session: SessionDep) -> NoteService:
    return NoteService(SqlNoteRepository(session))


NoteServiceDep = Annotated[NoteService, Depends(get_note_service)]
