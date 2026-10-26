---
name: project-manager
description: Plan and run a multi-agent software project on one repository with a git-backed task board - split work into task cards with disjoint file ownership and explicit prerequisites, tag each card with the cheapest capable model, execute cards one per context in private git worktrees merged back through a board tool that enforces claims, and run the board unattended with a board manager loop (autospawn) that launches, resumes and recovers cards. Use when asked to set up a task board or plan files, plan or split work into tasks, start a new phase, allocate tasks to models, "invoke" or claim the next task, run the board overnight, or run several agents in parallel on one codebase.
---

# Project manager

One repository, several agents at once, no shared memory between them. Everything lives under `plan/` in the project:

| Part | What it is |
|---|---|
| `plan/BOARD.md` | One row per task: id, title, phase, model tag, prerequisites, status. Read by everyone, written only by the tool. |
| `plan/board.py` | The only writer of the board: `next`, `claim`, `waiting`, `answered`, `done`, `blocked`, `add` (lints the card), `set` (rewrites a `todo` row and its card header), `lint`, `suggest`, `archive`, `archive-done`, `show`, `html`, `watch`. Refuses anything that breaks the rules; no override flag on purpose. |
| `plan/start.py` | The head of the protocol in one command: claim through `board.py`, worktree and branch, the worktree's environment (linked, copied or empty dirs), print the card and the doc pages for its files. `--resume Txx` re-attaches to a checkpointed card and prints only its Handoff block. |
| `plan/finish.py` | The tail in one command: gates (questions, Result, Reproduce A/B, optional test-coverage, tier and doc gates), stage the card's own Files (never `git add -A`), commit, main lock, MOVED decision, merge `--no-ff`, mark done, remove worktree and branch. Also `--checkpoint`, `--blocked`, `--dry-run`. `plan/test_finish_gates.py` tests the gates on a scratch repo. |
| `plan/launch.py` | Run by the owner, not an agent: prints or `--run`s the `claude --model <m> --effort <e>` line for the next card, holds the card's lease for the session's life, `--headless` for unattended runs, `--resume`, `--answer`. Effort is fixed at process start, so it is chosen here. |
| `plan/autospawn.py` | **The board manager.** A plain loop, no tokens: starts a launch whenever a slot frees, resumes checkpointed cards, hands an expensive model's card to a cheaper one after its first context, detects orphans and usage limits from transcripts, waits out a dirty MAIN, runs an optional regression net on an idle board. `reference/operations.md`. |
| `plan/context_hook.py` | A PostToolUse hook that tells a card session its context size and when to checkpoint (~175k); agents cannot see it themselves. |
| `plan/ANSWER.md` | The stand-in answerer's prompt, used only when the owner turns stand-ins on (`autospawn.py --answers N`). |
| `plan/COMPACT.md` | The periodic pass that moves or cuts what no longer needs to sit in the always-loaded documents. Run by the owner at a phase checkpoint. |
| `plan/SUGGESTIONS.md` | Agents' improvement log: a problem in the code or the process plus concrete fixes, appended only by `board.py suggest` (`--also Sxx` +1s a duplicate). An inbox of open entries: once the owner sets `Status:`, `archive` (and every `suggest`) moves it to `SUGGESTIONS-ARCHIVE.md`. When you triage for the owner, set statuses, commit, `archive` in the same pass. |
| `plan/tasks/Txx-*.md` | One card per task: Kind, goal, what exists, files, spec, acceptance with Defect view, how the owner checks it, questions, Result with Reproduce block. |
| `plan/INVOKE.md` | The single prompt an executing agent follows: orient, `start.py`, reproduce, work, `finish.py`, report, stop. Kept to about a page: every agent re-reads it on every turn. |

Invariants every mode respects: MAIN stays clean; the board changes only through `board.py`, one row per commit; a task touches only the files its card lists; one task per agent context; an `in-progress`, `waiting` or `blocked` row belongs to the agent named in it until the owner resets it.

**What this system costs, and the rules that follow** (measured on one project, about 215 cards). An agent turn is billed the whole context, so a session costs roughly `turns x average context`, quadratic in turns: one card ran 535 turns, grew from 60k to 338k and consumed 118M cached input tokens; protocol bookkeeping alone was 15 to 20 of those turns. Hence (a) the mechanical steps live in `start.py`/`finish.py`, never in a numbered list the agent executes by hand; (b) cards checkpoint near 175k context (`context_hook.py`) and resume fresh; (c) effort is the largest lever after context length (thinking was ~86 % of output tokens) and is set at launch from the card's `Effort:` header; (d) lookups were 45-52 % of spend, so agents batch queries and find code by symbol; (e) idle time is free — waits on a lock cost no re-cache — so never poll or serialise to keep a cache warm; (f) no orchestrator agent: one cost 165M cached tokens over 618 turns to spawn, poll and message, while `autospawn.py` does the same for nothing. Quality had its own measured lesson: 38 % of closed cards had a material spec problem against 5 % caused by the executor, and five cards in a row closed `done` on numbers the executor defined while the owner saw no change. Hence `Kind: build|explore`, sourced thresholds, methods as suggestions, and acceptance as the owner's check at a Defect view, A against B (`reference/planning.md` §2).

