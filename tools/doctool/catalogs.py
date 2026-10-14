"""Каталоги значений для полей-ссылок данных YAML и типы параметров из таблиц спек.

Каталог — множество допустимых значений поля (`ref = "<имя>"` в схеме вида). Откуда он строится, объявляется в конфиге:

    [[data.catalogs]]
    name = "participant"                          # имя для ref
    about = "компонент или блок"                  # пояснение в ошибке и в JSON Schema
    kinds = ["component", "block"]                # ID единиц этих видов [[data.kinds]]
    fields = [{ kind = "component", field = "name" }]   # значения поля единиц вида
    keys = [{ kind = "participants", field = "aliases" }]   # ключи словаря-поля (каталог псевдонимов участников)
    files = [{ glob = "specs/*/interfaces.md", pattern = '^\\| (S\\d+) \\|' }]  # регулярка по файлам (группа 1 или всё совпадение)
    values = ["a", "b"]                           # перечисление в конфиге

Вид единиц и `[ids.*]` тоже каталоги; встроенный каталог `param` — параметры из таблиц спек (`[data.params]`), по компоненту единицы.
Здесь же — формальный тип параметра (число с границами, длительность, перечисление) по колонкам «по умолчанию / мин / макс»
и подсказка ближайшего значения (difflib). Правило: rules/data.md#yaml-units.
"""
import difflib
import re
from pathlib import Path

from . import util as U

NUM = r"(\d[\d   ]*(?:[.,]\d+)?)"
DEF_UNITS = ["$", "%", "КБ", "МБ", "ГБ", "KB", "MB", "GB"]
DEF_DUR = {"с": 1, "сек": 1, "s": 1, "sec": 1, "мин": 60, "min": 60, "ч": 3600, "h": 3600, "д": 86400, "дн": 86400, "дня": 86400,
           "дней": 86400, "день": 86400, "d": 86400}
ONOFF = [{"вкл", "выкл"}, {"true", "false"}, {"да", "нет"}, {"on", "off"}, {"включён", "выключен"}]


def suggest(v, cat) -> str:
    """« — может быть: a, b» по ближайшим значениям каталога (пусто, если близких нет)."""
    near = difflib.get_close_matches(str(v), [str(x) for x in cat], n=3, cutoff=0.5)
    return f" — может быть: {', '.join(near)}" if near else ""


# ---------- каталоги из конфига ----------

def declared(ctx) -> dict:
    return {c["name"]: c for c in (ctx.get("data.catalogs", []) or []) if c.get("name")}


def _build(ctx, c: dict) -> set:
    from . import units as UN
    out = {str(x) for x in U.as_list(c.get("values"))}
    for k in U.as_list(c.get("kinds")):
        out |= set(UN.units(ctx, k))
    for f in U.as_list(c.get("fields")):
        for _, (_, v) in UN.units(ctx, f["kind"]).items():
            x = v.get(f["field"]) if isinstance(v, dict) else None
            out |= {str(i) for i in (x if isinstance(x, list) else [x]) if i not in (None, "")}
    for f in U.as_list(c.get("keys")):  # ключи словаря-поля: каталог псевдонимов {подпись: участник}
        for _, (_, v) in UN.units(ctx, f["kind"]).items():
            x = v.get(f["field"]) if isinstance(v, dict) else None
            out |= {str(i) for i in (x if isinstance(x, dict) else {})}
    for f in U.as_list(c.get("files")):
        rx = re.compile(f["pattern"], re.M)
        for rel in ctx.glob(f.get("glob", [])):
            out |= {(m.group(1) if m.groups() else m.group(0)) for m in rx.finditer(ctx.text(rel))}
    return out


def entry(ctx, name: str):
    """(regex ссылки, множество ID) каталога из `[[data.catalogs]]` или встроенного `param`; иначе None."""
    if name == "param" and ctx.get("data.params"):
        ids = {n for rows in all_params(ctx).values() for n in rows}
        return re.compile(r"(?<![\w.-])(" + "|".join(re.escape(x) for x in sorted(ids, key=len, reverse=True)) + r")(?![\w.-])"
                          if ids else r"(?!x)x"), ids
    c = declared(ctx).get(name)
    if not c:
        return None

    def build():
        ids = _build(ctx, c)
        if c.get("pattern"):
            return re.compile(c["pattern"]), ids
        alt = "|".join(re.escape(x) for x in sorted(ids, key=len, reverse=True)) or r"(?!x)x"
        return re.compile(r"(?<![\w/-])(" + alt + r")(?![\w/-])"), ids
    return ctx.cached(("catalog", name), build)


