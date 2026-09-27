"""Finish a task: stage, commit, merge, verify, mark done, clean up. One command, one turn.

Usage (run from the worktree or from MAIN; both work):

    python plan/finish.py <Txx>                       the whole tail of the protocol
    python plan/finish.py <Txx> --add <path> ...      stage these on top of the card's Files
    python plan/finish.py <Txx> --checkpoint          commit work so far, keep the row, stop
    python plan/finish.py <Txx> --blocked "<reason>"  commit what exists, mark the row blocked
    python plan/finish.py <Txx> --verified <hash>     after a MOVED merge you re-tested by hand:
                                                      mark done and clean up

Replaces steps 12 to 16 of the old plan/INVOKE.md: stage by path, commit, return to MAIN,
check MAIN clean, merge --no-ff, the merge-base diff that decides UNCHANGED vs MOVED, the
cheap re-verify, `board.py done`, two `rmdir` calls for the junctions, `worktree remove`,
`branch -d`. That was 15 to 20 agent turns, each billed the full context of a card that is
by then 250k to 350k tokens long, i.e. millions of cached tokens of pure
bookkeeping per card. Here it is one turn, two when the merge lands on moved code.

The safety rules are kept, not relaxed:

- staging is by explicit path only, taken from the card's own "Files" section; there is no
  `git add -A` anywhere in this file, and a leftover modified path aborts the commit;
- `plan/BOARD.md` is never staged on a branch;
- questions gate the merge: a card with unanswered questions refuses to merge and tells the
  agent to set the row `waiting` instead;
- every board transition still goes through `plan/board.py`, which re-checks its own rules.

Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

# --- project settings ---------------------------------------------------------

#: The links start.py created in the worktree. Removed as links (never recursively: that
#: would erase MAIN's shared cache) before the worktree goes. Keep in step with start.py.
SHARED_DIRS = [{{SHARED_DIRS}}]
SHARED_FLAG = "{{SHARED_FLAG}}"

#: Cheap re-verification in MAIN after a merge that only re-applies the branch (UNCHANGED).
#: Keyed by the card's `<SHARED_FLAG>: yes|no` header. None = nothing to run.
#: The cheapest command that proves the merge is sound, NOT the card's full acceptance test:
#: the branch's own run already passed, and this only rechecks that the merge did not break
#: the build. None = nothing to run, and the merge is trusted as is.
VERIFY: dict[str, list[str] | None] = {
    "yes": [{{VERIFY_CMD}}],
    "no": [{{VERIFY_CMD_PLAIN}}],
}
#: Token the verify command prints when it succeeded (in addition to exit 0). "" = exit code only.
VERIFY_OK = "{{VERIFY_OK}}"

#: Co-authorship trailer appended to task commits. The agent passes --coauthor with the line
#: its own harness gave it; TASK_COAUTHOR in the environment is the fallback.
DEFAULT_COAUTHOR = os.environ.get("TASK_COAUTHOR", "")

# Importing plan/board.py would drop plan/__pycache__/ into MAIN, and an untracked directory
# there makes `git status --porcelain` non-empty, which is exactly what board.py refuses to
# claim against. Never write bytecode from these two scripts.
sys.dont_write_bytecode = True

MODULE_DIR = Path(__file__).resolve().parent


# --- plumbing -----------------------------------------------------------------


def git(*args: str, cwd: Path, check: bool = True) -> "subprocess.CompletedProcess[str]":
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
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


# --- card reading -------------------------------------------------------------


def section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def card_files(text: str) -> list[str]:
    """Repo-relative paths from the card's Files section: the backticked path of each bullet."""
    paths: list[str] = []
    for line in section(text, "Files").splitlines():
        if not line.lstrip().startswith(("-", "*")):
            continue
        m = re.search(r"`([^`]+)`", line)
        if not m:
            continue
        p = m.group(1).strip().replace("\\", "/").lstrip("./")
        if p and not p.startswith("<"):
            paths.append(p)
    return paths


def card_flag(text: str) -> str:
    """The card's `<SHARED_FLAG>: yes|no`. It sits inline in the header line
    ("Model: opus. Phase: 3. Prerequisites: T82. {{SHARED_FLAG}}: yes."), so this does not anchor to
    the start of a line; the search is capped to the header so later prose cannot match."""
    head = " ".join(text.splitlines()[:15])
    m = re.search(rf"{re.escape(SHARED_FLAG)}:\s*(yes|no)", head, re.I)
    return m.group(1).lower() if m else "no"


def questions(text: str) -> list[str]:
    return [l.strip() for l in section(text, "Questions for").splitlines()
            if re.match(r"^\s*\d+[.)]\s+\S", l)]


def result_filled(text: str) -> bool:
    r = section(text, "Result").strip()
    return bool(r) and "(filled by the executing agent)" not in r


# --- staging ------------------------------------------------------------------


def stage(worktree: Path, paths: list[str]) -> list[str]:
    """Stage each existing path (and its `<path>.meta` sibling, for asset pipelines that
    keep one per file, e.g. Unity). Returns what was staged."""
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


def commit(worktree: Path, message: str, coauthor: str) -> str | None:
    if not git("diff", "--cached", "--name-only", cwd=worktree).stdout.strip():
        return None
    full = message if not coauthor else f"{message}\n\nCo-Authored-By: {coauthor}"
    git("commit", "-q", "-m", full, cwd=worktree)
    return git("rev-parse", "--short", "HEAD", cwd=worktree).stdout.strip()


