"""Print (or run) the right `claude` command for the next card. Run this from a shell, not from an agent.

Usage (from MAIN, the repository root):

    python plan/launch.py                     what each model could pick up next, with its command
    python plan/launch.py opus55              the next card for one model
    python plan/launch.py opus55 --run        start Claude Code on it, here, interactively
    python plan/launch.py opus55 --task T12 --run   start that card (it must be claimable by the model)
    python plan/launch.py --resume T34        re-attach to a checkpointed card (model read from its row)
    python plan/launch.py opus55 --effort max override the effort for this one launch
    python plan/launch.py opus55 --permission-mode manual   watch a card instead of letting it run
    python plan/launch.py --answer T34 --run  answer a waiting card's questions for {{OWNER}} (see ANSWERERS)
    ... --run --headless                      `claude -p`: the session exits (and its window closes) when
                                              the agent ends its turn; transcript in Logs/agents/
    ... --run --detach                        the same session in a console window of its own (Windows) or
                                              a detached process logging to Logs/agents/ (elsewhere)

Cards run unattended in auto mode by default; see PERMISSION_MODE below.

This is the whole of what an orchestrator agent was doing, minus the orchestrator.
A coordinating session pays its own full context on every turn *in addition* to each
worker's and writes no code: the one measured on one project burned 165M cached tokens
over 618 turns to spawn, poll and message. Choosing a model and an effort level is table
lookup, so a table does it, for nothing.

Two things it decides that a pasted prompt cannot decide for itself:

  model   from the card's Model tag, which board.py already enforces at claim time.
  effort  reasoning effort, fixed when the process starts and unchangeable from inside
          the session -- which is why this lives here and not in plan/INVOKE.md.
          Thinking was ~86% of billed output tokens and output ~20% of the bill on the
          project this was measured on, so it is the largest lever after context length.

Effort precedence, cheapest source last: --effort > ${{ENV_PREFIX}}_EFFORT > the card's own
`Effort:` header > board.EFFORTS[model].

plan/autospawn.py imports AGENT_LOGS, MAIN, lease_held and load_board from here, and
plan/board.py and plan/finish.py read the leases through lease_held: keep those names.

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
ANSWER = MODULE_DIR / "ANSWER.md"
AGENT_LOGS = MAIN / "Logs" / "agents"

#: Who answers a waiting card's questions while {{OWNER}} is away, by the model holding the card:
#: (answering model, effort). Cheaper cards get a mid-tier model at medium; mid-tier and top-tier
#: cards get a peer at high, since their questions are the hard ones. plan/autospawn.py uses this.
#: A holder missing here is answered by its own model at high.
ANSWERERS: dict[str, tuple[str, str]] = {
    "fable": ("fable", "high"),
    "opus55": ("opus55", "high"),     # medium when the card itself runs at low/medium effort (see --answer)
    "sonnet": ("opus55", "medium"),
}

#: Environment variable that overrides effort for every launch, for a week when the
#: budget is tight (or generous). Same idea as CLAUDE_REASONING_EFFORT upstream.
EFFORT_ENV = "{{ENV_PREFIX}}_EFFORT"

#: Serial mode: set by --in-main and inherited by the session, so start/finish/board see it.
IN_MAIN_ENV = "{{ENV_PREFIX}}_IN_MAIN"

#: Permission mode every card runs in. Cards are unattended by design -- {{OWNER}} is not at
#: the keyboard, logs and outputs are the only feedback -- and a card that stops on a
#: permission prompt burns the whole context waiting. Auto mode still refuses genuinely
#: destructive operations (it is what blocked a `git checkout --` over uncommitted work
#: once), which is the behaviour a worktree protocol wants. Override per launch with
#: --permission-mode; `manual` is the one to use when watching a card you do not trust.
PERMISSION_MODE = "auto"
PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")


#: Tools no card called in an audited week, whose schemas were still sent with every request, and
#: the claude.ai connectors (--strict-mcp-config with no --mcp-config loads none). Measured with
#: one-word `claude -p` launches on one project: the starting context dropped from 33.2k to 21.0k
#: tokens, about a third (connectors 1.9k, fifteen tools 5.0k, Skill and its listing 5.3k), and that
#: context is read again on every request of the card. A card that needs one of these is launched
#: with --all-tools (or {{ENV_PREFIX}}_ALL_TOOLS=1). Re-audit the list against your own transcripts.
TRIM_TOOLS = ("CronCreate", "CronDelete", "CronList", "DesignSync", "EnterWorktree", "ExitWorktree", "NotebookEdit",
              "PushNotification", "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "SendMessage", "Workflow",
              "LSP", "ListAgents", "Skill")
ALL_TOOLS_ENV = "{{ENV_PREFIX}}_ALL_TOOLS"


def trim_flags() -> list[str]:
    """The flags that keep unused tools and connectors out of a card's context ([] when switched off)."""
    if os.environ.get(ALL_TOOLS_ENV) == "1":
        return []
    return ["--strict-mcp-config", "--disallowedTools", ",".join(TRIM_TOOLS)]


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