def about(ctx, name: str) -> str:
    c = declared(ctx).get(name)
    if c:
        return c.get("about") or name
    return "параметр из таблиц спек" if name == "param" else name


# ---------- параметры из таблиц спек ----------

def _cols(ctx) -> dict:
    d = ctx.get("data.params", {}) or {}
    return {"name": d.get("name_col", "Параметр"), "default": d.get("default_col", "По умолчанию"),
            "min": d.get("min_col", "Мин"), "max": d.get("max_col", "Макс"), "what": d.get("what_col", "Что задаёт"), "type": d.get("type_col", "Тип")}


def _clean(s: str) -> str:
    return re.sub(r"\s*\([^)]*\)$", "", str(s).strip()).strip()


def _fnum(s: str) -> float:
    return float(re.sub(r"[\s  ]", "", s).replace(",", "."))


def parse_num(ctx, s):
    """(число, единица) или None: «200 000», «5 $», «0,1 $», «80%»."""
    units = (ctx.get("data.params.units") if ctx else None) or DEF_UNITS
    m = re.fullmatch(NUM + r"\s*(" + "|".join(re.escape(u) for u in units) + r")?", _clean(s))
    return (_fnum(m.group(1)), m.group(2) or "") if m else None


def _durs(ctx) -> dict:
    return (ctx.get("data.params.durations") if ctx else None) or DEF_DUR


def parse_dur(ctx, s):
    """Секунды или None: «24 ч», «15 мин», «7 дней»."""
    d = _durs(ctx)
    m = re.fullmatch(NUM + r"\s*(" + "|".join(re.escape(u) for u in sorted(d, key=len, reverse=True)) + r")", _clean(s))
    return _fnum(m.group(1)) * d[m.group(2)] if m else None


def param_type(ctx, r: dict):
    """Формальный тип параметра по колонкам default / min / max или None (описан словами)."""
    if r.get("decl"):  # явная колонка «Тип» важнее вывода из default / min / max
        t = parse_decl(r["decl"])
        if t:
            return t
    d, mn, mx = (_clean(r.get(k, "")) for k in ("default", "min", "max"))
    nd, nmn, nmx = parse_num(ctx, d), parse_num(ctx, mn), parse_num(ctx, mx)
    if nd and nmn and nd[1] == nmn[1]:
        mxv = nmx[0] if nmx and nmx[1] == nd[1] else None
        if mxv is not None and mxv < nmn[0]:  # «0 (особое значение)» в «Макс» — не граница
            return None
        return {"kind": "number", "unit": nd[1], "min": nmn[0], "max": mxv}
    dd, dmn, dmx = parse_dur(ctx, d), parse_dur(ctx, mn), parse_dur(ctx, mx)
    if dd is not None and dmn is not None:
        return {"kind": "duration", "min": dmn, "max": dmx}
    for s in ONOFF:
        if {d, mn, mx} <= s:
            return {"kind": "enum", "values": sorted(s)}
    if all(re.fullmatch(r"[a-z][a-z0-9_\-]*", x) for x in (d, mn, mx)):
        return {"kind": "enum", "values": sorted({d, mn, mx})}
    g = [re.fullmatch(r"([A-Z])(\d)", x) for x in (d, mn, mx)]
    if all(g) and len({x.group(1) for x in g}) == 1:
        return {"kind": "enum", "values": [f"{g[0].group(1)}{i}" for i in range(int(g[1].group(2)), int(g[2].group(2)) + 1)]}
    return None


def parse_decl(s):
    """Явный «Тип» таблицы параметров → dict типа или None. Грамматика: число [ед.] · длительность · одно из: a, b ·
    список <каталог> · ссылка <что> · строка [<мин>–<макс> симв.] · доля окна % · части через « / » (составной)."""
    s = str(s).strip().strip("`")
    parts = [p.strip() for p in s.split(" / ")]
    if len(parts) > 1:
        ps = [parse_decl(p) for p in parts]
        return {"kind": "composite", "parts": ps} if all(ps) else None
    m = re.fullmatch(r"число(?:\s+(\S+))?", s)
    if m:
        return {"kind": "number", "unit": m.group(1) or "", "min": 0.0, "max": None}
    if s == "длительность":
        return {"kind": "duration", "min": 0.0, "max": None}
    if s.startswith("одно из:"):
        v = [x.strip() for x in s[8:].split(",") if x.strip()]
        return {"kind": "enum", "values": v} if v else None
    m = re.fullmatch(r"(список|ссылка)\s+(.+)", s)
    if m:
        return {"kind": "list" if m.group(1) == "список" else "ref", "what": m.group(2)}
    m = re.fullmatch(r"строка(?:\s+(\d[\d  ]*)[–-](\d[\d  ]*)\s+симв\.)?", s)
    if m:
        f = lambda x: int(re.sub(r"[  ]", "", x)) if x else None
        return {"kind": "string", "min": f(m.group(1)), "max": f(m.group(2))}
    if s == "доля окна %":
        return {"kind": "percent"}
    return None


