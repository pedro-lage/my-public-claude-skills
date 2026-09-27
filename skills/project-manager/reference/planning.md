# Planning: from a spec to a board of claimable cards

This is the planning half of the project-manager skill. Read it when asked to plan, split, re-plan or add tasks. The executing half is `plan/INVOKE.md` in the project.

## 1. Settle the spec before writing cards

- The spec file (`decisions.md` by convention) is authoritative. The original prompt or brief is intent and loses where the two conflict. Say so at the top of the spec.
- Reach shared understanding first. If the `grilling` skill is available, grill the owner in rounds; the owner may answer in files rather than in chat. Record every decision in the spec as a numbered section with the date, and mark later additions with the grill round they came from ("section 14, grill rounds 4 to 6, 2026-09-07").
- A grill round that changes scope ends with one `Plan:` commit that updates the spec, adds or rewrites the affected cards, and re-points prerequisites. Card rewrites are allowed only for `todo` cards; a `done` card is history.
- Keep the owner's original brief in the repo (`docs/brief-<iteration>.md`) and have the re-plan/checkpoint cards read it; the spec refines the brief, it does not replace the intent behind it.
- Record the delivery order as phases with a checkpoint at the end of each: a notebook, a screen or a report the owner opens and plays with. Checkpoints are cards too. Nothing in phase n+1 lists only phase n-1 prerequisites by accident; the checkpoint card is what gates the phase.

## 2. Decompose into cards

A card is what one agent finishes in one context with no memory of other contexts. Practical size: one coherent module plus its tests, or one research deliverable. If a card needs more than one sitting it is two cards.

Rules that make parallel execution safe:

- **Disjoint files.** Every card lists every file it may create or modify. Two cards that are not both `done` never list the same file. When two cards genuinely need one file (a `settings.yaml`, a shared `__init__.py`), split the file by section and give each section to one card, or make one card the prerequisite of the other. A later card may edit a file an earlier, `done` card created; ownership is about concurrency, not forever.
- **True prerequisites only.** A prerequisite is a card whose output this card imports, reads or extends. "Nice to do after" is not a prerequisite; it constrains the schedule for nothing. Aim for a wide graph: many cards claimable at once early in each phase.
- **Board order is claim order.** `board.py next` takes the first claimable row, and `claim` refuses to skip ahead. Put critical-path cards before cards that can wait, and use `add --after Tyy` to slot a new urgent card behind its last prerequisite instead of at the end.
- **Offline acceptance test.** Every card has a command that passes without network access, against fixtures the card also creates. Network tests are marked and skipped by default. The agent runs the acceptance test in the worktree and again in MAIN after the merge; anything not covered by it is not verified.
- **Numbered spec steps.** Write the Spec section as numbered steps with exact names, signatures, defaults, formats and edge cases. An agent in a fresh context has no way to ask; every ambiguity becomes either a question or a guess.
- **"What exists" for follow-on cards.** When a card builds on done work, name the functions, tables and files it will use, their current signatures and what they lack. Refer to symbols, never to line numbers (they drift as files grow; symbols are grepped). This section is what makes the card executable by a cheaper model.
- **Budget the expensive checks.** When verification costs more than a test run (renders, screenshots, long simulations), the card states a budget ("three sheets while iterating, one full look at the end") and names the numeric check that comes first. Agents without a budget iterate visually until the context is gone.
- **Research cards produce documents.** Tax rules, strategy definitions, data-source surveys: the deliverable is a file under `docs/` plus, when applicable, a machine-readable rules file. Implementation cards then reference those files as spec.
- **Provenance.** A card added later carries "Owner's request, <date>: ..." so an agent knows why it exists and can tell intent from accident.

Use `templates/TASK.md` for the card layout. Keep the header block exactly: it is what agents check before starting.

## 3. Allocate models: cheapest capable model per card

Every card carries one tag. Tags are model keys (`fable`, `opus`, `sonnet`, `local`) or several joined with `-or-`. `haiku` is not offered: it cannot run in auto mode, so it cannot execute a card unattended; add it to `MODELS` only when the owner explicitly asks for it. The owner launches a model; the tag decides what it may claim. The board's `Counts:` line shows the split at a glance.

Heuristic, from most to least expensive:

