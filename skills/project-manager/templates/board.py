"""Board tool: the only sanctioned way for an agent to change plan/BOARD.md.

Usage (run from MAIN, the repository root):

    python plan/board.py next <model>                 list the task you may claim, or why none
    python plan/board.py claim <Txx> <model>          claim: todo -> in-progress, commit "Txx: claim"
    python plan/board.py done <Txx> <merge-hash>      in-progress -> done (<hash>), commit "Txx: done"
    python plan/board.py blocked <Txx> "<reason>"     in-progress -> blocked (...), commit "Txx: blocked"
    python plan/board.py waiting <Txx> "<n> questions" in-progress -> waiting (...), commit "Txx: waiting":
                                                      the agent has questions for the owner and stops until answered
    python plan/board.py answered <Txx>               waiting -> in-progress once the owner has answered
    python plan/board.py add <Txx> "<title>" <phase> <tag> "<prereqs>" [--after Tyy]
                                                      append a new todo row, commit "Board: add Txx"
    python plan/board.py show                         print every row with its claimability
    python plan/board.py html                         rewrite plan/board.html (owner's live view, gitignored)
    python plan/board.py watch [--every N]            keep plan/board.html fresh; open it in a browser

<model> is one of the keys of MODELS below. A Model tag on the board is either one
model key ("opus") or several joined with "-or-" ("opus-or-local"); a model may
claim a row when its key is one of the parts of the tag.

Rules enforced here, deliberately without any override flag:

- Only a `todo` row can be claimed. `in-progress` and `blocked` rows belong to
  the agent named in them until that agent finishes or the owner resets the row
  by hand. The date in the row is informational; age never makes a row claimable.
- Every prerequisite must be `done`.
- The Model tag must be claimable by <model>.
- Rows are claimed in board order: `claim` refuses a row when an earlier row is
  claimable by the same model.
- No worktree or branch `task/Txx` may exist for the task.
- MAIN must be on MAIN_BRANCH with a clean working tree.
- `add` only ever writes a `todo` row, only for an id the board does not
  have, only when `plan/tasks/<Txx>-*.md` already exists, and only with
  prerequisites the board knows. It appends at the end of the table unless
  `--after` says otherwise, so a new card never jumps the claim order of
  the ones already there.

Standard library only, so it runs with any Python 3 before a venv exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- project settings (the bootstrap step of the project-manager skill fills these) ---

#: Person who resets rows by hand and answers "Questions for the owner".
OWNER = "the owner"

#: Card header flag for cards that need the shared cache (see start.py); shown on the HTML board.
SHARED_FLAG = "{{SHARED_FLAG}}"

#: Branch that MAIN must be on; task branches are cut from and merged into it.
MAIN_BRANCH = "main"

#: Model key -> name written on the board row. Keys are what `next`/`claim` take.
#: Remove the models this project does not use; every tag on the board must be
#: built from these keys.
MODELS: dict[str, str] = {
    "fable": "Claude Fable",
    "opus": "Claude Opus",
    "sonnet": "Claude Sonnet",
    "local": "local model",
}

#: Reasoning effort a card runs at when its header carries no `Effort: <level>`.
#: Measured on a real project: thinking was ~86% of billed output tokens and output
#: ~20% of the total bill, so effort is the largest lever after context length. It is
#: also the easiest to overspend on the wrong side: a card that needs a second pass
#: because it under-reasoned pays the difference back many times over, since rework is
#: turns and turns are billed the whole context. Hence high for the model that only
#: ever holds hard cards, medium for the mechanical bulk, and per-card overrides.
#: Levels, cheapest first: low, medium, high, xhigh, max.
EFFORTS: dict[str, str] = {
    # one entry per key of MODELS; the most expensive model only ever holds hard cards.
    "fable": "high",
    "opus": "medium",
    "sonnet": "medium",
}
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# --- derived paths -------------------------------------------------------------

BOARD_PATH = Path(__file__).resolve().parent / "BOARD.md"
MAIN = BOARD_PATH.parent.parent
TASKS_DIR = BOARD_PATH.parent / "tasks"
WORKTREES = MAIN.parent / f"{MAIN.name}.worktrees"

ROW_RE = re.compile(r"^\|\s*(T\d{2,3})\s*\|(.*)\|(.*)\|\s*([\w-]+)\s*\|(.*)\|(.*)\|\s*$")
SEPARATOR_RE = re.compile(r"^\|\s*-{3,}\s*\|")


def tag_parts(tag: str) -> list[str]:
    """Return the model keys a tag is made of ("opus-or-local" -> ["opus", "local"])."""
    return [p for p in tag.split("-or-") if p]


def may_claim(tag: str, model: str) -> bool:
    """Return True if `model` is one of the models the tag allows."""
    return model in tag_parts(tag)


def valid_tag(tag: str) -> str:
    """argparse type: a tag whose parts are all known, distinct model keys."""
    parts = tag_parts(tag)
    unknown = [p for p in parts if p not in MODELS]
    if not parts or unknown or len(set(parts)) != len(parts):
        raise argparse.ArgumentTypeError(
            f"{tag!r} is not a valid tag; use one of {', '.join(MODELS)} or join them with -or-"
        )
    return tag


@dataclass
class Row:
    task_id: str
    title: str
    phase: str
    model: str
    prereqs: list[str]
    status: str
    line_no: int

    @property
    def state(self) -> str:
        """Return the bare status word: todo, in-progress, blocked or done."""
        return self.status.split(" ", 1)[0].split("(", 1)[0].strip()


def parse_board(text: str) -> list[Row]:
    """Return the task rows of a board file, in board order."""
    rows: list[Row] = []
    for i, line in enumerate(text.splitlines()):
        m = ROW_RE.match(line)
        if not m:
            continue
        task_id, title, phase, model, prereqs, status = m.groups()
        prereq_ids = [p.strip() for p in prereqs.split(",") if p.strip() and p.strip() != "none"]
        rows.append(Row(task_id, title.strip(), phase.strip(), model, prereq_ids, status.strip(), i))
    return rows


def claim_reason(row: Row, rows: list[Row], model: str) -> str | None:
    """Return None if `model` may claim `row` now, else a one-line reason it may not."""
    by_id = {r.task_id: r for r in rows}
    state = row.state
    if state == "in-progress":
        return f"held by {row.status}; not claimable until that agent finishes or {OWNER} resets it"
    if state == "blocked":
        return f"blocked: {row.status}; waiting on {OWNER}"
    if state == "waiting":
        return f"waiting: {row.status}; the agent named there continues once {OWNER} answers"
    if state == "done":
        return "done"
    if state != "todo":
        return f"unknown status {row.status!r}"
    if not may_claim(row.model, model):
        return f"tagged {row.model}, not claimable by {model}"
    missing = [p for p in row.prereqs if p not in by_id or by_id[p].state != "done"]
    if missing:
        return "prerequisite not done: " + ", ".join(missing)
    return None


def next_claimable(rows: list[Row], model: str) -> tuple[Row | None, list[tuple[Row, str]]]:
    """Return (first claimable row or None, [(row, reason)] for the rows that are not claimable)."""
    reasons: list[tuple[Row, str]] = []
    for row in rows:
        reason = claim_reason(row, rows, model)
        if reason is None:
            return row, reasons
        reasons.append((row, reason))
    return None, reasons


def set_status(text: str, task_id: str, new_status: str) -> str:
    """Return the board text with the status cell of `task_id` replaced."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = ROW_RE.match(line.rstrip("\r\n"))
        if m and m.group(1) == task_id:
            cells = line.rstrip("\r\n").split("|")
            cells[-2] = f" {new_status} "
            lines[i] = "|".join(cells) + ("\r\n" if line.endswith("\r\n") else "\n")
            return "".join(lines)
    raise SystemExit(f"{task_id} not found on the board")


