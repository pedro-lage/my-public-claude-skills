# Task board

Protocol: see `CLAUDE.md` (Board protocol) and `plan/INVOKE.md`. Status values: `todo`, `in-progress (<agent>, <date>)`, `waiting (<agent>, <date>; <n> questions)` (the agent wrote questions for {{OWNER}} in its card and stopped; it continues once answered), `blocked (<agent>, <date>; <reason>)`, `done (<hash>)`.

Agents never edit this file by hand. Every change goes through `python plan/board.py` (`next`, `claim`, `waiting`, `done`, `blocked`, `add`), which enforces the rules below. Only {{OWNER}} edits rows manually, and only to reset a row to `todo` or re-point prerequisites, in a commit titled `Board: reset ...` or `Board: ...`.

Ownership: an `in-progress` or `blocked` row belongs to the agent named in it until that agent marks it `done` or {{OWNER}} resets it. The date is when the claim happened, nothing more. No age, idle worktree, missing commits or suspected crash makes a held row claimable; an agent that finds no `todo` row it may claim stops with "No valid task available".

Model assignment, the only rule for who may claim a card. A tag is one model key or several joined with `-or-`; a model may claim a row when its key is one of the parts.

| Tag | Who may execute |
|---|---|
| `fable` | Only Claude Code running Claude Fable. |
| `opus` | Only Claude Code running Claude Opus. |
| `sonnet` | Only Claude Code running Claude Sonnet. |
| `local` | Only {{OWNER}}'s local model. |
| `opus-or-local` | Either Claude Opus or the local model, whichever is free. |

There is deliberately no tag that pairs the most expensive model with a cheaper one: expensive budget goes only to cards that need it. {{OWNER}} runs the most expensive model until no card for it is claimable, then switches down.

Phases: {{PHASES}}. Checkpoints are deliverables {{OWNER}} reviews before the next phase is claimed.

| Id | Title | Phase | Model | Prerequisites | Status |
|---|---|---|---|---|---|

Counts: none yet.
