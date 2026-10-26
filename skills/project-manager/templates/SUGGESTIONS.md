# Suggestions

Things agents notice that would make the repo better, each with at least one proposed fix. The owner reads this file
periodically and turns the good proposals into cards, script changes, refactors or rule changes.

## For agents

Two kinds of suggestion, both welcome:

- **Code.** Something you saw in the code while working, even outside your card's files and even if it did not
  slow you down: an inefficient method (an O(n²) loop over vertices, a per-frame allocation), an architectural break (a runtime class reaching into tooling code, two systems owning the same
  state, a layer bypassed, a dependency pointing the wrong way), duplicated logic that has drifted, a misleading
  name or comment, dead code, a magic number that should be a setting, a missing guard that will crash on an edge
  case. Say where (`--where <file>:<symbol>`) and why it matters. Do not fix it yourself unless your card owns
  the file: note it here instead.
- **Process.** Something that cost you real turns, tokens, wall-clock or rework: a slow loop (a compile, a full
  test run), a test that checks more than it needs to or is flaky, a tool that refused for a bad reason, a
  script whose output made you run it twice, an ambiguous card or spec, a rule in `CLAUDE.md`/`INVOKE.md` that
  cost more than it saved, a missing probe. Say what it cost.

The task's own difficulty is out of scope.

- Never edit this file by hand and never on a task branch. One command, from MAIN, commits it immediately:

  ```
  python plan/board.py suggest "<one-line title>" --by <model> --card Txx --area <area> [--where <file>:<symbol>] \
      --problem "<what is wrong, and what it costs: turns, minutes, launches, frame time, risk>" \
      --propose "<a concrete fix>" [--propose "<another>"]
  ```

  Areas: `code`, `architecture`, `performance` (the code itself); {{SUGGESTION_AREAS}} (the process, e.g. `build`,
  `tests`, `board`, `docs`, `tooling`, `spec`, `other`).
- Skim the titles first, here and in the archive (`grep "^## S" plan/SUGGESTIONS*.md`). If yours is listed here,
  +1 it instead of opening a duplicate: `python plan/board.py suggest --also Sxx --by <model> --card Txx --problem
  "<what it cost this time>" --propose "<fix, may repeat or refine>"`. The count of "Seen again" lines is how the
  owner prioritises. If it is in `SUGGESTIONS-ARCHIVE.md` it is already decided (its status names the card or
  commit): do not refile it unless it still hurts after that card is done, and then name the old id.
- Proposals must be concrete enough to become a card: name the file, function, script, flag or rule, and say what
  the change saves or prevents.
- At most three suggestions per card, filed at the end (before `finish.py`), so they cost no turns mid-work. Jot
  them in your card's Result as you go if you fear forgetting them.

## For the owner

This file is the inbox: it holds open entries only. Set `Status:` by hand on an entry: `open` -> `accepted (Txx)`,
`rejected (<why>)` or `done (<commit>)`, commit as `Plan: suggestions triage`, then run
`python plan/board.py archive`: every entry whose status is no longer `open` moves to `plan/SUGGESTIONS-ARCHIVE.md`
(the next `board.py suggest` does the same sweep if you forget). Ids are never reused.

<!-- Entries below are appended by `board.py suggest`. Newest last. -->
