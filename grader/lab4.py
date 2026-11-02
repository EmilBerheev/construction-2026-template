"""ЛР4. API и база данных."""

from __future__ import annotations

import ast
import configparser
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grader.common import check_report, relative
from grader.core import Check, Context, Lab, Outcome, fail, not_applicable, ok, skip_ci
from grader.util import (
    diagram_type,
    has_placeholder,
    mermaid_blocks,
    module_available,
    parse_sources,
    plural,
    python,
    run,
)

API_DOC = "docs/api.md"
OPENAPI_FILE = "docs/api/openapi.json"
MIN_MIGRATIONS = 2
MIN_TABLES = 3
MIN_OPERATIONS = 6
CONTRACT_EXAMPLES = 50
PROBLEM_TYPE = "application/problem+json"
GATE = f"когда в БД ≥ {MIN_TABLES} таблицы и в API ≥ {MIN_OPERATIONS} операций"


# --- Миграции ------------------------------------------------------------------------


def _script_location(ctx: Context) -> Path:
    ini = ctx.path("alembic.ini")
    location = "migrations"
    if ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(ini.read_text(encoding="utf-8", errors="replace"))
            location = parser.get("alembic", "script_location", fallback=location)
        except configparser.Error:
            pass
    location = location.replace("%(here)s", ".").strip()
    return (ctx.root / location).resolve()


def migration_files(ctx: Context) -> list[Path]:
    versions = _script_location(ctx) / "versions"
    if not versions.is_dir():
        return []
    return sorted(path for path in versions.glob("*.py") if not path.name.startswith("__"))


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _alembic(ctx: Context, url: str, *args: str) -> tuple[int, str]:
    if not module_available("alembic"):
        return 127, "alembic не установлен: выполните uv sync"
    proc = run(python("-m", "alembic", *args), ctx, env={"DATABASE_URL": url}, timeout=300)
    return proc.returncode, proc.output


def _tables(db: Path) -> tuple[list[str], int]:
    with sqlite3.connect(db) as connection:
        names = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if row[0] != "alembic_version" and not row[0].startswith("sqlite_")
        ]
        foreign_keys = sum(
            len(connection.execute(f'PRAGMA foreign_key_list("{name}")').fetchall())
            for name in names
        )
    return names, foreign_keys


@dataclass
class RoundTrip:
    ok: bool
    problem: str = ""
    tables: list[str] = field(default_factory=list)
    foreign_keys: int = 0
    database: Path | None = None


def round_trip_sqlite(ctx: Context) -> RoundTrip:
    def load() -> RoundTrip:
        db = ctx.tmp / "migrations.db"
        url = _sqlite_url(db)
        code, output = _alembic(ctx, url, "upgrade", "head")
        if code != 0:
            return RoundTrip(False, "upgrade head упал: " + _last_error(output))
        tables, foreign_keys = _tables(db)
        code, output = _alembic(ctx, url, "downgrade", "base")
        if code != 0:
            return RoundTrip(
                False, "downgrade base упал: " + _last_error(output), tables, foreign_keys, db
            )
        leftover, _ = _tables(db)
        if leftover:
            return RoundTrip(
                False,
                "после downgrade base остались таблицы: " + ", ".join(leftover[:5]),
                tables,
                foreign_keys,
                db,
            )
        code, output = _alembic(ctx, url, "upgrade", "head")
        if code != 0:
            return RoundTrip(
                False,
                "повторный upgrade head упал: " + _last_error(output),
                tables,
                foreign_keys,
                db,
            )
        return RoundTrip(True, "", tables, foreign_keys, db)

    return ctx.memo("lab4:roundtrip", load)


