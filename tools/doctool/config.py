"""Конфиг проекта: `doc.config.toml` (tomllib, Python 3.11+; на 3.10 — пакет tomli, если есть) или `doc.config.json`.

Значения по умолчанию соответствуют раскладке методологии (rules/doc-types.md); конфиг проекта их дополняет и переопределяет.
"""
import copy
import json
import os
from pathlib import Path

CONFIG_NAMES = ("doc.config.toml", "doc.config.json")


class ConfigError(SystemExit):
    pass


DEFAULTS = {
    "project": {
        "name": "",
        "command": "tools/doc",          # как вызывать инструмент — подставляется в подсказки
        "rules_dir": "rules",            # методологии; может быть вне корня (../methodology/rules)
        "work_dir": ".doctool",          # пакеты проверки, схемы, блокировки — не коммитится
    },
    "paths": {
        "exclude": [".git", "node_modules", ".venv", "venv", "site", "build", "dist", "__pycache__", ".doctool",
                    ".pytest_cache", ".mypy_cache", ".idea", ".vscode"],
    },
    # Таблица типов в правилах: из неё берутся колонки «Обязательные разделы», «Правила», «Синхронизировать», «Методология»
    # (копий правил в конфиге нет). Если [[types]] не заданы — типы и пути берутся из колонки «Где» этой таблицы.
    "types_table": {"file": "doc-types.md", "header": "| Тип |"},
    "types": [],
    "generated": [],
    # Роли: [[roles]] letter, name, question. Пусто — из таблицы «| Буква | Роль | Вопрос |» правил подтверждений.
    "roles": [],
    "roles_table": {"file": "approvals.md", "header": "| Буква |", "types_header": "| Тип документа |",
                    "requirements_header": "| Требование | Роли |"},
    "approvals": {
        "enabled": True,
        "registry": "approvals.md",
        "summary": "",
        "skip": [],
        "version_ignore": [],
        "lock_timeout": 30,
        "stale_lock": 300,
        "sync_on_check": True,
    },
    "requirements": {
        "specs": ["specs/*/spec.md"],
        "id": r"R-[A-Z0-9]+-\d+",
        "heading": r"^###\s+Requirement:\s*(.+?)\s*$",
        "task_field": "Реализует задача",
        "no_task": ["не в MVP", "BACKLOG"],
        "anchor": True,
        "scenario": r"^####\s+Scenario:\s*(.+?)\s*$",
        "min_scenarios": 3,
        "open_questions": "open-questions.md",
        "open_question_row": r"^\|\s*[A-Za-zА-Яа-яЁё]+-\d+\s*\|",
        "open_question_id": r"(?<![\w-])ОВ-(?:\w+-)*\d+(?![\w-])",   # ID открытого вопроса в тексте
        # lint fmea-scenarios: строка FMEA файла рядом со спекой ↔ сценарий отказа с тем же именем
        "fmea": {"file": "interfaces.md", "section": "Отказы (FMEA)"},
        # lint rtm: строка RTM = требование («Реализует задача», число сценариев)
        "rtm": {"file": "traceability.md", "id_col": "Требование", "task_col": "Задач", "count_col": "Сценари"},
        "approvals": {"enabled": False, "file": "approvals.md", "default": "", "by_title": {},
                      "version_ignore": [r"^ID:.*$", r"<a id=[^>]*></a>"]},
    },
    "tasks": {
        "glob": ["tasks/*.md"],
        "id": r"\d+\.\d+",
        "id_from_name": r"^(\d+\.\d+)-",
        "requirements_section": "Закрывает требования",
        "closed": ["❌ закрыта"],
        "component_column": "Компонент",
        "subtasks": {"section": "Декомпозиция", "hours_col": 3, "done_col": 4, "min": 2, "max": 8, "total_label": "Итого"},
        "roadmap": "ROADMAP.md",
        "roadmap_hours_col": 0,
        # lint task-acceptance-coverage: требование из «Закрывает требования» названо в этих разделах (ID или имя сценария)
        "acceptance_sections": ["Приёмка", "Декомпозиция"],
        "acceptance_exempt": [],          # заголовки требований (начало), которые проверяются общей подзадачей
        # lint task-estimate-range: сумма подзадач в днях — в диапазоне оценки шапки задачи
        "estimate": {"table": "| Этап |", "column": "Человек", "hours_per_day": 8, "tolerance_days": 0.25},
        "open_items_section": "Что проработать",
        "roadmap_total_label": "Итого",
        "derived": [],
    },
    "components": {"list": [], "source": "", "key": "", "from_path": []},
    "findings": {"dir": "reviews/findings", "file": "{component}.md", "id": "Z-{code}-{n}"},
    "blocks": {
        "glob": [],
        "source": "",
        "table_header": "| Поле | Значение |",
        "kind_field": "Вид",
        "kinds": {"интерфейс": "Экраны", "сервис": "Операции", "хранилище": "Схема"},
        "fields": [
            {"name": "Что делает", "required": True},
            {"name": "Технология", "required": True},
            {"name": "Истории", "required": True, "refs": ["story"]},
            {"name": "Входы и выходы", "required": True, "refs": ["interface"]},
            {"name": "Данные", "required": True, "refs": ["table"]},
            {"name": "Параметры", "required": True, "refs": ["param"]},
            {"name": "Требования", "required": True, "refs": ["requirement"]},
            {"name": "Задачи", "required": True, "refs": ["task"]},
            {"name": "При сбое", "required": True},
            {"name": "Почему так", "required": True},
        ],
        "screens": {"section": "Экраны", "status_col": "Статус", "operation_col": "Операция", "new": ["новое", "доработка"]},
        # lint block-refs: ID этих каталогов, названные в карточке, должны быть в своём поле
        "refs_cover": [{"kind": "requirement", "field": "Требования"}, {"kind": "table", "field": "Данные"}],
        # карточки из единиц YAML ([[data.kinds]]): вид единицы, поле карточки → ключ YAML, вид блока → ключ раздела
        "data_kind": "",
        "data_map": {},
        "data_kind_field": "kind",
        "data_sections": {},
    },
    # Данные в YAML по единице (rules/data.md#yaml): [[data.kinds]] name, glob, id, id_pattern, title, component, regen, fields
    "data": {"kinds": [], "order_file": "_order.yaml", "require_yaml": False, "methodology": "data.md#yaml"},
    # Учёт расхода токенов (rules/token-costs.md): журнал, генерируемая страница, названия процессов, допуск
    "costs": {"file": "", "page": "", "titles": {}, "tolerance": 2},
    "ids": {},
    "graph": {"nodes": {}, "edges": []},
    "lint": {"enable": ["subtasks", "requirement-scenarios", "block-card", "block-screens", "recommended-sections",
                        "task-acceptance", "task-acceptance-coverage", "task-estimate-range", "open-question-refs",
                        "fmea-scenarios", "rtm", "block-refs", "deploy-migrations"],
             "placeholders_exclude": ["templates/**"],
             "deploy": {"types": ["Deploy"], "section": "Обновление и откат", "words": ["миграц"], "data_field": "Данные"}},
    "check": {"disable": [], "entries": ["README.md", "AGENTS.md", "CLAUDE.md"], "index_names": ["README.md", "index.md"],
              "orphans_ignore": [], "nav": [], "nav_base": "", "anchor_styles": ["github", "mkdocs"]},
    "tooling": {"docs": ["AGENTS.md", "CLAUDE.md", "skills/*.md", "agents/*.md", ".claude/skills/*/SKILL.md", ".claude/agents/*.md"]},
    # review-batch: карточки страниц для всех ролей; card в [[types]] — task | block | unit | sections | плагин
    "review": {"checklist_sections": ["Чек-лист", "Из чего состоит"], "card_max": 2500, "block_max": 1500,
               "batch_max": 30, "chars_per_token": 3},
    "move": {"extra": ["**/*.py", "**/*.toml", "**/*.json", "**/*.yml", "**/*.yaml", "**/*.txt"]},
    "rnd": {"dir": "rnd", "template": "templates/rnd.md", "index": "rnd/README.md", "check": True, "check_glob": [],
            "index_row": "| [{title}]({file}) | {title} | черновик | v3.1 |"},
    "diagram": {"mermaid": "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"},
    "plugins": [],
    "builtin_plugins": [],
    "plugin": {},
}


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_data(path: Path):
    """Файл данных: TOML, JSON или YAML (YAML — только если установлен PyYAML)."""
    text = Path(path).read_text(encoding="utf-8")
    suf = Path(path).suffix.lower()
    if suf == ".json":
        return json.loads(text)
    if suf == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError:
            try:
                import tomli as tomllib  # type: ignore
            except ModuleNotFoundError:
                raise ConfigError(f"{path}: для TOML нужен Python 3.11+ (tomllib) или пакет tomli; либо используйте JSON")
        try:
            return tomllib.loads(text)
        except Exception as e:  # tomllib.TOMLDecodeError
            raise ConfigError(f"{path}: ошибка TOML: {e}")
    if suf in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError:
            raise ConfigError(f"{path}: для YAML нужен PyYAML; либо используйте JSON или TOML")
        return yaml.safe_load(text)
    raise ConfigError(f"{path}: неизвестный формат данных (нужен .toml, .json, .yaml)")


def find_root(start=None, explicit=None):
    """Корень проекта: --root / DOC_ROOT, иначе ближайшая вверх папка с doc.config.*, иначе git-корень, иначе текущая."""
    if explicit:
        return Path(explicit).resolve()
    if os.environ.get("DOC_ROOT"):
        return Path(os.environ["DOC_ROOT"]).resolve()
    here = Path(start or os.getcwd()).resolve()
    for d in [here, *here.parents]:
        if any((d / n).is_file() for n in CONFIG_NAMES):
            return d
    for d in [here, *here.parents]:
        if (d / ".git").exists():
            return d
    return here


def load(root: Path):
    """(конфиг с умолчаниями, путь файла конфига или None)."""
    for n in CONFIG_NAMES:
        p = root / n
        if p.is_file():
            data = load_data(p)
            if not isinstance(data, dict):
                raise ConfigError(f"{p}: ожидается таблица верхнего уровня")
            return merge(DEFAULTS, data), p
    cfg = copy.deepcopy(DEFAULTS)
    cfg["approvals"]["enabled"] = False  # без конфига инструмент ничего не пишет в проект
    return cfg, None
