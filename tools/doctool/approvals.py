"""Подтверждения ролями (rules/approvals.md): реестр «документ × роль» с версией текста, сброс решения при правке,
решения `approve`, очередь `review-queue`, сводная таблица; подтверждения требований спеки — рядом со спекой.

Реестр пишут параллельно несколько проверяющих — запись идёт под файловой блокировкой (`<реестр>.lock`).
"""
import contextlib
import collections
import datetime
import os
import re
import time
from pathlib import Path

from . import util as U

HEAD = "| Документ | Роль | Кто | 👤 / 🤖 | Дата | Решение | Версия | Комментарий |"
RHEAD = "| Требование | Роль | Кто | 👤 / 🤖 | Дата | Решение | Версия | Комментарий |"
SEP = "|---|---|---|---|---|---|---|---|"
PREV = re.compile(r"после решения «([^»]+)»")


# ---------- блокировка ----------

@contextlib.contextmanager
def lock(ctx, target: Path):
    lk = Path(str(target) + ".lock")
    lk.parent.mkdir(parents=True, exist_ok=True)
    timeout = float(os.environ.get("DOC_LOCK_TIMEOUT") or ctx.get("approvals.lock_timeout", 30))
    stale = float(ctx.get("approvals.stale_lock", 300))
    t0, fd = time.time(), None
    while fd is None:
        try:
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode())
        except FileExistsError:
            try:
                if time.time() - lk.stat().st_mtime > stale:  # брошенная блокировка (процесс упал)
                    lk.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > timeout:
                raise SystemExit(f"реестр занят: {ctx.show(lk)} держится дольше {timeout:g} с; удалите файл, если запись не идёт")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            lk.unlink()


# ---------- что подтверждается ----------

def registry_path(ctx) -> Path:
    return ctx.root / ctx.get("approvals.registry", "approvals.md")


def pages(ctx) -> dict:
    """{страница: (тип, буквы)} — документы, у типа которых есть подтверждающие роли."""
    reg = ctx.get("approvals.registry", "")
    summ = ctx.get("approvals.summary", "")
    rfile = ctx.get("requirements.approvals.file", "")
    skip = ctx.get("approvals.skip", []) or []
    out = {}
    for f in ctx.files():
        if f in (reg, summ, reg + ".lock") or U.match(f, skip):
            continue
        if rfile and Path(f).name == rfile and any(Path(s).parent == Path(f).parent for s in ctx.spec_files()):
            continue
        t = ctx.type_of(f)
        if t and t.approve:
            out[f] = (t, t.approve)
    return out


def version(ctx, rel: str) -> str:
    return U.short_hash(ctx.text(rel), ctx.get("approvals.version_ignore", []) or [])


def req_roles(ctx, title: str) -> str:
    conf = ctx.get("requirements.approvals", {}) or {}
    for k, v in (conf.get("by_title") or {}).items():
        if title.lower().startswith(k.lower()):
            return "".join(U.as_list(v)) if isinstance(v, list) else v
    f = ctx.rules_file(ctx.get("roles_table.file", "approvals.md"))
    if f:
        for r in U.table_rows(U.read(f), ctx.get("roles_table.requirements_header", "| Требование | Роли |")):
            name = U.plain(U.col(r, "Требование"))
            if name and title.lower().startswith(name.lower()):
                cell = U.plain(U.col(r, "Роли"))
                letters = "".join(l for l, n, _ in ctx.roles() if n.lower() in cell.lower())
                if letters:
                    return letters
    return conf.get("default", "")


# ---------- чтение и запись таблиц ----------

def _read(path: Path, prefix: str) -> dict:
    rows = {}
    if path.is_file():
        for l in U.read(path).splitlines():
            if l.startswith(prefix):
                c = U.split_row(l)
                c += ["—"] * (8 - len(c))
                rows[(c[0].strip("`"), c[1])] = c[:8]
    return rows


def _read_req(path: Path) -> dict:
    return {k: v for k, v in _read(path, "| ").items() if k[0] != "Требование"}


def read_registry(ctx) -> dict:
    return _read(registry_path(ctx), "| `")


def _rel_link(ctx, from_file: Path, target: Path) -> str:
    return Path(os.path.relpath(target, from_file.parent)).as_posix()


