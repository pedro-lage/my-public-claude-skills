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
                                                      lint the card, append a new todo row, commit "Board: add Txx"
    python plan/board.py set <Txx> [--title "..."] [--prereqs "..."] [--tag T] [--phase N]
                                                      rewrite one todo row (and the card header's Prerequisites,
                                                      Model, Phase), commit "Board: set Txx (<fields>)"
    python plan/board.py lint [Txx ...]               check cards against the card rules (default: every todo row)
    python plan/board.py suggest "<title>" --problem "..." --propose "..." [--propose ...] --by <model> [--card Txx] [--area A]
                                                      log friction in plan/SUGGESTIONS.md, commit "Plan: suggestion Sxx";
                                                      --also Sxx (no title) +1s an existing suggestion instead
    python plan/board.py archive                      move triaged suggestions (Status no longer `open`) to
                                                      plan/SUGGESTIONS-ARCHIVE.md, commit "Plan: suggestions archive (...)";
                                                      every `suggest` does the same sweep
    python plan/board.py archive-done [--dry-run]     move done rows that no open row waits on to
                                                      plan/BOARD-ARCHIVE.md, commit "Board: archive N done rows"
    python plan/board.py show                         print every row with its claimability
    python plan/board.py html                         rewrite plan/board.html (owner's live view, gitignored)
    python plan/board.py watch [--every N]            keep plan/board.html fresh; open it in a browser

<model> is one of the keys of MODELS below. A Model tag on the board is either one
model key ("opus55") or several joined with "-or-" ("opus55-or-sonnet"); a model may
claim a row when its key is one of the parts of the tag.

Rules enforced here, deliberately without any override flag:

- Only a `todo` row can be claimed. `in-progress` and `blocked` rows belong to
  the agent named in them until that agent finishes or the owner resets the row
  by hand. The date in the row is informational; age never makes a row claimable.
- Every prerequisite must be `done`: the row's, and any the card header's
  `Prerequisites:` names that the row does not.
- The Model tag must be claimable by <model>.
- Rows are claimed in claim order: the rows of a PRIORITY_PHASES phase (tooling)
  first, then board order. `claim` refuses a row when an earlier row in that
  order is claimable by the same model.
- No worktree or branch `task/Txx` may exist for the task.
- MAIN must be on MAIN_BRANCH with a clean working tree.
- `add` only ever writes a `todo` row, only for an id the board does not
  have, only when `plan/tasks/<Txx>-*.md` already exists and passes the card
  lint, and only with prerequisites the board knows. It appends at the end of
  the table unless `--after` says otherwise, so a new card never jumps the claim
  order of the ones already there, unless its phase is in PRIORITY_PHASES: a
  tooling card is claimed first wherever its row sits.

Standard library only, so it runs with any Python 3 before a venv exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- project settings (the bootstrap step of the project-manager skill fills these) ---

#: Person who resets rows by hand and answers "Questions for the owner".
OWNER = "{{OWNER}}"

#: Branch that MAIN must be on; task branches are cut from and merged into it.
MAIN_BRANCH = "{{MAIN_BRANCH}}"

#: The authoritative spec. Card lint accepts `[<SPEC_FILE> §x]` and `[<its stem> §x]` as a threshold source,
#: and its messages point there.
SPEC_FILE = "{{SPEC_FILE}}"

#: Card-header word that says a card needs the heavy shared environment (`<SHARED_FLAG>: yes|no`).
#: Shown as a column on board.html and read by plan/launch.py; empty when the project has none.
SHARED_FLAG = "{{SHARED_FLAG}}"

#: Model key -> name written on the board row. Keys are what `next`/`claim` take.
#: Delete the models the owner will not run; every tag on the board must be built from these keys.
#: Add a `local` key only when there is a local model. Do not add `haiku` unless asked: it has no
#: auto mode, so it cannot work unattended.
MODELS: dict[str, str] = {
    "fable": "Claude Fable",
    "opus55": "Claude Opus 5.5",
    "sonnet": "Claude Sonnet",
}

#: Agent names of retired keys still written in held rows -> the key that now takes them over.
#: When a model is dropped for its successor (e.g. a cheaper version at the same capability), its
#: key leaves MODELS, its cards are retagged, and its name goes here so the rows it still holds keep
#: resolving to a live key: {"Claude Opus": "opus55"}.
LEGACY_AGENTS: dict[str, str] = {}

#: Reasoning effort a card runs at when its header carries no `Effort: <level>`.
#: Measured on one project: thinking was ~86% of billed output tokens and output ~20% of the total
#: bill, so effort is the largest lever after context length. It is also the easiest to overspend on
#: the wrong side: a card that needs a second pass because it under-reasoned pays the difference back
#: many times over, since rework is turns and turns are billed the whole context. Hence high for the
#: models that hold hard cards, medium for the mechanical bulk, and per-card overrides.
#: One entry per key of MODELS. Levels, cheapest first: low, medium, high, xhigh, max.
EFFORTS: dict[str, str] = {
    "fable": "high",
    "opus55": "high",
    "sonnet": "medium",
}
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

#: Phases whose claimable rows are taken before any other row, wherever they sit on the board.
#: Phase 0 is tooling: a card that makes launches, tests or the protocol cheaper pays back on every
#: card still `todo`, so each feature card run ahead of it wastes part of the saving. The gate is at
#: card creation: a tooling card that would not pay back on the cards left is not added. Priority
#: never overrides prerequisites or tags.
PRIORITY_PHASES: frozenset[str] = frozenset({"0"})

#: Model key -> the value `claude --model` takes, for keys that are not themselves a CLI alias.
#: Pin a key to a full id so it never drifts with an alias. plan/launch.py reads this.
CLI_MODELS: dict[str, str] = {
    "opus55": "claude-opus-5-5",
}

#: Models that run only on cards the owner named. A card whose Model tag names one of them (alone or in
#: an `-or-` tag, which runs that model's context first) needs a line `<name>: [<OWNER> <date>] <why>`,
#: where <name> is the key, the display name in MODELS, or the display name without its first word
#: ("Fable: [Ana 2026-01-31] needs the hard diagnosis"); `board.py lint` and `add` refuse it otherwise.
#: Measured on one project: a request on the most expensive model took ~6-7x a cheaper one's share of
#: the plan's usage window, and three such cards in parallel used up a 5-hour window in 90 minutes.
#: A hard diagnosis gets `Effort: xhigh` on a cheaper model instead. Empty: no tag needs the owner's word.
OWNER_WORD_MODELS: frozenset[str] = frozenset({"fable"})


def agent_model(status: str) -> str | None:
    """The model key whose agent name appears in a row status. Longest name first, so
    "Claude Opus 5.5" is not read as "Claude Opus"."""
    names = list(MODELS.items()) + [(k, n) for n, k in LEGACY_AGENTS.items()]
    for key, name in sorted(names, key=lambda kv: -len(kv[1])):
        if name in status:
            return key
    return None

# --- derived paths -------------------------------------------------------------

BOARD_PATH = Path(__file__).resolve().parent / "BOARD.md"
MAIN = BOARD_PATH.parent.parent
TASKS_DIR = BOARD_PATH.parent / "tasks"
WORKTREES = MAIN.parent / f"{MAIN.name}.worktrees"

ROW_RE = re.compile(r"^\|\s*(T\d{2,3})\s*\|(.*)\|(.*)\|\s*([\w-]+)\s*\|(.*)\|(.*)\|\s*$")
SEPARATOR_RE = re.compile(r"^\|\s*-{3,}\s*\|")


def tag_parts(tag: str) -> list[str]:
    """Return the model keys a tag is made of ("opus55-or-sonnet" -> ["opus55", "sonnet"])."""
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
        """Return the bare status word: todo, in-progress, waiting, blocked or done."""
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


#: Done rows that no open row waits on, moved off the board by `archive-done` so the board stays readable.
#: A prerequisite found here counts as done; a card id found here is taken.
BOARD_ARCHIVE_PATH = BOARD_PATH.parent / "BOARD-ARCHIVE.md"
BOARD_ARCHIVE_HEADER = """# Task board archive

Done rows moved off `plan/BOARD.md` by `python plan/board.py archive-done`: every `done` row that no unfinished
row lists as a prerequisite. Their cards stay in `plan/tasks/`. A prerequisite listed here counts as done.

| Id | Title | Phase | Model | Prerequisites | Status |
|---|---|---|---|---|---|
"""
BOARD_ARCHIVE_NOTE = ("Done rows that no open row waits on live in `plan/BOARD-ARCHIVE.md` "
                      "(`python plan/board.py archive-done`); a prerequisite listed there counts as done.")


def archived_ids() -> set[str]:
    if not BOARD_ARCHIVE_PATH.exists():
        return set()
    return {r.task_id for r in parse_board(BOARD_ARCHIVE_PATH.read_text(encoding="utf-8"))}


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
    archived = archived_ids() if any(p not in by_id for p in row.prereqs) else set()
    missing = [p for p in row.prereqs if (by_id[p].state != "done" if p in by_id else p not in archived)]
    if missing:
        return "prerequisite not done: " + ", ".join(missing)
    # A card header named two prerequisites its row did not, and the card was handed out before they were done.
    path = _card_path(row.task_id)
    extra = [p for p in (header_prereqs(path.read_text(encoding="utf-8", errors="replace")) or [])
             if p not in row.prereqs] if path else []
    if extra:
        archived = archived_ids()
        missing = [p for p in extra if (by_id[p].state != "done" if p in by_id else p not in archived)]
        if missing:
            return ("prerequisite named in the card header, not on the row, not done: " + ", ".join(missing)
                    + f" (`board.py set {row.task_id} --prereqs` makes the two agree)")
    return None


def claim_order(rows: list[Row]) -> list[Row]:
    """Return `rows` in the order they are offered: PRIORITY_PHASES rows first, then board order."""
    return sorted(rows, key=lambda r: r.phase not in PRIORITY_PHASES)   # stable: board order within each group


def next_claimable(rows: list[Row], model: str) -> tuple[Row | None, list[tuple[Row, str]]]:
    """Return (first claimable row in claim order or None, [(row, reason)] for the rows that are not claimable)."""
    reasons: list[tuple[Row, str]] = []
    for row in claim_order(rows):
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


#: Serial mode (`{{ENV_PREFIX}}_IN_MAIN=1`, set by `autospawn.py --in-main` / `launch.py --in-main`): one card at a
#: time, worked directly in MAIN with no worktree, branch or merge. The card's own work is then
#: uncommitted in MAIN while its row moves (waiting, blocked, done), so those transitions accept a
#: dirty tree; a claim still needs a clean one, so a card never starts on another card's leftovers.
IN_MAIN = os.environ.get("{{ENV_PREFIX}}_IN_MAIN") == "1"


def in_main(task_id: str, held: bool = True) -> bool:
    """Serial mode for this card: `{{ENV_PREFIX}}_IN_MAIN=1`, or detected from the repo when the held row has no
    worktree, no `task/<id>` branch and `<id>: ` commits on main (its claim at least): a serial card
    resumed or finished from a plain terminal. Detection prints one line and sets the environment
    variable and `IN_MAIN`, so the board calls made afterwards behave the same."""
    global IN_MAIN
    if IN_MAIN:
        return True
    if not held or (WORKTREES / task_id).exists() or task_has_checkout(task_id):
        return False
    n = len(git("log", "--oneline", MAIN_BRANCH, "--grep", f"^{task_id}: ").splitlines())
    if not n:
        return False
    print(f"serial mode detected (no worktree, no branch, {n} commits on main)")
    os.environ["{{ENV_PREFIX}}_IN_MAIN"] = "1"
    IN_MAIN = True
    return True


def require_clean_main(own_work: bool = False) -> None:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != MAIN_BRANCH:
        raise SystemExit(f"MAIN is on branch {branch!r}, not {MAIN_BRANCH}. Refusing.")
    if own_work and IN_MAIN:
        return                                  # the holder's own uncommitted work; commit_board commits only BOARD.md
    # Untracked files block only in serial mode, where they are the previous card's leftovers. Otherwise a board
    # commit stages BOARD.md alone and a worktree starts from HEAD, so an untracked file (the owner's notes, say)
    # cannot leak into either; finish.py checks the ones a merge would overwrite.
    status = git("status", "--porcelain") if IN_MAIN else git("status", "--porcelain", "--untracked-files=no")
    if status.strip():
        raise SystemExit("MAIN has uncommitted changes (another agent is mid-claim). Wait and retry.\n"
                         + "\n".join("  " + l for l in status.splitlines()[:10]))


def task_has_checkout(task_id: str) -> str | None:
    """Return a description if a worktree or branch for the task exists, else None."""
    branch = f"task/{task_id}"
    if git("branch", "--list", branch).strip():
        return f"branch {branch} exists"
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.strip() == f"branch refs/heads/{branch}":
            return f"worktree on {branch} exists"
    return None


def commit_board(message: str, *also: str) -> None:
    """Commit the board, plus the files in `also` (`set` rewrites a card header with its row), nothing else."""
    paths = ["plan/BOARD.md", *also]
    git("add", *paths)
    git("commit", "-q", "-m", message, "--", *paths)   # only these, whatever else is staged
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
    """Questions, whether Result is filled, the SHARED_FLAG and Effort values of the card, or empty values.
    `shared` is "yes", "no" or "" (no SHARED_FLAG field, or the project has none); plan/launch.py reads it."""
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
    if SHARED_FLAG:
        m = re.search(r"(?<![\w-])" + re.escape(SHARED_FLAG) + r":\s*(yes|no)\b", text)
        if m:
            info["shared"] = m.group(1)
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


# Card lint (the spec's rule "numbers are not requirements unless the owner gave them"): every threshold in a
# card's Goal, Spec and Acceptance carries its source right after it, and only a number from the owner, the spec
# or a measurement may gate. A number an earlier planner invented and wrote into a card was later enforced as if
# the owner had asked for it; the source tag makes the difference visible. Explore cards diagnose before they fix
# and are accepted on an A/B against today.
CARD_KINDS = ("build", "explore")
_THRESHOLD = re.compile(r"(≤|≥|<=|>=|(?<![-=<])<(?![-=])|(?<![-=>])>(?![=])|\bat (?:least|most)\b|\bwithin\b)"
                        r"\s*[−-]?\s*\d")


def _source_re() -> "re.Pattern[str]":
    """`[<OWNER> ...]`, `[<SPEC_FILE> §x]` or `[<spec stem> §x]`, `[measured Txx ...]`, `[planner ...]`."""
    specs = sorted({SPEC_FILE, Path(SPEC_FILE).stem} - {""}, key=len, reverse=True)
    spec = "|".join(re.escape(s) for s in specs) or r"(?!)"
    return re.compile(r"\[(?:" + re.escape(OWNER) + r"(?=[\s\]])[^\]]*|(?:" + spec + r") §[^\]]*"
                      r"|measured T\d+[^\]]*|planner\b[^\]]*)\]")


_SOURCE = _source_re()
_PLANNER = re.compile(r"\[planner\b[^\]]*\]")
_GATE_WORD = re.compile(r"\b(?:[Gg]at(?:e|es|ed|ing)|[Dd]one when|pass/fail)\b")


def _card_items(body: str) -> list[str]:
    """A section's paragraphs and numbered items as single lines; code fences and inline code dropped."""
    body = re.sub(r"^```.*?^```", "", body, flags=re.S | re.M)
    body = re.sub(r"`[^`\n]*`", "", body)
    items, cur = [], []
    for line in body.splitlines():
        if not line.strip() or re.match(r"^\s*(\d+[.)]|[-*])\s+", line):
            if cur:
                items.append(" ".join(cur))
            cur = [line.strip()] if line.strip() else []
        else:
            cur.append(line.strip())
    if cur:
        items.append(" ".join(cur))
    return items


#: The card header's `Prerequisites: T60, T62.` field (`none.` when empty); `set --prereqs` rewrites it.
_HEADER_PREREQS = re.compile(r"\bPrerequisites:[ \t]*([^.\n]*)")
_HEADER_LINES = 8


def header_prereqs(text: str) -> list[str] | None:
    """The task ids the card header names as prerequisites, or None when the header has no such field."""
    m = _HEADER_PREREQS.search("\n".join(text.splitlines()[:_HEADER_LINES]))
    return re.findall(r"\bT\d{2,3}\b", m.group(1)) if m else None


def set_header_field(text: str, field: str, value: str) -> str:
    """Return the card text with the header's `<field>: <value>.` replaced; unchanged when the header lacks it."""
    lines = text.splitlines(keepends=True)
    head = "".join(lines[:_HEADER_LINES])
    pattern = _HEADER_PREREQS if field == "Prerequisites" else re.compile(rf"\b{field}:[ \t]*[\w-]+(?:\.\d+)?")   # Phase: 4.5
    new_head = pattern.sub(lambda _m: f"{field}: {value}", head, count=1)
    return new_head + "".join(lines[_HEADER_LINES:])


def _owner_word_names(key: str) -> list[str]:
    """Names an owner's-word line may start with for model `key`: the key, its display name, and the
    display name without its first word ("Claude Fable" -> "Fable"); longest first."""
    name = MODELS.get(key, key)
    names = {key, name}
    if " " in name:
        names.add(name.split(" ", 1)[1])
    return sorted(names, key=len, reverse=True)


def lint_card(path: Path, prereqs: list[str] | None = None, text: str | None = None) -> list[str]:
    """Problems that keep a card off the board (`add`) or that a planner fixes before the card is claimed.
    `prereqs` is the card's row (or the row about to be written): the header must name the same ids, since a
    header naming more than its row once let a card be claimed before its real prerequisites were done.
    `text` lints a card's new content before it is written (`set`)."""
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    head = "\n".join(text.splitlines()[:_HEADER_LINES])
    problems = []
    if prereqs is not None:
        named = header_prereqs(text)
        if named is not None and set(named) != set(prereqs):
            problems.append(f"header Prerequisites ({', '.join(named) or 'none'}) differ from the row's "
                            f"({', '.join(prereqs) or 'none'}): `board.py set <Txx> --prereqs \"...\"` rewrites both")
    m = re.search(r"\bKind:\s*(\w+)", head)
    kind = m.group(1) if m else ""
    if kind not in CARD_KINDS:
        problems.append("header has no `Kind: build` or `Kind: explore` (plan/tasks/TEMPLATE.md)")

    def section(name: str) -> str:
        s = re.search(rf"^## {re.escape(name)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        return s.group(1) if s else ""

    for name in ("Goal", "Spec", "Acceptance test"):
        for item in _card_items(section(name)):
            for t in _THRESHOLD.finditer(item):
                if not _SOURCE.search(item, t.end(), t.end() + 120):
                    problems.append(f"{name}: threshold without a source tag: ...{item[max(0, t.start() - 40):t.end() + 30]}...")
            if _PLANNER.search(item) and _GATE_WORD.search(item):
                problems.append(f"{name}: a [planner] number gates: ...{item[:110]}...")
    # OWNER_WORD_MODELS: a tag naming one of them (an `-or-` tag included: that model runs the first context)
    # needs the owner's word on the card.
    tag = re.search(r"\bModel:\s*([\w-]+)", head)
    for key in sorted(OWNER_WORD_MODELS & set(tag_parts(tag.group(1)) if tag else ())):
        names = _owner_word_names(key)
        alts = "|".join(re.escape(n) for n in names)
        if not re.search(r"^(?:" + alts + r")(?:-only)?:.*\[" + re.escape(OWNER) + r"\s+\d{4}-\d\d-\d\d",
                         text, re.M | re.I):
            short = next((n for n in names if n not in (key, MODELS.get(key))), MODELS.get(key, key))
            problems.append(f"tag names `{key}` without a `{short}: [{OWNER} <date>] <why>` line: {short} runs only "
                            f"on cards {OWNER} names; tag it for a cheaper model (Effort: xhigh for a hard diagnosis)")
    if kind == "explore":
        if "A/B" not in section("Acceptance test"):
            problems.append("explore card: the Acceptance test has no A/B against today's build")
        if not re.search(r"[Dd]iagnos", section("Spec")):
            problems.append("explore card: the Spec does not start with a diagnosis step")
    # A build card at medium/low effort is executed literally, so its diagnosis must be settled: a hedged cause
    # ("likely", "confirm first") was once implemented as fact and the card had to be redone as an explore card.
    # Hedges make it an explore card at high effort, or the planner settles them in the card before it is added.
    effort = re.search(r"\bEffort:\s*(\w+)", head)
    if kind == "build" and effort and effort.group(1).lower() in ("medium", "low"):
        hedged = _HEDGE.search(section("What exists") + "\n" + section("Spec"))
        if hedged:
            problems.append("build card at Effort: %s with a hypothesis in its diagnosis (\"...%s...\"): settle it "
                            "in the card, or tag it Kind: explore, Effort: high" % (effort.group(1), _hedge_context(hedged)))
    # A card with a visible result is accepted on what it shows at the view where the defect shows, before the
    # first edit (A) and after (B); a card that closed on a number the agent picked itself showed no visible change.
    # Any `Render budget:` / `Budget:` line other than "none" marks a visible result.
    acc = section("Acceptance test")
    budget = re.search(r"^(?:Render budget|Budget):\s*(.+)$", text, re.M)
    if budget and not budget.group(1).strip().lower().startswith("none") and not re.search(r"^Defect view:", acc, re.M):
        problems.append("visible result (a render budget) but no `Defect view:` line in the Acceptance test: "
                        "name the view where the defect shows (the agent's A capture before its first edit, "
                        "INVOKE.md §3)")
    return problems


#: Hedge words that mark a card's cause as a hypothesis rather than a measurement.
_HEDGE = re.compile(r"\b(likely|probabl[ye]|presumabl[ye]|suspect(?:ed)?|confirm (?:on|with|first)|check whether|may be the cause|might be)\b", re.I)


def _hedge_context(m: "re.Match[str]") -> str:
    s = m.string
    return s[max(0, m.start() - 40):m.end() + 30].replace("\n", " ")


def cmd_lint(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cards quote ≤, ≥ and −
    _, rows = load()
    ids = args.tasks or [r.task_id for r in rows if r.status.strip() == "todo"]
    bad = 0
    for task_id in ids:
        path = _card_path(task_id)
        if path is None:
            print(f"{task_id}: no card")
            bad += 1
            continue
        row = next((r for r in rows if r.task_id == task_id), None)
        problems = lint_card(path, row.prereqs if row else None)
        bad += bool(problems)
        print(f"{task_id}: " + ("ok" if not problems else f"{len(problems)} problem(s)"))
        for p in problems[: args.max]:
            print(f"  - {p}")
    print(f"LINT {len(ids) - bad}/{len(ids)} ok")
    return 1 if bad else 0


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_html(rows: list[Row]) -> Path:
    """Render plan/board.html for the owner: status colours, claimability, open questions, cards without rows."""
    by_id = {r.task_id: r for r in rows}
    archived = archived_ids()
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

    shared_th = f"<th>{_esc(SHARED_FLAG)}</th>" if SHARED_FLAG else ""
    cols = 7 if SHARED_FLAG else 6
    out.append(f"<table><tr><th>Id</th><th>Title</th><th>Model</th>{shared_th}<th>Prerequisites</th><th>Status</th><th>Card</th></tr>")
    phase = None
    for r in rows:
        if r.phase != phase:
            phase = r.phase
            out.append(f'<tr class="phase"><td colspan="{cols}">Phase {_esc(phase)}</td></tr>')
        info = infos[r.task_id]
        pres = " ".join(
            f'<span class="pre {"ok" if (by_id[p].state == "done" if p in by_id else p in archived) else "no"}">{p}</span>' for p in r.prereqs
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
        shared_td = f'<td class="dim">{_esc(info["shared"])}</td>' if SHARED_FLAG else ""
        out.append(
            f'<tr><td><a href="{_esc(_rel(r.task_id))}">{r.task_id}</a></td><td>{_esc(r.title)}</td>'
            f'<td class="tag">{_esc(r.model)}</td>{shared_td}<td>{pres}</td><td>{status}</td><td>{card}</td></tr>'
        )
    out.append("</table>")

    orphans = sorted(p.name for p in TASKS_DIR.glob("T*-*.md") if p.name.split("-", 1)[0] not in by_id and p.name.split("-", 1)[0] not in archived)
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


def _being_launched(task_id: str) -> bool:
    """True while a launch.py process holds the card's lease (plan/launch.py `lease_held`)."""
    import importlib.util
    sys.dont_write_bytecode = True   # a plan/__pycache__ in MAIN makes every claim refuse
    spec = importlib.util.spec_from_file_location("_launch", Path(__file__).resolve().parent / "launch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.lease_held(task_id)


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
    for ahead in claim_order(rows):
        if ahead.task_id == row.task_id:
            break
        # A row ahead whose lease a live launch holds is being claimed this minute: autospawn starts its whole
        # batch in one second, and two spawns of a batch were once lost because the rows ahead of them had not
        # committed their claims yet. The lease exempts a card being launched.
        if claim_reason(ahead, rows, args.model) is None and not _being_launched(ahead.task_id):
            raise SystemExit(
                f"Refusing to claim {row.task_id}: {ahead.task_id} comes first in claim order for {args.model}"
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
    require_clean_main(own_work=True)
    text, row = _require_in_progress(args.task)
    short = git("rev-parse", "--short", "--verify", args.hash).strip()
    BOARD_PATH.write_text(set_status(text, row.task_id, f"done ({short})"), encoding="utf-8")
    commit_board(f"{row.task_id}: done")
    return 0


def cmd_blocked(args: argparse.Namespace) -> int:
    require_clean_main(own_work=True)
    text, row = _require_in_progress(args.task)
    reason = args.reason.replace("|", "/").strip()
    holder = _holder(row)
    BOARD_PATH.write_text(set_status(text, row.task_id, f"blocked ({holder}; {reason})"), encoding="utf-8")
    commit_board(f"{row.task_id}: blocked")
    return 0


def cmd_waiting(args: argparse.Namespace) -> int:
    """in-progress -> waiting: the agent has written questions in its card and stops until the owner answers."""
    require_clean_main(own_work=True)
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
    require_clean_main(own_work=True)
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
    `next` considers and no existing card's claim order changes (a PRIORITY_PHASES
    row is the exception: `claim_order` offers it first from any position). An empty table
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


def _parse_prereqs(arg: str | None, known: set[str]) -> list[str]:
    """The ids of a `"T01, T02"` / `"none"` argument; refuses ids the board and its archive do not know."""
    prereqs = [p.strip() for p in (arg or "").split(",") if p.strip()]
    prereqs = [p for p in prereqs if p.lower() != "none"]
    unknown = [p for p in prereqs if p not in known]
    if unknown:
        raise SystemExit(f"prerequisites not on the board: {', '.join(unknown)}")
    return prereqs


def cmd_set(args: argparse.Namespace) -> int:
    """Retitle, re-point, retag or re-phase one todo row; the card header follows, so the two never disagree."""
    require_clean_main(own_work=True)
    text, rows = load()
    row = next((r for r in rows if r.task_id == args.task), None)
    if row is None:
        raise SystemExit(f"{args.task} is not on the board")
    if row.state != "todo":
        raise SystemExit(f"{args.task} is {row.status!r}; set changes todo rows only (a held row is its agent's).")
    if args.title is None and args.prereqs is None and args.tag is None and args.phase is None:
        raise SystemExit("nothing to set: give --title, --prereqs, --tag or --phase")
    path = _card_path(row.task_id)
    if path is None:
        raise SystemExit(f"no plan/tasks/{row.task_id}-*.md exists")
    title = row.title if args.title is None else args.title.replace("|", "/").strip()
    if not title:
        raise SystemExit("the title may not be empty")
    prereqs = row.prereqs if args.prereqs is None else _parse_prereqs(
        args.prereqs, {r.task_id for r in rows} | archived_ids())
    if row.task_id in prereqs:
        raise SystemExit(f"{row.task_id} cannot be its own prerequisite")
    tag = row.model if args.tag is None else args.tag
    phase = row.phase if args.phase is None else args.phase.strip()
    cell = ", ".join(prereqs) if prereqs else "none"

    card = path.read_text(encoding="utf-8")
    new_card = card
    if args.prereqs is not None:
        new_card = set_header_field(new_card, "Prerequisites", cell)
    if args.tag is not None:
        new_card = set_header_field(new_card, "Model", tag)
    if args.phase is not None:
        new_card = set_header_field(new_card, "Phase", phase)
    problems = lint_card(path, prereqs, new_card)
    if problems:
        raise SystemExit(f"{path.name} fails the card lint ({SPEC_FILE}); fix the card and set again:\n"
                         + "\n".join(f"  - {p}" for p in problems))

    row_line = f"| {row.task_id} | {title} | {phase} | {tag} | {cell} | {row.status} |"
    lines = text.splitlines(keepends=True)
    ending = "\r\n" if lines[row.line_no].endswith("\r\n") else "\n"
    lines[row.line_no] = row_line + ending
    new_text = "".join(lines)
    if args.tag is not None and re.search(r"^Counts:.*$", new_text, flags=re.MULTILINE):
        new_text = re.sub(r"^Counts:.*$", counts_line(parse_board(new_text)), new_text, count=1, flags=re.MULTILINE)
    fields = [f for f, v in (("title", args.title), ("prereqs", args.prereqs), ("tag", args.tag),
                             ("phase", args.phase)) if v is not None]
    if new_text == text and new_card == card:
        print(f"{row.task_id} unchanged; nothing committed")
        return 0
    BOARD_PATH.write_text(new_text, encoding="utf-8")
    also = []
    if new_card != card:
        path.write_text(new_card, encoding="utf-8")
        also.append(path.relative_to(MAIN).as_posix())
    commit_board(f"Board: set {row.task_id} ({', '.join(fields)})", *also)
    print(row_line)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    require_clean_main(own_work=True)   # serial mode: a card may be added while the running card's work is uncommitted
    text, rows = load()
    task_id = args.task.strip()
    if not re.fullmatch(r"T\d{2,3}", task_id):
        raise SystemExit(f"{task_id!r} is not a task id of the form Txx")
    archived = archived_ids()
    if any(r.task_id == task_id for r in rows) or task_id in archived:
        raise SystemExit(f"{task_id} is already on the board or in its archive. Pick a free id.")
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
    prereqs = _parse_prereqs(args.prereqs, {r.task_id for r in rows} | archived)
    problems = lint_card(cards[0], prereqs)
    if problems:
        raise SystemExit(f"{cards[0].name} fails the card lint ({SPEC_FILE}); fix the card and add again:\n"
                         + "\n".join(f"  - {p}" for p in problems))
    cell = ", ".join(prereqs) if prereqs else "none"
    row_line = f"| {task_id} | {title} | {args.phase} | {args.model} | {cell} | todo |"
    BOARD_PATH.write_text(insert_row(text, row_line, args.after), encoding="utf-8")
    commit_board(f"Board: add {task_id}")
    print(f"added {task_id} ({cards[0].name}) as todo, tagged {args.model}")
    return 0


def cmd_archive_done(args: argparse.Namespace) -> int:
    """Move every done row that no unfinished row waits on to plan/BOARD-ARCHIVE.md; commit only those two files."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != MAIN_BRANCH:
        raise SystemExit(f"MAIN is on branch {branch!r}, not {MAIN_BRANCH}. Refusing.")
    text, rows = load()
    needed = {p for r in rows if r.state != "done" for p in r.prereqs}
    move = [r for r in rows if r.state == "done" and r.task_id not in needed]
    if not move:
        print("nothing to archive: every done row is still a prerequisite of an open one")
        return 0
    kept = len(rows) - len(move)
    if args.dry_run:
        print(f"would archive {len(move)} done rows and keep {kept}: " + ", ".join(r.task_id for r in move))
        return 0
    lines = text.splitlines(keepends=True)
    gone = {r.line_no for r in move}
    moved_lines = [lines[i].rstrip("\r\n") for i in sorted(gone)]
    lines = [line for i, line in enumerate(lines) if i not in gone]
    if BOARD_ARCHIVE_PATH.name not in text:
        headers = [i for i, line in enumerate(lines) if SEPARATOR_RE.match(line)]
        at = headers[-1] - 1 if headers else len(lines)       # the task table is the last table of the file
        lines[at:at] = [BOARD_ARCHIVE_NOTE + "\n", "\n"]
    new = "".join(lines)
    new = re.sub(r"^Counts:.*$", counts_line(parse_board(new)), new, count=1, flags=re.MULTILINE)
    archive = (BOARD_ARCHIVE_PATH.read_text(encoding="utf-8") if BOARD_ARCHIVE_PATH.exists() else BOARD_ARCHIVE_HEADER)
    BOARD_ARCHIVE_PATH.write_text(archive.rstrip("\n") + "\n" + "\n".join(moved_lines) + "\n", encoding="utf-8")
    BOARD_PATH.write_text(new, encoding="utf-8")
    rels = [p.relative_to(MAIN).as_posix() for p in (BOARD_PATH, BOARD_ARCHIVE_PATH)]
    message = f"Board: archive {len(move)} done rows ({kept} rows stay)"
    git("add", *rels)
    git("commit", "-q", "-m", message, "--", *rels)
    write_html(parse_board(new))
    print(f"committed: {message} ({git('rev-parse', '--short', 'HEAD').strip()})")
    return 0


# --- suggestions: agents' friction log, read by the owner to improve the repo ------

SUGGESTIONS_PATH = BOARD_PATH.parent / "SUGGESTIONS.md"
#: Triaged entries (Status no longer `open`) are moved here, so SUGGESTIONS.md stays an inbox.
SUGGESTIONS_ARCHIVE_PATH = BOARD_PATH.parent / "SUGGESTIONS-ARCHIVE.md"
SUGGESTION_RE = re.compile(r"^## (S\d{2,3}) ", flags=re.MULTILINE)
SUGGESTION_STATUS_RE = re.compile(r"^Status:\s*(\w+)", flags=re.MULTILINE)
# Entries deleted by hand before the archive existed; this marker keeps their ids from being handed out again.
SUGGESTION_FLOOR_RE = re.compile(r"<!-- Triaged up to S(\d{2,3})\b")
SUGGESTIONS_ARCHIVE_HEADER = """# Suggestions archive

Triaged suggestions, moved here from `SUGGESTIONS.md` by `python plan/board.py archive` (and by every
`board.py suggest`) once their `Status:` is no longer `open`. The status says where each one went:
`accepted (Txx)` is carried by that card, `done (<commit>)` is implemented, `rejected (<why>)` is closed.
Nothing here needs reading to do a card. Newest last.
"""


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _sweep_suggestions(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split the triaged entries out of the suggestions text: (what stays, [(id, entry)] to archive)."""
    matches = list(SUGGESTION_RE.finditer(text))
    if not matches:
        return text, []
    kept: list[str] = []
    moved: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entry = text[m.start():end].rstrip("\n") + "\n"
        status = SUGGESTION_STATUS_RE.search(entry)
        if status is None or status.group(1) == "open":
            kept.append(entry)
        else:
            moved.append((m.group(1), entry))
    if not moved:
        return text, []
    head = text[:matches[0].start()].rstrip("\n") + "\n"
    return head + ("\n" + "\n".join(kept) if kept else ""), moved


def _archived_suggestion_ids() -> list[int]:
    if not SUGGESTIONS_ARCHIVE_PATH.exists():
        return []
    archive = SUGGESTIONS_ARCHIVE_PATH.read_text(encoding="utf-8")
    return [int(m.group(1)[1:]) for m in SUGGESTION_RE.finditer(archive)]


def _append_to_archive(moved: list[tuple[str, str]]) -> None:
    archive = (SUGGESTIONS_ARCHIVE_PATH.read_text(encoding="utf-8") if SUGGESTIONS_ARCHIVE_PATH.exists()
               else SUGGESTIONS_ARCHIVE_HEADER)
    SUGGESTIONS_ARCHIVE_PATH.write_text(archive.rstrip("\n") + "\n\n" + "\n".join(e for _, e in moved),
                                        encoding="utf-8")


def cmd_archive(args: argparse.Namespace) -> int:
    """Move every triaged suggestion to plan/SUGGESTIONS-ARCHIVE.md and commit only those two files."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != MAIN_BRANCH:
        raise SystemExit(f"MAIN is on branch {branch!r}, not {MAIN_BRANCH}. Refusing.")
    text, moved = _sweep_suggestions(SUGGESTIONS_PATH.read_text(encoding="utf-8"))
    if not moved:
        print("nothing to archive: every entry is still open")
        return 0
    _append_to_archive(moved)
    SUGGESTIONS_PATH.write_text(text, encoding="utf-8")
    rels = [p.relative_to(MAIN).as_posix() for p in (SUGGESTIONS_PATH, SUGGESTIONS_ARCHIVE_PATH)]
    message = "Plan: suggestions archive (" + ", ".join(cid for cid, _ in moved) + ")"
    git("add", *rels)
    git("commit", "-q", "-m", message, "--", *rels)
    print(f"committed: {message} ({git('rev-parse', '--short', 'HEAD').strip()})")
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    """Append a suggestion (or a +1 to an existing one) to plan/SUGGESTIONS.md and commit only that file.

    It is written in MAIN, never on a task branch, so two cards suggesting at once never conflict
    at merge time. A suggestion without a proposed solution is refused: the file is a to-do list for
    the owner, not a place to vent.
    """
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != MAIN_BRANCH:
        raise SystemExit(f"MAIN is on branch {branch!r}, not {MAIN_BRANCH}. Refusing.")
    if not args.propose:
        raise SystemExit('at least one --propose "<solution>" is required: every suggestion carries a fix.')
    # Triaged entries leave with this commit, so the file an agent skims holds open entries only.
    text, moved = _sweep_suggestions(SUGGESTIONS_PATH.read_text(encoding="utf-8"))
    today = dt.date.today().isoformat()
    who = f"{args.by}" + (f" on {args.card}" if args.card else "")
    if args.also:
        matches = list(SUGGESTION_RE.finditer(text))
        hit = next((i for i, m in enumerate(matches) if m.group(1) == args.also), None)
        if hit is None:
            triaged = [cid for cid, _ in moved] + [f"S{n:02d}" for n in _archived_suggestion_ids()]
            if args.also in triaged:
                raise SystemExit(f"{args.also} is already triaged (see {SUGGESTIONS_ARCHIVE_PATH.name}): if it still "
                                 f"hurts, file a new suggestion that names {args.also}.")
            raise SystemExit(f"{args.also} is not in {SUGGESTIONS_PATH.name}.")
        end = matches[hit + 1].start() if hit + 1 < len(matches) else len(text)
        lines = [f"- Seen again {today}, {who}: {_one_line(args.problem or '')}".rstrip(": ")]
        lines += [f"  - proposal: {_one_line(p)}" for p in args.propose]
        gap = "\n" if "- Seen again" in text[matches[hit].start():end] else "\n\n"
        entry = text[:end].rstrip("\n") + gap + "\n".join(lines) + "\n\n"
        text = entry + text[end:].lstrip("\n")
        cid, message = args.also, f"Plan: suggestion {args.also} seen again"
    else:
        if not args.title or not args.problem:
            raise SystemExit("a new suggestion needs a title and --problem (or use --also Sxx to +1 one).")
        ids = [int(m.group(1)[1:]) for m in SUGGESTION_RE.finditer(text)]
        ids += [int(m.group(1)) for m in SUGGESTION_FLOOR_RE.finditer(text)]
        ids += [int(cid[1:]) for cid, _ in moved] + _archived_suggestion_ids()   # ids are never reused
        cid = f"S{(max(ids) + 1 if ids else 1):02d}"
        body = [
            f"## {cid} {_one_line(args.title)}",
            "",
            f"Status: open | {today} | {who} | area: {args.area}"
            + (f" | where: `{_one_line(args.where)}`" if args.where else ""),
            "",
            _one_line(args.problem),
            "",
            "Proposals:",
            *[f"{i}. {_one_line(p)}" for i, p in enumerate(args.propose, 1)],
            "",
        ]
        text = text.rstrip("\n") + "\n\n" + "\n".join(body)
        message = f"Plan: suggestion {cid}"
    SUGGESTIONS_PATH.write_text(text, encoding="utf-8")
    rels = [SUGGESTIONS_PATH.relative_to(MAIN).as_posix()]
    if moved:
        _append_to_archive(moved)
        rels.append(SUGGESTIONS_ARCHIVE_PATH.relative_to(MAIN).as_posix())
    git("add", *rels)
    git("commit", "-q", "-m", message, "--", *rels)   # only the suggestions files, whatever else is staged
    print(f"committed: {message} ({git('rev-parse', '--short', 'HEAD').strip()}) -> {cid}"
          + (f"; archived {', '.join(c for c, _ in moved)}" if moved else ""))
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

    p = sub.add_parser("set", help="change a todo row's title, prerequisites, tag or phase; the card header follows")
    p.add_argument("task")
    p.add_argument("--title")
    p.add_argument("--prereqs", help='comma-separated task ids, or "none"')
    p.add_argument("--tag", type=valid_tag, help='a model key or keys joined with "-or-"')
    p.add_argument("--phase")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("lint", help="check cards against the card rules: Kind header, a source on every threshold, "
                                    "no planner number as a gate, the owner's word for OWNER_WORD_MODELS; "
                                    "default every todo row")
    p.add_argument("tasks", nargs="*", help="task ids (default: every todo row)")
    p.add_argument("--max", type=int, default=6, help="problems printed per card")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("suggest", help="log friction plus proposed fixes in plan/SUGGESTIONS.md")
    p.add_argument("title", nargs="?", help="one-line name of the suggestion (omit with --also)")
    p.add_argument("--problem", help="what costs time, tokens or rework, and how much")
    p.add_argument("--propose", action="append", default=[], help="a proposed solution; repeat for several")
    p.add_argument("--by", required=True, help="model key of the suggesting agent")
    p.add_argument("--card", help="the card being worked when it hurt")
    p.add_argument("--area", default="other",
                   help="code, architecture, performance (the code itself); "
                        "environment, build, tests, board, docs, tooling, spec, other (the process)")
    p.add_argument("--where", help="for code suggestions: <file>:<symbol> the problem lives in")
    p.add_argument("--also", metavar="Sxx", help="+1 an existing suggestion instead of opening a new one")
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("archive-done", help="move done rows that no open row waits on to plan/BOARD-ARCHIVE.md")
    p.add_argument("--dry-run", action="store_true", help="say what would move, change nothing")
    p.set_defaults(func=cmd_archive_done)

    sub.add_parser("archive", help="move triaged suggestions (Status no longer open) to "
                                   "plan/SUGGESTIONS-ARCHIVE.md").set_defaults(func=cmd_archive)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
