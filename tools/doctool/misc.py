"""diagram, rnd, blocks, staged, init."""
import html as H
import os
import re
import subprocess
from datetime import date
from pathlib import Path

from . import util as U

# ---------- diagram ----------


def diagram(ctx, path: str) -> Path:
    src = Path(path) if Path(path).is_absolute() else ctx.root / path
    if not src.is_file():
        src = Path(path).resolve()
    text = U.read(src).replace("\r\n", "\n")
    blocks = re.findall(r"^\s*```mermaid[^\n]*\n(.*?)^\s*```", text, flags=re.S | re.M)
    if not blocks:
        raise SystemExit(f"{path}: нет блоков ```mermaid")
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), src.stem)
    body = "\n".join(f'<pre class="mermaid">{H.escape(b)}</pre>' for b in blocks)
    page = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{H.escape(title)}</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body{font-family:system-ui,sans-serif;margin:24px;background:#fff;color:#212121}pre.mermaid{margin:32px 0}'
            '@media (prefers-color-scheme: dark){body{background:#121212;color:#eee}}</style></head>'
            f'<body><h1>{H.escape(title)}</h1>{body}'
            f'<script type="module">import mermaid from "{ctx.get("diagram.mermaid")}";'
            'mermaid.initialize({startOnLoad:true,securityLevel:"strict"});</script></body></html>')
    rel = ctx.rel(src) or src.name
    out = ctx.work_dir / "diagrams" / (rel.replace("/", "__").rsplit(".", 1)[0] + ".html")
    U.write(out, page)
    return out


# ---------- blocks ----------

def blocks_report(ctx) -> str:
    bl = ctx.blocks()
    rows = [(b, *ctx.block_card(b)) for b in bl]
    full = sum(1 for r in rows if not r[2])
    L = [f"блоков {len(rows)}, карточка заполнена полностью у {full}"]
    for b, ok, miss in rows:
        if miss:
            L.append(f"- {b.component or '—'} / {b.title} ({b.file}): {len(ok)}/{len(ok) + len(miss)}, нет: {', '.join(miss)}")
    return "\n".join(L)


# ---------- staged ----------

def staged_files(ctx) -> list:
    try:
        r = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"], cwd=ctx.root, capture_output=True)
    except OSError:
        return []
    if r.returncode:
        return []
    prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=ctx.root, capture_output=True, text=True).stdout.strip()
    out = []
    for x in r.stdout.decode("utf-8", "replace").split("\0"):
        if x:
            out.append(x[len(prefix):] if prefix and x.startswith(prefix) else x)
    return out


def staged_generated(ctx) -> list:
    out = []
    for f in staged_files(ctx):
        g = ctx.generated_of(f)
        if g and not g.get("commit", False):
            out.append(f"{f}: генерируемый файл изменён — правь источник ({g.get('source', '?')}) и перегенерируй ({g.get('by', '?')})")
    return out


# ---------- init ----------

TEMPLATE = Path(__file__).with_name("doc.config.template.toml")


def init(root: Path, as_json: bool = False, force: bool = False) -> Path:
    target = root / ("doc.config.json" if as_json else "doc.config.toml")
    if target.exists() and not force:
        raise SystemExit(f"{target.name} уже есть (--force — перезаписать)")
    text = U.read(TEMPLATE)
    if as_json:
        import json
        from .config import load_data
        tmp = root / ".doc.config.tmp.toml"
        U.write(tmp, text)
        try:
            data = load_data(tmp)
        finally:
            tmp.unlink()
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    U.write(target, text)
    return target
