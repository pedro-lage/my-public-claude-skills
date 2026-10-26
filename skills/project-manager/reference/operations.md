# Operations: running the board unattended, and what goes wrong

Read this when the owner wants cards to run while they are away, when a card seems stuck, or when tuning cost.
The scripts' own docstrings are the detailed reference; this page is what was learned running them on one project
(about 215 cards, up to four agents at once, many nights unattended).

## 1. The board manager: `plan/autospawn.py`

A plain loop, no model: it reads `plan/BOARD.md` and git every `--every` seconds and starts
`python plan/launch.py <model> --run --task Txx` in a new console whenever a slot is free. It spends no tokens.

```
python plan/autospawn.py --once --dry-run          what it would start now, then exit (always run this first)
python plan/autospawn.py --max 3                   up to three cards at once, headless
python plan/autospawn.py --max 1 --in-main         serial mode: one card at a time, worked directly in MAIN
python plan/autospawn.py --set-mode interactive    switch a running controller (new spawns only)
```

- **Slots.** `--max` counts `in-progress` and `waiting` rows plus spawns not yet claimed. Which card a free slot
  gets is the first in `board.claim_order` any allowed model may claim (phase 0 first), not the first card of the
  first model. Launches are a few seconds apart; the claim tool exempts a card whose lease a live launch holds, so a
  batch started in the same second does not lose the race.
- **Leases.** Every session holds an OS lock on `Logs/agents/Txx.lease` for its whole life (`launch.take_lease`). A
  second launch for the same card refuses, and autospawn skips a card whose lease is held. Before leases, a restart
  of the controller re-resumed every parked card while the first resumed session was still working, and the owner
  saw two shells on one card.
- **Resume.** A checkpointed card (branch tip `Txx: checkpoint` or `Txx: answers`) is resumed with
  `launch.py --resume Txx --run`, at most `--resumes` times since the newest `Txx: answers` (default 10); past that
  the row goes `blocked`. Counts come from commit subjects, so the controller survives restarts: never rename those
  subjects.
- **Handoff** (`--handoff fable:opus55`): a card the expensive model claims runs its first context only; every
  later context (resume, orphan restart, answer) runs as the cheaper model. Diagnosis is where the expensive model
  earns its price; tuning loops are where it burned it.
- **Questions.** Waiting cards wait for the owner by default. `--answers N` (N > 0) lets a stand-in
  (`plan/ANSWER.md`) answer spec-versus-reality conflicts with "deviate and document"; anything that would create a
  card or a gate is left for the owner. Keep it off unless the owner asks: stand-ins turned planner guesses into
  requirements and new cards.
- **Idle job.** `IDLE_JOB` (off by default) runs a regression net (the whole test suite, a long grid) only when the
  board is idle and build code moved since its last run; new failures are filed with `board.py suggest`. On a busy
  board it held the shared build environment for 50+ minutes under four running cards.

## 2. Headless or interactive

- **Headless** (default, owner away): `claude -p` with a stream-json transcript in `Logs/agents/`. The console
  closes when the agent ends its turn, and the transcript is what orphan and usage-limit detection read. A
  headless session dies when the agent ends its turn, killing its background runs: agents must block on long runs,
  not background them and end the turn.
- **Interactive** (owner watching): the normal Claude Code screen per window. The window and its lease stay until
  the owner closes it, so a parked card is resumed only after that, and there is no transcript, so a dead
  interactive session is never orphan-resumed and its `in-progress` row blocks a `--max 1` queue. Before leaving
  after an interactive stretch, close the finished windows.

## 3. When a session ends badly

- **Orphan.** A headless session whose transcript went quiet with its lease free and no final `result` (killed
  mid-turn: crash, power cut, a window closed by hand) is resumed once per orphan. Before this rule, a session
  killed mid-turn was never resumed and its row blocked the queue.
