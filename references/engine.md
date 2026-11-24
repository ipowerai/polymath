# Engine command reference

`ENGINE` means the engine command the skill gives you (`python3 <plugin folder>/scripts/polymath.py`).
- Add `--json` to any command except `brief` and `guide` for structured output.
- Data lives in `$POLYMATH_HOME` (default `~/.polymath`), readable only by the learner:
  every command keeps the folder 0700 and its files 0600.
- Errors exit with code 2 and say how to fix them.

## Commands

| Command | What it does |
| --- | --- |
| `init --name N [--persona P]` | Create or update the profile (safe to re-run). |
| `brief` | One-screen status for the tutor. Never fails. |
| `guide NAME ...` | Print the tutor's instructions: `persona`, `teaching`, `questions`, `onboarding`, `engine`. |
| `blueprints [NAME] [--to-track S] [--force]` | List the bundled syllabi, print one, or copy one into a track folder to edit and import. |
| `track import FILE\|- [--slug S] [--target-date D]` | Create or update a track from syllabus JSON. Re-importing keeps progress. |
| `track list` / `track show S [--domain D]` | List all tracks / show every concept of one track with its status. |
| `track set S key=value ...` | Set `title`, `short` (the name in the dashboard's menu), `goal`, `target_date`, `status` (active, paused, done), `daily_minutes`, `kind` or `target_mastery`. |
| `next [--track S] [--limit 12]` | The session plan: mistakes to fix, reviews due, probes of new concepts, and concepts in progress. |
| `record ID RESULT [flags]` | Record one graded answer. Returns the signal, the action and the next difficulty. With `--targets M3` on a mistake that has since been undone or voided, it says so (`target_gone`): a right answer then counts toward no mistake. |
| `record-batch FILE\|- [--track S]` | Record a JSON list of answers. Nothing is written unless every item is valid. |
| `mistakes [--track S] [--status unresolved\|open\|fixing\|resolved\|all]` | The mistake bank. |
| `void [ATTEMPT_ID] [--concept C] [--reason R]` | Remove a disputed answer from the learner model. Without an id it takes your own most recent answer (on that concept), by when it was given. |
| `notes [--track S] [--plan]` | Print `learner.md`, or a track's `notes.md` (`plan.md` with `--plan`). |
| `note TEXT [--track S] [--section H]` | Add a dated note under a heading of `learner.md` (default "Coaching notes"), or to a track's `notes.md`. |
| `undo [--count N]` | Remove the most recent event(s). The web dashboard's records are left alone: undo steps past them only to your own study records from the last 12 hours, and stops at an answer of yours that was already voided in the dashboard. |
| `assess ID=LEVEL ... [--source diagnostic\|self]` | Set starting knowledge (0-1 or %). Levels of 0.7 and above are checked again in about 4 days. |
| `lesson ID [--minutes M] [--track S]` | Log that a concept was taught. |
| `mock S SCORE [--domain id=score ...] [--questions N] [--minutes M]` | Log a mock exam. |
| `session start [--track S] [--focus F]` / `session end [--summary T]` | Time a session. `end` writes the journal. |
| `status [--track S]` | Readiness, coverage, mastery by area, pace, open mistakes and placement. |
| `profile` / `profile set key.path=value ...` | Learner analytics (pace, memory, calibration, exam technique, mistakes) / preferences. |
| `search WORDS` / `concept ID` / `due [--track S]` | Find concept ids / everything about one concept / concepts due for review. |
| `serve [--open] [--host H] [--port P] [--read-only] [--demo] [--tutor T] ...` | Run the live web dashboard (default http://127.0.0.1:8765). It prints a sign-in link with a token (also in `web-token`); only `--read-only` or `--demo` on localhost opens without one. Never repeat the token anywhere else. `--token-file F` (or `POLYMATH_WEB_TOKEN`) sets a chosen token privately; `--rotate-token` makes a new one. `--tutor auto\|claude\|api\|fake\|off` picks who writes questions for study in the browser. Run it in the background. If one is already running, it says so and exits (`--open` opens it). |
| `dashboard [--open]` | Write a self-contained snapshot of the dashboard to `~/.polymath/dashboard.html`. |

## `record` flags

- **The question**: `--qtype scenario|mcq|multi|tf|recall|explain|code` (default `mcq`),
  `--difficulty 1-5` (default 3), `--question TEXT`, `--options N` (the number of choices,
  if not 4).
- **The answer**: `--confidence 1-5`, `--seconds N`.
- **Why it was missed**: `--error misconception|gap|confusion|detail|slip|trap`,
  `--confused-with ID`, `--misconception TEXT`.
- **Mistakes**: `--targets M3` (this question re-asks mistake M3), `--mistake M3` (link a
  miss to an existing mistake).
- **Other**: `--track S`, `--note TEXT`, `--rating again|hard|good|easy` (normally
  derived).

RESULT is `correct`, `partial` or `incorrect` (`right` and `wrong` also work).

## Plan item kinds

| Kind | What it is |
| --- | --- |
| `mistake` | An open mistake, or one fixed earlier that is due its next-day check. Re-ask it with `--targets`. |
| `review` | Due for spaced review. |
| `probe` | Never asked. Ask it cold; teach only if they miss it. |
| `practice` | Started but not yet solid. |

Each item carries:
- `difficulty` and `format`;
- `recent_questions`, which you must not repeat;
- `misconceptions`, `docs` and `objective`.

## Modes (set by first-try accuracy, starting from `preferences.start_level`)

| Mode | First-try accuracy | How new concepts are probed |
| --- | --- | --- |
| `top-down` | at least 70% | Advanced concepts first, with no prerequisite gating, and more new concepts per session. |
| `bottom-up` | in between | In order, once their prerequisites are in place. |
| `primer` | below 45% | A short primer first, foundations first. |

## Signals and actions

| Signal | Action |
| --- | --- |
| `solid` | `advance` |
| `fragile` | `confirm` |
| `partial` | `fill-gap` |
| `gap` | `teach` |
| `misconception` | `refute` |
| `slip` / `trap` | `technique` |

A second gap in a row on a concept with a weak prerequisite gives `check-prerequisites`,
with the list in `prereqs_to_check`. A misconception always gives `refute`; when it
recurs, the output marks it `repeat`, so the explanation should change angle. Without
`--error`, a miss at confidence 4-5 is read as a misconception; with it, the error type
decides (a confident guess recorded with `--error gap` is a gap, and gets `teach`).

A correct answer at difficulty 4-5 with confidence of at least 4 credits its prerequisites
(listed as `inferred`); the engine checks them about 3 days later.

## Concept status

| Status | Meaning |
| --- | --- |
| `new` | Never seen. |
| `learning` | Below 60%. |
| `developing` | 60-85%. |
| `mastered` | At least 85%, **and** recalled correctly a day or more later, or known before starting. |
| `due` | Scheduled for review now. |

**Mastery** = knowledge × memory:
- **knowledge** is the probability they know it, updated from their answers;
- **memory** is the probability they can still recall it today. It decays until the next
  successful review.

## Mistakes

Every real miss (not a slip or a trap) becomes a mistake such as `M7`. It moves from:
- **open**: just missed;
- **fixing**: answered right the same day;
- **resolved**: answered right a day or more after the miss.

The same wrong belief missed again reopens the same mistake.
