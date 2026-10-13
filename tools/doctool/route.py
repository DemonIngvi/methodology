"""route / generated / next: правило в момент правки и следующий документ по графу связности."""
import re
from pathlib import Path

from . import util as U


def meth_show(ctx, meth: str) -> str:
    if not meth:
        return ""
    f = ctx.rules_dir / meth.split("#")[0]
    anchor = "#" + meth.split("#", 1)[1] if "#" in meth else ""
    return ctx.show(f) + anchor


def route(ctx, rel: str) -> str:
    if not rel:
        return ""
    from . import units as UN
    t = ctx.type_of(rel)
    gen = ctx.generated_of(rel)
    data = UN.route_lines(ctx, rel)
    if not t and not gen and not data:
        return ""
    cmd = ctx.command
    L = [f"Правила для `{rel}` ({cmd} route):"]
    if gen:
        L.append(f"- ГЕНЕРИРУЕТСЯ ({gen.get('by', '?')}): правь {gen.get('source', '?')}, затем перегенерируй.")
    L += data
    if t:
        L.append(f"- Тип: {t.name}." + (f" Обязательно: {t.required}." if t.required else ""))
        if t.rules and t.rules != "—":
            L.append(f"- Правила: {t.rules}.")
        if t.source:
            L.append(f"- Источник факта: {t.source}.")
        if t.methodology:
            L.append(f"- Методология: {meth_show(ctx, t.methodology)} — прочитай «Из чего состоит», «Как вести», «Чек-лист».")
        if t.sync and t.sync != "—":
            L.append(f"- Синхронизировать: {t.sync}.")
        if t.sections:
            L.append("- Разделы (проверяет check): " + ", ".join(s.lstrip("^#").strip() for s in t.sections) + ".")
        if t.approve:
            L.append("- Подтверждают: " + ", ".join(f"{x} ({ctx.role_name(x)})" for x in t.approve) + ". Правка текста сбрасывает решения в ⏳.")
    L.append("- Перед правкой прочитай методологию; если она отстала от того, что меняешь, — сначала поправь её (и инструменты), потом документ.")
    L.append(f"- После правки: {cmd} check; следующий документ — {cmd} next {rel}")
    return "\n".join(L)


def generated_line(ctx, rel: str) -> str:
    g = ctx.generated_of(rel)
    return f"генерируется: {g.get('by', '?')}; править: {g.get('source', '?')}" if g else ""


# ---------- граф связности ----------

def nodes(ctx) -> dict:
    return {k: U.as_list(v) for k, v in (ctx.get("graph.nodes", {}) or {}).items()}


def edges(ctx) -> list:
    out = []
    for e in ctx.get("graph.edges", []) or []:
        if isinstance(e, dict):
            out.append((e["from"], e["to"], e.get("check", "")))
        else:
            out.append((e[0], e[1], e[2] if len(e) > 2 else ""))
    return out


def node_of(ctx, rel: str):
    ns = nodes(ctx)
    best, score = None, None
    for name, pats in ns.items():
        for p in pats:
            if U.match(rel, p):
                s = U.specificity(p)
                if score is None or s < score:
                    best, score = name, s
    if best:
        return best
    gen = ctx.generated_of(rel)
    if gen:  # генерируемый файл — узел его источника
        for name, pats in ns.items():
            if any(U.match(s.strip(), pats) for s in re.split(r"[,;]", gen.get("source", ""))):
                return name
    if rel and ctx.rel(ctx.rules_dir / Path(rel).name) == rel:
        return "Методология" if "Методология" in ns else None
    return None


def next_docs(ctx, rel: str) -> str:
    from . import units as UN
    node = node_of(ctx, rel)
    cmd = ctx.command
    regen = UN.regen_line(ctx, rel)
    try:
        in_rules = (ctx.root / rel).resolve().parent == ctx.rules_dir
    except OSError:
        in_rules = False
    if not node and not in_rules:
        if regen:
            return regen + f"\nУзла в графе нет ([graph.nodes]): дальше — документы из «перегенерируется» и {cmd} lint."
        return f"{rel}: нет узла в графе связности (конфиг [graph.nodes]) — проверь колонку «Синхронизировать» типа: {ctx.command} route {rel}"
    ns = nodes(ctx)
    L = ([regen] if regen else []) + [f"Узел `{rel}`: {node or 'методология'}. Дальше — по одному документу, в этом порядке:"]
    for a, b, what in edges(ctx):
        if a != node:
            continue
        files = ctx.glob(ns.get(b, []))
        meth = ""
        t = ctx.type_of(files[0]) if files else None
        if t and t.methodology:
            meth = f" · методология: {meth_show(ctx, t.methodology)}"
        L.append(f"- {b} — проверить: {what or '—'} · файлы: {', '.join(ns.get(b, [])) or '—'}{meth}")
    # изменилась методология → документы, которые ведутся по ней
    if in_rules:
        name = Path(rel).name
        for t in ctx.types():
            if t.methodology.split("#")[0] == name:
                L.append(f"- затем документы типа «{t.name}»: {', '.join(t.globs) or '—'} — привести к новой методологии")
    if not L[-1].startswith("- "):
        L.append("- исходящих связей нет: цепочка на этом узле заканчивается")
    L.append(f"Для каждого: прочитай методологию → устарела — сначала поправь её → правка → {cmd} check → {cmd} next <файл>. "
             "Документ не изменился по смыслу — дальше по его стрелкам не идёшь.")
    return "\n".join(L)