| Give to | Cards where |
|---|---|
| The most capable model only (`fable`) | Judgment cannot be specified away: research that produces the spec (tax rules, strategy definition), the core algorithm whose objective is underdetermined (optimizer objective), anything where a subtly wrong answer is expensive and no test would catch it. |
| The strong general model (`opus`) | Integration across many modules (a backtest engine over ledger, taxes and costs), numerically delicate code (estimators, shrinkage), long specs that are precise but dense, cards whose acceptance test is only meaningful if the design is right. |
| Either a mid model or the local model (`opus-or-local`, `sonnet-or-local`) | Adapters with a fixed interface, CLIs, notebooks, screens with a precise spec, fixtures, docs, glue between done modules. Whichever is free takes it. |
| The local model only (`local`), or `sonnet` when there is none | Skeleton, config plumbing, one-source adapters with a sample response in the fixtures, mechanical refactors with a test that already exists. |

Rules:

- Never pair the most expensive model with a cheaper one in an `-or-` tag. Expensive budget goes only where required; the owner runs the expensive model until it has nothing claimable, then switches down.
- Cheaper models fail on ambiguity, not on volume. When you downgrade a card's tag, upgrade its spec: exact signatures, exact acceptance command, a "What exists" section, and the conservative default for every choice the agent might otherwise make.
- If a downgraded card comes back blocked with questions that a precise spec would have answered, fix the card, not the tag.
- Checkpoint reviews read `git diff --stat` and the diffs of source paths only; generated assets and data are marked `-diff` in `.gitattributes` so no agent can pull them into its context by accident.
- Checkpoint cards go to the strong general model (`opus`), or `fable` where the checkpoint is itself a re-plan. Besides the deliverable the owner reviews, a checkpoint reviews the phase's merges for architectural fit and compatibility and opens at most three fix cards (cheapest capable tag, slotted before the next phase); a fix card never re-opens a delivered card's scope, and a phase of five cards must not produce six fixes.
- Re-tag only `todo` cards, by hand, in a `Board: retag ...` commit by the owner.

## 4. Put the cards on the board

1. Write the card file `plan/tasks/Txx-<slug>.md`. Ids are two or three digits, allocated once and never reused.
2. Commit the card on MAIN: `Plan: add Txx, <title>` (a planning session with the owner is the one case where planning files are committed directly on MAIN; there is no board row yet, so no agent can be working on it).
3. Add the row: `python plan/board.py add Txx "<title>" <phase> <tag> "<prereqs>" [--after Tyy]`. The tool refuses an id without a card file, an unknown prerequisite or an invalid tag, and commits `Board: add Txx` on its own.
4. Re-point prerequisites of existing `todo` rows only when the new card must run before them; that is a hand edit by the owner, in the same session, committed as `Board: re-point Tyy, Tzz at Txx`.

Initial planning of a new project is the same loop for every card, then one `Plan: conventions, task board, N task files, invoker prompt` commit.

## 5. Owner operations (never done by an executing agent)

- **Reset a held row.** A crashed agent leaves `in-progress`. The owner checks the worktree, merges or discards the branch, removes the worktree and branch, and edits the row back to `todo` in a `Board: reset Txx to todo` commit. Agents never do this, whatever the date on the row.
- **Unblock.** Answer the "Questions for the owner" in the card file; commit `Txx: answers`. The blocked branch and worktree are still there. Either merge the partial work (`git merge --no-ff task/Txx`) or discard it (`git branch -D task/Txx`), remove the worktree, then reset the row to `todo`. The next claim starts from the answered card.
- **Reprioritize.** Move rows or change prerequisites by hand, `Board: reorder ...`. Only `todo` rows move.
- **Switch models.** When `board.py next <expensive>` reports nothing claimable, launch the next model down. `board.py show` lists what each model could claim right now; `plan/board.html` (kept fresh by `board.py watch`) shows the same plus cards waiting on answers.

## 6. Signs the plan needs work

- Two agents conflicted on merge: a shared file was not split. Split it and add the prerequisite.
- A card came back with more than three questions: the spec was not settled. Grill, update the spec, rewrite the card. Cards are capped at four questions; questions gate the merge (the agent asks the owner before merging, see `templates/INVOKE.md` 4b).
- The expensive model has claimable cards while cheap ones idle: tags are too generous, or the graph is too narrow. Re-tag or add prerequisites-free cards.
- Cards keep being added at the end and never claimed: the board order is wrong. Use `--after`.
