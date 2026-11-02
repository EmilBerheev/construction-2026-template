"""Ошибки предметной области. Слой API переводит их в HTTP-ответы."""


class DomainError(Exception):
    """Нарушено правило предметной области."""


class NotFoundError(DomainError):
    """Объект не найден."""


class ConflictError(DomainError):
    """Операция конфликтует с текущим состоянием (дубликат, занятый слот и т. п.)."""
