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
        self.assertIn("блоков 1, карточка заполнена полностью у 1", out)
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


if __name__ == "__main__":
    unittest.main()
