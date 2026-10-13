@AGENTS.md

Сначала — [таблица «тип задачи → читай только сценарий»](AGENTS.md#task-entry) из `AGENTS.md`: для R&D, правки документа, данных и двойного чтения читается только сценарий, README и полные правила — нет.

Claude Code: если в проекте настроены хуки (`.claude/settings.json`), они могут сами вызывать `doc pre` до правки и `doc post` после неё — выжимка правил и замечания проверок придут в разговор ([tools/README.md](tools/README.md#hooks)). Сценарии из `skills/` можно положить в `.claude/skills/<имя>/SKILL.md`, промпт проверяющего — в `.claude/agents/doc-reviewer.md`.
