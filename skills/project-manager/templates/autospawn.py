"""Watch the board and start an agent, in its own console window, whenever a card frees up.

Usage (from MAIN, in a shell of its own; Ctrl-C stops it, running agents keep going):

    python plan/autospawn.py                        every model, at most 4 agents at once
    python plan/autospawn.py --models opus55,sonnet only these models
    python plan/autospawn.py --max 2 --every 60     cap and poll interval (seconds)
    python plan/autospawn.py --once --dry-run       print what it would start, then exit

Each spawn is `python plan/launch.py <model> --run` in a new console, so model, effort and
permission mode come from launch.py exactly as for a hand launch. No tokens are spent here:
this is a plain loop reading plan/BOARD.md.

Rules that keep it from double-spawning:
  * each spawn names its card (`launch.py <model> --task Txx`), and a card stays pending until it
    leaves `todo` (the agent claimed it) or --grace minutes pass; one model may have several spawns
    pending at once (a one-pending-spawn-per-model rule left --max unused, and an unnamed spawn could
    pick the card another model's spawn was already starting on);
  * a card is spawned at most once per run of this script (a card that went back to `todo`
    after a failed start is left for {{OWNER}}, not retried in a loop);
  * `--max` counts `in-progress` and `waiting` rows plus pending spawns (--max 1 is fully serial);
  * every session holds an OS lock on Logs/agents/Txx.lease for its whole life (launch.take_lease):
    a second launch for the same card refuses, and this script skips a card whose lease is held.
    An in-memory `resumed` set alone did not survive a restart of this script, so a restart re-resumed
    every card still parked on its checkpoint while the first resumed session was working;
  * launches are LAUNCH_GAP_S apart.

Checkpointed cards (an `in-progress` row whose task/Txx branch ends in a `Txx: checkpoint`
commit) are resumed with `launch.py --resume Txx --run`, once per checkpoint commit and at
most --resumes times per card (default 10). The count is the number of checkpoint commits on
the branch (or on main in serial mode) since its newest `Txx: answers` commit (an answer is a new
direction and restarts the count), so it survives restarts of this script. Past --resumes the
row is moved to `blocked` (`board.py blocked`) and left for {{OWNER}}: an unbudgeted resume once let
one context run unbounded and burn the usage window (launch.py --no-budget remains for hand use).
A card resumed by hand before its next checkpoint would be resumed a second time; do not
mix hand resumes with this script.

Waiting cards (questions for {{OWNER}}) wait for {{OWNER}} by default. Stand-in answers are off
(--answers 0) because, measured on one project, they turned planner guesses into requirements and
spawned new cards: a stand-in has no authority to decide, but its answer reads like a decision to
every later context. {{OWNER}} answers under each question in the card, commits `Txx: answers` on the
branch and runs `board.py answered`, and this script resumes the card like a checkpoint. Only with
`--answers N` (N > 0) are they answered by a stand-in so the board runs unattended:
`launch.py --answer Txx` (model and effort from launch.ANSWERERS, prompt in plan/ANSWER.md). The
stand-in writes the answers in the card, commits `Txx: answers` on the branch and runs
`board.py answered`, and the card is then resumed like a checkpoint (answer resumes do not count
against --resumes). A question only {{OWNER}} can answer turns the row `blocked`, which this script
leaves alone. At most --answers rounds per card (counted from `Txx: waiting` commits on main);
later rounds are left for {{OWNER}}.

--in-main (only with --max 1) is serial mode: no worktrees, branches or merges; each card is
worked directly in MAIN ({{ENV_PREFIX}}_IN_MAIN=1, see board.IN_MAIN) and its checkpoints and answers
are commits on main. Nothing else runs, so there is nothing to merge against or re-verify.

Two launch modes, for new sessions only (a running session keeps the mode it started in):

  headless (default, for when {{OWNER}} is away): `claude -p` (launch.py --headless). The session
      exits when the agent ends its turn, so its console window closes instead of idling overnight;
      the window shows the agent's text and each tool call with a one-line summary, and the full
      stream-json transcript goes to Logs/agents/. On POSIX there is no new window: the launch runs
      detached in its own session and its console output goes to Logs/agents/autospawn-*.log.
  interactive (for when {{OWNER}} is watching): the normal Claude Code screen in each window, where
      they can read everything and type into the session. The window stays open after the agent
      stops and the card's lease is held until it is closed, so a checkpointed or waiting card is
      resumed only after its window is closed. No transcript file, so no orphan detection. On POSIX
      it needs TERMINAL_CMD (a detached process has no terminal to draw in).

Handoff (--handoff, default `fable:opus55` when both keys are in board.MODELS): the expensive model
scopes and starts the work and the cheaper one iterates, catches the bugs and runs the tests. A card
whose tag names the first model is claimed by it and worked by it in its first context only; every
later context (checkpoint resume, orphan or usage-limit restart, stand-in answer) runs as the second
(`launch.py <second> --resume Txx`), whoever holds the row, with or without a checkpoint commit (a
first context that hit the usage limit before checkpointing was otherwise restarted on the expensive
model). While the first is at its usage limit the second claims the cards whose tag names it too.
Cards tagged for the first alone are handed the same way, but only the first claims them. Measured
on one project: the expensive model was 89% of card spend at three times the price per request,
mostly in tuning loops; it diagnoses, the cheaper one iterates. `--handoff ""` turns it off.

A session that ends on a usage limit (HTTP 429, "resets 6:10pm") is not an orphan: nothing it covers
is spawned, resumed or answered until the reset time read from the transcript (`limit_until`), and
the card is resumed once after the reset without spending an orphan retry. A session or weekly limit
("You've hit your session limit") is the whole account's, so every model waits (`limit_scope`); only
a limit that names a model (its board.MODELS display name) holds that model alone, and its `-or-`
cards then fall to the next model.

Pick the mode at start (`--mode interactive`, or `--interactive`) or switch a running controller
from any shell: `python plan/autospawn.py --set-mode interactive` (or `headless`), which writes
Logs/autospawn.mode; the controller reads that file on every poll and logs the switch.

Idle job (IDLE_JOB, off by default): a regression net or any other long job that needs the shared
environment, run in MAIN only when the board is idle (no card running or pending, none claimable).
`due_argv` decides whether a run is due (exit 0) and `run_argv` is the run, started in a console
of its own. It never starts on a busy board: measured on one project, a run that began in the same
second as four cards held the shared build environment for 50+ minutes, and every finish.py queued
behind it. --no-idle-job turns it off for one run of this script.

Standard library only. Log: Logs/autospawn.log (keep Logs/ ignored by git).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from launch import AGENT_LOGS, MAIN, lease_held, load_board  # noqa: E402  (plan/ is on sys.path when run as a script)

LOG = MAIN / "Logs" / "autospawn.log"

#: The regression net, or None (off). When set: `(due_argv, run_argv)`, two argv lists run with cwd=MAIN
#: (a leading "python" is replaced by this interpreter). On an idle board only, at most once per
#: IDLE_JOB_GAP_S, autospawn runs `due_argv` and, when it exits 0, starts `run_argv` in a console of its
#: own and does not wait for it. `due_argv` should exit non-zero when nothing changed since the last run
#: (e.g. `git diff --quiet <last head> HEAD -- <build paths>`) and also while a hand run holds the shared
#: environment; `run_argv` records its own verdict and files a failure itself (`board.py suggest`).
#: Example: (["python", "plan/net_run.py", "--due"], ["python", "plan/net_run.py"]).
IDLE_JOB: tuple[list[str], list[str]] | None = None

#: Minimum seconds between two idle-job starts, so a due check that keeps saying yes (a run that
#: failed before recording its verdict) does not relaunch it every poll.
IDLE_JOB_GAP_S = 1800

#: POSIX only: argv prefix that opens a terminal window running the rest, for interactive mode
#: (e.g. ["x-terminal-emulator", "-e"] or ["tmux", "new-window"]). None: interactive mode is refused
#: on POSIX. Windows always uses a new console.
TERMINAL_CMD: list[str] | None = None

#: Default --handoff, used only when both keys are in board.MODELS.
DEFAULT_HANDOFF = "fable:opus55"

#: Environment variable inherited by every launch in serial mode (board.IN_MAIN reads it).
IN_MAIN_ENV = "{{ENV_PREFIX}}_IN_MAIN"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


MODES = ("headless", "interactive")
MODE_FILE = MAIN / "Logs" / "autospawn.mode"
mode = "headless"                                # set by main(), then by MODE_FILE on every poll


def read_mode_file() -> str | None:
    try:
        value = MODE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if value in MODES else None


def interactive_ok() -> bool:
    return os.name == "nt" or TERMINAL_CMD is not None


#: Seconds between two launches: a batch started in one second raced its own claims (board.py's
#: being-launched exemption) and its first tool calls; the gap staggers them at no real cost.
LAUNCH_GAP_S = 5
_last_launch = 0.0


def new_console(argv: list[str], label: str, interactive: bool = False) -> subprocess.Popen:
    """Start argv in MAIN without waiting for it. Windows: a console window of its own. POSIX: interactive
    runs under TERMINAL_CMD; anything else is detached in its own session (it survives this script and
    Ctrl-C here) with its output in Logs/agents/autospawn-<stamp>-<label>.log (not .jsonl: those are
    session transcripts, which the orphan and usage-limit checks read)."""
    if os.name == "nt":
        return subprocess.Popen(argv, cwd=MAIN, creationflags=subprocess.CREATE_NEW_CONSOLE)
    if interactive:
        return subprocess.Popen([*(TERMINAL_CMD or []), *argv], cwd=MAIN, start_new_session=True)
    AGENT_LOGS.mkdir(parents=True, exist_ok=True)
    out = (AGENT_LOGS / f"autospawn-{dt.datetime.now():%Y%m%d-%H%M%S}-{label}.log").open("ab")
    try:
        return subprocess.Popen(argv, cwd=MAIN, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    finally:
        out.close()                              # the child keeps its own copy of the descriptor


def launch(*args: str) -> None:
    """launch.py in a console of its own; headless sessions close it when the agent stops."""
    global _last_launch
    time.sleep(max(0.0, _last_launch + LAUNCH_GAP_S - time.monotonic()))
    extra = ["--headless"] if mode == "headless" else []
    new_console([sys.executable, str(MAIN / "plan" / "launch.py"), *args, "--run", *extra],
                "-".join(a.lstrip("-") for a in args), interactive=mode == "interactive")
    _last_launch = time.monotonic()


def spawn(model: str, task_id: str) -> None:
    launch(model, "--task", task_id)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=MAIN, capture_output=True, text=True).stdout.strip()


BOARD_MOVES = ("claim", "waiting", "answered", "blocked", "done")


def since_answers(task_id: str, subjects: list[str]) -> int:
    """`Txx: checkpoint` commits in `subjects` (newest first) after the newest `Txx: answers`: an answer
    is a new direction from {{OWNER}} and restarts the resume budget (a card was once resumed for eight
    checkpoints of a design that had just been rejected)."""
    n = 0
    for s in subjects:
        if s == f"{task_id}: answers":
            break
        n += s == f"{task_id}: checkpoint"
    return n


def parked(task_id: str) -> tuple[str, int, str] | None:
    """(tip sha, checkpoints since the last answers, kind) when task/<id> ends in a `Txx: checkpoint`
    or a `Txx: answers` commit, else None. In serial mode (no branch) the same is read from main:
    the newest `Txx: ...` commit that is not a board move, back to the claim."""
    if not git("branch", "--list", f"task/{task_id}"):
        subjects: list[str] = []
        latest = None
        for line in git("log", "--format=%H %s", board_main(), f"--grep=^{task_id}: ").splitlines():
            sha, subj = line.split(" ", 1)
            what = subj[len(task_id) + 2:]
            if what == "claim":
                break
            if what in BOARD_MOVES:
                continue
            if latest is None:
                latest = (sha, what)
            subjects.append(subj)
        if latest and latest[1] in ("checkpoint", "answers"):
            return latest[0], since_answers(task_id, subjects), latest[1]
        return None
    tip = git("log", "-1", "--format=%H %s", f"task/{task_id}")
    kind = next((k for k in ("checkpoint", "answers") if tip.endswith(f" {task_id}: {k}")), None)
    if kind is None:
        return None
    subjects = git("log", "--format=%s", f"{board_main()}..task/{task_id}").splitlines()
    return tip.split(" ", 1)[0], since_answers(task_id, subjects), kind


#: What finish.py and board.py print when MAIN is dirty. A session that ended on it is done but could not merge.
DIRTY_MAIN_MARKS = ("MAIN still has uncommitted changes", "MAIN has uncommitted changes")


def _tail(path, size: int) -> str:
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - size))
        return f.read().decode("utf-8", "replace")


def ended_on_dirty_main(path) -> bool:
    """True when the session's last 64 KB show finish.py or board.py refusing a dirty MAIN. A finished card
    whose finish.py refused an untracked file of the owner's in MAIN was otherwise resumed three times as an
    orphan, each fresh context re-reading the card to write the same "commit or move that file" report."""
    return any(m in _tail(path, 65536) for m in DIRTY_MAIN_MARKS)


def main_dirty() -> bool:
    """Any change in MAIN, untracked files included: a card's worktree may run an older finish.py that
    refuses those too, so the session is resumed only once MAIN is fully clean."""
    return bool(git("status", "--porcelain"))


def last_transcript(task_id: str):
    """The newest Logs/agents/<stamp>-<Txx>-*.jsonl a headless session wrote for this card, or None."""
    found = sorted(AGENT_LOGS.glob(f"*-{task_id}-*.jsonl"))
    return found[-1] if found else None


def session_ended(path, lease_free: bool = False) -> bool:
    """True when the transcript has been quiet for 2 minutes and holds the final `result` event, or, with
    `lease_free` (no launch.py holds the card's lease, so the process is gone), without one: a session killed
    mid-turn (a crash, a power cut) writes no `result`, and a power cut once left four cards never resumed."""
    if time.time() - path.stat().st_mtime < 120:
        return False
    if lease_free:
        return True
    return '"type":"result"' in _tail(path, 65536).replace(" ", "")


# The message's shape, from a final `result` event with "api_error_status":429:
# "You've hit your session limit · resets 2:50am (America/Sao_Paulo)".
LIMIT_RE = re.compile(r"resets\s+(?:[A-Za-z]{3,9}\s+\d{1,2},?\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)", re.I)


def limit_until(path) -> float | None:
    """When the session ended on a usage limit (HTTP 429, "You've hit your session/weekly limit · resets 6:10pm"),
    the epoch time it resets: the first such clock time after the transcript's last write, or one hour after it
    when the text names no time. None for any other ending. A card once burned its ten orphan resumes in
    27 minutes this way, each relaunch dying on its first request."""
    tail = _tail(path, 8192)
    if '"api_error_status":429' not in tail.replace(" ", ""):
        return None
    ended = dt.datetime.fromtimestamp(path.stat().st_mtime)
    m = LIMIT_RE.search(tail)
    if not m:
        return ended.timestamp() + 3600
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    at = ended.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if at <= ended:
        at += dt.timedelta(days=1)               # a weekly reset days away is retried daily: one cheap 429 each
    return at.timestamp() + 120


def limit_names(models: dict[str, str]) -> dict[str, str]:
    """Lower-cased family word of each display name -> the first model key that carries it
    ("Claude Opus 5.5" -> "opus": "opus55"); the word a usage-limit message names a model by."""
    names: dict[str, str] = {}
    for key, display in models.items():
        words = [w for w in display.split() if w.lower() != "claude"]
        if words and words[0].isalpha():
            names.setdefault(words[0].lower(), key)
    return names


_model_limit_re: re.Pattern | None = None


def model_limit_re(models: dict[str, str]) -> re.Pattern | None:
    """MODEL_LIMIT_RE built from board.MODELS display names: "hit your <family> ... limit"."""
    global _model_limit_re
    if _model_limit_re is None:
        names = limit_names(models)
        if names:
            _model_limit_re = re.compile(
                r"hit your (" + "|".join(map(re.escape, names)) + r")\b[^\"\n]{0,30}?limit", re.I)
    return _model_limit_re


def limit_scope(path, keys) -> str | None:
    """The model key a usage limit names ("your Opus weekly limit" -> the opus key), or None when it is the
    whole account's: the session and weekly limits ("You've hit your session limit") stop every model."""
    tail = _tail(path, 8192)
    if '"model_requires_usage_credits"' in tail:   # "You're out of usage credits. Switch to another model": this model only
        model = path.stem.rsplit("-", 1)[-1]
        return model if model in keys else None
    pattern = model_limit_re(_board.MODELS)
    m = pattern.search(tail) if pattern else None
    if not m:
        return None
    key = limit_names(_board.MODELS).get(m.group(1).lower())
    return key if key in keys else None


def model_limits(keys) -> dict[str, float]:
    """model key -> reset time, from the newest transcripts that ended on a usage limit (name ends -<model>.jsonl).
    An account-wide limit covers every model in `keys`; a later session that got through clears it."""
    limits: dict[str, float] = {}
    account: float | None = None
    for t in sorted(AGENT_LOGS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-30:]:
        until = limit_until(t)
        model = t.stem.rsplit("-", 1)[-1]
        if until is None:
            account = None                       # a later session got through: the account is not limited
            limits.pop(model, None)              # nor is this model
        elif (scoped := limit_scope(t, keys)) is not None:
            limits[scoped] = until
        else:
            account = until
    if account is not None:
        for m in keys:
            limits[m] = max(limits.get(m, 0.0), account)
    return limits


def waiting_commits(task_id: str) -> list[str]:
    """Shas of the `Txx: waiting` board commits on main, newest first."""
    out = git("log", "--format=%H %s", board_main(), "--", "plan/BOARD.md")
    return [l.split(" ", 1)[0] for l in out.splitlines() if l.endswith(f" {task_id}: waiting")]


_board = None                                    # plan/board.py as a module, set by main()


def board_main() -> str:
    return _board.MAIN_BRANCH


def idle_job_argv(argv: list[str]) -> list[str]:
    return [sys.executable, *argv[1:]] if argv and argv[0] == "python" else list(argv)


def idle_job_due() -> bool:
    return subprocess.run(idle_job_argv(IDLE_JOB[0]), cwd=MAIN).returncode == 0


def resume(model: str, task_id: str) -> None:
    launch(model, "--resume", task_id)


_prereq_logged: set[str] = set()


def prereqs_pending(board, row, rows) -> bool:
    """A prerequisite {{OWNER}} added after the claim keeps the card from being resumed (start.py --resume
    refuses it too); the row is left as it is and the wait is logged once per prerequisite set."""
    by_id = {r.task_id: r for r in rows}
    archived = board.archived_ids() if any(p not in by_id for p in row.prereqs) else set()
    pending = [p for p in row.prereqs if (by_id[p].state != "done" if p in by_id else p not in archived)]
    key = f"{row.task_id}:{','.join(pending)}"
    if pending and key not in _prereq_logged:
        _prereq_logged.add(key)
        log(f"{row.task_id} not resumed: prerequisite not done: {', '.join(pending)}")
    return bool(pending)


def block(task_id: str, n: int, limit: int) -> None:
    """Past --resumes: the row goes to `blocked` for {{OWNER}} instead of an unbudgeted resume."""
    reason = f"checkpointed {n} times (limit {limit}); left for {{OWNER}}"
    r = subprocess.run([sys.executable, str(MAIN / "plan" / "board.py"), "blocked", task_id, reason],
                       cwd=MAIN, capture_output=True, text=True)
    log(f"{task_id}: {reason}" + ("" if r.returncode == 0 else f" (board.py blocked failed: {(r.stderr or r.stdout).strip()[:200]})"))


def answer(task_id: str, model: str | None = None) -> None:
    launch(*([model] if model else []), "--answer", task_id)


def main(argv: list[str] | None = None) -> int:
    global _board
    board = _board = load_board()
    first_default, _, second_default = DEFAULT_HANDOFF.partition(":")
    default_handoff = DEFAULT_HANDOFF if {first_default, second_default} <= set(board.MODELS) else ""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=",".join(board.MODELS), help="comma-separated model keys, in priority order")
    ap.add_argument("--handoff", default=default_handoff,
                    help="first:second -- cards tagged for first (alone or with second) are started by first and "
                         f"every later context runs as second (default: {default_handoff or 'none'}; \"\" for none)")
    ap.add_argument("--resumes", type=int, default=10,
                    help="auto-resumes per card; past it the row is moved to blocked (0 disables resuming)")
    ap.add_argument("--max", type=int, default=4,
                    help="max agents at once (in-progress + waiting rows + pending spawns)")
    ap.add_argument("--every", type=int, default=60, help="poll interval in seconds")
    ap.add_argument("--grace", type=int, default=15, help="minutes a spawn may take to claim before it stops blocking its model")
    ap.add_argument("--answers", type=int, default=0,
                    help="stand-in answer rounds per waiting card; 0 (default) leaves every question for {{OWNER}}")
    ap.add_argument("--orphans", type=int, default=10,
                    help="resumes per card of sessions that ended without finishing, per run of this script")
    ap.add_argument("--mode", choices=MODES, help="how new sessions start (default: Logs/autospawn.mode, else headless)")
    ap.add_argument("--interactive", action="store_true", help="same as --mode interactive")
    ap.add_argument("--set-mode", choices=MODES, help="switch a running controller's mode, then exit")
    ap.add_argument("--in-main", action="store_true", help="serial mode: cards worked directly in MAIN (needs --max 1)")
    ap.add_argument("--no-idle-job", dest="idle_job", action="store_false",
                    help="never start IDLE_JOB on an idle board (no effect while IDLE_JOB is None)")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--dry-run", action="store_true", help="print instead of spawning")
    args = ap.parse_args(argv)

    global mode
    if args.set_mode:
        if args.set_mode == "interactive" and not interactive_ok():
            raise SystemExit("interactive mode needs TERMINAL_CMD on this platform (see plan/autospawn.py)")
        MODE_FILE.parent.mkdir(exist_ok=True)
        MODE_FILE.write_text(args.set_mode + "\n", encoding="utf-8")
        print(f"new sessions will start {args.set_mode}; running sessions keep their mode")
        return 0
    chosen = "interactive" if args.interactive else args.mode
    if chosen == "interactive" and not interactive_ok():
        raise SystemExit("interactive mode needs TERMINAL_CMD on this platform (see plan/autospawn.py)")
    if chosen:
        MODE_FILE.parent.mkdir(exist_ok=True)
        MODE_FILE.write_text(chosen + "\n", encoding="utf-8")   # the file is the one source of truth
    mode = read_mode_file() or "headless"
    if mode == "interactive" and not interactive_ok():
        mode = "headless"
    if args.in_main:
        if args.max != 1:
            raise SystemExit("--in-main works only with --max 1: two cards in one tree would share uncommitted work")
        os.environ[IN_MAIN_ENV] = "1"            # inherited by every launch.py and session
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    bad = [m for m in models if m not in board.MODELS]
    if bad:
        raise SystemExit(f"unknown model(s): {', '.join(bad)}; use {', '.join(board.MODELS)}")
    first, _, second = args.handoff.partition(":")
    for m in filter(None, (first, second)):
        if m not in board.MODELS:
            raise SystemExit(f"--handoff: unknown model {m}")
    handoff = bool(first and second)

    def handed(r) -> bool:
        return handoff and first in r.model.split("-or-")

    def claimer(r, limits) -> str | None:
        """The model that claims a handoff card (the first, or the second while the first is limited and the
        tag names it too; board.claim_reason keeps the second off a card tagged for the first alone)."""
        if not handed(r):
            return None
        return first if first in models and first not in limits else second

    def runner(r, limits) -> str | None:
        """The model that resumes or answers a held row: for a handoff card the second, since every resume,
        restart and answer is a context after the first (see the module summary); else its holder (None: the
        holder is not one this script runs)."""
        if handed(r) and second in models:
            return second
        holder = board.agent_model(r.status)
        return holder if holder in models else None

    pending: dict[str, tuple[str, float]] = {}   # task -> (model, spawn time)
    spawned: set[str] = set()
    resumed: set[str] = set()                    # checkpoint / answers commits already handled
    orphaned: set[str] = set()                   # transcripts of ended sessions already resumed
    orphan_count: dict[str, int] = {}
    dirty_waits: set[str] = set()                # transcripts that ended on a dirty MAIN, already logged
    answered: set[str] = set()                   # `Txx: waiting` commits already given a stand-in
    limited_logged: dict[str, float] = {}        # "the whole account" or model list -> reset time already logged
    job_proc: subprocess.Popen | None = None     # the idle job, if one is going
    job_started = 0.0
    log(f"watching {board.BOARD_PATH} every {args.every}s; models {models}, handoff {args.handoff or 'off'}, "
        f"max {args.max}, {mode}"
        + (" (dry run)" if args.dry_run else ""))

    while True:
        new_mode = read_mode_file() or mode
        if new_mode == "interactive" and not interactive_ok():
            new_mode = mode
        if new_mode != mode:
            mode = new_mode
            log(f"mode -> {mode} (new sessions only)")
        try:
            _, rows = board.load()
        except OSError as e:                     # BOARD.md mid-rewrite by a commit
            log(f"board unreadable ({e}); retrying")
            rows = None
        if rows is not None:
            by_id = {r.task_id: r for r in rows}
            now = time.time()
            limits = {m: u for m, u in model_limits(board.MODELS).items() if u > now}
            started: set[str] = set()            # cards launched in this pass (their lease is not taken yet)
            for u in sorted(set(limits.values())):
                held = sorted(m for m, v in limits.items() if v == u)
                who = "the whole account" if set(held) >= set(board.MODELS) else ", ".join(held)
                if limited_logged.get(who) != u:
                    limited_logged[who] = u
                    log(f"usage limit for {who} until {dt.datetime.fromtimestamp(u):%Y-%m-%d %H:%M}; "
                        "no spawns, resumes or answers for "
                        + ("any model" if who == "the whole account" else "it") + " until then")
            for t, (m, since) in list(pending.items()):
                state = by_id[t].state if t in by_id else "gone"
                if state == "todo" and m in limits:
                    log(f"{t} spawn for {m} hit the usage limit; it is offered again after the reset")
                    del pending[t]
                    spawned.discard(t)
                elif state != "todo":
                    log(f"{t} picked up ({by_id[t].status if t in by_id else state})")
                    del pending[t]
                elif now - since > args.grace * 60:
                    log(f"{t} still todo {args.grace} min after spawning {m}; not retrying it")
                    del pending[t]

            # Checkpointed cards: already counted as in-progress, so resuming does not raise `running`.
            for r in rows:
                if r.state != "in-progress" or not args.resumes:
                    continue
                m = runner(r, limits)
                if m is None or m in limits:
                    continue
                p = parked(r.task_id)
                if p is None or p[0] in resumed:
                    continue
                sha, n, kind = p
                if lease_held(r.task_id):
                    continue                     # a session is already on it (see launch.take_lease)
                if prereqs_pending(board, r, rows):
                    continue
                resumed.add(sha)
                started.add(r.task_id)
                if n > args.resumes:
                    if not args.dry_run:
                        block(r.task_id, n, args.resumes)
                    continue
                log(f"resume {m} -> {r.task_id} ({kind}, checkpoint {n}/{args.resumes}, {sha[:7]})")
                if not args.dry_run:
                    resume(m, r.task_id)

            # Orphans: an in-progress row whose last autospawned session has ended (quiet for 2 min with its
            # lease free) with no checkpoint or answers commit to resume from. A card that died like this at
            # night once left the serial queue standing still until morning. Rows worked by hand have no
            # transcript and are never touched.
            for r in rows:
                if r.state != "in-progress" or not args.resumes:
                    continue
                m = runner(r, limits)
                if m is None or lease_held(r.task_id):
                    continue
                t = last_transcript(r.task_id)
                if t is None or t.name in orphaned or r.task_id in started or not session_ended(t, lease_free=True):
                    continue
                if m in limits:
                    continue                     # revisited after the reset; not counted as an orphan
                if prereqs_pending(board, r, rows):
                    continue                     # revisited every tick; not counted as an orphan
                dirty_stop = ended_on_dirty_main(t)
                if dirty_stop and main_dirty():
                    if t.name not in dirty_waits:
                        dirty_waits.add(t.name)
                        log(f"{r.task_id} ended on a dirty MAIN; resumed once MAIN is clean (not an orphan)")
                    continue                     # revisited every tick; nothing spent while MAIN stays dirty
                orphaned.add(t.name)
                hit_limit = limit_until(t) is not None
                n = orphan_count.get(r.task_id, 0) + (not hit_limit and not dirty_stop)
                orphan_count[r.task_id] = n
                if n > args.orphans:
                    log(f"{r.task_id} session ended without finishing {n} times; left for {{OWNER}}")
                    continue
                p = parked(r.task_id)
                if p is not None and p[0] not in resumed:
                    continue                     # the checkpoint/answers pass resumes it
                if p is not None and p[1] > args.resumes:
                    if not args.dry_run:
                        block(r.task_id, p[1], args.resumes)
                    continue
                why = ("stopped at the usage limit, now reset" if hit_limit
                       else "stopped on a dirty MAIN, now clean" if dirty_stop else "ended without finishing")
                log(f"resume {m} -> {r.task_id} (orphan: session {t.name} {why}, {n}/{args.orphans})")
                if not args.dry_run:
                    resume(m, r.task_id)

            # Waiting cards: with --answers a stand-in answers for {{OWNER}}, then the resume pass above picks
            # them up; without it (the default) they wait for {{OWNER}}'s own `Txx: answers` commit.
            for r in rows:
                if r.state != "waiting" or not args.answers:
                    continue
                m = runner(r, limits)
                if m is None or m in limits:
                    continue
                shas = waiting_commits(r.task_id)
                if not shas or shas[0] in answered or lease_held(r.task_id):
                    continue
                answered.add(shas[0])
                if len(shas) > args.answers:
                    log(f"{r.task_id} waiting for the {len(shas)}th time; answer limit {args.answers} reached, "
                        "left for {{OWNER}}")
                    continue
                log(f"answer {r.task_id} ({r.status}), round {len(shas)}/{args.answers}")
                if not args.dry_run:
                    answer(r.task_id, None if m == board.agent_model(r.status) else m)

            running = sum(r.state in ("in-progress", "waiting") for r in rows) + len(pending)
            # Fill every free slot with the card first in claim order (phase 0 first) that some model may claim;
            # `models` order only picks who runs it. Walking the models in order once gave a freed slot to the
            # expensive model's card while a phase-0 card for the cheaper one waited. A pending card still reads
            # `todo`, so claim_reason sees its prerequisites as before.
            while running < args.max:
                pick = None
                for r in board.claim_order(rows):
                    if r.task_id in spawned or r.task_id in pending or lease_held(r.task_id):
                        continue
                    # a limited model's -or- cards fall to the next model; the handoff may give a card to one model
                    m = next((m for m in models if m not in limits and board.claim_reason(r, rows, m) is None
                              and claimer(r, limits) in (None, m)), None)
                    if m is not None:
                        pick = (m, r)
                        break
                if pick is None:
                    break
                m, row = pick
                log(f"spawn {m} -> {row.task_id}: {row.title}")
                spawned.add(row.task_id)
                running += 1
                if not args.dry_run:
                    pending[row.task_id] = (m, now)
                    spawn(m, row.task_id)

            # The idle job (IDLE_JOB) in MAIN, on an idle board only: never next to running cards (see the module
            # summary). `running` is 0 here only when no card is held, pending or claimable by any model.
            if IDLE_JOB is not None and args.idle_job and not args.dry_run:
                busy = job_proc is not None and job_proc.poll() is None
                if not busy and running == 0 and now - job_started > IDLE_JOB_GAP_S and idle_job_due():
                    log(f"idle job: board idle and due; starting {' '.join(IDLE_JOB[1])}")
                    job_started = now
                    job_proc = new_console(idle_job_argv(IDLE_JOB[1]), "idle-job")

        if args.once:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("stopped")
