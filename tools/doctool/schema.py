"""yaml-schema: JSON Schema редактора для единиц данных — из схем видов `[[data.kinds]]` и каталогов.

Для каждого вида — `<schema_dir>/<вид>.schema.json` (по умолчанию `data/_schema`); значения каталогов (ссылки `ref`) — один раз в
`catalog.schema.json` (`enum` с подсказкой в редакторе); порядок папки — `order.schema.json`. Сопоставление «схема → файлы» дописывается
в `.vscode/settings.json` (`yaml.schemas`, расширение Red Hat YAML): остальные настройки файла и чужие схемы сохраняются; файл с
комментариями (JSONC) не трогается — инструмент печатает, что дописать. `check` сообщает, если схема устарела.
Правило: rules/data.md#yaml-units.
"""
import json
import re
from pathlib import Path

from . import catalogs as CT
from . import units as UN
from . import util as U

HEAD = "http://json-schema.org/draft-07/schema#"


def schema_dir(ctx) -> str:
    return str(ctx.get("data.schema_dir", "data/_schema")).strip("/")


def _jsre(rx: str) -> str:
    return "^(?:" + rx + ")$"


def _ref_names(fields, acc: set):
    for f in fields:
        for r in U.as_list(f.get("ref")):
            acc.add(r)
        it = f.get("items")
        if isinstance(it, dict):
            for r in U.as_list(it.get("ref")):
                acc.add(r)
            _ref_names(it.get("fields", []), acc)
        _ref_names(f.get("fields", []), acc)


def catalog_values(ctx, name: str):
    """Отсортированные значения каталога или None (значения не перечислимы)."""
    if name == "param":
        return None
    ent = UN._catalog(ctx, name)
    if not ent or not ent[1]:
        return None
    return sorted(str(x) for x in ent[1])


def _str_schema(ctx, f: dict, defs: set) -> dict:
    out = {"type": "string"}
    if f.get("choices"):
        out["enum"] = list(f["choices"])
    elif f.get("pattern"):
        out["pattern"] = _jsre(f["pattern"])
    refs = U.as_list(f.get("ref"))
    if refs and not f.get("choices"):
        alts = []
        for r in refs:
            if catalog_values(ctx, r) is None:
                alts = []
                break
            alts.append({"$ref": f"catalog.schema.json#/definitions/{r}"})
            defs.add(r)
        if alts:
            if f.get("also"):
                alts.append({"type": "string", "pattern": _jsre(f["also"])})
            alts.append({"const": ""})  # пусто — как в yaml-check
            out = {"anyOf": alts}
    return out


def field_schema(ctx, f: dict, defs: set) -> dict:
    t = f.get("type", "any")
    if t == "str":
        out = _str_schema(ctx, f, defs)
    elif t == "int":
        out = {"type": "integer"}
    elif t == "float":
        out = {"type": "number"}
    elif t == "bool":
        out = {"type": "boolean"}
    elif t == "date":
        out = {"type": "string", "pattern": r"^\d{4}-\d\d-\d\d$"}
    elif t == "list":
        item = f.get("items") or {}
        item = {"type": item} if isinstance(item, str) else dict(item)
        for key in ("ref", "choices", "pattern", "hint", "also"):
            if key in f and key not in item:
                item[key] = f[key]
        out = {"type": "array", "items": field_schema(ctx, item, defs) if item else {}}
        if f.get("none_ok"):
            pre = re.escape(UN.none_prefix(ctx))
            out = {"anyOf": [out, {"type": "string", "pattern": f"^{pre}\\s*\\S",
                                   "description": f"«{UN.none_prefix(ctx)} <почему>» — списка нет, причина обязательна"}]}
    elif t == "dict" and f.get("fields"):
        out = object_schema(ctx, f["fields"], f.get("extra", False), defs, f.get("one_of"))
    elif t == "dict":
        out = {"type": "object"}
    else:
        out = {}
    d = f.get("desc") or f.get("hint")
    if d and f.get("type") != "date":
        out = dict(out, description=str(d))
    return out


def object_schema(ctx, fields: list, extra: bool, defs: set, one_of=None) -> dict:
    out = {"type": "object", "properties": {f["name"]: field_schema(ctx, f, defs) for f in fields},
           "additionalProperties": bool(extra)}
    req = [f["name"] for f in fields if f.get("required")]
    if req:
        out["required"] = req
    if one_of:
        out["anyOf"] = [{"required": [k]} for k in one_of]
    return out


def kind_schema(ctx, k: dict, defs: set) -> dict:
    out = {"$schema": HEAD, "title": f"Единица «{k['name']}»",
           "description": f"Единица данных вида «{k['name']}» (конфиг [[data.kinds]]). Генерируется: {ctx.command} yaml-schema — не править руками."}
    out.update(object_schema(ctx, k.get("fields", []), k.get("extra", False), defs))
    return out