#: Effort of a resumed context by model, over the card's header. Measured on one project, output was
#: ~29% of the most expensive model's bill, and a context after a checkpoint iterates on a plan the
#: first one made, so it needs less thinking. Empty dict: a resume runs at the card's effort.
RESUME_EFFORT: dict[str, str] = {"fable": "medium"}


def effort_for(board, model: str, task_id: str | None, override: str | None,
               resume: bool = False) -> tuple[str, str]:
    """Return (level, where it came from)."""
    if override:
        return override, "--effort"
    env = os.environ.get(EFFORT_ENV, "").strip()
    if env:
        if env not in board.EFFORT_LEVELS:
            raise SystemExit(f"${EFFORT_ENV} is {env!r}; use one of {', '.join(board.EFFORT_LEVELS)}")
        return env, f"${EFFORT_ENV}"
    if resume and model in RESUME_EFFORT:
        return RESUME_EFFORT[model], f"RESUME_EFFORT for {model}"
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
previous context stopped — and carry on from there. If the branch ends in a `{task}: answers` commit, the
questions were answered by a stand-in for {{OWNER}} (each answer is under its question in the card) and the row is
already back to `in-progress`: do not run `board.py answered`; apply the answers as step 4 says and finish.
If `git log --oneline HEAD..{{MAIN_BRANCH}} -- plan/tasks/` lists a `Plan:` commit naming {task}, {{OWNER}} rescoped
the card after it was claimed: `git merge {{MAIN_BRANCH}}` in the worktree first and work to the card as it now reads.
Everything else in this prompt applies unchanged.
"""


def board_cli_model(model: str) -> str:
    """The `--model` value for a board key (board.CLI_MODELS, else the key itself is the alias)."""
    board = sys.modules.get("task_board") or load_board()
    return getattr(board, "CLI_MODELS", {}).get(model, model)


#: Appended by --no-budget (hand use only: autospawn blocks a card past its resume limit instead,
#: since an unbounded context once burned the usage window).
NO_BUDGET_NOTE = """
**No budget for this context.** {task} has already been checkpointed and resumed many times. Ignore the
per-context budget ({{BUDGET}}) and the advice to checkpoint when the context fills: do not
run `finish.py --checkpoint` again. Keep working in this context until the acceptance test passes and you
finish, or the card genuinely cannot be finished (`finish.py --blocked`). The project's token-economy rules
still apply.
"""


#: Appended on a resume by a model the card's tag does not name (autospawn --handoff: the later contexts of a
#: card tagged for the most expensive model). The expensive model diagnoses, a cheaper one iterates.
HANDED_NOTE = """
**{task} is tagged `{tag}` and its first context ran on that model; the later ones are yours.** Work toward the
Handoff's `Done when:` line. If the next step needs what the card reserves for the tagged model (a new diagnosis,
new math or design, a judgement the card says only it makes) rather than iteration against numbers, write it as a
Question for {{OWNER}} instead of guessing.
"""


#: Appended by --task (autospawn names the card it spawns for, so two spawns of one model take different cards).
TASK_NOTE = """

---

