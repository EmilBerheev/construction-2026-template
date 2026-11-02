"""ЛР1. Требования и архитектура как код."""

from __future__ import annotations

import re

from grader.common import check_report
from grader.core import Check, Context, Lab, Outcome, fail, ok
from grader.util import (
    commits,
    diagram_type,
    has_placeholder,
    is_git_repo,
    mermaid_blocks,
    plural,
    prose,
    sections,
    strip_comments,
    tables,
)

REQUIREMENTS = "docs/requirements.md"
ARCHITECTURE = "docs/architecture.md"
ADR_DIR = "docs/adr"

MIN_STORIES = 8
MIN_NFR = 4
MIN_ADR = 2
MIN_COMMITS = 5
CC_RATIO = 0.8

STORY_HEADING = re.compile(r"^(#{2,4})\s*(US-\d+)\b.*$", re.M)
STORY_SENTENCE = re.compile(r"\bКак\b.+?,\s*я\s+хочу\b.+?,\s*чтобы\b\s*\S", re.I | re.S)
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
GHERKIN = re.compile(r"\b(Дано|Given)\b.*?\b(Когда|When)\b.*?\b(Тогда|Then)\b", re.I | re.S)
CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^()\s][^()]*\))?!?: \S"
)


# --- Пользовательские истории ------------------------------------------------------


def _stories(text: str) -> list[tuple[str, str]]:
    clean = strip_comments(text)
    matches = list(STORY_HEADING.finditer(clean))
    result = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        end = len(clean)
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        # история кончается на следующем заголовке того же или более высокого уровня
        higher = re.compile(rf"^#{{1,{level}}}\s", re.M)
        stop = higher.search(clean, match.end())
        if stop and stop.start() < end:
            end = stop.start()
        result.append((match.group(2), clean[match.end() : end]))
    return result


def _criteria(body: str) -> int:
    items: list[str] = []
    for line in body.splitlines():
        if LIST_ITEM.match(line):
            items.append(line)
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
    return sum(1 for item in items if GHERKIN.search(item.replace("*", "")))


def _valid_stories(ctx: Context) -> tuple[list[str], list[str]] | None:
    text = ctx.read(REQUIREMENTS)
    if text is None:
        return None
    valid, broken = [], []
    for story_id, body in _stories(text):
        sentence = " ".join(prose(body).replace("*", "").replace("_", " ").split())
        if STORY_SENTENCE.search(sentence) and not has_placeholder(body):
            valid.append(story_id)
        else:
            broken.append(story_id)
    return valid, broken


def check_stories(ctx: Context) -> Outcome:
    found = _valid_stories(ctx)
    if found is None:
        return fail(f"Нет файла {REQUIREMENTS}. Возьмите его из шаблона и заполните.")
    valid, broken = found
    if len(valid) >= MIN_STORIES:
        return ok(plural(len(valid), "история", "истории", "историй"))
    hint = (
        f"Нужно ≥ {MIN_STORIES} историй вида «### US-01. Название» и строкой "
        "«Как <роль>, я хочу <действие>, чтобы <ценность>.»"
    )
    if broken:
        hint += f" Не распознаны (формат или TODO): {', '.join(broken[:5])}."
    return fail(hint, detail=f"сейчас {len(valid)}")


def check_acceptance(ctx: Context) -> Outcome:
    text = ctx.read(REQUIREMENTS)
    if text is None:
        return fail(f"Нет файла {REQUIREMENTS}.")
    stories = _stories(text)
    if len(stories) < MIN_STORIES:
        return fail(f"Сначала нужны ≥ {MIN_STORIES} пользовательских историй.")
    without = [story_id for story_id, body in stories if _criteria(body) == 0]
    if without:
        return fail(
            "У каждой истории нужен хотя бы один критерий приёмки пунктом списка "
            "«- Дано …, когда …, тогда …».",
            detail=f"без критериев: {', '.join(without[:6])}",
        )
    total = sum(_criteria(body) for _, body in stories)
    return ok(plural(total, "критерий", "критерия", "критериев"))


