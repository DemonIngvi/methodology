"""Учёт расхода токенов по процессам методологии (rules/token-costs.md): журнал → сводка и генерируемая страница.

    cost                                   сводка: последний замер на единицу против ожидания (допуск ±2×)
    cost add <процесс> <токены> <единиц> <единица> [--method файл#якорь] [--expect a-b] [--model M] [--min N]
                                           [--note …] [--date ГГГГ-ММ-ДД]
    cost md                                перегенерировать страницу `[costs] page`

Журнал — `[costs] file` (YAML — нужен PyYAML; JSON — без зависимостей). Токены: 409000, 409k или сумма 84.9k+68.6k;
--expect: «2-4» (тыс.), «150k-250k», «2000-4000» — токены на единицу. Статус ✅ — замер на единицу в [мин / допуск,
макс × допуск] (допуск `[costs] tolerance`, по умолчанию 2), иначе ⚠️ «расхождение >2×»: остановиться и разобрать.
"""
import json
import re
from datetime import date
from pathlib import Path

from . import units as UN
from . import util as U


def _file(ctx) -> Path:
    f = ctx.get("costs.file", "")
    if not f:
        raise SystemExit("журнал расхода не задан: [costs] file = \"data/costs.yaml\" в конфиге (rules/token-costs.md)")
    return ctx.root / f


def _is_json(p: Path) -> bool:
    return p.suffix.lower() == ".json"


def load(ctx) -> list:
    p = _file(ctx)
    if not p.is_file():
        return []
    if _is_json(p):
        v = json.loads(U.read(p))
    else:
        v = UN.load(p)
    errs = []
    UN.validate_fields(ctx, v if isinstance(v, dict) else {}, UN.COSTS_KIND["fields"], "", errs, refs=True)
    errs += extra_errors(ctx, v)
    if errs:
        raise SystemExit(f"{ctx.show(p)}: " + "; ".join(errs[:5]) + f" ({ctx.command} yaml-check)")
    for r in v["costs"]:
        r["date"] = str(r["date"])
    return v["costs"]


def extra_errors(ctx, v) -> list:
    """Проверки записей журнала сверх схемы: методология с якорем существует, ожидание [мин, макс], единиц > 0."""
    out = []
    for n, r in enumerate((v or {}).get("costs", []) if isinstance(v, dict) else []):
        if not isinstance(r, dict):
            continue
        m, _, anchor = str(r.get("methodology", "")).partition("#")
        doc = ctx.rules_dir / m
        if m and not doc.is_file():
            out.append(f"costs[{n}].methodology: файла {ctx.show(doc)} нет")
        elif m and anchor and anchor not in U.anchors_of(U.read(doc)):
            out.append(f"costs[{n}].methodology: в {m} нет якоря #{anchor}")
        e = r.get("expected")
        if e is not None and (len(e) != 2 or e[0] > e[1]):
            out.append(f"costs[{n}].expected: [минимум, максимум] токенов на единицу")
        if isinstance(r.get("units"), int) and r["units"] <= 0:
            out.append(f"costs[{n}].units: больше нуля")
    return out


def save(ctx, rows: list):
    p = _file(ctx)
    if _is_json(p):
        U.write(p, json.dumps({"costs": rows}, ensure_ascii=False, indent=2) + "\n")
    else:
        UN.write_unit(ctx, ctx.rel(p), {"costs": rows}, UN.COSTS_KIND["fields"])


def per_unit(r) -> float:
    return r["tokens"] / r["units"]


def status(ctx, r) -> str:
    e = r.get("expected")
    if not e:
        return "—"
    k = float(ctx.get("costs.tolerance", 2) or 2)
    return "✅" if e[0] / k <= per_unit(r) <= e[1] * k else f"⚠️ расхождение >{k:g}×"


def fmt_n(n) -> str:
    n = float(n)
    return f"{n:.0f}" if n < 1000 else f"{n / 1000:.1f}".replace(".", ",") + " тыс."


def fmt_exp(e) -> str:
    return f"{fmt_n(e[0])} – {fmt_n(e[1])}" if e else "—"


def by_process(rows) -> dict:
    out = {}
    for r in sorted(rows, key=lambda r: r["date"]):  # внутри даты — порядок в журнале
        out.setdefault(r["process"], []).append(r)
    return out