def _last_error(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    errors = [line for line in lines if re.match(r"^[\w.]+(Error|Exception)\b", line)]
    return (errors or lines or ["нет вывода"])[-1][:200]


def head_database(ctx: Context) -> Path | None:
    """База в состоянии head: после удачного круга миграций или отдельным upgrade head."""

    def load() -> Path | None:
        trip = round_trip_sqlite(ctx)
        if trip.ok and trip.database is not None:
            return trip.database
        db = ctx.tmp / "head.db"
        code, _ = _alembic(ctx, _sqlite_url(db), "upgrade", "head")
        return db if code == 0 else None

    return ctx.memo("lab4:head", load)


def fresh_database(ctx: Context, name: str) -> str | None:
    """Копия базы после миграций, чтобы зонды не мешали друг другу."""
    head = head_database(ctx)
    if head is None or not head.is_file():
        return None
    copy = ctx.tmp / f"{name}.db"
    shutil.copyfile(head, copy)
    return _sqlite_url(copy)


def check_migrations(ctx: Context) -> Outcome:
    files = migration_files(ctx)
    if len(files) < MIN_MIGRATIONS:
        return fail(
            f"Нужно ≥ {MIN_MIGRATIONS} миграции в migrations/versions/: "
            'uv run alembic revision --autogenerate -m "add users" --rev-id 0002.',
            detail=f"сейчас {len(files)}",
        )
    trip = round_trip_sqlite(ctx)
    if not trip.ok:
        return fail("Миграции должны проходить upgrade → downgrade → upgrade. " + trip.problem)
    return ok(plural(len(files), "миграция", "миграции", "миграций") + "; upgrade/downgrade чисто")


def check_tables(ctx: Context) -> Outcome:
    trip = round_trip_sqlite(ctx)
    if not trip.tables and not trip.ok:
        return fail("Миграции не применились, таблиц нет. " + trip.problem)
    if len(trip.tables) < MIN_TABLES:
        return fail(
            f"После миграций в базе должно быть ≥ {MIN_TABLES} таблицы предметной области.",
            detail=f"сейчас {len(trip.tables)}: {', '.join(trip.tables) or '—'}",
        )
    if trip.foreign_keys == 0:
        return fail("Нет ни одного внешнего ключа: свяжите таблицы через ForeignKey.")
    return ok(f"таблиц {len(trip.tables)}, внешних ключей {trip.foreign_keys}")


def check_alembic_check(ctx: Context) -> Outcome:
    if (gate := lab4_gate(ctx)) is not None:
        return gate
    url = fresh_database(ctx, "alembic-check")
    if url is None:
        return fail("Миграции не применились, сравнивать модели не с чем.")
    code, output = _alembic(ctx, url, "check")
    if code == 0:
        return ok("модели и миграции совпадают")
    return fail(
        "Модели расходятся с миграциями: создайте миграцию через "
        "uv run alembic revision --autogenerate и проверьте её.",
        detail=_last_error(output),
    )


def check_postgres(ctx: Context) -> Outcome:
    if (gate := lab4_gate(ctx)) is not None:
        return gate
    url = os.environ.get("GRADER_PG_URL")
    if not url:
        return skip_ci("PostgreSQL поднимается сервис-контейнером в GitHub Actions")
    if not module_available("psycopg"):
        return fail("Нет драйвера psycopg: в CI нужен uv sync --all-groups.")
    for step in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        code, output = _alembic(ctx, url, *step)
        if code != 0:
            return fail(
                f"На PostgreSQL упал шаг alembic {' '.join(step)}. Частые причины: "
                "типы, которых нет в PostgreSQL, длина VARCHAR, булевы значения по умолчанию.",
                detail=_last_error(output),
            )
    return ok("upgrade → downgrade → upgrade на PostgreSQL")


# --- Статические проверки кода ---------------------------------------------------------


def _legacy_sqlalchemy(node: ast.AST) -> str | None:
    """Описание устаревшего вызова SQLAlchemy 1.x или None."""
    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
        "sqlalchemy.ext.declarative"
    ):
        return "sqlalchemy.ext.declarative"
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if name == "declarative_base":
        return "declarative_base()"
    if name == "query" and isinstance(func, ast.Attribute):
        return f"строка {node.lineno}: session.query()"
    first = node.args[0] if node.args else None
    raw_sql = isinstance(first, ast.Constant) and isinstance(first.value, str)
    if name == "execute" and raw_sql and isinstance(func, ast.Attribute):
        # cursor.execute("...") — это DB-API, а не SQLAlchemy; ловим session/connection/engine.
        receiver = func.value
        owner = receiver.id if isinstance(receiver, ast.Name) else getattr(receiver, "attr", "")
        if any(word in owner.lower() for word in ("session", "conn", "engine")):
            return f"строка {node.lineno}: {owner}.execute(строка) без text()"
    return None


