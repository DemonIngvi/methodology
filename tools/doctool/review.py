"""review-pack (пакет проверки для роли) и finding (статусы замечаний ролей)."""
import re
from pathlib import Path

from . import approvals as A
from . import checks as K
from . import lint as LN
from . import util as U


def _reqs(ctx, text: str):
    """Требования, на которые ссылается страница: заголовок и сценарии; файлы открытых вопросов их спек."""
    reqs = ctx.requirements()
    out, qfiles = [], []
    for rid in ctx.requirement_ids_in(text):
        r = reqs.get(rid)
        if not r:
            out.append(f"- **{rid}** — нет в спеках (ссылка на несуществующее требование)")
            continue
        sc = "; ".join(f"«{s}»" for s in r.scenarios) or "сценариев нет"
        out.append(f"- **{rid}** {r.title} ({r.file}): {sc}")
        q = (Path(r.file).parent / ctx.get("requirements.open_questions", "open-questions.md")).as_posix()
        if q not in qfiles and (ctx.root / q).is_file():
            qfiles.append(q)
    return out, qfiles


def pack(ctx, page: str, letter: str) -> Path:
    rel = ctx.rel(page) if Path(page).exists() else page.replace("\\", "/")
    if not rel or not (ctx.root / rel).is_file():
        raise SystemExit(f"{page}: нет такого файла в проекте")
    letter = ctx.role_letter(letter)
    text = ctx.text(rel)
    t = ctx.type_of(rel)
    role = next(((l, n, q) for l, n, q in ctx.roles() if l == letter), (letter, letter, ""))
    meth = ctx.rules_file(t.methodology) if t and t.methodology else None
    check = ""
    if meth:
        mt = U.read(meth)
        for s in ([t.checklist] if t.checklist else []) + U.as_list(ctx.get("review.checklist_sections")):
            check = U.section(mt, s)
            if check:
                break
    reqs, qfiles = _reqs(ctx, text)
    qrx = re.compile(ctx.get("requirements.open_question_row"))
    oq = [f"{l}  ({q})" for q in qfiles for l in ctx.text(q).splitlines() if qrx.match(l)]
    comp = ctx.component_of(rel) or next((t.component for t in ctx.tasks().values() if t.file == rel), "")
    ff = K.findings_files(ctx).get(comp)
    prev = [l for l in (ctx.text(ff).splitlines() if ff else []) if l.startswith("|") and (rel in l or Path(rel).name in l)]
    lint = LN.run(ctx).get(comp or "—", [])
    reg = A.read_registry(ctx).get((rel, letter))
    cmd = ctx.command
    L = [f"# Пакет проверки: `{rel}` × {letter}", "",
         f"**Тип:** {t.name if t else '—'}. **Роль:** {role[1]}. **Вопрос роли:** {role[2] or '—'}", "",
         "**Как решать (экономная проверка):** отказ — только за то, что мешает ответить на вопрос роли, с фактом-источником; "
         "не больше 3 замечаний; механическое уже проверил lint (ниже) — не повторять. Повторная проверка — только изменённое "
         "и закрытие прежних замечаний.", "",
         f"**Прежнее решение:** {(' | '.join(reg[2:8])) if reg else '—'}", "",
         f"**Решение:** `{cmd} approve {rel} {letter} ok \"<что проверено>\"` или "
         f"`{cmd} approve {rel} {letter} no \"<чего не хватает> → <что исправить, где>\"`", "",
         f"## Чек-лист методологии типа{f' ({ctx.show(meth)})' if meth else ''}", "", check or "—", "",
         f"## Требования страницы ({len(reqs)})", "", *(reqs or ["—"]), "",
         "## Открытые вопросы спек этих требований", "", *(oq or ["—"]), "",
         "## Прежние замечания по странице", "", *(prev or ["—"]), "",
         f"## lint компонента {comp or '—'} ({len(lint)})", "", *([f"- {x}" for x in lint] or ["—"]), "",
         "## Страница", "", text]
    out = ctx.work_dir / "review" / (rel.replace("/", "__") + f"__{letter}.md")
    U.write(out, "\n".join(L))
    return out


MARK = {"ok": "✅", "wait": "⏳", "owner": "📌", "reject": "отклонено"}


