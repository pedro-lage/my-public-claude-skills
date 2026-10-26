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
- **Precise only where the answer is known: `Kind: build` or `Kind: explore`.** A build card (tooling, UI, data, a fix whose cause was measured) gets the exact spec above. An explore card (the cause or the answer is unknown: tuning, physics feel, visual quality, performance whose bottleneck is unmeasured) states the goal as what the owner sees, starts with a diagnosis step, lists methods as ideas rather than instructions, and is accepted on an A/B against today's build with numbers reported before and after. Measured on one project (155 cards): 38 % of closed cards had a material spec problem against 5 % caused by the executor, almost all on undiagnosed problems specced as if they were build cards — invented thresholds that physics could not meet, prescribed methods that proved wrong, and cards whose letter was met while the result was worse.
- **Every threshold carries its source.** Right after the number: `[{{OWNER}} <date>]`, `[spec §x]`, `[measured Txx]` or `[planner]`. Only the first three may gate; a `[planner]` number is a starting value the card reports. A number you cannot source is `[planner]` or left out. Executors deviate from planner numbers and prescribed methods when measurement contradicts them, and document old value, new value and the measurement instead of stopping to ask. Numbers an earlier card or planner wrote ("keep today's look", a tolerance that only encodes an older algorithm's behaviour) are not the owner's requirements and no later card reinstates them as gates. Correctness invariants (no crash, no NaN, tests green, the owner's own budgets) stay gates.
- **The method is a suggestion, on every card.** The goal and the owner's constraints on the result bind; how to get there is the executor's call, with the reason in the Result, even when the owner wrote the method. A method binds only where the spec records it as a decision. Planners write methods under "Ideas" or "Suggested approach", never as required steps.
- **The acceptance is the behaviour the owner checks, at the view that shows the defect.** Measured on one project: five cards in a row closed `done` on numbers the executor defined itself while the visible result was nil — a slip of a millimetre, a change shipped default-off, a check from the one angle that hides the defect, a quantity the fix's own clamp zeroes by construction. Hence: a card with a visible result names a `Defect view:` (where the defect shows), a render or capture budget, and "How the owner checks this" in the owner's words; the executor writes a `### Reproduce` block (A on today's build before the first edit, the requirement restated, the gate and whether it is bad on A, the assumed causes, B at the end) and `finish.py` refuses without the A and B captures. A probe that would not also fail on today's build is not evidence; an A/B that "looks the same at sheet size" is a question for the owner, not a done.
- **A suspected cause is an explore card.** Words like "likely", "probably", "check whether", "confirm first" in What exists or Spec mean the cause was not measured: the card is `Kind: explore` at high effort. `board.py lint` refuses such hedges on a `build` card at medium or low effort.
- **A stand-in answer is not the owner.** When an AI answers questions on the owner's behalf (unattended runs), it may only say "deviate and document" or keep the conservative assumption; it never confirms a planner number as a requirement, prescribes a new method or creates a card. Anything that would is left for the owner. Stand-ins are off by default (`autospawn.py --answers 0`): measured, they turned planner guesses into requirements and new cards.
- **The lint is the gate on card text.** `python plan/board.py lint Txx` checks Kind, source tags on thresholds, hedges on build cards, a Defect view where a render budget exists, the expensive-model line, and that the header `Prerequisites:` match the row. `board.py add` refuses a card that fails it; `claim` does not lint, so old cards stay claimable.
- **"What exists" for follow-on cards.** When a card builds on done work, name the functions, tables and files it will use, their current signatures and what they lack. Refer to symbols, never to line numbers (they drift as files grow; symbols are grepped). This section is what makes the card executable by a cheaper model.
- **Budget the expensive checks.** When verification costs more than a test run (renders, screenshots, long simulations), the card states a budget ("three sheets while iterating, one full look at the end") and names the numeric check that comes first. Agents without a budget iterate visually until the context is gone.
- **Research cards produce documents.** Tax rules, strategy definitions, data-source surveys: the deliverable is a file under `docs/` plus, when applicable, a machine-readable rules file. Implementation cards then reference those files as spec.
- **Provenance.** A card added later carries "Owner's request, <date>: ..." so an agent knows why it exists and can tell intent from accident.

Use `templates/TASK.md` for the card layout. Keep the header block exactly: it is what agents check before starting.

## 3. Allocate models: cheapest capable model per card

Every card carries one tag. Tags are model keys (`fable`, `opus55`, `sonnet`, `local`) or several joined with `-or-`. `haiku` is not offered: it cannot run in auto mode, so it cannot execute a card unattended; add it to `MODELS` only when the owner explicitly asks for it. The owner launches a model; the tag decides what it may claim. The board's `Counts:` line shows the split at a glance.

**The default is the general model; the most capable one only on the owner's word.** Measured on one project: a Fable request cost about 4.5x an Opus 5.5 one in dollars and took about 6-7x its share of the plan's 5-hour usage window; three Fable cards started together used the window up in 90 minutes, and Fable was 89 % of card spend, mostly in tuning loops. So every card is `opus55` by default, at `Effort: high`, `xhigh` for the hardest diagnosis, `medium` for the mechanical bulk. A tag naming `fable` needs a `Fable: [{{OWNER}} <date>] <why>` line on the card, which `board.py lint` checks (`OWNER_WORD_MODELS`). A planner who thinks a card needs Fable writes that under the card's Questions and tags it `opus55`. `fable-or-opus55` is not "whichever is free": Fable runs the first context and Opus 5.5 every context after the first checkpoint (`autospawn.py --handoff`), so it needs the same line. A hard diagnosis gets a higher effort, not a dearer model.

Heuristic, from most to least expensive:

| Give to | Cards where |
|---|---|
| The most capable model (`fable`, owner's word only) | Judgment cannot be specified away and the owner says so: research that produces the spec, the core algorithm whose objective is underdetermined, anything where a subtly wrong answer is expensive and no test would catch it. It scopes and starts; the general model iterates after the first checkpoint. |
| The general model at xhigh effort (`opus55`, `Effort: xhigh`) | The hardest diagnosis: new geometry or frame math, a visual result judged from pixels or against a reference, a bug whose cause several cards failed to find. |
| The general model at high effort (`opus55`, Claude Opus 5.5, the default effort) | Explore cards; diagnosis with numbers to lean on (a subtle solver, shading or numerical bug), geometry with a clear spec, integration across many modules, numerically delicate code, long specs that are precise but dense, cards whose acceptance test is only meaningful if the design is right. |
| The general model at medium effort (`opus55` with `Effort: medium` in the card header) | UI wiring, data and slider tables, tests, docs, batch scripts, running a build/screenshot cycle and reporting it: precise specs that need care, not depth. |
| Either a mid model or the local model (`opus55-or-local`, `sonnet-or-local`) | Adapters with a fixed interface, CLIs, notebooks, screens with a precise spec, fixtures, docs, glue between done modules. Whichever is free takes it. |
| The local model only (`local`), or `sonnet` when there is none | Skeleton, config plumbing, one-source adapters with a sample response in the fixtures, mechanical refactors with a test that already exists. |

Rules:

- Never pair the most expensive model with a cheaper one in an `-or-` tag. Expensive budget goes only where required; the owner runs the expensive model until it has nothing claimable, then switches down.
- Cheaper models fail on ambiguity, not on volume. When you downgrade a card's tag, upgrade its spec: exact signatures, exact acceptance command, a "What exists" section, and the conservative default for every choice the agent might otherwise make.
- If a downgraded card comes back blocked with questions that a precise spec would have answered, fix the card, not the tag.
- Checkpoint reviews read `git diff --stat` and the diffs of source paths only; generated assets and data are marked `-diff` in `.gitattributes` so no agent can pull them into its context by accident.
- Tooling first: a new tooling or tooling-performance card goes in phase 0 whatever phase is running (`PRIORITY_PHASES` in `board.py` puts it first in claim order). Slow tools are a tax on every later card; fixing them early pays back on all of them.
- Checkpoint cards go to the general model (`opus55`). Besides the deliverable the owner reviews, a checkpoint reviews the phase's merges for architectural fit and compatibility and opens at most three fix cards (cheapest capable tag, slotted before the next phase); a fix card never re-opens a delivered card's scope, and a phase of five cards must not produce six fixes.
- Claude Opus 5 (`opus`) is not offered: Opus 5.5 is its successor at a lower price per token ($4/$20 per MTok vs $5/$25, cache reads $0.20 vs $0.50), so the cheap end of the Opus tier is `opus55` at `Effort: medium`, not an older model.
- Re-tag only `todo` cards, by hand, in a `Board: retag ...` commit by the owner.

## 4. Put the cards on the board

1. Write the card file `plan/tasks/Txx-<slug>.md`. Ids are two or three digits, allocated once and never reused.
2. Commit the card on MAIN: `Plan: add Txx, <title>` (a planning session with the owner is the one case where planning files are committed directly on MAIN; there is no board row yet, so no agent can be working on it).
3. Add the row: `python plan/board.py add Txx "<title>" <phase> <tag> "<prereqs>" [--after Tyy]`. The tool refuses an id without a card file, an unknown prerequisite or an invalid tag, and commits `Board: add Txx` on its own.
4. Change a `todo` row only with `python plan/board.py set Txx [--title ...] [--prereqs ...] [--tag ...] [--phase ...]`: it lints the card, rewrites the row and the card header's `Prerequisites`/`Model`/`Phase` to match, and commits `Board: set Txx (<fields>)`. Re-point prerequisites only when the new card must run before them. A held row is refused; a card rescoped after its claim reaches the branch through `git merge main`.
5. `--after Tyy` only places the row; claim order is board order, so a card meant to run "right after the running one" must sit above every earlier `todo` row with the same prerequisites.

Initial planning of a new project is the same loop for every card, then one `Plan: conventions, task board, N task files, invoker prompt` commit.

**A new phase or iteration.** When the owner opens a new scope ("the MVP is done, plan the next product"), first `python plan/board.py archive-done` (done rows no open row waits on move to `plan/BOARD-ARCHIVE.md`) and `archive` for suggestions, then grill the new scope with the earlier decisions open for review: record which spec sections still bind, which are superseded (dated, named), and keep the new brief in `docs/brief-<iteration>.md`. Cards of the new phase cite the sections that still bind; nothing is inherited by default.

## 5. Owner operations (never done by an executing agent)

- **Reset a held row.** A crashed agent leaves `in-progress`. The owner checks the worktree, merges or discards the branch, removes the worktree and branch, and edits the row back to `todo` in a `Board: reset Txx to todo` commit. Agents never do this, whatever the date on the row.
- **Answer a waiting card.** Write the answers under each question in the card on its branch (the worktree), commit `Txx: answers` there, then from MAIN `python plan/board.py answered Txx`. An agent still in that context continues; otherwise `autospawn.py` (or `launch.py --resume Txx --run`) resumes the card like a checkpoint.
- **Unblock.** Answer the "Questions for the owner" in the card file; commit `Txx: answers`. The blocked branch and worktree are still there. Either merge the partial work (`git merge --no-ff task/Txx`) or discard it (`git branch -D task/Txx`), remove the worktree, then reset the row to `todo`. The next claim starts from the answered card.
- **Archive.** `python plan/board.py archive-done` keeps the board to open rows and their prerequisites; `archive` does the same for triaged suggestions. `plan/COMPACT.md` is the periodic pass over the always-loaded documents.
- **Unattended runs, recovery, usage limits:** `reference/operations.md`.
- **Reprioritize.** Move rows or change prerequisites by hand, `Board: reorder ...`. Only `todo` rows move.
- **Switch models.** When `board.py next <expensive>` reports nothing claimable, launch the next model down. `board.py show` lists what each model could claim right now; `plan/board.html` (kept fresh by `board.py watch`) shows the same plus cards waiting on answers.

## 6. Signs the plan needs work

- Two agents conflicted on merge: a shared file was not split. Split it and add the prerequisite.
- A card came back with more than three questions: the spec was not settled. Grill, update the spec, rewrite the card. Cards are capped at four questions; questions gate the merge (the agent asks the owner before merging, see `templates/INVOKE.md` 4b).
- The expensive model has claimable cards while cheap ones idle: tags are too generous, or the graph is too narrow. Re-tag or add prerequisites-free cards.
- Cards keep being added at the end and never claimed: the board order is wrong. Use `--after`.
- Cards close `done` while the owner sees no change: the acceptance was a number the executor could define. Rewrite the next card's acceptance as the owner's check at a Defect view, and add a redo card rather than reopening the done one.
- Several cards in a row "fix" the same defect: the underlying model cannot do what the owner asks (on one project a simulation model whose design ruled out the behaviour the owner wanted absorbed card after card of tuning). Stop writing tuning cards; write one explore card that questions the model, with the owner.
- A card is relaunched again and again as an orphan and each context reaches the same conclusion: a dirty MAIN (often an untracked file the owner left at the root) or a broken tool, not the card. Look at the transcripts in `Logs/agents/` before relaunching.
