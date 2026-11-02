"""ЛР3. Качество кода и рефакторинг."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grader.common import (
    MIN_CODE_LINES,
    check_report,
    code_gate,
    collect_tests,
    failed_tests,
    relative,
    run_tests_in,
)
from grader.core import Check, Context, Lab, Outcome, fail, ok
from grader.util import (
    changed_files,
    clean_cell,
    commit_subject,
    first_commit_adding,
    first_lines,
    has_placeholder,
    is_ancestor,
    is_git_repo,
    module_available,
    parse_sources,
    plural,
    python,
    resolve_commit,
    run,
    tables,
)

REFACTORING_DOC = "docs/refactoring.md"
CHARACTERIZATION_DIR = "tests/characterization"
MIN_ENTRIES = 5
MIN_CHARACTERIZATION = 3
MAX_SUPPRESSIONS = 5
MAX_COMPLEXITY = 10
GATE = f"когда в src/ ≥ {MIN_CODE_LINES} строк кода"

SUPPRESSION = re.compile(r"#\s*(noqa\b|type:\s*ignore\b|pyright:\s*ignore\b|mypy:\s*ignore)", re.I)
HASH = re.compile(r"\b[0-9a-f]{7,40}\b")


def _lint_paths(ctx: Context) -> list[str]:
    return [name for name in ("src", "tests", "migrations", "scripts") if ctx.path(name).is_dir()]


def _ruff(ctx: Context, *args: str) -> tuple[int, str]:
    if not module_available("ruff"):
        return 127, "ruff не установлен: выполните uv sync"
    proc = run(python("-m", "ruff", *args), ctx)
    return proc.returncode, proc.output


def check_ruff(ctx: Context) -> Outcome:
    if (gate := code_gate(ctx)) is not None:
        return gate
    code, output = _ruff(
        ctx, "check", "--no-cache", "--output-format", "concise", *_lint_paths(ctx)
    )
    if code == 0:
        return ok("замечаний нет")
    issues = [line for line in output.splitlines() if re.search(r":\d+:\d+: [A-Z]+\d+", line)]
    return fail(
        "Исправьте замечания: uv run ruff check --fix, остальное руками. "
        + first_lines("\n".join(issues) or output, 3),
        detail=plural(len(issues), "замечание", "замечания", "замечаний"),
    )


def check_format(ctx: Context) -> Outcome:
    if (gate := code_gate(ctx)) is not None:
        return gate
    code, output = _ruff(ctx, "format", "--check", "--no-cache", *_lint_paths(ctx))
    if code == 0:
        return ok("форматирование в порядке")
    # ruff 0.17 печатает «--> файл:строка:столбец», старые версии — «Would reformat: файл».
    files = re.findall(r"^\s*--> (.+?):\d+:\d+", output, re.M)
    files += re.findall(r"^Would reformat: (.+)$", output, re.M)
    unique = list(dict.fromkeys(name.strip().replace("\\", "/") for name in files))
    return fail(
        "Отформатируйте код: uv run ruff format.",
        detail=", ".join(unique[:3]) or first_lines(output, 2),
    )


def check_complexity(ctx: Context) -> Outcome:
    if (gate := code_gate(ctx)) is not None:
        return gate
    # Отдельный запуск без конфигурации проекта и без noqa: порог одинаков для всех.
    code, output = _ruff(
        ctx,
        "check",
        "src",
        "--isolated",
        "--no-cache",
        "--ignore-noqa",
        "--select",
        "C901",
        "--config",
        f"lint.mccabe.max-complexity = {MAX_COMPLEXITY}",
        "--output-format",
        "concise",
    )
    if code == 0:
        return ok(f"у всех функций сложность ≤ {MAX_COMPLEXITY}")
    hot = re.findall(r"`(\w+)` is too complex \((\d+) > \d+\)", output)
    shown = ", ".join(f"{name} ({value})" for name, value in hot[:4])
    return fail(
        f"Упростите функции со сложностью > {MAX_COMPLEXITY}: Extract Method, "
        "Replace Conditional with Polymorphism, таблица вместо if/elif.",
        detail=shown or first_lines(output, 2),
    )


def _pyright_configured(ctx: Context) -> bool:
    tool: dict[str, Any] = ctx.pyproject.get("tool", {})
    return "pyright" in tool or ctx.path("pyrightconfig.json").is_file()


def check_types(ctx: Context) -> Outcome:
    if (gate := code_gate(ctx)) is not None:
        return gate
    if _pyright_configured(ctx):
        mode = str(ctx.pyproject.get("tool", {}).get("pyright", {}).get("typeCheckingMode", ""))
        if ctx.path("pyrightconfig.json").is_file():
            mode = "strict" if '"strict"' in (ctx.read("pyrightconfig.json") or "") else mode
        if mode != "strict":
            return fail('Для pyright нужен typeCheckingMode = "strict" в [tool.pyright].')
        if not module_available("pyright"):
            return fail("Настроен pyright, но пакета нет: uv add --dev pyright.")
        proc = run(python("-m", "pyright", "src"), ctx)
        tool_name = "pyright (strict)"
    else:
        if not module_available("mypy"):
            return fail("mypy не установлен: выполните uv sync.")
        proc = run(python("-m", "mypy", "--strict", "src"), ctx)
        tool_name = "mypy --strict"
    if proc.returncode == 0:
        return ok(f"{tool_name}: ошибок нет")
    errors = [line for line in proc.output.splitlines() if re.search(r":\d+(:\d+)?: error", line)]
    errors = errors or [line for line in proc.output.splitlines() if "error" in line]
    return fail(
        f"{tool_name} нашёл ошибки. Добавьте аннотации и уберите Any. "
        + first_lines("\n".join(errors), 2),
        detail=plural(len(errors), "ошибка", "ошибки", "ошибок"),
    )


def check_suppressions(ctx: Context) -> Outcome:
    if (gate := code_gate(ctx)) is not None:
        return gate
    found = []
    for source in parse_sources(ctx, "src"):
        for number, line in enumerate(source.text.splitlines(), start=1):
            if SUPPRESSION.search(line):
                found.append(f"{relative(ctx, source.path)}:{number}")
    if len(found) <= MAX_SUPPRESSIONS:
        return ok(plural(len(found), "подавление", "подавления", "подавлений"))
    return fail(
        f"Не больше {MAX_SUPPRESSIONS} комментариев noqa / type: ignore в src/. "
        "Исправьте причину замечания, а не прячьте его.",
        detail=f"{len(found)}: {', '.join(found[:4])}",
    )


# --- Характеризационные тесты и журнал --------------------------------------------


def check_characterization(ctx: Context) -> Outcome:
    if not ctx.path(CHARACTERIZATION_DIR).is_dir():
        return fail(
            f"Нет папки {CHARACTERIZATION_DIR}/. До рефакторинга зафиксируйте текущее поведение "
            "тестами (golden master)."
        )
    ids = [
        node for node in collect_tests(ctx).node_ids if node.startswith(CHARACTERIZATION_DIR + "/")
    ]
    if len(ids) < MIN_CHARACTERIZATION:
        return fail(
            f"Нужно ≥ {MIN_CHARACTERIZATION} теста в {CHARACTERIZATION_DIR}/.",
            detail=f"сейчас {len(ids)}",
        )
    suite = run_tests_in(ctx, CHARACTERIZATION_DIR)
    if suite.returncode != 0:
        return fail(
            "Характеризационные тесты должны проходить и после рефакторинга: "
            "поведение не меняется.",
            detail=failed_tests(suite.output) or suite.summary,
        )
    return ok(plural(len(ids), "тест", "теста", "тестов"))


@dataclass(frozen=True)
class Entry:
    smell: str
    technique: str
    ref: str


def log_entries(ctx: Context) -> list[Entry] | None:
    def load() -> list[Entry] | None:
        text = ctx.read(REFACTORING_DOC)
        if text is None:
            return None
        entries = []
        for table in tables(text):
            smell, technique, commit = (
                table.column("запах"),
                table.column("техник"),
                table.column("коммит"),
            )
            if smell is None or technique is None or commit is None:
                continue
            for row in table.rows:
                if has_placeholder(" ".join(row)):
                    continue
                cells = [clean_cell(cell) for cell in row]
                match = HASH.search(cells[commit].lower())
                if cells[smell] and cells[technique] and match:
                    entries.append(Entry(cells[smell], cells[technique], match.group(0)))
        return entries

    return ctx.memo("lab3:log", load)


def check_log(ctx: Context) -> Outcome:
    entries = log_entries(ctx)
    if entries is None:
        return fail(f"Нет файла {REFACTORING_DOC}.")
    if len(entries) >= MIN_ENTRIES:
        return ok(plural(len(entries), "запись", "записи", "записей"))
    return fail(
        f"Нужно ≥ {MIN_ENTRIES} строк таблицы «№ | Запах кода | Где | Техника | Коммит | "
        "Что изменилось» с хешем коммита (7–40 символов).",
        detail=f"сейчас {len(entries)}",
    )


@dataclass(frozen=True)
class Verified:
    good: list[str]
    problems: list[str]


def verify_hashes(ctx: Context) -> Verified:
    def load() -> Verified:
        good: list[str] = []
        problems: list[str] = []
        for entry in log_entries(ctx) or []:
            sha = resolve_commit(ctx, entry.ref)
            if sha is None:
                problems.append(f"{entry.ref}: такого коммита нет (rebase или squash меняют хеши)")
            elif not is_ancestor(ctx, sha, "HEAD"):
                problems.append(f"{entry.ref}: коммит не входит в историю текущей ветки")
            elif not commit_subject(ctx, sha).startswith("refactor"):
                problems.append(f"{entry.ref}: сообщение должно начинаться с refactor")
            elif not any(name.startswith("src/") for name in changed_files(ctx, sha)):
                problems.append(f"{entry.ref}: коммит не меняет src/")
            elif sha in good:
                problems.append(f"{entry.ref}: один коммит указан дважды")
            else:
                good.append(sha)
        return Verified(good, problems)

    return ctx.memo("lab3:hashes", load)


def check_hashes(ctx: Context) -> Outcome:
    if not is_git_repo(ctx):
        return fail("Это не git-репозиторий.")
    entries = log_entries(ctx) or []
    if len(entries) < MIN_ENTRIES:
        return fail(f"Сначала заполните журнал: ≥ {MIN_ENTRIES} записей с хешами.")
    verified = verify_hashes(ctx)
    if not verified.problems and len(verified.good) >= MIN_ENTRIES:
        return ok(
            plural(len(verified.good), "коммит проверен", "коммита проверены", "коммитов проверено")
        )
    return fail(
        "Каждая запись журнала — отдельный коммит refactor(...): … в истории ветки, меняющий src/. "
        + "; ".join(verified.problems[:3]),
        detail=f"подтверждено {len(verified.good)} из {len(entries)}",
    )


def check_characterization_first(ctx: Context) -> Outcome:
    if not is_git_repo(ctx):
        return fail("Это не git-репозиторий.")
    first = first_commit_adding(ctx, CHARACTERIZATION_DIR)
    if first is None:
        return fail(f"В истории нет коммита, который добавил {CHARACTERIZATION_DIR}/.")
    verified = verify_hashes(ctx)
    if not verified.good:
        return fail("Нет подтверждённых коммитов рефакторинга в журнале — сравнивать не с чем.")
    late = [sha[:7] for sha in verified.good if sha == first or not is_ancestor(ctx, first, sha)]
    if late:
        return fail(
            "Характеризационные тесты должны появиться в истории раньше первого рефакторинга "
            "(отдельным коммитом test: …).",
            detail=f"тесты в {first[:7]}, а рефакторинг раньше или там же: {', '.join(late)}",
        )
    return ok(f"тесты в {first[:7]}, рефакторинг после")


# --- Quality gate в CI --------------------------------------------------------------

GATE_COMMANDS = {
    "ruff check": re.compile(r"ruff\s+check"),
    "ruff format --check": re.compile(r"ruff\s+format\s+(?:[^\n]*\s)?--(check|diff)"),
    "mypy или pyright": re.compile(r"\b(mypy|pyright)\b"),
    "pytest": re.compile(r"\bpytest\b"),
}


def _workflow_runs(path: Path) -> tuple[str, bool, str | None]:
    """Тексты всех run-шагов, есть ли триггер push/pull_request, ошибка разбора."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        import yaml
    except ImportError:
        triggers = bool(re.search(r"\b(push|pull_request)\b", text))
        return text, triggers, None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return "", False, f"YAML не разбирается: {str(exc).splitlines()[0]}"
    if not isinstance(data, dict):
        return "", False, "YAML не похож на workflow"
    on = data.get("on", data.get(True))  # PyYAML читает ключ on как True
    if isinstance(on, str):
        triggers = on in {"push", "pull_request"}
    elif isinstance(on, list | dict):
        triggers = any(name in on for name in ("push", "pull_request"))
    else:
        triggers = False
    runs = []
    for job in (data.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                runs.append(step["run"])
    return "\n".join(runs), triggers, None


def check_quality_gate(ctx: Context) -> Outcome:
    directory = ctx.path(".github/workflows")
    candidates = sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in directory.glob(pattern)
        if path.stem != "grade"
    )
    if not candidates:
        return fail(
            "Добавьте свой workflow .github/workflows/ci.yml (grade.yml не считается) с шагами "
            "ruff check, ruff format --check, mypy и pytest."
        )
    problems = []
    for path in candidates:
        runs, triggers, error = _workflow_runs(path)
        if error:
            problems.append(f"{path.name}: {error}")
            continue
        missing = [name for name, pattern in GATE_COMMANDS.items() if not pattern.search(runs)]
        if not triggers:
            missing.append("триггер on: push или pull_request")
        if not missing:
            return ok(f"{path.name}: ruff, формат, типы и тесты на каждый push")
        problems.append(f"{path.name}: нет {', '.join(missing)}")
    return fail("Quality gate неполный. " + "; ".join(problems[:2]))