def type_text(t) -> str:
    k = t["kind"]
    if k == "composite":
        return " / ".join(type_text(p) for p in t["parts"])
    if k in ("list", "ref"):
        return ("список: " if k == "list" else "ссылка: ") + t["what"]
    if k == "string":
        return "строка" + (f" {t['min']}–{t['max']} симв." if t["min"] is not None else "")
    if k == "percent":
        return "доля окна, %"
    if t["kind"] == "number":
        u = f" {t['unit']}" if t["unit"] else ""
        return f"число{u} от {t['min']:g}" + (f" до {t['max']:g}" if t["max"] is not None else "")
    if t["kind"] == "duration":
        return "длительность (с, мин, ч, д)" + f" от {t['min']:g} с" + (f" до {t['max']:g} с" if t["max"] is not None else "")
    return "одно из: " + ", ".join(t["values"])


def value_ok(ctx, t, v: str) -> bool:
    """Значение параметра соответствует формальному типу."""
    k = t["kind"]
    if k == "composite":
        vs = [x.strip() for x in v.split(" / ")]
        return len(vs) == len(t["parts"]) and all(value_ok(ctx, p, x) for p, x in zip(t["parts"], vs))
    if k in ("list", "ref"):
        return bool(v.strip())
    if k == "string":
        return (t["min"] is None or len(v) >= t["min"]) and (t["max"] is None or len(v) <= t["max"])
    if k == "percent":
        return bool(re.fullmatch(r"\d+\s*%( окна)?", v.strip()))
    if t["kind"] == "number":
        n = parse_num(ctx, v)
        return bool(n) and n[1] == t["unit"] and n[0] >= t["min"] and (t["max"] is None or n[0] <= t["max"])
    if t["kind"] == "duration":
        n = parse_dur(ctx, v)
        return n is not None and n >= t["min"] and (t["max"] is None or n <= t["max"])
    return v in t["values"]


def all_params(ctx) -> dict:
    """{файл таблицы: {имя: {file, default, min, max, what, type}}} — таблицы, где первая колонка заголовка — `name_col`."""
    def build():
        cols = _cols(ctx)
        out = {}
        for rel in ctx.glob((ctx.get("data.params", {}) or {}).get("glob", [])):
            d = {}
            for r in U.table_rows(ctx.text(rel), "| " + cols["name"]):
                name = U.plain(U.col(r, cols["name"])).strip("`")
                if name and name not in d:
                    row = {"file": rel, "default": U.plain(U.col(r, cols["default"])), "min": U.plain(U.col(r, cols["min"])),
                           "max": U.plain(U.col(r, cols["max"])), "what": U.plain(U.col(r, cols["what"])),
                           "decl": U.plain(U.col(r, cols["type"]))}
                    row["type"] = param_type(ctx, row)
                    d[name] = row
            out[rel] = d
        return out
    return ctx.cached("params", build)


def params(ctx, comp: str) -> dict:
    """Параметры компонента: таблицы файлов, чей компонент (`ctx.component_of`) совпал; без компонента — все."""
    out = {}
    for rel, rows in all_params(ctx).items():
        if not comp or ctx.component_of(rel) == comp:
            for n, r in rows.items():
                out.setdefault(n, r)
    return out


def check_param(ctx, v: str, comp: str):
    """None или сообщение: параметра нет среди таблиц спек компонента."""
    if not comp:
        return None
    cv = params(ctx, comp)
    d = (ctx.get("data.params", {}) or {}).get("glob", "")
    return None if v in cv else f"«{v}» нет среди параметров спек компонента {comp} ({U.as_list(d)[0] if d else 'data.params'}){suggest(v, cv)}"


def check_param_value(ctx, name: str, v, comp: str):
    """None или сообщение: значение не по типу параметра."""
    spec = params(ctx, comp).get(name)
    if not spec or not spec.get("type") or str(v) == spec["default"]:
        return None
    if value_ok(ctx, spec["type"], str(v)):
        return None
    return (f"«{v}» не по типу параметра {name} ({spec['file']}): {type_text(spec['type'])} (по умолчанию {spec['default']}); "
            f"пояснение вместо значения — в другом поле")
