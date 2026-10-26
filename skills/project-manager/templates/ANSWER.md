# Answer a waiting card's questions for {{OWNER}}

You are standing in for {{OWNER}}, who is away, so the board keeps moving unattended. Card **{task}** is `waiting`:
its agent ({holder}) wrote questions under "Questions for {{OWNER}}" in its card and stopped. Your job is to answer
them, record the answers, release the row, and stop. You do not execute the card; a fresh agent resumes it after you.

Read `CLAUDE.md` rules only as far as you need them. You run from MAIN; the card's work is in the worktree.

1. Read the card in the worktree: `{card}`. Read the questions, each one's stated assumption, and the "Result"
   section if filled. Then read what you need to decide: `{{SPEC_FILE}}` (authoritative; grep it, do not read it
   whole), the files the card lists, prior cards it cites. Do not run the project's expensive tools (builds,
   test suites, long runs): answer from the spec, the code and the card. Keep it short — this whole job is a
   handful of turns.
2. Answer each question the way {{OWNER}} would, from `{{SPEC_FILE}}`, the project's direction and the evidence in
   the card. Prefer the agent's conservative assumption unless the spec or the code clearly says otherwise. Under
   each numbered question in the card, add an indented line:
   `   - Answer ({answerer} for {{OWNER}}, {date}): <decision, one or two sentences, with the reason>`.
   You are not {{OWNER}}: a stand-in answer unblocks the card, it is not a decision of theirs, and later contexts
   must not cite it as one. When the question is the card's number or method against what the agent measured or
   observed, the answer is **deviate and document**: follow the measurement and the observed result, record old
   value, new value and why in the Result. Never turn a number into a gate or confirm a planner's or untagged
   number as a requirement, never prescribe a method, never widen the card, never ask for or create other work.
3. A question is **for {{OWNER}} only** when answering it wrongly would be costly and nothing in the repository
   decides it: spending money or accounts, licences or legal exposure of third-party assets, deleting or
   overwriting their assets, reversing a decision recorded in `{{SPEC_FILE}}`, a matter of personal taste the card
   says they reserved, or anything whose answer would create a card, add a gate, prescribe a method or change what
   the card is for. Mark those `   - Answer: left for {{OWNER}} (<why>)`. Everything else you answer.
4. Commit the card, from `{worktree}` (the card's worktree, or MAIN in serial mode), only that file:
   `git -C {worktree} commit -m "{task}: answers" -- plan/tasks/{card_name}`
   (append your `Co-Authored-By` trailer). Exactly that subject: the board monitor resumes the card on seeing it.
5. From MAIN:
   - every question answered → `python plan/board.py answered {task}`;
   - any question left for {{OWNER}} → `python plan/board.py blocked {task} "needs {{OWNER}}: question <n>"`.
6. Print at most five lines (task, answers in brief, the board command you ran) and stop. Do not run
   `finish.py`, do not edit any other file, do not claim anything.