def _write_registry(ctx, rows: dict):
    p = registry_path(ctx)
    meth = ctx.rules_dir / ctx.get("roles_table.file", "approvals.md")
    order = ctx.role_letters()
    body = ["# Подтверждения документов", "",
            f"← [методология подтверждений]({_rel_link(ctx, p, meth)})"
            + (f" · [сводная таблица]({_rel_link(ctx, p, ctx.root / ctx.get('approvals.summary'))})" if ctx.get("approvals.summary") else ""), "",
            f"Реестр ведёт инструмент (`{ctx.command} approve`, синхронизация в `{ctx.command} check`); руками не правится. "
            "⏳ — ждёт роли; ✅ — подтверждено; ❌ — отказ (причина в комментарии). "
            "Решение сбрасывается в ⏳, если текст документа изменился (столбец «Версия» — хеш текста).", "", HEAD, SEP]
    for k in sorted(rows, key=lambda k: (k[0], order.index(k[1]) if k[1] in order else 99)):
        body.append("| " + " | ".join(rows[k]) + " |")
    text = "\n".join(body) + "\n"
    if not p.is_file() or U.read(p) != text:
        U.write(p, text)


def _write_req(ctx, spec: str, path: Path, rows: dict):
    order = ctx.role_letters()
    reqs = ctx.requirements()

    def key(k):
        r = reqs.get(k[0])
        return (r.line if r else 10 ** 9, k[0], order.index(k[1]) if k[1] in order else 99)
    body = [f"# Подтверждения требований: {Path(spec).parent.name}", "",
            f"← [спека]({Path(spec).name}) · [правило]({_rel_link(ctx, path, ctx.rules_dir / ctx.get('roles_table.file', 'approvals.md'))}#requirements)", "",
            f"Строки ⏳ добавляет `{ctx.command} check` по обязательным ролям; решение — `{ctx.command} approve <ID требования> <буква> ok|no \"<комментарий>\"`. "
            "«Версия» — хеш текста требования: текст изменился после решения — решение сбрасывается в ⏳.", "", RHEAD, SEP]
    for k in sorted(rows, key=key):
        body.append("| " + " | ".join(rows[k]) + " |")
    text = "\n".join(body) + "\n"
    if not path.is_file() or U.read(path) != text:
        U.write(path, text)


def _reconcile(c: list, ver: str, label: str, ident: str) -> list:
    if c[6] != ver and c[5] != "⏳":
        who = c[2] if c[2] not in ("", "—") else "роли"
        return [ident, c[1], "—", "—", "—", "⏳", ver, f"текст изменён после решения «{c[5]}» от {who}"]
    c[6] = ver
    return c


# ---------- синхронизация ----------

def sync(ctx) -> dict:
    """Строки ⏳ для обязательных пар, сброс решения по изменённому тексту, удаление лишних строк.
    → {страница: [(буква, решение, 👤/🤖, было)]} для сводки и очереди."""
    rows = read_registry(ctx)
    status, keep = {}, {}
    for page, (t, letters) in pages(ctx).items():
        ver = version(ctx, page)
        for letter in letters:
            key = (page, letter)
            c = rows.get(key) or [f"`{page}`", letter, "—", "—", "—", "⏳", ver, t.name]
            c = _reconcile(c, ver, t.name, f"`{page}`")
            keep[key] = c
            prev = PREV.search(c[7])
            status.setdefault(page, []).append((letter, c[5], c[3], prev.group(1) if prev and c[5] == "⏳" else ""))
    _write_registry(ctx, keep)
    if ctx.get("requirements.approvals.enabled"):
        status.update(sync_requirements(ctx))
    if ctx.get("approvals.summary"):
        write_summary(ctx, status)
    return status


def req_file(ctx, spec: str) -> Path:
    return (ctx.root / spec).parent / ctx.get("requirements.approvals.file", "approvals.md")


def sync_requirements(ctx) -> dict:
    status = {}
    ign = ctx.get("requirements.approvals.version_ignore", []) or []
    by_spec = {}
    for rid, r in ctx.requirements().items():
        if not rid.startswith("?"):
            by_spec.setdefault(r.file, []).append(r)
    for spec in ctx.spec_files():
        path = req_file(ctx, spec)
        old = _read_req(path)
        keep, per = {}, {}
        for r in by_spec.get(spec, []):
            ver = U.short_hash(r.block, ign)
            for letter in req_roles(ctx, r.title):
                c = old.get((r.id, letter)) or [r.id, letter, "—", "—", "—", "⏳", ver, r.title]
                c = _reconcile(c, ver, r.title, r.id)
                keep[(r.id, letter)] = c
                per.setdefault(letter, []).append((c[5], PREV.search(c[7])))
        if keep or path.is_file():
            _write_req(ctx, spec, path, keep)
        st = []
        for letter in ctx.role_letters():
            if letter in per:
                v = [x[0] for x in per[letter]]
                s = "❌" if "❌" in v else ("✅" if all(x == "✅" for x in v) else "⏳")
                was = next((p.group(1) for d, p in per[letter] if p and d == "⏳"), "")
                st.append((letter, s, "", was))
        if st:
            status[spec] = st
    return status


