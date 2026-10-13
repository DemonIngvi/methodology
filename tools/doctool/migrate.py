"""yaml-migrate: перевод единиц данных из прежнего вида в текущую схему (идемпотентно).

Механизм общий, правила — проектные:
- в конфиге: у спискового поля `positional = ["dir", "with", "what"]` — строка-список `[a, b, c]` становится словарём с именованными полями;
- в плагине: `@migration("вид")` — функция `fn(ctx, значение, путь) -> новое значение` (например, вынести ID из текста в поля);
  `@migration_report("вид")` — `fn(ctx, значение, путь) -> [что осталось текстом]` для ручного разбора.

Файл читается как есть, правила применяются по порядку (конфиг, затем плагины), результат проверяется по схеме вида (структура и типы),
записывается через `yaml-fmt` (порядок ключей по схеме, строки таблиц в одну строку). Повторный запуск ничего не меняет.
Правило: rules/data.md#yaml-units.
"""
import copy

from . import units as UN


def _positional(v, fields: list, path: str, notes: list):
    """Применить `positional` из схемы к значению (рекурсивно по dict / list)."""
    if not isinstance(v, dict):
        return v
    out = dict(v)
    for f in fields:
        n = f["name"]
        if n not in out:
            continue
        pos = f.get("positional")
        item = f.get("items") if isinstance(f.get("items"), dict) else {}
        if f.get("type") == "list" and isinstance(out[n], list):
            rows = []
            for i, r in enumerate(out[n]):
                if pos and isinstance(r, list):
                    if len(r) > len(pos):
                        notes.append(f"{path}{n}[{i}]: в строке {len(r)} значений, а полей {len(pos)} — оставлена как есть")
                        rows.append(r)
                        continue
                    r = {k: x for k, x in zip(pos, r)}
                if item.get("fields"):
                    r = _positional(r, item["fields"], f"{path}{n}[{i}].", notes)
                rows.append(r)
            out[n] = rows
        elif f.get("type") == "dict" and f.get("fields"):
            out[n] = _positional(out[n], f["fields"], f"{path}{n}.", notes)
    return out


def rules(ctx, name: str) -> list:
    reg = ctx.plugins.migrations if ctx.plugins else {}
    return reg.get(name, [])


def has_rules(ctx, k: dict) -> bool:
    def pos(fields):
        return any(f.get("positional") or pos(f.get("fields", [])) or pos((f.get("items") or {}).get("fields", [])
                                                                          if isinstance(f.get("items"), dict) else []) for f in fields)
    return bool(rules(ctx, k["name"])) or pos(k.get("fields", []))


def migrate(ctx, path=None, dry=False) -> tuple:
    """(изменённые файлы, ошибки, остаток текстом)."""
    UN.need_yaml()
    changed, errors, left = [], [], []
    for rel in UN.unit_files(ctx, path):
        k = UN.kind_of(ctx, rel)
        if not k or k["name"] == "order" or not has_rules(ctx, k):
            continue
        try:
            old = UN.load(ctx.root / rel)
        except Exception as e:
            errors.append(f"{rel}: не YAML: {str(e).splitlines()[0]}")
            continue
        if not isinstance(old, dict):
            errors.append(f"{rel}: единица — словарь полей")
            continue
        notes = []
        v = _positional(copy.deepcopy(old), k.get("fields", []), "", notes)
        for fn in rules(ctx, k["name"]):
            v = fn(ctx, v, rel)
        errors += [f"{rel}: {n}" for n in notes]
        errs = []
        UN.validate_fields(ctx, v, k.get("fields", []), "", errs, refs=False, extra=k.get("extra", False))
        if errs:
            errors.append(f"{rel}: после перевода не по схеме: " + "; ".join(errs[:3]))
            continue
        if UN.write_unit(ctx, rel, v, k.get("fields"), dry=dry):
            changed.append(rel)
        reg = ctx.plugins.migration_reports if ctx.plugins else {}
        for fn in reg.get(k["name"], []):
            left += [f"{rel}: {x}" for x in fn(ctx, v, rel)]
    ctx.invalidate()
    return changed, errors, left
