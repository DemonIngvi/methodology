"""lint: нестрогие проверки методологий по компонентам — не блокируют коммит, показывают пробелы.

Встроенные правила включаются списком `[lint] enable`; плагины добавляют свои (`@lint_rule`). Правило — функция
ctx → [(компонент, сообщение)]; компонент «—» — общее.
"""
import re
from pathlib import Path

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


# ---------- механика до ролей: то, за что роли отказывали, проверяется данными (rules/approvals.md#economy) ----------

def r_task_acceptance_coverage(ctx):
    """Каждое требование из «Закрывает требования» названо в «Приёмке» или подзадаче — ID или именем сценария (rules/tasks.md)."""
    reqs = ctx.requirements()
    secs = U.as_list(ctx.get("tasks.acceptance_sections", []))
    exempt = [x.lower() for x in U.as_list(ctx.get("tasks.acceptance_exempt", []))]
    for tid, t in ctx.tasks().items():
        if t.closed:
            continue
        body = "\n".join(U.section(t.text, s) for s in secs)
        low = body.lower()
        miss = []
        for rid in sorted(t.reqs):
            r = reqs.get(rid)
            if not r or rid in body or any(r.title.lower().startswith(x) for x in exempt):
                continue
            if any(len(s) > 6 and s.lower() in low for s in r.scenarios):
                continue
            miss.append(rid)
        if miss:
            yield t.component, (f"задача {tid}: приёмка и подзадачи не покрывают {', '.join(miss)} → строка приёмки "
                                "«сценарий → результат (ID требования)» по сценариям требования, включая отказы")


def _range(s: str, hpd: float):
    """«1–2 дн», «3 дн», «6–10 ч» → (мин, макс) в днях или None."""
    m = re.search(r"(\d+(?:[.,]\d+)?)(?:\s*[–-]\s*(\d+(?:[.,]\d+)?))?\s*(дн|д|ч|h|d)", U.plain(s or ""))
    if not m:
        return None
    lo = float(m.group(1).replace(",", "."))
    hi = float((m.group(2) or m.group(1)).replace(",", "."))
    k = 1 / hpd if m.group(3) in ("ч", "h") else 1
    return lo * k, hi * k


def r_task_estimate_range(ctx):
    """Сумма подзадач в днях — в диапазоне оценки шапки задачи (rules/tasks.md); «Итого» = сумма — строго в check."""
    est = ctx.get("tasks.estimate", {}) or {}
    hpd, tol = float(est.get("hours_per_day", 8)), float(est.get("tolerance_days", 0.25))
    for tid, t in ctx.tasks().items():
        hrs = [h for _, h, _ in t.subtasks if h is not None]
        if t.closed or not hrs:
            continue
        rows = U.table_rows(t.text, est.get("table", "| Этап |"))
        rng = _range(U.col(rows[0], est.get("column", "Человек")), hpd) if rows else None
        if not rng:
            continue
        days = sum(hrs) / hpd
        if not rng[0] - tol <= days <= rng[1] + tol:
            yield t.component, (f"задача {tid}: подзадачи {sum(hrs):g} ч ≈ {days:.1f} дн вне оценки шапки {rng[0]:g}–{rng[1]:g} дн "
                                "→ поправить оценку или подзадачи (и Roadmap)")


def open_question_ids(ctx) -> dict:
    """{ID открытого вопроса: файл} — строки файлов открытых вопросов рядом со спеками."""
    def build():
        row = re.compile(ctx.get("requirements.open_question_row"))
        out = {}
        name = ctx.get("requirements.open_questions", "open-questions.md")
        for spec in ctx.spec_files():
            q = (Path(spec).parent / name).as_posix()
            if (ctx.root / q).is_file():
                for l in ctx.text(q).splitlines():
                    if row.match(l):
                        out[U.plain(U.split_row(l)[0])] = q
        return out
    return ctx.cached("open_questions", build)


def r_open_question_refs(ctx):
    """Открытый пункт задачи и карточка блока ссылаются только на открытый вопрос (он есть в файле вопросов спеки)."""
    rx = re.compile(ctx.get("requirements.open_question_id"))
    opened = open_question_ids(ctx)
    sec = ctx.get("tasks.open_items_section", "Что проработать")
    for tid, t in ctx.tasks().items():
        for l in U.section(t.text, sec).splitlines():
            if re.match(r"^\s*- \[ \]", l):
                for q in rx.findall(l):
                    if q not in opened:
                        yield t.component, (f"задача {tid}: открытый пункт ссылается на {q}, которого нет среди открытых вопросов → "
                                            "[x] со ссылкой на решение (D, требование) или вернуть вопрос в файл вопросов спеки")
    for b in ctx.blocks():
        text = " ".join(b.fields.values()) + " " + " ".join(b.sections.values())
        for q in sorted(set(rx.findall(text)) - set(opened)):
            yield b.component or "—", f"блок «{b.title}»: упомянут закрытый или несуществующий {q} → ссылка на решение (D, требование)"


