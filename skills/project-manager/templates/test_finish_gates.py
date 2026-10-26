"""The finish gates on a scratch git repository. `python plan/test_finish_gates.py` (standard library, ~10 s).

Each case builds what the gate reads (a card, test sources, Logs/ results, main and a task branch) in a temp repo
and calls the plan/finish.py function, so no worktree, board or build is involved. The cases set finish.py's
optional settings themselves (and restore them), so they test the gates whatever this project configured; the ones
that need a tier script write a tiny fake one into the temp repo. A new finish gate adds a case.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True
PLAN = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_finish_under_test", PLAN / "finish.py")
finish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finish)

#: A test-split module honouring the contract in finish.py's docstring, over C#-style test files `tests/<X>Tests.cs`.
FAKE_TIERS = r'''
import re
from pathlib import Path

FULL = "full"
ORDER = ["core", "body", "ui", "look", "widget"]
SMOKE_TIERS = {"core"}
TIERS = {"core": [], "body": [r"\.WidgetMeshTests\."], "ui": [r"\.UiTests\."], "look": [],
         "widget": [r"\.WidgetTests\."]}
CLASS_TIER = {"WidgetTests": "widget", "WidgetMeshTests": "body", "UiTests": "ui"}
PATH_TIER = [(r"^src/data/widgets/", ["body", "widget"]), (r"^src/(shaders|materials)/", ["look"]),
             (r"^src/ui/", ["ui"]), (r"^src/core/", [FULL]), (r"^assets/levels/", [])]
DECL = re.compile(r"\[Test\][^{;]*?void (\w+)\(", re.S)


def tiers_for(paths):
    out = set()
    for p in paths:
        m = re.match(r"^tests/(\w+Tests)\.cs$", p)
        if m:
            out.add(CLASS_TIER.get(m.group(1), FULL))
            continue
        p = p[:-5] if p.endswith(".meta") else p
        for pat, tiers in PATH_TIER:
            if re.match(pat, p):
                out.update(tiers)
                break
        else:
            if p.startswith(("src/", "assets/")):
                out.add(FULL)
    return [FULL] if FULL in out else [t for t in ORDER if t in out]


def source_tests(root):
    return {f.stem: DECL.findall(f.read_text(encoding="utf-8")) for f in sorted(Path(root, "tests").glob("*Tests.cs"))}


def tier_tests(tier, root):
    names = {f"Demo.{c}.{n}" for c, ns in source_tests(root).items() for n in ns}
    return names if tier == FULL else {t for t in names if any(re.search(f, t) for f in TIERS[tier])}


def untiered(root, classes):
    frags = [f for fs in TIERS.values() for f in fs]
    out = []
    for c, ns in source_tests(root).items():
        if c not in classes:
            continue
        if c not in CLASS_TIER:
            out.append(f"{c} (no CLASS_TIER row)")
        out += [f"{c}.{n}" for n in ns if not any(re.search(f, f"Demo.{c}.{n}") for f in frags)]
    return out


def command_for(tiers):
    return "run-tests --tier " + ",".join(tiers)
'''

#: Settings every case starts from; a case overrides what it tests.
BASE = dict(BUILD_PATHS=["src", "tests", "assets", "packages"], RETEST_ALWAYS=["packages/"], TEST_CMD="run-tests",
            INERT_EXT=(".png", ".meta", ".scene"), TIERS_SCRIPT=None,
            TEST_FILE_RE=r"^tests/(\w+Tests)\.cs$", TEST_DECL_RE=r"\[Test\][^{;]*?void (\w+)\(",
            TEST_NAME="Demo.{cls}.{name}", TEST_RESULTS=("Logs/tests.xml", "Logs/test_runs/*.xml"),
            RENDER_DIRS=(), WIKI_TOOL=None, POST_MERGE_HOOK=None, STOP_CMD=None,
            SHARED_DIRS=[], SEEDED_DIRS=[], EMPTY_DIRS=[])


def sh(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {p.stderr}")
    return p.stdout.strip()


def write(repo: Path, rel: str, text: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cls(name: str, *methods: str) -> str:
    ob, cb = "{", "}"       # not doubled braces: Mode A greps the templates for leftover placeholders
    body = "".join(f"        [Test] public void {m}() {ob} {cb}\n" for m in methods)
    return f"namespace Demo {ob}\n    public class {name} {ob}\n{body}    {cb}\n{cb}\n"


def results(repo: Path, rel: str, *names: str) -> None:
    """An NUnit 3 results file with these test-case full names."""
    cases = "".join(f'<test-case fullname="{escape(n, {chr(34): "&quot;"})}" result="Passed" />' for n in names)
    write(repo, rel, f'<test-run><test-suite>{cases}</test-suite></test-run>')


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        saved = {k: getattr(finish, k) for k in BASE}
        self.addCleanup(lambda: [setattr(finish, k, v) for k, v in saved.items()])
        for k, v in BASE.items():
            setattr(finish, k, v)
        self.repo = Path(tempfile.mkdtemp(prefix="finish_gates_"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        sh(self.repo, "init", "-q", "-b", "main")
        write(self.repo, ".gitignore", "Logs/\n")
        write(self.repo, "tests/WidgetTests.cs", cls("WidgetTests", "Old_A"))
        write(self.repo, "tests/WidgetMeshTests.cs", cls("WidgetMeshTests", "Mesh_A"))
        write(self.repo, "tests/UiTests.cs", cls("UiTests", "Ui_A"))
        write(self.repo, "src/shaders/basic.shader")
        write(self.repo, "plan/fake_tiers.py", FAKE_TIERS)
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "base")

    def tiers_on(self) -> None:
        finish.TIERS_SCRIPT = str(self.repo / "plan" / "fake_tiers.py")

    # card reading ---------------------------------------------------------------------------------------------
    def test_files_bullet_with_two_paths_stages_both(self) -> None:
        card = ("## Files\n\n"
                "- `tests/WidgetMeshTests.cs`, `tests/WidgetStrapTests.cs`\n"
                "- `spec.md`, `docs/research/study.md` (calls `Type.Member`, not `SOME_FLAG`)\n"
                "- `plan/fake_tiers.py` (the table; not `KNOWN_FAILURES`, not `<placeholder>.cs`)\n"
                "- `./tools\\wiki.py` (`card_files`), `assets/data/*Bra_Micro*`\n\n## Spec\n")
        want = ["tests/WidgetMeshTests.cs", "tests/WidgetStrapTests.cs", "spec.md", "docs/research/study.md",
                "plan/fake_tiers.py", "tools/wiki.py", "assets/data/*Bra_Micro*"]
        self.assertEqual(finish.card_files(card), want)
        # a wiki tool that reads Files keeps the same rule
        tool = PLAN.parent / (finish.WIKI_TOOL or "-")
        if not finish.WIKI_TOOL or not tool.is_file():
            return
        wspec = importlib.util.spec_from_file_location("_wiki_under_test", tool)
        wiki = importlib.util.module_from_spec(wspec)
        sys.modules[wspec.name] = wiki
        wspec.loader.exec_module(wiki)
        if hasattr(wiki, "card_files"):
            write(self.repo, "card.md", card)
            self.assertEqual(wiki.card_files(self.repo / "card.md"), want)

    def test_real_cards_parse_to_paths(self) -> None:
        # every card of this repository: Files yields only path-shaped entries (no spaces, no placeholders)
        cards = sorted((PLAN / "tasks").glob("T[0-9][0-9]*-*.md")) if (PLAN / "tasks").is_dir() else []
        if not cards:
            self.skipTest("no cards in plan/tasks")
        for card in cards:
            for p in finish.card_files(card.read_text(encoding="utf-8", errors="replace")):
                self.assertFalse(" " in p or p.startswith("<"), f"{card.name}: {p!r}")

    def test_open_questions_and_coauthor(self) -> None:
        card = ("## Questions for the owner\n\n1. Which size? Assumed 2.\n   Answer: 3.\n"
                "2. Which colour? Assumed red.\n\n## Result\n")
        self.assertEqual(finish.open_questions(card), ["2. Which colour? Assumed red."])
        self.assertEqual(finish.coauthor_trailer("Co-Authored-By: A <a@b>"), "Co-Authored-By: A <a@b>")
        self.assertEqual(finish.coauthor_trailer("A <a@b>"), "Co-Authored-By: A <a@b>")
        self.assertEqual(finish.coauthor_trailer(""), "")

    # reproduce gate -------------------------------------------------------------------------------------------
    def test_reproduce_gate_lines_then_captures(self) -> None:
        acc = "## Acceptance test\n\nRender budget: three cheap looks.\nDefect view: the front camera.\n\n"
        block = ("## Result\n\n### Reproduce\nA: shot_a\nRequirement: the seam is gone.\nGate: seam test, red on A.\n"
                 "Assumed: untested.\nB: <same views at the end>\n\ndone.\n")
        self.assertTrue(finish.needs_reproduce(acc))
        self.assertFalse(finish.needs_reproduce(acc.replace("three cheap looks", "none")))
        self.assertEqual(finish.reproduce_gate(acc + block, self.repo), ["`B:` line missing or still the placeholder"])
        filled = acc + block.replace("<same views at the end>", "shot_b")
        self.assertEqual(finish.reproduce_gate(filled, self.repo), [])          # no RENDER_DIRS: lines only
        finish.RENDER_DIRS = ("shots", "shots/_runs/*")
        write(self.repo, "shots/_runs/0001/shot_a_front.png")
        self.assertEqual(len(finish.reproduce_gate(filled, self.repo)), 1)     # shot_b never taken
        write(self.repo, "shots/shot_b.jpg")
        self.assertEqual(finish.reproduce_gate(filled, self.repo), [])

    # coverage gate --------------------------------------------------------------------------------------------
    def test_coverage_gate_names_new_tests_without_a_result(self) -> None:
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "tests/WidgetTests.cs", cls("WidgetTests", "Old_A", "New_B"))
        self.assertEqual(finish.coverage_gate(self.repo, "main"), ["Demo.WidgetTests.New_B (tier ?)"])
        self.tiers_on()
        self.assertEqual(finish.coverage_gate(self.repo, "main"), ["Demo.WidgetTests.New_B (tier widget)"])
        results(self.repo, "Logs/test_runs/run2.xml", 'Demo.WidgetTests.New_B("x",1.0f)')
        self.assertEqual(finish.coverage_gate(self.repo, "main"), [])
        # JUnit results count too
        write(self.repo, "tests/UiTests.cs", cls("UiTests", "Ui_A", "Ui_New"))
        write(self.repo, "Logs/tests.xml", '<testsuite><testcase classname="Demo.UiTests" name="Ui_New[p0]"/></testsuite>')
        self.assertEqual(finish.coverage_gate(self.repo, "main"), [])
        finish.TEST_RESULTS = ()                                                # off: holds nothing
        self.assertEqual(finish.coverage_gate(self.repo, "main"), [])

    # tier gates -----------------------------------------------------------------------------------------------
    def test_tiers_gate_names_the_tier_no_run_covers(self) -> None:
        self.tiers_on()
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "tests/WidgetTests.cs", cls("WidgetTests", "Old_A", "New_B"))
        write(self.repo, "src/data/widgets/gear.asset")          # uncommitted: gates run before staging
        tiers, gaps = finish.tiers_gate(self.repo, "main")
        self.assertEqual(tiers, ["body", "widget"])
        self.assertEqual([g.split(":")[0] for g in gaps], ["body", "widget"])
        # a body-only run leaves the widget tier uncovered
        results(self.repo, "Logs/tests.xml", "Demo.WidgetMeshTests.Mesh_A")
        self.assertEqual([g.split(":")[0] for g in finish.tiers_gate(self.repo, "main")[1]], ["widget"])
        # the widget tier in a second launch, one test parameterized: covered
        results(self.repo, "Logs/test_runs/tests_2.xml", "Demo.WidgetTests.Old_A", 'Demo.WidgetTests.New_B("x",1.0f)')
        self.assertEqual(finish.tiers_gate(self.repo, "main")[1], [])

    def test_tiers_gate_full_run_covers_every_tier_and_tooling_needs_none(self) -> None:
        self.tiers_on()
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "plan/x.py")
        self.assertEqual(finish.tiers_gate(self.repo, "main"), ([], []))
        write(self.repo, "src/core/card.cs")
        self.assertEqual(finish.tiers_gate(self.repo, "main")[0], ["full"])
        results(self.repo, "Logs/tests.xml", "Demo.WidgetTests.Old_A", "Demo.WidgetMeshTests.Mesh_A", "Demo.UiTests.Ui_A")
        self.assertEqual(finish.tiers_gate(self.repo, "main")[1], [])

    def test_tiers_gate_tolerates_a_test_main_brought_after_the_run(self) -> None:
        self.tiers_on()
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        names = [f"Ui_{i}" for i in range(10)]
        write(self.repo, "src/ui/defaults.cs")                                   # tier ui
        results(self.repo, "Logs/tests.xml", *[f"Demo.UiTests.{n}" for n in names[:9]])
        write(self.repo, "tests/UiTests.cs", cls("UiTests", *names))
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "as if merged from main")
        self.assertEqual(finish.tiers_gate(self.repo, "main")[1], [])            # 9 of 10: the tier ran
        results(self.repo, "Logs/tests.xml", *[f"Demo.UiTests.{n}" for n in names[:5]])
        self.assertEqual(len(finish.tiers_gate(self.repo, "main")[1]), 1)       # 5 of 10: a filtered run, not the tier

    def test_tier_gates_off_without_a_tier_script(self) -> None:
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "src/core/card.cs")
        write(self.repo, "tests/NewTests.cs", cls("NewTests", "Brand_New"))
        self.assertEqual(finish.tiers_gate(self.repo, "main"), ([], []))
        self.assertEqual(finish.untiered_gate(self.repo, "main"), [])

    def test_untiered_gate_only_for_classes_the_branch_touches(self) -> None:
        self.tiers_on()
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        self.assertEqual(finish.untiered_gate(self.repo, "main"), [])
        write(self.repo, "tests/NewTests.cs", cls("NewTests", "Brand_New"))
        self.assertEqual(finish.untiered_gate(self.repo, "main"),
                         ["NewTests (no CLASS_TIER row)", "NewTests.Brand_New"])
        # an untiered class the branch does not touch holds nothing
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "new class")
        sh(self.repo, "checkout", "-q", "main")
        sh(self.repo, "merge", "-q", "task/T1")
        sh(self.repo, "checkout", "-q", "-b", "task/T2")
        self.assertEqual(finish.untiered_gate(self.repo, "main"), [])

    # MOVED ----------------------------------------------------------------------------------------------------
    def test_moved_ignores_inert_paths_and_names_them(self) -> None:
        self.tiers_on()
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "src/shaders/basic.shader", "card\n")                  # the card: tier look
        sh(self.repo, "commit", "-q", "-am", "card")
        sh(self.repo, "checkout", "-q", "main")
        for rel in ("assets/tex/logo.png", "assets/tex/logo.png.meta", "assets/scenes/main.scene",
                    "src/ui/foo_tab.cs"):                                         # images, meta, scene; ui only
            write(self.repo, rel)
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "data")
        before = sh(self.repo, "rev-parse", "HEAD")
        inert: list[str] = []
        self.assertEqual(finish.moved_retest(self.repo, before, "T1", inert)[0], "")
        self.assertEqual(sorted(inert), ["assets/scenes/main.scene", "assets/tex/logo.png",
                                         "assets/tex/logo.png.meta", "src/ui/foo_tab.cs"])
        write(self.repo, "src/materials/logo.mat")                               # shares `look`
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "material")
        inert = []
        retest, theirs = finish.moved_retest(self.repo, sh(self.repo, "rev-parse", "HEAD"), "T1", inert)
        self.assertEqual(retest, "run-tests --tier look")
        self.assertNotIn("src/materials/logo.mat", inert)
        self.assertEqual(len(theirs), 5)
        # packages/ always re-tests, even a card whose paths map to no tier (then `full`)
        sh(self.repo, "checkout", "-q", "-b", "task/T2", "HEAD~2")
        write(self.repo, "assets/levels/intro.prefab", "card\n")          # tier []: no test reads it
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "card 2")
        sh(self.repo, "checkout", "-q", "main")
        self.assertEqual(finish.moved_retest(self.repo, sh(self.repo, "rev-parse", "HEAD"), "T2")[0], "")
        write(self.repo, "packages/manifest.json")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "packages")
        self.assertIn("--tier full", finish.moved_retest(self.repo, sh(self.repo, "rev-parse", "HEAD"), "T2")[0])

    def test_moved_without_a_tier_script_is_any_live_build_path(self) -> None:
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "src/shaders/basic.shader", "card\n")
        sh(self.repo, "commit", "-q", "-am", "card")
        sh(self.repo, "checkout", "-q", "main")
        write(self.repo, "docs/notes.md")                                        # not a build path
        write(self.repo, "assets/tex/logo.png")                                  # inert
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "docs and an image")
        inert: list[str] = []
        self.assertEqual(finish.moved_retest(self.repo, sh(self.repo, "rev-parse", "HEAD"), "T1", inert),
                         ("", ["assets/tex/logo.png"]))
        self.assertEqual(inert, ["assets/tex/logo.png"])
        write(self.repo, "src/ui/foo_tab.cs")                                    # no tiers to say it is unrelated
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "ui")
        self.assertEqual(finish.moved_retest(self.repo, sh(self.repo, "rev-parse", "HEAD"), "T1")[0], "run-tests")

    # MAIN and cleanup -----------------------------------------------------------------------------------------
    def test_untracked_file_in_main_blocks_only_when_the_branch_adds_it(self) -> None:
        sh(self.repo, "checkout", "-q", "-b", "task/T1")
        write(self.repo, "notes/plan.md", "branch\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "card")
        sh(self.repo, "checkout", "-q", "main")
        write(self.repo, "grilling/owner.md")                                    # the owner's own notes
        self.assertEqual(finish.main_blockers(self.repo, "task/T1"), [])
        write(self.repo, "notes/plan.md", "main\n")
        self.assertEqual(finish.main_blockers(self.repo, "task/T1"), ["?? notes/plan.md (the branch adds it too)"])
        write(self.repo, "src/shaders/basic.shader", "edited\n")                 # a tracked change always blocks
        self.assertEqual(len(finish.main_blockers(self.repo)), 1)

    def test_cleanup_removes_links_without_following_them(self) -> None:
        outside = self.repo / "main_cache"
        write(self.repo, "main_cache/keep.bin")
        wt = self.repo / "wt"
        wt.mkdir()

        def link(at: Path, target: Path) -> bool:
            if sys.platform == "win32":
                return subprocess.run(["cmd", "/c", "mklink", "/J", str(at), str(target)],
                                      capture_output=True).returncode == 0
            os.symlink(target, at, target_is_directory=True)
            return True

        if not link(wt / "cache", outside):
            self.skipTest("cannot create a directory link here")
        write(self.repo, "wt/seeded/a.bin")
        self.assertTrue(link(wt / "seeded" / "inner", outside))                 # a link inside a seeded copy
        (wt / "build").mkdir()
        finish.SHARED_DIRS, finish.SEEDED_DIRS, finish.EMPTY_DIRS = ["cache"], ["seeded"], ["build", "absent"]
        finish.remove_junctions(wt)
        self.assertEqual(sorted(p.name for p in wt.iterdir()), [])
        self.assertTrue((outside / "keep.bin").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=1)
