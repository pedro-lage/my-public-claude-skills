"""Start a task: claim it, build its worktree, print its card. One command, one turn.

Usage (run from MAIN, the repository root):

    python plan/start.py <model>             claim the next valid task and set it up
    python plan/start.py <model> <Txx>       claim that task (the board still decides if you may)
    python plan/start.py --resume <Txx>      re-attach to a task this agent already checkpointed

Replaces steps 3 to 7 of the old plan/INVOKE.md (next, claim, worktree add, two mklink
calls, read the card). Every one of those was a separate agent turn billed at the full
context; a turn near the end of a card costs ~300k cached tokens whether it runs a
compile or an `mklink`. This script does the lot in one turn and prints the card, so the
agent never spends a Read on it either.

It changes nothing that plan/board.py would not change: the claim goes through
board.py's own rules (todo only, prerequisites done, tag claimable, board order, no
existing worktree or branch) and no flag overrides them. If board.py refuses, this
script refuses with the same message and leaves MAIN untouched.

Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

# --- project settings ---------------------------------------------------------

#: Gitignored directories a worktree shares with MAIN through a junction (Windows) or a
#: symlink, instead of holding its own copy: a build cache, an import database, a model
#: download folder. Only for cards whose header carries `SHARED_FLAG: yes`. Empty list if
#: the project has no such cache. Sharing one means only one process may use it at a time,
#: which is what the project's own run lock is for.
SHARED_DIRS = [{{SHARED_DIRS}}]

#: Card header flag that switches SHARED_DIRS on, written inline in the card header as
#: `<name>: yes` (e.g. "Model: opus. Phase: 1. Prerequisites: none. Docker: yes.").
SHARED_FLAG = "{{SHARED_FLAG}}"

#: Commands that make a fresh worktree usable, one argv list each.
#: Keep it to what a checkout genuinely needs; each one runs on every claim.
ENV_SETUP: list[list[str]] = [{{ENV_SETUP_ARGV}}]

#: Printed to the agent after the card. Keep it short; it is paid for on every turn
#: of the context that follows.
BRIEF = """\
Work in the worktree above; MAIN stays untouched until finish.py.
Stage nothing by hand and never edit plan/BOARD.md on the branch.

Budget for this context: {{BUDGET}}. When you reach it, or when the
harness warns that the context is filling, stop and checkpoint:

    python plan/finish.py {task} --checkpoint

then end the context. A fresh one resumes with `python plan/start.py --resume {task}`
and pays 60k of startup instead of the 300k this one has grown to. Splitting a card
across two contexts is cheaper than finishing it in one long one, because every turn
is billed the whole context.

When the acceptance test passes and Result is filled:

    python plan/finish.py {task}

which stages the card's Files, commits, merges into MAIN, verifies, marks the row done
and removes the worktree.
"""

# Importing plan/board.py would drop plan/__pycache__/ into MAIN, and an untracked directory
# there makes `git status --porcelain` non-empty, which is exactly what board.py refuses to
# claim against. Never write bytecode from these two scripts.
sys.dont_write_bytecode = True

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


def make_junctions(worktree: Path, main: Path) -> list[str]:
    """Link SHARED_DIRS in the worktree to MAIN's copies. Windows junctions; symlinks elsewhere."""
    made = []
    for name in SHARED_DIRS:
        target = main / name
        link = worktree / name
        if link.exists():
            made.append(f"{name} (already there)")
            continue
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            p = run(["cmd", "/c", "mklink", "/J", str(link), str(target)], worktree)
        else:
            p = run(["ln", "-s", str(target), str(link)], worktree)
        made.append(name if p.returncode == 0 else f"{name} FAILED: {(p.stderr or p.stdout).strip()}")
    return made


def print_card(path: Path, text: str) -> None:
    print(f"\n--- {path.as_posix()} ---")
    print(text.rstrip())
    print("--- end of card ---\n")


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
    if shared:
        print("shared:   " + ", ".join(make_junctions(worktree, main)) +
              "  (junctions to MAIN; remove with finish.py, never with a recursive delete)")
    for cmd in ENV_SETUP:
        r = run(cmd, worktree)
        print(f"env:      {' '.join(cmd)} -> exit {r.returncode}")

    if path is not None:
        print_card(worktree / "plan" / "tasks" / path.name, text)
    print(BRIEF.format(task=task))
    return 0


def cmd_resume(task: str) -> int:
    main = MODULE_DIR.parent
    board = load_board(main)
    _, rows = board.load()
    row = next((r for r in rows if r.task_id == task), None)
    if row is None:
        raise SystemExit(f"{task} is not on the board")
    if row.state not in ("in-progress", "waiting"):
        raise SystemExit(f"{task} is {row.status!r}; --resume only re-attaches to a row you already hold.")
    worktree = board.WORKTREES / task
    if not worktree.exists():
        raise SystemExit(f"no worktree at {worktree.as_posix()}; nothing to resume")

    path, _ = card_text(board, task)
    card_in_wt = worktree / "plan" / "tasks" / path.name if path else None
    text = card_in_wt.read_text(encoding="utf-8", errors="replace") if card_in_wt and card_in_wt.exists() else ""

    print(f"=== {task} resumed ({row.status}) ===")
    print(f"worktree: {worktree.as_posix()}")
    log = run(["git", "log", "--oneline", "-5", f"task/{task}"], main)
    print("branch commits so far:")
    for line in log.stdout.strip().splitlines():
        print("  " + line)
    dirty = run(["git", "status", "--porcelain"], worktree).stdout.strip()
    if dirty:
        n = len(dirty.splitlines())
        print(f"uncommitted in the worktree: {n} path(s) (checkpoint did not run, or work continued after it)")
    if card_in_wt:
        print_card(card_in_wt, text)
    print(BRIEF.format(task=task))
    print("Pick up from the Result section of the card: it is what the previous context left you.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Claim a task and set up its worktree in one turn.")
    p.add_argument("model", nargs="?", help="your model key (as on the board tag)")
    p.add_argument("task", nargs="?", help="task id; omit to take the next valid one")
    p.add_argument("--resume", metavar="Txx", help="re-attach to a task you checkpointed earlier")
    args = p.parse_args(argv)
    if args.resume:
        return cmd_resume(args.resume)
    if not args.model:
        p.error("give your model key, or --resume Txx")
    return cmd_start(args.model, args.task)


if __name__ == "__main__":
    sys.exit(main())
