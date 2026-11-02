"""Общие фикстуры: приложение на временной SQLite-базе и HTTP-клиент."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings
from app.db.base import Base


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    application = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    # В тестах схему создаём напрямую; миграции проверяет грейдер.
    Base.metadata.create_all(application.state.engine)
    yield application
    application.state.engine.dispose()  # закрыть соединения, иначе ResourceWarning


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