def parse_tokens(s: str) -> int:
    total = 0.0
    for part in s.replace(",", ".").split("+"):
        m = re.fullmatch(r"\s*([\d.]+)\s*(k|к|тыс)?\s*", part)
        if not m:
            raise SystemExit(f"токены «{s}»: нужно число, 409k или сумма 84.9k+68.6k")
        total += float(m.group(1)) * (1000 if m.group(2) else 1)
    return round(total)


def parse_expect(s: str) -> list:
    m = re.fullmatch(r"\s*([\d.]+)\s*(k|к)?\s*[-–]\s*([\d.]+)\s*(k|к)?\s*", s.replace(",", "."))
    if not m:
        raise SystemExit(f"--expect «{s}»: нужно a-b, например 2-4 (тыс.) или 150k-250k")
    lo, hi = float(m.group(1)), float(m.group(3))
    k = 1000 if (m.group(2) or m.group(4) or hi < 1000) else 1
    return [round(lo * k), round(hi * k)]


OPTS = ("--model", "--min", "--expect", "--note", "--method", "--date")


def add(ctx, args) -> str:
    opts, pos, i = {}, [], 0
    while i < len(args):
        if args[i] in OPTS:
            j = i + 1
            while j < len(args) and args[j] not in OPTS:
                j += 1
            opts[args[i][2:]] = " ".join(args[i + 1:j])
            i = j
        else:
            pos.append(args[i])
            i += 1
    if len(pos) < 4:
        raise SystemExit(f"использование: {ctx.command} cost add <процесс> <токены> <единиц> <единица> "
                         "[--method файл#якорь] [--expect a-b] [--model M] [--min N] [--note …] [--date ГГГГ-ММ-ДД]")
    try:
        units = int(pos[2])
    except ValueError:
        raise SystemExit(f"единиц «{pos[2]}»: нужно целое число")
    proc, tokens, unit = pos[0], parse_tokens(pos[1]), " ".join(pos[3:])
    rows = load(ctx)
    prev = [r for r in rows if r["process"] == proc]
    method = opts.get("method") or (prev[-1]["methodology"] if prev else "")
    if not method:
        raise SystemExit(f"новый процесс {proc}: укажи --method файл#якорь (методология в {ctx.show(ctx.rules_dir)})")
    rec = {"date": opts.get("date") or date.today().isoformat(), "process": proc, "methodology": method,
           "units": units, "unit": unit, "tokens": tokens}
    if "model" in opts:
        rec["model"] = opts["model"]
    if "min" in opts:
        rec["minutes"] = int(opts["min"])
    if "expect" in opts:
        rec["expected"] = parse_expect(opts["expect"])
    elif prev and prev[-1].get("expected"):
        rec["expected"] = prev[-1]["expected"]
    if "note" in opts:
        rec["note"] = opts["note"]
    errs = []
    UN.validate_fields(ctx, {"costs": [rec]}, UN.COSTS_KIND["fields"], "", errs)
    errs += extra_errors(ctx, {"costs": [rec]})
    if errs:
        raise SystemExit("; ".join(errs))
    save(ctx, rows + [rec])
    page = write_md(ctx) if ctx.get("costs.page") else None
    st = status(ctx, rec)
    return (f"{proc}: {fmt_n(tokens)} / {units} {unit} = {fmt_n(tokens / units)} на единицу; ожидание {fmt_exp(rec.get('expected'))} — {st}"
            + (" → остановиться и разобрать: поправить ожидание в журнале или процесс (rules/token-costs.md#measure)" if st.startswith("⚠️") else "")
            + (f"\n{ctx.get('costs.page')}: обновлено" if page else ""))


def summary(ctx) -> str:
    procs = by_process(load(ctx))
    L = ["| процесс | единица | ожидание на единицу | последний замер на единицу | статус | дата |", "|---|---|---|---|---|---|"]
    for p, rs in procs.items():
        r = rs[-1]
        L.append(f"| {p} | {r['unit']} | {fmt_exp(r.get('expected'))} | {fmt_n(per_unit(r))} | {status(ctx, r)} | {r['date']} |")
    bad = [p for p, rs in procs.items() if status(ctx, rs[-1]).startswith("⚠️")]
    return "\n".join(L) + ("\n\n⚠️ вне допуска: " + ", ".join(bad) + " — остановиться и разобрать" if bad else
                           "\n\nвсе замеры в допуске" if procs else "\n\nзамеров нет")