LAB = Lab(
    "lab3",
    "ЛР3. Качество кода и рефакторинг",
    (
        Check("report", "Отчёт: раздел ЛР3 в README.md заполнен", 3, lambda c: check_report(c, 3)),
        Check("ruff", "Ruff: ruff check без замечаний", 7, check_ruff, gate=GATE),
        Check("format", "Ruff: ruff format --check без изменений", 4, check_format, gate=GATE),
        Check(
            "complexity", "Сложность: у каждой функции ≤ 10 (C901)", 6, check_complexity, gate=GATE
        ),
        Check(
            "types",
            "Типы: mypy --strict (или pyright strict) без ошибок",
            9,
            check_types,
            gate=GATE,
        ),
        Check(
            "suppressions",
            "Подавления: ≤ 5 noqa / type: ignore в src/",
            3,
            check_suppressions,
            gate=GATE,
        ),
        Check(
            "characterization",
            "Характеризационные тесты: ≥ 3 в tests/characterization/, проходят",
            5,
            check_characterization,
        ),
        Check(
            "characterization_first",
            "Характеризационные тесты появились раньше рефакторинга",
            5,
            check_characterization_first,
        ),
        Check("log", "Журнал: ≥ 5 записей в docs/refactoring.md", 5, check_log),
        Check(
            "hashes", "Журнал: коммиты существуют, это refactor и они меняют src/", 7, check_hashes
        ),
        Check("gate", "CI: свой workflow с ruff, форматом, типами и pytest", 6, check_quality_gate),
    ),
)