def check_nfr(ctx: Context) -> Outcome:
    text = ctx.read(REQUIREMENTS)
    if text is None:
        return fail(f"Нет файла {REQUIREMENTS}.")
    measurable, vague = [], []
    for table in tables(text):
        threshold = table.column("порог", "метрик")
        verify = table.column("провер")
        for row in table.rows:
            row_id = row[0].replace("`", "").replace("*", "").strip()
            if not re.fullmatch(r"(NFR|НФТ)-\d+", row_id):
                continue
            cells = row[1:] if threshold is None else [row[threshold]]
            has_number = any(re.search(r"\d", cell) for cell in cells)
            has_check = verify is None or bool(row[verify].strip())
            if has_number and has_check and not has_placeholder(" ".join(row)):
                measurable.append(row_id)
            else:
                vague.append(row_id)
    if len(measurable) >= MIN_NFR:
        return ok(plural(len(measurable), "требование", "требования", "требований"))
    hint = (
        f"Нужно ≥ {MIN_NFR} строк таблицы НФТ (NFR-01 …) с числовым порогом "
        "в колонке «Метрика и порог» и заполненной колонкой «Как проверить»."
    )
    if vague:
        hint += f" Без числа или способа проверки: {', '.join(vague[:5])}."
    return fail(hint, detail=f"сейчас {len(measurable)}")


# --- Диаграммы ---------------------------------------------------------------------


def _blocks(ctx: Context, kind: str) -> list[str] | None:
    text = ctx.read(ARCHITECTURE)
    if text is None:
        return None
    return [block for block in mermaid_blocks(text) if diagram_type(block) == kind]


def _count(pattern: str, block: str) -> int:
    return len(re.findall(pattern, block, re.M))


def check_c4_context(ctx: Context) -> Outcome:
    blocks = _blocks(ctx, "C4Context")
    if blocks is None:
        return fail(f"Нет файла {ARCHITECTURE}.")
    if not blocks:
        return fail(f"В {ARCHITECTURE} нет блока ```mermaid с типом C4Context.")
    for block in blocks:
        people = _count(r"^\s*Person(_Ext)?\s*\(", block)
        systems = _count(r"^\s*System(Db|Queue)?(_Ext)?\s*\(", block)
        rels = _count(r"^\s*(Bi)?Rel(_[UDLR]|_Up|_Down|_Left|_Right|_Back)?\s*\(", block)
        if people >= 1 and systems >= 2 and rels >= 2 and not has_placeholder(block):
            return ok(f"людей {people}, систем {systems}, связей {rels}")
    return fail(
        "Диаграмма контекста должна показать ≥ 1 Person, ≥ 2 System/System_Ext "
        "(ваша система и внешние) и ≥ 2 Rel, без TODO."
    )


def check_c4_container(ctx: Context) -> Outcome:
    blocks = _blocks(ctx, "C4Container")
    if blocks is None:
        return fail(f"Нет файла {ARCHITECTURE}.")
    if not blocks:
        return fail(f"В {ARCHITECTURE} нет блока ```mermaid с типом C4Container.")
    for block in blocks:
        containers = _count(r"^\s*Container(Db|Queue)?(_Ext)?\s*\(", block)
        rels = _count(r"^\s*(Bi)?Rel(_[UDLR]|_Up|_Down|_Left|_Right|_Back)?\s*\(", block)
        if containers >= 2 and rels >= 2 and not has_placeholder(block):
            return ok(f"контейнеров {containers}, связей {rels}")
    return fail("Диаграмма контейнеров: нужно ≥ 2 Container/ContainerDb и ≥ 2 Rel, без TODO.")