def finding(ctx, comp: str, ids: str, status: str, note: str) -> str:
    files = K.findings_files(ctx)
    f = files.get(comp)
    if not f:
        d = ctx.get("findings.dir")
        raise SystemExit(f"нет файла замечаний компонента «{comp}» ({d}/{ctx.get('findings.file').replace('{component}', comp)})")
    if status not in MARK:
        raise SystemExit(f"статус: {' | '.join(MARK)}")
    lines = ctx.text(f).split("\n")
    want = [x.strip() for x in ids.split(",") if x.strip()]
    done = []
    for i, l in enumerate(lines):
        if not l.startswith("|"):
            continue
        cells = U.split_row(l)
        if cells and U.plain(cells[0]) in want:
            cells[-1] = MARK[status] + (f" {note.replace('|', '/')}" if note else "")
            lines[i] = "| " + " | ".join(cells) + " |"
            done.append(U.plain(cells[0]))
    U.write(ctx.root / f, "\n".join(lines))
    miss = [x for x in want if x not in done]
    return f"{comp}: обновлено {len(done)}" + (f"; не найдены: {', '.join(miss)}" if miss else "")


def next_finding_id(ctx, comp: str) -> str:
    c = ctx.component(comp)
    code = c["code"] if c else comp.upper()
    rx = K.finding_rx(ctx, code)
    f = K.findings_files(ctx).get(comp)
    nums = [int(m.group(1)) for l in (ctx.text(f).splitlines() if f else []) if l.startswith("|")
            for m in [rx.match(U.plain(U.split_row(l)[0]) if U.split_row(l) else "")] if m]
    return ctx.get("findings.id").replace("{code}", code).replace("{n}", str(max(nums, default=0) + 1))


# ---------- пакетная проверка (rules/approvals.md#economy) ----------
# Карточка страницы — то, что нужно всем ролям сразу, без ссылок, схем и HTML: цель ~1–1,5 тыс. токенов на документ за все роли.

def _clean(s) -> str:
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", str(s))
    s = re.sub(r"<br\s*/?>", "; ", s)
    s = re.sub(r"</?[a-zA-Z][^>]*>|\s*\{#[^}]*\}", "", s)
    return re.sub(r"\s+", " ", s.replace("`", "").replace("**", "")).strip()


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(n - 1, 0)].rstrip() + "…"


def _fit(lines: list, budget: int) -> list:
    """Обрезать строки одним потолком, пока сумма не влезет в бюджет; остаток — «… ещё строк»."""
    cap = max((len(l) for l in lines), default=0)
    while cap > 60 and sum(min(len(l), cap) + 1 for l in lines) > budget - 20:
        cap -= 10
    out, total = [], 0
    for l in lines:
        l = _cut(l, cap)
        if total + len(l) + 1 > budget - 20:
            out.append(f"… ещё строк: {len(lines) - len(out)}")
            break
        out.append(l)
        total += len(l) + 1
    return out


def _flat(v) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_flat(x)}" for k, x in v.items() if x not in ("", [], {}, None))
    if isinstance(v, list):
        return "; ".join(_flat(x) for x in v if x not in ("", [], {}, None))
    return _clean(v)


def _scenarios(r) -> list:
    """[(имя, WHEN, THEN)] сценариев требования."""
    out = []
    for m in re.finditer(r"^####\s+Scenario:\s*(.+?)\s*$((?:\n(?!#).*)*)", r.block, re.M):
        body = m.group(2)
        w = re.search(r"\*\*WHEN\*\*\s*(.+)", body)
        t = re.search(r"\*\*THEN\*\*\s*(.+)", body)
        out.append((m.group(1), _clean(w.group(1)) if w else "", _clean(t.group(1)) if t else ""))
    return out


def _req_lines(ctx, ids, budget: int) -> list:
    """«ID Название: когда→то; …» — не больше 3 сценариев; при нехватке места — короче и меньше."""
    reqs = ctx.requirements()
    ids = [i for i in dict.fromkeys(ids) if i in reqs]
    lines = []
    for n, k in ((3, 90), (3, 60), (2, 50), (1, 50), (1, 35), (0, 0)):
        lines = []
        for i in ids:
            sc = _scenarios(reqs[i])[:n]
            lines.append(f"{i} {reqs[i].title}" + (": " + "; ".join(f"{_cut(w or nm, k)}→{_cut(t, k)}" for nm, w, t in sc) if sc else ""))
        if sum(len(l) + 1 for l in lines) <= budget:
            return lines
    return _fit(lines, budget)


