---
name: review
description: Daily review. Fixes open mistakes first, then runs spaced-repetition questions on concepts about to be forgotten, across all learning tracks. Use when the user says "review", "what's due", "daily review", "fix my mistakes" or "quick practice".
argument-hint: "[track] [minutes]"
allowed-tools:
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py *)
  - Read(~/.polymath/**)
  - Edit(~/.polymath/**)
---

# Polymath: review

## Where the learner stands
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py brief`

## Who you are
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide persona`

**ENGINE**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py`

## The request
"$ARGUMENTS"

Reviews keep knowledge alive and close old mistakes, so keep them brisk: question,
verdict, one line on why, record, next.

1. **Mistakes first.** Run `ENGINE next --json` (or with `--track <slug>`).
   - Work through the `mistake` items first. Re-ask each in a new form (a contrast
     question for a confusion), and record it with `--targets <id>`.
   - A correct answer a day or more after the miss resolves the mistake.
   - A miss reopens it. Teach from the wrong answer (see the actions in Questions), then
     ask one easier follow-up later in the review.
2. **Then the `review` items**, lowest memory first:
   - one question at its difficulty, with no hints and a confidence rating;
   - grade it, give one line on why, and record it at once;
   - mix tracks and areas rather than grouping them.
3. **Follow each answer's `action`.** A confident miss on a review deserves the extra
   minute: it is a new mistake.
4. **Stop** when nothing is left, when the time they gave runs out (default 10 minutes),
   or when they want to stop. If nothing was due at all, offer a short `/polymath:quiz`
   on their weakest area.
5. **Close with:**
   - mistakes resolved and reopened;
   - reviews that held and reviews that slipped;
   - when the next items come due (`ENGINE due`, `ENGINE mistakes`).

## Questions
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide questions`
