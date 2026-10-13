"""Контекст проекта: файлы, типы документов, роли, компоненты, требования, задачи, карточки блоков, каталоги ID.

Всё проектное берётся из конфига; правила (колонки таблиц методологии) читаются из rules_dir при каждом вызове.
Плагины получают этот же объект (`ctx`) и пользуются теми же функциями.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
from . import util as U


def section_filled(body: str) -> bool:
    """Раздел заполнен: в таблице есть строка данных не из заглушек, или есть текст без таблицы."""
    rows = [l for l in body.splitlines() if l.strip().startswith("|")]
    if rows:
        data = [r for r in rows[1:] if not re.match(r"^\s*\|?\s*:?-{2,}", r)]
        return any(not U.is_empty(" ".join(U.split_row(r))) for r in data)
    return not U.is_empty(re.sub(r"<!--.*?-->", "", body, flags=re.S))


@dataclass
class DocType:
    name: str
    globs: list
    exclude: list = field(default_factory=list)
    methodology: str = ""
    required: str = ""
    rules: str = ""
    sync: str = ""
    source: str = ""
    approve: str = ""
    sections: list = field(default_factory=list)
    recommended: list = field(default_factory=list)
    checklist: str = ""
    card: str = ""
    own_reqs: str = ""


@dataclass
class Requirement:
    id: str
    title: str
    file: str
    line: int
    block: str
    tasks: set
    task_line: str
    scenarios: list
    anchor: str


@dataclass
class Task:
    id: str
    file: str
    text: str
    reqs: set
    closed: bool
    component: str
    subtasks: list   # [(номер, часы|None, «готово, когда»)]
    total: object    # часы из строки «Итого» или None


@dataclass
class Block:
    component: str
    title: str
    file: str
    fields: dict
    kind: str
    sections: dict


class Ctx:
    def __init__(self, root=None, cfg=None, cfg_path=None):
        self.root = Path(root or C.find_root()).resolve()
        if cfg is None:
            cfg, cfg_path = C.load(self.root)
        self.cfg = cfg
        self.cfg_path = cfg_path
        self._files = None
        self._text = {}
        self._cache = {}
        self.plugins = None

    # ---------- конфиг ----------
    def get(self, dotted: str, default=None):
        cur = self.cfg
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def command(self) -> str:
        return self.get("project.command", "tools/doc")

    @property
    def rules_dir(self) -> Path:
        return (self.root / self.get("project.rules_dir", "rules")).resolve()

    @property
    def work_dir(self) -> Path:
        return self.root / self.get("project.work_dir", ".doctool")

    def rules_file(self, name: str):
        p = self.rules_dir / name.split("#")[0]
        return p if p.is_file() else None

    def show(self, p: Path) -> str:
        """Путь для вывода: от корня, если внутри, иначе как есть."""
        r = self.rel(p)
        return r or Path(os.path.relpath(p, self.root)).as_posix()

    # ---------- файлы ----------
    def rel(self, path) -> str:
        if not path:
            return ""
        p = Path(path)
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
        try:
            return Path(os.path.normpath(p)).relative_to(self.root).as_posix()
        except ValueError:
            try:
                return p.resolve().relative_to(self.root).as_posix()
            except (ValueError, OSError):
                return ""

    def path(self, rel: str) -> Path:
        return self.root / rel

    def files(self) -> list:
        if self._files is None:
            skip = set(self.get("paths.exclude", []))
            out = []
            for d, dirs, names in os.walk(self.root):
                dirs[:] = sorted(x for x in dirs if x not in skip)
                base = Path(d).relative_to(self.root).as_posix()
                for n in sorted(names):
                    r = n if base == "." else f"{base}/{n}"
                    if not U.match(r, [s for s in skip if "/" in s or "*" in s]):
                        out.append(r)
            self._files = out
        return self._files

    def glob(self, patterns) -> list:
        pats = U.as_list(patterns)
        return [f for f in self.files() if U.match(f, pats)]

    def md_files(self) -> list:
        return [f for f in self.files() if f.lower().endswith(".md")]

    def text(self, rel: str) -> str:
        if rel not in self._text:
            self._text[rel] = U.read(self.root / rel)
        return self._text[rel]

    def invalidate(self):
        self._files = None
        self._text = {}
        self._cache = {}

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # ---------- типы документов ----------
    def types_table(self) -> list:
        f = self.rules_file(self.get("types_table.file", "doc-types.md"))
        return U.table_rows(U.read(f), self.get("types_table.header", "| Тип |")) if f else []

    def table_row(self, name: str):
        key = U.plain(name).lower()
        return next((r for r in self.types_table() if U.plain(U.col(r, "Тип")).lower() == key), None)

    def types(self) -> list:
        return self.cached("types", self._types)

    def _types(self) -> list:
        out = []
        conf = self.get("types", []) or []
        if not conf:  # пути — из колонки «Где» таблицы правил
            for r in self.types_table():
                globs = [re.sub(r"<[^>]+>", "*", p).strip() for p in re.findall(r"`([^`]+)`", U.col(r, "Где"))]
                globs = [g + "**" if g.endswith("/") else g for g in globs]  # папка → все файлы в ней
                if globs:
                    conf.append({"name": U.plain(U.col(r, "Тип")), "glob": globs})
        for t in conf:
            row = self.table_row(t.get("table_name", t["name"])) or {}
            meth = t.get("methodology") or ""
            if not meth:
                m = re.search(r"\]\(([^)]+)\)", U.col(row, "Методология"))
                meth = m.group(1) if m else ""
            out.append(DocType(
                name=t["name"], globs=U.as_list(t.get("glob")), exclude=U.as_list(t.get("exclude")), methodology=meth,
                required=t.get("required") or U.plain(U.col(row, "Обязательн")),
                rules=t.get("rules") or U.plain(U.col(row, "Правила")),
                sync=t.get("sync") or U.plain(U.col(row, "Синхрониз")),
                source=t.get("source", ""),
                approve=self._approve_letters(t),
                sections=U.as_list(t.get("sections")), recommended=U.as_list(t.get("recommended")),
                checklist=t.get("checklist", ""), card=t.get("card", ""), own_reqs=t.get("own_reqs", "")))
        return out

    def _approve_letters(self, t: dict) -> str:
        if "approve" in t:
            v = t["approve"]
            return "".join(U.as_list(v)) if isinstance(v, list) else str(v or "")
        f = self.rules_file(self.get("roles_table.file", "approvals.md"))
        if not f:
            return ""
        name = U.plain(t.get("approve_as") or t.get("table_name") or t["name"]).lower()
        for r in U.table_rows(U.read(f), self.get("roles_table.types_header", "| Тип документа |")):
            if U.plain(U.col(r, "Тип")).lower() == name:
                letters = set(self.role_letters())
                return "".join(x for x in re.findall(r"\w", U.plain(U.col(r, "Роли"))) if x in letters)
        return ""

    def type_of(self, rel: str):
        best, score = None, None
        for t in self.types():
            if t.exclude and U.match(rel, t.exclude):
                continue
            for g in t.globs:
                if U.match(rel, g):
                    s = U.specificity(g)
                    if score is None or s < score:
                        best, score = t, s
        return best

    def generated_of(self, rel: str):
        for g in self.get("generated", []) or []:
            if U.match(rel, g.get("glob", [])):
                return g
        return None

    # ---------- роли ----------
    def roles(self) -> list:
        """[(буква, роль, вопрос)]."""
        return self.cached("roles", self._roles)

    def _roles(self) -> list:
        conf = self.get("roles", []) or []
        table = []
        f = self.rules_file(self.get("roles_table.file", "approvals.md"))
        if f:
            for r in U.table_rows(U.read(f), self.get("roles_table.header", "| Буква |")):
                table.append((U.plain(U.col(r, "Буква")), U.plain(U.col(r, "Роль")), U.plain(U.col(r, "Вопрос"))))
        if not conf:
            return table
        q = {x[0]: x[2] for x in table}
        return [(r["letter"], r["name"], r.get("question") or q.get(r["letter"], "")) for r in conf]

    def role_letters(self) -> list:
        return [r[0] for r in self.roles()]

    def role_letter(self, x: str) -> str:
        for letter, name, _ in self.roles():
            if x == letter or x.lower() == name.lower():
                return letter
        return x

    def role_name(self, letter: str) -> str:
        return next((n for l, n, _ in self.roles() if l == letter), letter)

    # ---------- компоненты ----------
    def components(self) -> list:
        """[{slug, code, name}] — из списка в конфиге или из файла данных (JSON / TOML / YAML)."""
        return self.cached("components", self._components)

    def _components(self) -> list:
        items = list(self.get("components.list", []) or [])
        src = self.get("components.source", "")
        if src:
            data = C.load_data(self.root / src)
            for k in [x for x in self.get("components.key", "").split(".") if x]:
                data = data[k]
            if isinstance(data, dict):
                data = [dict({"slug": k}, **(v if isinstance(v, dict) else {"name": v})) for k, v in data.items()]
            items += data
        out = []
        for c in items:
            c = {"slug": c} if isinstance(c, str) else dict(c)
            c.setdefault("slug", c.get("id") or c.get("name"))
            c.setdefault("code", str(c["slug"]).upper().replace("-", ""))
            c.setdefault("name", c["slug"])
            out.append(c)
        return out

    def component(self, slug: str):
        return next((c for c in self.components() if c["slug"] == slug), None)

    def component_of(self, rel: str) -> str:
        for rx in self.get("components.from_path", []) or []:
            m = re.search(rx, rel)
            if m:
                return m.groupdict().get("c") or m.group(1)
        slugs = {c["slug"] for c in self.components()}
        for part in reversed(Path(rel).with_suffix("").parts):
            if part in slugs:
                return part
        return ""

    # ---------- требования ----------
    def requirements(self) -> dict:
        return self.cached("reqs", self._requirements)

    def spec_files(self) -> list:
        return self.glob(self.get("requirements.specs", []))

    def _requirements(self) -> dict:
        idrx = re.compile(self.get("requirements.id"))
        head = re.compile(self.get("requirements.heading"))
        scen = re.compile(self.get("requirements.scenario"))
        tid = re.compile(self.get("tasks.id"))
        field_ = self.get("requirements.task_field", "")
        out, self._dup_reqs = {}, []
        for f in self.spec_files():
            lines = self.text(f).splitlines()
            starts = [i for i, l in enumerate(lines) if head.match(l)]
            for n, s in enumerate(starts):
                end = starts[n + 1] if n + 1 < len(starts) else len(lines)
                for j in range(s + 1, end):
                    if re.match(r"^#{1,3}\s", lines[j]) or re.match(r"^<a id=", lines[j]):
                        end = j
                        break
                block = "\n".join(lines[s:end])
                m = idrx.search(block)
                rid = m.group(0) if m else f"?{f}:{s + 1}"
                tl = next((l for l in lines[s:end] if field_ and field_ in l), "")
                tasks = set(tid.findall(tl.split(field_, 1)[1])) if tl else set()
                anchor = ""
                for j in range(s - 1, max(-1, s - 4), -1):
                    a = re.search(r'<a\s+(?:id|name)="([^"]+)"', lines[j])
                    if a:
                        anchor = a.group(1)
                        break
                    if lines[j].strip():
                        break
                r = Requirement(rid, head.match(lines[s]).group(1), f, s + 1, block, tasks, tl,
                                [x.group(1) for x in (scen.match(l) for l in lines[s:end]) if x], anchor)
                if rid in out:
                    self._dup_reqs.append(rid)
                out[rid] = r
        return out

    def requirement_ids_in(self, text: str) -> list:
        rx = re.compile(self.get("requirements.id"))
        return list(dict.fromkeys(rx.findall(text)))

    # ---------- задачи ----------
    def tasks(self) -> dict:
        return self.cached("tasks", self._tasks)

    def task_files(self) -> list:
        return self.glob(self.get("tasks.glob", []))

    def _tasks(self) -> dict:
        out = {}
        rx_name = re.compile(self.get("tasks.id_from_name"))
        st = self.get("tasks.subtasks", {})
        for f in self.task_files():
            m = rx_name.search(Path(f).name)
            if not m:
                continue
            tid, text = m.group(1), self.text(f)
            sec = U.section(text, self.get("tasks.requirements_section", "")) if self.get("tasks.requirements_section") else ""
            if not sec:
                sec = text.split("\n## ", 2)[0] if "\n## " in text else text
            closed = any(c in text for c in U.as_list(self.get("tasks.closed")))
            comp = ""
            colname = self.get("tasks.component_column", "")
            if colname:
                for r in U.table_rows(text, ""):
                    v = U.plain(r.get(colname, ""))
                    if v:
                        comp = v
                        break
            comp = self._component_slug(comp) or self.component_of(f)
            subs, total = [], None
            body = U.section(text, st.get("section", "")) if st.get("section") else text
            hc, dc = int(st.get("hours_col", 3)), int(st.get("done_col", 4))
            for l in body.splitlines():
                if not l.strip().startswith("|"):
                    continue
                cells = U.split_row(l)
                if not cells:
                    continue
                num = U.plain(cells[0])
                if re.fullmatch(re.escape(tid) + r"\.\d+", num):
                    subs.append((num, U.first_number(cells[hc - 1]) if len(cells) >= hc else None,
                                 cells[dc - 1] if len(cells) >= dc else ""))
                elif any(st.get("total_label", "Итого") in U.plain(c) for c in cells[:2]):
                    total = U.first_number(cells[hc - 1]) if len(cells) >= hc else None
            out[tid] = Task(tid, f, text, set(self.requirement_ids_in(sec)), closed, comp, subs, total)
        return out

    def _component_slug(self, v: str) -> str:
        v = U.plain(v).strip().lower()
        for c in self.components():
            if v and v in (str(c["slug"]).lower(), str(c["name"]).lower()):
                return c["slug"]
        return ""

    # ---------- каталоги ID (ссылки карточек, «не выдумывать») ----------
    def ids(self, kind: str):
        """(regex ссылки, множество существующих ID) или None, если каталога нет."""
        return self.cached(("ids", kind), lambda: self._ids(kind))

    def _ids(self, kind: str):
        conf = (self.get("ids", {}) or {}).get(kind)
        if conf:
            rx = re.compile(conf["pattern"])
            defined = set()
            drx = re.compile(conf["define"], re.M) if conf.get("define") else None
            nrx = re.compile(conf["define_name"]) if conf.get("define_name") else None
            for f in self.glob(conf.get("defined_in", [])):
                if drx:
                    defined |= {m.group(1) if m.groups() else m.group(0) for m in drx.finditer(self.text(f))}
                if nrx:
                    mm = nrx.search(Path(f).name)
                    if mm:
                        defined.add(mm.group(1) if mm.groups() else mm.group(0))
            defined |= set(U.as_list(conf.get("values")))
            return rx, defined
        if kind == "requirement":
            return re.compile(self.get("requirements.id")), set(self.requirements())
        if kind == "task":
            return re.compile(r"(?<![\w.])(" + self.get("tasks.id") + r")(?![\w.]*\d)"), set(self.tasks())
        from . import units as UN
        if UN.kind(self, kind):  # вид единиц data/ — каталог ID
            return UN.id_catalog(self, kind)
        from . import catalogs as CT
        ent = CT.entry(self, kind)  # [[data.catalogs]] и встроенный `param`
        if ent:
            return ent
        if self.plugins:
            fn = self.plugins.ref_kinds.get(kind)
            if fn:
                return fn(self)
        return None

    def find_ids(self, kind: str, text: str) -> list:
        ent = self.ids(kind)
        if not ent:
            return []
        rx = ent[0]
        return [m.group(1) if m.groups() else m.group(0) for m in rx.finditer(text)]

    # ---------- карточки блоков ----------
    def blocks(self) -> list:
        return self.cached("blocks", self._blocks)

    def _blocks(self) -> list:
        out = []
        kf = self.get("blocks.kind_field", "Вид")
        for f in self.glob(self.get("blocks.glob", [])):
            text = self.text(f)
            rows = U.table_rows(text, self.get("blocks.table_header", "| Поле | Значение |"))
            fields = {U.plain(r.get("Поле", "")): r.get("Значение", "") for r in rows}
            title = next((re.sub(r"^#\s+(Блок:\s*)?", "", l).strip() for l in text.splitlines() if l.startswith("# ")), Path(f).stem)
            m = re.search(r"\*\*" + re.escape(kf) + r":\*\*\s*([^·\n]+)", text)
            kind = U.plain(fields.get(kf) or (m.group(1) if m else "")).strip().lower()
            secs = {k: U.section(text, v) for k, v in (self.get("blocks.kinds", {}) or {}).items()}
            out.append(Block(self.component_of(f), title, f, fields, kind, secs))
        src = self.get("blocks.source", "")
        if src:
            data = C.load_data(self.root / src)
            for b in (data.get("blocks", []) if isinstance(data, dict) else data):
                fields = {k: (", ".join(map(str, v)) if isinstance(v, list) else str(v)) for k, v in (b.get("fields") or {}).items()}
                out.append(Block(b.get("component", ""), b.get("title", "?"), src, fields, str(b.get("kind", "")).lower(),
                                 {k: "\n".join(map(str, b.get("sections", {}).get(k, []))) for k in (self.get("blocks.kinds", {}) or {})}))
        dk = self.get("blocks.data_kind", "")
        if dk:  # карточки из единиц YAML (без PyYAML — пропускаются; yaml-check скажет, что нужен PyYAML)
            from . import units as UN
            k = UN.kind(self, dk) or {}
            dmap = self.get("blocks.data_map", {}) or {}
            secs = self.get("blocks.data_sections", {}) or {}

            def txt(v):
                if isinstance(v, list):
                    return "; ".join(txt(x) for x in v)
                if isinstance(v, dict):
                    return ", ".join(f"{a}: {txt(b)}" for a, b in v.items())
                return "" if v is None else str(v)
            for uid, (rel, v) in UN.units(self, dk).items():
                v = v if isinstance(v, dict) else {}
                fields = {name: txt(v.get(key)) for name, key in dmap.items()}
                title = str(v.get(k.get("title") or "title", "") or uid)
                out.append(Block(UN.component_of(self, rel, k), title, rel, fields,
                                 str(v.get(self.get("blocks.data_kind_field", "kind"), "")).lower(),
                                 {kd: txt(v.get(key)) for kd, key in secs.items()}))
        return out

    def block_card(self, b: Block):
        """(заполненные поля, недостающие поля) по обязательным полям конфига и виду блока."""
        ok, miss = [], []
        for fdef in self.get("blocks.fields", []):
            name = fdef["name"]
            (ok if not U.is_empty(b.fields.get(name, "")) else (miss if fdef.get("required", True) else ok)).append(name)
        kinds = self.get("blocks.kinds", {}) or {}
        if kinds:
            label = "вид и " + " / ".join(v.lower() for v in kinds.values())
            (ok if b.kind in kinds and section_filled(b.sections.get(b.kind, "")) else miss).append(label)
        return ok, miss
