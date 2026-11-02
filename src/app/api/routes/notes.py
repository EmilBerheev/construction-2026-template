"""Ресурс /notes — пример, который вы замените своими ресурсами в ЛР4."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from app.api.deps import NoteServiceDep
from app.api.schemas import NoteCreate, NoteRead
from app.domain.errors import DomainError, NotFoundError

router = APIRouter(prefix="/notes", tags=["notes"])

# Ограничение сверху нужно: без него на огромном числе SQLite упадёт с ошибкой 500.
NoteId = Annotated[int, Path(ge=1, le=2**63 - 1)]


# 400 бывает в двух случаях: FastAPI не разобрал тело (например, битый UTF-8) или домен
# отверг данные. Код нужно задокументировать, иначе Schemathesis найдёт недокументированный ответ.
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Тело не разобрано или нарушает правила домена"}},
)
def create_note(payload: NoteCreate, service: NoteServiceDep) -> NoteRead:
    try:
        note = service.create(payload.title, payload.text)
    except DomainError as error:
        # ЛР4: правильнее 422 в формате RFC 9457; см. задание.
        raise HTTPException(status_code=400, detail=str(error)) from error
    return NoteRead.model_validate(note)


@router.get("/{note_id}", responses={404: {"description": "Заметка не найдена"}})
def get_note(note_id: NoteId, service: NoteServiceDep) -> NoteRead:
    try:
        note = service.get(note_id)
    except NotFoundError as error:
        # ЛР4: ошибки нужно отдавать в формате RFC 9457 (application/problem+json).
        raise HTTPException(status_code=404, detail=str(error)) from error
    return NoteRead.model_validate(note)


@router.get("")
def list_notes(service: NoteServiceDep) -> list[NoteRead]:
    # ЛР4: список без пагинации растёт без ограничений. Добавьте limit + offset или курсор.
    return [NoteRead.model_validate(note) for note in service.list_all()]
