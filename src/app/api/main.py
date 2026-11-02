"""Точка сборки приложения.

Запуск для разработки: uv run uvicorn app.api.main:app --reload
"""

from fastapi import FastAPI

from app.api.routes import health, notes
from app.config import Settings
from app.db.session import make_engine, make_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)

    app = FastAPI(title="Курсовой проект", version="0.1.0")
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.include_router(health.router)
    app.include_router(notes.router)
    return app


app = create_app()
