"""Модель проверок, контекст запуска и вывод результатов."""

from __future__ import annotations

import enum
import json
import os
import shutil
import tempfile
import time
import tomllib
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


class Status(enum.Enum):
    PASS = "зачтено"
    FAIL = "не зачтено"
    SKIP_CI = "пропущено (только в CI)"
    NOT_APPLICABLE = "не применяется"
    ERROR = "ошибка грейдера"


# Пропущенные и неприменимые проверки баллы не снимают.
AWARDED = {Status.PASS, Status.SKIP_CI, Status.NOT_APPLICABLE}


@dataclass(frozen=True)
class Outcome:
    status: Status
    hint: str = ""
    detail: str = ""


def ok(detail: str = "") -> Outcome:
    return Outcome(Status.PASS, detail=detail)


def fail(hint: str, detail: str = "") -> Outcome:
    return Outcome(Status.FAIL, hint=hint, detail=detail)


def skip_ci(detail: str) -> Outcome:
    return Outcome(Status.SKIP_CI, detail=detail)


def not_applicable(detail: str) -> Outcome:
    return Outcome(Status.NOT_APPLICABLE, detail=detail)


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    points: int
    run: Callable[[Context], Outcome]
    ci_only: bool = False
    gate: str = ""  # условие, при котором проверка начинает давать баллы


@dataclass(frozen=True)
class Lab:
    id: str
    title: str
    checks: tuple[Check, ...]

    @property
    def max_points(self) -> int:
        return sum(check.points for check in self.checks)


@dataclass
class CheckResult:
    check: Check
    outcome: Outcome
    seconds: float

    @property
    def score(self) -> int:
        return self.check.points if self.outcome.status in AWARDED else 0


@dataclass
class LabResult:
    lab: Lab
    results: list[CheckResult] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(result.score for result in self.results)


class Context:
    """Всё, что проверкам нужно знать о репозитории, плюс кэш дорогих операций."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.pyproject: dict[str, Any] = {}
        self.config = self._load_config()
        self.in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
        self.tmp = Path(tempfile.mkdtemp(prefix="grader-"))
        self.crashed = False
        self._memo: dict[str, Any] = {}

    def _load_config(self) -> dict[str, Any]:
        pyproject = self.root / "pyproject.toml"
        if not pyproject.is_file():
            return {}
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            return {}
        self.pyproject = data
        section = data.get("tool", {}).get("grader", {})
        return section if isinstance(section, dict) else {}

    @property
    def package(self) -> str:
        return str(self.config.get("package", "app"))

    @property
    def asgi_app(self) -> str:
        return str(self.config.get("asgi_app", f"{self.package}.api.main:app"))

    def path(self, relative: str) -> Path:
        return self.root / relative

    def read(self, relative: str) -> str | None:
        file = self.path(relative)
        if not file.is_file():
            return None
        return file.read_text(encoding="utf-8", errors="replace")

    def memo(self, key: str, compute: Callable[[], T]) -> T:
        if key not in self._memo:
            self._memo[key] = compute()
        value: T = self._memo[key]
        return value

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def run_lab(lab: Lab, ctx: Context) -> LabResult:
    lab_result = LabResult(lab)
    for check in lab.checks:
        started = time.perf_counter()
        try:
            outcome = check.run(ctx)
        except Exception:
            ctx.crashed = True
            outcome = Outcome(
                Status.ERROR,
                hint="Проверка упала внутри грейдера. Пришлите преподавателю ссылку на прогон.",
                detail=traceback.format_exc(limit=4),
            )
        lab_result.results.append(CheckResult(check, outcome, time.perf_counter() - started))
    return lab_result


# --- Вывод ------------------------------------------------------------------------


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def lab_markdown(lab_result: LabResult) -> str:
    lab = lab_result.lab
    lines = [
        f"### {lab.title} — {lab_result.score} / {lab.max_points}",
        "",
        "| Проверка | Баллы | Статус | Что исправить |",
        "|---|---|---|---|",
    ]
    for result in lab_result.results:
        outcome = result.outcome
        status = outcome.status.value
        if outcome.status is Status.ERROR:
            note = outcome.hint
        elif outcome.status is Status.FAIL:
            note = outcome.hint + (f" ({outcome.detail})" if outcome.detail else "")
        else:
            note = outcome.detail
        lines.append(
            f"| {_cell(result.check.name)} | {result.score} / {result.check.points} "
            f"| {status} | {_cell(note)} |"
        )
    return "\n".join(lines) + "\n"


def summary_markdown(lab_results: list[LabResult]) -> str:
    lines = ["## Итог автопроверки", "", "| Лабораторная | Авто-баллы |", "|---|---|"]
    for lab_result in lab_results:
        lines.append(
            f"| {lab_result.lab.title} | {lab_result.score} / {lab_result.lab.max_points} |"
        )
    lines += [
        "",
        "Автопроверка даёт до 60 баллов за лабораторную. "
        "Ещё 40 преподаватель ставит на защите по рубрике из задания.",
    ]
    return "\n".join(lines) + "\n"


def checks_markdown(lab: Lab) -> str:
    """Таблица проверок без запуска: для заданий и для студентов."""
    lines = [f"### {lab.title}", "", "| Проверка | Баллы | Примечание |", "|---|---|---|"]
    for check in lab.checks:
        notes = []
        if check.ci_only:
            notes.append("только в CI, локально пропускается без потери баллов")
        if check.gate:
            notes.append(f"засчитывается, {check.gate}")
        lines.append(f"| {_cell(check.name)} | {check.points} | {'; '.join(notes)} |")
    lines.append(f"| **Итого** | **{lab.max_points}** | |")
    return "\n".join(lines) + "\n"


def to_json(lab_results: list[LabResult], extra: dict[str, Any]) -> str:
    labs: list[dict[str, Any]] = []
    for lab_result in lab_results:
        labs.append(
            {
                "id": lab_result.lab.id,
                "title": lab_result.lab.title,
                "score": lab_result.score,
                "max": lab_result.lab.max_points,
                "checks": [
                    {
                        "id": result.check.id,
                        "name": result.check.name,
                        "score": result.score,
                        "max": result.check.points,
                        "status": result.outcome.status.name.lower(),
                        "hint": result.outcome.hint,
                        "detail": result.outcome.detail,
                        "seconds": round(result.seconds, 2),
                    }
                    for result in lab_result.results
                ],
            }
        )
    payload = {
        **extra,
        "labs": labs,
        "total": sum(result.score for result in lab_results),
        "max_total": sum(result.lab.max_points for result in lab_results),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
