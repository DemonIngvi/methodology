"""Встроенный плагин-пример «модель данных»: проектные проверки вынесены из ядра сюда.

Включение: `builtin_plugins = ["datamodel"]` и

    [plugin.datamodel]
    file = "data/model.json"                    # JSON / TOML / YAML: таблицы и интерфейсы
    interfaces_in = ["specs/*/interfaces.md"]   # где объявляются I-коды (строки таблиц `| I1 |`)

Формат файла:

    {"tables": {"invoices": {"owner": "billing", "columns": {"id": "uuid", "amount": "numeric"}}},
     "interfaces": {"I1": {"from": "billing", "to": "mailer", "what": "счёт"}}}

Что даёт:
- каталоги ID `table` и `interface` — по ним check проверяет поля карточек блоков «Данные» и «Входы и выходы»;
- check `datamodel`: I-коды спек есть в модели и наоборот;
- lint `datamodel-migrations`: каждая таблица компонента названа хотя бы в одной его задаче (миграцию кто-то делает, rules/tasks.md).

По этому образцу пишутся проверки, которые зависят от устройства проекта (OpenAPI форка, схема БД и т. п.).
"""
import re

from ..config import load_data
from . import check_rule, lint_rule, ref_kind


def _model(ctx) -> dict:
    def load():
        f = ctx.get("plugin.datamodel.file", "")
        if not f or not (ctx.root / f).is_file():
            return {"tables": {}, "interfaces": {}}
        d = load_data(ctx.root / f) or {}
        return {"tables": d.get("tables", {}) or {}, "interfaces": d.get("interfaces", {}) or {}}
    return ctx.cached("plugin.datamodel", load)


@ref_kind("table")
def tables(ctx):
    m = _model(ctx)["tables"]
    names = set(m)
    for t, v in m.items():
        cols = (v or {}).get("columns", {}) if isinstance(v, dict) else {}
        names |= {f"{t}.{c}" for c in cols}
    return re.compile(r"`([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)?)`"), names


@ref_kind("interface")
def interfaces(ctx):
    return re.compile(r"\bI\d+\b"), set(_model(ctx)["interfaces"])


@check_rule("datamodel")
def check(ctx):
    model = set(_model(ctx)["interfaces"])
    declared = {}
    for f in ctx.glob(ctx.get("plugin.datamodel.interfaces_in", []) or []):
        for code in re.findall(r"^\|\s*(I\d+)\s*\|", ctx.text(f), re.M):
            declared.setdefault(code, f)
    for code, f in sorted(declared.items()):
        if code not in model:
            yield f"{f}: интерфейс {code} не описан в модели данных ({ctx.get('plugin.datamodel.file')})"
    if ctx.get("plugin.datamodel.interfaces_in"):
        for code in sorted(model - set(declared)):
            yield f"интерфейс {code} модели данных не объявлен ни в одной спеке"


@lint_rule("datamodel-migrations")
def migrations(ctx):
    by_comp = {}
    for t in ctx.tasks().values():
        by_comp.setdefault(t.component, []).append(t.text)
    for name, v in _model(ctx)["tables"].items():
        owner = (v or {}).get("owner", "") if isinstance(v, dict) else ""
        if owner and owner in by_comp and not any(name in x for x in by_comp[owner]):
            yield owner, f"таблица `{name}`: не названа ни в одной задаче компонента (миграция)"
