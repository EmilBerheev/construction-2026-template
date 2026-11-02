"""Проверки, общие для нескольких лабораторных."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from grader.core import Context, Outcome, fail, ok
from grader.util import (
    first_lines,
    has_placeholder,
    logical_lines,
    module_available,
    parse_sources,
    plural,
    python,
    run,
    sections,
    word_count,
)

REPORT_MIN_WORDS = 120
NODE_ID = re.compile(r"^[\w./\-]+\.py::\S")


def check_report(ctx: Context, number: int) -> Outcome:
    text = ctx.read("README.md")
    if text is None:
        return fail("Нет README.md в корне репозитория: это ваш отчёт.")
    pattern = re.compile(rf"^(ЛР|Лабораторная работа)\s*№?\s*{number}\b", re.I)
    body = next((body for title, body in sections(text, 2) if pattern.match(title)), None)
    if body is None:
        return fail(f"В README.md нет раздела «## ЛР{number}». Не удаляйте заголовки из шаблона.")
    if has_placeholder(body):
        return fail(f"В разделе ЛР{number} осталась заглушка TODO. Замените её своим текстом.")
    words = word_count(body)
    if words < REPORT_MIN_WORDS:
        return fail(
            f"Раздел ЛР{number} слишком короткий: нужно ≥ {REPORT_MIN_WORDS} слов "
            "(что сделано, ключевые решения, что было сложно, выводы).",
            detail=f"сейчас {words}",
        )
    return ok(plural(words, "слово", "слова", "слов"))


# --- Объём собственного кода (порог для проверок качества) -------------------------

MIN_CODE_LINES = 400


def code_lines(ctx: Context) -> int:
    return ctx.memo(
        "loc:src", lambda: sum(logical_lines(source) for source in parse_sources(ctx, "src"))
    )


def code_gate(ctx: Context) -> Outcome | None:
    """None, если кода достаточно; иначе готовый отказ с объяснением."""
    lines = code_lines(ctx)
    if lines >= MIN_CODE_LINES:
        return None
    return fail(
        f"Проверка засчитывается, когда в src/ есть ваш код: ≥ {MIN_CODE_LINES} строк "
        "(без пустых, комментариев и docstring). Шаблон чистый сам по себе, поэтому "
        "до ЛР2 эта проверка не даёт баллов.",
        detail=f"сейчас {lines}",
    )


# --- pytest ---------------------------------------------------------------------


@dataclass(frozen=True)
class Collected:
    ok: bool
    node_ids: list[str]
    output: str


@dataclass(frozen=True)
class Suite:
    returncode: int
    summary: str
    coverage: float | None
    output: str


def collect_tests(ctx: Context) -> Collected:
    def load() -> Collected:
        if not module_available("pytest"):
            return Collected(False, [], "pytest не установлен: выполните uv sync")
        proc = run(python("-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"), ctx)
        ids = [line.strip() for line in proc.output.splitlines() if NODE_ID.match(line)]
        # Код 5 у pytest — «тесты не найдены»: это не ошибка сбора.
        return Collected(proc.returncode in (0, 5), ids, proc.output)

    return ctx.memo("pytest:collect", load)


def _summary(output: str) -> str:
    for line in reversed(output.splitlines()):
        if re.search(r"\d+ (passed|failed|error|errors|skipped|deselected)", line) or (
            "no tests ran" in line
        ):
            return line.strip(" =")
    return first_lines(output, 3)


def run_suite(ctx: Context) -> Suite:
    def load() -> Suite:
        if not module_available("pytest"):
            return Suite(1, "pytest не установлен: выполните uv sync", None, "")
        report = ctx.tmp / "coverage.json"
        cmd = python("-m", "pytest", "-q", "-p", "no:cacheprovider")
        if module_available("pytest_cov"):
            cmd += ["--cov=src", f"--cov-report=json:{report}"]
        proc = run(cmd, ctx, timeout=900)
        coverage = None
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            coverage = float(data.get("totals", {}).get("percent_covered", 0.0))
        return Suite(proc.returncode, _summary(proc.output), coverage, proc.output)

    return ctx.memo("pytest:suite", load)


def run_tests_in(ctx: Context, directory: str) -> Suite:
    def load() -> Suite:
        proc = run(python("-m", "pytest", "-q", "-p", "no:cacheprovider", directory), ctx)
        return Suite(proc.returncode, _summary(proc.output), None, proc.output)

    return ctx.memo(f"pytest:{directory}", load)


def failed_tests(output: str, limit: int = 3) -> str:
    names = re.findall(r"^(?:SUB)?FAILED (\S+)", output, re.M)
    names += re.findall(r"^ERROR (\S+)", output, re.M)
    if not names:
        return ""
    shown = ", ".join(names[:limit])
    return shown + (f" и ещё {len(names) - limit}" if len(names) > limit else "")


def relative(ctx: Context, path: Path) -> str:
    return path.relative_to(ctx.root).as_posix()
