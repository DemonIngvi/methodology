"""lint: нестрогие проверки методологий по компонентам — не блокируют коммит, показывают пробелы.

Встроенные правила включаются списком `[lint] enable`; плагины добавляют свои (`@lint_rule`). Правило — функция
ctx → [(компонент, сообщение)]; компонент «—» — общее.
"""
import re

from . import checks as K
from . import util as U


def r_subtasks(ctx):
    """Подзадачи: нумерация с N.M.1, 2–8 ч, у каждой «готово, когда» (rules/tasks.md)."""
    st = ctx.get("tasks.subtasks", {})
    lo, hi = float(st.get("min", 2)), float(st.get("max", 8))
    for tid, t in ctx.tasks().items():
        if t.closed:
            continue
        if not t.subtasks:
            yield t.component, f"задача {tid}: нет декомпозиции на подзадачи"
            continue
        if t.subtasks[0][0] != f"{tid}.1":
            yield t.component, f"задача {tid}: подзадачи начинаются с {t.subtasks[0][0]}, а не {tid}.1"
        for num, h, done in t.subtasks:
            if h is None:
                yield t.component, f"задача {tid}: у подзадачи {num} нет оценки в часах"
            elif not lo <= h <= hi:
                yield t.component, f"задача {tid}: подзадача {num} — {h:g} ч (нужно {lo:g}–{hi:g})"
            if U.is_empty(done):
                yield t.component, f"задача {tid}: у подзадачи {num} нет «готово, когда»"


def r_task_acceptance(ctx):
    """Приёмка задачи — чек-лист «что сделать → что увидеть» (rules/tasks.md)."""
    for tid, t in ctx.tasks().items():
        sec = U.section(t.text, "Приёмка")
        if not t.closed and not re.search(r"^\s*- \[[ xX]\]", sec, re.M):
            yield t.component, f"задача {tid}: в «Приёмке» нет пунктов чек-листа"


def r_requirement_scenarios(ctx):
    """Не меньше N сценариев у требования (rules/sdd.md)."""
    n = int(ctx.get("requirements.min_scenarios", 3) or 0)
    for rid, r in ctx.requirements().items():
        if n and len(r.scenarios) < n:
            yield ctx.component_of(r.file) or "—", f"{rid} «{r.title}»: сценариев {len(r.scenarios)}, нужно не меньше {n}"


def r_block_card(ctx):
    """Карточка блока заполнена полностью — иначе Р и Т отказывают (rules/blocks.md#ready)."""
    for b in ctx.blocks():
        _, miss = ctx.block_card(b)
        if miss:
            yield b.component or "—", f"блок «{b.title}»: карточка не заполнена — {', '.join(miss)}"


def r_block_screens(ctx):
    """Экраны «новое» / «доработка» без операции API (rules/blocks.md)."""
    sc = ctx.get("blocks.screens", {}) or {}
    new = [x.lower() for x in U.as_list(sc.get("new"))]
    for b in ctx.blocks():
        if not b.file.lower().endswith(".md"):
            continue
        body = U.section(ctx.text(b.file), sc.get("section", "Экраны"))
        for r in U.table_rows(body, ""):
            st = U.plain(U.col(r, sc.get("status_col", "Статус"))).lower()
            if any(st.startswith(x) for x in new) and U.is_empty(U.col(r, sc.get("operation_col", "Операция"))):
                yield b.component or "—", f"блок «{b.title}», экран «{U.plain(list(r.values())[0])}» ({st}): нет операции API для действий"


def r_recommended_sections(ctx):
    """Рекомендуемые разделы типа (`recommended` в [[types]])."""
    for f in ctx.md_files():
        t = ctx.type_of(f)
        if t and t.recommended and not ctx.generated_of(f):
            miss = K.missing_sections(ctx, f, t.recommended)
            if miss:
                yield ctx.component_of(f) or "—", f"{f} ({t.name}): нет раздела {', '.join(m.lstrip('^') for m in miss)}"


def r_placeholders(ctx):
    """Незаполненные заглушки шаблонов `<…>` вне шаблонов и блоков кода."""
    excl = ctx.get("lint.placeholders_exclude", []) or []
    for f in ctx.md_files():
        if U.match(f, excl):
            continue
        for ln, line in U.md_lines(ctx.text(f)):
            line = re.sub(r"`[^`]*`|<!--.*?-->|</?[a-zA-Z][^>]*>", "", line)
            for m in re.finditer(r"<[^<>\n]{2,60}>", line):
                yield ctx.component_of(f) or "—", f"{f}:{ln}: заглушка шаблона {m.group(0)}"


BUILTIN = {
    "subtasks": r_subtasks,
    "task-acceptance": r_task_acceptance,
    "requirement-scenarios": r_requirement_scenarios,
    "block-card": r_block_card,
    "block-screens": r_block_screens,
    "recommended-sections": r_recommended_sections,
    "placeholders": r_placeholders,
}


def run(ctx) -> dict:
    """→ {компонент: [сообщение]}."""
    enabled = U.as_list(ctx.get("lint.enable", []))
    rules = {k: v for k, v in BUILTIN.items() if k in enabled}
    if ctx.plugins:
        rules.update(ctx.plugins.lint_rules)
    res = {}
    for name, fn in rules.items():
        for comp, msg in fn(ctx) or []:
            res.setdefault(comp or "—", []).append(msg)
    return res
