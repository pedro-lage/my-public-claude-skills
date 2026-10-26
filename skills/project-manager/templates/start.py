"""Start a task: claim it, build its worktree, print its card. One command, one turn.

Usage (run from MAIN, the repository root):

    python plan/start.py <model>             claim the next valid task and set it up
    python plan/start.py <model> <Txx>       claim that task (the board still decides if you may)
    python plan/start.py --resume <Txx>      re-attach to a task this agent already checkpointed

This is the agent's first command inside a session. Run from {{OWNER}}'s own shell (no
CLAUDECODE in the environment) it starts no work by itself, so it hands over to
`plan/launch.py ... --run`, which starts the Claude session that then runs this script
again from inside; `--print-only` keeps the print-the-card behaviour for a human shell.

Replaces the hand-run head of the protocol (next, claim, worktree add, link or copy the
environment, read the card). Every one of those was a separate agent turn billed at the
full context; a turn near the end of a card costs ~300k cached tokens whether it runs a
compile or a `mkdir`. This script does the lot in one turn and prints the card, so the
agent never spends a Read on it either.

It changes nothing that plan/board.py would not change: the claim goes through
board.py's own rules (todo only, prerequisites done, tag claimable, board order, no
existing worktree or branch) and no flag overrides them. If board.py refuses, this
script refuses with the same message and leaves MAIN untouched.

Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --- project settings ---------------------------------------------------------

#: Card header flag that switches the per-worktree environment below on, written inline in
#: the card header as `<name>: yes` (e.g. "Model: opus55. Phase: 1. Prerequisites: none. Docker: yes.").
#: Keep it even with every list below empty: cards use it to say what they need.
SHARED_FLAG = "{{SHARED_FLAG}}"

#: Gitignored directories a worktree LINKS to MAIN's copy (a junction on Windows, a symlink
#: elsewhere) instead of holding its own: a download cache, a model folder, anything read-mostly.
#: Linking costs nothing, but it makes the directory one resource for every worktree, so only one
#: process on the machine may write it at a time; that is what the project's own run lock is for.
SHARED_DIRS: list[str] = [{{SHARED_DIRS}}]

#: Gitignored directories a worktree gets its own COPY of, seeded from MAIN's: a build or import
#: cache that is slow to rebuild and written by every run. A link would let one run at a time on
#: the machine; a copy took about a minute where a rebuild took twenty (measured on one project),
#: and the first run then rebuilds only what the branch changed, so worktrees run in parallel.
#: Windows copies with robocopy (multithreaded, junctions skipped), elsewhere shutil.copytree.
SEEDED_DIRS: list[str] = [{{SEEDED_DIRS}}]

#: Entries never copied when seeding: state that belongs to one checkout or one live process
#: (per-root compile caches, lock files, a running editor's record, a pid file). Dirs are names at
#: the top of each seeded dir; files are glob patterns matched at any depth. Example for a game
#: engine's import cache: SEED_SKIP_DIRS = ["Bee", "ScriptAssemblies", "BurstCache"],
#: SEED_SKIP_FILES = ["*.lock", "*-lock", "EditorInstance.json", "ilpp.pid"].
SEED_SKIP_DIRS: list[str] = []
SEED_SKIP_FILES: list[str] = []

#: Written into a seeded dir only once its copy finished; never copied itself. A seed killed
#: halfway (a closed window, a crash) left a partial cache that the next run trusted, so `--resume`
#: re-seeds a dir without it, and the project's run tool may refuse one too.
SEED_MARKER = ".seeded"

#: Lock file under MAIN the project's run tool takes while it writes the seeded dirs (e.g.
#: "cache/run.lock"). When set, a seed creates it exclusively for the copy, so no run writes the
#: cache mid-copy; after SEED_LOCK_WAIT_S the copy runs anyway (a torn cache is rebuilt by the
#: first run). None: copy without a lock.
SEED_LOCK: str | None = None
SEED_LOCK_WAIT_S = 30

#: argv that tells whether a process still uses a worktree's seeded dirs, `{worktree}` replaced by
#: its path; "busy" when it exits 0 and prints anything. When set, --resume does not re-seed under a
#: live process and says so instead. None: never checked.
BUSY_CHECK: list[str] | None = None

#: Directories made empty in each worktree (build output, logs): gitignored, so a checkout lacks them.
EMPTY_DIRS: list[str] = [{{EMPTY_DIRS}}]

#: Commands that make a fresh worktree usable, one argv list each, run inside it on every claim.
#: Keep it to what a checkout genuinely needs.
ENV_SETUP: list[list[str]] = [{{ENV_SETUP_ARGV}}]

#: Prints the wiki pages for a card's Files, relative to the tree the card is in. Called as
#: `python <WIKI_TOOL> pages --card <card path>`; prints one indented `<path>.md` per page.
#: None: no pages are named.
WIKI_TOOL: str | None = None

#: The page sections printed inline on a fresh claim (the first heading a page has among these),
#: for at most this many of the card's pages and characters. Agents seldom opened the pages they
#: were pointed at (measured on one project: 0.2 page reads before the first edit), so the start
#: of each page comes to them.
WIKI_INLINE = ("Where to start",)
WIKI_INLINE_PAGES = 3
WIKI_INLINE_CHARS = 5000

#: Printed after the brief on a fresh claim by these models, keyed by board model key: the note
#: for the most expensive model's first context. Measured on one project, that model was 89% of
#: card spend at three times a cheaper request, mostly in tuning loops; plan/autospawn.py --handoff
#: runs every later context of such a card on the cheaper model. Empty dict: no note.
FIRST_CONTEXT_NOTE: dict[str, str] = {"fable": """\
FIRST CONTEXT: this is likely the card's only context on this model; the controller resumes every later one on a
cheaper model. Spend it on what needs you: the diagnosis, the hard math, the design, the first cut of the hard
part. Checkpoint as soon as what remains is iteration against numbers (tuning, test runs, verification, docs),
with a Handoff whose `Done when:` line is measurable and whose Next is concrete enough for a cheaper model.
"""}

#: Prints the code a Handoff block names under `Open:` on --resume. Called as
#: `python <SYM_TOOL> --max 80 <name> <name> ...` from the tree's root, printing each symbol's
#: source. None: the resumed context gets the Handoff block alone.
SYM_TOOL: str | None = None
OPEN_MAX_SYMBOLS = 8
OPEN_MAX_CHARS = 14000

RESUME_NOTE = ("Pick up from the Handoff block in the card's Result: it is what the previous context left you. "
               "No Handoff block: the whole Result is.")

#: Printed to the agent after the card. Keep it short; it is paid for on every turn
#: of the context that follows.
BRIEF = """\
Work in the worktree above; MAIN stays untouched until finish.py.
Stage nothing by hand and never edit plan/BOARD.md on the branch.

