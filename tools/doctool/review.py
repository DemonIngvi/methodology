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
