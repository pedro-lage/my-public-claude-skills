"""Print (or run) the right `claude` command for the next card. Run this from a shell, not from an agent.

Usage (from MAIN, the repository root):

    python plan/launch.py                   what each model could pick up next, with its command
    python plan/launch.py opus              the next card for one model
    python plan/launch.py opus --run        start Claude Code on it, here, interactively
    python plan/launch.py --resume T34      re-attach to a checkpointed card (model read from its row)
    python plan/launch.py opus --effort max override the effort for this one launch
    python plan/launch.py opus --permission-mode manual   watch a card instead of letting it run

Cards run unattended in auto mode by default; see PERMISSION_MODE below.

This is the whole of what an orchestrator agent was doing, minus the orchestrator.
A coordinating session pays its own full context on every turn *in addition* to each
worker's and writes no code: the one measured on the project this skill came from burned
165M cached tokens over 618 turns to spawn, poll and message. Choosing a model and an effort level is table
lookup, so a table does it, for nothing.

Two things it decides that a pasted prompt cannot decide for itself:

  model   from the card's Model tag, which board.py already enforces at claim time.
  effort  reasoning effort, fixed when the process starts and unchangeable from inside
          the session -- which is why this lives here and not in plan/INVOKE.md.
          Thinking was ~86% of billed output tokens and output ~20% of the bill on the
          project this was measured on, so it is the largest lever after context length.

Effort precedence, cheapest source last: --effort > ${{EFFORT_ENV}} > the card's own
`Effort:` header > board.EFFORTS[model].

Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
MAIN = MODULE_DIR.parent
INVOKE = MODULE_DIR / "INVOKE.md"

#: Environment variable that overrides effort for every launch, for a week when the
#: budget is tight (or generous). Same idea as CLAUDE_REASONING_EFFORT upstream.
EFFORT_ENV = "{{EFFORT_ENV}}"

#: Permission mode every card runs in. Cards are unattended by design -- {{OWNER}} is not at
#: the keyboard, renders and logs are the only feedback -- and a card that stops on a
#: permission prompt burns the whole context waiting. Auto mode still refuses genuinely
#: destructive operations (it is what blocked a `git checkout --` over uncommitted work
#: here), which is the behaviour a worktree protocol wants. Override per launch with
#: --permission-mode; `manual` is the one to use when watching a card you do not trust.
PERMISSION_MODE = "auto"
PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")


def load_board():
    """Import plan/board.py as a module without requiring a package or a __pycache__."""
    sys.dont_write_bytecode = True          # a stray plan/__pycache__ makes MAIN dirty
    spec = importlib.util.spec_from_file_location("task_board", MODULE_DIR / "board.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot import plan/board.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod            # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


def effort_for(board, model: str, task_id: str | None, override: str | None) -> tuple[str, str]:
    """Return (level, where it came from)."""
    if override:
        return override, "--effort"
    env = os.environ.get(EFFORT_ENV, "").strip()
    if env:
        if env not in board.EFFORT_LEVELS:
            raise SystemExit(f"${EFFORT_ENV} is {env!r}; use one of {', '.join(board.EFFORT_LEVELS)}")
        return env, f"${EFFORT_ENV}"
    if task_id:
        carried = board._card_info(task_id).get("effort")
        if carried:
            return carried, "card header"
    return board.EFFORTS.get(model, "medium"), "default for " + model


#: Appended to INVOKE.md when resuming, because INVOKE.md's step 2 otherwise sends the
#: session off to claim a *new* card. Without this the resume flag would only work for a
#: human reading the printed reminder, not for --run.
RESUME_NOTE = """

---

**This context resumes {task}, which an earlier context checkpointed.** In step 2 run
`python plan/start.py --resume {task}` instead of `python plan/start.py <model>`: the row is already
yours and the branch already holds the work. Read the card's "Result" section first — it says where the
previous context stopped — and carry on from there. Everything else in this prompt applies unchanged.
"""


def command(model: str, effort: str, resume: str | None = None, mode: str = "") -> list[str]:
    """The argv that starts a card. The prompt is INVOKE.md itself, as {{OWNER}} would paste it."""
    prompt = INVOKE.read_text(encoding="utf-8")
    if resume:
        prompt += RESUME_NOTE.format(task=resume)
    return ["claude", "--model", model, "--effort", effort,
            "--permission-mode", mode or PERMISSION_MODE, prompt]


def shown(argv: list[str], resume: str | None = None) -> str:
    """The same command in a form {{OWNER}} can paste into a shell."""
    head = " ".join(argv[:-1])
    if resume:
        # the resume note has to reach the session, so --run is the honest way to start one
        return f"python plan\\launch.py --resume {resume} --run"
    return f'{head} "$(Get-Content -Raw plan\\INVOKE.md)"'


def describe(board, model: str, row, override: str | None, mode: str = "") -> list[str]:
    effort, source = effort_for(board, model, row.task_id if row else None, override)
    if row is None:
        return [f"{model:7} -- nothing claimable"]
    info = board._card_info(row.task_id)
    shared = "{{SHARED_FLAG}}" if info.get("shared") == "yes" else "no shared cache"
    return [f"{model:7} {row.task_id}: {row.title}",
            f"        phase {row.phase}, tag {row.model}, {shared}; effort {effort} (from {source})",
            f"        {shown(command(model, effort, mode=mode))}"]


def pick_model_for(board, task_id: str) -> str:
    """The model key a held row belongs to, read back from the agent name written in it."""
    _, rows = board.load()
    row = next((r for r in rows if r.task_id == task_id), None)
    if row is None:
        raise SystemExit(f"{task_id} is not on the board")
    for key, name in board.MODELS.items():
        if name in row.status:
            return key
    parts = [p for p in row.model.split("-or-") if p in board.MODELS]
    if not parts:
        raise SystemExit(f"cannot tell which model holds {task_id}: {row.status!r}")
    return parts[0]


def main(argv: list[str] | None = None) -> int:
    board = load_board()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", nargs="?", choices=sorted(board.MODELS), help="limit to one model")
    ap.add_argument("--resume", metavar="Txx", help="re-attach to a card checkpointed earlier")
    ap.add_argument("--effort", choices=board.EFFORT_LEVELS, help="override the effort for this launch")
    ap.add_argument("--permission-mode", choices=PERMISSION_MODES, default=PERMISSION_MODE,
                    help=f"permission mode for the session (default: {PERMISSION_MODE})")
    ap.add_argument("--run", action="store_true", help="start Claude Code instead of printing the command")
    args = ap.parse_args(argv)

    if args.resume:
        task = args.resume
        model = args.model or pick_model_for(board, task)
        effort, source = effort_for(board, model, task, args.effort)
        argvv = command(model, effort, resume=task, mode=args.permission_mode)
        print(f"{task}: resume as {model}, effort {effort} (from {source})")
        print(f"  {shown(argvv, resume=task)}")
        if args.run:
            return subprocess.call(argvv, cwd=MAIN)
        return 0

    models = [args.model] if args.model else list(board.MODELS)
    _, rows = board.load()
    picks = {m: board.next_claimable(rows, m)[0] for m in models}

    for m in models:
        for line in describe(board, m, picks[m], args.effort, args.permission_mode):
            print(line)

    if args.run:
        m = args.model or next((m for m in models if picks[m]), None)
        if m is None or picks[m] is None:
            print("\nNothing to run.")
            return 1
        effort, _ = effort_for(board, m, picks[m].task_id, args.effort)
        print(f"\nstarting {m} on {picks[m].task_id} ...")
        return subprocess.call(command(m, effort, mode=args.permission_mode), cwd=MAIN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
