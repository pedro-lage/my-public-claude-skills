---
name: project-manager
description: Plan and run a multi-agent software project on one repository with a git-backed task board - split work into task cards with disjoint file ownership and explicit prerequisites, tag each card with the cheapest capable model, and execute cards one per context in private git worktrees merged back through a board tool that enforces claims. Use when asked to set up a task board or plan files, plan or split work into tasks, allocate tasks to models, "invoke" or claim the next task, or run several agents in parallel on one codebase.
---

# Project manager

One repository, several agents at once, no shared memory between them. The system has four parts, all under `plan/` in the project:

| Part | What it is |
|---|---|
| `plan/BOARD.md` | One row per task: id, title, phase, model tag, prerequisites, status. Read by everyone, written only by the tool. |
| `plan/board.py` | The only writer of the board. `next`, `claim`, `waiting`, `answered`, `done`, `blocked`, `add`, `show`, `html`, `watch`. Refuses anything that breaks the rules; no override flag on purpose. Standard library only. |
| `plan/start.py` | One command for the head of the protocol: claim the next valid task through `board.py`, create the worktree and branch, link any shared cache, print the card. `--resume Txx` re-attaches to a checkpointed card. |
| `plan/launch.py` | Run by the owner from a shell, not by an agent: prints (or `--run`s) the `claude --model <m> --effort <e>` line for the next claimable card. This is all an orchestrator agent was ever doing, done by a table lookup for nothing. |
| `plan/finish.py` | One command for the tail: stage the card's own Files (never `git add -A`), commit, merge `--no-ff`, decide whether main moved, re-verify, mark done, remove worktree and branch. Also `--checkpoint`, `--blocked`, `--verified`. |
| `plan/tasks/Txx-*.md` | One card per task: goal, files it owns, numbered spec, offline acceptance test, questions for the owner, result. |
| `plan/INVOKE.md` | The single prompt an executing agent follows: orient, `start.py`, work, `finish.py`, report, stop. Kept to about a page: every agent reads it on every card, and it is then re-read on every turn of that context. |

Invariants every mode respects: MAIN stays clean; the board changes only through `board.py`, one row per commit; a task touches only the files its card lists; one task per agent context; an `in-progress` or `blocked` row belongs to the agent named in it until the owner resets it by hand.

**What this system costs, and the two rules that follow.** An agent turn is billed the whole context, so a session's cost is roughly `turns x average context` — quadratic in the number of turns, since the context only grows. Measured on a real project (82 cards): one Sonnet card ran 535 turns, grew from 60k to 338k of context and consumed 118M cached input tokens; protocol bookkeeping alone (claim, worktree, link, stage, commit, merge, done, cleanup) was 15 to 20 of those turns, each at full price. Hence: (a) the protocol's mechanical steps live in `start.py`/`finish.py`, not in the agent's turns — never re-expand them into a numbered list the agent executes by hand; (b) cards carry a context budget and a `--checkpoint`/`--resume` path, because finishing a card in two fresh contexts is far cheaper than one long one. A third lever sits outside the agent entirely. Reasoning effort is fixed when the process starts, so no prompt can set its own; on the measured project thinking was ~86% of billed output tokens and output ~20% of the whole bill, which makes effort the largest lever after context length. `plan/launch.py` applies it at launch from the card's `Effort:` header, falling back to a per-model default. Set it as you set the model tag - `high` for diagnosis, geometry and subtle bugs, `low` for mechanical cards - and err upward on anything diagnostic, since a card that under-reasons and needs a second pass costs far more than the thinking it saved. A fourth measured result: idle time is free — 5 to 20 minute waits on a build lock cost no re-cache (only gaps beyond the cache TTL do), so never serialise work or poll in order to "keep the cache warm".

Decide which mode the request is, then follow that section. The files under `templates/` and `reference/` next to this skill are the sources; the project's own `plan/INVOKE.md` and `CLAUDE.md` win over them once they exist.

## Mode A: bootstrap a project

Use when the project has no `plan/` directory yet.

