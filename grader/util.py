"""Вспомогательные функции: запуск команд, разбор Markdown, git, исходники Python."""

from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

from grader.core import Context

# --- Запуск команд ----------------------------------------------------------------


@dataclass(frozen=True)
class Proc:
    returncode: int
    output: str


def run(
    cmd: list[str],
    ctx: Context,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 600,
    cwd: Path | None = None,
) -> Proc:
    full_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    full_env.pop("FORCE_COLOR", None)
    if env:
        full_env.update(env)
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd or ctx.root,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return Proc(127, f"команда не найдена: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return Proc(124, f"команда не уложилась в {timeout} с: {' '.join(cmd)}")
    return Proc(completed.returncode, completed.stdout.decode("utf-8", errors="replace"))


def python(*args: str) -> list[str]:
    return [sys.executable, *args]


def module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def first_lines(text: str, count: int = 5) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    shown = "; ".join(lines[:count])
    if len(lines) > count:
        shown += f"; … ещё {len(lines) - count}"
    return shown


def plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = one
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = few
    else:
        word = many
    return f"{count} {word}"


# --- Markdown ---------------------------------------------------------------------

PLACEHOLDER = re.compile(r"\bTODO\b")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^(```|~~~)[^\n]*\n.*?^\1[ \t]*$", re.S | re.M)
_MERMAID = re.compile(r"^(```|~~~)[ \t]*mermaid[^\n]*\n(.*?)^\1[ \t]*$", re.S | re.M)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def strip_comments(text: str) -> str:
    return _COMMENT.sub("", text)


def prose(text: str) -> str:
    """Текст без HTML-комментариев и блоков кода: то, что пишет человек."""
    return _FENCE.sub("", strip_comments(text))


def word_count(text: str) -> int:
    return len(re.findall(r"[0-9A-Za-zА-Яа-яЁё]{2,}", prose(text)))


def has_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER.search(strip_comments(text)))


def mermaid_blocks(text: str) -> list[str]:
    return [match.group(2) for match in _MERMAID.finditer(strip_comments(text))]


def diagram_type(block: str) -> str:
    lines = block.splitlines()
    index = 0
    if lines and lines[0].strip() == "---":  # frontmatter с title/config
        index = 1
        while index < len(lines) and lines[index].strip() != "---":
            index += 1
        index += 1
    for line in lines[index:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        return stripped.split()[0]
    return ""


def sections(text: str, level: int = 2) -> list[tuple[str, str]]:
    """Разбивает документ на (заголовок, тело) по заголовкам заданного уровня."""
    marker = "#" * level
    pattern = re.compile(rf"^{marker}(?!#)\s+(.+?)\s*#*\s*$", re.M)
    clean = strip_comments(text)
    matches = list(pattern.finditer(clean))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
        result.append((match.group(1), clean[match.end() : end]))
    return result


def clean_cell(cell: str) -> str:
    cell = _LINK.sub(r"\1", cell)
    cell = cell.replace("`", "").replace("**", "").replace("__", "")
    return " ".join(cell.split()).strip()


@dataclass(frozen=True)
class Table:
    headers: list[str]
    rows: list[list[str]]

    def column(self, *keywords: str) -> int | None:
        for index, header in enumerate(self.headers):
            lowered = header.lower()
            if any(keyword in lowered for keyword in keywords):
                return index
        return None


def _split_row(line: str) -> list[str]:
    line = line.strip()
    line = line.removeprefix("|").removesuffix("|")
    cells = re.split(r"(?<!\\)\|", line)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def tables(text: str) -> list[Table]:
    lines = strip_comments(text).splitlines()
    found: list[Table] = []
    index = 0
    separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
    while index < len(lines) - 1:
        if "|" in lines[index] and separator.match(lines[index + 1]):
            headers = [clean_cell(cell) for cell in _split_row(lines[index])]
            rows = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = _split_row(lines[index])
                cells += [""] * (len(headers) - len(cells))
                rows.append(cells[: len(headers)])
                index += 1
            found.append(Table(headers, rows))
        else:
            index += 1
    return found


# --- Git --------------------------------------------------------------------------


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    subject: str


def git(ctx: Context, *args: str) -> Proc:
    return run(
        ["git", "-c", "core.quotepath=off", "-c", "i18n.logOutputEncoding=UTF-8", *args], ctx
    )


def is_git_repo(ctx: Context) -> bool:
    return ctx.memo(
        "git:repo",
        lambda: git(ctx, "rev-parse", "--is-inside-work-tree").output.strip() == "true",
    )


def commits(ctx: Context) -> list[Commit]:
    def load() -> list[Commit]:
        if not is_git_repo(ctx):
            return []
        proc = git(ctx, "log", "--format=%H%x1f%P%x1f%s%x1e", "HEAD")
        if proc.returncode != 0:
            return []
        result = []
        for record in proc.output.split("\x1e"):
            parts = record.strip("\n").split("\x1f")
            if len(parts) == 3:
                sha, parents, subject = parts
                result.append(Commit(sha.strip(), tuple(parents.split()), subject.strip()))
        return result

    return ctx.memo("git:commits", load)


def resolve_commit(ctx: Context, ref: str) -> str | None:
    proc = git(ctx, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = proc.output.strip()
    return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


def is_ancestor(ctx: Context, ancestor: str, descendant: str) -> bool:
    return git(ctx, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def commit_subject(ctx: Context, sha: str) -> str:
    return git(ctx, "log", "-1", "--format=%s", sha).output.strip()


def changed_files(ctx: Context, sha: str) -> list[str]:
    proc = git(ctx, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    return [line.strip() for line in proc.output.splitlines() if line.strip()]


def first_commit_adding(ctx: Context, path: str) -> str | None:
    proc = git(ctx, "log", "--diff-filter=A", "--format=%H", "--reverse", "HEAD", "--", path)
    lines = [line.strip() for line in proc.output.splitlines() if line.strip()]
    return lines[0] if proc.returncode == 0 and lines else None


# --- Исходники Python -------------------------------------------------------------


def python_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts and ".venv" not in path.parts
    )


@dataclass(frozen=True)
class Source:
    path: Path
    text: str
    tree: ast.Module | None
    error: str | None


def parse_sources(ctx: Context, directory: str = "src") -> list[Source]:
    def load() -> list[Source]:
        result = []
        for path in python_files(ctx.path(directory)):
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree: ast.Module | None = ast.parse(text, filename=str(path))
                error = None
            except SyntaxError as exc:
                tree, error = (
                    None,
                    f"{path.relative_to(ctx.root).as_posix()}:{exc.lineno}: {exc.msg}",
                )
            result.append(Source(path, text, tree, error))
        return result

    return ctx.memo(f"sources:{directory}", load)


def logical_lines(source: Source) -> int:
    """Строки с кодом: без пустых, комментариев и docstring."""
    if source.tree is None:
        return 0
    doc_lines: set[int] = set()
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                end = body[0].end_lineno or body[0].lineno
                doc_lines.update(range(body[0].lineno, end + 1))
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    code_lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source.text).readline):
            if token.type not in skip:
                code_lines.update(range(token.start[0], token.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        return 0
    return len(code_lines - doc_lines)


def annotation_names(node: ast.expr | None) -> set[str]:
    """Имена, упомянутые в аннотации: Repo, repo.Repo, Repo | None, 'Repo', list[Repo]."""
    if node is None:
        return set()
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            with contextlib.suppress(SyntaxError):
                names |= annotation_names(ast.parse(child.value, mode="eval").body)
    return names
