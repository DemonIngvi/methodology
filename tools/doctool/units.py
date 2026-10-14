"""Данные в YAML по единице (`[data]` конфига): схема вида, проверка `yaml-check`, формат `yaml-fmt`, карточка `unit`.

Единица — один небольшой файл (блок, модель, история…), который читается и правится целиком. Вид единицы задаётся в
конфиге `[[data.kinds]]`: шаблон пути, поля с типами, обязательностью, допустимыми значениями и ссылками на каталоги ID.
Порядок единиц папки (если важен) — `_order.yaml`: список имён файлов без расширения.

YAML читается PyYAML (`pip install pyyaml`). Без него `yaml-check`, `yaml-fmt`, `unit` завершаются ошибкой «нужен PyYAML»,
а `check` пропускает проверку данных с предупреждением (`[data] require_yaml = true` — считать это замечанием).
Методология — rules/data.md#yaml, порядок правки — rules/change-process.md#yaml-path.
"""
import datetime
import difflib
import json
import re
import sys
from pathlib import Path

from . import util as U

TYPES = {"str": "строка", "int": "целое число", "float": "число", "bool": "true / false", "list": "список",
         "dict": "словарь", "date": "дата ГГГГ-ММ-ДД", "any": "любое значение"}


class NoYaml(SystemExit):
    pass


def yaml_mod():
    """Модуль PyYAML или None."""
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError:
        return None


def need_yaml():
    y = yaml_mod()
    if y is None:
        raise NoYaml("для данных YAML нужен PyYAML: pip install pyyaml (Python " + sys.version.split()[0] + "); "
                     "без него check пропускает проверку data/, а yaml-check, yaml-fmt, unit недоступны")
    return y


def load(path: Path):
    y = need_yaml()
    return y.safe_load(Path(path).read_text(encoding="utf-8"))


def _flat_scalar(x) -> bool:
    return isinstance(x, (str, int, float, bool)) or x is None


class _Row(dict):
    """Строка таблицы (словарь в списке) — в YAML в одну строку."""


def _mark_rows(v, in_list=False):
    if isinstance(v, dict):
        out = {k: _mark_rows(x) for k, x in v.items()}
        if in_list and out and all(_flat_scalar(x) or (isinstance(x, list) and all(_flat_scalar(i) for i in x)) for x in v.values()) \
                and len(json.dumps(v, ensure_ascii=False)) <= 100:
            return _Row(out)
        return out
    if isinstance(v, list):
        return [_mark_rows(x, True) for x in v]
    return v


def dump(value, flow_rows=True) -> str:
    """YAML: порядок ключей как в значении, Unicode без экранирования, короткие списки скаляров и строки таблиц
    (словари с именованными полями внутри списка) — в одну строку."""
    y = need_yaml()

    class D(y.SafeDumper):
        pass

    D.add_representer(_Row, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", dict(v), flow_style=True))

    def rep_list(d, v):
        flow = v is not value and bool(v) and all(isinstance(x, (str, int, float, bool)) or x is None for x in v) \
            and len(json.dumps(v, ensure_ascii=False)) <= 100
        return d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=flow)
    D.add_representer(list, rep_list)
    if flow_rows:
        value = _mark_rows(value)
    return y.dump(value, Dumper=D, sort_keys=False, allow_unicode=True, width=100000, default_flow_style=False)


# ---------- виды единиц ----------

COSTS_KIND = {
    "name": "costs", "builtin": True,
    "fields": [{"name": "costs", "type": "list", "required": True, "items": {"type": "dict", "fields": [
        {"name": "date", "type": "date", "required": True},
        {"name": "process", "type": "str", "required": True, "pattern": r"[a-z0-9-]+", "hint": "код процесса латиницей"},
        {"name": "methodology", "type": "str", "required": True, "hint": "файл правил#якорь"},
        {"name": "units", "type": "int", "required": True},
        {"name": "unit", "type": "str", "required": True},
        {"name": "tokens", "type": "int", "required": True},
        {"name": "model", "type": "str"},
        {"name": "minutes", "type": "int"},
        {"name": "expected", "type": "list", "items": {"type": "int"}},
        {"name": "agents", "type": "list", "items": {"type": "str"}},
        {"name": "calls", "type": "int"},
        {"name": "context", "type": "int"},
        {"name": "note", "type": "str"},
    ]}}],
}


