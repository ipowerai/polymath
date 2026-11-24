# Onboarding and new goals

`ENGINE` means the engine command the skill gives you (`python3 <plugin folder>/scripts/polymath.py`).

## First run (the brief says Polymath is not set up)

1. Say in two lines what this is: a tutor that learns how you answer, teaches from your
   mistakes, and remembers your progress across sessions, with everything stored as plain
   files in `~/.polymath` on your machine.
2. Ask, in one message:
   - their name;
   - what they want to learn first, and why;
   - any deadline (an exam date?);
   - how much time they can give on a typical day;
   - how much they already know of it: beginner, intermediate, experienced or expert;
   - for certifications, which related ones they already hold.

   Use `AskUserQuestion` for the multiple-choice parts if it is available.
3. Run `ENGINE init --name "<name>"`, then set what they told you:
   `ENGINE profile set preferences.session_minutes=<n> preferences.start_level=<level>`.
   Offer:
   - rapid-fire blocks for experienced learners (`preferences.block_size=3`);
   - renaming the tutor or changing its tone
     (`ENGINE profile set persona.name=<name> persona.tone="<tone>"`).
4. Record what you learned with `ENGINE note "<text>" --section "<heading>"`, using the
   headings of `learner.md`: Background and existing knowledge, Goals and motivation, How I
   learn best, Recurring misconceptions and weak spots, Coaching notes.
5. Continue with **New goal** below.

## New goal

### 1. Clarify (one message, then move on)

- The outcome: a certification, a skill for a project, or understanding a field.
- Where they start from, and what neighbouring knowledge they have.
- The target date and the time per day.

### 2. Reuse what already exists

- `ENGINE track list`: is there a track for this already, or a related one?
- `ENGINE blueprints`: is there a community syllabus for it? If one matches:
  1. Copy it with `ENGINE blueprints <name> --to-track <slug>`. It lands in
     `~/.polymath/tracks/<slug>/syllabus.json`.
  2. Read the copy and summarise it for the learner.
  3. Check its `exam.verified` flag (next step), then go to step 4.
- **Verify the exam facts** (sections, weights, question count, time, pass mark, name)
  against the official exam guide.
  - Fetch it if you have web tools.
  - Official pages often don't load for tools (Trailhead, for one). When the fetch fails,
    ask the learner to paste the outline from the official page; that takes them under
    a minute.
  - Fix the copy, and set `exam.verified` to `true` with the date in `verify_note`.
  - Tell the learner what you verified and what you could not.
- `ENGINE search "<words>"` for concepts they have already studied. **Reuse those ids** in
  the new syllabus, so knowledge carries across tracks (PD1 → PD2 → architect).

### 3. Build the syllabus (when there is no blueprint)

- **Certifications**: use the official exam guide's sections, weights and objectives
  exactly. Put the URLs in `sources`. With no web access, build it from what you know,
  set `exam.verified` to `false`, and ask the learner to confirm the weights.
- **Skills and fields**: 4-8 domains, each with a few to a dozen concepts, foundations
  first.
- **Size of a concept**: one idea you could ask 3-5 different questions about, taking
  roughly 20-60 minutes to learn. A typical certification has 40-120.
- **ids** look like `area.subarea.concept-name`: lowercase and dot-separated, with hyphens
  inside a segment. Name the idea, not the track (`salesforce.apex.async-batch`, not
  `pd1.batch`), so other tracks can share it.
- **prereqs**: only true dependencies, 0-3 per concept, kept shallow. The engine refuses
  cycles and unknown ids.
- **related**: similar concepts already in the registry. They give a head start.
- **objective**: one testable sentence, starting with a verb.
- **docs**: links to the official page for the concept, when you are sure of them.

```json
{
  "slug": "salesforce-pd2",
  "title": "Salesforce Platform Developer II",
  "goal": "Pass PD2 by June",
  "kind": "certification",
  "target_date": "2027-06-30",
  "exam": {"name": "...", "questions": 60, "minutes": 120, "passing_score": 0.70,
           "verified": true, "verify_note": "checked against the official guide on 2026-11-17"},
  "sources": ["https://..."],
  "domains": [
    {"id": "advanced-apex", "name": "Advanced Apex", "weight": 0.3, "concepts": [
      {"id": "salesforce.apex.async-batch", "title": "Batch Apex", "prereqs": [],
       "tags": ["salesforce", "apex"], "objective": "Implement ...",
       "docs": ["https://developer.salesforce.com/..."]}
    ]}
  ]
}
```

`kind` is either:
- `certification`: the domain weights must sum to 1.0;
- `general`: the weights are normalised for you.

For a goal with no exam, leave out `exam`.

### 4. Import it

1. Run `ENGINE track import ~/.polymath/tracks/<slug>/syllabus.json --target-date <YYYY-MM-DD>`.
   If it is rejected, fix the listed errors and import again.
2. Show the learner the outline: each domain with its weight and number of concepts, plus
   a few example concepts. Ask them to confirm or adjust it.
3. If they already hold this certification:
   - import it anyway, so its concepts count toward later tracks;
   - mark it done with `ENGINE track set <slug> status=done`;
   - credit the knowledge: `ENGINE assess <id>=0.8 ...` for its concepts, which the engine
     checks with a question in a few days.

### 5. Placement happens in the first session

There is no separate diagnostic. The first session probes across the areas at a level set
by `preferences.start_level`, and the engine adjusts from their first-try accuracy:
- **experts** get *top-down* probing, deepest concepts first; a hard hit credits the
  prerequisites underneath;
- **beginners** get a *primer* before each new concept.

Expect the first 30-40 questions to map what they already know.

### 6. Plan

Run `ENGINE status --track <slug>` for `needed_per_day`. Then write
`~/.polymath/tracks/<slug>/plan.md`, kept to one screen:
- the phases: probe and fix every concept → consolidate and take mock exams → final
  review;
- the weekly target (concepts per week) and the session rhythm;
- mock exams: the first at about 60% coverage, then weekly in the last month;
- the rule for booking the exam: predicted score at least 7 points above the pass mark,
  coverage of at least 90%, and the last two mocks at least 5 points above the pass mark.

### 7. Start

Start the first session now (the first question goes in the same message), or tell them
what the next session will cover.