Decide which mode the request is, then follow that section. The files under `templates/` and `reference/` are the sources; the project's own `plan/INVOKE.md` and `CLAUDE.md` win once they exist.

## Mode A: bootstrap a project

Use when the project has no `plan/` directory yet.

1. Read the project's `CLAUDE.md`, README and spec if any. Collect: project name (the repo directory name; it names the worktrees folder), owner's name, main branch, the spec file (default `decisions.md`; create it from the brief if missing), an upper-case env-var prefix (e.g. `ACME`), the heavy environment a card may need (a build engine, a database, a GPU; or none) and its gitignored cache dirs, the environment setup commands for a fresh checkout, the test command, which paths can change a test verdict, and which models the owner will run.
2. Copy every script from `templates/` to `plan/`: `board.py`, `start.py`, `finish.py`, `launch.py`, `autospawn.py`, `context_hook.py`, `test_finish_gates.py`. Replace every `{{PLACEHOLDER}}` (grep for `{{` afterwards; none may remain):
   - `{{OWNER}}`, `{{MAIN_BRANCH}}`, `{{SPEC_FILE}}`, `{{ENV_PREFIX}}` (every env var: `<PREFIX>_IN_MAIN`, `_EFFORT`, `_COAUTHOR`, `_ALL_TOOLS`, `_CHECKPOINT_AT`).
   - `{{SHARED_FLAG}}`: the card-header word that says a card needs the heavy environment (`Unity`, `Docker`; keep it even with none, cards use it to say what they need).
   - `{{SHARED_DIRS}}` (linked to MAIN's copy), `{{SEEDED_DIRS}}` (copied per worktree), `{{EMPTY_DIRS}}` (created empty), as Python list items, possibly empty. Prefer seeding a heavy cache over linking it: a shared cache let one worktree's build overwrite another's, and a worktree built against MAIN's newer data crashed; a copy took about 10 s.
   - `{{ENV_SETUP_ARGV}}` (argv lists that make a fresh worktree usable), `{{BUDGET}}` (one clause: the expensive operation and how many a context gets, e.g. "about 8 engine launches or 3 screenshot sheets").
   - `{{BUILD_PATHS}}` (top-level paths whose change on main can change a test verdict; only those make a merge MOVED), `{{RETEST_ALWAYS}}` (prefixes that always force MOVED, e.g. dependency manifests), both as Python list items like the dir lists (`"src", "tests"`), and `{{TEST_CMD}}`.
   - In `board.py`'s settings: `MODELS` (delete models the owner will not run; `local` only if there is a local model; never `haiku` unless asked, it has no auto mode and cannot run unattended), `EFFORTS`, `CLI_MODELS` (pin keys whose alias drifts), `OWNER_WORD_MODELS` (models a tag may name only with the owner's line on the card; default `fable`), `PRIORITY_PHASES` (default phase 0: tooling first).
   - Optional features default off; turn on what the project has: `WIKI_TOOL`/`SYM_TOOL` (doc pages and symbol lookup printed on claim, doc gate at finish), `TIERS_SCRIPT` (test tiers: which tests a card's paths can affect; finish.py's docstring lists the functions it needs), test-result locations for the coverage gate, `RENDER_DIRS` (where A/B captures are found), `POST_MERGE_HOOK`, `STOP_CMD`, `SEED_LOCK`/`BUSY_CHECK` in `start.py`, `IDLE_JOB` and `TERMINAL_CMD` in `autospawn.py`, `ANSWERERS`/`TRIM_TOOLS` in `launch.py`.
   Then run `python plan/test_finish_gates.py` and one throwaway card end to end in a temp repo (two rows, a `{{SHARED_FLAG}}: no` card; `autospawn.py --once --dry-run`) before handing the project to any agent: refusal gates, merge and cleanup are much cheaper to debug there.