def kinds(ctx) -> list:
    out = [dict(k) for k in (ctx.get("data.kinds", []) or [])]
    cf = ctx.get("costs.file", "")
    if cf and cf.lower().endswith((".yaml", ".yml")) and not any(k["name"] == "costs" for k in out):
        out.append(dict(COSTS_KIND, glob=cf))
    return out


def kind(ctx, name: str):
    return next((k for k in kinds(ctx) if k["name"] == name), None)


def order_name(ctx) -> str:
    return ctx.get("data.order_file", "_order.yaml")


def kind_of(ctx, rel: str):
    """Вид единицы по пути (или None); файл порядка — вид «order»."""
    if not rel.lower().endswith((".yaml", ".yml")):
        return None
    for k in kinds(ctx):
        if U.match(rel, k.get("glob", [])):
            if Path(rel).name == order_name(ctx):
                return {"name": "order"}
            return k
    return None


def _subst(tpl: str, rel: str) -> str:
    p = Path(rel)
    return tpl.replace("{stem}", p.stem).replace("{parent}", p.parent.name).replace("{path}", rel)


def unit_id(k: dict, rel: str, value=None) -> str:
    f = k.get("id_field")
    if f and isinstance(value, dict) and value.get(f):
        return str(value[f])
    return _subst(k.get("id", "{stem}"), rel)


def unit_files(ctx, path=None) -> list:
    """Файлы единиц (без файлов порядка) под путём или все."""
    files = [f for k in kinds(ctx) for f in ctx.glob(k.get("glob", []))]
    files = [f for f in dict.fromkeys(files) if Path(f).name != order_name(ctx)]
    if path:
        rel = ctx.rel(path) if Path(path).exists() else str(path).replace("\\", "/").rstrip("/")
        files = [f for f in files if f == rel or f.startswith(rel + "/")]
    return files


def units(ctx, name: str) -> dict:
    """{ID: (rel, значение)} единиц вида (без PyYAML — пусто)."""
    def build():
        if yaml_mod() is None:
            return {}
        k = kind(ctx, name)
        out = {}
        for f in ctx.glob(k.get("glob", []) if k else []):
            if Path(f).name == order_name(ctx):
                continue
            try:
                v = load(ctx.root / f)
            except Exception:  # ошибки разбора покажет yaml-check
                v = None
            out[unit_id(k, f, v)] = (f, v)
        return out
    return ctx.cached(("units", name), build)


def id_catalog(ctx, name: str):
    """Вид единиц как каталог ID: (regex, множество ID) — для ссылок карточек и `[ids]`."""
    k = kind(ctx, name)
    if not k or yaml_mod() is None:  # без PyYAML каталог неизвестен — ссылки на него не проверяются
        return None
    ids = set(units(ctx, name))
    if k.get("id_pattern"):
        return re.compile(k["id_pattern"]), ids
    alt = "|".join(re.escape(x) for x in sorted(ids, key=len, reverse=True)) or r"(?!x)x"
    return re.compile(r"(?<![\w/-])(" + alt + r")(?![\w/-])"), ids


# ---------- проверка значения по схеме ----------

def _tname(v) -> str:
    return {str: "строка", int: "число", float: "дробное", bool: "true/false", list: "список", dict: "словарь",
            type(None): "пусто"}.get(type(v), type(v).__name__)


