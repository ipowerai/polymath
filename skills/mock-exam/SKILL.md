---
name: mock-exam
description: Simulated certification exam under exam conditions (weighted by the exam blueprint, timed, no feedback until the end), then a scored debrief by area that turns every miss into a mistake to fix. Use when the user wants a mock exam, practice test or full-length exam simulation, or asks whether they are ready.
argument-hint: "[track] [mini | full | number of questions]"
allowed-tools:
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py *)
  - Read(~/.polymath/**)
  - Edit(~/.polymath/**)
  - Write(~/.polymath/**)
---

# Polymath: mock exam

## Where the learner stands
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py brief`

## Who you are
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide persona`

**ENGINE**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py`

## The request
"$ARGUMENTS"

1. **Set it up.**
   - Find the track (default: the active certification track), and read
     `ENGINE track show <slug> --json` for its areas, weights, concepts and `exam` facts.
   - The size is `mini` (20 questions, the default), `full` (the exam's question count,
     from `exam.questions`), or a number they give.
   - Scale the time limit from `exam.minutes` to the size.
   - Tell them the rules in three lines: the number of questions, the time, and no
     feedback until the end.
   - Run `ENGINE session start --track <slug> --focus mock-exam`.
2. **Write the whole exam first, in private.** Before showing the first question, draft
   every question with its concept id, area, qtype, difficulty and answer key.
   - Divide the questions across areas in proportion to the weights. Round to whole
     numbers, but give every area at least one.
   - Within each area, spread them across concepts, including ones not yet started; that
     is what a real exam does.
   - Mix difficulty 2-5, with most at 3-4.
   - Use the exam's formats (`scenario`, and `multi` "choose two").
   - Every question must be original.
3. **Give the exam.** Present the questions in blocks of five, numbered, in plain text.
   - Collect answers in a compact form ("1B4 2AC3 3D5", each an answer then a
     confidence).
   - Give no verdicts and no hints.
   - Note the time at the start and the end.
4. **Score it.**
   - Grade every answer. Multi-select counts only if fully right, as on the real exam,
     but record it as `partial` when more than half right (so never on a "choose two":
     one wrong pick there is `incorrect`).
   - Compute the overall score and the score per area.
   - For each miss, decide its `--error` type. A confident miss is usually a
     `misconception`; "choose two" and NOT questions are often a `trap`.
5. **Record everything**, in order:
   1. Every answer: `ENGINE record-batch - --track <slug>`, one JSON list on stdin, with
      `error`, `misconception` and `confused_with` on the misses. Every real miss opens a
      mistake that the next sessions fix first.
   2. The result:
      `ENGINE mock <slug> <score> --domain <area-id>=<score> ... --questions <n> --minutes <m>`.
   3. `ENGINE session end --summary "..."`.
6. **Debrief.**
   - The score against the pass mark, the area scores, and how this compares with the
     engine's predicted score (`ENGINE status --track <slug>`).
   - The misses grouped by concept, starting with the confident ones: the correct answer
     and a one-or-two-line why for each.
   - Exam technique: slips, traps, and time used against the budget.
   - The 3-5 concepts to focus on next.
   - Whether they are ready to book the exam: the predicted score at least 7 points above
     the pass mark, coverage of at least 90%, and the last two mocks above the pass mark.
   - Note the result and the focus list: `ENGINE note "<result, focus list>" --track <slug>`.

## Questions
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide questions`