def sync_locked(ctx) -> list:
    with lock(ctx, registry_path(ctx)):
        sync(ctx)
    return []


def write_summary(ctx, status: dict):
    p = ctx.root / ctx.get("approvals.summary")
    letters = ctx.role_letters()
    used = [x for x in letters if any(x == s[0] for v in status.values() for s in v)]
    total = sum(len(v) for v in status.values())
    ok = sum(1 for v in status.values() for s in v if s[1] == "✅")
    full = sum(1 for v in status.values() if all(s[1] == "✅" for s in v))

    def cell(s):
        return f"⏳ было {s[3]}" if s[3] else s[1] + (" 👤" if s[2] == "👤" else "")

    def table(pg):
        out = ["| Документ | " + " | ".join(used) + " | Готов |", "|---|" + "---|" * len(used) + "---|"]
        for page in pg:
            st = {s[0]: s for s in status[page]}
            out.append(f"| [{page}]({_rel_link(ctx, p, ctx.root / page)}) | " + " | ".join(cell(st[x]) if x in st else "" for x in used)
                       + f" | {'✅' if all(s[1] == '✅' for s in st.values()) else ''} |")
        return out
    checked = [x for x in sorted(status) if any(s[1] != "⏳" or s[3] for s in status[x])]
    waiting = [x for x in sorted(status) if x not in checked]
    L = ["# Сводка подтверждений", "",
         f"← [реестр]({_rel_link(ctx, p, registry_path(ctx))}) · [методология]({_rel_link(ctx, p, ctx.rules_dir / ctx.get('roles_table.file', 'approvals.md'))})", "",
         "✅ — подтвердил, ❌ — отказ, ⏳ — ждёт; «⏳ было ❌» — документ исправлен после решения и ждёт повторной проверки; "
         "пусто — роль этот тип не подтверждает. Роли: " + (", ".join(f"**{x}** — {ctx.role_name(x)}" for x in used) or "—") + ".", "",
         f"Подтверждено {ok} из {total} пар «документ × роль»; документов подтверждено полностью: {full} из {len(status)}.", "",
         f"## Проверенные документы ({len(checked)})", "", *table(checked), "",
         f"## Ещё не проверялись ({len(waiting)})", "", *table(waiting), ""]
    text = "\n".join(L)
    if not p.is_file() or U.read(p) != text:
        U.write(p, text)


# ---------- решение и очередь ----------

def set_decision(ctx, page: str, letter: str, ok: bool, comment: str, human: bool = False, who: str = "") -> str:
    """Решение роли по странице или требованию (`approve`)."""
    done, errs = set_decisions(ctx, [{"page": page, "role": letter, "ok": ok, "comment": comment}], human=human, who=who)
    if errs:
        raise SystemExit(errs[0])
    return done[0]


# проверенный факт: ID (R-…, US-…, D20, I3), `код`, номер 2.5.3, раздел «…», метод или код ответа HTTP, #якорь
FACT_RX = (r"[A-Z]{1,4}-?[A-Z]*-?\d|`|\b[A-Z]\d+\b|\d+\.\d+|«[^»]{3,}»|\b(?:GET|POST|PUT|PATCH|DELETE)\b|\b[2-5]\d\d\b|#[a-z][\w-]+")


def batch_guard(ctx, items: list) -> list:
    """Защита пачки от шаблонных решений (rules/approvals.md#economy): пусто — можно применять.

    Отклоняется весь JSON, если один комментарий у большой доли пар (`[review] template_share`, 0.10, от `template_min_pairs`, 5 пар)
    или у решения нет проверенного факта: комментарий короче `min_comment` (40) символов либо без ID / поля / номера (`fact_pattern`)."""
    ds = [d for d in items if isinstance(d, dict)]
    if not ds:
        return []
    share = float(ctx.get("review.template_share", 0.10))
    minpairs = int(ctx.get("review.template_min_pairs", 5))
    minlen = int(ctx.get("review.min_comment", 40))
    fact = re.compile(ctx.get("review.fact_pattern", FACT_RX))
    out = []
    top = collections.Counter(str(d.get("comment", "")) for d in ds).most_common(1)
    if len(ds) >= minpairs and top[0][1] > max(2, int(len(ds) * share)):
        out.append(f"шаблонные решения: комментарий «{top[0][0][:60]}» у {top[0][1]} из {len(ds)} пар — у каждой пары свой "
                   f"проверенный факт")
    short = [f"{d.get('page')} × {d.get('role')}" for d in ds
             if len(str(d.get("comment", ""))) < minlen or not fact.search(str(d.get("comment", "")))]
    if short:
        out.append(f"{len(short)} решений без проверенного факта (нужно ≥ {minlen} символов и ID / поле / номер из карточки), "
                   f"например: {', '.join(short[:5])}")
    return out


