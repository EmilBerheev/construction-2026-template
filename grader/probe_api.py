"""Зонд для ЛР4. Грейдер запускает его отдельным процессом:

    python -m grader.probe_api <модуль:объект> <куда_записать.json>

Зонд импортирует приложение студента, снимает OpenAPI-схему и делает несколько запросов
через TestClient. Код студента работает в своём процессе, поэтому его падение не роняет грейдер.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

LIMIT_NAMES = ("limit", "page_size", "per_page", "size")
POSITION_NAMES = ("offset", "cursor", "page", "after")
JSON_BODY = ("application/json",)


def load_app(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    factory = attr.endswith("()")
    target = getattr(module, attr.removesuffix("()") or "app")
    return target() if factory else target


def resolve(schema: dict[str, Any], node: Any) -> Any:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        ref = str(node["$ref"])
        if not ref.startswith("#/"):
            return {}
        node = schema
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        seen += 1
    return node


def describe(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = None
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "body": body if isinstance(body, dict | list) else None,
        "text": response.text[:300],
    }


def operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    result = []
    for path, item in (schema.get("paths") or {}).items():
        for method, operation in (item or {}).items():
            if method in {"get", "post", "put", "patch", "delete"} and isinstance(operation, dict):
                result.append((method.upper(), path, operation))
    return result


def parameters(schema: dict[str, Any], operation: dict[str, Any], where: str) -> dict[str, Any]:
    found = {}
    for raw in operation.get("parameters") or []:
        parameter = resolve(schema, raw)
        if parameter.get("in") == where:
            found[parameter.get("name")] = resolve(schema, parameter.get("schema") or {})
    return found


def _type_of(schema: dict[str, Any], node: Any) -> str:
    node = resolve(schema, node)
    if not isinstance(node, dict):
        return ""
    if "type" in node:
        return str(node["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        for option in node.get(key) or []:
            kind = _type_of(schema, option)
            if kind and kind != "null":
                return kind
    return ""


def _fill_path(path: str, schema: dict[str, Any], operation: dict[str, Any]) -> str:
    for name, param_schema in parameters(schema, operation, "path").items():
        value = "1" if _type_of(schema, param_schema) == "integer" else "x"
        path = path.replace("{" + str(name) + "}", value)
    return path


def invalid_request(client: Any, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Заведомо неверный запрос, который должен получить 4xx в формате problem+json."""
    ops = operations(schema)
    for method, path, operation in ops:
        body = resolve(schema, operation.get("requestBody") or {})
        content = body.get("content") or {}
        if method in {"POST", "PUT", "PATCH"} and any(kind in content for kind in JSON_BODY):
            body_schema = content.get("application/json", {}).get("schema", {})
            wrong: Any = (
                {"__grader__": True} if _type_of(schema, body_schema) == "array" else [1, 2, 3]
            )
            url = _fill_path(path, schema, operation)
            response = client.request(method, url, json=wrong)
            return {"request": f"{method} {url}", "response": describe(response)}
    for method, path, operation in ops:
        for name, param_schema in parameters(schema, operation, "query").items():
            if _type_of(schema, param_schema) == "integer":
                url = _fill_path(path, schema, operation)
                response = client.request(method, url, params={name: "not-a-number"})
                return {
                    "request": f"{method} {url}?{name}=not-a-number",
                    "response": describe(response),
                }
    for method, path, operation in ops:
        for name, param_schema in parameters(schema, operation, "path").items():
            if _type_of(schema, param_schema) == "integer":
                url = path.replace("{" + str(name) + "}", "not-a-number")
                url = _fill_path(url, schema, operation)
                response = client.request(method, url)
                return {"request": f"{method} {url}", "response": describe(response)}
    return None


def pagination(client: Any, schema: dict[str, Any]) -> dict[str, Any]:
    paginated, bare = [], []
    for method, path, operation in operations(schema):
        if method != "GET" or "{" in path:
            continue
        ok_response = resolve(schema, (operation.get("responses") or {}).get("200") or {})
        body_schema = resolve(
            schema, ((ok_response.get("content") or {}).get("application/json") or {}).get("schema")
        )
        if not isinstance(body_schema, dict):
            continue
        if _type_of(schema, body_schema) == "array":
            bare.append(f"GET {path}")
            continue
        query = parameters(schema, operation, "query")
        limit = next((name for name in LIMIT_NAMES if name in query), None)
        position = next((name for name in POSITION_NAMES if name in query), None)
        items = resolve(schema, (body_schema.get("properties") or {}).get("items") or {})
        envelope = _type_of(schema, items) == "array"
        if limit and position and envelope:
            paginated.append(
                {"op": f"GET {path}", "path": path, "limit": limit, "position": position}
            )
    runtime = None
    if paginated:
        first = paginated[0]
        response = client.get(first["path"], params={first["limit"]: 1})
        runtime = {
            "request": f"GET {first['path']}?{first['limit']}=1",
            "response": describe(response),
        }
    return {"paginated": paginated, "bare_arrays": bare, "runtime": runtime}


def main() -> None:
    spec, target = sys.argv[1], Path(sys.argv[2])
    result: dict[str, Any] = {"error": None}
    try:
        app = load_app(spec)
        schema = app.openapi()
        result["openapi"] = schema
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            result["missing"] = describe(client.get("/__grader__/no-such-resource"))
            result["invalid"] = invalid_request(client, schema)
            result["pagination"] = pagination(client, schema)
    except BaseException as exc:  # код студента может бросить что угодно, включая SystemExit
        result["error"] = f"{type(exc).__name__}: {exc}"
    target.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