3. Copy `templates/BOARD.md`, `INVOKE.md`, `SUGGESTIONS.md`, `ANSWER.md` and `COMPACT.md` to `plan/` and fill: `{{PROJECT}}`, `{{OWNER}}`, `{{MAIN_BRANCH}}`, `{{SPEC_FILE}}`, `{{ENV_PREFIX}}`, `{{PHASES}}`, the model table in `BOARD.md` (one row per key in `MODELS` plus the `-or-` pairs the plan uses); `{{ITERATION_TIPS}}` in `INVOKE.md` (three to six bullets on running the slow tooling narrowly while iterating: one test instead of the suite, several probes in one launch, the cheap render instead of the full build — most of a card's turns go here); `{{BUDGET}}` (same clause as in `start.py`); `{{SUGGESTION_AREAS}}` in `SUGGESTIONS.md` (the project's friction areas as backticked words).
4. Create `plan/tasks/` and put `templates/TASK.md` there as `plan/tasks/TEMPLATE.md`, placeholders filled (the board ignores it: no `Txx-` prefix).
5. Append `templates/CLAUDE-section.md`, filled, to the project's `CLAUDE.md`. If the project has conflicting commit or branching rules, the board protocol wins for task work; say so.
6. Merge `templates/settings-hook.json` into `.claude/settings.json` (the context hook).
7. `.gitignore`: `__pycache__/`, `/plan/board.html` (regenerated on every board command; `board.py watch` keeps it fresh for a browser tab), `Logs/` (leases, main lock, transcripts, autospawn state), environments and caches. The scripts also set `sys.dont_write_bytecode`; a `plan/__pycache__` in MAIN makes every claim refuse. Worktrees live beside the repo in `<project>.worktrees/`.
8. Mark generated or bulky tracked files `-diff` (and `merge=binary` where a line merge is meaningless) in `.gitattributes` so no diff prints them. Keep the always-loaded files (CLAUDE.md, INVOKE.md) short; agents read only the spec sections their card names. A per-subsystem doc page (what it does, where a change starts, what is easy to get wrong) cut fresh cards' requests before the first edit roughly in half.
9. Keep the owner's brief in the repo (`docs/brief-<iteration>.md`) and make re-plan cards read it. Untracked scratch files at the root make MAIN dirty; commit them or list them in `.git/info/exclude`.
10. Shared external quotas (one machine, one IP): a tooling card that wraps an API specifies a cache shared across worktrees consulted before every fetch, no retry on 4xx or rate limits (fail fast with the reset time), the remaining quota printed on every call, and the credential as a user environment variable, never a file. Four research agents once exhausted an unauthenticated quota within minutes and routed around it with worse fetches.
11. Commit on MAIN: `Plan: conventions, task board, invoker prompt`. Then continue with Mode B.

Optional: a project-local skill whose whole body is "read `plan/INVOKE.md` and follow it" lets other runners invoke the same protocol. Keep it a pointer.

## Mode B: plan or re-plan tasks

Use when asked to plan, split, add, re-scope or re-tag work, or to start a new phase. Read `reference/planning.md` in full first: decomposition rules, card kinds and acceptance, model allocation, owner operations.

Short form:

1. Settle the spec first. Grill the owner if the `grilling` skill is available (they may answer in files); record decisions in the spec with dates. No cards for undecided scope. A new phase starts with `board.py archive-done` and `archive`, and reviews which earlier decisions still bind.
2. Write cards from `plan/tasks/TEMPLATE.md`: one context's worth of work, disjoint files, true prerequisites only, `Kind: build` (exact spec) or `Kind: explore` (diagnose first, methods as ideas, A/B acceptance), every threshold sourced, a Defect view and "How the owner checks this" for any visible result, a "What exists" section when it builds on done work.
3. Tag each card `opus55` by default, effort set to the difficulty (`medium` mechanical, `high` default, `xhigh` hardest diagnosis). The most expensive model only with the owner's word, written on the card. When you lower effort, sharpen the spec.
4. `python plan/board.py lint Txx`, commit the cards on MAIN (`Plan: add Txx, <title>`, or one `Plan:` commit for a batch), then `python plan/board.py add Txx "<title>" <phase> <tag> "<prereqs>" [--after Tyy]` in claim order. Change a `todo` row only with `board.py set`.
5. Report: the new rows, the `Counts:` line, what each model will claim, and the launch line (`python plan/launch.py`, or `python plan/autospawn.py --once --dry-run` then `--max N`). Point the owner at `plan/board.html`.

Planning sessions with the owner are the one case where files are committed directly on MAIN. Never touch an `in-progress`, `waiting` or `blocked` card.

## Mode C: invoke, execute one task

Use when asked to invoke, claim, run or continue the next task.

1. From the repository root, read `plan/INVOKE.md` in full and follow it exactly: `python plan/start.py <model>`, the Reproduce block, the work, `python plan/finish.py Txx`. Do not reimplement either script's steps by hand. Do not start a second task.
2. If `plan/INVOKE.md` is missing, say so and stop; Mode A creates it.
3. If the board tool refuses or reports "No valid task available", print its output verbatim and stop. Doing no task is a correct result.
4. Never act as an orchestrator: do not spawn agents to run cards and poll them. The owner launches cards, or `plan/autospawn.py` does.

## Mode D: owner operations

Only when the owner asks, in the owner's own session: run the board unattended (`autospawn.py`; always `--once --dry-run` first, and ask which mode — worktrees with `--max N`, or serial `--max 1 --in-main`), answer a waiting card, reset a held row, unblock, reorder or re-tag `todo` rows (`board.py set`), archive, compact documents (`plan/COMPACT.md`), recover a stale card from its transcript, audit token cost. Procedures: `reference/planning.md` §5 and `reference/operations.md`. Every hand edit to the board is its own `Board: ...` commit on MAIN. Never stop a running board manager without asking.
