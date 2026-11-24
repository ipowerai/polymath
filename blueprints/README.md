# Blueprints

A blueprint is a ready-made syllabus: weighted areas, concepts, prerequisites and
learning objectives for a common goal. The tutor checks this folder before building a
syllabus from scratch, and imports a blueprint with
`polymath.py track import <file> --target-date <date>`.

## Contributing one

1. **Use the official source.** For a certification, follow the current official exam
   guide's sections, weights and objectives. Put the URLs in `sources`, and set
   `exam.verified` to `true` only if you checked every number (questions, minutes, pass
   mark, weights) against that guide. Note the date you checked it in `verify_note`.
2. **No exam content.** A blueprint describes what to learn. Never include real exam
   questions, recalled questions or anything from a brain dump.
3. **Concept ids are global.** Name them by subject, not by exam
   (`salesforce.apex.async-batch`, not `pd1.batch`), and reuse the ids already in other
   blueprints for the same idea, so progress carries between tracks.
4. **Granularity**: one concept is one idea you could ask 3-5 different questions
   about. Most certifications come to 40-120 concepts.
5. **Prerequisites**: only true dependencies, 0-3 per concept. The importer rejects
   cycles and unknown ids.
6. **Objectives**: one testable sentence starting with a verb.
7. **Check it**: `POLYMATH_HOME=$(mktemp -d) python3 scripts/polymath.py init && POLYMATH_HOME=... python3 scripts/polymath.py track import blueprints/<file>.json`
   must succeed with no warnings, and `python3 -m unittest discover tests` must pass.

File names are kebab-case after the goal (`aws-solutions-architect-associate.json`).
