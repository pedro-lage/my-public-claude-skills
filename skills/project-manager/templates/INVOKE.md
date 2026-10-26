# Invoke: execute the next valid task

You are an agent on the {{PROJECT}} repository. Other agents may be working at the same time.
Execute exactly one task, then stop.

MAIN is the directory containing `CLAUDE.md`, where this prompt was pasted. Run every command from MAIN unless a
step says otherwise.

## 0. Rules no reasoning overrides

- A row is claimable only when its status is exactly `todo`. `in-progress`, `waiting` and `blocked` rows belong to
  the agent named in them. The date is informational: a row never becomes free because it looks old, idle or
  crashed. Only {{OWNER}} releases a row, in a `Board: reset ...` commit.
- Never hand-edit `plan/BOARD.md`. Every status change goes through `plan/board.py`, directly or through
  `start.py`/`finish.py`. They refuse what the rules refuse and have no override flag: do not work around a refusal.
- If nothing is claimable, printing the report and stopping is the correct outcome of this context. Doing no task
  is a valid result; taking a held or unready one never is.

## 1. Orient

Do **not** read `plan/BOARD.md`, `README.md` or `{{SPEC_FILE}}` up front: the card carries what the task needs, and
`start.py` prints the card. Read a `{{SPEC_FILE}}` section only when your card's "Spec references" line names it,
and only that section.

Identify your model key from the system prompt: the key whose `MODELS` name in `plan/board.py` matches your model
most exactly. You may claim a row when your key is one of the parts of its tag split on `-or-`. If {{OWNER}}'s
message names a model, that wins.

## 2. Start

```
python plan/start.py <model>
```

It claims the next valid task, creates `../{{PROJECT}}.worktrees/Txx` on branch `task/Txx` with its own
environment, and prints the card. There is nothing else to read before working. To resume a card you checkpointed
earlier: `python plan/start.py --resume Txx`. If it prints "No valid task available", print that and stop.

Then `cd` into the worktree. Everything until step 5 happens there.

## 3. Work

**Reproduce and restate, before the first edit.** The card is not the text on it; it is the requirement that text
was written for. So the first block of your Result is `### Reproduce`, written before you change anything:

```
### Reproduce
A: <captures taken now on today's build at the card's Defect view, where the defect shows>
Requirement: <one sentence, in {{OWNER}}'s terms, of what must be true when the card is done>
Gate: <the card's number or test, and whether it is bad on A; if it is not, it cannot gate: name what does>
Assumed: <each cause or number the card hands you, with "measured: ..." or "untested">
B: <the same views at the end, on your build; the Result's claims rest on them>
```

Look at A before you read the card's diagnosis as true: a card's "likely cause" is a hypothesis, and the cheapest
move on the whole card is one probe that tests it. If the requirement in your sentence and the card's acceptance
disagree, the requirement wins: say so in the Result and test against it. A card without a visible result
(tooling, data, docs) fills only `Requirement`, `Gate` and `Assumed`. `finish.py` refuses a card with a Defect view
whose A and B captures do not exist.

Do exactly what the card says: only the files it lists, the tests it names. Add a file only by adding it to the
card's "Files" section with a one-line reason. Find code by symbol, never by line number. Never run a
whole-repository diff or log: name a path, so generated or vendored files cannot flood the context.

**The card's goal binds you; its numbers and methods only as far as their source does.** A threshold tagged
`[{{OWNER}} ...]`, `[{{SPEC_FILE}} §x]` or `[measured Txx]` is a gate. One tagged `[planner]`, or untagged, is a
starting value: report it before and after, do not spend contexts chasing it. **How to do it is never a rule**: a
method in a card, even one {{OWNER}} suggested, is an idea; take another path whenever you find a better one, and
say why in the Result. Only a method recorded as a decision in `{{SPEC_FILE}}` binds. When the measurement or the
look contradicts a planner number, deviate and document (old value, new value, the measurement); do not stop to
ask. Ask only when the goal itself is in doubt. On a `Kind: explore` card diagnose first: the diagnosis goes in the
Result before you choose a method. **The look decides, numbers are for iterating**: the acceptance is B against A at
the Defect view and against "How {{OWNER}} checks this", never a statistic you defined yourself.

**Iterate cheap, verify once.**

{{ITERATION_TIPS}}

- Green means no NEW failures: a failure that also fails on main without your change is not yours; record it where
  the project keeps known failures and file a `tests` suggestion, do not spend turns on it.
- Instrument before tuning: make a failing check print the number in the same run. A tuning loop on a yes/no check
  costs a full run per guess.

**Budget this context.** {{BUDGET}}, or when the context hook says ~175k tokens, whichever comes first; not when the
card will finish within ~15 requests. Overwrite the `### Handoff` block at the top of "Result", run
`python plan/finish.py Txx --checkpoint` and end the turn. The block is all the next context (maybe another model)
sees of your Result: five lines that stand alone, with a `Done when:` line (`finish.py` prints the format when the
block is missing). When the card is finished, the Result says what was built and the Handoff block is deleted.

## 4. Questions

If you hit a decision the card and `{{SPEC_FILE}}` do not cover: write it under "Questions for {{OWNER}}" in the
card, take the most conservative assumption, note it in a code comment, and continue. At most four questions per
card, one sentence each, each with its assumption; merge related doubts.

Questions gate the merge (`finish.py` prints the `board.py waiting` step); print the questions with their
assumptions, and end your turn. Keep every question self-contained: in an unattended run a fresh context reads the
answer. When {{OWNER}} answers in this context, `python plan/board.py answered Txx` prints the next steps. If
{{OWNER}} says to finish without answering, mark each "unanswered, assumption kept", do only what is strictly
needed, and finish. A stand-in's answer never creates a card or a gate: if it seems to, keep the conservative
reading and name the follow-up in the Result. If {{OWNER}}'s own answer implies work beyond this card, write a new
card `plan/tasks/Tyy-<slug>.md` (from `plan/tasks/TEMPLATE.md`; provenance: "{{OWNER}}'s answer to Txx question n,
<date>") on your branch and add its row from MAIN after the merge with
`python plan/board.py add Tyy "<title>" <phase> <tag> "<prereqs>" --after Txx`.

A card that cannot be finished at all: `python plan/finish.py Txx --blocked "<one-line reason>"`, which commits
what exists and leaves the worktree in place. Then stop.

## 5. Finish

**Test the merged state, once.** Right before your final acceptance run, in your worktree: `git merge main`
(resolve any conflict keeping both cards' intent and commit `Txx: merge main (<what and how>)`). Do not merge main
earlier than that while iterating.

Fill the card's "Result" section (what was built, what the acceptance test printed, any deviations). If you saw a
problem in the code, even outside your files, or something in the tools or rules cost you real turns, file it (at
most three per card, each with a concrete fix; `+1` an existing one with `--also Sxx`), as `plan/SUGGESTIONS.md`
explains. Then:

```
python plan/finish.py Txx --coauthor "<the Co-Authored-By line from your system prompt>"
```

It stages exactly the card's "Files" (plus the card), commits, merges `--no-ff`, marks the row `done` and removes
the worktree and branch. A merge conflict, the main lock held by another card, a leftover path or **MOVED** (main
changed what your tests depend on): `finish.py` prints the steps each time; follow them, never edit or merge in
MAIN by hand, do not end your turn.

## 6. Report and stop

At most ten lines: task id, merge hash, what exists now, how {{OWNER}} checks it, open questions. Then stop. Do not
start another task.
