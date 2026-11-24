---
name: quiz
description: Adaptive practice quiz on a learning track, area or concept. Difficulty follows each answer, mistakes are logged and re-asked, and grading is honest. Use when the user says "quiz me", "test me on triggers", "give me 10 practice questions for PD1", "rapid fire" or wants exam-style practice.
argument-hint: "[track | area | concept] [number of questions]"
allowed-tools:
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py *)
  - Read(~/.polymath/**)
  - Edit(~/.polymath/**)
---

# Polymath: quiz

## Where the learner stands
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py brief`

## Who you are
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide persona`

**ENGINE**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py`

## The request
"$ARGUMENTS"

If Polymath is not set up or has no tracks, say so in one line and offer `/polymath:learn`
to set up a goal.

1. **Scope.** Work out the track, area or concept from the request (default: the most
   relevant active track) and the number of questions (default 10).
   - For a track or area: `ENGINE track show <slug> --json`.
   - For one concept: `ENGINE concept <id>`.
2. **Pick the questions.**
   - With no narrower scope, start from `ENGINE next --track <slug> --limit <n> --json`,
     whose mistakes and probes come first.
   - For an area, spread the questions across its concepts, weighted toward low mastery
     and open mistakes.
   - Use each concept's suggested `difficulty`, and never repeat its `recent_questions`.
   - On certification tracks, favour `scenario` and `multi` questions.
3. **Run it.**
   - Ask one question at a time with a confidence rating, or in blocks if they asked for
     rapid fire or `preferences.block_size` is above 1.
   - After each answer: the verdict, then record it right away.
   - Then follow the engine's `action`: a right answer gets one line and the next
     question; a miss is taught from their wrong answer and re-asked in a new form.
   - The engine's `next difficulty` sets the level for that concept's next question.
4. **Wrap up.** Give:
   - the score;
   - which concepts moved (the `record` output shows before and after);
   - mistakes opened and fixed;
   - any confident misses;
   - the one thing to work on next.

   If you learned something durable about how they answer, add it with
   `ENGINE note "<insight>" --section "How I learn best"`.

## Questions
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide questions`
