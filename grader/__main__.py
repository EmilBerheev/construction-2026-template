"""Точка входа: uv run python -m grader lab1 | lab2 | lab3 | lab4 | all."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from grader import __version__, lab1, lab2, lab3, lab4
from grader.core import (
    Context,
    Lab,
    LabResult,
    checks_markdown,
    lab_markdown,
    run_lab,
    summary_markdown,
    to_json,
)
from grader.util import git

LABS: dict[str, Lab] = {lab.id: lab for lab in (lab1.LAB, lab2.LAB, lab3.LAB, lab4.LAB)}


def fingerprint(root: Path) -> str:
    """Отпечаток грейдера и workflow: преподаватель сверяет его с эталоном."""
    digest = hashlib.sha256()
    files = [*sorted((root / "grader").glob("*.py")), root / ".github" / "workflows" / "grade.yml"]
    for file in files:
        if file.is_file():
            digest.update(file.name.encode())
            digest.update(file.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="python -m grader", description="Автопроверка лабораторных"
    )
    parser.add_argument("target", nargs="?", default="all", choices=[*LABS, "all"])
    parser.add_argument(
        "--list", action="store_true", help="показать проверки и баллы, ничего не запуская"
    )
    parser.add_argument(
        "--json", default="grade.json", help="куда записать результат (по умолчанию grade.json)"
    )
    parser.add_argument("--root", default=".", help="корень репозитория")
    parser.add_argument("--fingerprint", action="store_true", help="напечатать отпечаток грейдера")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if args.fingerprint:
        print(fingerprint(root))
        return 0
    selected = list(LABS.values()) if args.target == "all" else [LABS[args.target]]
    if args.list:
        print("\n".join(checks_markdown(lab) for lab in selected))
        return 0

    ctx = Context(root)
    results: list[LabResult] = []
    try:
        for lab in selected:
            print(f"Проверяю {lab.title}…", file=sys.stderr, flush=True)
            lab_result = run_lab(lab, ctx)
            results.append(lab_result)
            print(lab_markdown(lab_result), flush=True)
    finally:
        ctx.cleanup()

    report = "\n".join(lab_markdown(result) for result in results)
    if len(results) > 1:
        summary = summary_markdown(results)
        print(summary)
        report = summary + "\n" + report
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")

    commit = git(ctx, "rev-parse", "HEAD").output.strip()
    extra = {
        "grader_version": __version__,
        "fingerprint": fingerprint(root),
        "commit": commit if re.fullmatch(r"[0-9a-f]{40,64}", commit) else None,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ci": ctx.in_ci,
    }
    (root / args.json).write_text(to_json(results, extra), encoding="utf-8")
    total = sum(result.score for result in results)
    maximum = sum(result.lab.max_points for result in results)
    print(f"Итого авто-баллов: {total} / {maximum}. Подробности — в {args.json}.")
    # Низкий балл — не ошибка. Ненулевой код только если упал сам грейдер.
    return 1 if ctx.crashed else 0


if __name__ == "__main__":
    sys.exit(main())
