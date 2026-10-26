"""Finish a task: stage, commit, merge, verify, mark done, clean up. One command, one turn.

Usage (run from the worktree or from MAIN; both work):

    python plan/finish.py <Txx>                       the whole tail of the protocol
    python plan/finish.py <Txx> --add <path> ...      stage these on top of the card's Files
    python plan/finish.py <Txx> --checkpoint          commit work so far, keep the row, stop
    python plan/finish.py <Txx> --blocked "<reason>"  commit what exists, mark the row blocked
    python plan/finish.py <Txx> --dry-run             what would be staged and what the tier gates say; touch nothing
    python plan/finish.py <Txx> --release-lock        drop this card's main lock and exit
    python plan/finish.py <Txx> --verified <hash>     legacy: a merge already re-tested in MAIN by hand:
                                                      mark done and clean up

Replaces the hand-run tail of the protocol: stage by path, commit, return to MAIN, check MAIN clean,
merge --no-ff, the merge-base diff that decides UNCHANGED vs MOVED, `board.py done`, removing the
worktree's links, `worktree remove`, `branch -D`. That was 15 to 20 agent turns, each billed the full
context of a card that is by then 250k to 350k tokens long, i.e. roughly three to four million cached
tokens of pure bookkeeping per card (measured on one project). Here it is one turn, two when the merge
lands on moved code.

The safety rules are kept, not relaxed:

- staging is by explicit path only, taken from the card's own "Files" section; there is no
  `git add -A` anywhere in this file, and a leftover modified path aborts the commit;
- `plan/BOARD.md` is never staged on a branch;
- questions gate the merge: a card with unanswered questions refuses to merge and tells the
  agent to set the row `waiting` instead;
- every board transition still goes through `plan/board.py`, which re-checks its own rules.

Gates, in order, before anything is staged (a refused finish leaves the branch as it was): open questions, the
Result placeholder, the `### Reproduce` block (cards with a visible result), new tests without a result (coverage),
test classes no tier reaches and tiers no run covers (tiers), wiki pages behind the code (wiki). Each optional gate
is off until its setting below is filled, and has a `--skip-*` flag for when the tool is wrong (file a suggestion).

Serial mode (`{{ENV_PREFIX}}_IN_MAIN=1`, or detected by `board.in_main`): the card was worked directly in MAIN, so
there is no merge, MOVED check or cleanup; the commit is on main and the row goes done.

Tier script contract (`TIERS_SCRIPT`). A project that splits its test suite into tiers writes a module with:

    FULL: str                                    the tier name that runs every test (e.g. "full")
    ORDER: list[str]                             every other tier name, in run order
    tiers_for(paths: list[str]) -> list[str]     tiers the repo-relative paths need, in ORDER; [FULL] for shared
                                                 code; [] when no test can be affected
    tier_tests(tier: str, root: str) -> set[str] the test names (as ran_tests() reports them) a run of `tier`
                                                 gives a result for, read from the sources under `root`
    command_for(tiers: list[str]) -> str         the command line that runs these tiers
    untiered(root: str, classes: set[str]) -> list[str]
                                                 one line per test in these classes that no tier reaches
and optionally:
    TIERS: dict[str, list[str]]                  tier -> regex fragments matched on test names (names the tier of
                                                 a test the coverage gate reports; without it: FULL)
    SMOKE_TIERS: set[str]                        tiers every tier includes; never a reason to re-test
    OUTSIDE_FULL: set[str]                       tiers FULL does not include (a slow sweep), kept when shared

Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# --- project settings ---------------------------------------------------------

#: Gitignored directories start.py made in the worktree, in its three kinds. A link to MAIN's copy (SHARED) is
#: removed as a link, never followed: a recursive delete through it would erase MAIN's cache. A real directory
#: (SEEDED copy, EMPTY dir, or a SHARED name the worktree got its own of) is the worktree's own and is deleted,
#: again without following any link inside it. Keep these in step with start.py.
SHARED_DIRS = [{{SHARED_DIRS}}]
SEEDED_DIRS = [{{SEEDED_DIRS}}]
EMPTY_DIRS = [{{EMPTY_DIRS}}]
SHARED_FLAG = "{{SHARED_FLAG}}"

#: Paths whose change on main can change a test verdict. A merge that only brought other paths (plan/, cards,
#: docs, tooling) is UNCHANGED: the branch's own acceptance run still proves the merged state. Nothing runs the
#: build in MAIN at a merge: re-proving there what the worktree had just tested queued every finish on the board
#: behind MAIN's own long runs while holding the main lock.
BUILD_PATHS = [{{BUILD_PATHS}}]
#: Build-path prefixes whose change on main always makes the card re-test (dependency manifests, project settings:
#: they change every build).
RETEST_ALWAYS = [{{RETEST_ALWAYS}}]
#: The test command printed for a MOVED card when no tier script exists.
TEST_CMD = "{{TEST_CMD}}"

#: Extensions of paths main brings that never move a test verdict on their own (lower case, with the dot), so they
#: set no MOVED. Measured on one project: a card re-ran its full tier four times (75 min) because other cards merged
#: images, sidecar files and a scene. Only safe when a regression net on main runs the tests that read such files.
#: Empty = every build path counts. Example: (".png", ".jpg", ".tga", ".psd", ".meta").
INERT_EXT: tuple[str, ...] = ()

#: Staged with every card without a line in its Files section, as git pathspecs. Example: ["wiki/*.md"] when every
#: card that changes code updates the wiki pages citing it (listing them per card is a refusal waiting to happen).
ALWAYS_STAGED: list[str] = []

#: The test-split module (see the docstring for its contract), repo-relative. None = no tier gates, and MOVED is
#: any build-path change. Example: "plan/test_tiers.py".
TIERS_SCRIPT: str | None = None

#: Coverage gate: tests the branch adds must have a result in a run logged in the worktree. Off until all three of
#: TEST_FILE_RE, TEST_DECL_RE and TEST_RESULTS are set; TEST_FILE_RE also names the classes untiered_gate checks.
#: TEST_FILE_RE matches a repo-relative test source path, group 1 = its class name.
#: TEST_DECL_RE finds each test declaration in that file, group 1 = the test's name.
#: TEST_NAME builds the name a results file reports for it.
#: TEST_RESULTS are globs, relative to the worktree, of results files (NUnit 3 `test-case fullname=` or JUnit
#: `testcase classname= name=`; arguments after "(" or "[" are cut).
#: Example (C#, NUnit 3 results):
#:   TEST_FILE_RE = r"^Assets/Game/Tests/Editor/(\w+Tests)\.cs$"
#:   TEST_DECL_RE = r"\[(?:Test|TestCase)\b[^\]]*\][^{;]*?(?:void|IEnumerator) (\w+)\("
#:   TEST_NAME = "Game.Tests.{cls}.{name}"
#:   TEST_RESULTS = ("Logs/tests.xml", "Logs/test_runs/*.xml")
TEST_FILE_RE: str | None = None
TEST_DECL_RE: str | None = None
TEST_NAME = "{cls}.{name}"
TEST_RESULTS: tuple[str, ...] = ()

#: Reproduce gate: folders (globs, relative to the worktree) where the A and B captures named in the card's
#: `### Reproduce` block must exist as `<name>*.<image ext>`. Empty = the gate checks only that the five lines are
#: filled. Example: ("Build/Shots", "Build/Shots/_review", "Build/Shots/_runs/*").
RENDER_DIRS: tuple[str, ...] = ()
RENDER_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

#: Wiki tool, repo-relative. When set, `python <tool> check --changed <main>` runs in the worktree and its lines
#: starting `BROKEN` or containing `[NOT edited]` hold the merge (exit 0 or 1; anything else holds nothing).
#: None = no wiki gate.
WIKI_TOOL: str | None = None

#: Called after a merge (and after a serial-mode commit), before the worktree goes, as
#: `fn(card_text, reproduce_block, task, title, merge_hash, render_dirs) -> iterable of lines to print`.
#: "path/to/module.py:function", repo-relative. A failure prints a warning and never stops the finish: the merge
#: has already happened. Example: "Tools/bugs.py:record_fix" (marks the bug reports a card fixed). None = off.
POST_MERGE_HOOK: str | None = None

#: Command (argv, run in the worktree) that stops a long-lived process holding the worktree open (a warm editor,
#: a dev server) before its links and folders go. Runs only when STOP_IF_EXISTS (worktree-relative) exists, the
#: cheap sign that something is running. None = nothing to stop.
STOP_CMD: list[str] | None = None
STOP_IF_EXISTS: str | None = None

#: Co-authorship trailer appended to task commits. The agent passes --coauthor with the line
#: its own harness gave it; {{ENV_PREFIX}}_COAUTHOR in the environment is the fallback.
DEFAULT_COAUTHOR = os.environ.get("{{ENV_PREFIX}}_COAUTHOR", "")

# Importing plan/board.py would drop plan/__pycache__/ into MAIN, and an untracked directory
# there makes `git status --porcelain` non-empty, which is exactly what board.py refuses to
# claim against. Never write bytecode from these scripts.
sys.dont_write_bytecode = True

MODULE_DIR = Path(__file__).resolve().parent
REPO_DIR = MODULE_DIR.parent


# --- plumbing -----------------------------------------------------------------


def git(*args: str, cwd: Path, check: bool = True) -> "subprocess.CompletedProcess[str]":
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{p.stdout}{p.stderr}")
    return p


def find_main() -> Path:
    """MAIN's working tree, whether this runs from MAIN or from a worktree of it."""
    p = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("not inside a git repository")
    common = Path(p.stdout.strip())
    if not common.is_absolute():
        common = (Path.cwd() / common).resolve()
    return common.parent


