## Work shape (token economy)

- **A turn is billed the whole context, so turns are the bill.** Cost is roughly `turns x average context`, which grows
  with the square of the turns. The same work in two fresh contexts costs a fraction of one long context: checkpoint
  (`plan/INVOKE.md` §3) and resume with `python plan/start.py --resume Txx`.
- Lookups are most of the bill (measured at 45-52 % of spend on one project): put several queries in one call and
  independent reads/greps in one message, not one per turn. Find code by symbol, never by line number; never read a
  big file whole; never print a whole-repository diff or log (name a path).
- Idle time is free: a 5-20 minute wait on a lock costs no re-cache (only gaps beyond the cache TTL do). Never
  de-parallelise or poll to "keep the cache warm"; block in one tool call and let it wait.
- No orchestrator agent. A coordinating session pays its own full context on every turn *in addition to* each
  worker's, and writes no code (measured: 165M cached tokens over 618 turns to spawn, poll and message).
  {{OWNER}} launches each card (`python plan/launch.py`), or the plain loop `python plan/autospawn.py` does.
  Delegating a self-contained mechanical chunk to a subagent inside one card is fine; standing coordination is not.
- **Numbers while iterating, the look decides.** A printed statistic or a test is how you iterate and a regression
  net; it is never the acceptance of a card with a visible result. That card is judged on A (today's build, before
  the first edit) against B (yours) at the card's Defect view; a number the agent defined itself cannot close a card.
- Chats with {{OWNER}} (planning, triage, board work) `/clear` between topics: a request at 300k of context costs
  five times one at 60k.

## How work is organized

- `{{SPEC_FILE}}` is the authoritative spec. Where any other document conflicts with it, `{{SPEC_FILE}}` wins.
- Work is split into tasks in `plan/tasks/Txx-*.md`. The board `plan/BOARD.md` tracks status and prerequisites.
- The single entry prompt is `plan/INVOKE.md`. {{OWNER}} launches it (`python plan/launch.py <model> --run`, or
  pastes it into a fresh context, or invokes the `project-manager` skill with "invoke"); the agent claims the next
  valid task, executes it, merges, and stops. `plan/INVOKE.md` stays the single source of truth for the protocol.
- One task per context. Do not start a second task after finishing one.
- Never edit a task file other than the one you claimed, except to fill "Questions for {{OWNER}}" in your own task.
- Do not write code outside the files your task lists. If the task needs a file it did not list, add it to the
  task's "Files" section with a one-line reason.

## Board protocol

Several agents may run at the same time on this machine. The repository root (MAIN) is shared and must stay clean;
each task is executed in a private git worktree (`../{{PROJECT}}.worktrees/Txx`) and merged back. The command
sequence is in `plan/INVOKE.md`; the rules are:

1. The only edits ever made directly in MAIN are single-row changes to `plan/BOARD.md`, each committed immediately
   on its own (`Txx: claim`, `Txx: done`, `Txx: blocked`...), made only by `python plan/board.py`, never by hand.
   The one other file written in MAIN is `plan/SUGGESTIONS.md`, the agents' friction log with proposed fixes, which
   {{OWNER}} triages; only `python plan/board.py suggest` appends to it (`Plan: suggestion Sxx`). Never leave
   uncommitted changes in MAIN: an untracked scratch file there stalls every finish that adds the same path.
2. `python plan/start.py <model>` claims only a `todo` row with all prerequisites `done` and a tag you may claim,
   in board order, through `board.py`, which refuses if a worktree or branch exists. An `in-progress`, `waiting` or
   `blocked` row belongs to the agent named in it, whatever its date; agents never decide a row is abandoned. Only
   {{OWNER}} resets rows, in a `Board: reset ...` commit. If the tool refuses or finds nothing, stop.
3. Staging is by explicit path, never `git add -A` or `git add .`, and `plan/BOARD.md` is never touched on a branch.
   `plan/finish.py` stages exactly the card's "Files" and refuses when anything else changed.
4. Finish, checkpoint, questions, blocked: `plan/INVOKE.md` §3-5 (`plan/finish.py`, `board.py waiting|answered|blocked`).
   A MOVED merge (main changed what the card's tests depend on) is re-tested in the worktree after `git merge main`,
   never in MAIN.
5. Model assignment: every task carries one tag (a model key or keys joined with `-or-`). A model may claim a row
   only when its key is one of the parts; the most expensive model never claims a card tagged for a cheaper one. A
   tag naming the most expensive model needs {{OWNER}}'s word on that card, written as a
   `Fable: [{{OWNER}} <date>] <why>` line (`board.py lint` checks it). Reasoning effort is fixed when the process
   starts, so it belongs to the launch: `plan/launch.py` takes `--effort`, else `${{ENV_PREFIX}}_EFFORT`, else the
   card's `Effort:` header, else `board.EFFORTS[model]`. A hard diagnosis gets a higher effort, not a dearer model.
6. Tasks own disjoint files by design. If your task needs to touch a file another task owns, stop and write a
   Question for {{OWNER}} instead of editing it.

## Commit format

`Txx: <short title>` for task commits; `Plan: <what>` for planning commits (cards, spec); `Board: <what>` for hand
edits to the board by {{OWNER}}. Claude Code adds its `Co-Authored-By` trailer; a local model uses its own name in
the same trailer.

## When something is unclear

Do not guess on a domain question: `plan/INVOKE.md` §4.
