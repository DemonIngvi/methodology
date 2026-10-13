"""Плагин проекта (пример): свои правила lint и check и команда-генератор.

Подключается в doc.config.toml: plugins = ["tools/doc_plugins"]. API — tools/README.md#plugins.
"""
import re

from doctool.api import check_rule, command, lint_rule, util as U


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
