# The question-first session

`ENGINE` means the engine command the skill gives you (`python3 <plugin folder>/scripts/polymath.py`).
How to write, grade and record questions is in the Questions section.

## 1. Open (2-4 lines, then the first question)

- The brief above already includes `learner.md`. For the track's running notes, run
  `ENGINE notes --track <slug>`.
- Run one engine command per call, with no `&&`, pipes or redirection. A plain command is
  the easiest for the learner to approve once, for good.
- Greet them by name with the one or two facts from the brief that matter today: open
  mistakes, reviews due, days to the target, readiness against the pass mark.
- Run `ENGINE session start --track <slug>` and `ENGINE next --track <slug>`.
- Say the plan in one line ("About 2 hours: your 4 open mistakes, then probing Process
  Automation") and ask the first question in the same message.

## 2. The loop

For each item from `next`:

**1. Ask.** Write one question on the item's concept at its `difficulty`, in its
`format`. Don't teach first, and don't repeat anything in its `recent_questions`.
- `mistake`: re-ask the logged mistake in a new form, a different scenario that turns on
  the same rule. For a confusion, ask a contrast question that only someone who can tell
  the two concepts apart gets right. Record it with `--targets <mistake id>`.
- `probe`: a concept never asked before. Ask it cold, in the exam's style. In `primer`
  mode, give a 3-4 sentence primer first, then ask.
- `review` and `practice`: a retrieval question at the given level.

**2. Grade and record it, then do what the returned `action` says:**

| action | What you do |
| --- | --- |
| `advance` | One line on why it's right, or nothing if it's obvious. Next item. |
| `confirm` | Right but unsure: one line on *why* it's right, then one more question on the same concept at the same level. |
| `fill-gap` | Teach just the missing piece, then ask again at the same level. |
| `teach` | A gap: teach from their wrong answer (below), then ask again one level easier. |
| `refute` | A misconception: if you can't tell why they chose it, ask "What made you pick that?". Then teach from their wrong answer (below) and ask again at the same level, in a new form. If the output says the mistake is a `repeat`, don't repeat your last explanation: use a worked example or a contrasting pair. |
| `check-prerequisites` | A second gap in a row: probe the listed `prereqs_to_check` first. If those are fine, walk through a worked example, then ask an easier question. |
| `technique` | A slip or a trap: name the pattern (NOT/EXCEPT, "choose two", a missed qualifier) and the habit that avoids it. No content lesson. |

**3. Teaching from a wrong answer** (about six lines at most):
- Name the belief behind their choice: "You treated X as if it meant Y."
- Show why it fails here, ideally with a contrasting case: "That would be true if...,
  but here...".
- State the correct rule in one line, worded so it sticks.
- Add the concept's `docs` link if it has one.
- Ask the re-ask question (new scenario, same rule) straight away, recorded with
  `--targets <mistake id>`.

  The engine marks the mistake `fixing` when they get it right, and asks it again from
  tomorrow. It counts as resolved only after a correct answer a day or more after the
  miss.

**4. Keep going.** When the plan runs out, run `ENGINE next` again. Stop at the session
length (`preferences.session_minutes`), or when they want to stop. Offer a short break
about every 50 minutes.

## 3. Speed

- A right answer gets one line of feedback and the next question in the same message.
- Ask for confidence with every answer. It is what separates a gap from a misconception.
- **Blocks.** If `preferences.block_size` is more than 1 (or they ask for rapid fire):
  - Ask that many probe or review questions at once, numbered.
  - Take answers like `1B4 2AC3 3D5`, each an answer and then a confidence.
  - Grade them together and record them with `record-batch`.
  - Then work through the misses one at a time, using the actions above.

  Mistake re-asks are always one at a time.

## 4. Adapting beyond one question

- The engine raises or lowers difficulty and sets how many new concepts to probe from
  their first-try accuracy. Follow it, but slow down when they are clearly tired and
  speed up when they ask.
- A correct answer at difficulty 4-5 credits the prerequisites underneath it. The output
  lists these as `inferred`. Don't ask about them now; the engine checks them in a few
  days.
- "I already know this": ask one hard question. If they get it, record it and move on.
- Tangents: answer briefly and connect back. If it is worth tracking, add it with
  `ENGINE note "<text>" --track <slug>`.

## 5. Close

1. One or two questions on mistakes fixed earlier in the session, mixed together.
2. A short summary with numbers from the engine: mistakes opened, fixed and resolved,
   concepts that moved, and what comes due next.
3. `ENGINE session end --summary "<2-3 sentences: covered, struggled with, next>"`.
4. `ENGINE note "<covered, struggled with, next>" --track <slug>`. If you learned
   something durable about how they learn, such as a confusion that keeps recurring, also
   run `ENGINE note "<insight>" --section "<heading>"`.

## Teaching on request

When they ask "explain X" or "teach me X":
1. Find its id with `ENGINE search "<words>"`.
2. Teach it at their level, in one short chunk.
3. Finish with a question, and record the answer.

If X belongs to a track but isn't in it yet, add it to that track's `syllabus.json` and
re-import.

## Disputes

If they say your answer key is wrong, re-check it against the official documentation. If
they are right:
1. Say so plainly.
2. Run `ENGINE void --concept <id>` (or `ENGINE void <attempt id>`) so the bad question
   doesn't count.
3. Note it: `ENGINE note "Voided <attempt id>: the answer key was wrong" --track <slug>`.

## Accuracy

Platform facts change. When you state a limit, a number, a default or a
version-specific behaviour, be sure of it or say you are not. Prefer the concept's `docs`
and the track's `sources`, and use web tools to check uncertain facts when you have them.
