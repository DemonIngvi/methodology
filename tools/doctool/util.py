"""Общие функции: чтение и запись (UTF-8, LF), glob по относительным путям, таблицы и разделы Markdown, якоря, хеш."""
import hashlib
import os
import re
import unicodedata
from pathlib import Path

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EXPLICIT_ID = re.compile(r"""(?:\bid|\bname)\s*=\s*["']([^"']+)["']|\{\s*#([^}\s]+)[^}]*\}""")
LINK = re.compile(r"(!?\[(?:[^\]\[]|\[[^\]]*\])*\]\(\s*)(<[^>]*>[^)\s]*|[^)\s]+)((?:\s+\"[^\"]*\")?\s*\))")
REF_DEF = re.compile(r"^(\s*\[[^\]]+\]:\s*)(\S+)")
EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:|^//", re.I)


def read(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Path(path).read_text(encoding="utf-8", errors="replace")


def write(path, text: str):
    """Запись UTF-8 с переводами строк LF на любой ОС."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def as_list(x) -> list:
    if x is None or x == "":
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


_GLOB = {}


def glob_re(pat: str):
    """Шаблон пути от корня → regex: `*` — внутри папки, `**/` — любое число папок, `?` — один символ."""
    if pat in _GLOB:
        return _GLOB[pat]
    p = pat.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    out, i = "", 0
    while i < len(p):
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    rx = re.compile(out + "/?")
    _GLOB[pat] = rx
    return rx


def match(rel: str, patterns) -> bool:
    return any(glob_re(p).fullmatch(rel) for p in as_list(patterns))


def specificity(pat: str) -> tuple:
    """Меньше — точнее: точный путь важнее шаблона, длинный шаблон важнее короткого."""
    return (pat.count("*") + pat.count("?"), -len(pat))


def plain(s: str) -> str:
    """Текст ячейки без ссылок и жирного."""
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s or "")
    return s.replace("**", "").replace("`", "").strip()


def split_row(line: str) -> list:
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def tables(text: str, header_prefix: str = "") -> list:
    """Таблицы Markdown, чей заголовок начинается с header_prefix: [[{колонка: значение}, ...], ...]."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        l = lines[i].strip()
        if l.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]) \
                and (not header_prefix or l.replace(" ", "").startswith(header_prefix.replace(" ", ""))):
            cols = split_row(l)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = split_row(lines[j])
                rows.append(dict(zip(cols, cells + [""] * (len(cols) - len(cells)))))
                j += 1
            out.append(rows)
            i = j
            continue
        i += 1
    return out


def table_rows(text: str, header_prefix: str) -> list:
    return [r for t in tables(text, header_prefix) for r in t]


def col(row: dict, prefix: str) -> str:
    """Значение колонки, чьё имя начинается с prefix (без учёта регистра): «Где (пример)» находится по «Где»."""
    for k, v in row.items():
        if plain(k).lower().startswith(prefix.lower()):
            return v
    return ""


def section(text: str, title: str) -> str:
    """Тело раздела, чей заголовок (любой уровень) начинается с title — до заголовка того же или более высокого уровня."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        m = HEADING.match(l)
        if m and plain(re.sub(r"\{#[^}]*\}", "", m.group(2))).lower().startswith(title.lower()):
            lvl, body = len(m.group(1)), []
            inside = False
            for x in lines[i + 1:]:
                if FENCE.match(x):
                    inside = not inside
                mm = HEADING.match(x)
                if mm and not inside and len(mm.group(1)) <= lvl:
                    break
                body.append(x)
            return "\n".join(body).strip()
    return ""


def md_lines(text: str):
    """(номер строки, строка) вне блоков кода."""
    inside = False
    for i, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            yield i, line


def slug_github(h: str) -> str:
    s = re.sub(r"\{#[^}]*\}", "", h)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def slug_mkdocs(h: str, unicode: bool = True) -> str:
    s = re.sub(r"\{#[^}]*\}", "", h)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s).replace("`", "")
    if not unicode:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)


def anchors_of(text: str, styles=("github", "mkdocs")) -> set:
    found, seen = set(), {}
    for _, line in md_lines(text):
        for a, b in EXPLICIT_ID.findall(line):
            found.add(a or b)
        m = HEADING.match(line)
        if not m:
            continue
        cands = set()
        if "github" in styles:
            cands.add(("g", slug_github(m.group(2))))
        if "mkdocs" in styles:
            cands.add(("m", slug_mkdocs(m.group(2))))
            cands.add(("m", slug_mkdocs(m.group(2), unicode=False)))
        for kind, base in cands:
            n = seen.get((kind, base), 0)
            found.add(base if n == 0 else (f"{base}-{n}" if kind == "g" else f"{base}_{n}"))
            seen[(kind, base)] = n + 1
    return found


def links(text: str):
    """(номер строки, ссылка) вне блоков и инлайн-кода."""
    for i, line in md_lines(text):
        line = re.sub(r"`[^`]*`", "", line)
        for m in LINK.finditer(line):
            yield i, m.group(2)
        m = REF_DEF.match(line)
        if m:
            yield i, m.group(2)


def short_hash(text: str, ignore=()) -> str:
    """Версия текста: хеш без пробельных различий и без частей, совпадающих с ignore (regex)."""
    t = text
    for rx in ignore:
        t = re.sub(rx, "", t, flags=re.M)  # (?s) в шаблоне — если нужно через строки
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]


def first_number(s: str):
    m = re.search(r"\d+(?:[.,]\d+)?", plain(s or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def norm(p) -> str:
    """Ключ пути для сравнения (регистр на Windows, .. и .)."""
    return os.path.normcase(os.path.normpath(str(p)))


def is_empty(value: str) -> bool:
    v = plain(value or "")
    v = re.sub(r"<[^>]*>", "", v).strip(" —-–.…\t")
    return not v
