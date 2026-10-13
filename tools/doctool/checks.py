"""check: строгие проверки за секунды — ссылки, якоря, «сироты», трассировка требование ↔ задача, итоги задач и Roadmap,
ссылки карточек блоков, замечания, обязательные разделы, ссылки на ID, соответствие инструментов правилам, правила плагинов.

Каждая проверка — функция ctx → [сообщение]; имя проверки — префикс сообщения и ключ для `check --only` / `[check] disable`.
"""
import re
from pathlib import Path
from urllib.parse import unquote

from . import rnd as RND
from . import route as R
from . import util as U


# ---------- ссылки, якоря, «сироты» ----------

def _resolve(ctx, src_rel: str, url: str):
    path_part, _, anchor = url.partition("#")
    path_part, anchor = unquote(path_part), unquote(anchor)
    if not path_part:
        return ctx.root / src_rel, anchor
    if path_part.startswith("/"):
        return ctx.root / path_part.lstrip("/"), anchor
    return (ctx.root / src_rel).parent / path_part, anchor


def links_scan(ctx):
    """(битые ссылки, битые якоря, входящие ссылки {rel: n})."""
    styles = tuple(ctx.get("check.anchor_styles", ["github", "mkdocs"]))
    broken, bad_anchor, incoming, cache = [], [], {f: 0 for f in ctx.md_files()}, {}
    for f in ctx.md_files():
        for ln, url in U.links(ctx.text(f)):
            if url.startswith("<") or U.EXTERNAL.match(url):
                continue  # заглушка шаблона (<путь>) или внешняя ссылка
            target, anchor = _resolve(ctx, f, url)
            where = f"{f}:{ln}"
            if not target.exists():
                broken.append(f"ссылка: {where}: нет файла «{url}»")
                continue
            if target.is_dir():
                for n in ctx.get("check.index_names", ["README.md", "index.md"]):
                    if (target / n).is_file():
                        target = target / n
                        break
            trel = ctx.rel(target)
            if trel in incoming and trel != f:
                incoming[trel] += 1
            if anchor and target.is_file() and target.suffix.lower() == ".md":
                key = U.norm(target)
                if key not in cache:
                    cache[key] = U.anchors_of(U.read(target), styles)
                if anchor not in cache[key] and anchor.lower() not in cache[key]:
                    bad_anchor.append(f"якорь: {where}: нет якоря «#{anchor}» в {trel or ctx.show(target)}")
    return broken, bad_anchor, incoming


def check_links(ctx):
    b, a, _ = ctx.cached("links", lambda: links_scan(ctx))
    return b


def check_anchors(ctx):
    b, a, _ = ctx.cached("links", lambda: links_scan(ctx))
    return a


def check_orphans(ctx):
    _, _, incoming = ctx.cached("links", lambda: links_scan(ctx))
    entries = set(ctx.get("check.entries", []))
    index = set(ctx.get("check.index_names", []))
    ignore = ctx.get("check.orphans_ignore", []) or []
    nav = set()
    for n in ctx.get("check.nav", []) or []:
        if (ctx.root / n).is_file():
            base = ctx.get("check.nav_base", "")
            for m in re.findall(r"([\w./\-]+\.md)", U.read(ctx.root / n)):
                nav.add((base.rstrip("/") + "/" + m) if base else m)
    gen = [g for g in ctx.get("generated", []) or []]
    out = []
    for f, n in incoming.items():
        if n or f in entries or Path(f).name in index or f in nav or U.match(f, ignore) \
                or any(U.match(f, g.get("glob", [])) for g in gen):
            continue
        out.append(f"сирота: {f}: на файл не ведёт ни одна ссылка (дайте входящую ссылку: строка «←», индекс типа, навигация)")
    return out


# ---------- трассировка требование ↔ задача ----------

