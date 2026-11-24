# How Polymath models learning

Everything below is computed by `scripts/polymath.py` from the event log
(`~/.polymath/events.jsonl`). Delete a derived number and nothing is lost: it is
recomputed on the next command. The constants sit at the top of the script.

## Concepts and tracks

A **concept** is one learnable idea with a global id (`salesforce.apex.async-batch`),
prerequisites, related concepts and a learning objective. A **track** is a goal: a
set of concepts grouped into weighted areas (an exam blueprint's sections), with an
optional target date and pass mark. Concepts belong to the learner, not the track, so
two tracks that share a concept share its progress.

## Knowledge: Bayesian Knowledge Tracing

Each concept has `p`, the probability the learner knows it (0.10 for an unseen
concept, or half the mastery of a `related` concept if that is higher). After each
graded answer:

```
guess = base_guess(format) × (1.3 − 0.15 × difficulty)
slip  = 0.05 + 0.03 × (difficulty − 1)

p | correct   = p(1−slip) / (p(1−slip) + (1−p)·guess)
p | incorrect = p·slip   / (p·slip + (1−p)(1−guess))
p | partial   = the average of the two
p ← p_posterior + (1 − p_posterior) × learn_rate
```

Base guess rates: `tf` 0.50, `mcq`/`scenario` 0.25, `multi` 0.10, `recall`/`code`
0.05, `explain` 0.03. A correct free answer is therefore strong evidence and a correct
multiple choice is weak evidence. Harder questions are harder to guess and easier to
fumble.

## Memory: a forgetting curve

Each concept also has a memory **stability** `S`, in days. The probability of
recalling it `t` days after the last review is

```
R(t) = (1 + t / (9·S·m))^−1
```

where `m` is the learner's personal memory factor (below). With the default 90%
retention target, the next review is due after `S·m` days (in general
`9·S·m·(1/r − 1)`).

- First answer: `S` = 0.3 (wrong), 1 (hard), 2.5 (good) or 6 (easy) days.
- A correct answer after a gap: `S ← S·(1 + g·(1 + 2(1−R))/(1 + 0.2·lapses))`, with
  `g` = 0.4 (hard), 1.2 (good) or 2.2 (easy). Recalling something you had half
  forgotten strengthens it more. This is the spacing effect.
- A correct answer less than 12 hours after the last one barely helps
  (`g × 0.15`). Cramming doesn't build durable memory.
- A wrong answer is a lapse: `S ← clamp(0.3·S, 0.2, 1)`, so it comes back within a day.

The grade (again, hard, good, easy) comes from the result and the stated confidence:
wrong is again, partial or low confidence is hard, confidence 5 is easy, and anything
else is good.

**Mastery** is `p × R`: what you know, discounted by what you have likely forgotten.
A concept counts as **mastered** when `p ≥ 0.85` and it has been recalled correctly
after a gap of at least a day (or it was known before starting).

## The learner model

- **Pace** = 3 ÷ the median number of attempts needed to reach two correct answers in
  a row on a new concept (after at least 5 concepts; bounded to 0.5-2). It scales the
  learn rate and the number of new concepts offered per session (3 × pace, from 1 to 6,
  fewer when reviews pile up).
- **Memory factor** `m` = 1 + 2 × (actual − predicted recall) on spaced reviews
  (after at least 5 reviews, fully trusted at 20, bounded to 0.5-2). People who
  remember better than the curve get longer intervals.
- **Calibration** = stated confidence (1 to 5, mapped to 20%-95%) minus actual accuracy
  over the last 50 rated answers. Positive means overconfident. Confident misses are
  counted separately because they are the most valuable to correct.

## Answers drive the session

Every `record` returns a **signal** and an **action**, so the tutor's next move is decided
by a rule rather than improvised:

| Answer | Signal | Action |
| --- | --- | --- |
| correct, confidence ≥ 3 or none given | solid | advance |
| correct, confidence ≤ 2 | fragile | confirm (explain why it's right, ask one more) |
| partial | partial | fill-gap |
| wrong, error gap or detail, or no error type with confidence ≤ 3 | gap | teach, then re-ask one level easier |
| wrong, error misconception or confusion, or no error type with confidence ≥ 4 | misconception | refute, then re-ask in a new form |
| a gap twice in a row, with a prerequisite below 0.6 | gap | check-prerequisites |
| error `slip` or `trap` | slip / trap | technique (no content lesson) |

A misconception that recurs is still `refute`, marked `repeat`: the fix is a different
explanation (a worked example or a contrasting pair), not a search for missing
foundations. A stated error type decides over the confidence: a confident miss the learner
says they guessed is a gap, taught and asked one level easier, as the staircase below does.

**Difficulty is a staircase per concept.**
- Up one level after a correct answer given with confidence, or with no confidence stated.
- The same level after a hesitant correct answer, a partial answer, or a misconception
  (re-asked in a new form).
- Down one level after a gap.

Slips and traps don't move it. A concept never asked starts at the placement difficulty.

## Placement

**First-try accuracy** is the score of the first answer on each concept never seen before,
smoothed toward a starting value worth 6 answers.
- A track's starting value is the learner's level elsewhere: their first-try accuracy in
  other tracks, itself smoothed toward `preferences.start_level` (beginner 0.30,
  intermediate 0.55, experienced 0.75, expert 0.85).
- Each answer counts once.
- So an expert in one subject can be a beginner in another, and one early miss doesn't
  knock an expert out of top-down probing (two do).

| First-try accuracy | Mode | New concepts are probed... |
| --- | --- | --- |
| ≥ 0.70 | top-down | deepest first (longest prerequisite chain), with no prerequisite gating |
| < 0.45 | primer | with a short primer first, in syllabus order, prerequisites first |
| otherwise | bottom-up | cold, in syllabus order, once prerequisites reach `p ≥ 0.6` |

- **Probe difficulty** = clamp(round(1 + 4 × accuracy), 2, 4).
- **Probes per session** = clamp(round(4 + 8 × accuracy), 3, 12), cut to a third when
  reviews and open mistakes together pass 15, and to zero past 30.

**Prerequisite credit.** A correct answer at difficulty 4-5, with confidence ≥ 4 or none
stated, credits the concept's untouched prerequisites:
- one level down: `p ≥ 0.75`;
- two levels down: `p ≥ 0.65`.

Each credited concept gets a 3-day memory, so a real question checks it soon. Credited
knowledge never counts toward learning pace.

## The mistake bank

- **What counts.** Every miss that is not a slip or a trap becomes a mistake with an id
  (`M7`). The same concept with the same wrong belief (normalised text) reuses its id.
  With no description, the mistake is filed by error type. When no error type is given,
  a confident miss is recorded as a misconception and any other miss as a gap.
- **Lifecycle.**
  - **open**: just missed;
  - **fixing**: a correct answer the same day;
  - **resolved**: a correct answer a day or more after the last miss.

  A later miss reopens it. A question can name the mistake it re-asks (`--targets M7`);
  otherwise a correct answer on the concept counts toward all of its unresolved mistakes.
- **Scheduling.** Open mistakes are scheduled first, every session. Mistakes in fixing
  come back once a day has passed.
- **Confusions.** A confusion (`--confused-with`) is re-asked as a contrast question
  between the two concepts.

**Slips and traps** move knowledge as a partial answer would, set the memory grade to
"hard", open no mistake, and are counted as exam technique. So is the median time per
answer against the exam's budget (exam minutes × 60 / questions; 90 s by default). A
correct answer given with confidence 4 in under half the budget counts as "easy".

**Disputed answers.** `void` writes an event that removes one attempt from the replay, for
when the question or its answer key was wrong. Attempt and mistake ids are never reused.
`undo` also voids an answer rather than deleting it. It never removes the web dashboard's
records (they carry its session id or the source `web`): it steps past them only to the
tutor's own study records from the last 12 hours, never to an import or an older answer,
and it stops at a tutor answer that was already voided in the dashboard. An answer that
re-asks a mistake (`--targets`) counts only toward that mistake: if every answer that
showed the mistake has been voided, it counts toward none, never toward the concept's
other open mistakes.

**Answers from the dashboard** are recorded with the same fields, plus the session id
and a ref (`web:<session>:<question>`, with `:t` for a follow-up and `:c` for a closing
re-check), so a retried request is recorded once. A mock exam and a session's end are
also logged once per session id. Answers are dated when they were given, even when they
reach the log later: a mock the clock submits after the computer slept or a restart is
dated when time ran out (and logged dated when it was sat), and a confident miss left
without its reason is dated when it was answered. The model replays the log in time
order (the log's order where times are equal), so whatever was recorded in between, in
Claude Code too, takes its place after them: a right answer given before a miss doesn't
fix the mistake that miss opened. An answer recorded now is never dated before the newest
record, so a clock that steps back (a correction after waking) can't move it earlier. A confident miss (4-5) is recorded
only after the learner says what made them pick it, which decides the error type. The
follow-up is offered only after an independent check answered both questions without the
key, agreed with it, and confirmed the follow-up tests the same rule in a new case.

## Choosing what to study (`next`)

1. **Mistakes that are due**: open ones first, misconceptions before other errors, then the
   most-missed. Up to three open the session.
2. **Due reviews**, lowest memory first (up to three).
3. **Probes and practice, interleaved.** Two probes per practice item in top-down mode,
   one otherwise.
   - Probes rotate across areas, heavier areas first.
   - Practice is ordered by area weight × (1 − p).
   - Claimed or credited knowledge is not practised; it waits for its check.
4. The remaining mistakes and reviews.

Each item comes with:
- its difficulty and a suggested format (probe, primer then question, recall or MCQ,
  scenario or code, exam-style, re-ask or contrast for mistakes);
- its last three question summaries, so they are never repeated;
- its recorded misconceptions and its documentation links.

## Readiness and pace

- **Predicted score** = Σ area weight × mean over the area's concepts of
  `0.25 + 0.70 × mastery`: the chance of answering a four-option exam question on that
  concept, averaged by the exam's weights. Unstarted concepts count at their prior,
  which keeps the prediction low until coverage is real.
- **Verdict**: *ready* when predicted ≥ pass mark + 7 points, coverage ≥ 90% and the
  latest mock exam (if any) ≥ pass mark + 5; *close* when predicted ≥ pass mark;
  otherwise *not yet*.
- **Pace**: concepts newly mastered per day over the last 14 days, projected over what
  remains, against the target date (ahead, on track or behind), plus the daily rate
  needed to finish with a week for mock exams and review.

## Trends (the web dashboard)

The dashboard replays the event log once and takes each track's numbers at every local
midnight. Its history therefore uses exactly the model above, and its last point equals
today's live numbers.

- **Starting point**: the predicted score just before the track's first answer.
- **This week**: today's predicted score, mastered count and coverage against seven days
  ago, and the share of answers right over the last seven days against the seven before.
  On a day without study the predicted score falls a little, because memory fades.
- **Direction**: the slope of `log(0.95 − predicted)` over the last week's daily values.
  0.95 is the highest the predicted score can go, since full mastery still leaves a slip
  chance. A falling slope means the gap is closing (*up*); a rising one means it is
  opening (*down*). It needs answers on at least two of those days.
- **Projection**: each coming day closes, or opens, the same share of the remaining gap
  as the last week did. The line therefore levels off below 95% rather than running past
  it. It shows where the recent pace leads, not a forecast.
- **Outlook**:
  - *on track* if the projection reaches the booking line (pass mark + 7) by the exam
    date;
  - *at risk* if it ends between the pass mark and that line;
  - *off track* if it ends below the pass mark.
- **Time against progress**: the share of the time from the starting point to the exam
  that has passed, against the share of the climb from the starting score to the booking
  line that is done.
- **Booking gate**: book when all three hold:
  - predicted score at least the pass mark + 7;
  - coverage at least 90%;
  - the last two mock exams at least the pass mark + 5.

  This is stricter than the verdict, which can say *ready* before any mock exam.

## Planning a goal

A goal's milestones are scheduled by the code, not the model: one at a time at your minutes a day, each after
what it depends on and any waiting that follows it (`schedule` in `scripts/polymath_goals.py`). A booked exam
keeps its date. A credential is complete when its parts are. Progress is hours done, counting a track in
progress by the share of its concepts mastered.

## Time spent

The page reports the seconds between your own actions while it is visible and in focus; a gap over 180 seconds
counts as nothing. A report can't claim more time than has passed since the last one. A Claude Code session has
no page to measure it, so it counts the time from its start to each answer, skipping any gap over 10 minutes.

## What it does not model (yet)

Transfer between concepts beyond explicit `related` links, time of day, question-level
difficulty calibration from many learners, and the separate skill of exam technique.
These are good contribution areas.