def set_decisions(ctx, items: list, human: bool = False, who: str = "") -> tuple:
    """Пачка решений [{page, role, ok, comment}] одним захватом блокировки реестра (`approve-batch`).

    Всё или ничего: есть неизвестная пара (страница × роль, требование × роль) или роль — реестр не меняется,
    возвращаются ([], ошибки). Иначе — ([строка на решение], [])."""
    letters = ctx.role_letters()
    with lock(ctx, registry_path(ctx)):
        sync(ctx)
        reg = read_registry(ctx)
        req_rows = {}  # путь файла подтверждений спеки → (спека, строки)
        plan, errs = [], []
        for n, d in enumerate(items):
            if not isinstance(d, dict):
                errs.append(f"[{n}]: нужен объект {{page, role, ok, comment}}")
                continue
            page, letter = str(d.get("page", "")).strip(), ctx.role_letter(str(d.get("role", "")).strip())
            if letter not in letters:
                errs.append(f"[{n}] {page}: роль «{letter}» не описана; роли: {', '.join(letters)}")
                continue
            if not isinstance(d.get("ok"), bool):
                errs.append(f"[{n}] {page} × {letter}: ok — true или false")
                continue
            is_req = ctx.get("requirements.approvals.enabled") and re.fullmatch(ctx.get("requirements.id"), page)
            if is_req:
                r = ctx.requirements().get(page)
                if not r:
                    errs.append(f"[{n}] требования {page} нет в спеках ({', '.join(ctx.get('requirements.specs'))})")
                    continue
                path = req_file(ctx, r.file)
                if str(path) not in req_rows:
                    req_rows[str(path)] = (r.file, path, _read_req(path))
                rows = req_rows[str(path)][2]
                if (page, letter) not in rows:
                    errs.append(f"[{n}] требование {page} роль {letter} не подтверждает (роли требования: {req_roles(ctx, r.title) or '—'})")
                    continue
                plan.append((rows, (page, letter), d, letter, page))
            else:
                rel = ctx.rel(page) if Path(page).exists() else page.replace("\\", "/").strip("`")
                rel = rel or page
                if (rel, letter) not in reg:
                    t = ctx.type_of(rel)
                    why = f"тип «{t.name}» подтверждают: {t.approve or 'никто'}" if t else "тип документа не определён (route)"
                    errs.append(f"[{n}] нет строки «{rel} × {letter}» в реестре: {why}")
                    continue
                plan.append((reg, (rel, letter), d, letter, rel))
        if errs:
            return [], [re.sub(r"^\[0\] ", "", e) for e in errs] if len(items) == 1 else errs
        done = []
        for rows, key, d, letter, name in plan:
            c = rows[key]
            c[2] = (who or (f"агент, роль {ctx.role_name(letter)}" if not human else ctx.role_name(letter))).replace("|", "/")
            c[3] = "👤" if human else "🤖"
            c[4] = datetime.date.today().isoformat()
            c[5] = "✅" if d["ok"] else "❌"
            c[7] = " ".join(str(d.get("comment") or "—").replace("|", "/").split())
            done.append(f"{name} × {letter} ({ctx.role_name(letter)}): {c[5]} {c[3]}")
        if any(x[0] is reg for x in plan):
            _write_registry(ctx, reg)
        for spec, path, rows in req_rows.values():
            if any(x[0] is rows for x in plan):
                _write_req(ctx, spec, path, rows)
        ctx.invalidate()
        sync(ctx)  # сводка — с новыми решениями
    return done, []


def queue(ctx, letter: str = "") -> list:
    letter = ctx.role_letter(letter) if letter else ""
    with lock(ctx, registry_path(ctx)):
        status = sync(ctx)
    out = []
    for page, st in sorted(status.items()):
        for l, d, _, was in st:
            if d == "⏳" and (not letter or l == letter):
                out.append((page, l, was))
    return out