def check_trace(ctx):
    reqs, tasks = ctx.requirements(), ctx.tasks()
    issues = [f"трассировка: требование {r} объявлено дважды" for r in sorted(set(getattr(ctx, "_dup_reqs", [])))]
    no_task = U.as_list(ctx.get("requirements.no_task"))
    for rid, r in reqs.items():
        if rid.startswith("?"):
            issues.append(f"трассировка: {r.file}:{r.line}: у требования «{r.title}» нет ID")
            continue
        if ctx.get("requirements.anchor") and r.anchor.lower() != rid.lower():
            issues.append(f"трассировка: {r.file}:{r.line}: якорь перед заголовком {rid} — "
                          f"{'«' + r.anchor + '»' if r.anchor else 'нет'}, нужен <a id=\"{rid.lower()}\"></a>")
        if not r.tasks and not any(x in r.task_line for x in no_task):
            issues.append(f"трассировка: {rid}: нет задачи (поле «{ctx.get('requirements.task_field')}» или пометка {' / '.join(no_task)})")
        for t in sorted(r.tasks):
            if t not in tasks:
                issues.append(f"трассировка: {rid} → задача {t}, но такой задачи нет")
            elif rid not in tasks[t].reqs:
                issues.append(f"трассировка: {rid} → {t}, но в задаче {t} нет {rid}")
    for tid, t in tasks.items():
        if not t.reqs:
            issues.append(f"трассировка: задача {tid} ({t.file}): нет требований")
        for rid in sorted(t.reqs):
            if rid not in reqs:
                issues.append(f"трассировка: задача {tid} → {rid}, но такого требования нет")
            elif not t.closed and tid not in reqs[rid].tasks:
                issues.append(f"трассировка: задача {tid} → {rid}, но у {rid} нет задачи {tid}")
    return issues


# ---------- задачи: итоги и Roadmap ----------

def check_tasks(ctx):
    tasks, issues = ctx.tasks(), []
    for tid, t in tasks.items():
        if t.subtasks and t.total is not None:
            s = sum(h for _, h, _ in t.subtasks if h is not None)
            if abs(s - t.total) > 0.01:
                issues.append(f"задачи: {tid}: сумма подзадач {s:g} ч ≠ «{ctx.get('tasks.subtasks.total_label')}» {t.total:g} ч")
    rm = ctx.get("tasks.roadmap", "")
    idrx = ctx.get("tasks.id")
    rm_ids = []
    if rm and tasks:
        p = ctx.root / rm
        if not p.is_file():
            issues.append(f"задачи: нет Roadmap {rm} (конфиг tasks.roadmap)")
        else:
            text = ctx.text(rm)
            hc = int(ctx.get("tasks.roadmap_hours_col", 0) or 0)
            stage_sum, label = 0.0, ctx.get("tasks.roadmap_total_label", "Итого")
            for l in text.splitlines():
                if not l.strip().startswith("|"):
                    if l.startswith("#"):
                        stage_sum = 0.0
                    continue
                cells = U.split_row(l)
                if not cells:
                    continue
                first = U.plain(cells[0])
                if re.fullmatch(idrx, first):
                    rm_ids.append(first)
                    if hc and first in tasks and len(cells) >= hc:
                        h = U.first_number(cells[hc - 1])
                        stage_sum += h or 0
                        tot = tasks[first].total
                        if h is not None and tot is not None and abs(h - tot) > 0.01:
                            issues.append(f"задачи: {rm}: у {first} {h:g} ч, в задаче «{label}» {tot:g} ч")
                elif hc and any(label in U.plain(c) for c in cells[:2]) and len(cells) >= hc:
                    h = U.first_number(cells[hc - 1])
                    if h is not None and abs(h - stage_sum) > 0.01:
                        issues.append(f"задачи: {rm}: итог «{U.plain(l)[:40]}» {h:g} ч ≠ сумма задач {stage_sum:g} ч")
            issues += [f"задачи: задача {tid} ({t.file}) нет в {rm}" for tid, t in tasks.items() if tid not in rm_ids]
            issues += [f"задачи: {rm}: задача {x} без документа задачи" for x in rm_ids if x not in tasks]
    for d in ctx.get("tasks.derived", []) or []:
        f = d["file"]
        have = set(re.findall(d.get("pattern", r"^- \[[ x]\] (" + idrx + ")"), ctx.text(f) if (ctx.root / f).is_file() else "", re.M))
        want = set(rm_ids or tasks)
        issues += [f"задачи: {f}: нет задачи {x} (производный список — перегенерируй: {d.get('by', 'генератор')})" for x in sorted(want - have)]
        issues += [f"задачи: {f}: лишняя задача {x}" for x in sorted(have - want)]
    return issues


# ---------- карточки блоков: ссылки существуют ----------

