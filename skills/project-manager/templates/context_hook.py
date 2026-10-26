"""Claude Code PostToolUse hook (.claude/settings.json): tell a card session when to checkpoint by context size.

Wired by templates/settings-hook.json as `python "$CLAUDE_PROJECT_DIR/plan/context_hook.py"`.

Measured on one project: a resume costs about $1 (about five re-orienting requests plus reading the Handoff),
every later request saves the dropped context, and the sum is cheapest at a checkpoint near 175k tokens; yet
contexts were checkpointed at a median of 228k, the most expensive model's up to 414k, because an agent cannot see
its own context size. This reads the size from the session transcript (the last request's input + cache write +
cache read) and, once it passes THRESHOLD and again every STEP above it, adds a reminder to the agent's context.
Silent in sessions that are not cards (no `plan/start.py` call: chats, stand-in answers) and in --no-budget
resumes. Never fails a tool call.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

THRESHOLD = int(os.environ.get("{{ENV_PREFIX}}_CHECKPOINT_AT") or 175_000)     # the variable is for testing the hook
STEP = 40_000
TAIL_BYTES = 512 * 1024

#: One state file per session, under a temp dir named after the repository, so two projects on one
#: machine never share reminder levels.
_REPO = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) or "repo"
STATE_DIR = os.path.join(tempfile.gettempdir(), re.sub(r"[^\w-]", "_", _REPO) + "-context-hook")

MESSAGE = ("[context hook] This context is at {k}k tokens. Checkpointing is cheapest near {t}k: at the next clean "
           "point overwrite the Handoff block (State, Next, Done when, Open, Tried), run "
           "`python plan/finish.py {task} --checkpoint` and end the context. Skip it only if {task} will finish within "
           "about 15 more requests.")


def context_size(path: str) -> int:
    """Tokens in the newest request's context, from the transcript's last assistant usage (0 if none)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        u = (d.get("message") or {}).get("usage") if d.get("type") == "assistant" else None
        n = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")) if u else 0
        if n:                               # a <synthetic> message (interrupt, API error) carries zeros
            return n
    return 0


def card_of(path: str) -> str | None:
    """The card this session works ("" for a --no-budget resume), None when it is not a card session."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    prompt = next((line for line in text.splitlines() if '"type":"user"' in line.replace(" ", "")), "")
    if "No budget for this context" in prompt:   # launch.py NO_BUDGET_NOTE, in the session's first prompt only
        return ""
    # a shell call of start.py (claim or resume), not a mention of it in a chat about the tooling
    starts = [c for c in re.findall(r'"command":\s*"((?:[^"\\]|\\.)*)"', text)
              if re.search(r"(?:^|[;&|]\s*)python[\w.]*\s+(?:-\w+\s+)*(?:[\w:.~-]+[/\\]+)*plan[/\\]+start\.py\s+"
                           r"(?!--help)[\w-]", c)]
    if not starts:
        return None
    for c in starts:
        m = re.search(r"start\.py\s+(?:--resume\s+|\w+\s+)(T\d+)\b", c)
        if m:
            return m.group(1)
    m = re.search(r"=== (T\d+) (?:claimed|resumed)", text)
    return m.group(1) if m else "Txx"


def main() -> int:
    try:
        event = json.load(sys.stdin)
        path = event.get("transcript_path") or ""
        if not path or not os.path.exists(path):
            return 0
        size = context_size(path)
        if size < THRESHOLD:
            return 0
        level = (size - THRESHOLD) // STEP
        os.makedirs(STATE_DIR, exist_ok=True)
        state_path = os.path.join(STATE_DIR, re.sub(r"[^\w-]", "_", event.get("session_id") or "unknown") + ".json")
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {"level": -1}
        if level <= state.get("level", -1):
            return 0
        if "card" not in state:
            state["card"] = card_of(path)
        state["level"] = level
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        if not state["card"]:
            return 0
        msg = MESSAGE.format(k=size // 1000, t=THRESHOLD // 1000, task=state["card"])
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
    except Exception:                       # a reminder is never worth a failed tool call
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