1. Read the project's `CLAUDE.md`, README and spec if any. Collect: project name (the repo directory name; it names the worktrees folder), owner's name, main branch, the spec file (default `decisions.md`; create it from the brief if missing), the environment setup commands for a fresh checkout, the test command, the install command, and which models the owner will run.
2. Copy `templates/board.py`, `templates/start.py`, `templates/finish.py` and `templates/launch.py` to `plan/`. Edit the settings block at the top of each:
   - `board.py`: `OWNER`, `MAIN_BRANCH`, `SHARED_FLAG` (same as in `start.py`), `MODELS` (delete models the owner will not run; keep `local` only if there is a local model; never add `haiku` unless the owner explicitly asks for it, since Haiku has no auto mode and cannot run a card unattended), and `EFFORTS`, one reasoning-effort default per model key.
   - `launch.py`: `{{EFFORT_ENV}}`, the environment variable that overrides effort for every launch (e.g. `MYPROJECT_EFFORT`).
   - `start.py`: `{{SHARED_DIRS}}` (gitignored caches a worktree shares with MAIN through a junction or symlink, or empty), `{{SHARED_FLAG}}` (the card-header word that switches them on, e.g. `Unity`, `Docker`; keep it even with no shared dirs, cards use it to say what they need), `{{ENV_SETUP_ARGV}}` (argv lists that make a fresh worktree usable), `{{BUDGET}}` (one clause naming the expensive operation and how many of it a context gets, e.g. "about 8 Unity launches or 3 contact sheets").
   - `finish.py`: the same `SHARED_DIRS`/`SHARED_FLAG`, and `{{VERIFY_CMD}}`/`{{VERIFY_CMD_PLAIN}}`/`{{VERIFY_OK}}` — the *cheapest* command that shows the merge did not break the build (a compile, a smoke test), never the card's full acceptance test, which already passed on the branch.
   Then run one throwaway card end to end (a temp repo, two rows, a `Unity: no` card) before handing the project to any agent: the refusal gates, the merge and the cleanup are much cheaper to debug there than inside a claimed card.
3. Copy `templates/BOARD.md` to `plan/BOARD.md` and `templates/INVOKE.md` to `plan/INVOKE.md`. Replace every `{{PLACEHOLDER}}`:
   - `{{PROJECT}}`, `{{OWNER}}`, `{{MAIN_BRANCH}}`, `{{SPEC_FILE}}`, `{{PHASES}}`.
   - `{{ITERATION_TIPS}}`: three to six bullets on running the project's slow tooling narrowly while iterating (one test instead of the suite, several probes in one launch, the cheap render instead of the full build). This is where most of a card's turns go, so it earns its space.
   - `{{BUDGET}}`: the same clause as in `start.py`, so the agent meets it whether it reads the prompt or the script's output.
   - `{{TEST_CMD}}`, `{{INSTALL_CMD}}`: how to run tests and install dependencies from MAIN's own environment.
   `{{ENV_SETUP}}` is no longer a prompt placeholder: environment setup happens in `start.py`.
   - `{{SHARED_CACHE_NOTE}}` and `{{SHARED_CACHE_CLAUSE}}`: if the project keeps gitignored data caches, tell the agent how to point the worktree at MAIN's copy (a local settings file with an absolute path) and state that cards write disjoint files under it; otherwise delete the placeholder.
   - The model table in `BOARD.md`: keep one row per model key in `MODELS`, plus the `-or-` pairs the plan will use.