def load_board(main: Path):
    spec = importlib.util.spec_from_file_location("_board", main / "plan" / "board.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot import plan/board.py from MAIN")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # board.py uses @dataclass, which looks its class up here
    spec.loader.exec_module(mod)
    return mod


def board_cmd(main: Path, *args: str) -> int:
    p = subprocess.run([sys.executable, str(main / "plan" / "board.py"), *args],
                       cwd=main, capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
    return p.returncode


def tail(text: str, n: int = 25) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def _load_file(rel: str, name: str):
    """Import a repo-relative (or absolute) Python file as module `name`."""
    path = REPO_DIR / rel
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod        # a module using @dataclass looks itself up here
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))   # so it can import its own siblings
    spec.loader.exec_module(mod)
    return mod


# --- card reading -------------------------------------------------------------


def section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


#: A backticked token that names a path: it has a `/`, or ends in a lowercase extension (`notes.md`, `x.asset`),
#: and no space. `Type.Member`, `CONSTANTS` and commands are not paths. A wiki tool that reads Files keeps the same rule.
_PATHLIKE = re.compile(r"^[^\s<>]*(/[^\s<>]*|\.[a-z][a-z0-9]{0,6})$")


def card_files(text: str) -> list[str]:
    """Repo-relative paths from the card's Files section: the first backticked token of each bullet, and every later
    one that looks like a path. Only the first used to count, so "`A.py`, `B.py` (why)" staged A.py alone and the
    finish refused B.py as a leftover."""
    paths: list[str] = []
    for line in section(text, "Files").splitlines():
        if not line.lstrip().startswith(("-", "*")):
            continue
        for i, tok in enumerate(re.findall(r"`([^`]+)`", line)):
            p = tok.strip().replace("\\", "/")
            p = p[2:] if p.startswith("./") else p.lstrip("/")
            if not p or p.startswith("<") or (i and not _PATHLIKE.match(p)):
                continue
            if p not in paths:
                paths.append(p)
    return paths


def card_flag(text: str) -> str:
    """The card's `<SHARED_FLAG>: yes|no`. It sits inline in the header line
    ("Model: opus55. Phase: 3. Prerequisites: T82. {{SHARED_FLAG}}: yes."), so this does not anchor to
    the start of a line; the search is capped to the header so later prose cannot match."""
    head = " ".join(text.splitlines()[:15])
    m = re.search(rf"{re.escape(SHARED_FLAG)}:\s*(yes|no)", head, re.I)
    return m.group(1).lower() if m else "no"


def questions(text: str) -> list[str]:
    return [l.strip() for l in section(text, "Questions for").splitlines()
            if re.match(r"^\s*\d+[.)]\s+\S", l)]


def open_questions(text: str) -> list[str]:
    """Questions with no answer recorded under them. A question is answered when its block (its line
    and the lines up to the next numbered question) says "answer" or "unanswered, assumption kept";
    without this the gate refused every card that had ever asked, even after `board.py answered`."""
    out: list[str] = []
    q, answered = None, False
    for line in section(text, "Questions for").splitlines() + ["0. end"]:
        if re.match(r"^\s*\d+[.)]\s+\S", line):
            if q is not None and not answered:
                out.append(q)
            q = line.strip()
            answered = bool(re.search(r"\banswer(ed)?\b|assumption kept", line, re.I))
        elif q is not None and re.search(r"\banswer(ed)?\b|assumption kept", line, re.I):
            answered = True
    return out


