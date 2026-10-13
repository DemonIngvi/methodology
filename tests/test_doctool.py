"""Тесты doctool на examples/minimal: все команды через командную строку, как их зовёт агент.

    python -m unittest discover -s tests -v

Без зависимостей. Пример копируется во временную папку (вместе с rules/, templates/, tools/), исходники не меняются.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HAS_GIT = shutil.which("git") is not None


def _has_yaml():
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


HAS_YAML = _has_yaml()  # данные YAML по единице: без PyYAML их тесты пропускаются, а check пропускает yaml-check


def _has_toml():
    try:
        import tomllib  # noqa: F401
    except ModuleNotFoundError:
        try:
            import tomli  # noqa: F401
        except ModuleNotFoundError:
            return False
    return True


@unittest.skipUnless(_has_toml(), "пример настроен TOML: нужен Python 3.11+ или пакет tomli")
class DocTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctool-тест-"))  # кириллица в пути — нарочно
        for d in ("rules", "templates", "tools"):
            shutil.copytree(REPO / d, self.tmp / d, ignore=shutil.ignore_patterns("__pycache__", ".doctool"))
        self.ex = self.tmp / "examples" / "minimal"
        shutil.copytree(REPO / "examples" / "minimal", self.ex, ignore=shutil.ignore_patterns("__pycache__", ".doctool"))
        self.doc = self.tmp / "tools" / "doc"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- помощники ----------
    def run_doc(self, *args, stdin=None, env=None, root=None):
        e = dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {}))
        e.pop("DOC_ROOT", None)
        r = subprocess.run([sys.executable, str(self.doc), "--root", str(root or self.ex), *args], cwd=str(root or self.ex),
                           input=(stdin or "").encode("utf-8"), capture_output=True, env=e)
        return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")

    def read(self, rel):
        return (self.ex / rel).read_text(encoding="utf-8")

    def write(self, rel, text):
        p = self.ex / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def edit(self, rel, old, new):
        t = self.read(rel)
        self.assertIn(old, t)
        self.write(rel, t.replace(old, new, 1))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.ex, capture_output=True)

    def git_init(self):
        self.git("init", "-q")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    def assert_clean(self):
        rc, out, err = self.run_doc("check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("замечаний нет", out)

    # ---------- route / pre / post / generated / next ----------
    def test_check_clean_on_example(self):
        self.assert_clean()

    def test_route(self):
        rc, out, _ = self.run_doc("route", "specs/billing/spec.md")
        self.assertEqual(rc, 0)
        self.assertIn("Тип: Spec", out)
        self.assertIn("rules/sdd.md", out)            # методология — из таблицы rules/doc-types.md
        self.assertIn("Purpose, требования", out)     # колонка «Обязательные разделы» таблицы правил
        rc, out, _ = self.run_doc("route", "deploy/billing.md")
        self.assertIn("Подтверждают: С", out)           # роли — из таблицы rules/approvals.md
        rc, out, _ = self.run_doc("route", "nowhere/x.md")
        self.assertIn("нет правил", out)

    def test_pre_generated(self):
        rc, _, err = self.run_doc("pre", "TASKS.md")
        self.assertEqual(rc, 2)
        self.assertIn("tasks/*.md", err)
        rc, _, _ = self.run_doc("pre", "deploy/billing.md")
        self.assertEqual(rc, 0)
        rc, out, _ = self.run_doc("generated", "approvals.md")
        self.assertIn("генерируется", out)
        rc, out, _ = self.run_doc("generated", "design.md")
        self.assertEqual(out.strip(), "")

    def test_post_and_next(self):
        rc, out, _ = self.run_doc("post", "tasks/1.1-invoice-export.md")
        self.assertEqual(rc, 0)
        self.assertIn("Тип: Задача", out)
        self.assertIn("Замечаний проверок нет", out)
        rc, out, _ = self.run_doc("next", "specs/billing/spec.md")
        self.assertIn("Задачи — проверить", out)
        self.assertIn("rules/tasks.md", out)
        rc, out, _ = self.run_doc("next", "ROADMAP.md")
        self.assertIn("исходящих связей нет", out)

    def test_hooks(self):
        rc, _, err = self.run_doc("hook-pre", stdin=json.dumps({"tool_input": {"file_path": str(self.ex / "TASKS.md")}}))
        self.assertEqual(rc, 2)
        self.assertIn("генерируется", err)
        rc, _, _ = self.run_doc("hook-pre", stdin=json.dumps({"tool_input": {"file_path": str(self.ex / "design.md")}}))
        self.assertEqual(rc, 0)
        self.edit("architecture.md", "](design.md#d-1-csv-вместо-xlsx)", "](design.md#нет-такого)")
        rc, out, _ = self.run_doc("hook-post", stdin=json.dumps({"tool_input": {"file_path": str(self.ex / "architecture.md")}}))
        self.assertEqual(rc, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Тип: Главная / архитектура", ctx)
        self.assertIn("Узел `architecture.md`", ctx)
        self.assertIn("нет якоря «#нет-такого»", ctx)
        rc, out, _ = self.run_doc("hook-post", stdin="не json")
        self.assertEqual((rc, out), (0, ""))

    # ---------- check: ошибки ----------
    def test_check_errors(self):
        self.edit("README.md", "](design.md)", "](design-нет.md)")                                   # битая ссылка
        self.edit("deploy/billing.md", "](../specs/billing/interfaces.md)", "](../specs/billing/interfaces.md#нет)")  # якорь
        self.write("specs/billing/lost.md", "# Потерянный документ\n")                               # сирота
        self.edit("specs/billing/spec.md", "ID: R-BIL-2 · Реализует задача: [1.1](../../tasks/1.1-invoice-export.md)",
                  "ID: R-BIL-2 · Реализует задача: —")                                              # требование без задачи
        self.edit("components/billing/blocks/exporter.md", "| Задачи | 1.1 |", "| Задачи | 9.9 |")    # ссылка карточки
        self.edit("components/billing/blocks/exporter.md", "`invoices` — чтение", "`payments` — чтение")
        self.edit("tasks/1.1-invoice-export.md", "| **Итого** | **6 ≈ 1 дн** |", "| **Итого** | **7 ≈ 1 дн** |")  # итог задачи
        self.edit("reviews/findings/billing.md", "| Z-BIL-2 |", "| Z-XX-2 |")                     # формат ID замечания
        self.edit("deploy/billing.md", "## Запуск", "## Старт")                                       # обязательный раздел
        self.edit("design.md", "## D-1.", "## D-2.")                                                  # ссылка на D-1 повисла
        rc, out, _ = self.run_doc("check")
        self.assertEqual(rc, 1)
        for s in ("нет файла «design-нет.md»", "нет якоря «#нет»", "сирота: specs/billing/lost.md",
                  "R-BIL-2: нет задачи", "задача 1.1 → R-BIL-2, но у R-BIL-2 нет задачи 1.1",
                  "task 9.9 не существует", "table payments не существует",
                  "сумма подзадач 6 ч ≠ «Итого» 7 ч", "у 1.1 6 ч, в задаче «Итого» 7 ч",
                  "ID «Z-XX-2» не по формату Z-BIL-{n}", "нет ## Запуск", "decision D-1 не существует"):
            self.assertIn(s, out)
        rc, out, _ = self.run_doc("check", "--only", "links")
        self.assertIn("design-нет.md", out)
        self.assertNotIn("сирота", out)

    def test_check_plugin_and_trace(self):
        self.edit("specs/billing/spec.md", "Биллинг выставляет счета и выгружает их бухгалтерии в CSV за выбранный период. ", "Коротко. ")
        self.edit("specs/billing/interfaces.md", "| I1 |", "| I7 |")
        self.edit("specs/billing/spec.md", '<a id="r-bil-2"></a>', '<a id="r-bil-x"></a>')
        rc, out, _ = self.run_doc("check")
        self.assertEqual(rc, 1)
        self.assertIn("purpose: specs/billing/spec.md: Purpose короче 50 символов", out)   # check-правило плагина проекта
        self.assertIn("интерфейс I7 не описан в модели данных", out)                     # встроенный плагин datamodel
        self.assertIn("якорь перед заголовком R-BIL-2", out)

    # ---------- lint ----------
    def test_lint(self):
        rc, out, _ = self.run_doc("lint")
        self.assertEqual((rc, out.strip()), (0, "пробелов нет"))
        self.edit("tasks/1.1-invoice-export.md", "| 1.1.2 | отправка через I1 с повтором | 3 |", "| 1.1.2 | отправка через I1 с повтором | 12 |")
        self.edit("tasks/1.1-invoice-export.md", "| 2–4 ч |", "| 3 ч |")
        self.edit("components/billing/blocks/exporter.md", "| При сбое | почтовый сервис недоступен → 3 повтора, затем «не отправлен» |", "| При сбое | — |")
        self.edit("tasks/1.1-invoice-export.md", "таблица `invoices` — миграция", "таблица — миграция")
        self.edit("tasks/1.1-invoice-export.md", "миграция таблицы `invoices` и", "миграция и")
        rc, out, _ = self.run_doc("lint", "billing")
        self.assertEqual(rc, 0)
        self.assertIn("подзадача 1.1.2 — 12 ч (нужно 2–8)", out)                         # встроенное
        self.assertIn("карточка не заполнена — При сбое", out)                           # встроенное
        self.assertIn("оценка агента не диапазоном", out)                                # плагин проекта
        self.assertIn("таблица `invoices`: не названа ни в одной задаче", out)           # встроенный плагин
        rc, _, _ = self.run_doc("lint", "--strict")
        self.assertEqual(rc, 1)

    # ---------- подтверждения ----------
    def test_approve_reset_after_edit(self):
        rc, out, err = self.run_doc("approve", "deploy/billing.md", "Д", "ok", "развёртывание и откат описаны")
        self.assertEqual(rc, 0, err)
        self.assertIn("✅ 🤖", out)
        reg = self.read("approvals.md")
        self.assertRegex(reg, r"\| `deploy/billing.md` \| Д \| агент, роль DevOps-инженер \| 🤖 \| \d{4}-\d\d-\d\d \| ✅ \|")
        rc, out, _ = self.run_doc("approve", "deploy/billing.md", "Б", "no", "нет секретов в хранилище → раздел «Переменные»", "--human")
        self.assertIn("❌ 👤", out)
        rc, out, _ = self.run_doc("review-queue", "Д")
        self.assertNotIn("deploy/billing.md", out)
        # правка текста → решения сброшены, в сводке «⏳ было ✅»
        self.edit("deploy/billing.md", "Один контейнер; порт 8080.", "Один контейнер; порт 9090.")
        self.assert_clean()
        reg = self.read("approvals.md")
        self.assertIn("текст изменён после решения «✅»", reg)
        self.assertIn("текст изменён после решения «❌»", reg)
        self.assertIn("⏳ было ✅", self.read("approvals-summary.md"))
        rc, out, _ = self.run_doc("review-queue", "Д")
        self.assertIn("deploy/billing.md × Д (было ✅)", out)
        # пробелы и переводы строк версию не меняют
        self.run_doc("approve", "deploy/billing.md", "Д", "ok", "повторно")
        self.edit("deploy/billing.md", "Один контейнер; порт 9090.", "Один контейнер;  порт 9090.")
        self.assert_clean()
        self.assertIn("| повторно |", self.read("approvals.md"))
        # неизвестная пара
        rc, _, err = self.run_doc("approve", "design.md", "Т", "ok", "x")
        self.assertEqual(rc, 1)
        self.assertIn("нет строки «design.md × Т»", err)

    def test_requirement_approval(self):
        rc, out, err = self.run_doc("approve", "R-BIL-1", "Т", "ok", "сценарии проверяемы")
        self.assertEqual(rc, 0, err)
        self.assertIn("| R-BIL-1 | Т | агент, роль Тестировщик | 🤖 |", self.read("specs/billing/approvals.md"))
        self.edit("specs/billing/spec.md", "одним CSV-файлом", "одним файлом CSV")
        self.assert_clean()
        self.assertIn("| R-BIL-1 | Т | — | — | — | ⏳ |", self.read("specs/billing/approvals.md"))
        self.assertIn("⏳ было ✅", self.read("approvals-summary.md"))

    def test_registry_lock(self):
        self.assert_clean()
        lock = self.ex / "approvals.md.lock"
        lock.write_text("1", encoding="utf-8")
        rc, _, err = self.run_doc("approve", "design.md", "А", "ok", "x", env={"DOC_LOCK_TIMEOUT": "0.5"})
        self.assertEqual(rc, 1)
        self.assertIn("реестр занят", err)
        lock.unlink()
        # параллельные проверяющие: каждый пишет свою строку, ни одна не теряется
        results = []

        def one(page, letter):
            results.append(self.run_doc("approve", page, letter, "ok", f"параллельно {letter}"))
        pairs = [("tasks/1.1-invoice-export.md", "Р"), ("tasks/1.1-invoice-export.md", "Т"), ("tasks/1.1-invoice-export.md", "В"),
                 ("design.md", "А"), ("design.md", "В"), ("architecture.md", "А")]
        th = [threading.Thread(target=one, args=p) for p in pairs]
        for t in th:
            t.start()
        for t in th:
            t.join()
        self.assertTrue(all(r[0] == 0 for r in results), results)
        reg = self.read("approvals.md")
        for page, letter in pairs:
            self.assertIn(f"| `{page}` | {letter} | агент", reg)
        self.assertFalse(lock.exists())

    def test_review_pack(self):
        rc, out, _ = self.run_doc("review-pack", "tasks/1.1-invoice-export.md", "Т")
        self.assertEqual(rc, 0)
        p = self.ex / out.split(" — ")[0]
        t = p.read_text(encoding="utf-8")
        for s in ("Пакет проверки: `tasks/1.1-invoice-export.md` × Т", "Вопрос роли:** как проверить каждое утверждение",
                  "## Чек-лист методологии типа", "Подзадачи 2–8 часов", "**R-BIL-1** Экспорт счетов за период",
                  "«Слишком много строк»", "ОВ-1", "## lint компонента billing", "## Страница"):
            self.assertIn(s, t)

    # ---------- замечания ----------
    def test_finding(self):
        rc, out, _ = self.run_doc("finding", "billing", "Z-BIL-1,Z-BIL-2", "ok", "добавлена проверка письма")
        self.assertIn("обновлено 2", out)
        self.assertIn("| ✅ добавлена проверка письма |", self.read("reviews/findings/billing.md"))
        rc, out, _ = self.run_doc("finding", "billing", "Z-BIL-2", "owner")
        self.assertIn("| 📌 |", self.read("reviews/findings/billing.md"))
        rc, out, _ = self.run_doc("finding", "billing", "next-id")
        self.assertEqual(out.strip(), "Z-BIL-3")
        self.assert_clean()

    # ---------- move ----------
    def test_move_rewrites_links(self):
        if HAS_GIT:
            self.git_init()
        rc, out, err = self.run_doc("move", "deploy/billing.md", "ops/развёртывание/billing.md")
        self.assertEqual(rc, 0, err)
        self.assertTrue((self.ex / "ops/развёртывание/billing.md").is_file())
        self.assertFalse((self.ex / "deploy/billing.md").exists())
        moved = self.read("ops/развёртывание/billing.md")
        self.assertIn("](../../architecture.md)", moved)                   # исходящие ссылки самого файла
        self.assertIn("](../../specs/billing/interfaces.md)", moved)
        self.assertIn("](ops/развёртывание/billing.md)", self.read("README.md"))  # входящие
        if HAS_GIT:
            st = self.git("status", "--porcelain").stdout.decode("utf-8", "replace")
            self.assertIn("R", st)
        rc, out, _ = self.run_doc("check", "--only", "links,anchors,orphans")
        self.assertEqual(rc, 0, out)
        # папка целиком
        rc, out, err = self.run_doc("move", "specs/billing", "specs/invoicing")
        self.assertEqual(rc, 0, err)
        self.assertIn("](specs/invoicing/spec.md)", self.read("README.md"))
        self.assertIn("](../specs/invoicing/spec.md#r-bil-1)", self.read("tasks/1.1-invoice-export.md"))
        rc, out, _ = self.run_doc("check", "--only", "links,anchors,orphans")
        self.assertEqual(rc, 0, out)

    # ---------- staged ----------
    @unittest.skipUnless(HAS_GIT, "нет git")
    def test_staged(self):
        self.git_init()
        rc, out, _ = self.run_doc("staged")
        self.assertEqual(rc, 0, out)
        self.write("site/index.html", "<html></html>")
        self.git("add", "-f", "site/index.html")
        rc, out, _ = self.run_doc("staged")
        self.assertEqual(rc, 1)
        self.assertIn("site/index.html: генерируемый файл изменён", out)

    # ---------- blocks / diagram / rnd / plugins / init ----------
    def test_blocks(self):
        rc, out, _ = self.run_doc("blocks")
        n = 2 if HAS_YAML else 1  # второй блок — единица YAML data/blocks/billing/mailer.yaml
        self.assertIn(f"блоков {n}, карточка заполнена полностью у {n}", out)
        self.edit("components/billing/blocks/exporter.md", "| Технология | Python 3.12 |", "| Технология | <технология> |")
        rc, out, _ = self.run_doc("blocks")
        self.assertIn("нет: Технология", out)

    def test_diagram(self):
        rc, out, _ = self.run_doc("diagram", "architecture.md")
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip().startswith("file:"))
        html = (self.ex / ".doctool/diagrams/architecture.html").read_text(encoding="utf-8")
        self.assertIn('class="mermaid"', html)
        rc, _, err = self.run_doc("diagram", "design.md")
        self.assertIn("нет блоков", err)

    def test_rnd_and_rnd_check(self):
        rc, out, err = self.run_doc("rnd", "csv-format", "Формат", "выгрузки")
        self.assertEqual(rc, 0, err)
        p = self.ex / "rnd/csv-format.md"
        t = p.read_text(encoding="utf-8")
        self.assertIn("# R&D: Формат выгрузки", t)
        self.assertIn("](../../../rules/rnd.md)", t)                       # ссылка на методологию — от нового файла
        self.assertIn("[все R&D](README.md)", t)                          # реестр проекта
        self.assertIn("[Формат выгрузки](csv-format.md)", self.read("rnd/README.md"))
        rc, out, _ = self.run_doc("rnd", "csv-format", "ещё раз")
        self.assertEqual(rc, 1)
        # заготовка: структура Э1–Э10 и ворота на месте, не заполнен только источник
        rc, out, _ = self.run_doc("rnd-check", "rnd/csv-format.md")
        self.assertEqual(rc, 1)
        self.assertIn("источник без", out)
        self.assertNotIn("Э1", out)
        self.edit("rnd/csv-format.md", "- [<название>](<ссылка>) — ", "- [RFC 4180](https://www.rfc-editor.org/rfc/rfc4180) — ")
        self.edit("rnd/csv-format.md", ", <А–Г>, <что взято>", ", А, формат CSV")
        rc, out, _ = self.run_doc("rnd-check", "rnd/csv-format.md")
        self.assertEqual(rc, 0, out)
        self.assertIn("структура v3.2 в порядке", out)
        self.assert_clean()
        # ворота ⚠️ без 📌, нет вывода этапа, нет раздела — ловит и rnd-check, и check
        t = self.read("rnd/csv-format.md")
        t = t.replace("G3 ✅", "G3 ⚠️ не подтверждено", 1).replace("**Вывод этапа.** …\n\nG5", "G5", 1)
        t = t.replace("## Э7. Отсев и встраивание", "## Отсев", 1)
        self.write("rnd/csv-format.md", t)
        rc, out, _ = self.run_doc("rnd-check")
        self.assertEqual(rc, 1)
        for s in ("G3 ⚠️ без 📌", "Э5: нет строки «**Вывод этапа.**»", "нет раздела «## Э7.»"):
            self.assertIn(s, out)
        rc, out, _ = self.run_doc("check", "--only", "rnd")
        self.assertIn("R&D: rnd/csv-format.md: Э3: G3 ⚠️ без 📌", out)

    def test_plugins_command(self):
        rc, out, _ = self.run_doc("plugins")
        self.assertIn("builtin:datamodel", out)
        self.assertIn("tasks-table", out)
        self.write("TASKS.md", "испорчено\n")
        rc, out, _ = self.run_doc("tasks-table")
        self.assertEqual(rc, 0)
        self.assertIn("| [1.1](tasks/1.1-invoice-export.md) | billing | R-BIL-1, R-BIL-2 | 6 |", self.read("TASKS.md"))

    def test_init_and_json_config(self):
        empty = self.tmp / "пустой проект"
        empty.mkdir()
        rc, out, err = self.run_doc("init", root=empty)
        self.assertEqual(rc, 0, err)
        self.assertTrue((empty / "doc.config.toml").is_file())
        rc, out, _ = self.run_doc("init", root=empty)
        self.assertEqual(rc, 1)
        rc, out, _ = self.run_doc("route", "specs/x/spec.md", root=empty)
        self.assertIn("Тип: Spec", out)
        rc, out, err = self.run_doc("init", "--json", root=empty)
        self.assertEqual(rc, 0, err)
        json.loads((empty / "doc.config.json").read_text(encoding="utf-8"))
        # тот же пример с конфигом JSON вместо TOML даёт тот же результат
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest("нет tomllib")
        data = tomllib.loads(self.read("doc.config.toml"))
        (self.ex / "doc.config.toml").unlink()
        self.edit("README.md", "(doc.config.toml)", "(doc.config.json)")
        self.write("doc.config.json", json.dumps(data, ensure_ascii=False, indent=2))
        self.assert_clean()
        rc, out, _ = self.run_doc("route", "deploy/billing.md")
        self.assertIn("Тип: Deploy", out)

    def test_lf_and_no_crlf(self):
        self.run_doc("approve", "design.md", "А", "ok", "x")
        self.assertNotIn(b"\r\n", (self.ex / "approvals.md").read_bytes())
        self.assertNotIn(b"\r\n", (self.ex / "approvals-summary.md").read_bytes())

    # ---------- данные: YAML по единице ----------
    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_check(self):
        rc, out, _ = self.run_doc("yaml-check")
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))
        blk = "data/blocks/billing/mailer.yaml"
        self.edit(blk, "kind: сервис", "kind: процесс")                                   # недопустимое значение
        self.edit(blk, "tasks: ['1.1']", "tasks: ['9.9']")                                # ссылка на несуществующую задачу
        self.edit(blk, "tech: Python 3.12, очередь задач планировщика", "tech: 3")        # тип
        self.edit(blk, "stories: [US-BIL-1]", "stories: [US-BIL-9]")                      # ссылка на вид единиц data/
        self.edit("data/stories/billing/US-BIL-1.yaml", "so_that: загрузить их в учётную систему без ручного сбора\n", "colour: red\n")
        self.write("data/blocks/billing/new.yaml", "title: Новый\n")                       # нет в _order.yaml, нет полей
        rc, out, _ = self.run_doc("yaml-check")
        self.assertEqual(rc, 1)
        for s in ("mailer.yaml: kind: «процесс» — допустимо: интерфейс, сервис, хранилище",
                  "mailer.yaml: tasks[0]: task 9.9 не существует", "mailer.yaml: tech: нужно строка, а не число",
                  "mailer.yaml: stories[0]: story US-BIL-9 не существует",
                  "US-BIL-1.yaml: so_that: обязательное поле отсутствует", "US-BIL-1.yaml: colour: поле не описано в схеме вида",
                  "new.yaml: kind: обязательное поле отсутствует", "_order.yaml: new не в списке"):
            self.assertIn(s, out)
        rc, out, _ = self.run_doc("check")                                                # yaml-check входит в check
        self.assertEqual(rc, 1)
        self.assertIn("yaml: data/blocks/billing/mailer.yaml: kind: «процесс»", out)
        rc, out, _ = self.run_doc("yaml-check", "data/stories")                           # только папка
        self.assertNotIn("mailer.yaml", out)
        self.write("data/models/Broken.yaml", "name: [не закрыт\n")
        rc, out, _ = self.run_doc("yaml-check", "data/models")
        self.assertIn("Broken.yaml: не YAML", out)

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_fmt(self):
        self.write("data/stories/billing/US-BIL-1.yaml",
                   "# комментарий сохраняется\naccept: [a, b]\nreqs:\n- R-BIL-1\nwant: w\nrole: r\nso_that: s\n")
        self.write("data/blocks/billing/zz.yaml", self.read("data/blocks/billing/mailer.yaml").replace("Рассылка счетов", "Другой"))
        rc, out, _ = self.run_doc("yaml-fmt")
        self.assertEqual(rc, 0)
        self.assertIn("US-BIL-1.yaml", out)
        self.assertEqual(self.read("data/stories/billing/US-BIL-1.yaml"),
                         "# комментарий сохраняется\nrole: r\nwant: w\nso_that: s\nreqs: [R-BIL-1]\naccept: [a, b]\n")
        self.assertEqual(self.read("data/blocks/billing/_order.yaml"), "- mailer\n- zz\n")
        rc, out, _ = self.run_doc("yaml-fmt")
        self.assertIn("отформатировано: 0", out)                                          # идемпотентно

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_unit_route_next(self):
        rc, out, _ = self.run_doc("unit", "US-BIL-1")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("# data/stories/billing/US-BIL-1.yaml · вид story · ID US-BIL-1"))
        self.assertIn("связано: requirement R-BIL-1, block billing/mailer (ссылается)", out)
        rc, out, _ = self.run_doc("unit", "billing/mailer")
        self.assertIn("связано: story US-BIL-1, participant планировщик, participant почтовый сервис, interface I1, table invoices, param mail.retries, requirement R-BIL-2, task 1.1", out)
        self.assertIn("перегенерируется", out)
        self.assertNotIn("screens", out)                                                  # пустые поля не выводятся
        rc, out, _ = self.run_doc("unit", "Рассылка счетов")                              # по названию
        self.assertIn("ID billing/mailer", out)
        rc, out, _ = self.run_doc("unit", "data/models/Invoice.yaml")
        self.assertIn("связано: interface I1", out)
        rc, out, _ = self.run_doc("unit", "нет-такого")
        self.assertIn("единица не найдена", out)
        rc, out, _ = self.run_doc("route", "data/blocks/billing/mailer.yaml")
        self.assertIn("Единица данных YAML, вид «block»", out)
        self.assertIn("yaml-check data/blocks/billing/mailer.yaml", out)
        self.assertIn("Подтверждают: А", out)
        rc, out, _ = self.run_doc("next", "data/stories/billing/US-BIL-1.yaml")
        self.assertIn("Источник данных (YAML, единица «story»)", out)
        self.assertIn("Перегенерируется — один раз в конце пути изменений", out)
        self.assertIn("Спека — проверить", out)

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_strict(self):
        """Строгая схема: именованные поля, каталоги с подсказкой ближайшего, тип параметра, «нет: <почему>»."""
        blk = "data/blocks/billing/mailer.yaml"
        rc, out, _ = self.run_doc("yaml-check")
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))
        self.edit(blk, "{dir: ←, with: планировщик, what: ID счёта}", "{dir: ←, with: планировщк, what: ID счёта}")        # участник не из каталога
        self.edit(blk, "{dir: →, with: почтовый сервис, what: письмо со счётом, via: [I1]}", "{dir: ->, what: письмо, via: [I11]}")
        self.edit(blk, "{table: invoices, access: read-write,", "{table: invoces, access: read-write,")                  # таблица
        self.edit(blk, "{name: mail.retries, value: 3}", "{name: mail.retrys, value: 3}")                                  # параметр
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual(rc, 1)
        for s in ("io[0].with: participant «планировщк» не существует — может быть: планировщик",
                  "io[1].dir: «->» — допустимо: →, ←, ↔", "io[1]: нужно хотя бы одно из полей: with, label",
                  "io[1].via[0]: interface I11 не существует", "data[0].table: table «invoces» не существует — может быть: invoices",
                  "params[0].name: «mail.retrys» нет среди параметров спек компонента billing", "может быть: mail.retries"):
            self.assertIn(s, out)
        # тип значения параметра — из таблицы параметров спеки (мин 0, макс 10)
        self.edit(blk, "{name: mail.retrys, value: 3}", "{name: mail.retries, value: 50}")
        self.edit(blk, "{dir: ->, what: письмо, via: [I11]}", "{dir: →, label: внешний сервис, what: письмо, via: [I1]}")
        self.edit(blk, "{table: invoces,", "{table: invoices,")
        self.edit(blk, "with: планировщк", "with: планировщик")
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual(rc, 1)
        self.assertIn("params[0].value: «50» не по типу параметра mail.retries (specs/billing/interfaces.md): число от 0 до 10 (по умолчанию 3)", out)
        self.assertNotIn("io[", out)
        self.edit(blk, "value: 50}", "value: 10}")
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))
        # поле строки не из схемы — подсказка ближайшего имени
        self.edit(blk, "{name: mail.retries, value: 10}", "{name: mail.retries, valu: 10}")
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertIn("params[0].valu: поле не описано в схеме вида — допустимо: name, value, note — может быть: value", out)
        self.edit(blk, "valu: 10", "value: 3")
        # «нет: <почему>» — допустимое значение списковых полей data и params; пустая причина и прочий текст — ошибка
        self.edit(blk, "data:\n- {table: invoices, access: read-write, why: статус отправки пишется в счёт}", "data: 'нет: таблиц нет, письмо уходит из очереди'")
        self.edit(blk, "params:\n- {name: mail.retries, value: 3}", "params: 'нет:'")
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertIn("params: после «нет:» нужна причина", out)
        self.assertNotIn("data:", out)
        self.edit(blk, "params: 'нет:'", "params: просто текст")
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertIn("params: нужен список или строка «нет: <почему>», а не «просто текст»", out)
        self.edit(blk, "params: просто текст", "params: 'нет: настроек у блока нет, повторы — в планировщике'")
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))
        rc, out, _ = self.run_doc("unit", "billing/mailer")                               # ссылок в строке «нет: …» нет — «связано» без таблицы
        self.assertIn("нет: таблиц нет", out)
        self.assertNotIn("table invoices", out)
        rc, out, _ = self.run_doc("lint", "billing")                                      # карточка блока заполнена: «нет: …» — значение поля
        self.assertEqual((rc, out.strip()), (0, "пробелов нет"))
        self.assert_clean()

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_catalogs_from_config(self):
        """Каталог объявляется в конфиге: значения из полей единиц, регулярки по файлам, перечисление."""
        blk = "data/blocks/billing/mailer.yaml"
        self.edit(blk, "with: планировщик", "with: почтовый сервис")                        # регулярка по interfaces.md: «биллинг → почтовый сервис»
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))
        self.edit(blk, "with: почтовый сервис", "with: Рассылка счетов")                  # поле title единиц вида block
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))
        self.edit(blk, "with: Рассылка счетов", "with: планировщик")                      # values = ["планировщик"]
        self.edit("doc.config.toml", 'values = ["планировщик"]', 'values = ["планировщика"]')
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertIn("participant «планировщик» не существует — может быть: планировщика", out)

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_schema(self):
        rc, out, _ = self.run_doc("yaml-schema")
        self.assertEqual(rc, 0, out)
        sd = self.ex / "data/_schema"
        names = sorted(p.name for p in sd.glob("*.json"))
        self.assertEqual(names, ["block.schema.json", "catalog.schema.json", "costs.schema.json", "model.schema.json", "order.schema.json",
                                 "story.schema.json"])
        blk = json.loads((sd / "block.schema.json").read_text(encoding="utf-8"))
        io = blk["properties"]["io"]["items"]["properties"]
        self.assertEqual(io["dir"]["enum"], ["→", "←", "↔"])
        self.assertIn("required", blk)
        self.assertIn("title", blk["required"])
        self.assertEqual(blk["properties"]["kind"]["enum"], ["интерфейс", "сервис", "хранилище"])
        self.assertEqual(blk["properties"]["data"]["anyOf"][1]["type"], "string")          # «нет: <почему>» — строка
        cat = json.loads((sd / "catalog.schema.json").read_text(encoding="utf-8"))["definitions"]
        self.assertIn("планировщик", cat["participant"]["enum"])
        self.assertIn("US-BIL-1", cat["story"]["enum"])
        self.assertIn("I1", cat["interface"]["enum"])
        vs = json.loads(self.read(".vscode/settings.json"))["yaml.schemas"]
        self.assertEqual(vs["./data/_schema/block.schema.json"], "data/blocks/*/[!_]*.yaml")
        self.assertEqual(vs["./data/_schema/order.schema.json"], "**/_order.yaml")
        rc, out, _ = self.run_doc("yaml-schema")                                            # идемпотентно
        self.assertIn("схемы: изменено 0", out)
        self.assert_clean()
        # изменили каталог — схема устарела (check ловит), yaml-schema чинит
        self.edit("doc.config.toml", 'values = ["планировщик"]', 'values = ["планировщик", "касса"]')
        rc, out, _ = self.run_doc("check")
        self.assertEqual(rc, 1)
        self.assertIn("yaml: data/_schema/catalog.schema.json: устарела", out)
        rc, out, _ = self.run_doc("yaml-schema")
        self.assertIn("data/_schema/catalog.schema.json", out)
        self.assert_clean()
        # лишняя схема удаляется
        self.write("data/_schema/old.schema.json", "{}\n")
        rc, out, _ = self.run_doc("check")
        self.assertIn("old.schema.json: лишняя", out)
        rc, out, _ = self.run_doc("yaml-schema")
        self.assertIn("old.schema.json (удалён)", out)

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_schema_vscode_keeps_settings(self):
        self.write(".vscode/settings.json", json.dumps({"editor.tabSize": 2, "yaml.schemas": {"https://example.org/x.json": "*.foo"}}))
        rc, out, _ = self.run_doc("yaml-schema")
        self.assertEqual(rc, 0, out)
        vs = json.loads(self.read(".vscode/settings.json"))
        self.assertEqual(vs["editor.tabSize"], 2)                                           # чужие настройки на месте
        self.assertEqual(vs["yaml.schemas"]["https://example.org/x.json"], "*.foo")         # чужая схема на месте
        self.assertIn("./data/_schema/block.schema.json", vs["yaml.schemas"])
        # файл с комментариями (JSONC) не затирается
        jsonc = '{\n  // мои настройки\n  "editor.tabSize": 4\n}\n'
        self.write(".vscode/settings.json", jsonc)
        rc, out, _ = self.run_doc("yaml-schema")
        self.assertEqual(self.read(".vscode/settings.json"), jsonc)
        self.assertIn("не изменён", out)

    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_yaml_migrate(self):
        blk = "data/blocks/billing/mailer.yaml"
        new = self.read(blk)
        legacy = (new.split("io:")[0] + "io:\n- [←, планировщик, ID счёта]\n- [→, почтовый сервис, I1]\n- [→, касса, письмо о платеже I2]\n"
                  "data:\n- [invoices, read-write]\nparams:\n- [mail.retries, '3']\n" + "reqs:" + new.split("reqs:")[1])
        self.write(blk, legacy)
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual(rc, 1)                                                             # прежний вид не проходит строгую схему
        rc, out, _ = self.run_doc("yaml-migrate", "--dry")
        self.assertIn("будет переведено файлов 1", out)
        self.assertEqual(self.read(blk), legacy)                                            # --dry ничего не пишет
        rc, out, _ = self.run_doc("yaml-migrate")
        self.assertIn("переведено файлов 1", out)
        t = self.read(blk)
        self.assertIn("- {dir: ←, with: планировщик, what: ID счёта}", t)
        self.assertIn("- {dir: →, with: почтовый сервис, via: [I1]}", t)                    # правило плагина: I-код → via
        self.assertIn("- {table: invoices, access: read-write}", t)
        self.assertIn("- {name: mail.retries, value: '3'}", t)
        self.assertIn("io[2]: I-код в тексте «письмо о платеже I2» — вынести в via", out)    # остаток текстом — для ручного разбора
        self.assertIn("осталось текстом (разобрать вручную): 1", out)
        self.write(blk, t.replace("with: касса", "label: касса"))
        rc, out, _ = self.run_doc("yaml-migrate")                                           # идемпотентно
        self.assertIn("переведено файлов 0", out)
        self.assertEqual(self.read(blk), t.replace("with: касса", "label: касса"))
        rc, out, _ = self.run_doc("yaml-check", blk)
        self.assertEqual((rc, out.strip()), (0, "yaml: замечаний нет"))

    def test_without_pyyaml(self):
        fake = self.tmp / "без-yaml"
        fake.mkdir()
        (fake / "yaml.py").write_text("raise ImportError('PyYAML не установлен (тест)')\n", encoding="utf-8")
        env = {"PYTHONPATH": str(fake)}
        rc, _, err = self.run_doc("yaml-check", env=env)
        self.assertEqual(rc, 1)
        self.assertIn("нужен PyYAML: pip install pyyaml", err)
        rc, _, err = self.run_doc("unit", "US-BIL-1", env=env)
        self.assertEqual(rc, 1)
        self.assertIn("нужен PyYAML", err)
        rc, out, err = self.run_doc("check", env=env)                                     # check не падает: пропуск с предупреждением
        self.assertEqual(rc, 0, out + err)
        self.assertIn("yaml-check пропущен — нет PyYAML", err)
        self.write("doc.config.toml", self.read("doc.config.toml").replace("[data]\n", "[data]\nrequire_yaml = true\n", 1))
        rc, out, _ = self.run_doc("check", env=env)
        self.assertEqual(rc, 1)
        self.assertIn("yaml: нужен PyYAML", out)

    # ---------- пакетная проверка ролями ----------
    def test_review_batch(self):
        rc, out, err = self.run_doc("review-batch", "billing")
        self.assertEqual(rc, 0, err)
        self.assertRegex(out, r"batch-billing-1\.md: страниц \d+, пар \d+, символов \d+, ≈\d+ ток\. карточек, ≈\d+ на страницу; с накладными агента ≈\d+ на страницу")
        self.assertNotIn("⚠️ lint", out)
        t = (self.ex / ".doctool/review/batch-billing-1.md").read_text(encoding="utf-8")
        for s in ("Вопросы ролей:", "- Т Тестировщик: как проверить", "approve-batch", "## tasks/1.1-invoice-export.md · роли: ВРТ",
                  "## components/billing/blocks/exporter.md · роли: АРТ", "## deploy/billing.md · роли: ДБС",
                  "## R-BIL-1 · роли: ТИ", "«Почтовый сервис недоступен»", "закрывает: R-BIL-1, R-BIL-2",
                  "незакрытые замечания: Z-BIL-1", "## data/blocks/billing/mailer.yaml · роли: АРТ"):
            self.assertIn(s, t)
        for part in t.split("\n## ")[1:]:                                                 # лимиты карточек из конфига
            self.assertLessEqual(len(part.split("\n", 2)[-1]), 2500, part[:80])
        self.assertNotIn("](", t)                                                          # без ссылок Markdown
        rc, out, _ = self.run_doc("review-batch", "billing", "--max", "2")                 # пачки по 2 страницы
        self.assertIn("batch-billing-2.md", out)
        self.edit("tasks/1.1-invoice-export.md", "| 1.1.2 | отправка через I1 с повтором | 3 |", "| 1.1.2 | отправка через I1 с повтором | 12 |")
        self.edit("tasks/1.1-invoice-export.md", "| **Итого** | **6 ≈ 1 дн** |", "| **Итого** | **15 ≈ 2 дн** |")
        (self.ex / ".doctool/review/batch-billing-1.md").unlink()
        rc, out, _ = self.run_doc("review-batch", "billing")                               # компонент с lint не берётся: сначала автоматика
        self.assertEqual(rc, 1)
        self.assertIn("⛔ billing: пробелов lint", out)
        self.assertIn("--force", out)
        self.assertFalse((self.ex / ".doctool/review/batch-billing-1.md").exists())
        rc, out, _ = self.run_doc("review-batch", "billing,нет-такого")                    # грязный отброшен, остальные взяты
        self.assertEqual(rc, 0)
        self.assertIn("⛔ billing: пробелов lint", out)
        self.assertIn("нет-такого: ⏳ нет", out)
        rc, out, _ = self.run_doc("review-batch", "billing", "--force")                    # осознанно обойти
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"batch-billing-1\.md: страниц \d+")
        self.assertIn("⚠️ lint billing: пробелов", out)
        rc, out, _ = self.run_doc("review-batch", "нет-такого")
        self.assertIn("⏳ нет", out)

    def test_review_batch_header_and_own_reqs(self):
        self.run_doc("review-batch", "billing")
        t = (self.ex / ".doctool/review/batch-billing-1.md").read_text(encoding="utf-8")
        self.assertIn("Каждую пару решай отдельно", t)
        self.assertIn("≥ 40 символов", t)
        self.assertIn("не генерировать скриптом", t)
        # страница уровня компонента: только требования его блоков (own_reqs = "blocks"), не все со страницы
        self.write("components/billing/overview.md", "# Обзор биллинга\n\nРассылка и экспорт счетов: R-BIL-1 и R-BIL-2.\n")
        cfg = self.read("doc.config.toml")
        self.write("doc.config.toml", cfg.replace('[[types]]\nname = "Deploy"', '[[types]]\nname = "Обзор компонента"\nglob = "components/*/overview.md"\n'
                                                  'approve = "А"\n\n[[types]]\nname = "Deploy"', 1))
        self.edit("data/blocks/billing/mailer.yaml", "reqs: [R-BIL-2]", "reqs: [R-BIL-1]")
        self.edit("components/billing/blocks/exporter.md", "| Требования | R-BIL-1, R-BIL-2 |", "| Требования | R-BIL-1 |")
        self.edit("components/billing/blocks/exporter.md", "затем «не отправлен» |", "затем «не отправлен» (R-BIL-1) |")
        page = "## components/billing/overview.md"

        def reqs_of_page():
            rc, out, err = self.run_doc("review-batch", "billing", "--force")
            self.assertEqual(rc, 0, out + err)
            t = (self.ex / ".doctool/review/batch-billing-1.md").read_text(encoding="utf-8")
            self.assertIn(page, t)
            return t.split(page)[1].split("\n## ")[0]
        self.assertIn("требования (2):", reqs_of_page())
        self.write("doc.config.toml", self.read("doc.config.toml").replace('glob = "components/*/overview.md"\n', 'glob = "components/*/overview.md"\nown_reqs = "blocks"\n', 1))
        own = reqs_of_page()
        self.assertIn("требования (1):", own)
        self.assertNotIn("R-BIL-2 ", own)

    def test_approve_batch(self):
        self.assert_clean()
        good = [{"page": "deploy/billing.md", "role": "Д", "ok": True, "comment": "Deploy: раздел `Обновление и откат` есть, миграции обратимые, откат — предыдущий тег"},
                {"page": "R-BIL-1", "role": "Т", "ok": True, "comment": "R-BIL-1: три сценария, каждый проверяем по входу и выходу"},
                {"page": "tasks/1.1-invoice-export.md", "role": "Р", "ok": False, "comment": "нет схемы | таблицы invoices в задаче 1.1 →\nдобавить в «Декомпозицию»"}]
        self.write(".doctool/review/a.json", json.dumps(good, ensure_ascii=False))
        rc, out, err = self.run_doc("approve-batch", ".doctool/review/a.json")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("применено 3: ✅ 2, ❌ 1", out)
        reg = self.read("approvals.md")
        self.assertRegex(reg, r"\| `deploy/billing.md` \| Д \| агент, роль DevOps-инженер \| 🤖 \| [\d-]+ \| ✅ \|")
        self.assertIn("| ❌ | ", reg)
        self.assertIn("нет схемы / таблицы invoices в задаче 1.1 → добавить в «Декомпозицию»", reg)                                # | и перевод строки не ломают таблицу
        self.assertIn("| R-BIL-1 | Т | агент, роль Тестировщик | 🤖 |", self.read("specs/billing/approvals.md"))
        # неизвестная пара — ошибка, и реестр не меняется совсем
        before = (self.read("approvals.md"), self.read("specs/billing/approvals.md"), self.read("approvals-summary.md"))
        bad = [{"page": "design.md", "role": "А", "ok": True, "comment": "решение D-1 согласовано с архитектурой, границы совпадают"},
               {"page": "design.md", "role": "Т", "ok": True, "comment": "роль Т не подтверждает решения: проверка D-1 не нужна"},
               {"page": "R-BIL-1", "role": "Д", "ok": True, "comment": "R-BIL-1: роль Д не подтверждает это требование спеки"}]
        self.write(".doctool/review/b.json", json.dumps(bad, ensure_ascii=False))
        rc, out, _ = self.run_doc("approve-batch", ".doctool/review/b.json")
        self.assertEqual(rc, 1)
        self.assertIn("нет строки «design.md × Т»", out)
        self.assertIn("требование R-BIL-1 роль Д не подтверждает", out)
        self.assertIn("не применено ничего", out)
        self.assertEqual(before, (self.read("approvals.md"), self.read("specs/billing/approvals.md"), self.read("approvals-summary.md")))
        self.write(".doctool/review/c.json", "{не json")
        rc, _, err = self.run_doc("approve-batch", ".doctool/review/c.json")
        self.assertEqual(rc, 1)
        self.assertIn("не прочитан JSON", err)

    def test_approve_batch_guard(self):
        self.assert_clean()
        before = (self.read("approvals.md"), self.read("specs/billing/approvals.md"))
        same = "Deploy: раздел `Обновление и откат` есть, откат — предыдущий тег образа"
        pairs = [("deploy/billing.md", "Д"), ("deploy/billing.md", "Б"), ("deploy/billing.md", "С"), ("components/billing/blocks/exporter.md", "А"),
                 ("components/billing/blocks/exporter.md", "Р"), ("components/billing/blocks/exporter.md", "Т"), ("R-BIL-1", "Т"), ("R-BIL-1", "И")]
        tpl = [{"page": p, "role": r, "ok": True, "comment": same} for p, r in pairs]       # один комментарий у всех пар
        self.write(".doctool/review/t.json", json.dumps(tpl, ensure_ascii=False))
        rc, out, _ = self.run_doc("approve-batch", ".doctool/review/t.json")
        self.assertEqual(rc, 1)
        self.assertIn("шаблонные решения", out)
        self.assertIn("у 8 из 8 пар", out)
        self.assertIn("ничего не применено", out)
        short = [{"page": "deploy/billing.md", "role": "Д", "ok": True, "comment": "всё хорошо"},        # короткий комментарий
                 {"page": "R-BIL-1", "role": "Т", "ok": True, "comment": "сценарии описаны достаточно подробно, но без единого идентификатора"}]
        self.write(".doctool/review/s.json", json.dumps(short, ensure_ascii=False))
        rc, out, _ = self.run_doc("approve-batch", ".doctool/review/s.json")
        self.assertEqual(rc, 1)
        self.assertIn("2 решений без проверенного факта", out)
        self.assertIn("deploy/billing.md × Д", out)
        self.assertEqual(before, (self.read("approvals.md"), self.read("specs/billing/approvals.md")))   # реестр не тронут
        # пороги — в конфиге: короткий комментарий разрешён, когда min_comment и fact_pattern ослаблены
        self.write("doc.config.toml", self.read("doc.config.toml") + "\n[review]\nmin_comment = 5\nfact_pattern = '.'\n")
        rc, out, err = self.run_doc("approve-batch", ".doctool/review/s.json")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("применено 2", out)

    def test_lint_endpoint_errors(self):
        """Коды ошибок — у всех изменяющих эндпоинтов каталога (любой статус), не только у новых."""
        i = "specs/billing/interfaces.md"
        rc, out, _ = self.run_doc("lint")
        self.assertEqual((rc, out.strip()), (0, "пробелов нет"))
        self.edit(i, "404 — нет счёта, 409 — уже отправлен", "ошибки: —")
        rc, out, _ = self.run_doc("lint", "billing")
        self.assertIn("POST /invoices/{id}/send: нет кодов ошибок", out)
        self.edit(i, "ошибки: —", "404 — нет счёта")
        rc, out, _ = self.run_doc("lint")
        self.assertEqual((rc, out.strip()), (0, "пробелов нет"))
        self.edit(i, "400 — период больше `export.max_rows` строк", "—")                       # GET ошибок не требует
        rc, out, _ = self.run_doc("lint")
        self.assertEqual((rc, out.strip()), (0, "пробелов нет"))
        self.assertIn("проверяет lint", (self.tmp / "rules/api.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("sh"), "нет sh")
    def test_launcher_sh(self):
        """tools/doc запускается как скрипт sh (Git Bash, Linux, macOS), не завися от shebang на python."""
        head = self.doc.read_text(encoding="utf-8").splitlines()[:2]
        self.assertEqual(head[0], "#!/usr/bin/env sh")
        self.assertIn("python3 python py", head[1])
        self.assertIn("sys.version_info < (3, 10)", head[1])
        self.assertTrue((self.tmp / "tools" / "doc.cmd").is_file())
        e = dict(os.environ, PYTHONIOENCODING="utf-8")
        e.pop("DOC_ROOT", None)
        r = subprocess.run(["sh", str(self.doc), "--root", str(self.ex), "check"], cwd=str(self.ex), capture_output=True, env=e)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))
        self.assertIn("замечаний нет", r.stdout.decode("utf-8"))
        r = subprocess.run([sys.executable, str(self.doc), "--root", str(self.ex), "check"], cwd=str(self.ex), capture_output=True, env=e)
        self.assertEqual(r.returncode, 0)                                                      # и как python-скрипт

    # ---------- расход токенов ----------
    @unittest.skipUnless(HAS_YAML, "нет PyYAML")
    def test_cost(self):
        rc, out, _ = self.run_doc("cost")
        self.assertEqual(rc, 0)
        self.assertIn("| review-batch | документ | 1,0 тыс. – 1,5 тыс. | 1,4 тыс. | ✅ |", out)
        self.assertIn("все замеры в допуске", out)
        rc, out, _ = self.run_doc("cost", "add", "review-batch", "5k+1k", "4", "документ", "--model", "Sonnet", "--note", "пачка 2")
        self.assertEqual(rc, 0)
        self.assertIn("1,5 тыс. на единицу", out)                                         # 6000 / 4; ожидание унаследовано
        self.assertIn("— ✅", out)
        rc, out, _ = self.run_doc("cost", "add", "review-batch", "30k", "4", "документ")    # 7,5 тыс. > 1,5 тыс. × 2
        self.assertIn("⚠️ расхождение >2×", out)
        self.assertIn("остановиться и разобрать", out)
        rc, out, _ = self.run_doc("cost")
        self.assertIn("⚠️ вне допуска: review-batch", out)
        page = self.read("token-costs.md")                                                 # страница перегенерирована
        self.assertIn("| 2026", page)
        self.assertIn("⚠️ расхождение >2×", page)
        self.assertIn("пачка 2", page)
        self.assert_clean()
        self.edit("data/costs.yaml", "tokens: 250", "tokens: 260")                         # журнал правлен руками — страница отстала
        rc, out, _ = self.run_doc("check")
        self.assertIn("расход: token-costs.md отстаёт от data/costs.yaml", out)
        rc, out, _ = self.run_doc("cost", "md")
        self.assertIn("обновлено", out)
        self.assert_clean()
        rc, _, err = self.run_doc("cost", "add", "new-process", "10k", "1", "документ")
        self.assertEqual(rc, 1)
        self.assertIn("укажи --method", err)
        rc, _, err = self.run_doc("cost", "add", "new-process", "10k", "1", "документ", "--method", "approvals.md#нет-якоря")
        self.assertIn("нет якоря #нет-якоря", err)
        rc, out, err = self.run_doc("cost", "add", "new-process", "10k", "1", "документ", "--method", "change-process.md#bulk", "--expect", "5-20")
        self.assertEqual(rc, 0, err)
        self.assertIn("ожидание 5,0 тыс. – 20,0 тыс. — ✅", out)
        self.assert_clean()

    # ---------- механика до ролей: новые правила lint ----------
    def test_lint_mechanics(self):
        t = "tasks/1.1-invoice-export.md"
        self.edit(t, "(R-BIL-1 «Основной путь»)", "(R-BIL-1)")
        self.edit(t, " (R-BIL-2 «Почтовый сервис недоступен»)", "")                         # R-BIL-2 не покрыт приёмкой
        self.edit(t, "| 1–2 дн |", "| 3–4 дн |")                                            # 6 ч вне 3–4 дн
        self.edit(t, "(ОВ-1)", "(ОВ-7)")                                                    # нет такого открытого вопроса
        self.edit("specs/billing/interfaces.md", "| Почтовый сервис недоступен | счёт не доставлен |",
                  "| Почтовый сервис недоступен | счёт не доставлен | таймаут I1 | повтор |\n| Диск заполнен | экспорт не создан |")
        self.edit("specs/billing/traceability.md", "| интерфейс I1 | [1.1](../../tasks/1.1-invoice-export.md) | 3 |",
                  "| интерфейс I1 | [1.1](../../tasks/1.1-invoice-export.md) | 4 |")
        b = "components/billing/blocks/exporter.md"
        self.edit(b, "| Требования | R-BIL-1, R-BIL-2 |", "| Требования | R-BIL-1 |")
        self.edit(b, "затем «не отправлен» |", "затем «не отправлен» (R-BIL-2) |")
        self.edit("deploy/billing.md", "Миграции обратимые; откат", "Откат")
        self.edit("specs/billing/interfaces.md", "| `GET /invoices/export` |", "| `GET /invoices/csv` |")     # плагин проекта
        self.edit("specs/billing/interfaces.md", "| платформа | конфиг | 10000 |", "| платформа | values | 10000 |")
        rc, out, _ = self.run_doc("lint", "billing")
        self.assertEqual(rc, 0)
        for s in ("задача 1.1: приёмка и подзадачи не покрывают R-BIL-2", "подзадачи 6 ч ≈ 0.8 дн вне оценки шапки 3–4 дн",
                  "открытый пункт ссылается на ОВ-7", "строка FMEA «Диск заполнен» без сценария отказа",
                  "RTM R-BIL-2 — «Сценариев» 4, в спеке 3", "блок «Экспорт счетов»: requirement R-BIL-2 названы в карточке, но не в поле «Требования»",
                  "deploy/billing.md: «Обновление и откат» без миграций",
                  "GET /invoices/export нет в каталоге API", "нет «Пример конфигурации», хотя параметры в values: export.max_rows"):
            self.assertIn(s, out)


if __name__ == "__main__":
    unittest.main()
