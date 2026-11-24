---
name: progress
description: Show learning progress: readiness for an exam, mastery by area, pace against the target date, the mistake bank, exam technique, strengths and weak spots, plus the live web dashboard. Use when the user asks "how am I doing", "my progress", "am I trending toward my goal", "am I ready for the exam" or "show my dashboard".
argument-hint: "[track]"
allowed-tools:
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py *)
  - Read(~/.polymath/**)
---

# Polymath: progress

## Where the learner stands
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py brief`

## Who you are
!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py guide persona`

**ENGINE**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py`

## The request
"$ARGUMENTS"

1. **Gather.** Run:
   - `ENGINE status --track <slug>` for the named track, or `ENGINE status` with no track
     for an overview;
   - `ENGINE profile`;
   - `ENGINE mistakes`.
2. **Report** in a few short lines, with numbers:
   - **Readiness**: the predicted score against the pass mark, and the verdict. Say what
     "predicted" means: an estimate from mastery and coverage, which mock exams make more
     reliable.
   - **By area**: the strongest and weakest areas, weighted by how much each counts.
   - **Mistakes**: how many are open, fixing and resolved, and the most important open
     ones.
   - **Pace**: ahead, on track or behind for the target date, and the daily rate it needs.
   - **How they answer**: placement mode and first-try accuracy, confidence calibration,
     exam technique (slips, traps, time per question). Report these only where there is
     enough data to mean something; the profile says how many samples each has.
3. **Next step.** End with the single most useful next step (fix the open mistakes, a
   weak area, a mock exam), and offer to start it.
4. **Dashboard.** Offer the live dashboard, which shows the trend visually: this week
   against last, where their pace leads by the exam date, and right answers over time.
   - If they want it, start `ENGINE serve --open` as a background command, then give the
     sign-in link it prints (http://127.0.0.1:8765/?token=… by default). Opening it once
     keeps the browser signed in for 30 days. The token is private: don't repeat it
     anywhere else.
   - If one is already running, the command says so and opens it.
   - For a file to keep or send, run `ENGINE dashboard --open` instead. It writes a
     self-contained snapshot; give its path.

Don't inflate. If there is little data so far (a few days or a few answers), say the
numbers will firm up with use.
