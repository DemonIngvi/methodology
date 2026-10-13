"""API для плагинов: `from doctool.api import lint_rule, check_rule, command, ref_kind, card_kind, migration, migration_report`.

Описание — tools/doctool/plugins/__init__.py и tools/README.md#plugins.
"""
from .plugins import card_kind, check_rule, command, lint_rule, migration, migration_report, ref_kind  # noqa: F401
from . import util  # noqa: F401  — таблицы, разделы, glob, запись с LF
