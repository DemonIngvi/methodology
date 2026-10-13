"""rnd (заготовка R&D v3.2 по шаблону) и rnd-check (структура R&D v3.2), rules/rnd.md.

rnd-check: у каждого этапа Э1–Э10 — раздел «## Эn.», строка «**Вывод этапа.**» и ворота Gn ✅ или ⚠️
(у ⚠️ — 📌 и следующий шаг, rules/rnd.md#gate-status); раздел «## Источники»: у каждой строки ссылка, дата
(или одна дата на раздел: «обращение ГГГГ-ММ-ДД») и уровень доказательства А–Г.
"""
import os
import re
from datetime import date
from pathlib import Path

from . import util as U

STAGES = ["Вопрос", "Потребности", "Опорное решение: как сейчас", "Аналоги и их ключевые функции", "Ценность функций",
          "Перенос и критерии", "Отсев и встраивание", "Спор ролей", "Выбор", "Последствия"]

FALLBACK = """# R&D: {title}

← [все R&D](README.md)

**Вопрос.** …

**Ответ.** …

{stages}## Источники

- …
"""


def _fallback(title: str) -> str:
    st = ""
    for n, name in enumerate(STAGES, 1):
        body = "- **Утверждение.** …\n- **Когда неверно.** …\n" if n == 9 else \
            "| Изменение | Документ | Статус |\n|---|---|---|\n| … | … | 📌 предложено: следующий шаг … |\n" if n == 10 else "…\n"
        st += f'<a id="e{n}"></a>\n\n## Э{n}. {name}\n\n{body}\n**Вывод этапа.** …\n\nG{n} ✅ …\n\n'
    return FALLBACK.format(title=title, stages=st)


def _rel(target: Path, base: Path) -> str:
    return Path(os.path.relpath(target, base)).as_posix()


def rnd(ctx, slug: str, title: str) -> Path:
    """Заготовка R&D: шаблон [rnd] template (templates/rnd.md v3.2), ссылки — относительно нового файла;
    строка в реестре R&D ([rnd] index). slug — имя в папке [rnd] dir или путь к .md."""
    if slug.endswith(".md"):
        p = Path(slug) if Path(slug).is_absolute() else ctx.root / slug
    else:
        if not re.fullmatch(r"[\w.\-]+", slug):
            raise SystemExit("slug — латиница, цифры, «-», «_», «.»")
        p = ctx.root / ctx.get("rnd.dir", "rnd") / f"{slug}.md"
    if p.exists():
        raise SystemExit(f"{ctx.show(p)} уже есть")
    d = p.parent
    tpl = ctx.root / ctx.get("rnd.template") if ctx.get("rnd.template") else None
    idx = ctx.root / ctx.get("rnd.index") if ctx.get("rnd.index") else None
    if tpl and tpl.is_file():
        text = re.sub(r"<!--.*?-->\n*", "", U.read(tpl), flags=re.S)
        text = text.replace("<тема>", title).replace("<ГГГГ-ММ-ДД>", date.today().isoformat())
        meth = ctx.rules_dir / "rnd.md"
        # ссылка шаблона на методологию: (../rules/rnd.md…) → путь к rules_dir от нового файла
        if meth.is_file():
            text = re.sub(r"\((?:\.\./)*rules/rnd\.md(#[^)]*)?\)", lambda m: f"({_rel(meth, d)}{m.group(1) or ''})", text)
        if idx:  # «все R&D» → реестр R&D проекта
            text = re.sub(r"\(<[^>]*README\.md#rnd>\)", f"({_rel(idx, d)})", text)
    else:
        text = _fallback(title)
        if idx:
            text = text.replace("(README.md)", f"({_rel(idx, d)})")
    U.write(p, text)
    if idx:
        row = ctx.get("rnd.index_row").format(title=title, file=_rel(p, idx.parent), slug=p.stem)
        if idx.is_file():
            U.write(idx, U.read(idx).rstrip("\n") + "\n" + row + "\n")
        else:
            U.write(idx, f"# R&D\n\n| Тема | Вопрос | Статус | Версия |\n|---|---|---|---|\n{row}\n")
    return p


def rnd_check(text: str) -> list:
    issues = []
    for n in range(1, 11):
        m = re.search(rf"^## Э{n}\.(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
        if not m:
            issues.append(f"нет раздела «## Э{n}.»")
            continue
        sec = m.group(0)
        if "**Вывод этапа.**" not in sec:
            issues.append(f"Э{n}: нет строки «**Вывод этапа.**»")
        g = re.search(rf'gate">G{n}</span>\s*(✅|⚠️)([^\n]*)', sec) or re.search(rf"\bG{n}\s*(✅|⚠️)([^\n]*)", sec)
        if not g:
            issues.append(f"Э{n}: нет ворот G{n} со статусом ✅ или ⚠️")
        elif g.group(1) == "⚠️" and "📌" not in g.group(2):
            issues.append(f"Э{n}: G{n} ⚠️ без 📌 владельцу и следующего шага (rules/rnd.md#gate-status)")
    src = re.search(r"^## Источники\s*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not src:
        issues.append("нет раздела «## Источники»")
        return issues
    body = re.sub(r"<!--.*?-->", "", src.group(1), flags=re.S)
    items = [l for l in body.splitlines() if l.startswith(("- ", "* ")) or re.match(r"^\d+\. ", l)]
    common = any(re.search(r"\d{4}-\d{2}-\d{2}", l) for l in body.splitlines() if l not in items)
    if not items:
        issues.append("в «Источниках» нет ни одной строки")
    for line in items:  # дата — у строки или одна на раздел («обращение 2026-10-13»)
        need = [("ссылка", r"\]\(|https?://"), ("уровень", r"(^|[\s(,;])[АБВГ]([\s),;.]|$)")]
        if not common:
            need.insert(1, ("дата", r"\d{4}-\d{2}(-\d{2})?"))
        miss = [k for k, rx in need if not re.search(rx, line)]
        if miss:
            issues.append(f"источник без: {', '.join(miss)} — {line[:60]}")
    return issues


def rnd_files(ctx) -> list:
    """R&D проекта для check: [rnd] check_glob (по умолчанию <dir>/*.md), кроме реестра."""
    pats = U.as_list(ctx.get("rnd.check_glob")) or [f"{ctx.get('rnd.dir', 'rnd')}/*.md"]
    idx = ctx.get("rnd.index", "")
    return [f for f in ctx.glob(pats) if f != idx and Path(f).name.lower() not in ("readme.md", "index.md")]


def check(ctx) -> list:
    if not ctx.get("rnd.check", True):
        return []
    return [f"R&D: {f}: {i}" for f in rnd_files(ctx) for i in rnd_check(ctx.text(f))]