def _link(ctx, page: Path, target: Path) -> str:
    import os
    return Path(os.path.relpath(target, page.parent)).as_posix()


def md(ctx) -> str:
    page = ctx.root / ctx.get("costs.page")
    titles = ctx.get("costs.titles", {}) or {}
    rules = ctx.rules_dir / "token-costs.md"
    rows = load(ctx)
    procs = by_process(rows)
    cmd = ctx.command

    def meth(m):
        f, _, a = m.partition("#")
        return f"[{m}]({_link(ctx, page, ctx.rules_dir / f)}{'#' + a if a else ''})"
    L = ["# Расход токенов", "",
         f"← [как замерять]({_link(ctx, page, rules)}#measure)", "",
         f"Сколько токенов уходит на единицу работы по процессам: ожидание против замера. Страница **генерируется** из "
         f"`{ctx.get('costs.file')}` (`{cmd} cost md`); новая запись — `{cmd} cost add …`, сводка — `{cmd} cost`. "
         f"Допуск ±{float(ctx.get('costs.tolerance', 2) or 2):g}×: вне его — ⚠️, остановиться и разобрать.", "",
         "## Сводка", "",
         "| Процесс | Методология | Единица | Ожидание на единицу | Последний замер на единицу | Статус | Дата |",
         "|---|---|---|---|---|---|---|"]
    for p, rs in procs.items():
        r = rs[-1]
        L.append(f"| [{titles.get(p, p)}](#{p}) | {meth(r['methodology'])} | {r['unit']} | {fmt_exp(r.get('expected'))} | "
                 f"{fmt_n(per_unit(r))} | {status(ctx, r)} | {r['date']} |")
    L += ["", "## Замеры по процессам", ""]
    for p, rs in procs.items():
        L += [f'<a id="{p}"></a>', "", f"### {titles.get(p, p)}", "", f"Методология: {meth(rs[-1]['methodology'])}.", "",
              "| Дата | Единиц | Единица | Токенов | На единицу | Модель | Минут | Статус | Примечание |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in rs:
            L.append(f"| {r['date']} | {r['units']} | {r['unit']} | {fmt_n(r['tokens'])} | {fmt_n(per_unit(r))} | "
                     f"{r.get('model', '—')} | {r.get('minutes', '—')} | {status(ctx, r)} | {str(r.get('note', '')).replace('|', '/')} |")
        L.append("")
    return "\n".join(L)


def write_md(ctx) -> bool:
    p = ctx.root / ctx.get("costs.page")
    text = md(ctx)
    if p.is_file() and U.read(p) == text:
        return False
    U.write(p, text)
    return True


def check(ctx) -> list:
    """Для `check`: журнал по схеме (YAML — его проверяет yaml-check), страница не отстала от журнала."""
    f = ctx.get("costs.file", "")
    if not f or not (ctx.root / f).is_file():
        return []
    p = ctx.root / f
    if not _is_json(p) and UN.yaml_mod() is None:
        return []
    out = []
    if _is_json(p):
        try:
            load(ctx)
        except SystemExit as e:
            return [f"расход: {e.code}"]
    else:
        try:
            v = UN.load(p)
        except Exception:
            return []  # ошибку разбора покажет yaml-check
        errs = extra_errors(ctx, v)
        out += [f"расход: {f}: {e}" for e in errs]
        if errs or UN.check_unit(ctx, f, UN.COSTS_KIND)[1]:
            return out
    pg = ctx.get("costs.page", "")
    if pg and (not (ctx.root / pg).is_file() or U.read(ctx.root / pg) != md(ctx)):
        out.append(f"расход: {pg} отстаёт от {f} → {ctx.command} cost md")
    return out


def main(ctx, args) -> tuple:
    """(текст, код возврата)."""
    if not args:
        return summary(ctx), 0
    if args[0] == "add":
        return add(ctx, args[1:]), 0
    if args[0] == "md":
        if not ctx.get("costs.page"):
            raise SystemExit("страница не задана: [costs] page = \"token-costs.md\"")
        return f"{ctx.get('costs.page')}: " + ("обновлено" if write_md(ctx) else "без изменений"), 0
    raise SystemExit(f"использование: {ctx.command} cost [add …|md]")
