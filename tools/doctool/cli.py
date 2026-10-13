"""Командная строка: `tools/doc [--root ПАПКА] <команда> [аргументы]`. Контракт — tools/README.md."""
import json
import re
import sys
from pathlib import Path

USAGE = """doctool — правило в момент правки, проверки, подтверждения ролями.

  tools/doc route <файл>              тип документа, выжимка правил, методология
  tools/doc pre <файл>                до правки: код 2 и источник, если файл генерируется
  tools/doc post <файл>               после правки: выжимка правил и замечания проверок
  tools/doc generated <файл>          откуда и чем генерируется файл (или пусто)
  tools/doc next <файл>               следующие документы по графу связности и их методологии
  tools/doc check [--only a,b]        строгие проверки (код 1 при замечаниях)
  tools/doc lint [компонент] [--strict]  нестрогие проверки по компонентам (код 0)
  tools/doc staged                    проверки для git pre-commit (код 1 при замечаниях)
  tools/doc hook-pre | hook-post      то же для хуков агентов (JSON хука на stdin)
  tools/doc move <старый> <новый>     перенос файла или папки и перепись всех ссылок
  tools/doc diagram <файл>            mermaid-схемы файла → HTML-страница
  tools/doc rnd <slug|путь.md> <тема>  заготовка R&D v3.2 по шаблону + строка в реестре R&D
  tools/doc rnd-check <файл>...       структура R&D v3.2: Э1–Э10, «Вывод этапа», ворота G1–G10, источники (код 1)
  tools/doc blocks                    заполненность карточек блоков
  tools/doc unit <ID|путь>            карточка единицы данных YAML: поля без пустых + «связано: …»
  tools/doc yaml-check [путь]         схема единиц data/: поля, типы, значения, ссылки, _order.yaml (код 1)
  tools/doc yaml-fmt [путь]           порядок ключей по схеме, единый стиль, новые единицы в _order.yaml
  tools/doc yaml-schema               JSON Schema редактора (data/_schema) и yaml.schemas в .vscode/settings.json
  tools/doc yaml-migrate [путь] [--dry]  перевод единиц в текущую схему (positional → именованные поля, правила плагинов)
  tools/doc review-batch <комп>[,<комп>…] [--max 40] [--force]  пачка ⏳-страниц чистых компонентов (lint = 0; --force — обойти)
  tools/doc approve-batch <файл.json> [--human]  решения пачки [{page, role, ok, comment}] одним захватом реестра; шаблон и комментарий без факта — отказ
  tools/doc review-pack <стр.> <буква>  пакет проверки одной роли (запасной режим)
  tools/doc review-queue [буква]      что ждёт решения роли
  tools/doc approve <стр.|ID требования> <буква> ok|no "<комментарий>" [--human] [--who ИМЯ]
  tools/doc cost [add …|md]           расход токенов: сводка, запись замера, страница (допуск ±2×)
  tools/doc finding <компонент> <ID[,ID]> ok|wait|owner|reject "<что сделано>"
  tools/doc finding <компонент> next-id  следующий свободный ID замечания
  tools/doc init [--json] [--force]   создать doc.config.toml (шаблон) в текущей папке
  tools/doc plugins                   загруженные плагины, их правила и команды

Глобально: --root ПАПКА (или DOC_ROOT) — корень проекта; по умолчанию — ближайшая вверх папка с doc.config.*.
"""


def _out(s: str = "", err: bool = False):
    stream = sys.stderr if err else sys.stdout
    stream.write(s + ("\n" if not s.endswith("\n") else ""))


def _hook_file():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        d = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return None
    ti = d.get("tool_input") or d.get("toolInput") or {}
    return ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or d.get("file_path")