def _type_ok(t: str, v) -> bool:
    if t in ("", "any"):
        return True
    if t == "str":
        return isinstance(v, str)
    if t == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "float":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "bool":
        return isinstance(v, bool)
    if t == "list":
        return isinstance(v, list)
    if t == "dict":
        return isinstance(v, dict)
    if t == "date":
        return isinstance(v, datetime.date) or (isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d\d-\d\d", v)))
    return True


_STATE = {"comp": ""}  # компонент проверяемой единицы (для каталога параметров)


def none_prefix(ctx) -> str:
    return ctx.get("data.none_prefix", "нет:")


def is_none(ctx, v) -> bool:
    """Строка «нет: <почему>» — допустимое значение спискового поля с `none_ok = true`."""
    return isinstance(v, str) and v.startswith(none_prefix(ctx))


def _catalog(ctx, ref: str):
    if kind(ctx, ref):
        return id_catalog(ctx, ref)
    from . import catalogs as CT
    return CT.entry(ctx, ref) or ctx.ids(ref)


def _ref_check(ctx, ref, v: str, also=None):
    """Сообщение об ошибке ссылки или None. Каталог — вид единиц data/, `[[data.catalogs]]`, `param`, каталог ID (`[ids]`,
    requirement, task, плагины); `ref` — список: значение из любого из них. `also` — регулярка допустимых значений вне каталога."""
    from . import catalogs as CT
    if also and re.fullmatch(also, v):
        return None
    if isinstance(ref, list):
        msgs = [_ref_check(ctx, r, v) for r in ref]
        return None if any(m is None for m in msgs) else f"«{v}» не из каталогов {', '.join(ref)}"
    if ref == "param" and ctx.get("data.params"):
        return CT.check_param(ctx, v, _STATE["comp"])
    ent = _catalog(ctx, ref)
    if not ent:
        return f"каталог ссылок «{ref}» не описан (вид [[data.kinds]], [[data.catalogs]], [ids.{ref}] или плагин)"
    rx, ids = ent
    if v in ids:
        return None
    found = [m.group(1) if m.groups() else m.group(0) for m in rx.finditer(v)]
    if found:
        bad = [x for x in found if x not in ids]
        return f"{ref} {', '.join(bad)} не существует{CT.suggest(bad[0], ids)}" if bad else None
    return f"{ref} «{v}» не существует{CT.suggest(v, ids)}"


def validate(ctx, value, fdef: dict, path: str, errs: list, refs=True):
    from . import catalogs as CT
    t = fdef.get("type", "any")
    if value is None:
        if fdef.get("required"):
            errs.append(f"{path}: пусто — нужно {TYPES.get(t, t)}")
        return
    if t == "list" and fdef.get("none_ok") and isinstance(value, str):
        pre = none_prefix(ctx)
        if not is_none(ctx, value):
            errs.append(f"{path}: нужен список или строка «{pre} <почему>», а не «{value[:30]}»")
        elif not value[len(pre):].strip():
            errs.append(f"{path}: после «{pre}» нужна причина — почему списка нет")
        return
    if not _type_ok(t, value):
        hint = " — возьми значение в кавычки" if t == "str" else ""
        if t == "list" and fdef.get("none_ok"):
            hint = f" — или строка «{none_prefix(ctx)} <почему>»"
        errs.append(f"{path}: нужно {TYPES.get(t, t)}, а не {_tname(value)}{hint}")
        return
    if t == "list":
        item = fdef.get("items") or {}
        if isinstance(item, str):
            item = {"type": item}
        item = dict(item)
        for key in ("ref", "choices", "pattern", "hint", "also"):
            if key in fdef and key not in item:
                item[key] = fdef[key]
        for i, x in enumerate(value):
            validate(ctx, x, item, f"{path}[{i}]", errs, refs)
        return
    if t == "dict" and fdef.get("fields"):
        validate_fields(ctx, value, fdef["fields"], path, errs, refs, extra=fdef.get("extra", False), one_of=fdef.get("one_of"))
        return
    if isinstance(value, str) and refs:
        if fdef.get("choices") and value not in fdef["choices"]:
            errs.append(f"{path}: «{value}» — допустимо: {', '.join(x or '«»' for x in fdef['choices'])}{CT.suggest(value, fdef['choices'])}")
        if fdef.get("pattern") and value and not re.fullmatch(fdef["pattern"], value):
            errs.append(f"{path}: «{value}» не по формату {fdef.get('hint') or fdef['pattern']}")
        if fdef.get("ref") and value:
            bad = _ref_check(ctx, fdef["ref"], value, fdef.get("also"))
            if bad:
                errs.append(f"{path}: {bad}")


def validate_fields(ctx, value: dict, fields: list, path: str, errs: list, refs=True, extra=False, one_of=None):
    from . import catalogs as CT
    names = [f["name"] for f in fields]
    for f in fields:
        p = f"{path}.{f['name']}" if path else f["name"]
        if f["name"] not in value:
            if f.get("required"):
                errs.append(f"{p}: обязательное поле отсутствует")
            continue
        validate(ctx, value[f["name"]], f, p, errs, refs)
        pv = f.get("param_value")  # значение параметра — по типу из таблицы параметров спеки
        if pv and refs and value.get(pv) and value[f["name"]] not in (None, "") and ctx.get("data.params"):
            bad = CT.check_param_value(ctx, str(value[pv]), value[f["name"]], _STATE["comp"])
            if bad:
                errs.append(f"{p}: {bad}")
    if one_of and not any(value.get(k) for k in one_of):
        errs.append(f"{path or '<корень>'}: нужно хотя бы одно из полей: {', '.join(one_of)}")
    if not extra:
        for k in value:
            if k not in names:
                errs.append(f"{(path + '.') if path else ''}{k}: поле не описано в схеме вида — допустимо: {', '.join(names)}"
                            f"{CT.suggest(k, names)}")


def check_unit(ctx, rel: str, k: dict, refs=True):
    """(значение, [ошибки]) одной единицы."""
    try:
        v = load(ctx.root / rel)
    except NoYaml:
        raise
    except Exception as e:
        mark = getattr(e, "problem_mark", None)
        return None, [f"не YAML: {str(e).splitlines()[0]}" + (f" (строка {mark.line + 1})" if mark else "")]
    errs = []
    if not isinstance(v, dict):
        return v, [f"единица — словарь полей, а не {_tname(v)}"]
    _STATE["comp"] = component_of(ctx, rel, k) if refs else ""
    validate_fields(ctx, v, k.get("fields", []), "", errs, refs, extra=k.get("extra", False))
    if k.get("id_pattern"):
        uid = unit_id(k, rel, v)
        if not re.fullmatch(k["id_pattern"], uid):
            errs.append(f"ID «{uid}» не по формату {k.get('id_hint') or k['id_pattern']} (имя файла или поле {k.get('id_field') or 'id'})")
    return v, errs


def check(ctx, path=None) -> list:
    """Замечания «файл: поле: что исправить» по схемам видов, ссылкам и порядку (`_order.yaml`)."""
    need_yaml()
    issues = []
    files = unit_files(ctx, path)
    seen = {}
    for f in files:
        k = kind_of(ctx, f)
        if not k or k["name"] == "order":
            continue
        v, errs = check_unit(ctx, f, k)
        issues += [f"{f}: {e}" for e in errs]
        uid = unit_id(k, f, v)
        if (k["name"], uid) in seen:
            issues.append(f"{f}: ID {uid} повторяется (уже в {seen[(k['name'], uid)]})")
        seen[(k["name"], uid)] = f
    on = order_name(ctx)
    for d in sorted({str(Path(f).parent.as_posix()) for f in files}):
        o = f"{d}/{on}" if d != "." else on
        if not (ctx.root / o).is_file():
            continue
        try:
            order = load(ctx.root / o) or []
        except Exception as e:
            issues.append(f"{o}: не YAML: {str(e).splitlines()[0]}")
            continue
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            issues.append(f"{o}: нужен список имён файлов без .yaml")
            continue
        stems = {Path(f).stem for f in files if Path(f).parent.as_posix() == d}
        issues += [f"{o}: нет {n}.yaml — убери имя из списка" for n in order if n not in stems]
        issues += [f"{o}: {n} не в списке — добавь (порядок) или {ctx.command} yaml-fmt {d}" for n in sorted(stems - set(order))]
    return issues


# ---------- формат ----------

def _ordered(value, fields: list):
    if not isinstance(value, dict) or not fields:
        return value
    out = {}
    for f in fields:
        if f["name"] in value:
            out[f["name"]] = _ordered_field(value[f["name"]], f)
    for k, v in value.items():
        if k not in out:
            out[k] = v
    return out


def _ordered_field(v, f: dict):
    if f.get("type") == "dict" and f.get("fields"):
        return _ordered(v, f["fields"])
    item = f.get("items")
    if f.get("type") == "list" and isinstance(item, dict) and item.get("fields") and isinstance(v, list):
        return [_ordered(x, item["fields"]) for x in v]
    return v


def _header(text: str) -> str:
    lines = []
    for l in text.splitlines():
        if not l.startswith("#"):
            break
        lines.append(l)
    return "".join(l + "\n" for l in lines)


def unit_text(ctx, rel: str, value, fields=None) -> str:
    """Текст файла единицы: комментарии в начале прежнего файла + YAML значения в порядке схемы."""
    p = ctx.root / rel
    text = dump(_ordered(value, fields or []), flow_rows=bool(ctx.get("data.flow_rows", True)))
    return (_header(U.read(p)) if p.is_file() else "") + text


def ref_ids(node, keys) -> set:
    """(путь строки, ID) из списковых полей keys — для проверки, что запись не теряет ID."""
    out, stack = set(), [((), node)]
    while stack:
        path, n = stack.pop()
        if isinstance(n, dict):
            for k, x in n.items():
                if k in keys and isinstance(x, list):
                    out |= {f"{'.'.join(path)}:{i}" for i in x}
                else:
                    stack.append((path + (str(k),), x))
        elif isinstance(n, list):
            stack += [(path + (str(i),), x) for i, x in enumerate(n)]
    return out


def restore_ids(old, new, keys):
    """Вернуть в строки new ID полей keys из old, если ID по-прежнему стоит в тексте строки (ID в поле и в тексте — допустимо)."""
    if isinstance(old, dict) and isinstance(new, dict):
        text = " ".join(str(x) for x in new.values() if isinstance(x, str))
        for k in keys:
            if isinstance(old.get(k), list):
                back = [i for i in old[k] if i not in (new.get(k) or []) and re.search(rf"(?<![\w-]){re.escape(str(i))}(?![\w-])", text)]
                if back:
                    new[k] = [i for i in old[k] if i in (new.get(k) or []) or i in back] + [i for i in (new.get(k) or []) if i not in old[k]]
        for k, x in new.items():
            if k in old and k not in keys:
                restore_ids(old[k], x, keys)
    elif isinstance(old, list) and isinstance(new, list):
        for o, n in zip(old, new):
            restore_ids(o, n, keys)


def write_unit(ctx, rel: str, value, fields=None, dry=False, drop_ids=False) -> bool:
    """Записать единицу; True — файл изменился (dry — только сообщить).

    Поля-списки ID из `[data] guard_id_fields` (например via, reqs, decisions) не теряются молча: ID, пропавший из поля, но
    стоящий в тексте строки, возвращается; иначе запись падает с ValueError. Намеренное удаление ID — `drop_ids=True`."""
    p = ctx.root / rel
    keys = tuple(U.as_list(ctx.get("data.guard_id_fields")))
    if keys and p.is_file() and not drop_ids and isinstance(value, (dict, list)):
        try:
            old = load(p)
        except Exception:
            old = None
        if old is not None:
            restore_ids(old, value, keys)
            lost = ref_ids(old, keys) - ref_ids(value, keys)
            if lost:
                raise ValueError(f"{rel}: запись потеряла бы ID из полей: {', '.join(sorted(lost))} (намеренно — drop_ids=True)")
    text = unit_text(ctx, rel, value, fields)
    if p.is_file() and U.read(p) == text:
        return False
    if not dry:
        U.write(p, text)
    return True


def fmt(ctx, path=None) -> list:
    """Порядок ключей по схеме, единый стиль; новые единицы — в `_order.yaml` папки. → изменённые файлы."""
    need_yaml()
    changed = []
    files = unit_files(ctx, path)
    for f in files:
        k = kind_of(ctx, f)
        if not k or k["name"] == "order":
            continue
        try:
            v = load(ctx.root / f)
        except Exception:
            continue  # сначала yaml-check
        try:
            if isinstance(v, dict) and write_unit(ctx, f, v, k.get("fields")):
                changed.append(f)
        except ValueError as e:  # формат потерял бы ID из полей — файл не трогаем
            print(f"⚠️ {e}")
    on = order_name(ctx)
    for d in sorted({Path(f).parent.as_posix() for f in files}):
        o = f"{d}/{on}"
        if (ctx.root / o).is_file():
            try:
                order = load(ctx.root / o) or []
            except Exception:
                continue
            stems = sorted(Path(f).stem for f in files if Path(f).parent.as_posix() == d)
            new = [n for n in order if n in stems] + [n for n in stems if n not in order]
            if write_unit(ctx, o, new):
                changed.append(o)
    ctx.invalidate()
    return changed


# ---------- карточка единицы ----------

def find(ctx, q: str):
    """(rel, вид) по пути, ID, «папка/имя» или названию; иначе (None, None)."""
    q = q.strip()
    for cand in (q, ctx.rel(q) if Path(q).exists() else ""):
        if cand and kind_of(ctx, cand) and (ctx.root / cand).is_file():
            return cand, kind_of(ctx, cand)
    for k in kinds(ctx):
        for uid, (rel, v) in units(ctx, k["name"]).items():
            if q in (uid, Path(rel).stem, f"{Path(rel).parent.name}/{Path(rel).stem}"):
                return rel, k
    low = q.lower()
    for k in kinds(ctx):
        tf = k.get("title")
        for uid, (rel, v) in units(ctx, k["name"]).items():
            if tf and isinstance(v, dict) and str(v.get(tf, "")).lower() == low:
                return rel, k
    return None, None


def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if x not in ("", [], {}, None)}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def _refs_of(ctx, value, fields: list) -> list:
    """Исходящие ссылки: [(каталог, ID)] по полям с ref."""
    out = []

    def walk(v, f):
        if v is None:
            return
        t = f.get("type")
        if t == "list" and isinstance(v, list):
            item = f.get("items") or {}
            item = {"type": item} if isinstance(item, str) else dict(item)
            item.setdefault("ref", f.get("ref"))
            for x in v:
                walk(x, item)
        elif t == "dict" and isinstance(v, dict) and f.get("fields"):
            for ff in f["fields"]:
                walk(v.get(ff["name"]), ff)
        elif isinstance(v, str) and f.get("ref") and not is_none(ctx, v):
            for ref in U.as_list(f["ref"]):
                ent = _catalog(ctx, ref)
                if ent and v in ent[1]:
                    out.append((ref, v))
                    break
                elif ent:
                    found = [(ref, m.group(1) if m.groups() else m.group(0)) for m in ent[0].finditer(v)]
                    out.extend(found)
                    if found:
                        break
            else:
                if not any(_catalog(ctx, r) for r in U.as_list(f["ref"])):
                    out.append((U.as_list(f["ref"])[0], v))
    for f in fields:
        if isinstance(value, dict):
            walk(value.get(f["name"]), f)
    return out