#: What a checkpoint leaves for the next context. Overwritten at every checkpoint, never appended to:
#: `start.py --resume` prints this block instead of the Result history, and the code named under Open.
HANDOFF_FORMAT = """\
### Handoff
State: what works now and what does not, with the last numbers.
Next: the next step, concretely enough to start on.
Done when: the measurable target that ends the card (a printed number and its bound, a test that passes).
Open: `Type.Member`, `other_file.py` (the code the next context reads first; `start.py --resume` prints it)
Tried: what was tried and rejected, so it is not tried again."""


#: The first block of every Result (plan/INVOKE.md, "Reproduce"), written before the first edit and completed at
#: the end. finish.py refuses a card with a visible result whose block is not filled (or, with RENDER_DIRS set,
#: whose A and B captures do not exist).
REPRODUCE_FORMAT = """\
### Reproduce
A: <capture names, comma-separated, taken on today's build at the card's Defect view, before the first edit>
Requirement: <one sentence, in {{OWNER}}'s terms, of what must be true when the card is done>
Gate: <the card's number or test, and whether it is bad on A; if it is not, the number cannot gate: name what does>
Assumed: <each cause or number the card hands you, with "measured: ..." or "untested">
B: <capture names of the same views at the end, the ones the Result's claims rest on>"""


def reproduce_block(text: str) -> str:
    """The `### Reproduce` block of the Result, "" when there is none."""
    m = re.search(r"^### Reproduce[^\n]*\n(.*?)(?=^#{2,3} |\Z)", section(text, "Result"), re.S | re.M)
    return m.group(1) if m else ""


def needs_reproduce(text: str) -> bool:
    """A card with a visible result: its Acceptance test has a Render budget line that is not "none" and a
    `Defect view:` line (cards written before the template had them have no view to capture A from, so the gate
    phases in with new cards)."""
    acc = section(text, "Acceptance test")
    m = re.search(r"^Render budget:\s*(.+)$", acc, re.M)
    return bool(m) and not m.group(1).strip().lower().startswith("none") and bool(re.search(r"^Defect view:", acc, re.M))


def render_dirs(worktree: Path) -> list[Path]:
    """The worktree's capture folders (RENDER_DIRS, globs expanded: per-run copies keep B from erasing A)."""
    dirs: list[Path] = []
    for pattern in RENDER_DIRS:
        if any(ch in pattern for ch in "*?["):
            dirs += sorted(d for d in worktree.glob(pattern) if d.is_dir())
        else:
            dirs.append(worktree / pattern)
    return dirs


def missing_renders(worktree: Path, names: str) -> list[str]:
    """Capture names (from an A: or B: line) with no `<name>*<image ext>` under the worktree's RENDER_DIRS."""
    dirs = [d for d in render_dirs(worktree) if d.is_dir()]
    missing = []
    for raw in re.split(r"[,;]", names):
        name = raw.strip().strip("`").split()[0] if raw.strip() else ""
        if not name or name.startswith("<"):
            continue
        if not any(f.suffix.lower() in RENDER_EXT for d in dirs for f in d.glob(f"{name}*")):
            missing.append(name)
    return missing


def reproduce_gate(text: str, worktree: Path) -> list[str]:
    """Why the Reproduce block does not pass: missing lines, placeholders, captures that were never taken."""
    block = reproduce_block(text)
    if not block:
        return ["the Result has no `### Reproduce` block"]
    problems = []
    for key in ("A", "Requirement", "Gate", "Assumed", "B"):
        m = re.search(rf"^{key}:\s*(.*)$", block, re.M)
        if not m or not m.group(1).strip() or m.group(1).strip().startswith("<"):
            problems.append(f"`{key}:` line missing or still the placeholder")
        elif key in ("A", "B") and RENDER_DIRS:
            for name in missing_renders(worktree, m.group(1)):
                problems.append(f"`{key}:` names capture `{name}` but no {name}* image exists under "
                                + ", ".join(RENDER_DIRS))
    return problems


def post_merge_hook(text: str, task: str, title: str, merge_hash: str, worktree: Path) -> None:
    """POST_MERGE_HOOK, after a merge and before the worktree goes. Never stops the finish: the merge has already
    happened."""
    if not POST_MERGE_HOOK:
        return
    try:
        rel, _, fn = POST_MERGE_HOOK.partition(":")
        hook = getattr(_load_file(rel, "_post_merge_hook"), fn)
        for line in hook(text, reproduce_block(text), task, title, merge_hash, render_dirs(worktree)) or ():
            print(line)
    except Exception as e:  # noqa: BLE001 - a hook hiccup must not fail a merged card
        print(f"POST-MERGE warning: {POST_MERGE_HOOK} failed: {e}")


def handoff(text: str) -> str:
    """The `### Handoff` block of the card's Result section, "" when there is none."""
    m = re.search(r"^### Handoff[^\n]*\n(.*?)(?=^#{2,3} |\Z)", section(text, "Result"), re.S | re.M)
    return m.group(1).strip() if m else ""