CLASS_DECL = re.compile(r"^\s*class\s+`?([A-Za-z_]\w*)", re.M)
CLASS_REL = re.compile(
    r'^\s*`?([A-Za-z_]\w*)`?\s*(?:"[^"]*"\s*)?'
    r"(<\|--|--\|>|\*--|--\*|o--|--o|<--|-->|<\.\.|\.\.>|<\|\.\.|\.\.\|>|--|\.\.)"
    r'\s*(?:"[^"]*"\s*)?`?([A-Za-z_]\w*)',
    re.M,
)


def check_class_diagram(ctx: Context) -> Outcome:
    blocks = _blocks(ctx, "classDiagram")
    if blocks is None:
        return fail(f"Нет файла {ARCHITECTURE}.")
    if not blocks:
        return fail(f"В {ARCHITECTURE} нет блока ```mermaid с типом classDiagram.")
    best = (0, 0)
    for block in blocks:
        names = set(CLASS_DECL.findall(block))
        relations = CLASS_REL.findall(block)
        for left, _, right in relations:
            names |= {left, right}
        best = max(best, (len(names), len(relations)))
        if len(names) >= 5 and len(relations) >= 4:
            return ok(f"классов {len(names)}, связей {len(relations)}")
    return fail(
        "Диаграмма классов: нужно ≥ 5 классов предметной области и ≥ 4 связи "
        "(наследование, композиция, агрегация, ассоциация).",
        detail=f"сейчас классов {best[0]}, связей {best[1]}",
    )


MESSAGE = re.compile(
    r"^\s*([\w\"]+)\s*(-->>|->>|-->|->|--x|-x|--\)|-\))\s*[+-]?\s*([\w\"]+)\s*:", re.M
)
DECLARED = re.compile(r"^\s*(?:participant|actor)\s+(\S+)", re.M)
FRAGMENT = re.compile(r"^\s*(alt|opt|loop|par|critical|break)\b", re.M)


def check_sequence(ctx: Context) -> Outcome:
    blocks = _blocks(ctx, "sequenceDiagram")
    if blocks is None:
        return fail(f"Нет файла {ARCHITECTURE}.")
    if not blocks:
        return fail(f"В {ARCHITECTURE} нет блока ```mermaid с типом sequenceDiagram.")
    for block in blocks:
        messages = MESSAGE.findall(block)
        participants = set(DECLARED.findall(block))
        for left, _, right in messages:
            participants |= {left, right}
        fragments = FRAGMENT.findall(block)
        if len(participants) >= 3 and len(messages) >= 4 and fragments:
            return ok(f"участников {len(participants)}, сообщений {len(messages)}")
    return fail(
        "Диаграмма последовательности: нужно ≥ 3 участника, ≥ 4 сообщения "
        "и хотя бы один фрагмент alt / opt / loop."
    )


# --- ADR и история -----------------------------------------------------------------

ADR_SECTIONS = ("статус", "контекст", "решение", "последствия")
ADR_STATUSES = ("предложено", "принято", "отклонено", "заменено", "устарело")


def _adr_files(ctx: Context) -> list[str]:
    directory = ctx.path(ADR_DIR)
    if not directory.is_dir():
        return []
    return sorted(
        path.name
        for path in directory.glob("*.md")
        if not path.name.startswith("0000") and "template" not in path.name.lower()
    )


def _adr_problem(text: str) -> str | None:
    found: dict[str, str] = {}
    for level in (2, 3):
        for title, body in sections(text, level):
            key = title.strip().lower()
            for name in ADR_SECTIONS:
                if key.startswith(name):
                    found.setdefault(name, body)
    missing = [name for name in ADR_SECTIONS if not prose(found.get(name, "")).strip()]
    if missing:
        return "нет разделов: " + ", ".join(missing)
    if has_placeholder(text):
        return "осталась заглушка TODO"
    status = found["статус"].lower()
    if not any(word in status for word in ADR_STATUSES):
        return "в «Статус» нужно одно из: " + ", ".join(ADR_STATUSES)
    return None