def _norm_name(x: str) -> str:
    return re.sub(r"\s*\([^)]*\)|`", "", U.plain(x)).strip().lower()


def r_fmea_scenarios(ctx):
    """У каждой строки FMEA (файл рядом со спекой) — сценарий отказа с тем же именем в спеке (rules/spec-criteria.md)."""
    conf = ctx.get("requirements.fmea", {}) or {}
    by_spec = {}
    for r in ctx.requirements().values():
        by_spec.setdefault(r.file, []).extend(r.scenarios)
    for spec in ctx.spec_files():
        f = (Path(spec).parent / conf.get("file", "interfaces.md")).as_posix()
        if not (ctx.root / f).is_file():
            continue
        body = U.section(ctx.text(f), conf.get("section", "Отказы (FMEA)"))
        scen = {_norm_name(s) for s in by_spec.get(spec, [])}
        comp = ctx.component_of(spec) or "—"
        for row in U.table_rows(body, ""):
            name = _norm_name(list(row.values())[0]) if row else ""
            if not name or any(name == s or name.startswith(s + " ") or s.startswith(name + " ") for s in scen):
                continue
            yield comp, (f"{f}: строка FMEA «{U.plain(list(row.values())[0])}» без сценария отказа в {spec} → "
                         "сценарий с тем же именем в требовании «Поведение при отказах»")


def r_rtm(ctx):
    """Строка RTM = требование: задачи = «Реализует задача», «Сценариев» = число сценариев (rules/spec-criteria.md)."""
    conf = ctx.get("requirements.rtm", {}) or {}
    reqs = ctx.requirements()
    for spec in ctx.spec_files():
        f = (Path(spec).parent / conf.get("file", "traceability.md")).as_posix()
        if not (ctx.root / f).is_file():
            continue
        comp = ctx.component_of(spec) or "—"
        for row in U.table_rows(ctx.text(f), ""):
            ids = ctx.requirement_ids_in(U.col(row, conf.get("id_col", "Требование")))
            r = reqs.get(ids[0]) if ids else None
            if not r:
                continue
            tc = U.col(row, conf.get("task_col", "Задач"))
            rt = set(ctx.find_ids("task", tc))
            if tc and rt != r.tasks and not (not rt and not r.tasks):
                yield comp, f"{f}: RTM {r.id} — задачи {sorted(rt)}, в спеке «{ctx.get('requirements.task_field')}» {sorted(r.tasks)} → выровнять RTM"
            n = U.plain(U.col(row, conf.get("count_col", "Сценари")))
            if n.isdigit() and int(n) != len(r.scenarios):
                yield comp, f"{f}: RTM {r.id} — «Сценариев» {n}, в спеке {len(r.scenarios)} → поправить RTM"


def r_block_refs(ctx):
    """ID, названные в карточке блока (операции, сбои, технология…), есть в своём поле: требования — в «Требованиях», таблицы — в «Данных»."""
    pairs = ctx.get("blocks.refs_cover", []) or []
    for b in ctx.blocks():
        for p in pairs:
            ent = ctx.ids(p["kind"])
            own_text = b.fields.get(p["field"], "")
            if not ent or U.is_empty(own_text):  # пустое поле — уже «карточка не заполнена»
                continue
            own = set(ctx.find_ids(p["kind"], own_text))
            other = " ".join(v for k, v in b.fields.items() if k != p["field"]) + " " + " ".join(b.sections.values())
            miss = sorted((set(ctx.find_ids(p["kind"], other)) & ent[1]) - own)
            if miss:
                yield b.component or "—", (f"блок «{b.title}»: {p['kind']} {', '.join(miss)} названы в карточке, "
                                           f"но не в поле «{p['field']}» → добавить")


def r_deploy_migrations(ctx):
    """Deploy компонента с данными: «Обновление и откат» говорит о миграциях (rules/deploy.md)."""
    conf = ctx.get("lint.deploy", {}) or {}
    types = set(U.as_list(conf.get("types", ["Deploy"])))
    words = [w.lower() for w in U.as_list(conf.get("words", ["миграц"]))]
    with_data = {b.component for b in ctx.blocks() if not U.is_empty(b.fields.get(conf.get("data_field", "Данные"), ""))}
    for f in ctx.md_files():
        t = ctx.type_of(f)
        if not t or t.name not in types:
            continue
        comp = ctx.component_of(f)
        body = U.section(ctx.text(f), conf.get("section", "Обновление и откат"))
        if comp in with_data and body and not any(w in body.lower() for w in words):
            yield comp, f"{f}: «{conf.get('section', 'Обновление и откат')}» без миграций, а у компонента есть данные → порядок миграции и откат после неё"


BUILTIN = {
    "task-acceptance-coverage": r_task_acceptance_coverage,
    "task-estimate-range": r_task_estimate_range,
    "open-question-refs": r_open_question_refs,
    "fmea-scenarios": r_fmea_scenarios,
    "rtm": r_rtm,
    "block-refs": r_block_refs,
    "deploy-migrations": r_deploy_migrations,
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