def check_blocks(ctx):
    issues = []
    kinds = ctx.get("blocks.kinds", {}) or {}
    for b in ctx.blocks():
        where = f"блок: «{b.title}» ({b.file})"
        if kinds and b.kind and b.kind not in kinds:
            issues.append(f"{where}: вид «{b.kind}» — нужен один из: {', '.join(kinds)}")
        for fdef in ctx.get("blocks.fields", []):
            val = b.fields.get(fdef["name"], "")
            for kind in U.as_list(fdef.get("refs")):
                ent = ctx.ids(kind)
                if not ent or U.is_empty(val):
                    continue
                for x in ctx.find_ids(kind, val):
                    if x not in ent[1]:
                        issues.append(f"{where}: поле «{fdef['name']}»: {kind} {x} не существует")
    return issues


# ---------- замечания ролей ----------

def finding_rx(ctx, code: str):
    fmt = ctx.get("findings.id", "Z-{code}-{n}")
    return re.compile("^" + re.escape(fmt).replace(r"\{code\}", re.escape(code)).replace(r"\{n\}", r"(\d+)") + "$")


def findings_files(ctx) -> dict:
    """{компонент: rel} существующих файлов замечаний."""
    d, pat = ctx.get("findings.dir", ""), ctx.get("findings.file", "{component}.md")
    rx = re.compile("^" + re.escape(pat).replace(r"\{component\}", "(?P<c>.+)") + "$")
    out = {}
    for f in ctx.glob(f"{d}/*" if d else "*"):
        m = rx.match(Path(f).name)
        if m and f.lower().endswith(".md") and Path(f).name.lower() not in ("readme.md", "index.md"):
            out[m.group("c")] = f
    return out


STATUSES = ("✅", "⏳", "📌", "отклонено", "❌")


def check_findings(ctx):
    issues = []
    for comp, f in findings_files(ctx).items():
        c = ctx.component(comp)
        code = c["code"] if c else comp.upper()
        rx, seen = finding_rx(ctx, code), set()
        for r in U.table_rows(ctx.text(f), "| №"):
            fid = U.plain(r.get("№", ""))
            if not fid or U.is_empty(fid):
                continue
            if not rx.match(fid):
                issues.append(f"замечания: {f}: ID «{fid}» не по формату {ctx.get('findings.id').replace('{code}', code)}")
            if fid in seen:
                issues.append(f"замечания: {f}: ID {fid} повторяется (ID не переиспользуется)")
            seen.add(fid)
            st = U.plain(list(r.values())[-1])
            if not st.startswith(STATUSES):
                issues.append(f"замечания: {f}: у {fid} статус «{st[:20]}» — нужен ✅ / ⏳ / 📌 / отклонено")
    return issues


# ---------- обязательные разделы по типу ----------

def missing_sections(ctx, rel, patterns) -> list:
    lines = [l for _, l in U.md_lines(ctx.text(rel))]
    return [p for p in patterns if not any(re.search(p if p.startswith("^") else "^" + re.escape(p), l) for l in lines)]


def check_sections(ctx):
    issues = []
    for f in ctx.md_files():
        t = ctx.type_of(f)
        if t and t.sections and not ctx.generated_of(f):
            miss = missing_sections(ctx, f, t.sections)
            if miss:
                issues.append(f"разделы: {f} ({t.name}): нет {', '.join(m.lstrip('^') for m in miss)}")
    return issues


# ---------- ссылки на ID существуют (не выдумывать D-N, R-…) ----------

def check_ids(ctx):
    issues = []
    for kind, conf in (ctx.get("ids", {}) or {}).items():
        if not conf.get("check_refs"):
            continue
        rx, defined = ctx.ids(kind)
        for f in ctx.glob(conf.get("refs_in", ["**/*.md"])):
            if U.match(f, conf.get("refs_exclude", [])):
                continue
            for ln, line in U.md_lines(ctx.text(f)):
                for m in rx.finditer(re.sub(r"`[^`]*<[^`]*`", "", line)):
                    x = m.group(1) if m.groups() else m.group(0)
                    if x not in defined:
                        issues.append(f"ссылки на ID: {f}:{ln}: {kind} {x} не существует")
    return issues


# ---------- инструменты соответствуют правилам ----------

