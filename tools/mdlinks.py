#!/usr/bin/env python3
"""Проверка Markdown-документации: битые относительные ссылки, битые якоря и «сироты».

    python tools/mdlinks.py [корень] [--entry ПУТЬ ...] [--no-orphans] [--no-anchors]

Независим от проекта, без зависимостей. Код возврата 1 — есть замечания.
Контракт — tools/README.md#mdlinks.
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "site", "build", "dist", "__pycache__", ".doctool"}
DEFAULT_ENTRIES = {"README.md", "AGENTS.md", "CLAUDE.md"}

FENCE = re.compile(r"^(```|~~~)")
LINK = re.compile(r"!?\[(?:[^\]\[]|\[[^\]]*\])*\]\(\s*(<[^>]*>[^)\s]*|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EXPLICIT_ID = re.compile(r"""(?:\bid|\bname)\s*=\s*["']([^"']+)["']|\{#([^}\s]+)\}""")


def md_files(root: Path):
    for p in sorted(root.rglob("*.md")):
        if not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            yield p


def text_lines(path: Path):
    """Строки файла вне блоков кода."""
    inside = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if FENCE.match(line.lstrip()):
            inside = not inside
            continue
        if not inside:
            yield line


def slug(heading: str) -> str:
    """Якорь заголовка в стиле GitHub."""
    s = re.sub(r"\{#[^}]*\}", "", heading)
    s = re.sub(r"<[^>]+>", "", s)                       # html-теги
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)    # ссылки → текст
    s = s.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)                       # пунктуация и эмодзи
    return s.replace(" ", "-")


def anchors(path: Path, cache: dict) -> set:
    if path in cache:
        return cache[path]
    found, seen = set(), {}
    for line in text_lines(path):
        for a, b in EXPLICIT_ID.findall(line):
            found.add(a or b)
        m = HEADING.match(line)
        if m:
            base = slug(m.group(2))
            n = seen.get(base, 0)
            found.add(base if n == 0 else f"{base}-{n}")
            seen[base] = n + 1
    cache[path] = found
    return found


def targets(path: Path):
    """(номер строки, ссылка) для всех ссылок файла вне блоков кода."""
    for i, line in enumerate(text_lines(path), 1):
        line = re.sub(r"`[^`]*`", "", line)  # инлайн-код
        for m in LINK.finditer(line):
            yield i, m.group(1)
        m = REF_DEF.match(line)
        if m:
            yield i, m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--entry", action="append", default=[], help="точка входа (не считается «сиротой»)")
    ap.add_argument("--no-orphans", action="store_true")
    ap.add_argument("--no-anchors", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(a.root).resolve()
    files = list(md_files(root))
    cache, issues, incoming = {}, [], {f: 0 for f in files}

    for f in files:
        for ln, url in targets(f):
            if url.startswith("<"):
                continue  # заглушка шаблона: (<путь>)
            if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) or url.startswith("//"):
                continue  # внешняя ссылка
            path_part, _, anchor = url.partition("#")
            path_part, anchor = unquote(path_part), unquote(anchor)
            if not path_part:
                target = f
            elif path_part.startswith("/"):
                target = (root / path_part.lstrip("/")).resolve()
            else:
                target = (f.parent / path_part).resolve()
            where = f"{f.relative_to(root).as_posix()}:{ln}"
            if not target.exists():
                issues.append(f"{where}: нет файла «{url}»")
                continue
            if target.is_dir() and (target / "README.md").exists():
                target = target / "README.md"
            if target in incoming and target != f:
                incoming[target] += 1
            if anchor and not a.no_anchors and target.suffix == ".md" and target.is_file():
                if anchor not in anchors(target, cache) and anchor.lower() not in anchors(target, cache):
                    issues.append(f"{where}: нет якоря «#{anchor}» в {target.relative_to(root).as_posix()}")

    if not a.no_orphans:
        entries = {(root / e).resolve() for e in DEFAULT_ENTRIES | set(a.entry)}
        for f, n in incoming.items():
            if n == 0 and f not in entries and f.name != "README.md":
                issues.append(f"{f.relative_to(root).as_posix()}: «сирота» — на файл не ведёт ни одна ссылка")

    for i in issues:
        print(f"⚠️ {i}")
    print(f"файлов: {len(files)}, замечаний: {len(issues)}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