# --- git helpers -------------------------------------------------------------


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=MAIN, capture_output=True, text=True, check=True)
    return out.stdout


def require_clean_main() -> None:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != MAIN_BRANCH:
        raise SystemExit(f"MAIN is on branch {branch!r}, not {MAIN_BRANCH}. Refusing.")
    if git("status", "--porcelain").strip():
        raise SystemExit("MAIN has uncommitted changes (another agent is mid-claim). Wait and retry.")


def task_has_checkout(task_id: str) -> str | None:
    """Return a description if a worktree or branch for the task exists, else None."""
    branch = f"task/{task_id}"
    if git("branch", "--list", branch).strip():
        return f"branch {branch} exists"
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.strip() == f"branch refs/heads/{branch}":
            return f"worktree on {branch} exists"
    return None


def commit_board(message: str) -> None:
    git("add", "plan/BOARD.md")
    git("commit", "-q", "-m", message)
    write_html(parse_board(BOARD_PATH.read_text(encoding="utf-8")))
    print(f"committed: {message} ({git('rev-parse', '--short', 'HEAD').strip()})")


# --- owner's HTML view -----------------------------------------------------------

HTML_PATH = BOARD_PATH.parent / "board.html"
#: Seconds between browser refreshes of board.html (meta refresh; works from file://).
HTML_REFRESH = 5


