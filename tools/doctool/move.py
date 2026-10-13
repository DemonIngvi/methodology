"""move: перенос файла или папки (`git mv`, без git — переименование) и перепись всех ссылок и упоминаний пути.

Переписываются: относительные ссылки Markdown `[..](путь#якорь)` и `[id]: путь` во всех .md (кроме блоков кода),
в том числе исходящие ссылки самого перенесённого файла; упоминания пути от корня в `кавычках`, "двойных" и 'одинарных'
во всех .md и файлах из `[move] extra` (скрипты, конфиги, файлы данных).
"""
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote

from . import util as U


def _git_mv(ctx, o: Path, n: Path) -> bool:
    try:
        r = subprocess.run(["git", "mv", str(o), str(n)], cwd=ctx.root, capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


def move(ctx, old: str, new: str) -> list:
    o = (ctx.root / old).resolve() if not Path(old).is_absolute() else Path(old).resolve()
    n = (ctx.root / new).resolve() if not Path(new).is_absolute() else Path(new).resolve()
    if not o.exists():
        raise SystemExit(f"{old}: нет такого файла или папки")
    if n.exists():
        raise SystemExit(f"{new}: уже существует")
    ro, rn = ctx.rel(o), ctx.rel(n)
    if not ro or not rn:
        raise SystemExit("оба пути должны быть внутри проекта")
    # карта старый → новый для всех файлов (папка переносится целиком)
    if o.is_dir():
        mapping = {U.norm(p): n / p.relative_to(o) for p in o.rglob("*") if p.is_file()}
    else:
        mapping = {U.norm(o): n}
    mapping[U.norm(o)] = n
    n.parent.mkdir(parents=True, exist_ok=True)
    if not _git_mv(ctx, o, n):
        o.rename(n)
    ctx.invalidate()
    inv = {U.norm(v): k for k, v in mapping.items()}
    changed = []
    extra = ctx.get("move.extra", []) or []
    for f in ctx.files():
        p = ctx.root / f
        is_md = f.lower().endswith(".md")
        if not is_md and not U.match(f, extra):
            continue
        try:
            t0 = U.read(p)
        except OSError:
            continue
        old_self = Path(inv.get(U.norm(p), str(p)))  # где файл лежал до переноса
        t = _rewrite_links(t0, old_self.parent, p.parent, mapping) if is_md else t0
        t = _rewrite_mentions(t, ro, rn, o.is_dir())
        if t != t0:
            U.write(p, t)
            changed.append(f)
    return changed


def _rewrite_links(text: str, old_dir: Path, new_dir: Path, mapping: dict) -> str:
    def fix(url: str) -> str:
        if not url or url.startswith(("<", "#")) or U.EXTERNAL.match(url):
            return url
        path_part, sep, anchor = url.partition("#")
        raw = unquote(path_part)
        if raw.startswith("/"):
            return url  # от корня сайта — не трогаем, упоминание пути перепишет _rewrite_mentions
        target_old = Path(os.path.normpath(old_dir / raw))
        target_new = mapping.get(U.norm(target_old))
        if target_new is None:
            if old_dir == new_dir or not target_old.exists():
                return url
            target_new = target_old
        r = Path(os.path.relpath(target_new, new_dir)).as_posix()
        if raw.endswith("/") and not r.endswith("/"):
            r += "/"
        if "%" in path_part:
            r = quote(r)
        return r + (sep + anchor if sep else "")

    out, inside = [], False
    for line in text.split("\n"):
        if U.FENCE.match(line):
            inside = not inside
            out.append(line)
            continue
        if not inside:
            # инлайн-код не трогаем: режем строку на куски вне `...`
            parts = re.split(r"(`[^`]*`)", line)
            for i in range(0, len(parts), 2):
                parts[i] = U.LINK.sub(lambda m: m.group(1) + fix(m.group(2)) + m.group(3), parts[i])
                parts[i] = U.REF_DEF.sub(lambda m: m.group(1) + fix(m.group(2)), parts[i])
            line = "".join(parts)
        out.append(line)
    return "\n".join(out)


def _rewrite_mentions(text: str, ro: str, rn: str, is_dir: bool) -> str:
    for q in ("`", '"', "'"):
        text = text.replace(f"{q}{ro}{q}", f"{q}{rn}{q}")
        if is_dir:
            text = re.sub(re.escape(q + ro + "/") + r"([^" + re.escape(q) + r"\n]*)" + re.escape(q),
                          lambda m: f"{q}{rn}/{m.group(1)}{q}", text)
    return text