def related(ctx, rel: str, k: dict, value) -> list:
    """«связано»: исходящие ссылки единицы и единицы, которые ссылаются на неё."""
    out = [f"{ref} {x}" for ref, x in _refs_of(ctx, value, k.get("fields", []))]
    me = unit_id(k, rel, value)
    for k2 in kinds(ctx):
        if k2.get("builtin"):
            continue
        for uid, (r2, v2) in units(ctx, k2["name"]).items():
            if r2 == rel:
                continue
            if any(ref == k["name"] and x == me for ref, x in _refs_of(ctx, v2, k2.get("fields", []))):
                out.append(f"{k2['name']} {uid} (ссылается)")
    return list(dict.fromkeys(out))


def card(ctx, q: str) -> str:
    need_yaml()
    rel, k = find(ctx, q)
    if not rel:
        names = ", ".join(x["name"] for x in kinds(ctx)) or "—"
        return f"{q}: единица не найдена — путь data/…, ID, «папка/имя» или название (виды: {names})"
    if k["name"] == "order":
        return f"# {rel} · порядок единиц папки\n" + U.read(ctx.root / rel)
    v, errs = check_unit(ctx, rel, k, refs=False)
    head = f"# {rel} · вид {k['name']} · ID {unit_id(k, rel, v)}" + (f" · ⚠️ схема: {len(errs)} — {ctx.command} yaml-check {rel}" if errs else "")
    body = dump(_strip(v)).rstrip() if isinstance(v, (dict, list)) else U.read(ctx.root / rel).rstrip()
    rel_ids = related(ctx, rel, k, v) if isinstance(v, dict) else []
    tail = ["связано: " + ", ".join(rel_ids)] if rel_ids else []
    tail.append(f"править: {rel} → {ctx.command} yaml-check {rel}" + (f" → {ctx.command} lint {comp}" if (comp := component_of(ctx, rel, k)) else ""))
    if k.get("regen"):
        tail.append(f"перегенерируется (один раз в конце пути): {_subst(k['regen'], rel)}")
    return "\n".join([head, body, *tail])