def check_tooling(ctx):
    issues = []
    if ctx.get("types_table.file") and not ctx.rules_file(ctx.get("types_table.file")):
        issues.append(f"инструменты: нет таблицы типов {ctx.show(ctx.rules_dir / ctx.get('types_table.file'))} (types_table.file, project.rules_dir)")
    for t in ctx.types():
        if t.methodology and not ctx.rules_file(t.methodology):
            issues.append(f"инструменты: тип «{t.name}»: методология {t.methodology} не найдена в {ctx.show(ctx.rules_dir)}")
        exact = [g for g in t.globs if "*" not in g and "?" not in g and (ctx.root / g).is_file()]
        if exact:
            got = ctx.type_of(exact[0])
            if got and got.name != t.name:
                issues.append(f"инструменты: route {exact[0]} выдаёт тип «{got.name}», ожидался «{t.name}»")
        if ctx.role_letters():
            issues += [f"инструменты: тип «{t.name}»: роль «{x}» не описана (roles)" for x in t.approve if x not in ctx.role_letters()]
    letters = ctx.role_letters()
    if not letters and any(t.approve for t in ctx.types()):
        issues.append("инструменты: роли не описаны — нет [[roles]] в конфиге и таблицы «| Буква | Роль | Вопрос |» в правилах подтверждений")
    if len(letters) != len(set(letters)):
        issues.append("инструменты: буквы ролей повторяются — значок роли должен быть одним символом")
    ns = R.nodes(ctx)
    for a, b, _ in R.edges(ctx):
        issues += [f"инструменты: граф: узел «{x}» в связи не описан в [graph.nodes]" for x in (a, b) if x not in ns]
    issues += [f"инструменты: граф: у узла «{n}» нет ни одного файла ({', '.join(ps)})" for n, ps in ns.items() if ps and not ctx.glob(ps)]
    tops = {f.split("/", 1)[0] for f in ctx.files() if "/" in f}
    for f in ctx.glob(ctx.get("tooling.docs", [])):
        for ln, line in U.md_lines(ctx.text(f)):
            for p in re.findall(r"`([\w.\-]+/[\w./\-]+)`", line):
                if "<" in p or "*" in p or p.split("/", 1)[0] not in tops:
                    continue
                if not (ctx.root / p).exists():
                    issues.append(f"инструменты: {f}:{ln}: путь `{p}` не существует")
    for g in ctx.get("generated", []) or []:
        if not g.get("glob"):
            issues.append("инструменты: [[generated]] без glob")
    return issues


# ---------- данные YAML по единице и журнал расхода ----------

def check_yaml(ctx):
    """yaml-check внутри check: схемы видов [[data.kinds]], ссылки, порядок. Без PyYAML — пропуск с предупреждением."""
    from . import units as UN
    if not UN.kinds(ctx) or not UN.unit_files(ctx):
        return []
    if UN.yaml_mod() is None:
        if ctx.get("data.require_yaml"):
            return ["yaml: нужен PyYAML (pip install pyyaml) — данные data/ не проверены"]
        import sys
        sys.stderr.write("doc: yaml-check пропущен — нет PyYAML (pip install pyyaml); [data] require_yaml = true — считать замечанием\n")
        return []
    return [f"yaml: {x}" for x in UN.check(ctx)]


def check_costs(ctx):
    from . import costs as CO
    return CO.check(ctx)


BUILTIN = {
    "links": check_links,
    "anchors": check_anchors,
    "orphans": check_orphans,
    "trace": check_trace,
    "tasks": check_tasks,
    "blocks": check_blocks,
    "findings": check_findings,
    "sections": check_sections,
    "ids": check_ids,
    "tooling": check_tooling,
    "rnd": lambda ctx: RND.check(ctx),
    "yaml": check_yaml,
    "costs": check_costs,
}


def run(ctx, only=None) -> list:
    from . import approvals as A
    disabled = set(ctx.get("check.disable", []) or [])
    issues = []
    if ctx.get("approvals.enabled") and ctx.get("approvals.sync_on_check") and (not only or "approvals" in only) \
            and "approvals" not in disabled:
        issues += A.sync_locked(ctx)
        ctx.invalidate()  # реестр и сводка могли появиться или измениться
    rules = dict(BUILTIN)
    if ctx.plugins:
        rules.update({f"plugin:{k}": v for k, v in ctx.plugins.check_rules.items()})
    for name, fn in rules.items():
        short = name.split(":", 1)[-1]
        if name in disabled or short in disabled or (only and name not in only and short not in only):
            continue
        res = list(fn(ctx) or [])
        issues += [x if name in BUILTIN else f"{short}: {x}" for x in res]
    return issues
