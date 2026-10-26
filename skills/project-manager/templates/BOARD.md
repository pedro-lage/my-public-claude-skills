# {{PROJECT}} task board

Protocol: see `CLAUDE.md` (Board protocol) and `plan/INVOKE.md`; `{{SPEC_FILE}}` is the authoritative spec, and every task branch is cut from and merged back into `{{MAIN_BRANCH}}`. Status values: `todo`, `in-progress (<agent>, <date>)`, `waiting (<agent>, <date>; <n> questions)` (the agent wrote questions for {{OWNER}} in its card and stopped; it continues once answered), `blocked (<agent>, <date>; <reason>)`, `done (<hash>)`.

Agents never edit this file by hand. Every change goes through `python plan/board.py` (`next`, `claim`, `waiting`, `answered`, `done`, `blocked`, `add`, `set`, `archive-done`), which enforces the rules below. Only {{OWNER}} edits rows manually, and only to reset a row to `todo` or re-point prerequisites, in a commit titled `Board: reset ...` or `Board: ...`.

Ownership: an `in-progress` or `blocked` row belongs to the agent named in it until that agent marks it `done` or {{OWNER}} resets it. The date is when the claim happened, nothing more. No age, idle worktree, missing commits or suspected crash makes a held row claimable; an agent that finds no `todo` row it may claim stops with "No valid task available".

Model assignment, the only rule for who may claim a card. A tag is one model key or several joined with `-or-`; a model may claim a row when its key is one of the parts. Delete the rows of models {{OWNER}} will not run (and their keys in `board.py` `MODELS`); add a `local` row only when there is a local model.

| Tag | Who may execute |
|---|---|
| `fable` | Only Claude Code running Claude Fable. |
| `opus55` | Only Claude Code running Claude Opus 5.5 (cheap cards set `Effort: medium` in their header). |
| `sonnet` | Only Claude Code running Claude Sonnet. |
| `opus55-or-sonnet` | Either Claude Opus 5.5 or Claude Sonnet, whichever is free. |
| `fable-or-opus55` | **Not "whichever is free": Fable first.** Fable runs the first context, Opus 5.5 every context from the first checkpoint (`autospawn.py --handoff`). It costs a Fable context, so it needs {{OWNER}}'s word like `fable` (below). |

**The most expensive model needs {{OWNER}}'s word.** A tag that names a model in `board.py` `OWNER_WORD_MODELS` (default `fable`, so `fable` and `fable-or-opus55`) needs a `Fable: [{{OWNER}} <date>] <why>` line in the card, and `board.py lint` and `add` refuse it otherwise. Everything else goes to a cheaper model, and a hard diagnosis gets `Effort: xhigh`, not the expensive model. Measured on one project: a Fable request took about 6-7x an Opus 5.5 request's share of the plan's usage window, and three Fable cards in parallel used up a 5-hour window in 90 minutes.

Reasoning effort travels with the card as an optional `Effort: <low|medium|high|xhigh|max>` in its header, next to the Model tag; without one the card runs at `board.EFFORTS[model]` (fable and opus55 high, sonnet medium). Effort is fixed when the process starts, so it is applied by `python plan/launch.py [model]`, which prints (or `--run`s) the `claude --model <m> --effort <e>` line for the next claimable card. `${{ENV_PREFIX}}_EFFORT` overrides every card for a tight week.

{{OWNER}} runs the most expensive model until no card for it is claimable, then switches down. The expensive model never claims a card tagged for a cheaper one, even when idle. Haiku is not used: it cannot run in auto mode, so it cannot work unattended.

Phases: {{PHASES}}. Phase 0 is tooling and is claimed first: `board.py` offers a claimable phase 0 row before any other row, wherever it sits in the table (`PRIORITY_PHASES`), because a tooling card makes every card still `todo` cheaper; prerequisites and tags still apply, and a tooling card that would not pay back on the cards left is not added. Checkpoints are deliverables {{OWNER}} reviews before the next phase is claimed.

Done rows that no open row waits on live in `plan/BOARD-ARCHIVE.md` (`python plan/board.py archive-done`); a prerequisite listed there counts as done.

| Id | Title | Phase | Model | Prerequisites | Status |
|---|---|---|---|---|---|

Counts: none yet.
