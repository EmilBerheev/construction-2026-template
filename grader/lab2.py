"""ЛР2. Принципы и паттерны проектирования."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from grader.common import check_report, collect_tests, failed_tests, relative, run_suite
from grader.core import Check, Context, Lab, Outcome, fail, ok
from grader.util import annotation_names, clean_cell, has_placeholder, parse_sources, plural, tables

PATTERNS_DOC = "docs/patterns.md"
MIN_PATTERNS = 3
MIN_ABSTRACTIONS = 3
MIN_INJECTED = 3
MIN_TESTS = 15
MIN_COVERAGE = 70.0
MIN_WHY_WORDS = 5

GROUP_NAMES = {
    "creational": "порождающий",
    "structural": "структурный",
    "behavioral": "поведенческий",
}

# Каталог GoF: псевдоним → (каноническое имя, группа)
GOF: dict[str, tuple[str, str]] = {}
for _group, _patterns in {
    "creational": {
        "Abstract Factory": ["абстрактная фабрика"],
        "Builder": ["строитель"],
        "Factory Method": ["фабричный метод"],
        "Prototype": ["прототип"],
        "Singleton": ["одиночка"],
    },
    "structural": {
        "Adapter": ["адаптер"],
        "Bridge": ["мост"],
        "Composite": ["компоновщик"],
        "Decorator": ["декоратор"],
        "Facade": ["фасад"],
        "Flyweight": ["приспособленец", "легковес"],
        "Proxy": ["заместитель", "прокси"],
    },
    "behavioral": {
        "Chain of Responsibility": ["цепочка обязанностей"],
        "Command": ["команда"],
        "Interpreter": ["интерпретатор"],
        "Iterator": ["итератор"],
        "Mediator": ["посредник"],
        "Memento": ["снимок", "хранитель"],
        "Observer": ["наблюдатель"],
        "State": ["состояние"],
        "Strategy": ["стратегия"],
        "Template Method": ["шаблонный метод"],
        "Visitor": ["посетитель"],
    },
}.items():
    for _name, _aliases in _patterns.items():
        for _alias in [_name.lower(), *_aliases]:
            GOF[_alias] = (_name, _group)


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").replace("-", " ").split())


def find_pattern(cell: str) -> tuple[str, str] | None:
    text = _normalize(cell)
    for alias in sorted(GOF, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(alias.replace('ё', 'е'))}(?!\w)", text):
            return GOF[alias]
    return None


def find_group(cell: str) -> str | None:
    text = _normalize(cell)
    if text.startswith(("порожд", "creational")):
        return "creational"
    if text.startswith(("структур", "structural")):
        return "structural"
    if text.startswith(("поведен", "behavio")):
        return "behavioral"
    return None


@dataclass(frozen=True)
class PatternRow:
    pattern: str
    group: str
    file: str
    classes: list[str]
    why: str
    problem: str | None


def pattern_rows(ctx: Context) -> list[PatternRow] | None:
    def load() -> list[PatternRow] | None:
        text = ctx.read(PATTERNS_DOC)
        if text is None:
            return None
        rows: list[PatternRow] = []
        for table in tables(text):
            columns = [
                table.column("паттерн"),
                table.column("групп"),
                table.column("файл"),
                table.column("класс"),
                table.column("зачем"),
            ]
            if any(index is None for index in columns):
                continue
            p_col, g_col, f_col, c_col, w_col = (index or 0 for index in columns)
            for raw in table.rows:
                cells = [clean_cell(cell) for cell in raw]
                if not cells[p_col] or has_placeholder(" ".join(raw)):
                    continue
                rows.append(
                    _row(cells[p_col], cells[g_col], cells[f_col], cells[c_col], cells[w_col])
                )
        return rows

    return ctx.memo("lab2:patterns", load)


def _row(pattern_cell: str, group_cell: str, file: str, class_cell: str, why: str) -> PatternRow:
    classes = [name for name in re.split(r"[,;/\s]+", class_cell) if name and name != "и"]
    found = find_pattern(pattern_cell)
    group = find_group(group_cell)
    problem = None
    if found is None:
        problem = f"«{pattern_cell}» нет в каталоге GoF"
        name, real_group = pattern_cell, group or ""
    else:
        name, real_group = found
        if group is None:
            problem = (
                f"{name}: в колонке «Группа» нужно «порождающий», «структурный» или «поведенческий»"
            )
        elif group != real_group:
            problem = f"{name} — {GROUP_NAMES[real_group]}, а в таблице «{group_cell}»"
    if problem is None and len(why.split()) < MIN_WHY_WORDS:
        problem = f"{name}: в колонке «Зачем» нужно ≥ {MIN_WHY_WORDS} слов"
    return PatternRow(name, real_group, file, classes, why, problem)


def check_table(ctx: Context) -> Outcome:
    rows = pattern_rows(ctx)
    if rows is None:
        return fail(f"Нет файла {PATTERNS_DOC}.")
    valid = [row for row in rows if row.problem is None]
    problems = [row.problem for row in rows if row.problem]
    if len(valid) >= MIN_PATTERNS:
        return ok(", ".join(row.pattern for row in valid))
    hint = (
        f"Нужно ≥ {MIN_PATTERNS} строк таблицы «Паттерн | Группа | Файл | Класс | Зачем» "
        "с паттернами из каталога GoF."
    )
    if problems:
        hint += " " + "; ".join(p for p in problems[:3] if p) + "."
    return fail(hint, detail=f"сейчас {len(valid)}")


def check_groups(ctx: Context) -> Outcome:
    rows = pattern_rows(ctx) or []
    groups = {row.group for row in rows if row.problem is None}
    missing = [GROUP_NAMES[group] for group in GROUP_NAMES if group not in groups]
    if not missing:
        return ok("порождающий, структурный, поведенческий")
    return fail("Нужны паттерны из всех трёх групп GoF. Не хватает: " + ", ".join(missing) + ".")


def _classes_in(ctx: Context, file: str) -> set[str] | None:
    path = ctx.path(file)
    if not file or not path.is_file() or path.suffix != ".py":
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def check_code_exists(ctx: Context) -> Outcome:
    rows = [row for row in (pattern_rows(ctx) or []) if row.problem is None]
    if len(rows) < MIN_PATTERNS:
        return fail(f"Сначала заполните таблицу в {PATTERNS_DOC} (≥ {MIN_PATTERNS} паттерна).")
    problems = []
    for row in rows:
        if not row.file.startswith("src/"):
            problems.append(f"{row.pattern}: путь «{row.file}» должен начинаться с src/")
            continue
        classes = _classes_in(ctx, row.file)
        if classes is None:
            problems.append(f"{row.pattern}: файла {row.file} нет или он не разбирается")
            continue
        missing = [name for name in row.classes if name not in classes]
        if not row.classes or missing:
            problems.append(f"{row.pattern}: в {row.file} нет класса {', '.join(missing) or '—'}")
    if problems:
        return fail("; ".join(problems[:4]) + ".")
    total = sum(len(row.classes) for row in rows)
    return ok(plural(total, "класс найден", "класса найдены", "классов найдено"))


def check_patterns_tested(ctx: Context) -> Outcome:
    rows = [row for row in (pattern_rows(ctx) or []) if row.problem is None]
    if len(rows) < MIN_PATTERNS:
        return fail(f"Сначала заполните таблицу в {PATTERNS_DOC}.")
    tests_text = "\n".join(source.text for source in parse_sources(ctx, "tests"))
    untested = [
        row.pattern
        for row in rows
        if not any(re.search(rf"\b{re.escape(name)}\b", tests_text) for name in row.classes)
    ]
    if untested:
        return fail(
            "Каждый паттерн из таблицы должен проверяться тестом: класс из колонки «Класс» "
            "должен встречаться в tests/. Без теста: " + ", ".join(untested) + "."
        )
    return ok("у каждого паттерна есть тест")


# --- SOLID ----------------------------------------------------------------------------


def _is_abstraction(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
        if name in {"Protocol", "ABC"}:
            return True
    return any(
        keyword.arg == "metaclass" and "ABCMeta" in annotation_names(keyword.value)
        for keyword in node.keywords
    )


def abstractions(ctx: Context) -> dict[str, str]:
    def load() -> dict[str, str]:
        found = {}
        for source in parse_sources(ctx, "src"):
            if source.tree is None:
                continue
            for node in ast.walk(source.tree):
                if isinstance(node, ast.ClassDef) and _is_abstraction(node):
                    found[node.name] = relative(ctx, source.path)
        return found

    return ctx.memo("lab2:abstractions", load)


def check_abstractions(ctx: Context) -> Outcome:
    broken = [source.error for source in parse_sources(ctx, "src") if source.error]
    if broken:
        return fail("Код не разбирается: " + "; ".join(e for e in broken[:2] if e))
    found = abstractions(ctx)
    if len(found) >= MIN_ABSTRACTIONS:
        return ok(", ".join(sorted(found)[:6]))
    return fail(
        f"Нужно ≥ {MIN_ABSTRACTIONS} абстракции: классы-наследники typing.Protocol или abc.ABC "
        "(порты репозиториев, стратегии, уведомления…).",
        detail=f"сейчас {len(found)}: {', '.join(sorted(found)) or '—'}",
    )


def _is_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if "dataclass" in annotation_names(target):
            return True
    return False


def _constructor_types(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            arguments = item.args
            for arg in [*arguments.posonlyargs, *arguments.args[1:], *arguments.kwonlyargs]:
                names |= annotation_names(arg.annotation)
    if _is_dataclass(node):
        for item in node.body:
            if isinstance(item, ast.AnnAssign):
                names |= annotation_names(item.annotation)
    return names


def check_injection(ctx: Context) -> Outcome:
    ports = set(abstractions(ctx))
    if not ports:
        return fail(
            "Сначала заведите абстракции (Protocol или ABC), от которых будут зависеть классы."
        )
    injected: list[str] = []
    for source in parse_sources(ctx, "src"):
        if source.tree is None:
            continue
        for node in ast.walk(source.tree):
            if isinstance(node, ast.ClassDef) and node.name not in ports:
                used = _constructor_types(node) & ports
                if used:
                    injected.append(f"{node.name}({', '.join(sorted(used))})")
    if len(injected) >= MIN_INJECTED:
        return ok(", ".join(injected[:4]))
    return fail(
        f"Нужно ≥ {MIN_INJECTED} класса, которые получают абстракцию через конструктор: "
        "def __init__(self, repo: OrderRepository) или поле dataclass с типом-протоколом.",
        detail=f"сейчас {len(injected)}: {', '.join(injected) or '—'}",
    )


FORBIDDEN_IN_DOMAIN = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "pydantic",
    "alembic",
    "httpx",
    "httpx2",
    "requests",
    "uvicorn",
    "psycopg",
    "flask",
    "django",
}


def _resolve(source_parts: list[str], node: ast.ImportFrom) -> list[str]:
    """Абсолютные имена модулей для from-импорта, включая относительные (from ..db import x)."""
    if node.level == 0:
        base = node.module or ""
    else:
        package = source_parts[:-1]
        parent = package[: len(package) - (node.level - 1)]
        base = ".".join([*parent, node.module] if node.module else parent)
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def check_domain_clean(ctx: Context) -> Outcome:
    package = ctx.package
    domain = ctx.path(f"src/{package}/domain")
    if not domain.is_dir():
        return fail(f"Нет пакета src/{package}/domain: доменный слой должен лежать отдельно.")
    inner = {f"{package}.api", f"{package}.db", f"{package}.adapters"}
    violations = []
    for source in parse_sources(ctx, "src"):
        if source.tree is None or domain not in source.path.parents:
            continue
        parts = list(source.path.relative_to(ctx.path("src")).with_suffix("").parts)
        for node in ast.walk(source.tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = _resolve(parts, node)
            for module in modules:
                root = module.split(".")[0]
                if root in FORBIDDEN_IN_DOMAIN or any(
                    module == layer or module.startswith(layer + ".") for layer in inner
                ):
                    violations.append(f"{relative(ctx, source.path)}: import {module}")
    if violations:
        return fail(
            "Домен не должен зависеть от фреймворков и внешних слоёв (api, db, adapters): "
            + "; ".join(violations[:3])
        )
    return ok(f"src/{package}/domain импортирует только стандартную библиотеку и себя")


# --- Тесты ---------------------------------------------------------------------------


def own_tests(ctx: Context) -> list[str]:
    return [node for node in collect_tests(ctx).node_ids if not node.startswith("tests/contract/")]


def check_tests(ctx: Context) -> Outcome:
    collected = collect_tests(ctx)
    if not collected.ok:
        return fail(
            "pytest не смог собрать тесты: " + collected.output.strip().splitlines()[-1][:200]
        )
    count = len(own_tests(ctx))
    if count < MIN_TESTS:
        return fail(
            f"Нужно ≥ {MIN_TESTS} тестов (без tests/contract): юнит-тесты домена, "
            "паттернов и сервисов.",
            detail=f"сейчас {count}",
        )
    suite = run_suite(ctx)
    if suite.returncode != 0:
        names = failed_tests(suite.output)
        return fail(
            "Не все тесты проходят. Запустите uv run pytest и почините."
            + (f" Упали: {names}." if names else ""),
            detail=suite.summary,
        )
    return ok(f"{count} тестов; {suite.summary}")


def check_coverage(ctx: Context) -> Outcome:
    suite = run_suite(ctx)
    if suite.coverage is None:
        return fail("Покрытие не посчитано: нет pytest-cov или тесты не запустились (uv sync).")
    if suite.coverage >= MIN_COVERAGE:
        return ok(f"{suite.coverage:.1f} %")
    return fail(
        f"Покрытие src/ должно быть ≥ {MIN_COVERAGE:.0f} %. "
        "Посмотрите непокрытые строки: uv run pytest --cov=src --cov-report=term-missing",
        detail=f"сейчас {suite.coverage:.1f} %",
    )


LAB = Lab(
    "lab2",
    "ЛР2. Принципы и паттерны проектирования",
    (
        Check("report", "Отчёт: раздел ЛР2 в README.md заполнен", 3, lambda c: check_report(c, 2)),
        Check(
            "patterns_table",
            "Паттерны: таблица в docs/patterns.md (≥ 3 паттерна GoF)",
            5,
            check_table,
        ),
        Check("patterns_groups", "Паттерны: все три группы GoF", 6, check_groups),
        Check(
            "patterns_code",
            "Паттерны: файлы и классы из таблицы есть в коде",
            10,
            check_code_exists,
        ),
        Check(
            "patterns_tested", "Паттерны: у каждого паттерна есть тест", 3, check_patterns_tested
        ),
        Check("abstractions", "SOLID: ≥ 3 абстракции (Protocol или ABC)", 5, check_abstractions),
        Check(
            "injection",
            "SOLID: ≥ 3 класса получают абстракции через конструктор",
            6,
            check_injection,
        ),
        Check(
            "domain_clean", "Домен не зависит от фреймворков и внешних слоёв", 4, check_domain_clean
        ),
        Check("tests", "Тесты: ≥ 15 тестов, все проходят", 10, check_tests),
        Check("coverage", "Тесты: покрытие src/ ≥ 70 %", 8, check_coverage),
    ),
)
