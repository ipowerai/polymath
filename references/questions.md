# Questions: writing, asking, grading, recording

## Formats (`--qtype`)

| qtype      | What it is                                        | Use it for                         |
| ---------- | ------------------------------------------------- | ---------------------------------- |
| `scenario` | an exam-style situation, then one answer          | most certification questions       |
| `mcq`      | one correct answer of four                        | quick checks, reviews              |
| `multi`    | choose N of five or more                          | exam-style "Which two..."          |
| `tf`       | true or false, plus "why"                         | rapid checks on a misconception    |
| `recall`   | a short free answer from memory                   | details, numbers, definitions      |
| `explain`  | explain it in their own words, or teach it back   | checking real understanding        |
| `code`     | read code (what happens?) or write a small piece  | programming topics                 |

The engine weighs evidence by format. A correct free recall counts for much more than a
correct multiple choice, which can be guessed.

## Difficulty (`--difficulty 1-5`)

1. Recognise or recall a definition.
2. Apply it in a simple, direct case.
3. Apply it in a realistic scenario with some noise.
4. Analyse a tricky case: an exception, an edge case, a limit, or the order of events.
5. Weigh a trade-off, design a solution, or combine several concepts.

## Why an answer was wrong (`--error`)

| error | What it means | What helps |
| --- | --- | --- |
| `misconception` | They hold a wrong rule. | Refute it with a contrasting case. |
| `gap` | They never learned it. | Teach it briefly. |
| `confusion` | They mixed it up with a similar concept (add `--confused-with <id>`). | A side-by-side contrast now, and a contrast question later. |
| `detail` | Right idea, wrong number, name or detail. | The exact detail, with a way to remember it. |
| `slip` | They knew it but misread the question or picked the wrong option. | Name it. No lesson. |
| `trap` | They fell for the question's wording (NOT, EXCEPT, "choose two", a qualifier). | Name the trap pattern. No lesson. |

Slips and traps don't count against what they know and don't open a mistake; the engine
counts them under exam technique.

If you can't tell which type it is, leave `--error` out. The engine then records a
confident miss as `misconception` and any other miss as `gap`. Asking "What made you pick
that?" usually tells you.

## Writing good questions

- **One concept per question**, so the answer is recorded against one concept id.
- **Wrong options from real misconceptions.** Use the concept's recorded misconceptions
  first, then the classic traps. Keep options plausible, similar in length and
  grammatically consistent. No "all of the above".
- **Never repeat** a question listed in the item's `recent_questions`. A re-ask keeps the
  rule and changes the scenario, the names and the numbers.
- **Contrast questions for confusions**: build a case where the two concepts give
  different answers, so only someone who can tell them apart gets it right.
- **On certification tracks, match the exam's style**: "A developer needs to...",
  realistic constraints, "Which two...".
- **Rehearse the common traps:**
  - NOT/EXCEPT stems;
  - "most efficient", "most secure" and "fewest steps" qualifiers;
  - limit and size numbers;
  - order-of-events questions;
  - options that are true but don't answer the question.
- **Original questions only.** Never use real exam items.
- **Fix the answer key first.** Decide it, and for free answers the 2-4 key points a good
  answer must contain, **before** you read their response.

## Asking

- Ask for a confidence rating with every answer: "Answer with a letter and how sure you
  are, 1 (guessing) to 5 (certain), e.g. `B 4`."
- **The question dialog.** Use `AskUserQuestion` for short single-answer multiple choice
  that fits in the dialog, if the tool is available and `preferences.use_question_ui`
  isn't false:
  - the first question is the item, with option labels `A`-`D` and each option's text in
    its description;
  - the second is confidence: `Guessing`=1, `Unsure`=2, `Fairly sure`=4, `Certain`=5.

  The question must stand on its own, because the learner may see only the dialog. Use
  plain text for code, long scenarios and multi-select with more than four options. An
  answer typed into "Other" is a free answer: grade it on its merits.
- For blocks of several questions at once, see "Speed" in the session guide.

## Grading

- `mcq`, `scenario`, `tf`: exact match. A right `tf` with a wrong "why" is `partial`.
- `multi`: all right is `correct`, more than half right is `partial`, otherwise
  `incorrect`. So a "choose two" with one wrong pick is `incorrect` (one of two is not
  more than half), as the dashboard grades it; `partial` arises only on "choose three"
  or more. The real exam gives no partial credit; say so on certification tracks.
- Free answers: every key point present is `correct`. The core idea right but a key
  point missing or fuzzy is `partial`. The core idea wrong is `incorrect`. Don't mark
  down wording, spelling, or a different but valid way of putting it.
- **Feedback.** Give the verdict first (✓ / ✗ / ½). On a right answer, add one line at
  most. On a wrong one, follow the engine's `action` (see the session guide).

## Recording (right after grading, every time)

```
ENGINE record <concept-id> <correct|partial|incorrect> --qtype <qtype> --difficulty <1-5> \
  --confidence <1-5> --track <slug> --question "<summary in at most 12 words>" \
  [--error <type>] [--misconception "<the wrong belief, one line>"] [--confused-with <id>] \
  [--targets <mistake id>] [--seconds <n>]
```

- **The output** gives the attempt id, the mastery change, the `signal`, the `action`,
  the next difficulty, any mistake opened, fixed or resolved, and any prerequisites
  credited. Act on it.
- **`--misconception`** describes their wrong belief, not the right answer: "thinks
  before-insert triggers can read the record Id", not "Id is null in before insert".
- **`--targets M3`** marks a question that re-asks mistake M3:
  - a miss reopens M3;
  - a right answer the same day marks it fixing;
  - a right answer a day or more later resolves it.
- **`--confidence`**: leave it out when they didn't give one; never invent it.
- **`--seconds`**: give it when you know roughly how long they took. The engine compares
  it with the exam's time per question.
- **Batches**: `ENGINE record-batch - --track <slug>` reads a JSON list on stdin, with the
  same fields: `concept`, `result`, `qtype`, `difficulty`, `confidence`, `question`,
  `error`, `misconception`, `confused_with`, `targets`, `seconds`.
- **Corrections**: `ENGINE undo` removes the most recent event if you recorded something
  wrong. It leaves the web dashboard's records alone, stepping past them only to your own
  study records from the last 12 hours; when it refuses, use `void`. `ENGINE void
  <attempt id>` removes a disputed answer whose question or answer key was wrong.
