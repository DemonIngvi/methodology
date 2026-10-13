# Минимальный пример проекта для doctool

Учебный проект «Биллинг»: один компонент, одна спека, одна задача, карточки блоков (Markdown и YAML), единицы данных YAML, Deploy, замечания ролей, реестр подтверждений и журнал расхода токенов. На нём прогоняются все команды `tools/doc` (тесты — `tests/test_doctool.py`). Настройка — [doc.config.toml](doc.config.toml).

| Документ | Тип |
|---|---|
| [architecture.md](architecture.md) | главная / архитектура |
| [design.md](design.md) | решения (D-номера) |
| [specs/billing/spec.md](specs/billing/spec.md) | спека: требования R-BIL-… |
| [specs/billing/interfaces.md](specs/billing/interfaces.md) | параметры и интерфейсы спеки |
| [specs/billing/open-questions.md](specs/billing/open-questions.md) | открытые вопросы спеки |
| [specs/billing/traceability.md](specs/billing/traceability.md) | трассировка (RTM) |
| [components/billing/blocks/exporter.md](components/billing/blocks/exporter.md) | карточка блока (Markdown) |
| `data/blocks/billing/mailer.yaml`, `data/models/Invoice.yaml`, `data/stories/billing/US-BIL-1.yaml` | единицы данных YAML: блок (строки таблиц — словари с именованными полями, значения ссылок из каталогов `[[data.catalogs]]`, тип значения параметра из `interfaces.md`), модель, история; схемы видов — `[[data.kinds]]` конфига |
| `data/_schema/` | JSON Schema редактора (генерируется `yaml-schema`; `check` ловит устаревшую) |
| `data/costs.yaml` · [token-costs.md](token-costs.md) | журнал расхода токенов (записи-образцы) и страница из него (генерируется `cost md`) |
| [deploy/billing.md](deploy/billing.md) | Deploy |
| [tasks/1.1-invoice-export.md](tasks/1.1-invoice-export.md) | задача |
| [ROADMAP.md](ROADMAP.md) · [TASKS.md](TASKS.md) | Roadmap и сводная таблица (генерируется плагином) |
| [reviews/findings/billing.md](reviews/findings/billing.md) | замечания ролей |
| [rnd/README.md](rnd/README.md) | реестр R&D |
| [approvals.md](approvals.md) · [approvals-summary.md](approvals-summary.md) | реестр и сводка подтверждений (ведёт инструмент) |

Попробовать из этой папки:

```bash
python ../../tools/doc route specs/billing/spec.md
python ../../tools/doc next specs/billing/spec.md
python ../../tools/doc check
python ../../tools/doc lint
python ../../tools/doc review-pack deploy/billing.md Д
python ../../tools/doc unit billing/mailer        # нужен PyYAML
python ../../tools/doc yaml-check
python ../../tools/doc yaml-schema           # JSON Schema для редактора
python ../../tools/doc yaml-migrate --dry    # прежний позиционный вид → именованные поля
python ../../tools/doc review-batch billing
python ../../tools/doc cost
python ../../tools/doc plugins
```
