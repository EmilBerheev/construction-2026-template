"""Интеграционные тесты HTTP-слоя на временной базе."""

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_create_and_get_note(client: TestClient) -> None:
    created = client.post("/notes", json={"title": "Заметка", "text": "Текст"})
    assert created.status_code == 201
    note_id = created.json()["id"]

    fetched = client.get(f"/notes/{note_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Заметка"


def test_missing_note_returns_404(client: TestClient) -> None:
    assert client.get("/notes/999").status_code == 404


def test_blank_title_is_rejected(client: TestClient) -> None:
    assert client.post("/notes", json={"title": "   "}).status_code == 400