Budget for this context: checkpoint when the context hook reports ~175k tokens, or after
{{BUDGET}}, whichever comes first; not when the card will finish within ~15 more
requests (a resume costs ~$1 of re-reading). Overwrite the `### Handoff` block in the
card's Result (State, Next, Done when, Open, Tried) and checkpoint:

    python plan/finish.py {task} --checkpoint

then end the context. A fresh one resumes with `python plan/start.py --resume {task}`
at ~60k instead of the context this one has grown to.

When the acceptance test passes and Result is filled:

    python plan/finish.py {task}

which stages the card's Files, commits, merges into MAIN, verifies, marks the row done
and removes the worktree.
"""

#: BRIEF for serial mode ({{ENV_PREFIX}}_IN_MAIN=1, see board.IN_MAIN): no worktree, no branch, no merge.
BRIEF_IN_MAIN = """SERIAL MODE: no other card runs, so there is no worktree. Work directly in MAIN (this
directory) on branch {{MAIN_BRANCH}}; wherever the card or INVOKE.md says "the worktree", read MAIN.
Do not commit by hand and never edit plan/BOARD.md; finish.py commits the card's Files.

Budget for this context: checkpoint when the context hook reports ~175k tokens, or after
{{BUDGET}}, whichever comes first; not when the card will finish within ~15 more
requests (a resume costs ~$1 of re-reading). Overwrite the `### Handoff` block in the
card's Result (State, Next, Done when, Open, Tried) and checkpoint:

    python plan/finish.py {task} --checkpoint

