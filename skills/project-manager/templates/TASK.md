# Txx: <title, same as the board row>

Kind: <build|explore>. Model: <tag>. Effort: <low|medium|high|xhigh|max; omit to take the default for the model>. Phase: <n>. Prerequisites: <Tyy, Tzz or none>. {{SHARED_FLAG}}: <yes|no>. Tests: <tier list, when the project has a tier script; omit to let the agent derive it from the changed paths>.
Ownership: claim only with `python plan/board.py claim Txx <model>`. If the board shows Txx as `in-progress`, `waiting` or `blocked`, it belongs to the agent named there, whatever the date; do not start it.
Spec references: <`{{SPEC_FILE}}` sections this card implements; files from done tasks this card builds on, each with the task id>.

<Optional one-paragraph provenance: "{{OWNER}}'s request, <date>: ...". A tag naming the most expensive model also
needs its own line here: `Fable: [{{OWNER}} <date>] <why>` (`board.py lint` refuses the tag without it).>

<Kind: `build` when what to build is known (tooling, UI, data, a fix whose cause was measured); `explore` when the
cause or the answer is not known yet (tuning, feel, fit, visual quality, a slowdown nobody profiled). Numbers: every
threshold in Goal, Spec and Acceptance is followed by its source, `[{{OWNER}} <date>]`, `[{{SPEC_FILE}} §x]`,
`[measured Txx]` or `[planner]`; only the first three may gate, a `[planner]` number is reported before and after. A
number you cannot source is `[planner]`, or better left out. Run `python plan/board.py lint Txx` before `board.py add`.>

## Goal

<One paragraph. What exists when this card is done and why the project needs it. Written so a reader who has not seen the rest of the plan understands the card. Explore: the goal is what {{OWNER}} sees, in their terms ("the export no longer drops the last row when the file has no trailing newline"), not a metric.>

## What exists

<Only for cards that build on done work. Name the functions, tables and files the agent will use, with their current signatures, and what they lack. Refer to symbols, never to line numbers: lines drift, symbols are grepped. State a cause only when it was measured; a suspected cause ("likely", "check whether") makes the card an explore card at high effort, and the lint refuses it on a build card at medium or low effort.>

## Files

- `<path>` (new)
- `<path>` (<Tyy>, done: <what changes>)
- `tests/<test file>`

<Every file the card may create or modify. No other task on the board may list the same file while both are not done. The agent may add a file only with a one-line reason here; `finish.py` stages exactly this list.>

## Spec

Build:

1. **<Step>.** <The behaviour the result must have: names, signatures, formats, defaults, edge cases that others depend on, each threshold with its source. How to implement it is a suggestion, labelled as one; the executor may take a better path and says why in the Result.>
2. **<Step>.** ...
3. **Tests** (`tests/<file>`, offline, fixtures under `tests/fixtures/`): <one clause per test>. Every test of <Tyy, Tzz> passes unchanged.

Explore:

1. **Diagnose.** <What to measure and look at on the actual defect before choosing anything: the dump, the probe, the one view that shows it. The diagnosis goes into the Result first.>
2. **Ideas, not instructions.** <Approaches the planner thinks may work, and what was already tried and rejected (with the card and the numbers). The executor picks from the diagnosis, or does something else, and says why.>
3. **Keep.** <What must not break: tests, invariants, {{OWNER}}'s own constraints with their source.>

## Acceptance test

```
git merge main                       (in the worktree, right before this run: it tests the merged state)
{{TEST_CMD}} tests/<file> <other test files this card must keep green>
```

Render budget: <for cards with a visible result: how many cheap looks while iterating and one full look at the end; "none" otherwise>. Numbers while iterating: <the statistic, test or dump checked between looks; it never closes the card>.
Defect view: <where the defect (or the result) shows: the screen, input, camera or command. The agent's A capture in its Reproduce block and its B at the end. Required when the Render budget is not "none".>

<One sentence per manual check, when the card has a visible result. The look decides: the card is done when B,
judged at the Defect view against A and against "How {{OWNER}} checks this", shows the requirement met. A number
the agent defined itself never closes a card.>

<Explore: the judgement is an A/B, today's build against the card's, same inputs and view, at the defect; the card
is done when the defect is gone or clearly better in that A/B, with the numbers reported before and after. If the
diagnosis shows the goal cannot be reached this way, that is a question for {{OWNER}}, not a reason to chase a number.>

Budget: <how many runs of the project's expensive operation this card gets before the agent checkpoints and
continues in a fresh context; omit to take the default in `plan/INVOKE.md`.>

## How {{OWNER}} checks this

<Two or three sentences: what to run or open and what a correct result looks like, in the owner's terms rather than the code's. This is the acceptance; write it as the behaviour {{OWNER}} will check, from the angle that shows the defect.>

## Questions for {{OWNER}}

<Numbered, at most four, one sentence each with the conservative assumption taken so work could continue. Left empty by the planner if there is nothing to ask; filled by the executing agent as questions arise. Questions gate the merge (`plan/INVOKE.md` §4).>

## Result

### Reproduce
A: <captures taken on today's build at the Defect view, before the first edit>
Requirement: <one sentence, in {{OWNER}}'s terms, of what must be true when the card is done>
Gate: <the card's number or test, and whether it is bad on A; if it is not, it cannot gate: name what does>
Assumed: <each cause or number the card hands you, with "measured: ..." or "untested">
B: <the same views at the end, on this card's build>

(filled by the executing agent)
