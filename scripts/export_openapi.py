"""Выгружает OpenAPI-схему приложения в docs/api/openapi.json.

Запуск: uv run python scripts/export_openapi.py
Запускайте после каждого изменения API: грейдер сверяет файл с тем, что отдаёт приложение.
"""

import json
import sys
from pathlib import Path

from app.api.main import app

DEFAULT_TARGET = Path("docs/api/openapi.json")


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    text = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    target.write_bytes(text.encode("utf-8"))  # байты: одинаковые переводы строк на любой ОС
    print(f"OpenAPI {schema.get('openapi')} -> {target}")


if __name__ == "__main__":
    main()