(commits the work so far on {{MAIN_BRANCH}} as `{task}: checkpoint`) and end the context; a fresh
one continues with `python plan/start.py --resume {task}`.

When the acceptance test passes and Result is filled:

    python plan/finish.py {task}

which commits the card's Files on {{MAIN_BRANCH}} and marks the row done. There is no merge and no
re-verify: your acceptance run already ran on {{MAIN_BRANCH}}.
"""

# Importing plan/board.py would drop plan/__pycache__/ into MAIN, and an untracked directory
# there makes `git status --porcelain` non-empty, which is exactly what board.py refuses to
# claim against. Never write bytecode from these scripts.
sys.dont_write_bytecode = True

# Inside an agent's shell stdout is a pipe, and Windows Python then encodes it as cp1252: the first
# "≤" or "–" in a card raised UnicodeEncodeError halfway through the card (exit 255 on --resume).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

MODULE_DIR = Path(__file__).resolve().parent


def load_board(main: Path):
    """Import MAIN's plan/board.py, whose own MAIN constant then points at `main`."""
    path = main / "plan" / "board.py"
    spec = importlib.util.spec_from_file_location("_board", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # board.py uses @dataclass, which looks its class up here
    spec.loader.exec_module(mod)
    return mod


def run(argv: list[str], cwd: Path) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def card_text(board, task: str) -> tuple[Path | None, str]:
    path = board._card_path(task)
    if path is None:
        return None, ""
    return path, path.read_text(encoding="utf-8", errors="replace")


def needs_shared(text: str) -> bool:
    """True when the card header carries `<SHARED_FLAG>: yes`. The flag sits inline in the
    header line, not at the start of one, so this does not anchor; capped to the header."""
    head = " ".join(text.splitlines()[:15])
    return bool(re.search(rf"{re.escape(SHARED_FLAG)}:\s*yes", head, re.I))


def take_seed_lock(main: Path, why: str) -> Path | None:
    """MAIN's SEED_LOCK while its seeded dirs are copied, so no run writes them mid-copy. Returns the
    lock path when taken, None when SEED_LOCK is off or still held after SEED_LOCK_WAIT_S (the copy
    then runs anyway: a run seldom rewrites the whole cache, and a torn one is rebuilt by the first run)."""
    if not SEED_LOCK:
        return None
    lock = main / SEED_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    end = time.time() + SEED_LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"PID={os.getpid()};START={stamp};REASON={why}".encode())
            os.close(fd)
            return lock
        except FileExistsError:
            if time.time() > end:
                return None
            time.sleep(5)