- **Usage limit.** A transcript ending on "hit your ... limit · resets 6:10pm" is not an orphan: nothing it covers
  is started until that time, then the card is resumed once without spending an orphan retry. A session or weekly
  limit is the whole account's, so every model waits; only a limit that names one model holds that model alone, and
  its `-or-` cards fall to the next model. A tiny transcript whose only text is the limit message is a limit death.
- **Dirty MAIN.** A session that ended because `finish.py` or `board.py` refused a dirty MAIN waits, uncounted,
  until `git status` in MAIN is empty. Measured: one card was relaunched three times as an orphan because of an
  untracked notes file at the root, and each fresh context reached the same conclusion. Untracked files now block a
  merge only when the branch adds the same path, but tracked edits in MAIN still stop everyone.
- **Recover a stale card.** `start.py --resume` reports uncommitted paths the Result does not mention: read the
  last transcript in `Logs/agents/<stamp>-Txx-*.jsonl` (assistant texts, the Edit inputs on those files, tool
  results matching failures) with a short script; two or three turns recover the state. Look in the temp dir for
  scripts the dead session wrote but did not run.
- **A row added a prerequisite after its claim.** Neither resume pass touches it (logged once) and
  `start.py --resume` refuses it until the prerequisite is done.

## 4. Shared heavy tools (a build engine, a GPU, a database)

- One instance per worktree, and a machine-wide cap of slots (two on a six-core machine: two runs at once gave
  1.48x the throughput of one, a third mostly added contention). A whole-system run (a full end-to-end build and run) takes
  every slot.
- Give each worktree its own copy of a heavy cache rather than a link to MAIN's (`SEEDED_DIRS` in `start.py`): a
  shared cache let one worktree's build overwrite another's, and a worktree built against MAIN's newer serialized
  data crashed at start. Seeding by copy took about 10 s; a rebuild from nothing about 3 minutes.
- Agents queued on a lock should block in one tool call, not poll: on one night 296 of 2075 tool turns were
  ten-minute poll loops, each billed the full context.
- Never "work around" a slow tool by disabling its optimiser (one card ran its tests 6x slower that way and held the
  lock six and a half hours); fix the tool in a phase-0 card.

## 5. Concurrency and merges

- `finish.py` takes `Logs/main.lock` from merge to done; a live lease keeps it, a hand session owns it for a
  bounded time. Waiting for it is free.
- **MOVED** is decided before the merge: main changed build paths that share a test tier with the card's own
  paths (or always-retest paths like dependency manifests). Then nothing is merged; the agent runs `git merge main`
  and the printed tiered test line in its worktree, and finishes again. Nothing runs the expensive tools in MAIN at
  a merge: the old "verify in MAIN" step queued behind the regression net for up to an hour while holding the lock.
- Two cards sharing one file are serialised by a prerequisite, never run together.

## 6. Cost, measured, and how to measure it again

- Fixed cost of a fresh launch: trim unneeded tools (`TRIM_TOOLS` in `launch.py`): 33k -> 21k starting context.
- Checkpoint near 175k context: a resume costs about five re-orienting requests plus the Handoff; the sum of later
  requests saved makes ~175k the optimum (contexts had been checkpointed at a median of 228k, the expensive model up
  to 414k). Agents cannot see their own context size, so `plan/context_hook.py` (a PostToolUse hook) tells them.
- Lookup-only requests were 45-52 % of spend: batch queries, find code by symbol, keep a per-subsystem doc page
  that says where a change starts.
- Thinking was ~86 % of billed output tokens: effort is the largest lever after context length, and it is fixed
  at launch.
- To audit: parse `~/.claude/projects/<project>/*.jsonl` (and `subagents/`); one API request is one `message.id`
  (assistant lines repeat per content block); headless transcripts in `Logs/agents/` duplicate the same sessions
  (dedupe by session id, then by message id); fit prices from the `result` lines' `modelUsage`. Usage-limit
  messages give each usage window's bounds, which is how a model's share of the window is measured.