def _ctx(root):
    from .core import Ctx
    from . import plugins
    ctx = Ctx(root)
    plugins.load(ctx)
    return ctx


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < (3, 10):
        _out("нужен Python 3.10+", err=True)
        return 1
    argv = list(sys.argv[1:] if argv is None else argv)
    root = None
    if "--root" in argv:
        i = argv.index("--root")
        root = argv[i + 1]
        del argv[i:i + 2]
    cmd, args = (argv[0] if argv else ""), argv[1:]

    if cmd in ("", "help", "-h", "--help"):
        _out(USAGE)
        return 0
    if cmd == "init":
        from . import misc
        p = misc.init(Path(root or ".").resolve(), as_json="--json" in args, force="--force" in args)
        _out(f"создан {p}; заполните типы документов, роли, граф — затем tools/doc check")
        return 0

    from . import approvals as A, checks as K, lint as LN, misc as M, move as MV, review as RV, route as R, rnd as RD
    ctx = _ctx(root)

    def need(n, usage):
        if len(args) < n:
            raise SystemExit(f"использование: {ctx.command} {usage}")

    if cmd == "route":
        need(1, "route <файл>")
        _out(R.route(ctx, ctx.rel(args[0])) or "нет правил для этого пути (конфиг [[types]] или колонка «Где» таблицы типов)")
    elif cmd == "generated":
        need(1, "generated <файл>")
        _out(R.generated_line(ctx, ctx.rel(args[0])))
    elif cmd == "pre":
        need(1, "pre <файл>")
        rel = ctx.rel(args[0])
        g = ctx.generated_of(rel)
        if g:
            _out(f"{rel} генерируется ({g.get('by', '?')}). Правь источник: {g.get('source', '?')}.", err=True)
            return 2
    elif cmd == "post":
        need(1, "post <файл>")
        rel = ctx.rel(args[0])
        out = R.route(ctx, rel)
        issues = K.run(ctx)
        _out((out or f"{rel}: нет правил для этого пути")
             + ("\n\nЗамечания проверок:\n" + "\n".join(f"- {i}" for i in issues) if issues else "\n\nЗамечаний проверок нет."))
    elif cmd == "next":
        need(1, "next <файл>")
        _out(R.next_docs(ctx, ctx.rel(args[0])))
    elif cmd == "check":
        only = None
        if "--only" in args:
            only = set(args[args.index("--only") + 1].split(","))
        issues = K.run(ctx, only)
        _out("\n".join(f"⚠️ {i}" for i in issues) or "замечаний нет")
        return 1 if issues else 0
    elif cmd == "lint":
        comp = next((a for a in args if not a.startswith("--")), "")
        res = LN.run(ctx)
        shown = 0
        for c in sorted(res):
            if comp and c != comp:
                continue
            _out(f"{c}: {len(res[c])}")
            for m in res[c]:
                _out(f"  - {m}")
            shown += len(res[c])
        if not shown:
            _out("пробелов нет")
        return 1 if (shown and "--strict" in args) else 0
    elif cmd == "staged":
        bad = M.staged_generated(ctx)
        issues = K.run(ctx)
        for x in bad + issues:
            _out(f"⚠️ {x}")
        if not bad and not issues:
            _out("замечаний нет")
        return 1 if bad or issues else 0
    elif cmd == "hook-pre":
        f = _hook_file()
        rel = ctx.rel(f) if f else ""
        g = ctx.generated_of(rel) if rel else None
        if g:
            _out(f"{rel} генерируется ({g.get('by', '?')}). Правь источник: {g.get('source', '?')}, затем перегенерируй.", err=True)
            return 2
    elif cmd == "hook-post":
        f = _hook_file()
        rel = ctx.rel(f) if f else ""
        if not rel:
            return 0
        text = R.route(ctx, rel)
        if R.node_of(ctx, rel):
            text += ("\n\n" if text else "") + R.next_docs(ctx, rel)
        if rel.lower().endswith((".md", ".toml", ".json", ".py", ".yml", ".yaml")):
            issues = K.run(ctx)
            if issues:
                text += "\n\nЗамечания проверок после правки:\n" + "\n".join(f"- {i}" for i in issues[:15])
                if len(issues) > 15:
                    text += f"\n- … ещё {len(issues) - 15}: {ctx.command} check"
        if text:
            _out(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}, ensure_ascii=False))
    elif cmd == "move":
        need(2, "move <старый> <новый>")
        ch = MV.move(ctx, args[0], args[1])
        _out(f"перенесено: {args[0]} → {args[1]}; ссылки обновлены в {len(ch)} файлах" + "".join(f"\n- {c}" for c in ch))
        _out(f"Проверь: {ctx.command} check")
    elif cmd == "diagram":
        need(1, "diagram <файл>")
        _out(M.diagram(ctx, args[0]).resolve().as_uri())
    elif cmd == "rnd":
        need(1, "rnd <slug|путь.md> <тема>")
        p = RD.rnd(ctx, args[0], " ".join(args[1:]) or args[0])
        _out(f"{ctx.show(p)} — заготовка R&D v3.2; строка в реестре: {ctx.get('rnd.index') or '—'}; "
             f"после заполнения — {ctx.command} rnd-check {ctx.show(p)}")
    elif cmd == "rnd-check":
        files = args or RD.rnd_files(ctx)
        bad = 0
        for f in files:
            p = Path(f) if Path(f).is_absolute() or Path(f).exists() else ctx.root / f
            iss = RD.rnd_check(p.read_text(encoding="utf-8"))
            bad += len(iss)
            _out(f"{f}: " + ("структура v3.2 в порядке" if not iss else f"{len(iss)}" + "".join(f"\n  - {i}" for i in iss)))
        if not files:
            _out("R&D не найдено")
        return 1 if bad else 0
    elif cmd == "blocks":
        _out(M.blocks_report(ctx))
    elif cmd == "review-pack":
        need(2, "review-pack <страница> <буква>")
        p = RV.pack(ctx, args[0], args[1])
        _out(f"{ctx.show(p)} — {len(p.read_text(encoding='utf-8')) // 4} ток. (примерно)")
    elif cmd in ("yaml-schema", "yaml-migrate"):
        from . import units as UN
        try:
            UN.need_yaml()
            if cmd == "yaml-schema":
                from . import schema as SC
                ch, notes = SC.write(ctx)
                _out(f"схемы: изменено {len(ch)}" + "".join(f"\n- {c}" for c in ch))
                for n in notes:
                    _out(f"⚠️ {n}")
            else:
                from . import migrate as MG
                path = next((a for a in args if not a.startswith("--")), None)
                ch, errs, left = MG.migrate(ctx, path, dry="--dry" in args)
                _out(f"data: {'будет переведено' if '--dry' in args else 'переведено'} файлов {len(ch)}"
                     + "".join(f"\n- {c}" for c in ch[:30]) + ("\n…" if len(ch) > 30 else ""))
                for e in errs:
                    _out(f"⚠️ {e}")
                _out(f"осталось текстом (разобрать вручную): {len(left)}" + "".join(f"\n- {x}" for x in left))
                return 1 if errs else 0
        except UN.NoYaml as e:
            _out(f"doc: {e.code}", err=True)
            return 1
    elif cmd in ("yaml-check", "yaml-fmt", "unit"):
        from . import units as UN
        try:
            if cmd == "yaml-check":
                iss = UN.check(ctx, args[0] if args else None)
                _out("\n".join(f"⚠️ {i}" for i in iss) or "yaml: замечаний нет")
                return 1 if iss else 0
            if cmd == "yaml-fmt":
                ch = UN.fmt(ctx, args[0] if args else None)
                _out(f"отформатировано: {len(ch)}" + "".join(f"\n- {c}" for c in ch))
            else:
                need(1, "unit <ID|путь>")
                _out(UN.card(ctx, " ".join(args)))
        except UN.NoYaml as e:
            _out(f"doc: {e.code}", err=True)
            return 1
    elif cmd == "review-batch":
        mx = int(args[args.index("--max") + 1]) if "--max" in args else 0
        rest = [a for i, a in enumerate(args) if not a.startswith("--") and not (i and args[i - 1] == "--max")]
        if not rest:
            raise SystemExit(f"использование: {ctx.command} review-batch <комп>[,<комп>…] [--max 40] [--force] (общие страницы — «-»)")
        comp = rest[0]
        lg = LN.run(ctx)
        names = [("—" if c.strip() in ("-", "—", "") else c.strip()) for c in comp.split(",")]
        dirty = [c for c in names if lg.get(c)]
        if dirty and "--force" not in args:  # механика — до ролей: роли не платят за то, что ловит lint (rules/approvals.md#economy)
            for c in dirty:
                _out(f"⛔ {c}: пробелов lint {len(lg[c])} — сначала закрыть ({ctx.command} lint {c}); --force — взять всё равно")
            comp = ",".join(c for c in comp.split(",") if ("—" if c.strip() in ("-", "—", "") else c.strip()) not in dirty)
            if not comp:
                return 1
        res = RV.batch(ctx, comp, mx)
        cpt = float(ctx.get("review.chars_per_token", 1.6) or 1.6)
        ovh = int(ctx.get("review.agent_overhead", 19500) or 0)
        for p, jf, n, k, c in res:
            tok = c / cpt
            _out(f"{ctx.show(p)}: страниц {n}, пар {k}, символов {c}, ≈{tok:.0f} ток. карточек, ≈{tok / max(n, 1):.0f} на страницу; "
                 f"с накладными агента ≈{(tok + ovh + 100 * n) / max(n, 1):.0f} на страницу; "
                 f"ответ — {ctx.show(jf)} → {ctx.command} approve-batch {ctx.show(jf)}")
        if not res:
            _out(f"{comp}: ⏳ нет")
        for cc in comp.split(","):
            cc = "—" if cc.strip() in ("-", "—", "") else cc.strip()
            if lg.get(cc):
                _out(f"⚠️ lint {cc}: пробелов {len(lg[cc])} (--force) — роли механику не повторяют: {ctx.command} lint {cc}")
    elif cmd == "approve-batch":
        need(1, "approve-batch <файл.json> [--human] [--who ИМЯ]")
        who = args[args.index("--who") + 1] if "--who" in args else ""
        f = Path(args[0]) if Path(args[0]).is_absolute() or Path(args[0]).exists() else ctx.root / args[0]
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"{args[0]}: не прочитан JSON-массив решений ({e})")
        if not isinstance(items, list):
            raise SystemExit(f"{args[0]}: нужен JSON-массив [{{\"page\", \"role\", \"ok\", \"comment\"}}]")
        guard = A.batch_guard(ctx, items)
        if guard:
            for g in guard:
                _out(f"⛔ {g}")
            _out("ничего не применено — реши каждую пару отдельно и напиши свой комментарий: что проверено (ID, поле, номер)")
            return 1
        done, errs = A.set_decisions(ctx, items, human="--human" in args, who=who)
        for x in done:
            _out(x)
        for e in errs:
            _out(f"⚠️ {e}")
        if errs:
            _out(f"не применено ничего: ошибок {len(errs)} — исправь файл и повтори")
            return 1
        _out(f"применено {len(done)}: ✅ {sum('✅' in d for d in done)}, ❌ {sum('❌' in d for d in done)}")
    elif cmd == "cost":
        from . import costs as CO
        text, rc = CO.main(ctx, args)  # без PyYAML для журнала YAML — ошибка «нужен PyYAML» (код 1)
        _out(text)
        return rc
    elif cmd == "review-queue":
        q = A.queue(ctx, args[0] if args else "")
        _out(f"ждёт решения: {len(q)}")
        for page, letter, was in q[:500]:
            _out(f"- {page} × {letter}" + (f" (было {was})" if was else ""))
    elif cmd == "approve":
        human = "--human" in args
        who = ""
        rest = [a for a in args if a != "--human"]
        if "--who" in rest:
            i = rest.index("--who")
            who = rest[i + 1]
            del rest[i:i + 2]
        if len(rest) < 3 or rest[2] not in ("ok", "no", "✅", "❌", "да", "нет"):
            raise SystemExit(f'использование: {ctx.command} approve <страница|ID требования> <буква> ok|no "<комментарий>" [--human]')
        _out(A.set_decision(ctx, rest[0], rest[1], rest[2] in ("ok", "✅", "да"), " ".join(rest[3:]), human=human, who=who))
    elif cmd == "finding":
        need(2, "finding <компонент> <ID[,ID]> ok|wait|owner|reject \"<что сделано>\"")
        if args[1] == "next-id":
            _out(RV.next_finding_id(ctx, args[0]))
        else:
            need(3, "finding <компонент> <ID[,ID]> ok|wait|owner|reject \"<что сделано>\"")
            _out(RV.finding(ctx, args[0], args[1], args[2], " ".join(args[3:])))
    elif cmd == "plugins":
        reg = ctx.plugins
        _out("загружены: " + (", ".join(reg.loaded) or "—"))
        _out("lint: " + (", ".join(reg.lint_rules) or "—"))
        _out("check: " + (", ".join(reg.check_rules) or "—"))
        _out("каталоги ID: " + (", ".join(reg.ref_kinds) or "—"))
        _out("карточки review-batch: " + (", ".join(reg.card_kinds) or "—"))
        _out("правила yaml-migrate: " + (", ".join(f"{k} ({len(v)})" for k, v in reg.migrations.items()) or "—"))
        _out("команды: " + (", ".join(f"{k} — {getattr(v, 'help', '')}" for k, v in reg.commands.items()) or "—"))
    elif ctx.plugins and cmd in ctx.plugins.commands:
        rc = ctx.plugins.commands[cmd](ctx, args)
        return int(rc or 0)
    else:
        _out(f"неизвестная команда: {cmd}\n", err=True)
        _out(USAGE)
        return 1
    return 0


def run() -> int:
    try:
        return main()
    except SystemExit as e:
        if isinstance(e.code, str):
            _out(f"doc: {e.code}", err=True)
            return 1
        return int(e.code or 0)
    except KeyboardInterrupt:
        return 130