def _card_path(task_id: str) -> Path | None:
    hits = sorted(TASKS_DIR.glob(f"{task_id}-*.md"))
    return hits[0] if hits else None


def _card_info(task_id: str) -> dict:
    """Questions count, whether Result is filled, and the title line of the card, or empty values."""
    info = {"path": None, "questions": 0, "result": False, "shared": "", "effort": "",
            "lines": [], "from_worktree": False}
    path = _card_path(task_id)
    if path is None:
        return info
    info["path"] = path
    wt = sorted((WORKTREES / task_id / "plan" / "tasks").glob(f"{task_id}-*.md")) if (WORKTREES / task_id).exists() else []
    if wt:
        path, info["from_worktree"] = wt[0], True   # the agent edits its card in the worktree until the merge
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"{re.escape(SHARED_FLAG)}:\s*(yes|no)", text, re.I)
    if m:
        info["shared"] = m.group(1).lower()
    # `Effort: <level>` in the card header overrides EFFORTS[model]; plan/launch.py reads it.
    m = re.search(rf"Effort:\s*({'|'.join(EFFORT_LEVELS)})\b", text)
    if m:
        info["effort"] = m.group(1)

    def section(name: str) -> str:
        m = re.search(rf"^## {re.escape(name)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        return m.group(1) if m else ""

    q = section("Questions for")
    info["lines"] = [l.strip() for l in q.splitlines() if re.match(r"^\s*\d+[.)]\s+\S", l)]
    info["questions"] = len(info["lines"])
    r = section("Result").strip()
    info["result"] = bool(r) and "(filled by the executing agent)" not in r
    return info


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_html(rows: list[Row]) -> Path:
    """Render plan/board.html for the owner: status colours, claimability, open questions, cards without rows."""
    by_id = {r.task_id: r for r in rows}
    try:
        head = git("rev-parse", "--short", "HEAD").strip()
        dirty = bool(git("status", "--porcelain").strip())
        branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    except Exception:  # noqa: BLE001 - the page must render even outside git
        head, dirty, branch = "?", False, "?"
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def next_for(model: str) -> str:
        row, _ = next_claimable(rows, model)
        return f'<a href="{_esc(_rel(row.task_id))}">{row.task_id}</a> {_esc(row.title)}' if row else "<i>nothing claimable</i>"

    def _rel(task_id: str) -> str:
        p = _card_path(task_id)
        return f"tasks/{p.name}" if p else "#"

    infos = {r.task_id: _card_info(r.task_id) for r in rows}
    states = {s: sum(1 for r in rows if r.state == s) for s in ("todo", "in-progress", "waiting", "blocked", "done")}
    waiting = [r for r in rows if r.state == "waiting" or (r.state in ("in-progress", "blocked") and infos[r.task_id]["questions"])]

    out = [f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="{HTML_REFRESH}">
<title>{_esc(MAIN.name)} board</title><style>
body{{font:14px/1.45 system-ui,Segoe UI,sans-serif;margin:24px;background:#14161a;color:#e6e6e6}}
a{{color:#8ab4f8;text-decoration:none}} a:hover{{text-decoration:underline}}
h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#9aa0a6;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%;margin-bottom:20px}} th,td{{padding:6px 8px;border-bottom:1px solid #2a2e35;text-align:left;vertical-align:top}}
th{{color:#9aa0a6;font-weight:600;font-size:12px;text-transform:uppercase}}
.phase td{{background:#1d2027;color:#c9cdd3;font-weight:600}}
.b{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}}
.todo{{background:#3a3f47;color:#d5d8dc}} .in-progress{{background:#1e4fa3;color:#fff}} .waiting{{background:#f2c94c;color:#111}} .blocked{{background:#a32626;color:#fff}} .done{{background:#1f7a3a;color:#fff}}
.tag{{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#c9cdd3}}
.pre.ok{{color:#6fcf97}} .pre.no{{color:#f2994a}}
.q{{background:#f2c94c;color:#111;padding:0 6px;border-radius:8px;font-weight:700}}
.dim{{color:#777}} .claim{{color:#6fcf97;font-size:12px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-bottom:20px}}
.card{{background:#1d2027;border-radius:8px;padding:10px 12px}} .card h3{{margin:0 0 6px;font-size:13px;color:#9aa0a6;text-transform:uppercase}}
</style></head><body>
<h1>{_esc(MAIN.name)} task board</h1>
<div class="meta">updated {now} · HEAD {head} on {_esc(branch)}{' · <b style="color:#f2994a">MAIN DIRTY</b>' if dirty else ' · MAIN clean'} ·
todo {states['todo']} · in progress {states['in-progress']} · <b>waiting {states['waiting']}</b> · blocked {states['blocked']} · done {states['done']} · refreshes every {HTML_REFRESH}s
(<code>python plan/board.py watch</code> keeps this file fresh)</div>
<div class="cards">"""]
    out.append('<div class="card"><h3>Next claim per model</h3>' + "".join(
        f'<div><span class="tag">{_esc(m)}</span>: {next_for(m)}</div>' for m in MODELS) + "</div>")
    if waiting:
        blocks = []
        for r in waiting:
            i = infos[r.task_id]
            src = " (from its worktree)" if i["from_worktree"] else ""
            qs = "".join(f"<li>{_esc(l[:240])}</li>" for l in i["lines"][:6])
            blocks.append(f'<div><span class="b {r.state}">{r.state}</span> <a href="{_esc(_rel(r.task_id))}">{r.task_id}</a> {_esc(r.title)}'
                          f' <span class="q">{i["questions"] or "?"} question(s)</span><span class="dim">{src}</span><ol>{qs}</ol></div>')
        out.append('<div class="card" style="grid-column:1/-1;border:1px solid #f2c94c"><h3>Waiting on ' + _esc(OWNER) + ": answer in the card file, then tell the agent</h3>" + "".join(blocks) + "</div>")
    blocked = [r for r in rows if r.state == "blocked"]
    if blocked:
        out.append('<div class="card"><h3>Blocked</h3>' + "".join(
            f'<div><a href="{_esc(_rel(r.task_id))}">{r.task_id}</a> {_esc(r.status)}</div>' for r in blocked) + "</div>")
    out.append("</div>")

    out.append(f"<table><tr><th>Id</th><th>Title</th><th>Model</th><th>{_esc(SHARED_FLAG)}</th><th>Prerequisites</th><th>Status</th><th>Card</th></tr>")
    phase = None
    for r in rows:
        if r.phase != phase:
            phase = r.phase
            out.append(f'<tr class="phase"><td colspan="7">Phase {_esc(phase)}</td></tr>')
        info = infos[r.task_id]
        pres = " ".join(
            f'<span class="pre {"ok" if (p in by_id and by_id[p].state == "done") else "no"}">{p}</span>' for p in r.prereqs
        ) or '<span class="dim">none</span>'
        claimable = [m for m in MODELS if claim_reason(r, rows, m) is None]
        status = f'<span class="b {r.state}">{_esc(r.status)}</span>'
        if claimable:
            status += f'<div class="claim">claimable by {", ".join(claimable)}</div>'
        card = ""
        if info["path"] is None:
            card = '<span class="dim">no card file</span>'
        else:
            bits = []
            if info["questions"]:
                bits.append(f'<span class="q">{info["questions"]} question(s)</span>')
            if info["result"]:
                bits.append("result filled")
            card = " ".join(bits) or '<span class="dim">—</span>'
        out.append(
            f'<tr><td><a href="{_esc(_rel(r.task_id))}">{r.task_id}</a></td><td>{_esc(r.title)}</td>'
            f'<td class="tag">{_esc(r.model)}</td><td class="dim">{_esc(info["shared"])}</td><td>{pres}</td><td>{status}</td><td>{card}</td></tr>'
        )
    out.append("</table>")

    orphans = sorted(p.name for p in TASKS_DIR.glob("T*-*.md") if p.name.split("-", 1)[0] not in by_id)
    if orphans:
        out.append("<h3>Card files not on the board</h3><ul>" + "".join(
            f'<li><a href="tasks/{_esc(n)}">{_esc(n)}</a></li>' for n in orphans) + "</ul>")
    out.append(f"<p class='dim'>Source of truth: <a href='BOARD.md'>BOARD.md</a>. Only <code>board.py</code> changes rows; only {_esc(OWNER)} edits rows by hand.</p></body></html>")
    HTML_PATH.write_text("\n".join(out), encoding="utf-8")
    return HTML_PATH


def cmd_html(_args: argparse.Namespace) -> int:
    _, rows = load()
    print(f"wrote {write_html(rows)}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Regenerate board.html whenever BOARD.md or a card file changes (for the owner's browser tab)."""
    import time

    def stamp() -> tuple:
        files = [BOARD_PATH, *TASKS_DIR.glob("T*-*.md")]
        return tuple((p.name, p.stat().st_mtime_ns) for p in files if p.exists())

    last = None
    print(f"watching {BOARD_PATH.parent} every {args.every}s; open {HTML_PATH} in a browser. Ctrl-C to stop.", flush=True)
    while True:
        cur = stamp()
        if cur != last:
            _, rows = load()
            write_html(rows)
            last = cur
            print(f"{dt.datetime.now():%H:%M:%S} board.html updated", flush=True)
        time.sleep(args.every)


# --- commands ----------------------------------------------------------------


def load() -> tuple[str, list[Row]]:
    text = BOARD_PATH.read_text(encoding="utf-8")
    return text, parse_board(text)


def cmd_show(_args: argparse.Namespace) -> int:
    _, rows = load()
    write_html(rows)
    if not rows:
        print("the board has no task rows yet")
    for row in rows:
        claimable = [m for m in MODELS if claim_reason(row, rows, m) is None]
        tail = "claimable by " + ", ".join(claimable) if claimable else ""
        print(f"{row.task_id}  {row.model:<14} {row.status:<50} {tail}")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    _, rows = load()
    row, reasons = next_claimable(rows, args.model)
    if row is None:
        print(f"No valid task available for {args.model}. Stop; do not reinterpret any row below.")
        # Only the rows that are not simply `done` tell the agent anything, and on a long board the
        # full list is hundreds of lines in a context that is billed on every later turn. `show` still
        # prints everything for the owner.
        interesting = [(r, why) for r, why in reasons if why != "done"]
        for r, reason in interesting[:8]:
            print(f"  {r.task_id}: {reason}")
        if len(interesting) > 8:
            print(f"  ... and {len(interesting) - 8} more unclaimable rows (python plan/board.py show for all)")
        if not interesting:
            print("  every row on the board is done.")
        return 1
    print(f"{row.task_id}: {row.title}")
    print(f"claim it with: python plan/board.py claim {row.task_id} {args.model}")
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    require_clean_main()
    text, rows = load()
    by_id = {r.task_id: r for r in rows}
    row = by_id.get(args.task)
    if row is None:
        raise SystemExit(f"{args.task} is not on the board")
    reason = claim_reason(row, rows, args.model)
    if reason is not None:
        raise SystemExit(f"Refusing to claim {row.task_id}: {reason}")
    expected, _ = next_claimable(rows, args.model)
    if expected is not None and expected.task_id != row.task_id:
        raise SystemExit(
            f"Refusing to claim {row.task_id}: {expected.task_id} comes first in board order for {args.model}"
        )
    checkout = task_has_checkout(row.task_id)
    if checkout:
        raise SystemExit(f"Refusing to claim {row.task_id}: {checkout}. Someone is working on it.")
    agent = args.agent or MODELS[args.model]
    today = dt.date.today().isoformat()
    BOARD_PATH.write_text(set_status(text, row.task_id, f"in-progress ({agent}, {today})"), encoding="utf-8")
    commit_board(f"{row.task_id}: claim")
    worktree = (WORKTREES / row.task_id).as_posix()
    print(f"{row.task_id} is yours. Next: git worktree add {worktree} -b task/{row.task_id} {MAIN_BRANCH}")
    return 0


def _require_in_progress(task_id: str) -> tuple[str, Row]:
    text, rows = load()
    row = next((r for r in rows if r.task_id == task_id), None)
    if row is None:
        raise SystemExit(f"{task_id} is not on the board")
    if row.state not in ("in-progress", "waiting"):
        raise SystemExit(f"{task_id} is {row.status!r}, not in-progress. Only the claiming agent moves a row on.")
    return text, row


def _holder(row: Row) -> str:
    """The `<agent>, <date>` part of an in-progress or waiting status."""
    inner = row.status.split("(", 1)[1].rstrip(")") if "(" in row.status else ""
    return inner.split(";", 1)[0].strip()


def cmd_done(args: argparse.Namespace) -> int:
    require_clean_main()
    text, row = _require_in_progress(args.task)
    short = git("rev-parse", "--short", "--verify", args.hash).strip()
    BOARD_PATH.write_text(set_status(text, row.task_id, f"done ({short})"), encoding="utf-8")
    commit_board(f"{row.task_id}: done")
    return 0


def cmd_blocked(args: argparse.Namespace) -> int:
    require_clean_main()
    text, row = _require_in_progress(args.task)
    reason = args.reason.replace("|", "/").strip()
    holder = _holder(row)
    BOARD_PATH.write_text(set_status(text, row.task_id, f"blocked ({holder}; {reason})"), encoding="utf-8")
    commit_board(f"{row.task_id}: blocked")
    return 0


def cmd_waiting(args: argparse.Namespace) -> int:
    """in-progress -> waiting: the agent has written questions in its card and stops until the owner answers."""
    require_clean_main()
    text, row = _require_in_progress(args.task)
    if row.state != "in-progress":
        raise SystemExit(f"{args.task} is {row.status!r}; only an in-progress row can start waiting.")
    reason = args.reason.replace("|", "/").strip()
    BOARD_PATH.write_text(set_status(text, row.task_id, f"waiting ({_holder(row)}; {reason})"), encoding="utf-8")
    commit_board(f"{row.task_id}: waiting")
    print(f"{row.task_id} is waiting on {OWNER}. Answer the questions in the card, then the same agent continues to merge and done.")
    return 0


def cmd_answered(args: argparse.Namespace) -> int:
    """waiting -> in-progress: the owner answered, the same agent (or its successor) continues.

    Without this a `waiting` row is a dead end: the questions get answered in the card but the
    board keeps telling every reader the card is still waiting on the owner.
    """
    require_clean_main()
    text, row = _require_in_progress(args.task)
    if row.state != "waiting":
        raise SystemExit(f"{args.task} is {row.status!r}; only a waiting row is released with `answered`.")
    BOARD_PATH.write_text(set_status(text, row.task_id, f"in-progress ({_holder(row)})"), encoding="utf-8")
    commit_board(f"{row.task_id}: answered")
    print(f"{row.task_id} is in-progress again. Record the answers in the card, apply them, "
          f"re-run the acceptance test, then finish.")
    return 0


def counts_line(rows: list[Row]) -> str:
    """The trailing `Counts:` line for `rows`, tags in order of first appearance."""
    tags: list[str] = []
    for r in rows:
        if r.model not in tags:
            tags.append(r.model)
    tally = {tag: sum(1 for r in rows if r.model == tag) for tag in tags}
    return "Counts: " + ", ".join(f"{tag} {n}" for tag, n in tally.items()) + "."


def insert_row(text: str, row_line: str, after: str | None) -> str:
    """Return the board text with `row_line` inserted and the Counts line refreshed.

    Without `after` the row goes at the end of the table, so it is the last one
    `next` considers and no existing card's claim order changes. An empty table
    gets its first row right under the header separator.
    """
    lines = text.splitlines(keepends=True)
    table = [i for i, line in enumerate(lines) if ROW_RE.match(line.rstrip("\r\n"))]
    if after is not None:
        matches = [i for i in table if ROW_RE.match(lines[i].rstrip("\r\n")).group(1) == after]
        if not matches:
            raise SystemExit(f"--after {after} is not on the board")
        at = matches[0]
    elif table:
        at = table[-1]
    else:
        seps = [i for i, line in enumerate(lines) if SEPARATOR_RE.match(line)]
        if not seps:
            raise SystemExit("no task table found on the board (expected a | Id | Title | ... header)")
        at = seps[-1]
    ending = "\r\n" if lines[at].endswith("\r\n") else "\n"
    lines.insert(at + 1, row_line + ending)
    text = "".join(lines)
    counts = counts_line(parse_board(text))
    if re.search(r"^Counts:.*$", text, flags=re.MULTILINE):
        return re.sub(r"^Counts:.*$", counts, text, count=1, flags=re.MULTILINE)
    return text.rstrip("\r\n") + ending + ending + counts + ending


def cmd_add(args: argparse.Namespace) -> int:
    require_clean_main()
    text, rows = load()
    task_id = args.task.strip()
    if not re.fullmatch(r"T\d{2,3}", task_id):
        raise SystemExit(f"{task_id!r} is not a task id of the form Txx")
    if any(r.task_id == task_id for r in rows):
        raise SystemExit(f"{task_id} is already on the board. Pick a free id.")
    cards = sorted(TASKS_DIR.glob(f"{task_id}-*.md"))
    if not cards:
        raise SystemExit(
            f"no plan/tasks/{task_id}-*.md exists. Write the card first: a board row "
            "without one cannot be executed."
        )
    if len(cards) > 1:
        raise SystemExit(f"{len(cards)} task files claim {task_id}: {[c.name for c in cards]}")
    title = args.title.replace("|", "/").strip()
    if not title:
        raise SystemExit("the title may not be empty")
    known = {r.task_id for r in rows}
    prereqs = [p.strip() for p in (args.prereqs or "").split(",") if p.strip()]
    prereqs = [p for p in prereqs if p.lower() != "none"]
    unknown = [p for p in prereqs if p not in known]
    if unknown:
        raise SystemExit(f"prerequisites not on the board: {', '.join(unknown)}")
    cell = ", ".join(prereqs) if prereqs else "none"
    row_line = f"| {task_id} | {title} | {args.phase} | {args.model} | {cell} | todo |"
    BOARD_PATH.write_text(insert_row(text, row_line, args.after), encoding="utf-8")
    commit_board(f"Board: add {task_id}")
    print(f"added {task_id} ({cards[0].name}) as todo, tagged {args.model}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show").set_defaults(func=cmd_show)
    sub.add_parser("html", help="rewrite plan/board.html (the owner's live view)").set_defaults(func=cmd_html)
    p = sub.add_parser("watch", help="keep plan/board.html fresh while cards and the board change")
    p.add_argument("--every", type=int, default=3, help="poll interval in seconds")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("next")
    p.add_argument("model", choices=sorted(MODELS))
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("claim")
    p.add_argument("task")
    p.add_argument("model", choices=sorted(MODELS))
    p.add_argument("--agent", help="name written on the row; defaults to the model's usual name")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("done")
    p.add_argument("task")
    p.add_argument("hash", help="merge commit hash")
    p.set_defaults(func=cmd_done)

    p = sub.add_parser("blocked")
    p.add_argument("task")
    p.add_argument("reason")
    p.set_defaults(func=cmd_blocked)

    p = sub.add_parser("waiting", help="the agent has questions for the owner; row stays held")
    p.add_argument("task")
    p.add_argument("reason", help='e.g. "2 questions"')
    p.set_defaults(func=cmd_waiting)

    p = sub.add_parser("answered", help="the owner answered a waiting card; row returns to in-progress")
    p.add_argument("task")
    p.set_defaults(func=cmd_answered)

    p = sub.add_parser("add")
    p.add_argument("task")
    p.add_argument("title")
    p.add_argument("phase")
    p.add_argument("model", type=valid_tag, help='a model key or keys joined with "-or-"')
    p.add_argument("prereqs", nargs="?", default="none",
                   help='comma-separated task ids, or "none"')
    p.add_argument("--after", help="insert after this row instead of at the end of the table")
    p.set_defaults(func=cmd_add)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