def wiki_gate(worktree: Path, base: str) -> list[str]:
    """Lines of `WIKI_TOOL check --changed` that hold the merge: a page citing a name that no longer
    exists, or a page that cites code this card changed and was not touched. A missing or failing tool
    holds nothing: the wiki must never be the reason a finished card cannot merge."""
    if not WIKI_TOOL:
        return []
    tool = worktree / WIKI_TOOL
    if not tool.exists():
        return []
    try:
        p = subprocess.run([sys.executable, str(tool), "check", "--changed", base], cwd=worktree,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode not in (0, 1):
        return []
    return [line for line in p.stdout.splitlines() if line.startswith("BROKEN") or "[NOT edited]" in line]


# --- test gates ---------------------------------------------------------------


_TIERS_CACHE: dict[str, object] = {}


def _test_tiers():
    """The TIERS_SCRIPT module (raises when it is unset or cannot be imported; callers treat that as "no gate")."""
    if not TIERS_SCRIPT:
        raise LookupError("no TIERS_SCRIPT")
    if TIERS_SCRIPT not in _TIERS_CACHE:
        _TIERS_CACHE[TIERS_SCRIPT] = _load_file(TIERS_SCRIPT, "_test_tiers")
    return _TIERS_CACHE[TIERS_SCRIPT]


def _coverage_on() -> bool:
    return bool(TEST_FILE_RE and TEST_DECL_RE and TEST_RESULTS)


def branch_paths(worktree: Path, base: str) -> list[str]:
    """Every path the branch changes against its merge-base with `base`: commits, uncommitted edits, untracked files
    (the gates run before anything is staged)."""
    mb = git("merge-base", base, "HEAD", cwd=worktree, check=False).stdout.strip()
    if not mb:
        return []
    out = git("diff", "--name-only", mb, cwd=worktree, check=False).stdout.splitlines()
    out += git("ls-files", "--others", "--exclude-standard", cwd=worktree, check=False).stdout.splitlines()
    return sorted({p.strip() for p in out if p.strip()})


def ran_tests(worktree: Path) -> set[str]:
    """Names (arguments cut) of every test with a result in the worktree's logged runs (TEST_RESULTS globs, so tiers
    run in several launches all count)."""
    import xml.etree.ElementTree as ET
    ran: set[str] = set()
    files: list[Path] = []
    for pattern in TEST_RESULTS:
        files += sorted(worktree.glob(pattern)) if any(ch in pattern for ch in "*?[") else [worktree / pattern]
    for results in files:
        if not results.is_file():
            continue
        try:
            root = ET.parse(results).getroot()
        except ET.ParseError:
            continue
        names = [c.get("fullname", "") for c in root.iter("test-case")]                        # NUnit 3
        names += [".".join(x for x in (c.get("classname", ""), c.get("name", "")) if x)
                  for c in root.iter("testcase")]                                               # JUnit
        ran |= {re.sub(r"[(\[].*$", "", n) for n in names if n}
    return ran


def coverage_gate(worktree: Path, base: str) -> list[str]:
    """The tests this branch adds (committed or not: declared now in a TEST_FILE_RE file and not at the merge-base)
    that have no result in any of the worktree's logged runs, each with the tier that runs it. A card's Tests header
    can miss the tier its own new tests live in. Tooling only: a failing git or a missing tier table holds nothing."""
    if not _coverage_on():
        return []
    mb = git("merge-base", base, "HEAD", cwd=worktree, check=False).stdout.strip()
    if not mb:
        return []
    decl = re.compile(TEST_DECL_RE, re.S)
    new_tests: list[str] = []
    for rel in branch_paths(worktree, base):
        m = re.match(TEST_FILE_RE, rel)
        path = worktree / rel
        if not m or not path.is_file():
            continue
        now = set(decl.findall(path.read_text(encoding="utf-8", errors="replace")))
        shown = git("show", f"{mb}:{rel}", cwd=worktree, check=False)
        before = set(decl.findall(shown.stdout)) if shown.returncode == 0 else set()
        new_tests += [TEST_NAME.format(cls=m.group(1), name=n) for n in sorted(now - before)]
    if not new_tests:
        return []
    ran = ran_tests(worktree)
    missing = [t for t in new_tests if t not in ran]
    if not missing:
        return []
    try:
        tt = _test_tiers()
        table = getattr(tt, "TIERS", {})
        tier_of = lambda t: next((k for k, fs in table.items() if any(re.search(x, t) for x in fs)), tt.FULL)
    except Exception:
        tier_of = lambda t: "?"
    return [f"{t} (tier {tier_of(t)})" for t in missing]


#: A tier counts as run when this share of its tests (tier_tests) has a result. A tier run gives ~100 %, a tier
#: never run ~0 %; the slack is for tests `git merge main` brought after the run (main's regression net covers
#: those, and a MOVED re-run of shared tiers must not send the card back for the others). The tests the branch
#: itself adds are held strictly by coverage_gate.
COVER_SHARE = 0.9


def tiers_gate(worktree: Path, base: str) -> tuple[list[str], list[str]]:
    """(the tiers the branch's paths need, a line per tier no logged run in the worktree covers: its count of tests
    without a result and the first of them). A card's `Tests:` header can name fewer tiers than its paths need.
    Tooling only: no tier script, no results setting, a failing git or tier table holds nothing."""
    if not TIERS_SCRIPT or not TEST_RESULTS:
        return [], []
    try:
        tt = _test_tiers()
        tiers = tt.tiers_for(branch_paths(worktree, base))
    except Exception:
        return [], []
    ran = ran_tests(worktree)
    gaps = []
    for tier in tiers:
        try:
            want = set(tt.tier_tests(tier, str(worktree)))
        except Exception:
            continue
        missing = sorted(want - ran)
        if missing and len(want) - len(missing) < COVER_SHARE * len(want):
            gaps.append(f"{tier}: {len(missing)} of {len(want)} test(s) without a result, e.g. {missing[0]}")
    return tiers, gaps


def untiered_gate(worktree: Path, base: str) -> list[str]:
    """`untiered()` lines of the tier script for the test classes the branch adds or changes: a test no tier reaches
    runs in no tier run and passes the coverage gate unseen."""
    if not TIERS_SCRIPT or not TEST_FILE_RE:
        return []
    classes = {m.group(1) for p in branch_paths(worktree, base) if (m := re.match(TEST_FILE_RE, p))}
    if not classes:
        return []
    try:
        return list(_test_tiers().untiered(str(worktree), classes))
    except Exception:
        return []


def result_filled(text: str) -> bool:
    r = section(text, "Result").strip()
    return bool(r) and "(filled by the executing agent)" not in r


# --- staging ------------------------------------------------------------------


def stage(worktree: Path, paths: list[str]) -> list[str]:
    """Stage each existing path (and its `<path>.meta` sibling, for asset pipelines that keep one per file).
    Returns what was staged."""
    staged: list[str] = []
    for rel in paths:
        if rel in ("plan/BOARD.md", ".", "-A"):
            continue  # the board is never touched on a branch
        if any(ch in rel for ch in "*?["):
            # a card may own a generated tree as a pattern ("build/generated/**");
            # hand it to git as a pathspec and let git decide what it matches.
            git("add", "--", rel, cwd=worktree, check=False)
            staged.append(rel)
            continue
        target = worktree / rel
        if not target.exists():
            continue
        git("add", "--", rel, cwd=worktree)
        staged.append(rel)
        meta = worktree / (rel.rstrip("/") + ".meta")
        if meta.exists():
            git("add", "--", rel.rstrip("/") + ".meta", cwd=worktree)
            staged.append(rel + ".meta")
    return staged


def leftovers(worktree: Path) -> list[str]:
    """Tracked-or-untracked paths still not staged (gitignored files are already excluded)."""
    out = git("status", "--porcelain", cwd=worktree).stdout
    rest: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if code[0] != " " and code[0] != "?":
            continue  # already staged
        rest.append(path)
    return rest


def coauthor_trailer(coauthor: str) -> str:
    """The trailer line for --coauthor given either as the bare value or as the whole `Co-Authored-By: ...` line
    (agents pass either)."""
    value = re.sub(r"^\s*co-authored-by:\s*", "", coauthor or "", flags=re.I).strip()
    return f"Co-Authored-By: {value}" if value else ""


def commit(worktree: Path, message: str, coauthor: str) -> str | None:
    if not git("diff", "--cached", "--name-only", cwd=worktree).stdout.strip():
        return None
    trailer = coauthor_trailer(coauthor)
    full = message if not trailer else f"{message}\n\n{trailer}"
    git("commit", "-q", "-m", full, cwd=worktree)
    return git("rev-parse", "--short", "HEAD", cwd=worktree).stdout.strip()


# --- steps --------------------------------------------------------------------


def main_blockers(main: Path, branch: str | None = None) -> list[str]:
    """What in MAIN stops a merge or a board commit: changes to tracked files, and untracked files only where the
    branch being merged brings the same path (git would refuse to overwrite them). Any other untracked file is the
    owner's or a tool's and is left alone: one owner's untracked notes file once kept a finished card from merging
    for 85 minutes and three relaunched contexts."""
    tracked = [l for l in git("status", "--porcelain", "--untracked-files=no", cwd=main).stdout.splitlines() if l.strip()]
    if not branch:
        return tracked
    untracked = {l[3:].strip().strip('"') for l in
                 git("status", "--porcelain", "--untracked-files=all", cwd=main).stdout.splitlines() if l.startswith("??")}
    if not untracked:
        return tracked
    incoming = set(git("diff", "--name-only", f"HEAD...{branch}", cwd=main, check=False).stdout.splitlines())
    return tracked + [f"?? {p} (the branch adds it too)" for p in sorted(untracked & incoming)]


def wait_for_clean_main(main: Path, tries: int = 5, branch: str | None = None) -> None:
    import time
    blockers: list[str] = []
    for i in range(tries):
        blockers = main_blockers(main, branch)
        if not blockers:
            return
        if i < tries - 1:
            print("MAIN is dirty (another agent is mid-claim); waiting 30 s ...")
            time.sleep(30)
    listing = "\n  ".join(blockers[:10])
    # autospawn.py matches "MAIN still has uncommitted changes": keep the wording.
    raise SystemExit("MAIN still has uncommitted changes after 2.5 minutes. Stop and tell the owner.\n"
                     f"MAIN_DIRTY (what blocks):\n  {listing}")


def _is_link(path: Path) -> bool:
    """A symlink or (Windows) a junction or other reparse point: removed as a link, never followed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _unlink_link(path: Path) -> None:
    try:
        os.unlink(path)          # a file symlink; a directory junction on recent Windows Pythons
    except OSError:
        os.rmdir(path)           # a directory symlink or junction: removes the link, not the target


def remove_junctions(worktree: Path) -> None:
    """Remove the worktree's SHARED, SEEDED and EMPTY directories before `git worktree remove`: links as links, real
    directories recursively without following any link inside them (a seeded cache may hold junctions of its own)."""
    root = worktree.resolve()
    for name in dict.fromkeys(SHARED_DIRS + SEEDED_DIRS + EMPTY_DIRS):
        link = worktree / name
        if _is_link(link):
            try:
                _unlink_link(link)
            except OSError as e:
                print(f"could not remove the link {link.as_posix()}: {e}")
        elif link.is_dir() and link.resolve().is_relative_to(root) and link.resolve() != root:
            if sys.platform == "win32":
                # `rmdir /s` unlinks junctions inside without following them, and copes with long paths
                subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(link)], capture_output=True, text=True)
            if link.exists():
                shutil.rmtree(link, ignore_errors=True)   # never follows symlinks


def stop_long_lived(worktree: Path) -> None:
    """STOP_CMD: a process left running in the worktree (a warm editor, a server) has it open through its links, so it
    must quit before they and the worktree go. STOP_IF_EXISTS is the cheap test: no file, nothing to stop."""
    if not STOP_CMD or (STOP_IF_EXISTS and not (worktree / STOP_IF_EXISTS).exists()):
        return
    try:
        p = subprocess.run(STOP_CMD, cwd=worktree, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"stop: {e}")
        return
    last = (p.stdout or "").strip().splitlines()[-1:] or ["(no output)"]
    print(f"stop: {last[0]}")


def cleanup(main: Path, board, task: str, worktree: Path) -> None:
    stop_long_lived(worktree)
    remove_junctions(worktree)
    os.chdir(main)  # a worktree cannot be removed while it is this process's cwd
    p = git("worktree", "remove", worktree.as_posix(), cwd=main, check=False)
    if p.returncode != 0:
        p = git("worktree", "remove", "--force", worktree.as_posix(), cwd=main, check=False)
    if p.returncode != 0:
        print(f"could not remove the worktree: {(p.stdout + p.stderr).strip()[:200]}")
        return
    git("branch", "-D", f"task/{task}", cwd=main, check=False)
    print(f"worktree and branch task/{task} removed")


def finalize(main: Path, board, task: str, merge_hash: str, worktree: Path) -> int:
    wait_for_clean_main(main)
    if board_cmd(main, "done", task, merge_hash) != 0:
        return 1
    cleanup(main, board, task, worktree)
    release_main_lock(main, task)
    return 0


# --- main lock -------------------------------------------------------------------
#
# Agents resolve their own merge conflicts, but one at a time. Logs/main.lock names the card that owns main from its
# merge attempt until it is done: seconds of git work, or a conflict being resolved in its worktree. A MOVED card
# releases it and re-tests in its worktree, so no card waits on another's test run. Every other finish.py waits for
# it. The owner is live while its session holds its launch lease (plan/launch.py take_lease); a hand session has no
# lease and owns the lock for LOCK_STALE_S.

LOCK_WAIT_S = 540        # one finish.py call waits at most this long (a foreground tool call allows 600 s)
LOCK_POLL_S = 20
LOCK_STALE_S = 90 * 60


def _lock_path(main: Path) -> Path:
    return main / "Logs" / "main.lock"


def lock_owner(main: Path) -> tuple[str, float] | None:
    p = _lock_path(main)
    try:
        task, stamp = p.read_text(encoding="utf-8").split()[:2]
        return task, float(stamp)
    except (OSError, ValueError):
        return None


def _owner_live(main: Path, task: str, since: float) -> bool:
    import time
    try:
        sys.path.insert(0, str(MODULE_DIR))
        import launch
        if launch.lease_held(task):
            return True
        if (launch.AGENT_LOGS / f"{task}.lease").exists():
            return False                          # its autospawned session ended
    except Exception:
        pass
    return time.time() - since < LOCK_STALE_S


def acquire_main_lock(main: Path, task: str) -> bool:
    """Take or keep the main lock for `task`; wait up to LOCK_WAIT_S for a live owner to finish."""
    import time
    deadline = time.time() + LOCK_WAIT_S
    while True:
        own = lock_owner(main)
        if own is None or own[0] == task or not _owner_live(main, *own):
            if own is not None and own[0] != task:
                print(f"main lock: {own[0]} no longer runs; taking it over")
            p = _lock_path(main)
            p.parent.mkdir(exist_ok=True)
            p.write_text(f"{task} {own[1] if own and own[0] == task else time.time()}\n", encoding="utf-8")
            return True
        if time.time() > deadline:
            print(f"\nmain is locked by {own[0]} (merging or resolving a conflict since "
                  f"{time.strftime('%H:%M', time.localtime(own[1]))}). Run `python plan/finish.py {task}` again; "
                  "do not end your turn and do not touch main meanwhile.")
            return False
        print(f"main locked by {own[0]}; waiting ...", flush=True)
        time.sleep(LOCK_POLL_S)


def release_main_lock(main: Path, task: str) -> None:
    own = lock_owner(main)
    if own is not None and own[0] == task:
        _lock_path(main).unlink(missing_ok=True)
        print("main lock released")


# --- main ---------------------------------------------------------------------


def _shared(mine: set[str], theirs: set[str], full: str, outside: set[str]) -> set[str]:
    """The tiers a test of both sides can be in (`full` shares every tier but those OUTSIDE_FULL)."""
    if not mine or not theirs:
        return set()
    if full in mine and full in theirs:
        return {full} | (outside & mine & theirs)
    if full in mine:
        return set(theirs)
    if full in theirs:
        return set(mine)
    return mine & theirs


def moved_retest(main: Path, before: str, task: str, inert: list[str] | None = None) -> tuple[str, list[str]]:
    """("", []) when the branch may merge onto main as it is now, else (the test command the card runs in its
    worktree after `git merge main`, the build paths main brought). `inert`, when given, receives the paths main
    brought that set no MOVED (INERT_EXT, or no tier shared with the card), for the caller to print.

    MOVED = build paths changed on main since the merge-base, judged path by path. An INERT_EXT path never counts
    (a RETEST_ALWAYS one always does). Without a tier script every other build path is MOVED and the command is
    TEST_CMD. With one, a path needs a re-test only when it can change a verdict of this card's tests, i.e. it shares
    a tier with the card's own paths (`full` shares every tier) or is RETEST_ALWAYS; anything else merges as it is
    and the regression net on main, if there is one, runs the whole suite. The re-test is the shared tiers only: a
    test in this card's tiers alone cannot be affected by main's paths and passed on the branch, one in main's tiers
    alone cannot be affected by the card and passed on main."""
    base = git("merge-base", before, f"task/{task}", cwd=main).stdout.strip()
    theirs = [p for p in git("diff", "--name-only", base, before, "--", *BUILD_PATHS,
                             cwd=main, check=False).stdout.splitlines() if p]
    if not theirs:
        return "", []
    always_prefixes = tuple(RETEST_ALWAYS)
    is_always = lambda p: bool(always_prefixes) and p.startswith(always_prefixes)
    is_inert = lambda p: bool(INERT_EXT) and not is_always(p) and p.lower().endswith(tuple(INERT_EXT))
    if not TIERS_SCRIPT:
        live = [p for p in theirs if not is_inert(p)]
        if inert is not None:
            inert += [p for p in theirs if is_inert(p)]
        return (TEST_CMD if live else ""), theirs
    mine = [p for p in git("diff", "--name-only", base, f"task/{task}", "--", *BUILD_PATHS,
                           cwd=main, check=False).stdout.splitlines() if p]
    try:
        tt = _test_tiers()
    except Exception as exc:                       # tooling only: never block a merge on it
        return f"({TIERS_SCRIPT} unavailable: {exc}) run the full suite: {TEST_CMD}", theirs
    full = tt.FULL
    smoke = set(getattr(tt, "SMOKE_TIERS", ()))
    outside = set(getattr(tt, "OUTSIDE_FULL", ()))
    t_mine = set(tt.tiers_for(mine)) - smoke
    shared: set[str] = set()
    for p in theirs:
        if is_inert(p):
            hit: set[str] = set()
        else:
            hit = _shared(t_mine, set(tt.tiers_for([p])) - smoke, full, outside)
            if is_always(p):
                hit = hit or {full}
        if hit:
            shared |= hit
        elif inert is not None:
            inert.append(p)
    if full in shared:
        shared = {full} | (outside & shared)
    if not shared:
        return "", theirs
    order = [full] + list(tt.ORDER)
    return tt.command_for([t for t in order if t in shared]), theirs


def _run_cmd_for(tiers: list[str]) -> str:
    try:
        return _test_tiers().command_for(tiers)
    except Exception:
        return TEST_CMD


def tier_refusal(worktree: Path, base: str, args: argparse.Namespace) -> bool:
    """Print why the tier gates hold the card; True when they do."""
    bad = [] if args.skip_tiers else untiered_gate(worktree, base)
    if bad:
        print(f"This branch adds or changes test classes with tests no tier reaches ({TIERS_SCRIPT} untiered()):")
        for line in bad[:12]:
            print("   UNTIERED " + line)
        print(f"Such a test runs in no tier run, so no tier run proves it. Map it to a tier in {TIERS_SCRIPT} (list\n"
              "that file in the card's Files), run the tier, then finish.py again.\n"
              "If the tool is wrong, pass --skip-tiers and file a suggestion.")
        return True
    tiers, gaps = ([], []) if args.skip_tiers else tiers_gate(worktree, base)
    if gaps:
        print(f"This branch's paths need the tiers {','.join(tiers)} ({TIERS_SCRIPT} tiers_for), and no test run "
              f"logged in this worktree ({', '.join(TEST_RESULTS)}) covers:")
        for line in gaps:
            print("   " + line)
        print(f"Run {_run_cmd_for(tiers)}\n(the tiers can be split over several launches), then finish.py again. If "
              "the tool is wrong, pass --skip-tiers and file a suggestion.")
        return True
    return False


def dry_run(args: argparse.Namespace) -> int:
    """--dry-run: what finish.py would stage and what the tier gates say, touching neither git, the board nor MAIN.
    `task` is a card id or a card's path (a scratch card); the gates read the card's worktree when it exists, else
    the repository this runs in (a card path: always the repository this runs in)."""
    main = find_main()
    board = load_board(main)
    given = Path(args.task)
    here = Path(git("rev-parse", "--show-toplevel", cwd=Path.cwd()).stdout.strip())
    # a leftover folder of a finished card (an emptied cache dir) is not its worktree: take the first root with the card
    roots = [r for r in (board.WORKTREES / args.task, here) if (r / ".git").exists()]
    root, card = here, (given if given.is_file() else None)
    for r in roots if card is None else []:
        card = next(iter(sorted((r / "plan" / "tasks").glob(f"{args.task}-*.md"))), None)
        if card is not None:
            root = r
            break
    if card is None:
        raise SystemExit(f"no card {args.task} (an id with plan/tasks/<id>-*.md, or a card's path)")
    text = card.read_text(encoding="utf-8", errors="replace")
    print(f"DRY-RUN {card.as_posix()} (repo {root.as_posix()})")
    for rel in card_files(text) + list(args.add or []):
        if any(ch in rel for ch in "*?["):
            print(f"  stage {rel} (pathspec)")
        elif (root / rel).exists():
            meta = " (+ .meta)" if (root / (rel.rstrip("/") + ".meta")).exists() else ""
            print(f"  stage {rel}{meta}")
        else:
            print(f"  skip  {rel} (no such path)")
    print("  stage " + ", ".join(ALWAYS_STAGED + ["the card"]))
    if TIERS_SCRIPT:
        tiers, gaps = tiers_gate(root, board.MAIN_BRANCH)
        print("TIERS " + (",".join(tiers) if tiers else "(none)"))
        for line in gaps:
            print("TIER-GAP " + line)
        for line in untiered_gate(root, board.MAIN_BRANCH):
            print("UNTIERED " + line)
    return 0


def run(args: argparse.Namespace) -> int:
    if args.dry_run:
        return dry_run(args)
    main = find_main()
    board = load_board(main)
    task = args.task
    worktree = board.WORKTREES / task

    _, rows = board.load()
    row = next((r for r in rows if r.task_id == task), None)
    if row is None:
        raise SystemExit(f"{task} is not on the board")

    if args.release_lock:
        release_main_lock(main, task)
        return 0

    if args.verified:
        if not worktree.exists():
            raise SystemExit(f"no worktree at {worktree.as_posix()}")
        print(f"{task}: acceptance test reported passing in MAIN; marking done.")
        return finalize(main, board, task, args.verified, worktree)

    in_main_fn = getattr(board, "in_main", None)
    in_main = (not worktree.exists() and in_main_fn is not None
               and in_main_fn(task, row.state in ("in-progress", "waiting")))
    if in_main:
        worktree = main          # serial mode (board.in_main): the card was worked directly on main
    elif not worktree.exists():
        raise SystemExit(f"no worktree at {worktree.as_posix()}; nothing to finish")
    if row.state not in ("in-progress", "waiting"):
        raise SystemExit(f"{task} is {row.status!r}; only the agent holding the row finishes it.")

    card_path = next(iter(sorted((worktree / "plan" / "tasks").glob(f"{task}-*.md"))), None)
    if card_path is None:
        raise SystemExit(f"no card plan/tasks/{task}-*.md in the worktree")
    text = card_path.read_text(encoding="utf-8", errors="replace")

    # 1. gates, before anything is staged: a refused finish must leave the branch as it was,
    # not a half-finished commit claiming to be the card.
    if not args.checkpoint and not args.blocked:
        qs = open_questions(text)
        if qs and row.state == "in-progress":
            print(f"{len(qs)} unanswered question(s) in the card and the row is still in-progress. The merge is gated.")
            print(f"Run: python plan/board.py waiting {task} \"{len(qs)} questions\"")
            print("then ask the owner in this context, apply the answers, and run finish.py again.")
            print("(To park the work meanwhile: python plan/finish.py %s --checkpoint)" % task)
            return 1
        if not result_filled(text):
            print("The card's Result section is still the placeholder. Fill it (what was built, what the "
                  "acceptance test printed, deviations) and run finish.py again.")
            return 1
        if needs_reproduce(text) and not args.skip_reproduce:
            bad = reproduce_gate(text, worktree)
            if bad:
                print("This card has a visible result (a Defect view, a render budget) and its Reproduce block does not pass:")
                for line in bad:
                    print("   " + line)
                print("\nA card closed on a number nobody looked at is redone later at many times its cost (measured on\n"
                      "one project). Write the block at the top of the Result, take the captures it names (B from the same\n"
                      "views as A, on this card's build), and run finish.py again. If the card truly has nothing to look\n"
                      "at, set its Render budget line to \"none\". Format:\n\n" + REPRODUCE_FORMAT)
                return 1
        unrun = [] if args.skip_coverage else coverage_gate(worktree, board.MAIN_BRANCH)
        if unrun:
            tiers = sorted({u.rsplit("(tier ", 1)[-1].rstrip(")") for u in unrun})
            print(f"This branch adds tests that no test run logged in this worktree ({', '.join(TEST_RESULTS)}) has a result for:")
            for line in unrun[:12]:
                print("   " + line)
            print(f"Run them ({_run_cmd_for(tiers) if TIERS_SCRIPT else TEST_CMD}, or the card's tiers plus those) so the\n"
                  "acceptance run covers them, then run finish.py again. If the tool is wrong, pass --skip-coverage\n"
                  "and file a suggestion.")
            return 1
        if tier_refusal(worktree, board.MAIN_BRANCH, args):
            return 1
        held = [] if args.skip_wiki else wiki_gate(worktree, board.MAIN_BRANCH)
        if held:
            print("The wiki is behind this card's change:")
            for line in held[:12]:
                print("   " + line.strip())
            print("Read those pages (one message), fix what your change made wrong, add what the next agent must\n"
                  "know, mark each page verified as the wiki's own rules say, and run finish.py again.\n"
                  "If the tool is wrong, pass --skip-wiki and file a suggestion.")
            return 1
    if args.checkpoint:
        now = handoff(text)
        rel = card_path.relative_to(worktree).as_posix()
        shown = git("show", f"HEAD:{rel}", cwd=worktree, check=False)   # cards are UTF-8; git() decodes as UTF-8
        before = handoff(shown.stdout or "") if shown.returncode == 0 else ""
        if not now or now == before:
            print(("The card's Result has no Handoff block." if not now else
                   "The Handoff block is the one already committed: it describes an earlier stop.")
                  + " Write it (overwrite, do not append) and run the checkpoint again:\n\n" + HANDOFF_FORMAT)
            return 1
        if not re.search(r"^Done when:\s*\S", now, re.M):
            print("The Handoff block has no `Done when:` line. The next context may run on another model and "
                  "iterates toward it, so state the measurable target, then run the checkpoint again:\n\n"
                  + HANDOFF_FORMAT)
            return 1

    # 2. stage
    paths = card_files(text) + list(args.add or []) + ALWAYS_STAGED
    rel_card = card_path.relative_to(worktree).as_posix()
    if rel_card not in paths:
        paths.append(rel_card)  # Result and answers live in the card and belong to the commit
    staged = stage(worktree, paths)
    rest = leftovers(worktree)
    if rest and not args.allow_dirty:
        print(f"{len(rest)} path(s) changed but not listed in the card's Files section:")
        for p in rest[:20]:
            print("   " + p)
        if len(rest) > 20:
            print(f"   ... and {len(rest) - 20} more")
        print("\nAdd them to the card's Files section with a one-line reason (that is the rule),\n"
              "or pass --add <path> for each, or --allow-dirty to leave them uncommitted on purpose.")
        return 1
    print(f"staged {len(staged)} path(s)" + (f", first: {', '.join(staged[:6])}" if staged else ""))

    # 3. commit on the branch
    title = row.title.strip() or task
    if args.blocked:
        msg = f"{task}: work in progress ({args.blocked})"
    elif args.checkpoint:
        msg = f"{task}: checkpoint"
    else:
        msg = f"{task}: {title}"
    sha = commit(worktree, msg, args.coauthor)
    print(f"commit: {sha} {msg}" if sha else "nothing new to commit on the branch")

    if args.blocked:
        release_main_lock(main, task)
        return board_cmd(main, "blocked", task, args.blocked)

    if args.checkpoint:
        release_main_lock(main, task)
        print(f"\nCheckpointed. End this context now; a fresh one continues with:\n"
              f"    python plan/start.py --resume {task}\n"
              f"It prints the Handoff block you just wrote and the code named under Open.")
        return 0

    if in_main:
        # Serial mode: the commit above is already on main and nothing else ran meanwhile, so the
        # acceptance run in MAIN is the verification. No merge, no MOVED check, no cleanup.
        head = sha or git("rev-parse", "--short", "HEAD", cwd=main).stdout.strip()
        post_merge_hook(text, task, title, head, worktree)
        return board_cmd(main, "done", task, head)

    # 4. merge into MAIN, holding the main lock for the git work only (seconds)
    if not acquire_main_lock(main, task):
        return 1
    wait_for_clean_main(main, branch=f"task/{task}")
    before = git("rev-parse", "HEAD", cwd=main).stdout.strip()

    # 5. did main move under us while the card was being built? Decided before the merge: a MOVED card re-tests in
    # its own worktree with the lock released, so no card waits on another's test run.
    inert: list[str] = []
    retest, theirs = moved_retest(main, before, task, inert)
    if inert:
        print(f"main brought {len(inert)} build path(s) judged inert for this card (an INERT_EXT file, or no "
              "test tier in common):")
        for path in inert[:8]:
            print("   " + path)
        if len(inert) > 8:
            print(f"   ... and {len(inert) - 8} more")
    if retest:
        release_main_lock(main, task)
        live = [p for p in theirs if p not in inert]
        why = "that share a test tier with this card" if TIERS_SCRIPT else "that can change a test verdict"
        print(f"\nMOVED: since this branch last merged main, main brought {len(live)} build path(s) {why}:")
        for path in live[:8]:
            print("   " + path)
        print(f"Not merged; the main lock is released. In the worktree, now (do not end your turn):\n"
              f"  1. `git merge {board.MAIN_BRANCH}` (a conflict: resolve it keeping both cards' intent, commit "
              f"`{task}: merge main (<what and how>)`);\n"
              f"  2. {retest}\n"
              f"     (passing = verified);\n"
              f"  3. `python plan/finish.py {task}` again.")
        return 1
    if theirs:
        why = "none sharing a test tier with this card" if TIERS_SCRIPT else "all inert"
        print(f"main moved ({len(theirs)} build path(s)), {why}: merging as is.")

    p = git("merge", "--no-ff", f"task/{task}", "-m", f"{task}: merge", cwd=main, check=False)
    if p.returncode != 0:
        git("merge", "--abort", cwd=main, check=False)
        print(tail(p.stdout + p.stderr, 15))
        # Agents resolve their own conflicts, holding the main lock so no other card merges meanwhile and the
        # resolution does not have to be redone.
        print(f"\nMerge conflicted and was aborted; MAIN is untouched. {task} now holds the main lock "
              "(Logs/main.lock): no other card merges until you finish. Resolve it yourself, now:")
        print(f"  1. in the worktree: `git merge {board.MAIN_BRANCH}`; resolve every conflict keeping both sides' "
              "intent (read the other card's commits for what it wanted);")
        print(f"  2. commit the merge as `{task}: merge main (<what conflicted and how it was resolved>)`;")
        print("  3. re-run the acceptance test in the worktree;")
        print(f"  4. `python plan/finish.py {task}` again (it keeps your lock and merges).")
        print("Never edit or merge in MAIN by hand, do not end your turn while holding the lock, and if you cannot "
              f"resolve it, run `python plan/finish.py {task} --blocked \"merge conflict: <why>\"` (releases the lock).")
        return 1
    merge_hash = git("rev-parse", "--short", "HEAD", cwd=main).stdout.strip()
    print(f"merged: {merge_hash} ({task}: merge)")
    post_merge_hook(text, task, title, merge_hash, worktree)
    # Nothing re-runs in MAIN (BUILD_PATHS above): the worktree's acceptance run is the verification.
    return finalize(main, board, task, merge_hash, worktree)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Commit, merge, verify and close out a task in one turn.")
    p.add_argument("task")
    p.add_argument("--add", action="append", metavar="PATH",
                   help="stage this path too (repeatable); use it only for files also added to the card's Files section")
    p.add_argument("--checkpoint", action="store_true", help="commit progress on the branch and stop, keeping the row")
    p.add_argument("--blocked", metavar="REASON", help="commit what exists and mark the row blocked")
    p.add_argument("--verified", metavar="HASH",
                   help="legacy: a merge whose acceptance test you re-ran in MAIN by hand; mark done and clean up")
    p.add_argument("--release-lock", action="store_true", help="drop this card's main lock (Logs/main.lock) and exit")
    p.add_argument("--allow-dirty", action="store_true", help="leave unlisted changed paths uncommitted on purpose")
    p.add_argument("--skip-wiki", action="store_true",
                   help="merge although the wiki tool holds the card (only when the tool is wrong; file a suggestion)")
    p.add_argument("--skip-reproduce", action="store_true",
                   help="merge although the Reproduce block does not pass (only when the gate is wrong; file a suggestion)")
    p.add_argument("--skip-coverage", action="store_true",
                   help="merge although a test the branch adds has no logged result (only when the gate is wrong)")
    p.add_argument("--skip-tiers", action="store_true",
                   help="merge although a tier the branch's paths need has no run, or a changed test class is untiered")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be staged and the tier gates' verdict; touch nothing (task: an id or a card path)")
    p.add_argument("--coauthor", default=DEFAULT_COAUTHOR, metavar="LINE",
                   help="Co-Authored-By trailer for the commit: the whole line or just its value")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
