"""Плагин проекта (пример): свои правила lint и check и команда-генератор.

Подключается в doc.config.toml: plugins = ["tools/doc_plugins"]. API — tools/README.md#plugins.
"""
import re

from doctool.api import check_rule, command, lint_rule, migration, migration_report, util as U


@check_rule("purpose")
def purpose(ctx):
    """Purpose спеки — не меньше 50 символов (rules/spec-criteria.md)."""
    for f in ctx.spec_files():
        body = U.section(ctx.text(f), "Purpose")
        if len(U.plain(body)) < 50:
            yield f"{f}: Purpose короче 50 символов"


@lint_rule("agent-estimate")
def agent_estimate(ctx):
    """Оценка агента в задаче — диапазон «от–до» (rules/tasks.md)."""
    for t in ctx.tasks().values():
        for row in U.table_rows(t.text, "| Этап |"):
            if not re.search(r"\d+\s*[–-]\s*\d+", row.get("Агент", "")):
                yield t.component, f"задача {t.id}: оценка агента не диапазоном («{row.get('Агент', '')}»)"


@command("tasks-table", "сгенерировать TASKS.md из документов задач")
def tasks_table(ctx, args):
    L = ["# Сводная таблица задач", "", "← [Roadmap](ROADMAP.md)", "",
         "Генерируется: `tools/doc tasks-table` из `tasks/*.md`; руками не правится.", "",
         "| Задача | Компонент | Требования | Часы |", "|---|---|---|---|"]
    for tid, t in sorted(ctx.tasks().items()):
        L.append(f"| [{tid}]({t.file}) | {t.component or '—'} | {', '.join(sorted(t.reqs))} | {t.total or '—':g} |"
                 if t.total else f"| [{tid}]({t.file}) | {t.component or '—'} | {', '.join(sorted(t.reqs))} | — |")
    U.write(ctx.root / "TASKS.md", "\n".join(L) + "\n")
    print("TASKS.md обновлён")
    return 0


# ---------- проектная механика до ролей (обобщённые правила — в ядре, rules/approvals.md#economy) ----------

ENDPOINT = re.compile(r"`(GET|POST|PUT|PATCH|DELETE) (/[^`\s?]*)")


@lint_rule("screen-endpoints")
def screen_endpoints(ctx):
    """Эндпоинт с экрана или из операций блока есть в каталоге API проекта ([plugin.house] api_pages)."""
    pages = ctx.glob(ctx.get("plugin.house.api_pages", []) or [])
    known = {f"{m} {p}" for f in pages for m, p in ENDPOINT.findall(ctx.text(f))}
    for b in ctx.blocks():
        for sec in b.sections.values():
            for m, p in ENDPOINT.findall(sec):
                if f"{m} {p}" not in known:
                    yield b.component or "—", f"блок «{b.title}»: {m} {p} нет в каталоге API ({', '.join(pages) or '—'}) → строка эндпоинта"


@lint_rule("deploy-config-example")
def deploy_config_example(ctx):
    """Параметр спеки хранится в values ([plugin.house] config_stores) → в Deploy компонента есть «Пример конфигурации»."""
    stores = [s.lower() for s in ctx.get("plugin.house.config_stores", []) or []]
    for f in ctx.glob("specs/*/interfaces.md"):
        comp = ctx.component_of(f)
        params = [U.plain(U.col(r, "Параметр")) for r in U.table_rows(ctx.text(f), "| Параметр |")
                  if U.plain(U.col(r, "Хранилище")).lower() in stores]
        dep = f"deploy/{comp}.md"
        if params and (ctx.root / dep).is_file() and "## Пример конфигурации" not in ctx.text(dep):
            yield comp, f"{dep}: нет «Пример конфигурации», хотя параметры в values: {', '.join(params[:3])}"


WRITE = ("POST", "PUT", "PATCH", "DELETE")


@lint_rule("endpoint-errors")
def endpoint_errors(ctx):
    """У всех изменяющих эндпоинтов каталога API (POST / PUT / PATCH / DELETE, любой статус) — коды ошибок (rules/api.md)."""
    for f in ctx.glob(ctx.get("plugin.house.api_pages", []) or []):
        for r in U.table_rows(ctx.text(f), "| Метод и путь"):
            m = ENDPOINT.search(U.col(r, "Метод и путь"))
            if not m or m.group(1) not in WRITE:
                continue
            errors = U.plain(U.col(r, "Ошибки"))
            if not re.search(r"\b[45]\d\d\b", errors):
                yield ctx.component_of(f) or "—", f"{m.group(1)} {m.group(2)}: нет кодов ошибок (колонка «Ошибки»; «ошибки: —» не годится) → {f}"


# ---------- правила yaml-migrate (позиционные строки → именованные поля делает ядро по `positional` схемы) ----------

@migration("block")
def io_via(ctx, v, rel):
    """I-код вместо описания в строке io → поле via."""
    for r in v.get("io") or []:
        if isinstance(r, dict) and re.fullmatch(r"I\d+(,\s*I\d+)*", str(r.get("what", "")).strip()):
            r["via"] = re.findall(r"I\d+", r.pop("what"))
    return v


@migration_report("block")
def io_left(ctx, v, rel):
    """I-код остался внутри текста — вынести в via вручную (иначе страница изменится)."""
    return [f"io[{i}]: I-код в тексте «{r['what']}» — вынести в via" for i, r in enumerate(v.get("io") or [])
            if isinstance(r, dict) and re.search(r"\bI\d+\b", str(r.get("what", "")))]