def check_adr(ctx: Context) -> Outcome:
    names = _adr_files(ctx)
    good, bad = [], []
    for name in names:
        problem = _adr_problem(ctx.read(f"{ADR_DIR}/{name}") or "")
        if problem is None:
            good.append(name)
        else:
            bad.append(f"{name} — {problem}")
    if len(good) >= MIN_ADR:
        return ok(", ".join(good))
    hint = (
        f"Нужно ≥ {MIN_ADR} ADR в {ADR_DIR}/ (0001-….md, 0002-….md) с разделами "
        "«Статус», «Контекст», «Решение», «Последствия». Файл 0000-template.md не считается."
    )
    if bad:
        hint += " Не приняты: " + "; ".join(bad[:3]) + "."
    return fail(hint, detail=f"сейчас {len(good)}")


def check_docs_filled(ctx: Context) -> Outcome:
    files = [REQUIREMENTS, ARCHITECTURE] + [f"{ADR_DIR}/{name}" for name in _adr_files(ctx)]
    missing = [name for name in (REQUIREMENTS, ARCHITECTURE) if ctx.read(name) is None]
    if missing:
        return fail("Нет файлов: " + ", ".join(missing))
    with_todo = [name for name in files if has_placeholder(ctx.read(name) or "")]
    if with_todo:
        return fail("Уберите заглушки TODO из: " + ", ".join(with_todo))
    return ok(plural(len(files), "файл", "файла", "файлов"))


def check_conventional_commits(ctx: Context) -> Outcome:
    if not is_git_repo(ctx):
        return fail("Это не git-репозиторий. Работайте в клоне своего репозитория.")
    history = [commit for commit in commits(ctx) if len(commit.parents) == 1]
    if len(history) < MIN_COMMITS:
        return fail(
            f"Нужно ≥ {MIN_COMMITS} своих коммитов (без первого коммита шаблона и merge). "
            "Коммитьте маленькими шагами.",
            detail=f"сейчас {len(history)}",
        )
    good = [commit for commit in history if CONVENTIONAL.match(commit.subject)]
    ratio = len(good) / len(history)
    if ratio >= CC_RATIO:
        return ok(f"{len(good)} из {len(history)} ({ratio:.0%})")
    bad = [commit.subject for commit in history if not CONVENTIONAL.match(commit.subject)]
    return fail(
        f"≥ {CC_RATIO:.0%} сообщений должны иметь вид «тип(область): описание», "
        "тип — feat, fix, docs, refactor, test, chore, ci, build, perf, style, revert.",
        detail=f"{ratio:.0%}; например: «{bad[0][:60]}»",
    )


LAB = Lab(
    "lab1",
    "ЛР1. Требования и архитектура как код",
    (
        Check("report", "Отчёт: раздел ЛР1 в README.md заполнен", 3, lambda c: check_report(c, 1)),
        Check("stories", "Требования: ≥ 8 пользовательских историй", 8, check_stories),
        Check("acceptance", "Требования: критерии приёмки у каждой истории", 6, check_acceptance),
        Check("nfr", "Требования: ≥ 4 измеримых нефункциональных требования", 6, check_nfr),
        Check("c4_context", "C4: диаграмма контекста (Mermaid)", 5, check_c4_context),
        Check("c4_container", "C4: диаграмма контейнеров (Mermaid)", 5, check_c4_container),
        Check("uml_class", "UML: диаграмма классов (Mermaid)", 6, check_class_diagram),
        Check("uml_sequence", "UML: диаграмма последовательности (Mermaid)", 5, check_sequence),
        Check(
            "adr", "ADR: ≥ 2 записи с разделами Статус/Контекст/Решение/Последствия", 8, check_adr
        ),
        Check("no_todo", "Документы ЛР1 без заглушек TODO", 2, check_docs_filled),
        Check(
            "commits",
            "Git: ≥ 80 % коммитов в формате Conventional Commits",
            6,
            check_conventional_commits,
        ),
    ),
)