def _is_call_to(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")) == name


def check_sqlalchemy_style(ctx: Context) -> Outcome:
    if (gate := lab4_gate(ctx)) is not None:
        return gate
    mapped, legacy = 0, []
    for source in parse_sources(ctx, "src"):
        if source.tree is None:
            continue
        for node in ast.walk(source.tree):
            mapped += _is_call_to(node, "mapped_column")
            problem = _legacy_sqlalchemy(node)
            if problem:
                legacy.append(f"{relative(ctx, source.path)}: {problem}")
    if legacy:
        return fail(
            "Уберите API SQLAlchemy 1.x: select() вместо query(), DeclarativeBase вместо "
            "declarative_base(), text() для сырого SQL. " + "; ".join(legacy[:3])
        )
    if mapped < MIN_TABLES:
        return fail(
            "Опишите модели в стиле 2.x: поля Mapped[...] = mapped_column(...).",
            detail=f"mapped_column встречается {mapped} раз",
        )
    return ok(f"mapped_column: {mapped}, legacy API нет")


PASSWORD = re.compile(r"password|passwd|парол", re.I)
WEAK_HASHERS = {"bcrypt", "passlib", "crypt"}


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        names = [f"{node.module}.{alias.name}" for alias in node.names]
        return [node.module or "", *names]
    return []


def _password_findings(tree: ast.Module, mentions_passwords: bool) -> tuple[bool, list[str]]:
    """(используется ли Argon2id, список проблем) для одного модуля."""
    argon, problems = False, []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            root = module.split(".")[0]
            argon = argon or root == "argon2" or module.endswith("Argon2Hasher")
            if root in WEAK_HASHERS:
                problems.append(f"import {module}")
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        owner, attr = node.value.id, node.attr
        if owner == "Type" and attr in {"I", "D"}:
            problems.append(f"строка {node.lineno}: Type.{attr} вместо Type.ID")
        if (
            owner == "hashlib"
            and mentions_passwords
            and attr in {"md5", "sha1", "sha256", "sha512"}
        ):
            problems.append(f"строка {node.lineno}: hashlib.{attr} рядом с паролями")
    return argon, problems


def check_passwords(ctx: Context) -> Outcome:
    if (gate := lab4_gate(ctx)) is not None:
        return gate
    sources = [source for source in parse_sources(ctx, "src") if source.tree is not None]
    if not any(PASSWORD.search(source.text) for source in sources):
        return not_applicable("паролей в проекте нет")
    argon, problems = False, []
    for source in sources:
        if source.tree is None:
            continue
        uses, found = _password_findings(source.tree, bool(PASSWORD.search(source.text)))
        argon = argon or uses
        problems += [f"{relative(ctx, source.path)}: {item}" for item in found]
    if problems:
        return fail(
            "Пароли хешируются только Argon2id (argon2-cffi или pwdlib). " + "; ".join(problems[:3])
        )
    if not argon:
        return fail(
            "В коде есть пароли, но нет Argon2id. "
            "Используйте argon2.PasswordHasher (тип ID по умолчанию)."
        )
    return ok("пароли хешируются Argon2id")


# --- Документы ------------------------------------------------------------------------

ER_RELATION = re.compile(r'^\s*"?[\w-]+"?\s+[|}o]{2}(?:--|\.\.)[|{o]{2}\s+"?[\w-]+"?\s*:', re.M)
ER_ENTITY = re.compile(r'^\s*"?([\w-]+)"?\s*(?:\[[^\]]*\])?\s*\{', re.M)


def check_er_diagram(ctx: Context) -> Outcome:
    text = ctx.read(API_DOC)
    if text is None:
        return fail(f"Нет файла {API_DOC}.")
    blocks = [block for block in mermaid_blocks(text) if diagram_type(block) == "erDiagram"]
    if not blocks:
        return fail(f"В {API_DOC} нет блока ```mermaid с типом erDiagram.")
    for block in blocks:
        entities = set(ER_ENTITY.findall(block))
        relations = ER_RELATION.findall(block)
        for line in relations:
            parts = line.split()
            entities |= {parts[0].strip('"'), parts[2].strip('"')}
        if len(entities) >= MIN_TABLES and len(relations) >= 2 and not has_placeholder(block):
            return ok(f"сущностей {len(entities)}, связей {len(relations)}")
    return fail(
        f"ER-диаграмма: ≥ {MIN_TABLES} сущности с атрибутами и ≥ 2 связи "
        "вида USER ||--o{ BOOKING : makes, без TODO."
    )


# --- Зонды приложения --------------------------------------------------------------


def probe(ctx: Context) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        url = fresh_database(ctx, "probe") or _sqlite_url(ctx.tmp / "probe-empty.db")
        target = ctx.tmp / "probe.json"
        proc = run(
            python("-m", "grader.probe_api", ctx.asgi_app, str(target)),
            ctx,
            env={"DATABASE_URL": url},
            timeout=300,
        )
        if not target.is_file():
            return {"error": "зонд не запустился: " + _last_error(proc.output)}
        data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
        return data

    return ctx.memo("lab4:probe", load)


def _probe_or_fail(ctx: Context) -> tuple[dict[str, Any] | None, Outcome | None]:
    data = probe(ctx)
    if data.get("error"):
        return None, fail(
            f"Приложение {ctx.asgi_app} не запустилось (адрес задаётся в [tool.grader] asgi_app).",
            detail=str(data["error"])[:200],
        )
    return data, None


def _operations(schema: dict[str, Any]) -> list[str]:
    result = []
    for path, item in (schema.get("paths") or {}).items():
        for method in item or {}:
            if method in {"get", "post", "put", "patch", "delete"} and "health" not in path:
                result.append(f"{method.upper()} {path}")
    return result


def check_operations(ctx: Context) -> Outcome:
    data, failure = _probe_or_fail(ctx)
    if data is None:
        return failure or fail("нет данных зонда")
    ops = _operations(data.get("openapi") or {})
    if len(ops) >= MIN_OPERATIONS:
        return ok(plural(len(ops), "операция", "операции", "операций"))
    return fail(
        f"В API нужно ≥ {MIN_OPERATIONS} операций над ресурсами предметной области (без /health).",
        detail=f"сейчас {len(ops)}: {', '.join(ops) or '—'}",
    )


def check_openapi_export(ctx: Context) -> Outcome:
    text = ctx.read(OPENAPI_FILE)
    if text is None:
        return fail(f"Нет {OPENAPI_FILE}. Выгрузите схему: uv run python scripts/export_openapi.py")
    try:
        saved = json.loads(text)
    except json.JSONDecodeError as exc:
        return fail(f"{OPENAPI_FILE} — не JSON: {exc.msg}")
    data, failure = _probe_or_fail(ctx)
    if data is None:
        return failure or fail("нет данных зонда")
    if saved != data.get("openapi"):
        return fail(
            "Схема в репозитории устарела: API изменился, а файл нет. "
            "Перевыгрузите: uv run python scripts/export_openapi.py"
        )
    return ok(f"OpenAPI {saved.get('openapi', '?')}, схема актуальна")


def _problem_issue(item: dict[str, Any] | None, expected: set[int]) -> str | None:
    if not item:
        return "запрос не выполнен"
    response = item.get("response", item)
    status = response.get("status")
    if status not in expected:
        return f"статус {status}, ожидался {'/'.join(map(str, sorted(expected)))}"
    if not str(response.get("content_type", "")).startswith(PROBLEM_TYPE):
        return f"Content-Type «{response.get('content_type')}», нужен {PROBLEM_TYPE}"
    body = response.get("body")
    if not isinstance(body, dict):
        return "тело ответа не JSON-объект"
    missing = [key for key in ("type", "title", "status") if key not in body]
    if missing:
        return "в теле нет полей " + ", ".join(missing)
    if body.get("status") != status:
        return f"поле status = {body.get('status')}, а HTTP-статус {status}"
    return None


def _documents_problem(schema: dict[str, Any]) -> bool:
    for item in (schema.get("paths") or {}).values():
        for operation in (item or {}).values():
            if not isinstance(operation, dict):
                continue
            for response in (operation.get("responses") or {}).values():
                if isinstance(response, dict) and PROBLEM_TYPE in (response.get("content") or {}):
                    return True
    return False


def check_problem_details(ctx: Context) -> Outcome:
    data, failure = _probe_or_fail(ctx)
    if data is None:
        return failure or fail("нет данных зонда")
    problems = []
    missing = _problem_issue(data.get("missing"), {404})
    if missing:
        problems.append(f"GET несуществующего пути: {missing}")
    invalid = data.get("invalid")
    if invalid is None:
        problems.append("не нашлось операции, куда можно отправить неверные данные")
    else:
        issue = _problem_issue(invalid, {400, 422})
        if issue:
            problems.append(f"{invalid.get('request')} с неверными данными: {issue}")
    if not _documents_problem(data.get("openapi") or {}):
        problems.append(f"в OpenAPI нет ни одного ответа с {PROBLEM_TYPE}")
    if problems:
        return fail(
            "Ошибки отдавайте по RFC 9457: Content-Type application/problem+json, поля type, "
            "title, status, detail. " + "; ".join(problems[:3])
        )
    return ok("404 и 422 приходят как application/problem+json")


def check_pagination(ctx: Context) -> Outcome:
    data, failure = _probe_or_fail(ctx)
    if data is None:
        return failure or fail("нет данных зонда")
    info = data.get("pagination") or {}
    paginated, bare = info.get("paginated") or [], info.get("bare_arrays") or []
    if bare:
        return fail(
            "Списки без пагинации: " + ", ".join(bare[:3]) + ". Отдавайте объект "
            "{items: [...], next_cursor | total} и принимайте limit + offset или cursor."
        )
    if not paginated:
        return fail(
            "Нет ни одного списочного GET с параметрами limit и offset (или cursor) "
            "и ответом-объектом с полем items."
        )
    runtime = info.get("runtime") or {}
    response = runtime.get("response") or {}
    body = response.get("body")
    if (
        response.get("status") != 200
        or not isinstance(body, dict)
        or not isinstance(body.get("items"), list)
    ):
        return fail(
            f"{runtime.get('request')}: ожидался 200 и объект с items.",
            detail=f"статус {response.get('status')}",
        )
    if len(body["items"]) > 1:
        return fail(
            f"{runtime.get('request')} вернул {len(body['items'])} элементов: limit не работает."
        )
    return ok(", ".join(item["op"] for item in paginated[:3]))


CONTRACT_TEST = """
import importlib

import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import allow_header_conformance, positive_data_acceptance

_module, _, _attr = {spec!r}.partition(":")
_target = getattr(importlib.import_module(_module), _attr.removesuffix("()") or "app")
app = _target() if _attr.endswith("()") else _target
schema = schemathesis.openapi.from_asgi("/openapi.json", app)


@schema.parametrize()
@settings(
    max_examples={examples},
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=list(HealthCheck),
)
def test_contract(case):
    case.call_and_validate(excluded_checks=[allow_header_conformance, positive_data_acceptance])
"""


def check_contract(ctx: Context) -> Outcome:
    if (gate := lab4_gate(ctx)) is not None:
        return gate
    if not module_available("schemathesis"):
        return fail("Schemathesis не установлен: выполните uv sync.")
    url = fresh_database(ctx, "contract")
    if url is None:
        return fail("Миграции не применились: контракт проверять не на чем.")
    folder = ctx.tmp / "contract"
    folder.mkdir(exist_ok=True)
    (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    test_file = folder / "test_grader_contract.py"
    test_file.write_text(
        CONTRACT_TEST.format(spec=ctx.asgi_app, examples=CONTRACT_EXAMPLES), encoding="utf-8"
    )
    proc = run(
        python(
            "-m",
            "pytest",
            str(test_file),
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(folder / "pytest.ini"),
            "--rootdir",
            str(folder),
        ),
        ctx,
        env={"DATABASE_URL": url},
        timeout=900,
    )
    if proc.returncode == 0:
        passed = re.search(r"(\d+) passed", proc.output)
        return ok(f"операций проверено: {passed.group(1) if passed else '?'}")
    operations = re.findall(r"^(?:SUB)?FAILED \S+\[(.+?)\]", proc.output, re.M)
    codes = list(dict.fromkeys(re.findall(r"- (ST\d+ [^\n|]+)", proc.output)))
    detail = "; ".join(codes[:3]) or _last_error(proc.output)
    return fail(
        "Schemathesis нашёл расхождения API и схемы"
        + (f" в {', '.join(dict.fromkeys(operations))}" if operations else "")
        + ". Повторите локально: uv run pytest -m contract",
        detail=detail[:300],
    )


def lab4_gate(ctx: Context) -> Outcome | None:
    """Проверки, которые проходят на шаблоне сами собой, ждут минимального объёма своей работы."""
    trip = round_trip_sqlite(ctx)
    data = probe(ctx)
    ops = _operations(data.get("openapi") or {}) if not data.get("error") else []
    if len(trip.tables) >= MIN_TABLES and len(ops) >= MIN_OPERATIONS:
        return None
    return fail(
        f"Засчитывается, когда в базе ≥ {MIN_TABLES} таблицы и в API ≥ {MIN_OPERATIONS} операций "
        "(проверки «БД: …» и «API: …»).",
        detail=f"сейчас таблиц {len(trip.tables)}, операций {len(ops)}",
    )


LAB = Lab(
    "lab4",
    "ЛР4. API и база данных",
    (
        Check("report", "Отчёт: раздел ЛР4 в README.md заполнен", 3, lambda c: check_report(c, 4)),
        Check("er", "Документ: ER-диаграмма (Mermaid) в docs/api.md", 3, check_er_diagram),
        Check(
            "migrations",
            "Alembic: ≥ 2 миграции, upgrade → downgrade → upgrade на SQLite",
            8,
            check_migrations,
        ),
        Check(
            "alembic_check",
            "Alembic: модели совпадают с миграциями (alembic check)",
            4,
            check_alembic_check,
            gate=GATE,
        ),
        Check(
            "postgres",
            "Alembic: миграции проходят на PostgreSQL",
            4,
            check_postgres,
            ci_only=True,
            gate=GATE,
        ),
        Check("tables", "БД: ≥ 3 таблицы и внешний ключ", 4, check_tables),
        Check(
            "sqlalchemy",
            "SQLAlchemy 2.x: Mapped и mapped_column, без legacy API",
            3,
            check_sqlalchemy_style,
            gate=GATE,
        ),
        Check(
            "openapi",
            "OpenAPI: схема выгружена в docs/api/openapi.json и актуальна",
            4,
            check_openapi_export,
        ),
        Check("operations", "API: ≥ 6 операций (без health)", 4, check_operations),
        Check("problems", "Ошибки: RFC 9457, application/problem+json", 7, check_problem_details),
        Check("pagination", "Пагинация: limit + offset или cursor в списках", 5, check_pagination),
        Check("contract", "Контракт: Schemathesis без ошибок", 8, check_contract, gate=GATE),
        Check(
            "passwords",
            "Пароли: Argon2id, если есть аутентификация",
            3,
            check_passwords,
            gate=GATE,
        ),
    ),
)