def _sections_card(ctx, rel: str, text: str):
    """Заголовки разделов и первые две строки каждого: «Раздел: строка / строка»."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out, left = [], 0
    for l in text.splitlines():
        s = l.strip()
        if re.match(r"#{1,4} ", l):
            out.append([_clean(l.lstrip("# ")), []])
            left = 2
        elif out and left and s and not re.fullmatch(r"\|[-| :]+\|", s) and not s.startswith(("←", ">", "<")):
            t = " · ".join(_clean(x) for x in U.split_row(s)) if s.startswith("|") else _clean(s.lstrip("-* "))
            if t:
                out[-1][1].append(t)
                left -= 1
    return [h + (": " + " / ".join(b) if b else "") for h, b in out], ctx.requirement_ids_in(text)


def _task_card(ctx, rel: str, text: str):
    t = next((x for x in ctx.tasks().values() if x.file == rel), None)
    L = [_clean(next((l for l in text.splitlines() if l.startswith("# ")), rel).lstrip("# "))]
    est = ctx.get("tasks.estimate", {}) or {}
    rows = U.table_rows(text, est.get("table", "| Этап |"))
    if rows:
        L.append(" · ".join(f"{_clean(k)} {_clean(v)}" for k, v in rows[0].items() if not U.is_empty(v)))
    ids = sorted(t.reqs) if t else []
    if ids:
        L.append("закрывает: " + ", ".join(ids))
    if t and t.subtasks:
        L.append("подзадачи: " + "; ".join(f"{n} {h:g} ч — {_cut(_clean(d), 60)}" if h is not None else f"{n} — {_cut(_clean(d), 60)}"
                                           for n, h, d in t.subtasks))
    acc = [_clean(l.split("]", 1)[1]) for l in U.section(text, "Приёмка").splitlines() if re.match(r"^\s*- \[", l)]
    if acc:
        L.append("приёмка: " + "; ".join(acc))
    q = [_clean(l.split("]", 1)[1]) for l in U.section(text, ctx.get("tasks.open_items_section", "Что проработать")).splitlines()
         if re.match(r"^\s*- \[ \]", l)]
    if q:
        L.append("открыто: " + "; ".join(q))
    return L, ids


def _block_card(ctx, rel: str, text: str):
    b = next((x for x in ctx.blocks() if x.file == rel), None)
    if not b:
        return _sections_card(ctx, rel, text)
    tech = b.fields.get("Технология", "")
    L = [f"блок {b.title} · {b.kind or '—'}" + (f" · {_clean(tech)}" if tech else "")]
    L += [f"{k}: {_clean(v)}" for k, v in b.fields.items() if k not in ("Технология", "Требования") and not U.is_empty(v)]
    for k, v in b.sections.items():
        if k == b.kind and not U.is_empty(v):
            rows = U.table_rows(v, "")
            L.append(f"{k}: " + ("; ".join(" · ".join(_clean(x) for x in r.values() if not U.is_empty(x)) for r in rows) if rows else _clean(v)))
    return L, ctx.requirement_ids_in(b.fields.get("Требования", "")) or ctx.requirement_ids_in(text)


def _unit_card(ctx, rel: str, text: str):
    from . import units as UN
    k = UN.kind_of(ctx, rel)
    if not k or UN.yaml_mod() is None:
        return _sections_card(ctx, rel, text)
    try:
        v = UN.load(ctx.root / rel) or {}
    except Exception:
        v = {}
    v = v if isinstance(v, dict) else {"значение": v}
    tf = k.get("title") or "title"
    L = [f"{k['name']} {UN.unit_id(k, rel, v)}" + (f" · {_clean(v[tf])}" if v.get(tf) else "")]
    L += [f"{key}: {_flat(x)}" for key, x in v.items() if key != tf and x not in ("", [], {}, None)]
    return L, ctx.requirement_ids_in(text)


CARDS = {"task": _task_card, "block": _block_card, "unit": _unit_card, "sections": _sections_card}


def _findings_open(ctx, rel: str, comp: str) -> list:
    ff = K.findings_files(ctx).get(comp)
    out = []
    for r in (U.table_rows(ctx.text(ff), "| №") if ff else []):
        doc = U.plain(U.col(r, "Документ"))
        st = U.plain(list(r.values())[-1])
        if (rel == doc or rel.startswith(doc + "/")) and not st.startswith(("✅", "отклонено")):
            out.append(_cut(f"{U.plain(r.get('№', ''))} ({U.plain(U.col(r, 'Роли'))}): {_clean(U.col(r, 'Отклонение'))} → "
                            f"{_clean(U.col(r, 'Как закрыть'))}", 200))
    return out


def comp_of(ctx, rel: str) -> str:
    """Компонент страницы реестра; «—» — общая страница."""
    from . import units as UN
    c = ctx.component_of(rel)
    if not c and UN.kind_of(ctx, rel):
        c = UN.component_of(ctx, rel)
    if not c:
        c = next((t.component for t in ctx.tasks().values() if t.file == rel), "") \
            or next((b.component for b in ctx.blocks() if b.file == rel), "")
    return c or "—"


def _req_card(ctx, rid: str) -> str:
    r = ctx.requirements()[rid]
    stmt = next((l for l in r.block.splitlines()[1:] if re.search(r"\bSHALL\b|\bДОЛЖ", l)), "")
    L = [f"{r.file} · {r.title}"] + ([_clean(stmt)] if stmt else [])
    L += [f"«{n}»: {w} → {t}" for n, w, t in _scenarios(r)]
    return "\n".join(_fit(L, int(ctx.get("review.block_max", 1500))))


def card(ctx, rel: str) -> str:
    """Компактная карточка страницы для всех ролей сразу (≤ card_max символов; блок и единица данных ≤ block_max).

    Способ — `card` типа в конфиге (task | block | unit | sections | плагин @card_kind); без него — по тому, чем страница
    является: единица data/, карточка блока, задача, иначе разделы с первыми строками."""
    from . import units as UN
    t = ctx.type_of(rel)
    text = ctx.text(rel)
    how = (t.card if t else "") or ("unit" if UN.kind_of(ctx, rel) else "")
    if not how and any(b.file == rel for b in ctx.blocks()):
        how = "block"
    if not how and any(x.file == rel for x in ctx.tasks().values()):
        how = "task"
    budget = int(ctx.get("review.block_max" if how in ("block", "unit") else "review.card_max", 2500))
    fn = (ctx.plugins.card_kinds.get(how) if ctx.plugins else None) or CARDS.get(how) or _sections_card
    res = fn(ctx, rel, text)
    body, ids = res if isinstance(res, tuple) else (list(res), ctx.requirement_ids_in(text))
    fnd = _findings_open(ctx, rel, comp_of(ctx, rel))
    tail = ["незакрытые замечания: " + " | ".join(fnd)] if fnd else []
    reqs = ctx.requirements()
    ids = [i for i in dict.fromkeys(ids) if i in reqs]
    reserve = min(budget // 2, 60 + sum(len(i) + len(reqs[i].title) + 80 for i in ids)) if ids else 0
    body = _fit([_clean(x) for x in body], budget - reserve - sum(len(x) + 1 for x in tail))
    rl = _req_lines(ctx, ids, budget - sum(len(x) + 1 for x in body + tail) - 25) if ids else []
    return "\n".join(body + ([f"требования ({len(rl)}):"] + rl if rl else []) + tail)


def batch(ctx, comp: str, maxn: int = 0) -> list:
    """Пачки ⏳-страниц компонентов (через запятую) в .doctool/review/batch-<компоненты>-<N>.md → [(путь .md, путь .json, страниц, пар, символов)]."""
    maxn = maxn or int(ctx.get("review.batch_max", 40))
    wants = [("—" if c.strip() in ("-", "—", "") else c.strip()) for c in comp.split(",")]
    want = "+".join(wants)
    pages = {}
    by_req = ctx.get("requirements.approvals.enabled")
    specs = set(ctx.spec_files())
    for page, letter, _ in A.queue(ctx):
        if not (ctx.root / page).is_file() or comp_of(ctx, page) not in wants:
            continue
        if by_req and page in specs:  # спека подтверждается по требованиям: пара — «ID требования × роль»
            for (rid, l), c in A._read_req(A.req_file(ctx, page)).items():
                if l == letter and c[5] == "⏳" and letter not in pages.get(rid, []):
                    pages.setdefault(rid, []).append(letter)
            continue
        pages.setdefault(page, []).append(letter)
    order = ctx.role_letters()
    items = sorted(pages.items())
    cmd = ctx.command
    res = []
    for n, i in enumerate(range(0, len(items), maxn), 1):
        chunk = items[i:i + maxn]
        letters = [x for x in order if any(x in ls for _, ls in chunk)]
        out = ctx.work_dir / "review" / f"batch-{want}-{n}.md"
        jf = out.with_suffix(".json")
        H = [f"# Пачка проверки: {want} · {n}", "", "Вопросы ролей:",
             *[f"- {x} {ctx.role_name(x)}: {next((q for l, _, q in ctx.roles() if l == x), '') or '—'}" for x in letters], "",
             "Правило: отказ (ok=false) — только за блокирующее, с фактом из карточки; ≤2 причины; механику не повторять — её ловит lint. "
             f"Ответ — JSON-массив {{\"page\", \"role\", \"ok\", \"comment\"}} по всем парам ниже в файл {ctx.show(jf)}, "
             f"затем {cmd} approve-batch {ctx.show(jf)}", ""]
        for p, ls in chunk:
            c = _req_card(ctx, p) if p in ctx.requirements() else card(ctx, p)
            H += [f"## {p} · роли: {''.join(x for x in order if x in ls)}", "", c, ""]
        txt = "\n".join(H)
        U.write(out, txt)
        res.append((out, jf, len(chunk), sum(len(ls) for _, ls in chunk), len(txt)))
    return res