def component_of(ctx, rel: str, k=None) -> str:
    k = k or kind_of(ctx, rel)
    if k and k.get("component"):
        return _subst(k["component"], rel)
    return ctx.component_of(rel)


# ---------- route / next для файлов данных ----------

def route_lines(ctx, rel: str) -> list:
    k = kind_of(ctx, rel)
    if not k:
        return []
    cmd = ctx.command
    if k["name"] == "order":
        return [f"- Порядок единиц папки (список имён файлов без .yaml); новые имена добавляет {cmd} yaml-fmt."]
    req = [f["name"] for f in k.get("fields", []) if f.get("required")]
    refs = [f"{f['name']} → {f['ref']}" for f in k.get("fields", []) if f.get("ref")]
    meth = ctx.get("data.methodology", "data.md#yaml")
    L = [f"- Единица данных YAML, вид «{k['name']}»" + (f"; обязательные поля: {', '.join(req)}" if req else "")
         + (f"; ссылки: {', '.join(refs)}" if refs else "") + ".",
         f"- Читать — {cmd} unit {rel} (без пустых полей, со связанными ID), а не страницу и не весь каталог данных.",
         f"- Проверка — {cmd} yaml-check {rel}; формат — {cmd} yaml-fmt {rel}. Правило: {ctx.show(ctx.rules_dir / meth.split('#')[0])}"
         + ("#" + meth.split("#", 1)[1] if "#" in meth else "") + "."]
    if k.get("regen"):
        L.append(f"- Перегенерируется — один раз в конце пути изменений: {_subst(k['regen'], rel)}.")
    return L


def regen_line(ctx, rel: str) -> str:
    k = kind_of(ctx, rel)
    if not k or k["name"] == "order":
        return ""
    comp = component_of(ctx, rel, k)
    lint = f" · {ctx.command} lint {comp}" if comp else ""
    s = f"Источник данных (YAML, единица «{k['name']}»). Сейчас: {ctx.command} yaml-check {rel}{lint}."
    if k.get("regen"):
        s += f"\nПерегенерируется — один раз в конце пути изменений: {_subst(k['regen'], rel)}."
    return s
