# Txx: <title, same as the board row>

Model: <tag>. Effort: <low|medium|high|xhigh|max, omit to take the default for the model>. Phase: <n>. Prerequisites: <Tyy, Tzz or none>.
Ownership: claim only with `python plan/board.py claim Txx <model>`. If the board shows Txx as `in-progress` or `blocked`, it belongs to the agent named there, whatever the date; do not start it.
Spec references: <`{{SPEC_FILE}}` sections this card implements; files from done tasks this card builds on, each with the task id>.

<Optional one-paragraph provenance: "{{OWNER}}'s request, <date>: ...">

## Goal

<One paragraph. What exists when this card is done and why the project needs it. Written so a reader who has not seen the rest of the plan understands the card.>

## What exists

<Only for cards that build on done work. Name the functions, tables and files the agent will use, with their current signatures, and what they lack. Refer to symbols, never to line numbers. This section is what lets a cheaper model do the card without exploring.>

## Files

- `<path>` (new)
- `<path>` (<Tyy>, done: <what changes>)
- `tests/<test file>`

<Every file the card may create or modify. No other task on the board may list the same file while both are not done. The agent may add a file only with a one-line reason here.>

## Spec

1. **<Step>.** <Exact behavior: signatures, types, defaults, edge cases, error handling. Numbers, names and formats spelled out.>
2. **<Step>.** ...
3. **Tests** (`tests/<file>`, offline, fixtures under `tests/fixtures/`): <one clause per test>. Every test of <Tyy, Tzz> passes unchanged.

## Acceptance test

```
{{TEST_CMD}} tests/<file> <other test files this card must keep green>
```

<One sentence per manual check, when the card has a visible result.>

Budget: <how many runs of the project's expensive operation this card gets before the executing agent
checkpoints and continues in a fresh context; omit to take the default in `plan/INVOKE.md`.>

## How {{OWNER}} checks this

<Two or three sentences: what to run or open and what a correct result looks like, in the owner's terms rather than the code's.>

## Questions for {{OWNER}}

<Numbered, at most four, one sentence each with the conservative assumption taken so work could continue; questions gate the merge (INVOKE.md 4b). Left empty by the planner if there is nothing to ask; filled by the executing agent as questions arise.>

## Result

(filled by the executing agent)