**This context is for {task}.** In step 2 run `python plan/start.py {model} {task}` instead of
`python plan/start.py {model}`: other sessions are being started on the other free cards at the same time. If
{task} is no longer claimable, stop. Everything else in this prompt applies unchanged.
"""


def command(model: str, effort: str, resume: str | None = None, mode: str = "",
            prompt: str | None = None, unbudgeted: bool = False, handed_tag: str | None = None,
            task: str | None = None) -> list[str]:
    """The argv that starts a card. The prompt is INVOKE.md itself, as {{OWNER}} would paste it."""
    if prompt is None:
        prompt = INVOKE.read_text(encoding="utf-8")
        if task and not resume:
            prompt += TASK_NOTE.format(task=task, model=model)
        if resume:
            prompt += RESUME_NOTE.format(task=resume)
            if unbudgeted:
                prompt += NO_BUDGET_NOTE.format(task=resume)
            if handed_tag:
                prompt += HANDED_NOTE.format(task=resume, tag=handed_tag)
    # the trim flags sit before --model: --disallowedTools takes a list and must not swallow the prompt
    return ["claude", *trim_flags(), "--model", board_cli_model(model), "--effort", effort,
            "--permission-mode", mode or PERMISSION_MODE, prompt]


def answer_prompt(board, task: str, holder_model: str, answerer: str) -> str:
    """plan/ANSWER.md filled for one card: {task} {holder} {answerer} {date} {worktree} {card} {card_name}."""
    import datetime as dt
    if not ANSWER.exists():
        raise SystemExit(f"--answer needs {ANSWER.as_posix()} (the stand-in answerer's prompt)")
    _, rows = board.load()
    row = next(r for r in rows if r.task_id == task)
    worktree = board.WORKTREES / task
    in_main = getattr(board, "in_main", None)
    if not worktree.exists() and in_main is not None and in_main(task):
        worktree = MAIN                         # serial mode: the card lives in MAIN
    card = sorted((worktree / "plan" / "tasks").glob(f"{task}-*.md"))
    if not card:
        raise SystemExit(f"no card for {task} in {worktree.as_posix()}")
    return ANSWER.read_text(encoding="utf-8").format(
        task=task, holder=board.MODELS.get(holder_model, holder_model),
        answerer=board.MODELS.get(answerer, answerer),
        date=dt.date.today().isoformat(), worktree=worktree.as_posix(), card=card[0].as_posix(),
        card_name=card[0].name) + f"\n\n(Row status now: {row.status})\n"


#: Open lease files, kept for the life of this process: the OS drops the lock when it exits.
_LEASES: list = []


def _try_lock(f) -> bool:
    """A non-blocking exclusive lock on the file's first byte (msvcrt on Windows, flock elsewhere)."""
    f.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    f.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _lease_path(task: str) -> Path:
    AGENT_LOGS.mkdir(parents=True, exist_ok=True)
    return AGENT_LOGS / f"{task}.lease"


def take_lease(task: str) -> None:
    """One live session per card. Held by this launch.py process until it exits, so a second launch
    (autospawn restarted and resuming the same checkpoint again, a hand resume, an answerer racing a
    resume) refuses instead of putting two agents in one worktree. A crashed session frees it."""
    f = _lease_path(task).open("a+")
    if not _try_lock(f):
        f.close()
        raise SystemExit(f"{task} already has a live session (lease {_lease_path(task)} is held); not starting another")
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()}\n")
    f.flush()
    _LEASES.append(f)


def lease_held(task: str) -> bool:
    """True while some launch.py process holds the card's lease. Used by plan/autospawn.py, and by
    plan/board.py so a claim does not refuse a card that is being launched."""
    path = _lease_path(task)
    if not path.exists():
        return False
    with path.open("a+") as f:
        if not _try_lock(f):
            return True
        _unlock(f)
    return False


#: Appended to every headless prompt. A card once started its test suite in the background and ended
#: its turn to wait for the notification; in `claude -p` that ends the session and kills the task.
HEADLESS_NOTE = """

---

**This session is headless (`claude -p`): the moment you end your turn, the session exits and every
background task you started is killed.** No one will reply and no notification will wake you. So never end your
turn to wait for something. Run long commands in the foreground (timeout up to 600000 ms). If one can take longer,
start it in the background and then block on it from a foreground command (a Monitor that waits until it is done,
or a foreground loop that checks its output file every minute for up to ten minutes, repeated), and only end your
turn when the card is finished, checkpointed, waiting on questions, or blocked.
"""