4. Create `plan/tasks/` and put `templates/TASK.md` there as `plan/tasks/TEMPLATE.md` with its placeholders filled (the board tool ignores it because it has no `Txx-` prefix).
5. Append the sections of `templates/CLAUDE-section.md`, placeholders filled, to the project's `CLAUDE.md`. If the project already has conflicting rules about commits or branching, the board protocol wins for task work; say so in the file.
6. Add `<project>.worktrees/` to nothing: it lives beside the repo, not inside it. Make sure environments and caches are gitignored so worktrees start clean. Add `__pycache__/` and `/plan/board.html` to `.gitignore`: `start.py` and `finish.py` import `board.py` (they also set `sys.dont_write_bytecode`, but keep both), and the board tool regenerates that page on every command (and `python plan/board.py watch` keeps it fresh while cards change) so the owner watches the board in a browser tab instead of re-opening `BOARD.md`; it must never make MAIN dirty.
7. Mark generated or bulky tracked files (`-diff`, and `merge=binary` where a line merge is meaningless) in `.gitattributes` so `git diff`/`git show` never print them; keep the orientation files (CLAUDE.md, spec, INVOKE.md) short, since every agent reads them on every turn, and have agents read only the spec sections their card names.
8. Keep the owner's original brief in the repo (`docs/brief-<iteration>.md`) and make every re-plan card read it, so the intent survives the research phase. Untracked scratch files at the repo root make MAIN dirty and stop the board tool; commit them or list them in `.git/info/exclude`.
9. Concurrent agents share one machine, hence one IP and one quota for every external API (GitHub: 60 requests/hour unauthenticated, per IP). A tooling card that wraps such an API must specify: a cache shared across worktrees and consulted before every fetch, no retry on 4xx or rate-limit responses (fail fast with the reset time), the remaining quota printed on every call, and the credential (`GITHUB_TOKEN` or equivalent) as a user environment variable the owner sets once, never a file in the repo. Sessions started before the variable exists do not inherit it; the invoker prompt tells research cards to check the printed quota before fetching. Learned the hard way: four research agents exhausted the quota within minutes and routed around it with worse fetches.
10. Commit on MAIN: `Plan: conventions, task board, invoker prompt`. Then continue with Mode B.

Optional: a project-local skill or slash command whose whole body is "read `plan/INVOKE.md` and follow it step by step" lets other tools (a local model, another agent runner) invoke the same protocol. Keep it a pointer; the protocol lives in `plan/INVOKE.md` only.

## Mode B: plan or re-plan tasks

Use when asked to plan, split, add, re-scope or re-tag work. Read `reference/planning.md` in full first; it holds the decomposition rules, the model-allocation heuristic and the owner operations.

Short form:

1. Settle the spec first. Grill the owner if the `grilling` skill is available; record decisions in the spec file with dates. Do not write cards for undecided scope.
2. Write cards from `plan/tasks/TEMPLATE.md`: one context's worth of work each, disjoint files, true prerequisites only, numbered spec steps, an offline acceptance test, a "What exists" section when the card builds on done work.
3. Tag each card with the cheapest model that can do it from the card alone. Never pair the most expensive model with a cheaper one in an `-or-` tag. When you downgrade the tag, sharpen the spec.
4. Commit the cards on MAIN (`Plan: add Txx, <title>` or one `Plan:` commit for a batch), then `python plan/board.py add Txx "<title>" <phase> <tag> "<prereqs>" [--after Tyy]` for each, in the order agents should claim them. Board order is claim order.
5. Report: the new rows, the `Counts:` line, which model to launch first and what it will claim. Point the owner at `plan/board.html` (open it in a browser; run `python plan/board.py watch` in a spare terminal for live updates).

Planning sessions with the owner present are the one case where files are committed directly on MAIN, because no board row exists yet for the work. Never touch an `in-progress` or `blocked` card.

## Mode C: invoke, execute one task

Use when asked to invoke, claim, run or continue the next task.

1. From the repository root, read `plan/INVOKE.md` in full and follow it exactly, from orientation to the final report. In practice that is `python plan/start.py <model>`, the work, then `python plan/finish.py Txx`. Do not reimplement either script's steps by hand: each hand-run git command is another turn billed at the full context, which is the cost this design exists to avoid. Do not start a second task.
2. If `plan/INVOKE.md` is missing, say so and stop; do not improvise a protocol from this skill. Mode A creates it.
3. If the board tool refuses or reports "No valid task available", print its output verbatim and stop. Doing no task is a correct result.
4. Do not act as an orchestrator: never spawn agents to run cards and poll them. A coordinating session pays its own full context on every turn *on top of* every worker's, and writes no code — the one measured burned 165M cached tokens over 618 turns to spawn, poll and message. The owner launches each card, or a plain shell script does (`claude -p "$(cat plan/INVOKE.md)"` in a loop); the board, not a model, is what keeps concurrent agents in line.

## Mode D: owner operations

Only when the owner asks, in the owner's own session: reset a held row, answer a blocked card's questions and release it, reorder or re-tag `todo` rows, remove a stale worktree. Procedures are in section 5 of `reference/planning.md`. Every hand edit to the board is its own `Board: ...` commit on MAIN.
