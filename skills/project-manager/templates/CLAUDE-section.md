## How work is organized

- `{{SPEC_FILE}}` is the authoritative spec. Where any other document conflicts with it, `{{SPEC_FILE}}` wins.
- Work is split into tasks in `plan/tasks/Txx-*.md`. The board `plan/BOARD.md` tracks status and prerequisites.
- The single entry prompt is `plan/INVOKE.md`. {{OWNER}} pastes it into a fresh agent context (or invokes the `project-manager` skill with "invoke"); the agent claims the next valid task, executes it, commits, and stops. `plan/INVOKE.md` stays the single source of truth; if the protocol changes, edit it there only.
- One task per context. Do not start a second task after finishing one.
- Never edit a task file other than the one you claimed, except to fill "Questions for {{OWNER}}" in your own task or to mark the board.
- Do not write application code outside the files your task lists. If the task needs a file it did not list, add it to the task's "Files" section with a one-line reason.

## Board protocol

Several agents run at the same time on this machine. The repository root (MAIN) is shared and must stay clean; each task is executed in a private git worktree and merged back. The exact command sequence is in `plan/INVOKE.md`; the rules are:

1. The only edits ever made directly in MAIN are single-row changes to `plan/BOARD.md`, each committed immediately on its own (`Txx: claim`, `Txx: done`, `Txx: blocked`), and they are made only by `python plan/board.py`, never by hand. Never leave uncommitted changes in MAIN.
2. Claim and set up in one command: `python plan/start.py <model>` claims the next valid row through `board.py`, creates the worktree `../{{PROJECT}}.worktrees/Txx` on branch `task/Txx` with its own environment{{SHARED_CACHE_CLAUSE}}, and prints the card; `--resume Txx` re-attaches to a card an earlier context checkpointed. It claims only a `todo` row with all prerequisites `done` and a tag you may claim, and refuses if a worktree or branch for the task exists. An `in-progress` or `blocked` row belongs to the agent named in it, whatever its date; agents never decide a row is abandoned. Only {{OWNER}} resets rows, by hand, in a `Board: reset ...` commit. If the tool refuses or finds nothing, stop: doing no task is the correct result.
3. Staging is by explicit path, never `git add -A` or `git add .`, and `plan/BOARD.md` is never touched on a branch. `plan/finish.py` stages exactly the card's "Files" section and refuses when anything else changed, so a file you had to touch belongs in that section with a one-line reason (or behind `--add`).
4. Finish in one command once the acceptance test passes and "Result" is filled: `python plan/finish.py Txx --coauthor "<your Co-Authored-By line>"` commits, merges `--no-ff`, decides whether main moved under you, re-verifies, marks the row `done` and removes the worktree and branch. `--checkpoint` parks the work and keeps the row; `--blocked "<reason>"` commits and blocks.
4a. A turn is billed the whole context, so turns are the bill: cost is roughly `turns x average context`, which grows with the square of the turns. Keep to the card's context budget, then `python plan/finish.py Txx --checkpoint`, record where you stopped in the card's Result, and end the context; `python plan/start.py --resume Txx` continues at a fraction of the price. Waiting is free, so never poll or de-parallelise to keep a cache warm.
5. Questions gate the merge: a card with an empty "Questions for {{OWNER}}" merges as soon as its acceptance test passes; a card with questions (at most four) is committed on its branch, its row is set `waiting` (`python plan/board.py waiting Txx "<n> questions"`, visible on the board) and the agent asks {{OWNER}} in the same context, then runs `python plan/board.py answered Txx`, applies the answers (fixes, extra cards) and only then merges; if {{OWNER}} says to finish without answering, assumptions are kept and noted. Only a card that cannot be finished at all goes `blocked` (`python plan/board.py blocked Txx "<reason>"`, worktree left in place).
6. Model assignment: every task carries one tag, a model key or several joined with `-or-`. A model may claim a row only when its key is one of the parts. Never claim a task tagged for another model, even if you could do it. {{OWNER}} decides which model to launch; the tag decides what it may claim. Reasoning effort is chosen the same way, but it is fixed when the process starts and cannot be changed from inside a session, so it belongs to the launch: `python plan/launch.py [model]` prints the `claude --model <m> --effort <e>` line for the next claimable card and `--run` starts it, taking the level from `--effort`, else `${{EFFORT_ENV}}`, else the card's own `Effort:` header, else `board.EFFORTS[model]`. Err upward on anything diagnostic: a card that under-reasons and needs a second pass costs far more than the thinking it saved, because rework is turns.
7. Tasks own disjoint files by design. If your task needs to touch a file another task owns, stop and write a Question for {{OWNER}} instead of editing it; that is how merge conflicts are avoided.

## Commit format

`Txx: <short title>` for task commits; `Plan: <what>` for planning commits (cards, spec); `Board: <what>` for hand edits to the board by {{OWNER}}. Claude Code adds its `Co-Authored-By` trailer; a local model uses its own name in the same trailer.

## When something is unclear

Do not guess on a domain question. Write the question under "Questions for {{OWNER}}" in the task file, choose the most conservative assumption, state it in the code as a comment, and continue if the task can still be finished. At most four questions per card, one sentence each. Set the task to `blocked` only if it cannot be finished.
