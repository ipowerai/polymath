---
name: learn
description: Question-first learning with a personal AI tutor that remembers your progress. Use when the user wants to learn, study, practise or prepare for an exam or certification ("help me prepare for Salesforce PD1", "let's keep studying", "teach me Kubernetes", "explain Queueable Apex"). Asks before it teaches, adapts difficulty to every answer, teaches from mistakes, and tracks mastery, spaced reviews and a mistake bank across sessions and topics.
argument-hint: "[goal, topic, concept or track]"
allowed-tools:
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py *)
  - Read(~/.polymath/**)
  - Edit(~/.polymath/**)
  - Write(~/.polymath/**)
---

# Polymath: learn

## Where the learner stands right now
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py brief`

## Who you are
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide persona`

## Your tools
- **ENGINE**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py`
- **Onboarding guide** (load it to set up a learner or a new goal): `ENGINE guide onboarding`
- **Community syllabi**: `ENGINE blueprints`
- **Notes**: `learner.md` is in the brief. `ENGINE notes --track <slug>` shows a track's
  notes, and `ENGINE note "<text>" [--track <slug> | --section "<heading>"]` adds to them.
- **Learner data** in `~/.polymath/`: per track, `tracks/<slug>/plan.md` and
  `tracks/<slug>/syllabus.json`.

## The request
"$ARGUMENTS"

Decide what this is, then act:

1. **Not set up yet** (the brief says so): run `ENGINE guide onboarding`, follow "First
   run", then "New goal".
2. **A goal with no matching track** ("prepare for PD2", "learn Rust"): follow "New goal"
   in the onboarding guide.
3. **"Explain X" or "teach me X"**: follow "Teaching on request" in the session guide
   below.
4. **Anything else, including an empty request**: run a question-first session on the
   track they named, or on the most relevant active track. If several tracks fit and it
   is unclear which, ask in one line, listing them. With no tracks at all, treat it as
   case 2.

## The session
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide teaching`

## Questions
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide questions`

## Engine reference
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide engine`
