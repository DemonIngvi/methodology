"""Плагины doctool: регистрация lint-правил, check-правил, команд и каталогов ID.

Плагин — модуль Python. Встроенные лежат здесь (`tools/doctool/plugins/<имя>.py`) и включаются списком
`builtin_plugins = ["datamodel"]`; плагины проекта — файлы или папки из `plugins = ["tools/doc_plugins"]`.

    from doctool.api import lint_rule, check_rule, command, ref_kind

    @lint_rule("deploy-rollback")            # нестрогое правило: yield (компонент, сообщение)
    def rollback(ctx): ...

    @check_rule("purpose")                   # строгое правило: yield сообщение (check вернёт код 1)
    def purpose(ctx): ...

    @command("stats", "сводка по документам")  # новая команда: tools/doc stats [аргументы]; вернуть код возврата
    def stats(ctx, args): ...

    @ref_kind("table")                       # каталог ID для ссылок карточек блоков: вернуть (regex, множество ID)
    def tables(ctx): ...

Настройки плагина — в таблице конфига `[plugin.<имя>]`, доступны как ctx.get("plugin.<имя>.<ключ>").
"""
import importlib
import importlib.util
import sys
from pathlib import Path


class Registry:
    def __init__(self):
        self.lint_rules = {}
        self.check_rules = {}
        self.commands = {}
        self.ref_kinds = {}
        self.loaded = []


REGISTRY = Registry()


def lint_rule(name: str):
    def deco(fn):
        REGISTRY.lint_rules[name] = fn
        return fn
    return deco


def check_rule(name: str):
    def deco(fn):
        REGISTRY.check_rules[name] = fn
        return fn
    return deco


def command(name: str, help: str = ""):
    def deco(fn):
        fn.help = help
        REGISTRY.commands[name] = fn
        return fn
    return deco


def ref_kind(name: str):
    def deco(fn):
        REGISTRY.ref_kinds[name] = fn
        return fn
    return deco


def _load_file(path: Path):
    name = "doc_plugin_" + "_".join(path.with_suffix("").parts[-2:]).replace("-", "_").replace(".", "_")
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    REGISTRY.loaded.append(str(path))


def load(ctx) -> Registry:
    """Загрузить встроенные и проектные плагины из конфига; вернуть реестр."""
    for name in ctx.get("builtin_plugins", []) or []:
        importlib.import_module(f"{__name__}.{name}")
        REGISTRY.loaded.append(f"builtin:{name}")
    for p in ctx.get("plugins", []) or []:
        path = (ctx.root / p).resolve()
        if path.is_dir():
            for f in sorted(path.glob("*.py")):
                if not f.name.startswith("_"):
                    _load_file(f)
        elif path.is_file():
            _load_file(path)
        else:
            raise SystemExit(f"плагин не найден: {p} (конфиг: plugins)")
    ctx.plugins = REGISTRY
    return REGISTRY