def _editor_glob(g: str) -> str:
    """Файлы порядка (`_order.yaml`) — не единицы: `*.yaml` → `[!_]*.yaml` в последнем сегменте."""
    head, _, tail = g.rpartition("/")
    return (head + "/" if head else "") + ("[!_]" + tail if tail.startswith("*") else tail)


def build(ctx) -> dict:
    """{путь схемы относительно корня: (JSON Schema, glob файлов редактора или None)}."""
    sd = schema_dir(ctx)
    res, defs = {}, set()
    for k in UN.kinds(ctx):
        globs = [_editor_glob(g) for g in U.as_list(k.get("glob"))]
        res[f"{sd}/{k['name']}.schema.json"] = (kind_schema(ctx, k, defs), globs)
    cat = {}
    for name in sorted(defs | set(CT.declared(ctx))):
        vals = catalog_values(ctx, name)
        if vals is not None:
            cat[name] = {"type": "string", "description": CT.about(ctx, name), "enum": vals}
    res[f"{sd}/catalog.schema.json"] = ({"$schema": HEAD, "title": "Каталоги данных",
                                         "description": "Допустимые значения полей-ссылок; строятся из данных и документов — не править руками.",
                                         "definitions": cat}, None)
    on = UN.order_name(ctx)
    res[f"{sd}/order.schema.json"] = ({"$schema": HEAD, "title": "Порядок единиц папки",
                                       "description": "Имена файлов папки без .yaml в порядке показа.", "type": "array",
                                       "items": {"type": "string"}}, [f"**/{on}"])
    return res


def _text(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=1) + "\n"


def _mapping(ctx, built: dict) -> dict:
    return {f"./{rel}": (g if len(g) > 1 else g[0]) for rel, (_, g) in built.items() if g}


def _settings(ctx):
    """(путь, JSON настроек или None при JSONC / ошибке, ошибка)."""
    p = ctx.root / ".vscode" / "settings.json"
    if not p.is_file() or not U.read(p).strip():
        return p, {}, ""
    try:
        v = json.loads(U.read(p))
        return p, v if isinstance(v, dict) else None, "" if isinstance(v, dict) else "не объект"
    except ValueError as e:
        return p, None, str(e)


def _merged(ctx, cur: dict, built: dict) -> dict:
    pref = f"./{schema_dir(ctx)}/"
    m = {k: v for k, v in (cur.get("yaml.schemas") or {}).items() if not k.startswith(pref)}
    m.update(_mapping(ctx, built))
    return dict(cur, **{"yaml.schemas": m})


def write(ctx) -> tuple:
    """Записать схемы и yaml.schemas → (изменённые файлы, замечания)."""
    UN.need_yaml()
    ctx.invalidate()
    built = build(ctx)
    changed, notes = [], []
    want = {}
    for rel, (sch, _) in built.items():
        want[rel] = _text(sch)
        p = ctx.root / rel
        if not p.is_file() or U.read(p) != want[rel]:
            U.write(p, want[rel])
            changed.append(rel)
    d = ctx.root / schema_dir(ctx)
    for p in sorted(d.glob("*.schema.json")) if d.is_dir() else []:
        rel = p.relative_to(ctx.root).as_posix()
        if rel not in want:
            p.unlink()
            changed.append(rel + " (удалён)")
    sp, cur, err = _settings(ctx)
    if cur is None:
        notes.append(f".vscode/settings.json не изменён ({err}): добавь yaml.schemas вручную — {json.dumps(_mapping(ctx, built), ensure_ascii=False)}")
    else:
        new = _merged(ctx, cur, built)
        if new.get("yaml.schemas") != cur.get("yaml.schemas"):
            U.write(sp, json.dumps(new, ensure_ascii=False, indent=2) + "\n")
            changed.append(".vscode/settings.json")
    return changed, notes


def stale(ctx) -> list:
    """Замечания check: схема редактора не совпадает с конфигом и данными (если схемы заведены: папка есть или задан schema_dir)."""
    sd = schema_dir(ctx)
    if not UN.kinds(ctx) or not (ctx.get("data.schema_dir") or (ctx.root / sd).is_dir()):
        return []
    built = build(ctx)
    cmd = f"{ctx.command} yaml-schema"
    out = []
    for rel, (sch, _) in built.items():
        p = ctx.root / rel
        if not p.is_file() or U.read(p) != _text(sch):
            out.append(f"{rel}: устарела — {cmd}")
    d = ctx.root / sd
    for p in sorted(d.glob("*.schema.json")) if d.is_dir() else []:
        rel = p.relative_to(ctx.root).as_posix()
        if rel not in built:
            out.append(f"{rel}: лишняя — {cmd}")
    _, cur, _err = _settings(ctx)  # локальный файл (.vscode/ обычно не в git): проверяется, если он есть и читается
    if cur and (ctx.root / ".vscode" / "settings.json").is_file() and \
            _merged(ctx, cur, built).get("yaml.schemas") != cur.get("yaml.schemas"):
        out.append(f".vscode/settings.json: yaml.schemas устарело — {cmd}")
    return out