def _copy_tree(src: Path, dst: Path, cwd: Path) -> str:
    """Copy src into dst without SEED_SKIP_* or links. "" on success, else the error."""
    skip_files = [*SEED_SKIP_FILES, SEED_MARKER]
    if sys.platform == "win32":
        argv = ["robocopy", str(src), str(dst), "/E", "/XJ", "/MT:16", "/R:1", "/W:1",
                "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
        if SEED_SKIP_DIRS:
            argv += ["/XD", *[str(src / d) for d in SEED_SKIP_DIRS]]
        argv += ["/XF", *skip_files]
        p = run(argv, cwd)
        if p.returncode >= 8:   # robocopy: 0-7 are kinds of success
            return f"robocopy exit {p.returncode}: {(p.stdout or p.stderr).strip()[-300:]}"
        return ""

    def ignore(folder: str, names: list[str]) -> set[str]:
        top = Path(folder) == src
        return {n for n in names
                if os.path.islink(os.path.join(folder, n))
                or (top and n in SEED_SKIP_DIRS)
                or any(fnmatch.fnmatch(n, pat) for pat in skip_files)}
    try:
        shutil.copytree(src, dst, symlinks=True, ignore=ignore, dirs_exist_ok=True)
    except (OSError, shutil.Error) as e:
        return f"copy failed: {str(e)[-300:]}"
    return ""


def seed_dir(worktree: Path, main: Path, task: str, name: str) -> str:
    """Copy MAIN's `name` into the worktree, without SEED_SKIP_*, and mark it seeded."""
    src, dst = main / name, worktree / name
    if not src.is_dir() or not any(src.iterdir()):
        dst.mkdir(parents=True, exist_ok=True)
        (dst / SEED_MARKER).write_text("empty: MAIN had nothing to copy\n", encoding="utf-8")
        return f"{name} (empty: MAIN has none to copy; the first run builds it)"
    t0 = time.time()
    lock = take_seed_lock(main, f"seed {task}")
    try:
        err = _copy_tree(src, dst, worktree)
    finally:
        if lock is not None:
            lock.unlink(missing_ok=True)
    if err:
        return f"{name} FAILED ({err})"
    (dst / SEED_MARKER).write_text(f"seeded from {src} for {task} in {time.time() - t0:.0f} s\n", encoding="utf-8")
    busy = SEED_LOCK and lock is None
    return (f"{name} (own, seeded from MAIN's in {time.time() - t0:.0f} s"
            + ("; MAIN's copy was busy, copied without its lock" if busy else "") + ")")


def dir_busy(worktree: Path) -> bool:
    """True when BUSY_CHECK says a process still uses the worktree's seeded dirs (False when unset)."""
    if not BUSY_CHECK:
        return False
    try:
        p = run([a.replace("{worktree}", str(worktree)) for a in BUSY_CHECK], worktree)
    except OSError:
        return False
    return p.returncode == 0 and bool(p.stdout.strip())


def reseed_dirs(worktree: Path, main: Path, task: str) -> list[str]:
    """A seeded dir of the worktree's own without SEED_MARKER is a seed that did not finish: copy
    MAIN's again, which also writes the marker. A dir that is a link to MAIN's was never seeded and
    is left alone. [] when there is nothing to do."""
    out = []
    for name in SEEDED_DIRS:
        d = worktree / name
        if not d.is_dir() or (d / SEED_MARKER).exists():
            continue
        if os.path.normcase(os.path.realpath(d)) != os.path.normcase(os.path.abspath(d)):
            continue
        if dir_busy(worktree):
            out.append(f"SEED_PARTIAL: {d} has no {SEED_MARKER}, but a process uses it; "
                       f"stop it, then --resume again")
            continue
        out.append(f"{name} had no {SEED_MARKER} (a seed that did not finish): re-seeded: "
                   + seed_dir(worktree, main, task, name))
    return out


def link_dir(worktree: Path, main: Path, name: str) -> str:
    """Link the worktree's `name` to MAIN's (created when missing): a junction on Windows, a symlink elsewhere."""
    target, link = main / name, worktree / name
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # a junction needs no administrator rights or developer mode, unlike a directory symlink
        p = run(["cmd", "/c", "mklink", "/J", str(link), str(target)], worktree)
        return f"{name} (linked)" if p.returncode == 0 else f"{name} FAILED: {(p.stderr or p.stdout).strip()}"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as e:
        return f"{name} FAILED: {e}"
    return f"{name} (linked)"


def make_shared_dirs(worktree: Path, main: Path, task: str) -> list[str]:
    """The worktree's environment: SHARED_DIRS linked to MAIN's, SEEDED_DIRS copied, EMPTY_DIRS made."""
    made = []
    for names, make in ((SHARED_DIRS, lambda n: link_dir(worktree, main, n)),
                        (SEEDED_DIRS, lambda n: seed_dir(worktree, main, task, n)),
                        (EMPTY_DIRS, lambda n: (worktree / n).mkdir(parents=True, exist_ok=True) or f"{n} (empty)")):
        for name in names:
            if (worktree / name).exists() or (worktree / name).is_symlink():
                made.append(f"{name} (already there)")
            else:
                made.append(make(name))
    return made


def print_card(path: Path, text: str, fresh: bool = False) -> None:
    print(f"\n--- {path.as_posix()} ---")
    print(text.rstrip())
    print("--- end of card ---\n")
    print_wiki(path, fresh)


def print_wiki(card: Path, fresh: bool = False) -> None:
    """Name the wiki pages that cover the card's Files (WIKI_TOOL): the agent reads them instead of
    searching the code for its bearings. On a fresh claim also print each page's WIKI_INLINE section,
    since agents seldom opened the pages. Silent when there is no wiki; never fails a start."""
    if not WIKI_TOOL:
        return
    root = card.resolve().parents[2]
    tool = root / WIKI_TOOL
    if not tool.exists():
        return
    try:
        p = subprocess.run([sys.executable, str(tool), "pages", "--card", str(card)], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return
    if p.returncode != 0 or not p.stdout.strip():
        return
    print(p.stdout.rstrip() + "\n")
    if not fresh or not WIKI_INLINE:
        return
    pages = re.findall(r"^\s+([\w./-]+\.md)\b", p.stdout, re.M)[:WIKI_INLINE_PAGES]
    budget = WIKI_INLINE_CHARS
    heads = "|".join(re.escape(h) for h in WIKI_INLINE)
    for page in pages:
        try:
            body = (root / page).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(rf"^## ({heads})\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        if not m or not m.group(2).strip():
            continue
        section = m.group(2).strip()
        if len(section) > budget:
            continue
        budget -= len(section)
        print(f"--- {page}, \"{m.group(1)}\" (the rest of the page: Read it) ---\n{section}\n")


def cmd_start(model: str, requested: str | None) -> int:
    main = MODULE_DIR.parent
    board = load_board(main)

    _, rows = board.load()
    task: str
    if requested is None:
        row, reasons = board.next_claimable(rows, model)
        if row is None:
            print(f"No valid task available for {model}. Stop; do not reinterpret any row below.")
            # `done` rows say nothing and a long board would otherwise print hundreds of lines
            # into a context that is billed again on every later turn.
            interesting = [(r, why) for r, why in reasons if why != "done"]
            for r, reason in interesting[:8]:
                print(f"  {r.task_id}: {reason}")
            if len(interesting) > 8:
                print(f"  ... and {len(interesting) - 8} more unclaimable rows "
                      f"(python plan/board.py show for all)")
            if not interesting:
                print("  every row on the board is done.")
            return 1
        task = row.task_id
    else:
        task = requested

    # board.py owns every claim rule; a refusal here is final.
    p = run([sys.executable, str(main / "plan" / "board.py"), "claim", task, model], main)
    sys.stdout.write(p.stdout)
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        return p.returncode

    if getattr(board, "IN_MAIN", False):
        path, text = card_text(board, task)
        print(f"\n=== {task} claimed by {model} (serial mode: work in MAIN) ===")
        print(f"MAIN:     {main.as_posix()}")
        if path is not None:
            print_card(path, text, fresh=True)
        print(BRIEF_IN_MAIN.format(task=task))
        if model in FIRST_CONTEXT_NOTE:
            print(FIRST_CONTEXT_NOTE[model])
        return 0

    worktree = board.WORKTREES / task
    branch = f"task/{task}"
    p = run(["git", "worktree", "add", worktree.as_posix(), "-b", branch, board.MAIN_BRANCH], main)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        print(f"\nWorktree creation failed. The row is claimed; release it with "
              f"`python plan/board.py blocked {task} \"worktree add failed\"` or fix and retry.")
        return 1

    path, text = card_text(board, task)
    if path is None:
        print(f"WARNING: no card file plan/tasks/{task}-*.md")
        text = ""

    shared = needs_shared(text)
    print(f"\n=== {task} claimed by {model} ===")
    print(f"worktree: {worktree.as_posix()}")
    print(f"branch:   {branch}")
    if shared and (SHARED_DIRS or SEEDED_DIRS or EMPTY_DIRS):
        print("env dirs: " + ", ".join(make_shared_dirs(worktree, main, task)) +
              "  (finish.py removes them; never delete a linked one recursively)")
    for cmd in ENV_SETUP:
        r = run(cmd, worktree)
        print(f"env:      {' '.join(cmd)} -> exit {r.returncode}")

    if path is not None:
        print_card(worktree / "plan" / "tasks" / path.name, text, fresh=True)
    print(BRIEF.format(task=task))
    if model in FIRST_CONTEXT_NOTE:
        print(FIRST_CONTEXT_NOTE[model])
    return 0


def lean_card(text: str) -> tuple[str, list[str]]:
    """(the card as a resumed context needs it, the names on its Handoff block's `Open:` line).

    A checkpoint leaves a `### Handoff` block in Result (finish.HANDOFF_FORMAT). With one, the rest of
    Result is not printed: it is the history of earlier contexts, up to 33k characters on a long card, and
    a card that overflowed the tool output was read a second time. Without one the card prints whole."""
    m = re.search(r"^## Result[^\n]*\n", text, re.M)
    if not m:
        return text, []
    nxt = re.search(r"^## ", text[m.end():], re.M)
    body_end = m.end() + nxt.start() if nxt else len(text)
    body = text[m.end():body_end]
    h = re.search(r"^### Handoff[^\n]*\n(.*?)(?=^#{2,3} |\Z)", body, re.S | re.M)
    if not h or not h.group(1).strip():
        return text, []
    block = h.group(1).strip()
    opens: list[str] = []
    for line in block.splitlines():
        if line.lstrip().startswith("Open:"):
            opens = re.findall(r"`([^`]+)`", line)
    omitted = len(body) - len(h.group(0))
    if omitted < 400:
        return text, opens
    note = f"(Result history not printed: {omitted} characters, in the card file if the Handoff points at it.)"
    return text[:m.end()] + "\n### Handoff\n" + block + "\n\n" + note + "\n\n" + text[body_end:], opens


def print_opens(root: Path, names: list[str]) -> None:
    """Print the code the Handoff block names under `Open:`, so the resumed context does not ask for it.
    Without SYM_TOOL the names stay in the printed Handoff block and the agent looks them up itself."""
    if not SYM_TOOL:
        return
    tool = root / SYM_TOOL
    names = [n for n in names if not re.search(r"\s", n)][:OPEN_MAX_SYMBOLS]
    if not names or not tool.exists():
        return
    try:
        p = subprocess.run([sys.executable, str(tool), "--max", "80", *names], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return
    out = p.stdout.rstrip()
    if not out:
        return
    if len(out) > OPEN_MAX_CHARS:
        out = out[:OPEN_MAX_CHARS].rsplit("\n", 1)[0] + f"\n... cut at {OPEN_MAX_CHARS} characters: ask {SYM_TOOL} for the rest"
    print(f"--- code named under Open ({SYM_TOOL} --max 80 {' '.join(names)}) ---")
    print(out)
    print("--- end of code ---\n")


def pending_prereqs(board, row, rows) -> list[str]:
    """The row's prerequisites that are not `done` (an archived one counts as done, as in board.claim_reason)."""
    by_id = {r.task_id: r for r in rows}
    archived_ids = getattr(board, "archived_ids", set)
    archived = archived_ids() if any(p not in by_id for p in row.prereqs) else set()
    return [p for p in row.prereqs if (by_id[p].state != "done" if p in by_id else p not in archived)]


def cmd_resume(task: str) -> int:
    main = MODULE_DIR.parent
    board = load_board(main)
    _, rows = board.load()
    row = next((r for r in rows if r.task_id == task), None)
    if row is None:
        raise SystemExit(f"{task} is not on the board")
    if row.state not in ("in-progress", "waiting"):
        raise SystemExit(f"{task} is {row.status!r}; --resume only re-attaches to a row you already hold.")
    pending = pending_prereqs(board, row, rows)
    if pending:
        # {{OWNER}} may add a prerequisite after the claim (a card found to depend on another one);
        # the row is left as it is and resumes once that card is done.
        raise SystemExit(f"{task}: prerequisite not done: {', '.join(pending)}; not resuming (the row is unchanged).")
    worktree = board.WORKTREES / task
    in_main = getattr(board, "in_main", None)
    if not worktree.exists() and in_main is not None and in_main(task):
        path, text = card_text(board, task)
        print(f"=== {task} resumed in MAIN, serial mode ({row.status}) ===")
        log = run(["git", "log", "--oneline", "-8", "--grep", f"^{task}: "], main)
        print(f"{task} commits on {board.MAIN_BRANCH} so far:")
        for line in log.stdout.strip().splitlines():
            print("  " + line)
        dirty = run(["git", "status", "--porcelain"], main).stdout.strip()
        if dirty:
            print(f"uncommitted in MAIN: {len(dirty.splitlines())} path(s) (the previous context's unsaved work)")
            if "### Handoff" not in (text or ""):    # no checkpoint note: the paths are all there is to go on
                for line in dirty.splitlines()[:30]:
                    print("  " + line)
        if path is not None:
            lean, opens = lean_card(text)
            print_card(path, lean)
            print_opens(main, opens)
        print(BRIEF_IN_MAIN.format(task=task))
        print(RESUME_NOTE)
        return 0
    if not worktree.exists():
        raise SystemExit(f"no worktree at {worktree.as_posix()}; nothing to resume")

    path, _ = card_text(board, task)
    card_in_wt = worktree / "plan" / "tasks" / path.name if path else None
    text = card_in_wt.read_text(encoding="utf-8", errors="replace") if card_in_wt and card_in_wt.exists() else ""

    print(f"=== {task} resumed ({row.status}) ===")
    print(f"worktree: {worktree.as_posix()}")
    if needs_shared(text):
        for line in reseed_dirs(worktree, main, task):
            print(line)
    log = run(["git", "log", "--oneline", "-5", f"task/{task}"], main)
    print("branch commits so far:")
    for line in log.stdout.strip().splitlines():
        print("  " + line)
    dirty = run(["git", "status", "--porcelain"], worktree).stdout.strip()
    if dirty:
        n = len(dirty.splitlines())
        print(f"uncommitted in the worktree: {n} path(s) (checkpoint did not run, or work continued after it)")
    changed = [f for f in run(["git", "diff", "--name-only", f"{board.MAIN_BRANCH}...task/{task}"], main).stdout.split()
               if not f.startswith("plan/tasks/")]
    if changed:
        print(f"changed on the branch so far ({len(changed)}): " + ", ".join(changed[:15]) + (" ..." if len(changed) > 15 else ""))
    if card_in_wt:
        lean, opens = lean_card(text)
        print_card(card_in_wt, lean)
        print_opens(worktree, opens)
    print(BRIEF.format(task=task))
    print(RESUME_NOTE)
    return 0


def launch_session(args) -> int:
    """{{OWNER}} ran this from a shell: start the session through launch.py instead of printing a card
    that no model reads. The session runs start.py itself (INVOKE.md step 2), so nothing is claimed here."""
    launch = [sys.executable, str(MODULE_DIR / "launch.py")]
    if args.resume:
        launch += ["--resume", args.resume]
    elif args.model:
        # launch.py checks a named card is claimable before it starts the session on it.
        launch += [args.model] + (["--task", args.task] if args.task else [])
    else:
        print("give your model key, or --resume Txx")
        return 2
    print("start.py is the agent's setup step; from your shell it starts the session through launch.py:", flush=True)
    return subprocess.call(launch + ["--run"], cwd=MODULE_DIR.parent)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Claim a task and set up its worktree in one turn.")
    p.add_argument("model", nargs="?", help="your model key (as on the board tag)")
    p.add_argument("task", nargs="?", help="task id; omit to take the next valid one")
    p.add_argument("--resume", metavar="Txx", help="re-attach to a task you checkpointed earlier")
    p.add_argument("--print-only", action="store_true",
                   help="from a human shell: do the setup and print the card instead of starting a session")
    args = p.parse_args(argv)
    if not os.environ.get("CLAUDECODE") and not args.print_only:
        return launch_session(args)
    if args.resume:
        return cmd_resume(args.resume)
    if not args.model:
        p.error("give your model key, or --resume Txx")
    return cmd_start(args.model, args.task)


if __name__ == "__main__":
    sys.exit(main())
