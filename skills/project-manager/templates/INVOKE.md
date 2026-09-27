# Invoke: execute the next valid task

You are an agent on the {{PROJECT}} repository. Other agents may be working at the same time.
Execute exactly one task, then stop.

MAIN is the directory containing `CLAUDE.md`, where this prompt was pasted. Run every command from MAIN unless a
step says otherwise.

## 0. Rules no reasoning overrides

- A row is claimable only when its status is exactly `todo`. `in-progress`, `waiting` and `blocked` rows belong to
  the agent named in them. The date is informational: a row never becomes free because it looks old, idle or
  crashed. Only {{OWNER}} releases a row, in a `Board: reset ...` commit.
- Never hand-edit `plan/BOARD.md`. Every status change goes through `plan/board.py`, directly or through the two
  scripts below. They refuse what the rules refuse and have no override flag. A refusal is correct: do not work
  around it, do not edit the board, do not start the task anyway.
- If nothing is claimable, printing the report and stopping is the correct outcome of this context. Doing no task
  is a valid result; taking a held or unready one never is.

## 1. Orient

Read `CLAUDE.md` (conventions and the token rules, which bind you). Do **not** read `plan/BOARD.md`, `README.md` or
`{{SPEC_FILE}}` up front: the card carries what the task needs, and `start.py` prints the card. Read a `{{SPEC_FILE}}`
section only when your card's "Spec references" line names it, and only that section.

Identify your model key from the system prompt and check it against the card's Model tag: you may claim a row
when your key is one of the parts of the tag split on `-or-`. Never claim a card tagged for another model; the
most expensive model never claims a card tagged for a cheaper one. If {{OWNER}}'s message names a model, that wins.

## 2. Start

```
python plan/start.py <model>
```

One command for the whole setup: it picks the next valid task, claims it through `board.py`, creates
`../{{PROJECT}}.worktrees/Txx` on branch `task/Txx`, prepares the worktree's environment{{SHARED_CACHE_CLAUSE}},
and prints the card. There is nothing else to read before working. To resume a card you checkpointed earlier:
`python plan/start.py --resume Txx`.

If it prints "No valid task available", print that report and stop.

Then `cd` into the worktree. Everything until step 5 happens there.

## 3. Work

Do exactly what the card says: only the files it lists, the tests it names, the acceptance criteria it sets. Add a
file only by adding it to the card's "Files" section with a one-line reason. Find code by symbol (`Grep` for the
function), never by line number, which drifts. Never run a whole-repository diff or log: name a path, so that
generated or vendored files cannot flood the context.

**Iterate cheap, verify once.** Run the narrow form while iterating and keep the card's full acceptance
command for the final run:

{{ITERATION_TIPS}}

**Budget this context.** {{BUDGET}}. Every turn is billed
the whole context, so a card finished across two fresh contexts costs far less than one long context. When you hit
the budget, or the harness warns the context is filling: write where you stopped into the card's "Result" section,
run `python plan/finish.py Txx --checkpoint`, print the resume command and end the turn. Stopping early is cheap;
grinding on at 300k of context is not.

## 4. Questions

If you hit a decision the card and `{{SPEC_FILE}}` do not cover: write it under "Questions for {{OWNER}}" in the card,
take the most conservative assumption, note it in a code comment, and continue. At most four questions per card,
one sentence each, each with its assumption; merge related doubts.

Questions gate the merge. `finish.py` refuses to merge a card with questions while the row is `in-progress`; run
`python plan/board.py waiting Txx "<n> questions"`, print the questions with their assumptions, and end your turn.
{{OWNER}} answers in this same context. Then run `python plan/board.py answered Txx` (the row returns to
`in-progress`), record the answers in the card, make the fixes, re-run the acceptance test, and finish. If {{OWNER}} says to finish without answering, mark each "unanswered, assumption kept", do only what
is strictly needed, and finish. If an answer implies work beyond this card, write a new card
`plan/tasks/Tyy-<slug>.md` on your branch (provenance: "{{OWNER}}'s answer to Txx question n, <date>") and add its row
from MAIN after the merge with `python plan/board.py add Tyy "<title>" <phase> <tag> "<prereqs>" --after Txx`.

A card that cannot be finished at all: `python plan/finish.py Txx --blocked "<one-line reason>"`, which commits
what exists and leaves the worktree in place. Then stop.

## 5. Finish

Fill the card's "Result" section (what was built, what the acceptance test printed, any deviations), then:

```
python plan/finish.py Txx --coauthor "<the Co-Authored-By line from your system prompt>"
```

One command for the whole tail: it stages exactly the paths in the card's "Files" section (plus the card), refuses
if anything else changed, commits `Txx: <title>`, merges into MAIN with `--no-ff`, decides whether main moved under
you, re-verifies, marks the row `done` and removes the worktree, shared links and branch.

Two things it may ask of you:

- **a leftover path** it will not stage silently — add it to the card's "Files" section with a reason, or pass
  `--add <path>`;
- **MOVED** — other merges landed on main while you worked, so run the card's full acceptance test in MAIN and then
  `python plan/finish.py Txx --verified <merge hash>`.

## 6. Report and stop

At most ten lines: task id, merge hash, what exists now, how {{OWNER}} checks it, open questions. Then stop. Do not
start another task.