def tool_summary(inp: dict) -> str:
    """One line saying what a tool call does, for the headless console: its command, path or pattern."""
    for key in ("description", "command", "file_path", "pattern", "url", "prompt"):
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            line = " ".join(value.split())
            return line[:150] + ("..." if len(line) > 150 else "")
    return ""


def run_session(argv: list[str], headless: bool, name: str, task: str) -> int:
    """Interactive: hand the console to Claude. Headless: `claude -p`, which exits when the agent ends its
    turn, so an unattended console window closes instead of idling overnight. The stream-json transcript
    goes to Logs/agents/<stamp>-<name>.jsonl; the console shows the agent's text and tool names as they arrive."""
    take_lease(task)
    if not headless:
        return subprocess.call(argv, cwd=MAIN)
    import datetime as dt
    import json
    AGENT_LOGS.mkdir(parents=True, exist_ok=True)
    log = AGENT_LOGS / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{name}.jsonl"
    argv = argv[:1] + ["-p", "--output-format", "stream-json", "--verbose"] + argv[1:-1] + [argv[-1] + HEADLESS_NOTE]
    print(f"headless; transcript {log}", flush=True)
    with log.open("w", encoding="utf-8") as out, subprocess.Popen(
            argv, cwd=MAIN, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace") as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        print(b["text"].strip()[:2000], flush=True)
                    elif b.get("type") == "tool_use":
                        print(f"  [{b.get('name')}] {tool_summary(b.get('input') or {})}", flush=True)
            elif ev.get("type") == "result":
                print(f"== {ev.get('subtype')} after {ev.get('num_turns')} turns", flush=True)
        return proc.wait()


def detached(args: list[str], name: str) -> "subprocess.Popen":
    """`python plan/launch.py <args> --run` in a session of its own, and return at once. Windows: a new
    console window, which a headless session closes when the agent stops. Elsewhere: a new process
    session with no terminal, its console output in Logs/agents/<name>.console.log. plan/autospawn.py
    starts every card this way."""
    argv = [sys.executable, str(MODULE_DIR / "launch.py"), *[a for a in args if a not in ("--run", "--detach")], "--run"]
    if os.name == "nt":
        return subprocess.Popen(argv, cwd=MAIN, creationflags=subprocess.CREATE_NEW_CONSOLE)
    AGENT_LOGS.mkdir(parents=True, exist_ok=True)
    out = (AGENT_LOGS / f"{name}.console.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(argv, cwd=MAIN, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            start_new_session=True)
    out.close()                             # the child holds its own handle
    return proc


def shown(argv: list[str], resume: str | None = None) -> str:
    """The same command in a form {{OWNER}} can paste into a shell (PowerShell on Windows, sh elsewhere)."""
    head = " ".join(argv[:-1])
    if resume:
        # the resume note has to reach the session, so --run is the honest way to start one
        return f"python {Path('plan') / 'launch.py'} --resume {resume} --run"
    if os.name == "nt":
        return f'{head} "$(Get-Content -Raw plan\\INVOKE.md)"'
    return f'{head} "$(cat plan/INVOKE.md)"'


def describe(board, model: str, row, override: str | None, mode: str = "") -> list[str]:
    effort, source = effort_for(board, model, row.task_id if row else None, override)
    if row is None:
        return [f"{model:7} -- nothing claimable"]
    info = board._card_info(row.task_id)
    shared = "{{SHARED_FLAG}}" if info.get("shared") == "yes" else "no shared env"
    return [f"{model:7} {row.task_id}: {row.title}",
            f"        phase {row.phase}, tag {row.model}, {shared}; effort {effort} (from {source})",
            f"        {shown(command(model, effort, mode=mode))}"]


def pick_model_for(board, task_id: str) -> str:
    """The model key a held row belongs to, read back from the agent name written in it."""
    _, rows = board.load()
    row = next((r for r in rows if r.task_id == task_id), None)
    if row is None:
        raise SystemExit(f"{task_id} is not on the board")
    key = board.agent_model(row.status)
    if key:
        return key
    parts = [p for p in row.model.split("-or-") if p in board.MODELS]
    if not parts:
        raise SystemExit(f"cannot tell which model holds {task_id}: {row.status!r}")
    return parts[0]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    board = load_board()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", nargs="?", choices=sorted(board.MODELS), help="limit to one model")
    ap.add_argument("--resume", metavar="Txx", help="re-attach to a card checkpointed earlier")
    ap.add_argument("--effort", choices=board.EFFORT_LEVELS, help="override the effort for this launch")
    ap.add_argument("--permission-mode", choices=PERMISSION_MODES, default=PERMISSION_MODE,
                    help=f"permission mode for the session (default: {PERMISSION_MODE})")
    ap.add_argument("--answer", metavar="Txx", help="answer a waiting card's questions for {{OWNER}}")
    ap.add_argument("--task", metavar="Txx", help="with a model: start that card instead of the model's next one")
    ap.add_argument("--run", action="store_true", help="start Claude Code instead of printing the command")
    ap.add_argument("--headless", action="store_true", help="with --run: `claude -p`, exits when the agent stops")
    ap.add_argument("--detach", action="store_true",
                    help="with --run: start the session in its own console window (Windows) or detached, "
                         "logging to Logs/agents/ (elsewhere), and return at once")
    ap.add_argument("--no-budget", action="store_true",
                    help="with --resume: tell the session to ignore the per-context budget and not checkpoint again")
    ap.add_argument("--in-main", action="store_true",
                    help=f"serial mode: the card is worked directly in MAIN, no worktree (sets {IN_MAIN_ENV}=1)")
    ap.add_argument("--all-tools", action="store_true",
                    help=f"keep every tool, skill and connector in the session (sets {ALL_TOOLS_ENV}=1)")
    args = ap.parse_args(raw)
    if args.detach:
        if not args.run:
            raise SystemExit("--detach needs --run")
        name = "-".join(x for x in (args.answer or args.resume or args.task, args.model) if x) or "launch"
        proc = detached(raw, name)
        print(f"started in its own session (pid {proc.pid})")
        return 0
    if args.all_tools:
        os.environ[ALL_TOOLS_ENV] = "1"
    if args.in_main and os.environ.get(IN_MAIN_ENV) != "1":
        os.environ[IN_MAIN_ENV] = "1"           # inherited by the session, so start/finish/board see it
        board = load_board()

    if args.answer:
        task = args.answer
        holder = pick_model_for(board, task)
        who, effort = ANSWERERS.get(holder, (holder, "high"))
        if holder == "opus55" and board._card_info(task).get("effort") in ("low", "medium"):
            effort = "medium"                    # a card the planner rated cheap asks cheap questions
        if args.model:
            who = args.model
        effort = args.effort or effort
        argvv = command(who, effort, mode=args.permission_mode,
                        prompt=answer_prompt(board, task, holder, who))
        print(f"{task}: answer {holder}'s questions as {who}, effort {effort}")
        if args.run:
            return run_session(argvv, args.headless, f"{task}-answer-{who}", task)
        return 0

    if args.resume:
        task = args.resume
        model = args.model or pick_model_for(board, task)
        effort, source = effort_for(board, model, task, args.effort, resume=True)
        _, rows = board.load()
        tag = next((r.model for r in rows if r.task_id == task), "")
        argvv = command(model, effort, resume=task, mode=args.permission_mode, unbudgeted=args.no_budget,
                        handed_tag=tag if tag and model not in tag.split("-or-") else None)
        print(f"{task}: resume as {model}, effort {effort} (from {source})")
        print(f"  {shown(argvv, resume=task)}")
        if args.run:
            return run_session(argvv, args.headless, f"{task}-resume-{model}", task)
        return 0

    models = [args.model] if args.model else list(board.MODELS)
    _, rows = board.load()
    picks = {m: board.next_claimable(rows, m)[0] for m in models}
    if args.task:
        if not args.model:
            raise SystemExit("--task needs a model")
        row = next((r for r in rows if r.task_id == args.task), None)
        why = "not on the board" if row is None else board.claim_reason(row, rows, args.model)
        if why:
            raise SystemExit(f"{args.task} is not claimable by {args.model}: {why}")
        picks[args.model] = row

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
        return run_session(command(m, effort, mode=args.permission_mode, task=args.task), args.headless,
                           f"{picks[m].task_id}-{m}", picks[m].task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
