# Compact the project documents

Invoked by {{OWNER}} in a fresh context ("read `plan/COMPACT.md` and compact all project documents"). It is not a
board card: do not run `plan/start.py`, do not run the project's expensive tools. Run it at a phase checkpoint, or
when a budget below is exceeded.

Why: every request re-reads what is loaded. `CLAUDE.md` rides in every request of every session and
`plan/INVOKE.md` in every card; the other documents cost by being read, written and searched. What is no longer
needed where it sits is moved or cut. Nothing that still binds an agent is lost.

## Rules

- **Move or delete, never rephrase.** Text that stays keeps its wording: rewording is where a rule changes
  meaning, and rewritten text is paid for at output price. Work with `Edit`, never by rewriting a file.
- **Every rule exists because an agent broke it.** A rule is removed only when it is a duplicate (say of
  what), names something retired (verify), or has moved (say where). "Obvious" is not a reason.
- **Keep the number, drop the story.** "535 turns, 118M tokens" stays; the paragraph telling how it happened goes.
- `{{SPEC_FILE}}` is the spec. Its section numbers are cited from code, cards and docs (`{{SPEC_FILE}} §6.3`):
  never renumber, never remove a heading, never edit a decision that still binds.
- Never touch: cards in `plan/tasks/`, `plan/BOARD.md` by hand, code, anything a row that is `in-progress`,
  `waiting` or `blocked` names in its card.
- In doubt whether a passage still binds: leave it and list it in the report (at most four questions).

## Setup

`git worktree add ../{{PROJECT}}.worktrees/compact -b compact {{MAIN_BRANCH}}`, and edit only there (MAIN is shared
and other work is in progress in it). The two `board.py` commands below run from MAIN and commit on their own.
One commit per document: `Plan: compact <file> (<before> -> <after> chars)`, the body listing what moved where and
what was dropped as what. A document inside its budget is skipped and reported as such.

Measure first, in one call: `wc -c CLAUDE.md plan/INVOKE.md plan/BOARD.md {{SPEC_FILE}} README.md plan/SUGGESTIONS.md`
(plus the project's wiki pages, if it keeps one).

## The documents

| Document | Budget | How |
| --- | --- | --- |
| `plan/BOARD.md` | open rows and their prerequisites | `python plan/board.py archive-done` (from MAIN) |
| `plan/SUGGESTIONS.md` | open entries only | `python plan/board.py archive` (from MAIN) |
| `CLAUDE.md` | 12,000 chars | below |
| `plan/INVOKE.md` | 8,000 chars | below |
| `{{SPEC_FILE}}` | no size budget | below |
| `README.md` | 8,000 chars | below |

**`CLAUDE.md` and `plan/INVOKE.md`** are always loaded, so a line stays only if most contexts need it before
they could look it up. In this order:
1. Duplicates between the two: a session-wide rule lives in `CLAUDE.md`, a step of the card protocol in
   `INVOKE.md`; the other file keeps at most a clause.
2. Retired things (a dropped model, a removed command): delete after verifying they are gone.
3. Explanation and history behind a rule: keep the rule and its number in one sentence; move the rest to the
   doc page that owns the subject, if it is not already there.
4. Procedures for a situation (merge conflict, MOVED, the questions flow): if the script prints the procedure
   when the situation occurs (check its source), keep one line naming the situation and the command.
Stop at the budget. Do not cut below it for its own sake.

**`{{SPEC_FILE}}`**: move the body of a section to `docs/decisions-archive.md` (same heading there) when it is
(a) a card list or an execution order for a phase whose cards are all `done` or archived, or (b) explicitly
superseded by a later dated section, which you name. The heading stays in place with one line:
`Archived <date>: docs/decisions-archive.md (<why>)`. Everything else stays untouched.

**`README.md`** becomes a landing page: requirements, how to run, the layout in ten lines, licences, and a
pointer to the docs index. For each section that goes, first make sure the doc page that owns the subject says
what an agent needs from it; what merely describes code that says the same is dropped. The status history goes:
git has it.

**Leftovers.** Old iteration plans, briefs, notes at the repo root: `grep` for references; report the ones nothing
points at. Do not delete them.

## Keep your own context small

One subagent per large job (`README.md` into the docs, `{{SPEC_FILE}}`), each given this file and its one
document; you stay the editor. Read `{{SPEC_FILE}}` and `README.md` by section (`grep -n "^## \|^### "` first),
never whole.

## Acceptance

1. The measuring command again: each document is inside its budget, or the report says why not.
2. Every section cited still has its heading: `grep -rhoE "{{SPEC_FILE}} §[0-9]+(\.[0-9]+)?" <code dirs> plan/tasks | sort -u`
   against `grep -n "^## \|^### " {{SPEC_FILE}}`.
3. `python plan/board.py show` and `python plan/start.py --help` still run.
4. For every passage removed from `CLAUDE.md` or `plan/INVOKE.md`, the commit body says where it lives now or
   which of the three removal reasons applies.

## Finish

In the worktree, `git merge {{MAIN_BRANCH}}` first (another session may have edited the same documents: keep both
intents), then from MAIN `git merge --no-ff compact -m "Plan: merge document compaction"`; on a conflict,
`git merge --abort` and report. Then `git worktree remove ../{{PROJECT}}.worktrees/compact` and
`git branch -d compact`. Use your Co-Authored-By line on every commit.

Report in at most fifteen lines: a before/after size for each document, what moved where, the leftovers nothing
points at, your questions. Then stop.