# --- steps --------------------------------------------------------------------


def wait_for_clean_main(main: Path, tries: int = 5) -> None:
    import time
    for i in range(tries):
        if not git("status", "--porcelain", cwd=main).stdout.strip():
            return
        if i < tries - 1:
            print("MAIN is dirty (another agent is mid-claim); waiting 30 s ...")
            time.sleep(30)
    raise SystemExit("MAIN still has uncommitted changes after 2.5 minutes. Stop and tell the owner.")


def remove_junctions(worktree: Path) -> None:
    for name in SHARED_DIRS:
        link = worktree / name
        if not link.exists() and not link.is_symlink():
            continue
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, text=True)
        else:
            link.unlink(missing_ok=True)


def cleanup(main: Path, board, task: str, worktree: Path) -> None:
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
    return 0


# --- main ---------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    main = find_main()
    board = load_board(main)
    task = args.task
    worktree = board.WORKTREES / task

    _, rows = board.load()
    row = next((r for r in rows if r.task_id == task), None)
    if row is None:
        raise SystemExit(f"{task} is not on the board")

    if args.verified:
        if not worktree.exists():
            raise SystemExit(f"no worktree at {worktree.as_posix()}")
        print(f"{task}: acceptance test reported passing in MAIN; marking done.")
        return finalize(main, board, task, args.verified, worktree)

    if not worktree.exists():
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
        qs = questions(text)
        if qs and row.state == "in-progress":
            print(f"{len(qs)} question(s) in the card and the row is still in-progress. The merge is gated.")
            print(f"Run: python plan/board.py waiting {task} \"{len(qs)} questions\"")
            print("then ask the owner in this context, apply the answers, and run finish.py again.")
            print("(To park the work meanwhile: python plan/finish.py %s --checkpoint)" % task)
            return 1
        if not result_filled(text):
            print("The card's Result section is still the placeholder. Fill it (what was built, what the "
                  "acceptance test printed, deviations) and run finish.py again.")
            return 1

    # 2. stage
    paths = card_files(text) + list(args.add or [])
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

    # 2. commit on the branch
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
        return board_cmd(main, "blocked", task, args.blocked)

    if args.checkpoint:
        print(f"\nCheckpointed. End this context now; a fresh one continues with:\n"
              f"    python plan/start.py --resume {task}\n"
              f"Make sure the card's Result section says where you stopped: it is the whole handover.")
        return 0

    # 3. merge into MAIN
    wait_for_clean_main(main)
    before = git("rev-parse", "HEAD", cwd=main).stdout.strip()
    p = git("merge", "--no-ff", f"task/{task}", "-m", f"{task}: merge", cwd=main, check=False)
    if p.returncode != 0:
        git("merge", "--abort", cwd=main, check=False)
        print(tail(p.stdout + p.stderr, 15))
        print("\nMerge conflicted and was aborted. A conflict means a card touched a file it does not own.")
        board_cmd(main, "blocked", task, "merge conflict")
        return 1
    merge_hash = git("rev-parse", "--short", "HEAD", cwd=main).stdout.strip()
    print(f"merged: {merge_hash} ({task}: merge)")

    # 5. did main move under us while the card was being built?
    base = git("merge-base", before, f"task/{task}", cwd=main).stdout.strip()
    moved = git("diff", "--quiet", base, before, "--", ".", ":!plan/BOARD.md",
                cwd=main, check=False).returncode != 0

    flag = card_flag(text)
    verify_cmd = VERIFY.get(flag)

    if moved:
        print("\nMOVED: other merges landed on main while this card was built, so the branch's own "
              "acceptance run no longer proves the merged state.")
        print("Run the card's full acceptance test here in MAIN (compile, tests, the renders it lists),")
        print(f"then: python plan/finish.py {task} --verified {merge_hash}")
        return 0

    print("UNCHANGED: the merge only re-applies this branch, whose acceptance test passed in the worktree.")
    if verify_cmd:
        print(f"verifying in MAIN: {' '.join(verify_cmd)}")
        v = subprocess.run(verify_cmd, cwd=main, capture_output=True, text=True)
        out = v.stdout + v.stderr
        print(tail(out, 25))
        if v.returncode != 0 or (VERIFY_OK and VERIFY_OK not in out):
            print(f"\nVerification failed in MAIN. The merge commit {merge_hash} stands; fix on main or "
                  f"reset it with `git reset --hard {before}` and reopen the branch. The row is NOT done.")
            return 1

    return finalize(main, board, task, merge_hash, worktree)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Commit, merge, verify and close out a task in one turn.")
    p.add_argument("task")
    p.add_argument("--add", action="append", metavar="PATH",
                   help="stage this path too (repeatable); use it only for files also added to the card's Files section")
    p.add_argument("--checkpoint", action="store_true", help="commit progress on the branch and stop, keeping the row")
    p.add_argument("--blocked", metavar="REASON", help="commit what exists and mark the row blocked")
    p.add_argument("--verified", metavar="HASH", help="a MOVED merge whose acceptance test you just re-ran in MAIN")
    p.add_argument("--allow-dirty", action="store_true", help="leave unlisted changed paths uncommitted on purpose")
    p.add_argument("--coauthor", default=DEFAULT_COAUTHOR, metavar="LINE",
                   help="Co-Authored-By value for the commit trailer")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
