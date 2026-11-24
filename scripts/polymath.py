#!/usr/bin/env python3
"""Polymath learning engine: the bookkeeping half of an AI tutor.

Claude does the teaching. This script keeps score. It owns an append-only
event log and derives everything else from it:

  * per-concept mastery          (Bayesian Knowledge Tracing)
  * memory strength + reviews    (a forgetting-curve scheduler)
  * the learner's pace, memory and confidence calibration
  * exam readiness and a finish-date projection per track

Standard library only. All data lives in $POLYMATH_HOME (default ~/.polymath)
and never leaves the machine. Run `polymath.py --help` for the commands.
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import stat
import statistics
import sys
import tempfile
import threading
import webbrowser
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

VERSION = "0.1.2"
SCHEMA = 1
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# --- model constants (explained in docs/HOW-IT-WORKS.md) -------------------
PRIOR_UNKNOWN = 0.10        # mastery assumed for a concept never seen
DEVELOPING = 0.60           # "you can usually do this"
MASTERED = 0.85             # "you reliably know this"
BASE_LEARN = 0.20           # BKT learn rate for an average-pace learner
GUESS = {                   # chance of answering right without knowing
    "mcq": 0.25, "scenario": 0.25, "multi": 0.10, "tf": 0.50,
    "recall": 0.05, "explain": 0.03, "code": 0.05,
}
FIRST_STABILITY = {"again": 0.3, "hard": 1.0, "good": 2.5, "easy": 6.0}  # days
GROWTH = {"hard": 0.4, "good": 1.2, "easy": 2.2}
CONF_P = {1: 0.2, 2: 0.4, 3: 0.6, 4: 0.8, 5: 0.95}  # stated confidence -> P(correct)
MAX_INTERVAL = 365.0
EXAM_GUESS, EXAM_SLIP = 0.25, 0.05
ERRORS = ("misconception", "gap", "confusion", "detail", "slip", "trap")
TECHNIQUE_ERRORS = ("slip", "trap")   # knew it, lost the mark anyway: exam technique, not knowledge
START_LEVELS = {"beginner": 0.30, "intermediate": 0.55, "experienced": 0.75, "expert": 0.85}
PLACEMENT_WEIGHT = 6                  # how many answers the stated starting level is worth
DEFAULT_SECONDS = 90.0                # time per question when a track has no exam timing
MISTAKE_RE = re.compile(r"^M\d+$")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
CONCEPT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
RESULTS = {
    "correct": "correct", "right": "correct", "c": "correct", "1": "correct", "yes": "correct",
    "partial": "partial", "p": "partial", "half": "partial",
    "incorrect": "incorrect", "wrong": "incorrect", "i": "incorrect", "0": "incorrect", "no": "incorrect",
}
SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
TRACK_STATUSES = ("active", "paused", "done")


class PolymathError(Exception):
    """A problem the user (or Claude) can fix; printed without a traceback."""


# --- small helpers ----------------------------------------------------------
def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def pct(x):
    return "-" if x is None else f"{round(100 * x)}%"


def bar(x, width=10):
    full = int(round(clamp(x or 0.0, 0.0, 1.0) * width))
    return "█" * full + "░" * (width - full)


def home() -> Path:
    return Path(os.environ.get("POLYMATH_HOME") or "~/.polymath").expanduser()


def parse_ts(value) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now() -> datetime:
    fixed = os.environ.get("POLYMATH_NOW")  # lets tests (and time travellers) pin the clock
    return parse_ts(fixed) if fixed else datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day(dt: datetime) -> date:
    return dt.astimezone().date()


def days_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400.0


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value: str) -> date:
    try:
        if not DATE_RE.match(str(value)):
            raise ValueError(value)
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise PolymathError(f"'{value}' is not a date; use YYYY-MM-DD.")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise PolymathError(f"{path} is not valid JSON ({exc}). Fix it or restore it from backup.")


def private_dir(path: Path, tighten: bool = False) -> None:
    """Create a folder only you can open (0700). With tighten, an existing folder of yours is closed to
    other accounts too (your own access is kept as it is): a best effort, so one you don't own (a
    mounted volume, say) is left alone."""
    existed = path.is_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if existed and not tighten:
        return
    try:
        info = path.stat()
        if existed and hasattr(os, "getuid") and info.st_uid != os.getuid():
            return
        have = stat.S_IMODE(info.st_mode)
        want = have & ~0o077 if existed else 0o700  # mkdir's mode is cut by the umask; chmod's isn't
        if have != want:
            os.chmod(path, want)
    except OSError:
        pass


def keep_private(root: Path) -> None:
    """Close a data folder that holds a learning record to other accounts, whichever command runs first
    after an upgrade (`brief` opens every session; older versions left the folder 0755). Inside a 0700
    folder, the files an older version made 0644 can't be opened by anyone else. A folder with no record
    yet (POLYMATH_HOME set to one that exists) is left to `init`. Never fails, and never creates one."""
    try:
        if (root / "learner.json").is_file() or (root / "events.jsonl").is_file():
            private_dir(root, tighten=True)
    except OSError:
        pass


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def parse_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def as_fraction(raw) -> float:
    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(raw)
    except (TypeError, ValueError, OverflowError):
        raise PolymathError(f"'{raw}' is not a number; give a fraction (0-1) or a percentage (0-100).")
    if value > 1.0:
        value /= 100.0
    if not 0.0 <= value <= 1.0:
        raise PolymathError(f"{raw} is not a fraction (0-1) or a percentage (0-100).")
    return value


def whole_number(name: str, raw, low: int, high: int = None, default=None):
    """A whole-number field of an answer (difficulty, confidence, options), kept within range. Left out
    (null or empty) gives the default; anything that isn't a number is an error, not a crash."""
    if raw is None or raw == "":
        return default
    try:
        if isinstance(raw, bool):
            raise ValueError(raw)
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(raw)
    except (TypeError, ValueError, OverflowError):
        span = f"from {low} to {high}" if high is not None else f"of at least {low}"
        raise PolymathError(f"{name} must be a whole number {span} (got '{raw}').")
    value = max(low, int(value))
    return value if high is None else min(high, value)


def shown(value) -> str:
    """A value as an error message quotes it: 'nothing' when it is left out, else as JSON writes it (so
    a list reads as one)."""
    if value is None or value == "":
        return "nothing"
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return f"'{text[:80]}'"


def name_field(name: str, raw):
    """A field of an answer that names something (its format, a mistake, a track, a ref): text, or left
    out (null or empty). Anything else, a list say, is an error, not a crash."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise PolymathError(f"{name} must be text (got {shown(raw)}).")
    return raw


def normalize_result(raw: str) -> str:
    result = RESULTS.get(str(raw).strip().lower())
    if not result:
        raise PolymathError(f"result must be correct, partial or incorrect (got '{raw}').")
    return result


def derive_rating(result: str, confidence, seconds=None, budget=None) -> str:
    """Map an answer to a spaced-repetition grade when Claude doesn't give one."""
    if result == "incorrect":
        return "again"
    if result == "partial":
        return "hard"
    conf = int(confidence) if confidence else 3
    if conf <= 2:
        return "hard"
    fast = seconds is not None and budget and float(seconds) <= 0.5 * budget
    if conf >= 5 or (conf >= 4 and fast):
        return "easy"
    return "good"


def normalize_text(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .!?;:")


def attempt_key(e: dict) -> str:
    return e.get("id") or f"{e.get('ts')}|{e.get('concept')}"


def mistake_key(cid: str, text, error) -> tuple:
    return (cid, normalize_text(text) if text else f"[{error or 'gap'}]")


def exam_budget(track: dict) -> float:
    exam = track.get("exam") or {}
    try:
        if exam.get("questions") and exam.get("minutes"):
            return float(exam["minutes"]) * 60.0 / float(exam["questions"])
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return DEFAULT_SECONDS


def is_full_mock(mock: dict, exam: dict) -> bool:
    """A mock counts toward readiness when it is exam-length (or its size wasn't recorded). A mock
    sat in the dashboard says so itself, since voided or unwritten questions leave its count short."""
    if isinstance(mock.get("full"), bool):
        return mock["full"]
    size, wanted = mock.get("questions"), (exam or {}).get("questions")
    try:
        return not size or not wanted or int(size) >= int(wanted)
    except (TypeError, ValueError):
        return True


def readiness_bars(passing: float) -> tuple:
    """What booking the exam needs: a predicted score of the pass mark + 7 points and a latest full mock
    of the pass mark + 5. Rounded, so 68% + 5 is 73% and not 0.7300000000000001: the readiness verdict
    and the dashboard's booking checks (polymath_web.booking_gate) then agree at the boundary."""
    return round(passing + 0.07, 4), round(passing + 0.05, 4)


def dashboard_record(e: dict) -> bool:
    """Whether the web dashboard wrote this event: it carries a study session's id, or the source "web"
    (or a "web-..." kind of it), as a change made on a dashboard page does. Undo leaves these alone."""
    source = str(e.get("source") or "")
    return bool(e.get("session")) or source == "web" or source.startswith("web-")


def interleave(items: list, group) -> list:
    """Round-robin across groups (in first-seen order), keeping each group's own order."""
    groups, order = defaultdict(list), []
    for item in items:
        key = group(item)
        if key not in groups:
            order.append(key)
        groups[key].append(item)
    out = []
    while any(groups[k] for k in order):
        for k in order:
            if groups[k]:
                out.append(groups[k].pop(0))
    return out


# --- storage ----------------------------------------------------------------
class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else home()
        self.learner_path = self.root / "learner.json"
        self.profile_md = self.root / "learner.md"
        self.concepts_path = self.root / "concepts.json"
        self.events_path = self.root / "events.jsonl"
        self.tracks_dir = self.root / "tracks"
        self.journal_dir = self.root / "journal"

    def ready(self) -> bool:
        return self.learner_path.exists()

    def require(self) -> None:
        if not self.ready():
            raise PolymathError(f"No learner profile in {self.root}. Run `init --name <name>` first.")

    def learner(self) -> dict:
        return read_json(self.learner_path, {})

    def save_learner(self, data: dict) -> None:
        write_json(self.learner_path, data)

    def concepts(self) -> dict:
        return read_json(self.concepts_path, {}).get("concepts", {})

    def save_concepts(self, concepts: dict) -> None:
        write_json(self.concepts_path, {"schema": SCHEMA, "concepts": dict(sorted(concepts.items()))})

    def track_path(self, slug: str) -> Path:
        return self.tracks_dir / slug / "track.json"

    def slugs(self) -> list:
        if not self.tracks_dir.exists():
            return []
        return sorted(p.parent.name for p in self.tracks_dir.glob("*/track.json"))

    def track(self, slug: str) -> dict:
        data = read_json(self.track_path(slug), None)
        if data is None:
            known = self.slugs()
            hint = difflib.get_close_matches(slug, known, n=1)
            raise PolymathError(
                f"No track '{slug}'." + (f" Did you mean '{hint[0]}'?" if hint else "")
                + f" Tracks: {', '.join(known) or 'none yet'}."
            )
        return data

    def save_track(self, data: dict) -> None:
        write_json(self.track_path(data["slug"]), data)

    def events(self) -> list:
        if not self.events_path.exists():
            return []
        out = []
        with self.events_path.open(encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"warning: skipped corrupt line {n} of {self.events_path}", file=sys.stderr)
        return out

    @contextmanager
    def writing(self):
        """Hold the data folder's write lock. Answers and mistakes get ids numbered from what is
        already in the log, so two writers (the tutor in Claude Code and the web dashboard) must
        take turns. Re-entrant within a thread; a no-op where file locks are unavailable."""
        depth = getattr(_WRITE_LOCAL, "depth", 0)
        if depth:
            _WRITE_LOCAL.depth = depth + 1
            try:
                yield
            finally:
                _WRITE_LOCAL.depth = depth
            return
        private_dir(self.root)
        with open(self.root / ".lock", "a+b") as handle:
            _lock_file(handle, True)
            _WRITE_LOCAL.depth = 1
            try:
                yield
            finally:
                _WRITE_LOCAL.depth = 0
                _lock_file(handle, False)

    def append(self, *events) -> list:
        """Add events to the log, stamped now unless they carry their own ts. Every line is made first and
        all are written in one go, so an event that can't be logged leaves the log untouched."""
        private_dir(self.root)
        with self.writing():
            stamp = iso(now())
            written, lines = [], []
            for event in events:
                record = {"ts": stamp, **{k: v for k, v in event.items() if v is not None}}
                lines.append(log_line(record))
                written.append(record)
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write("".join(lines))
            return written


_WRITE_LOCAL = threading.local()


def log_line(record: dict) -> str:
    """One event as a line of the log. Text that isn't valid Unicode (a lone surrogate, as browser JSON
    or an undecodable argument can carry) has those characters replaced with ?, rather than failing."""
    try:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    except ValueError as exc:  # NaN or infinity, which JSON can't hold
        raise PolymathError(f"Can't log this {record.get('type') or 'event'}: it holds a number that isn't "
                            f"finite ({exc}).")
    try:
        line.encode("utf-8")
    except UnicodeEncodeError:
        line = line.encode("utf-8", "replace").decode("utf-8")
    return line


def _lock_file(handle, lock: bool) -> None:
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if lock else fcntl.LOCK_UN)
    except ImportError:  # Windows
        try:
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if lock else msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass


# --- the learner model ------------------------------------------------------
class ConceptState:
    def __init__(self, cid: str, prior: float):
        self.id = cid
        self.p = prior            # P(knows it), from BKT
        self.S = None             # memory stability in days
        self.last = None          # last graded exposure
        self.first = None
        self.attempts = 0
        self.score = 0.0
        self.streak = 0
        self.lapses = 0
        self.assessed = False
        self.known_before = False
        self.spaced_success = False  # recalled correctly after a gap of a day or more
        self.introduced = False
        self.mastered_at = None
        self.last_result = None
        self.misconceptions = []
        self.recent = []
        self.last_difficulty = None
        self.last_confidence = None
        self.last_error = None
        self.misses_in_row = 0
        self.inferred = False

    @property
    def touched(self) -> bool:
        return self.attempts > 0 or self.S is not None


def compute_pace(events: list):
    """How quickly this learner gets to two-in-a-row correct, against a baseline of 3 tries."""
    assessed = {e.get("concept") for e in events if e.get("type") == "assess"}
    runs = defaultdict(list)
    for e in events:
        if e.get("type") == "attempt" and e.get("concept") not in assessed:
            runs[e["concept"]].append(e.get("result"))
    counts = []
    for results in runs.values():
        streak = 0
        for i, result in enumerate(results, 1):
            streak = streak + 1 if result == "correct" else 0
            if streak == 2:
                counts.append(i)
                break
        else:
            if len(results) >= 4:  # still struggling: count it, so pace isn't flattered
                counts.append(len(results) + 1)
    if len(counts) < 5:
        return 1.0, len(counts)
    return clamp(3.0 / statistics.median(counts), 0.5, 2.0), len(counts)


def compute_forgetting(reviews: list):
    """>1 when the learner remembers better than the curve predicts, <1 when worse."""
    if len(reviews) < 5:
        return 1.0, len(reviews)
    gap = statistics.mean(actual - predicted for predicted, actual in reviews)
    return clamp(1.0 + 2.0 * gap * min(1.0, len(reviews) / 20.0), 0.5, 2.0), len(reviews)


def in_time_order(events: list) -> list:
    """Events in the order they happened: by time, in the log's order where times are equal. An answer can be
    logged after others that came later (a mock submitted by the clock after the computer slept, a reason given
    after a pause), and memory, mistakes, pace, first tries and what one answer credits on another concept all
    follow the order the learner answered in. A line whose time can't be read keeps its neighbour's."""
    keyed, last = [], None
    for n, e in enumerate(events):
        try:
            last = parse_ts(e["ts"])
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        keyed.append((last, n, e))
    if all(k[0] is not None for k in keyed) and all(a[0] <= b[0] for a, b in zip(keyed, keyed[1:])):
        return [e for _, _, e in keyed]  # the usual case: already in order
    floor = datetime.min.replace(tzinfo=timezone.utc)
    keyed.sort(key=lambda k: (k[0] or floor, k[1]))
    return [e for _, _, e in keyed]


def split_at(events: list, moment: datetime) -> tuple:
    """(the events up to `moment`, the ones after it), for events in time order."""
    before, after = [], []
    for e in events:
        try:
            late = parse_ts(e["ts"]) > moment
        except (KeyError, TypeError, ValueError, AttributeError):
            late = False
        (after if late else before).append(e)
    return before, after


class Model:
    """Replays the event log into the current picture of the learner."""

    def __init__(self, store: Store, at: datetime = None, events: list = None, until: datetime = None):
        """`events` replaces the log (the dashboard replays it day by day for its history). The log is replayed
        in time order (see in_time_order). `until` replays only what happened up to that moment, to work out an
        answer given then (ids, voids and the known wrong beliefs still come from the whole log)."""
        self.store = store
        self.at = at or now()
        self.learner = store.learner()
        prefs = self.learner.get("preferences", {})
        self.retention = clamp(float(prefs.get("desired_retention", 0.9)), 0.70, 0.97)
        self.start_level = START_LEVELS.get(str(prefs.get("start_level", "intermediate")).lower(), 0.55)
        self.concepts = store.concepts()
        self.tracks = {}
        for slug in store.slugs():
            try:
                self.tracks[slug] = store.track(slug)
            except PolymathError as exc:
                print(f"warning: {exc}", file=sys.stderr)
        self.budgets = {slug: exam_budget(t) for slug, t in self.tracks.items()}
        raw = store.events() if events is None else list(events)
        self.raw_attempts = sum(1 for e in raw if e.get("type") == "attempt")
        # every mistake id the log has given, voided answers' too: ids are never reused, and a question
        # that re-asks a mistake whose answers were voided can still say so (see assign_mistake)
        self.given_mistakes = {str(e["mistake"]) for e in raw if MISTAKE_RE.match(str(e.get("mistake") or ""))}
        self.max_mistake = max((int(m[1:]) for m in self.given_mistakes), default=0)
        voided = {e.get("target") for e in raw if e.get("type") == "void"}
        self.events = in_time_order([e for e in raw if not (e.get("type") == "attempt" and attempt_key(e) in voided)])
        later = []
        if until is not None:
            self.events, later = split_at(self.events, until)
        self.states = {}
        self.reviews = []
        self.calibration = []
        self.by_format = defaultdict(lambda: [0, 0.0])
        self.by_difficulty = defaultdict(lambda: [0, 0.0])
        self.activity = Counter()
        self.active_days = set()
        self.session_minutes = 0.0
        self.mocks = defaultdict(list)
        self.mistakes = {}
        self.mistake_index = {}
        self.first_tries = []      # (track, score) of the first answer on a concept never seen before
        self.technique = Counter()
        self.timings = []          # (seconds, exam budget)
        self.effects = {}
        self._depths = {}
        self.pace, self.pace_n = compute_pace(self.events)
        self.learn_rate = clamp(BASE_LEARN * self.pace, 0.05, 0.5)
        self.ff = 1.0
        for event in self.events:
            try:
                self.apply(event)
            except (KeyError, ValueError, TypeError) as exc:
                print(f"warning: skipped unreadable event {event!r} ({exc})", file=sys.stderr)
        opened = set(self.mistakes)
        for e in later:  # recorded with a later time: a miss on the same belief given before it reuses the mistake that
            # belief opened. Only the answer that opens a mistake keys it, as in the replay (_track_mistakes): a re-ask
            # or a repeat carries its mistake's id but not its belief.
            mid = e.get("mistake") if e.get("type") == "attempt" else None
            if not mid or not e.get("concept") or mid in opened:
                continue
            opened.add(mid)
            self.mistake_index.setdefault(mistake_key(e["concept"], e.get("misconception"), e.get("error")), mid)
        self.ff, self.ff_n = compute_forgetting(self.reviews)

    # -- replay --
    def apply(self, e: dict) -> None:
        ts = parse_ts(e["ts"])
        kind = e.get("type")
        if kind == "attempt":
            self._attempt(e, ts)
        elif kind == "assess":
            self._assess(e, ts)
        elif kind == "lesson":
            self.state(e["concept"]).introduced = True
            self.active_days.add(local_day(ts))
        elif kind == "mock":
            self.mocks[e["track"]].append(e)
            self.active_days.add(local_day(ts))
        elif kind == "session_end":
            self.session_minutes += float(e.get("minutes", 0))

    def _attempt(self, e: dict, ts: datetime) -> None:
        cid = e["concept"]
        s = self.state(cid)
        self.effects = {"inferred": [], "mistake": None, "resolved": [], "fixing": []}
        first_try = not s.touched and not s.assessed
        result = e["result"]
        score = SCORE[result]
        error = e.get("error")
        technique = error in TECHNIQUE_ERRORS and result != "correct"
        fmt = e.get("qtype", "mcq")
        diff = int(clamp(int(e.get("difficulty", 3)), 1, 5))
        track = e.get("track") or (self.concepts.get(cid, {}).get("tracks") or [None])[0]
        budget = self.budgets.get(track, DEFAULT_SECONDS)

        # Knowledge (BKT). A slip or a trap says little about what they know.
        evidence = 0.5 if technique else score
        guess = float(e["guess"]) if e.get("guess") else GUESS.get(fmt, 0.25)
        guess = clamp(guess * (1.3 - 0.15 * diff), 0.01, 0.6)   # hard questions are harder to guess
        slip = 0.05 + 0.03 * (diff - 1)                           # ...and easier to fumble
        p = s.p
        post_correct = p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)
        post_wrong = p * slip / (p * slip + (1 - p) * (1 - guess))
        post = evidence * post_correct + (1 - evidence) * post_wrong
        s.p = clamp(post + (1 - post) * self.learn_rate, 0.001, 0.999)

        # Memory (forgetting curve).
        confidence = e.get("confidence")
        rating = e.get("rating") or ("hard" if technique else
                                     derive_rating(result, confidence, e.get("seconds"), budget))
        if s.S is None or s.last is None:
            s.S = FIRST_STABILITY[rating]
        else:
            elapsed = max(0.0, days_between(s.last, ts))
            recall = (1 + elapsed / (9 * s.S)) ** -1
            if elapsed >= 1.0:
                self.reviews.append((recall, evidence))
                if score >= 0.5:
                    s.spaced_success = True
            if rating == "again":
                if s.score > 0:
                    s.lapses += 1
                s.S = clamp(s.S * 0.3, 0.2, 1.0)  # a lapse means relearning: back within a day
            elif elapsed < 0.5:  # massed practice in one sitting barely builds long-term memory
                s.S = min(MAX_INTERVAL, s.S * (1 + GROWTH[rating] * 0.15))
            else:                # spaced success; harder recalls build more
                growth = 1 + GROWTH[rating] * (1 + 2 * (1 - recall)) / (1 + 0.2 * s.lapses)
                s.S = min(MAX_INTERVAL, s.S * growth)

        s.first = s.first or ts
        s.last = ts
        s.attempts += 1
        s.score += score
        s.streak = s.streak + 1 if result == "correct" else 0
        s.last_result = result
        s.last_difficulty = diff
        s.last_confidence = int(confidence) if confidence else None
        s.last_error = error if result != "correct" else None
        s.misses_in_row = s.misses_in_row + 1 if (result == "incorrect" and not technique) else 0
        if s.mastered_at is None and not s.known_before and s.spaced_success and s.p >= MASTERED:
            s.mastered_at = ts
        note = str(e.get("misconception") or "").strip()
        if note and note not in s.misconceptions:
            s.misconceptions = (s.misconceptions + [note])[-5:]
        s.recent = (s.recent + [{
            "id": e.get("id"), "ts": e["ts"], "result": result, "format": fmt, "difficulty": diff,
            "question": e.get("question"), "misconception": note or None, "error": error,
        }])[-10:]

        self.by_format[fmt][0] += 1
        self.by_format[fmt][1] += score
        self.by_difficulty[diff][0] += 1
        self.by_difficulty[diff][1] += score
        if confidence:
            self.calibration.append((CONF_P[int(clamp(int(confidence), 1, 5))], score, ts))
        if first_try:
            self.first_tries.append((track, score))
        if technique:
            self.technique[error] += 1
        if e.get("seconds"):
            self.timings.append((float(e["seconds"]), budget))
        day = local_day(ts)
        self.activity[day] += 1
        self.active_days.add(day)

        self._track_mistakes(e, cid, result, ts)
        if result == "correct" and diff >= 4 and (confidence is None or int(confidence) >= 4):
            self._infer_prerequisites(cid, ts)

    def _track_mistakes(self, e: dict, cid: str, result: str, ts: datetime) -> None:
        mid, target = e.get("mistake"), e.get("targets")
        if target in self.mistakes:
            self.mistakes[target]["probes"] += 1
        if mid:
            m = self.mistakes.get(mid)
            if m is None:
                belief = str(e.get("misconception") or "").strip()
                text = belief or str(e.get("question") or "").strip()
                m = {"id": mid, "concept": cid, "track": e.get("track"), "text": text, "belief": bool(belief),
                     "error": e.get("error"), "confused_with": e.get("confused_with"),
                     "first_seen": e["ts"], "misses": 0, "probes": 0, "status": "open"}
                self.mistakes[mid] = m
                self.mistake_index[mistake_key(cid, e.get("misconception"), e.get("error"))] = mid
            m["misses"] += 1
            m["last_miss"] = e["ts"]
            m["status"] = "open"
            m.pop("fixed_at", None)
            m.pop("resolved_at", None)
            self.effects["mistake"] = mid
        if result != "correct":
            return
        if target:  # it re-asked one mistake, so it counts for that one only (for none if it was voided since)
            targets = [self.mistakes[target]] if target in self.mistakes else []
        else:  # an untargeted right answer still counts toward this concept's open mistakes
            targets = [m for m in self.mistakes.values() if m["concept"] == cid and m["status"] != "resolved"]
        for m in targets:
            if m["status"] == "resolved":
                continue
            if days_between(parse_ts(m["last_miss"]), ts) >= 1.0:
                m["status"], m["resolved_at"] = "resolved", e["ts"]
                self.effects["resolved"].append(m["id"])
            elif m["status"] == "open":
                m["status"], m["fixed_at"] = "fixing", e["ts"]
                self.effects["fixing"].append(m["id"])

    def _infer_prerequisites(self, cid: str, ts: datetime) -> None:
        """A hard question answered well is evidence for what it builds on (checked later)."""
        frontier = [(p, 1) for p in self.concepts.get(cid, {}).get("prereqs", [])]
        seen = set()
        while frontier:
            pid, depth = frontier.pop(0)
            if pid in seen or depth > 2:
                continue
            seen.add(pid)
            ps = self.state(pid)
            if not ps.touched and not ps.assessed:
                ps.p = max(ps.p, 0.75 if depth == 1 else 0.65)
                ps.S, ps.last = 3.0, ts
                ps.inferred = ps.known_before = True
                self.effects["inferred"].append(pid)
            frontier += [(q, depth + 1) for q in self.concepts.get(pid, {}).get("prereqs", [])]

    def _assess(self, e: dict, ts: datetime) -> None:
        s = self.state(e["concept"])
        level = clamp(float(e["level"]), 0.0, 1.0)
        s.p = level if s.attempts == 0 else 0.5 * s.p + 0.5 * level
        s.assessed = True
        if level >= MASTERED:
            s.known_before = True
        if level >= 0.7 and s.S is None:
            # Claimed knowledge gets checked by a real question in a few days.
            s.S, s.last = 4.0, ts

    # -- queries --
    def state(self, cid: str) -> ConceptState:
        s = self.states.get(cid)
        if s is None:
            s = ConceptState(cid, self.prior(cid))
            self.states[cid] = s
        return s

    def prior(self, cid: str) -> float:
        best = 0.0
        for rel in self.concepts.get(cid, {}).get("related", []):
            other = self.states.get(rel)
            if other is not None and other.touched:
                best = max(best, other.p)
        return max(PRIOR_UNKNOWN, round(0.5 * best, 3))

    def memory(self, s: ConceptState) -> float:
        if s.S is None or s.last is None:
            return 1.0
        elapsed = max(0.0, days_between(s.last, self.at))
        return (1 + elapsed / (9 * s.S * self.ff)) ** -1

    def mastery(self, s: ConceptState) -> float:
        return s.p * self.memory(s)

    def interval(self, s: ConceptState) -> float:
        return clamp(s.S * self.ff * 9 * (1 / self.retention - 1), 0.1, MAX_INTERVAL)

    def due_at(self, s: ConceptState):
        if s.S is None or s.last is None:
            return None
        return s.last + timedelta(days=self.interval(s))

    def is_due(self, s: ConceptState) -> bool:
        due = self.due_at(s)
        return due is not None and due <= self.at

    def label(self, s: ConceptState) -> str:
        if not s.touched:
            return "new"
        if self.is_due(s):
            return "due"
        if s.p >= MASTERED and (s.spaced_success or s.known_before):
            return "mastered"  # durable: survived a spaced check, or known before starting
        return "developing" if s.p >= DEVELOPING else "learning"

    def placement(self, slug=None) -> float:
        """Smoothed first-try accuracy: how much of a new area the learner already knows.

        A track starts from the learner's level elsewhere (other tracks, then the stated
        starting level) and moves with its own first answers. Each answer counts once."""
        def smooth(scores, prior):
            scores = scores[-30:]
            return (sum(scores) + PLACEMENT_WEIGHT * prior) / (len(scores) + PLACEMENT_WEIGHT)
        if slug is None:
            return smooth([score for _, score in self.first_tries], self.start_level)
        elsewhere = smooth([score for track, score in self.first_tries if track != slug], self.start_level)
        return smooth([score for track, score in self.first_tries if track == slug], elsewhere)

    def mode(self, slug=None) -> str:
        level = self.placement(slug)
        if level >= 0.70:
            return "top-down"   # probe advanced concepts first; a hit also credits what they build on
        if level < 0.45:
            return "primer"     # a short primer before each new concept, foundations first
        return "bottom-up"      # probe new concepts in order, once their prerequisites are in place

    def probe_difficulty(self, slug=None) -> int:
        return int(clamp(round(1 + 4 * self.placement(slug)), 2, 4))

    def difficulty(self, s: ConceptState, slug=None) -> int:
        """The next question's level: up after a confident hit, down after a real miss."""
        if s.attempts == 0:
            if s.S is not None:  # claimed or inferred knowledge: check it at that level
                return int(clamp(round(1 + 4 * s.p), 2, 5))
            return self.probe_difficulty(slug)
        d = s.last_difficulty or 3
        if s.last_result == "correct":
            if s.last_confidence is None or s.last_confidence >= 3:
                d += 1
        elif s.last_result == "incorrect" and s.last_error not in TECHNIQUE_ERRORS + ("misconception", "confusion"):
            d -= 1  # a gap steps down; a misconception is re-asked at the same level in a new form
        return int(clamp(d, 1, 5))

    def suggested_format(self, s: ConceptState, kind, mode="bottom-up") -> str:
        if not s.touched:
            return "short primer, then a question" if mode == "primer" else "probe: ask first, teach only if missed"
        if s.p < DEVELOPING:
            return "recall or mcq"
        if s.p < MASTERED:
            return "scenario or code"
        return "scenario (exam-style)" if kind == "certification" else "explain or apply"

    def depth(self, cid: str, visiting=None) -> int:
        """Length of the longest prerequisite chain under a concept."""
        if cid in self._depths:
            return self._depths[cid]
        visiting = visiting if visiting is not None else set()
        if cid in visiting:
            return 0
        visiting.add(cid)
        prereqs = self.concepts.get(cid, {}).get("prereqs", [])
        value = max((1 + self.depth(p, visiting) for p in prereqs), default=0)
        visiting.discard(cid)
        self._depths[cid] = value
        return value

    # -- mistakes --
    def mistake_due(self, m: dict) -> bool:
        if m["status"] == "resolved":
            return False
        if m["status"] == "open":
            return True
        return days_between(parse_ts(m["last_miss"]), self.at) >= 1.0  # fixing: re-check a day later

    def open_mistakes(self, concepts=None) -> list:
        rows = [m for m in self.mistakes.values() if m["status"] != "resolved"
                and (concepts is None or m["concept"] in concepts)]
        rows.sort(key=lambda m: (m["status"] != "open", m.get("error") != "misconception",
                                 -m["misses"], m["last_miss"]))
        return rows

    def assign_mistake(self, e: dict) -> None:
        """Give a miss its mistake id before it is written, so replay is deterministic.

        A question may still name a mistake whose answers were voided after it was written: it stays
        aimed at that mistake (so it credits no other), rather than being refused and re-recorded as an
        untargeted answer that would count toward every open mistake on the concept."""
        result, error = e["result"], e.get("error")
        for field in ("targets", "mistake"):
            value = e.get(field)
            if not value or value in self.mistakes:
                continue
            if field == "targets" and value in self.given_mistakes:
                continue  # its answers were voided: the question is still aimed at it
            raise PolymathError(f"Unknown mistake '{value}'. Run `mistakes` to see the ids.")
        if result == "correct" or error in TECHNIQUE_ERRORS:
            return
        if e.get("targets"):
            if result == "incorrect":
                e["mistake"] = e["targets"]
            return
        if e.get("mistake"):
            return
        text = str(e.get("misconception") or "").strip()
        if result == "partial" and not text and not error:
            return
        if not error:
            confident = int(e.get("confidence") or 0) >= 4
            error = "misconception" if confident else ("detail" if result == "partial" else "gap")
            e["error"] = error
        mid = self.mistake_index.get(mistake_key(e["concept"], text, error))
        if mid is None:
            self.max_mistake += 1
            mid = f"M{self.max_mistake}"
        e["mistake"] = mid

    def signal(self, e: dict) -> dict:
        """What this answer says, and what the tutor should do next."""
        s = self.state(e["concept"])
        result, error, conf = e["result"], e.get("error"), e.get("confidence")
        out = {"prereqs_to_check": []}
        if error in TECHNIQUE_ERRORS and result != "correct":
            out.update(signal=error, action="technique")
        elif result == "correct":
            if conf is not None and int(conf) <= 2:
                out.update(signal="fragile", action="confirm")
            else:
                out.update(signal="solid", action="advance")
        elif result == "partial":
            out.update(signal="partial", action="fill-gap")
        else:
            weak = [p for p in self.concepts.get(s.id, {}).get("prereqs", []) if self.state(p).p < DEVELOPING]
            # A wrong belief: one the learner stated, or a confident miss with no other reason given. An
            # explicit gap or detail ("I wasn't really sure") is taught, like the difficulty steps it down.
            misconception = error in ("misconception", "confusion") or (not error and conf is not None and int(conf) >= 4)
            out["signal"] = "misconception" if misconception else "gap"
            if misconception:
                out["action"] = "refute"  # a wrong belief: correct it, from a new angle if it recurs
            elif s.misses_in_row >= 2 and weak:
                out.update(action="check-prerequisites", prereqs_to_check=weak)  # repeated gaps: look underneath
            else:
                out["action"] = "teach"
        out["next_difficulty"] = self.difficulty(s, e.get("track"))
        return out

    def streak(self) -> int:
        today = local_day(self.at)
        day = today if today in self.active_days else today - timedelta(days=1)
        n = 0
        while day in self.active_days:
            n += 1
            day -= timedelta(days=1)
        return n

    def concept_view(self, cid: str) -> dict:
        s = self.state(cid)
        due = self.due_at(s)
        return {
            "concept": cid,
            "title": self.concepts.get(cid, {}).get("title", cid),
            "status": self.label(s),
            "mastery": round(self.mastery(s), 3),
            "knowledge": round(s.p, 3),
            "memory": round(self.memory(s), 3),
            "attempts": s.attempts,
            "accuracy": round(s.score / s.attempts, 3) if s.attempts else None,
            "next_review": iso(due) if due else None,
            "difficulty": self.difficulty(s),
            "misconceptions": s.misconceptions,
            "inferred": s.inferred and s.attempts == 0,
            "docs": self.concepts.get(cid, {}).get("docs", []),
        }

    # -- tracks --
    @staticmethod
    def track_rows(track: dict) -> list:
        rows = []
        for d in track.get("domains", []):
            ids = d.get("concepts", [])
            for cid in ids:
                rows.append((cid, d, float(d.get("weight", 0)) / max(1, len(ids))))
        return rows

    def plan(self, slugs: list, limit: int) -> dict:
        seen = {}
        for slug in slugs:
            track = self.store.track(slug)
            for cid, domain, weight in self.track_rows(track):
                if cid not in seen or weight > seen[cid][2]:
                    seen[cid] = (track, domain, weight)
        modes = {slug: self.mode(slug) for slug in slugs}
        level = statistics.mean(self.placement(slug) for slug in slugs)

        mistakes = [m for m in self.open_mistakes(set(seen)) if self.mistake_due(m)]
        with_mistake = {m["concept"] for m in mistakes}
        due, practice, probes, locked = [], [], [], []
        for cid, (track, domain, _) in seen.items():
            if cid in with_mistake:
                continue
            s = self.state(cid)
            if s.touched:
                if self.is_due(s):
                    due.append(cid)
                elif s.p < MASTERED and s.attempts > 0:  # claimed or inferred knowledge waits for its check
                    practice.append(cid)
                continue
            if modes[track["slug"]] != "top-down":
                missing = [pr for pr in self.concepts.get(cid, {}).get("prereqs", [])
                           if self.state(pr).p < DEVELOPING]
                if missing:
                    locked.append({"concept": cid, "waiting_on": missing})
                    continue
            probes.append(cid)
        due.sort(key=lambda c: (self.memory(self.state(c)), -seen[c][2]))
        practice.sort(key=lambda c: -seen[c][2] * (1 - self.state(c).p))
        index = {c: i for i, c in enumerate(seen)}

        def probe_key(c):
            track, domain, _ = seen[c]
            deep = -self.depth(c) if modes[track["slug"]] == "top-down" else 0
            return (-float(domain.get("weight", 0)), deep, index[c])

        probes.sort(key=probe_key)
        probes = interleave(probes, lambda c: (seen[c][0]["slug"], seen[c][1].get("id")))
        quota = int(clamp(round(4 + 8 * level), 3, 12))
        backlog = len(due) + len(mistakes)
        if backlog > 30:
            quota = 0
        elif backlog > 15:
            quota = max(1, quota // 3)
        new_now = probes[:quota]

        order = [(m["concept"], "mistake", m) for m in mistakes[:3]]
        order += [(c, "review", None) for c in due[:3]]
        per_practice = 2 if level >= 0.7 else 1
        i = j = 0
        while i < len(practice) or j < len(new_now):
            for _ in range(per_practice):
                if j < len(new_now):
                    order.append((new_now[j], "probe", None))
                    j += 1
            if i < len(practice):
                order.append((practice[i], "practice", None))
                i += 1
        order += [(m["concept"], "mistake", m) for m in mistakes[3:]]
        order += [(c, "review", None) for c in due[3:]]

        items = []
        for cid, kind, m in order[:limit]:
            track, domain, _ = seen[cid]
            s = self.state(cid)
            view = self.concept_view(cid)
            view.update({
                "kind": kind,
                "track": track["slug"],
                "domain": domain.get("name"),
                "objective": self.concepts.get(cid, {}).get("objective", ""),
                "difficulty": self.difficulty(s, track["slug"]),
                "format": self.suggested_format(s, track.get("kind"), modes[track["slug"]]),
                "misconceptions": s.misconceptions[-3:],
                "recent_questions": [r["question"] for r in s.recent if r.get("question")][-3:],
            })
            if m:
                view["mistake"] = {k: m.get(k) for k in ("id", "text", "belief", "error", "confused_with", "misses",
                                                         "status")}
                view["format"] = (f"contrast question: {cid} vs {m['confused_with']} (--targets {m['id']})"
                                  if m.get("confused_with") else f"re-ask it in a new form (--targets {m['id']})")
            items.append(view)
        return {
            "mode": modes[slugs[0]] if len(slugs) == 1 else modes,
            "placement": round(level, 3),
            "items": items,
            "mistakes_due": len(mistakes),
            "due_total": len(due),
            "practice_total": len(practice),
            "new_available": len(probes),
            "new_quota": quota,
            "locked": len(locked),
            "locked_sample": locked[:5],
        }

    def track_report(self, track: dict) -> dict:
        slug = track["slug"]
        domains, total_w, predicted = [], 0.0, 0.0
        states = []
        for d in track.get("domains", []):
            ss = [self.state(c) for c in d.get("concepts", [])]
            if not ss:
                continue
            states += ss
            masteries = [self.mastery(s) for s in ss]
            exam_p = statistics.mean(EXAM_GUESS + (1 - EXAM_GUESS - EXAM_SLIP) * m for m in masteries)
            weight = float(d.get("weight", 0))
            domains.append({
                "id": d.get("id"), "name": d.get("name"), "weight": weight,
                "concepts": len(ss),
                "started": sum(1 for s in ss if s.touched),
                "mastered": sum(1 for s in ss if self.label(s) == "mastered"),
                "due": sum(1 for s in ss if self.is_due(s)),
                "mastery": round(statistics.mean(masteries), 3),
                "exam_probability": round(exam_p, 3),
            })
            total_w += weight
            predicted += weight * exam_p
        total = len(states)
        started = sum(1 for s in states if s.touched)
        mastered = sum(1 for s in states if self.label(s) == "mastered")
        exam = track.get("exam") or {}
        passing = exam.get("passing_score")
        predicted_score = round(predicted / total_w, 3) if total_w else None
        coverage = started / total if total else 0.0
        all_mocks = sorted(self.mocks.get(slug, []), key=lambda m: m["ts"])
        mocks = all_mocks[-3:]
        full = [m for m in all_mocks if is_full_mock(m, exam)]

        verdict = None
        if passing is not None and predicted_score is not None:
            # compared as the report shows them and as booking_gate checks them (rounded), so the two agree
            book_at, mock_at = readiness_bars(passing)
            last_mock = full[-1]["score"] if full else None
            if (round(predicted_score, 4) >= book_at and round(coverage, 3) >= 0.9
                    and (last_mock is None or round(last_mock, 4) >= mock_at)):
                verdict = "ready"
            elif round(predicted_score, 4) >= round(passing, 4):
                verdict = "close"
            else:
                verdict = "not yet"

        projection = self._projection(track, states, total, mastered)
        mistakes_open = len(self.open_mistakes({c for c, _, _ in self.track_rows(track)}))
        return {
            "slug": slug, "title": track.get("title"), "goal": track.get("goal"),
            "kind": track.get("kind"), "status": track.get("status", "active"),
            "target_date": track.get("target_date"),
            "concepts": total, "started": started, "mastered": mastered,
            "coverage": round(coverage, 3),
            "due": sum(1 for s in states if self.is_due(s)),
            "predicted_score": predicted_score, "passing_score": passing, "verdict": verdict,
            "mocks": [{"date": local_day(parse_ts(m["ts"])).isoformat(), "score": m["score"], "by_domain": m.get("by_domain"),
                       "questions": m.get("questions"), "minutes": m.get("minutes"), "full": is_full_mock(m, exam)}
                      for m in mocks],
            "full_mocks": [{"date": local_day(parse_ts(m["ts"])).isoformat(), "score": m["score"], "questions": m.get("questions")}
                           for m in full[-3:]],
            "domains": domains, "projection": projection,
            "mistakes_open": mistakes_open, "placement": round(self.placement(slug), 3), "mode": self.mode(slug),
        }

    def _projection(self, track, states, total, mastered) -> dict:
        today = local_day(self.at)
        target = track.get("target_date")
        days_left = (parse_date(target) - today).days if target else None
        firsts = [s.first for s in states if s.first]
        out = {"days_left": days_left, "remaining": total - mastered, "velocity_per_day": None,
               "finish_estimate": None, "needed_per_day": None, "status": "too early to tell"}
        if days_left is not None and total - mastered > 0:
            reserve = min(7, max(0, int(days_left * 0.15)))  # keep the last days for mocks and review
            out["needed_per_day"] = round((total - mastered) / max(1, days_left - reserve), 2)
        if not firsts:
            return out
        history = days_between(min(firsts), self.at)
        if history < 3 or sum(s.attempts for s in states) < 15:  # too little to judge pace
            return out
        window = min(14.0, history)
        recent = sum(1 for s in states if s.mastered_at and days_between(s.mastered_at, self.at) <= window)
        velocity = recent / window
        out["velocity_per_day"] = round(velocity, 2)
        if total - mastered == 0:
            out["status"] = "complete"
            return out
        if velocity <= 0:
            out["status"] = "stalled" if days_left is None else "behind"
            return out
        need_days = (total - mastered) / velocity
        out["finish_estimate"] = (today + timedelta(days=int(round(need_days)))).isoformat()
        if days_left is None:
            out["status"] = "no target date"
        elif need_days <= days_left * 0.85:
            out["status"] = "ahead"
        elif need_days <= days_left:
            out["status"] = "on track"
        else:
            out["status"] = "behind"
        return out

    def learner_report(self) -> dict:
        attempts = [e for e in self.events if e.get("type") == "attempt"]
        recent_cal = self.calibration[-50:]
        calibration = None
        if len(recent_cal) >= 8:
            stated = statistics.mean(c for c, _, _ in recent_cal)
            actual = statistics.mean(a for _, a, _ in recent_cal)
            calibration = {
                "stated": round(stated, 3), "actual": round(actual, 3), "gap": round(stated - actual, 3),
                "confident_misses": sum(1 for c, a, _ in self.calibration[-30:] if c >= 0.8 and a == 0.0),
                "n": len(recent_cal),
            }
        touched = [s for s in self.states.values() if s.attempts >= 2]
        ranked = sorted(touched, key=self.mastery)
        first_seen = min((e["ts"] for e in self.events), default=None)
        return {
            "name": self.learner.get("name"),
            "persona": self.learner.get("persona", {}),
            "since": local_day(parse_ts(first_seen)).isoformat() if first_seen else None,
            "attempts": len(attempts),
            "accuracy": round(statistics.mean(SCORE[e["result"]] for e in attempts), 3) if attempts else None,
            "pace": round(self.pace, 2), "pace_samples": self.pace_n,
            "memory_factor": round(self.ff, 2), "memory_samples": self.ff_n,
            "calibration": calibration,
            "by_format": {k: {"n": n, "accuracy": round(t / n, 3)} for k, (n, t) in sorted(self.by_format.items())},
            "by_difficulty": {str(k): {"n": n, "accuracy": round(t / n, 3)} for k, (n, t) in sorted(self.by_difficulty.items())},
            "weakest": [self.concept_view(s.id) for s in ranked if self.mastery(s) < MASTERED][:5],
            "strongest": [self.concept_view(s.id) for s in ranked[::-1] if self.mastery(s) >= DEVELOPING][:5],
            "streak": self.streak(), "active_days": len(self.active_days),
            "session_hours": round(self.session_minutes / 60.0, 1),
            "due_now": sum(1 for s in self.states.values() if self.is_due(s)),
            "placement": round(self.placement(), 3), "mode": self.mode(),
            "technique": {
                "slips": self.technique["slip"], "traps": self.technique["trap"],
                "median_seconds": round(statistics.median(t for t, _ in self.timings), 1) if self.timings else None,
                "budget_seconds": round(statistics.median(b for _, b in self.timings), 1) if self.timings else None,
                "timed_answers": len(self.timings),
            },
            "mistakes": {
                "open": sum(1 for m in self.mistakes.values() if m["status"] == "open"),
                "fixing": sum(1 for m in self.mistakes.values() if m["status"] == "fixing"),
                "resolved": sum(1 for m in self.mistakes.values() if m["status"] == "resolved"),
                "top": self.open_mistakes()[:5],
            },
        }


# --- track import / validation ----------------------------------------------
def validate_syllabus(data: dict, registry: dict, slug: str) -> tuple:
    errors, warnings = [], []
    if not SLUG_RE.match(slug or ""):
        errors.append(f"slug '{slug}' must be lowercase letters, digits and hyphens (2-64 chars).")
    if not str(data.get("title") or "").strip():
        errors.append("title is required.")
    kind = data.get("kind", "general")
    domains = data.get("domains")
    if not isinstance(domains, list) or not domains:
        errors.append("domains must be a non-empty list.")
        return errors, warnings, {}
    concepts, domain_ids = {}, set()
    for di, d in enumerate(domains):
        where = f"domains[{di}]"
        if not d.get("id") or not d.get("name"):
            errors.append(f"{where} needs an id and a name.")
        elif d["id"] in domain_ids:
            errors.append(f"{where}: duplicate domain id '{d['id']}'.")
        domain_ids.add(d.get("id"))
        try:
            if float(d.get("weight", 0)) <= 0:
                errors.append(f"{where} '{d.get('name')}': weight must be > 0.")
        except (TypeError, ValueError):
            errors.append(f"{where}: weight must be a number.")
        items = d.get("concepts")
        if not isinstance(items, list) or not items:
            errors.append(f"{where} '{d.get('name')}': concepts must be a non-empty list.")
            continue
        for ci, c in enumerate(items):
            cw = f"{where}.concepts[{ci}]"
            cid = c.get("id") if isinstance(c, dict) else None
            if not cid or not CONCEPT_RE.match(cid):
                errors.append(f"{cw}: id '{cid}' must be dotted lowercase, e.g. 'salesforce.apex.triggers'.")
                continue
            if cid in concepts:
                errors.append(f"{cw}: concept '{cid}' appears twice in this track.")
                continue
            if not str(c.get("title") or "").strip():
                errors.append(f"{cw} '{cid}': title is required.")
            concepts[cid] = c
    if errors:
        return errors, warnings, concepts

    weights = [float(d["weight"]) for d in domains]
    total = sum(weights)
    if kind == "certification" and abs(total - 1.0) > 0.02:
        errors.append(f"domain weights sum to {total:.2f}; an exam blueprint's weights must sum to 1.0.")
    elif abs(total - 1.0) > 0.001:
        warnings.append(f"domain weights summed to {total:.2f}; normalized to 1.0.")
        for d in domains:
            d["weight"] = round(float(d["weight"]) / total, 4)

    known = set(registry) | set(concepts)
    graph = {cid: set(info.get("prereqs", [])) for cid, info in registry.items()}
    for cid, c in concepts.items():
        for field in ("prereqs", "related"):
            for ref in c.get(field, []) or []:
                if ref not in known:
                    errors.append(f"concept '{cid}': {field} '{ref}' is not defined here or in your concept registry.")
                if ref == cid:
                    errors.append(f"concept '{cid}' lists itself in {field}.")
        graph.setdefault(cid, set()).update(c.get("prereqs", []) or [])
        existing = registry.get(cid)
        if existing and existing.get("title") != c.get("title"):
            warnings.append(f"'{cid}' already exists as '{existing.get('title')}'; keeping that title (it is shared across tracks).")
    for cid, c in concepts.items():
        docs = c.get("docs", [])
        if docs and (not isinstance(docs, list)
                     or not all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in docs)):
            errors.append(f"concept '{cid}': docs must be a list of http(s) links.")
    cycle = find_cycle(graph)
    if cycle:
        errors.append("prerequisite cycle: " + " -> ".join(cycle))
    return errors, warnings, concepts


def find_cycle(graph: dict):
    """Kahn's algorithm; returns the concepts left in a cycle, or None."""
    nodes = set(graph) | {p for ps in graph.values() for p in ps}
    indegree = {n: 0 for n in nodes}
    for node, prereqs in graph.items():
        indegree[node] += len(prereqs)
    dependents = defaultdict(list)
    for node, prereqs in graph.items():
        for p in prereqs:
            dependents[p].append(node)
    queue = [n for n, deg in indegree.items() if deg == 0]
    done = 0
    while queue:
        n = queue.pop()
        done += 1
        for m in dependents[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)
    if done == len(nodes):
        return None
    return sorted(n for n, deg in indegree.items() if deg > 0)


def import_track(store: Store, data: dict, slug=None, target_date=None) -> dict:
    with store.writing():
        return _import_track(store, data, slug, target_date)


def _import_track(store: Store, data: dict, slug=None, target_date=None) -> dict:
    slug = slug or data.get("slug")
    if target_date:
        data["target_date"] = target_date
    if data.get("target_date"):
        parse_date(data["target_date"])
    registry = store.concepts()
    errors, warnings, concepts = validate_syllabus(data, registry, slug)
    if errors:
        raise PolymathError("Syllabus rejected:\n  - " + "\n  - ".join(errors))

    previous = read_json(store.track_path(slug), None)
    for cid, c in concepts.items():
        entry = registry.get(cid, {"title": c["title"], "created": iso(now())})
        entry["title"] = entry.get("title") or c["title"]
        for field in ("tags", "prereqs", "related"):
            entry[field] = sorted(set(entry.get(field, [])) | set(c.get(field, []) or []))
        if c.get("objective") and not entry.get("objective"):
            entry["objective"] = c["objective"]
        for url in c.get("docs", []) or []:
            entry.setdefault("docs", [])
            if url not in entry["docs"]:
                entry["docs"].append(url)
        entry["tracks"] = sorted(set(entry.get("tracks", [])) | {slug})
        registry[cid] = entry
    for cid, entry in registry.items():  # concepts dropped from this track on re-import
        if cid not in concepts and slug in entry.get("tracks", []):
            entry["tracks"] = [t for t in entry["tracks"] if t != slug]

    track = {
        "schema": SCHEMA,
        "slug": slug,
        "title": data["title"].strip(),
        "short": str(data.get("short") or "").strip()[:16] or None,
        "goal": data.get("goal", ""),
        "kind": data.get("kind", "general"),
        "target_date": data.get("target_date"),
        "daily_minutes": data.get("daily_minutes"),
        "target_mastery": data.get("target_mastery", MASTERED),
        "exam": data.get("exam"),
        "sources": data.get("sources", []),
        "status": (previous or {}).get("status", "active"),
        "created": (previous or {}).get("created", iso(now())),
        "updated": iso(now()),
        "domains": [
            {"id": d["id"], "name": d["name"], "weight": float(d["weight"]),
             "concepts": [c["id"] for c in d["concepts"]]}
            for d in data["domains"]
        ],
    }
    store.save_concepts(registry)
    store.save_track(track)
    store.append({"type": "track_import", "track": slug, "concepts": len(concepts)})
    return {"track": slug, "concepts": len(concepts), "domains": len(track["domains"]),
            "replaced": previous is not None, "warnings": warnings}


# --- web dashboard ------------------------------------------------------------------
def load_web():
    """Import the dashboard server (scripts/polymath_web.py) against this same engine module."""
    sys.modules.setdefault("polymath", sys.modules[__name__])
    folder = str(Path(__file__).resolve().parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import polymath_web
    return polymath_web


# --- changes shared by the CLI and the dashboard ---------------------------------------
TRACK_SETTABLE = ("title", "short", "goal", "target_date", "status", "daily_minutes", "target_mastery", "kind")


def positive_number(name: str, value, upper=None):
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 \
            or (upper and value > upper):
        raise PolymathError(f"{name} must be a positive number{f' up to {upper}' if upper else ''}.")
    return value


def ensure_gitignore(root: Path, names) -> None:
    """Add names to the data folder's .gitignore (keeping its last line intact), so private files are
    never committed if the folder becomes a git repository."""
    path = Path(root) / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError):
        return
    have = set(text.split())
    missing = [n for n in dict.fromkeys(names) if n not in have]
    if not missing:
        return
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + "".join(n + "\n" for n in missing))
    except OSError:
        pass


def update_track(store: Store, slug: str, changes: dict, raw=None, source=None) -> dict:
    """Change a track's settings (not its syllabus), and log the change (source "web" when the
    dashboard made it, so the tutor's undo leaves it alone)."""
    with store.writing():
        return _update_track(store, slug, changes, raw, source)


def _update_track(store: Store, slug: str, changes: dict, raw=None, source=None) -> dict:
    track = store.track(slug)
    for key, value in changes.items():
        if key not in TRACK_SETTABLE:
            raise PolymathError(f"Can't set '{key}'. Settable: {', '.join(sorted(TRACK_SETTABLE))}. "
                                "To change the syllabus, edit it and re-run `track import`.")
        if key == "target_date" and value not in (None, ""):
            parse_date(str(value))
        if key == "status" and value not in TRACK_STATUSES:
            raise PolymathError(f"status must be one of {', '.join(TRACK_STATUSES)}.")
        if key == "daily_minutes":
            value = positive_number("daily_minutes", value, upper=1440)
        track[key] = value
    track["updated"] = iso(now())
    store.save_track(track)
    event = {"type": "track_update", "track": slug,
             "changes": raw or [f"{k}={'' if v is None else v}" for k, v in changes.items()]}
    if source:
        event["source"] = source
    store.append(event)
    return track


PREFERENCE_RANGES = {  # what the dashboard (and the coach's one-click proposals) may change
    "session_minutes": (int, 10, 480), "block_size": (int, 1, 5), "desired_retention": (float, 0.80, 0.97),
    "web_daily_usd": (float, 0.5, 200.0), "web_daily_calls": (int, 10, 5000),
}


def set_preferences(store: Store, changes: dict) -> dict:
    """Change the learner's preferences, each checked against its allowed range."""
    if not isinstance(changes, dict) or not changes:
        raise PolymathError("Nothing to change.")
    with store.writing():
        learner = store.learner()
        prefs = learner.setdefault("preferences", {})
        for key, raw in changes.items():
            if key == "start_level":
                value = str(raw or "").strip().lower()
                if value not in START_LEVELS:
                    raise PolymathError(f"start_level must be one of {', '.join(START_LEVELS)}.")
            elif key in PREFERENCE_RANGES:
                kind, low, high = PREFERENCE_RANGES[key]
                try:
                    value = float(raw)
                except (TypeError, ValueError, OverflowError):
                    value = math.nan
                if isinstance(raw, bool) or not math.isfinite(value) or not low <= value <= high:
                    raise PolymathError(f"{key} must be a number from {low:g} to {high:g}.")
                value = int(round(value)) if kind is int else round(value, 3)
            else:
                raise PolymathError(f"Can't set '{key}' here. Settable: "
                                    f"{', '.join(sorted(list(PREFERENCE_RANGES) + ['start_level']))}.")
            prefs[key] = value
        store.save_learner(learner)
        return dict(prefs)


def log_mock(store: Store, slug: str, score, by_domain=None, questions=None, minutes=None, full=None,
             source=None, session=None, at=None) -> dict:
    """Record a mock exam (one taken with the tutor, or elsewhere). A dashboard session's mock is logged
    once: asked again with the same session id, this returns the mock already in the log. Either way the
    result is the mock as logged, with every field (None where it has none) and its ts. `at` dates it when
    it was sat (never later than now); the readiness checks take mocks in date order."""
    with store.writing():
        if session:
            logged = next((e for e in reversed(store.events())
                           if e.get("type") == "mock" and e.get("session") == session), None)
            if logged is not None:
                return mock_record(logged)
        when = answer_time(at) if at is not None else None
        return _log_mock(store, slug, score, by_domain, questions, minutes, full, source, session, when)


MOCK_FIELDS = ("ts", "type", "track", "score", "questions", "minutes", "by_domain", "full", "source", "session")


def mock_record(e: dict) -> dict:
    """A logged mock with every field it can have, None where it has none: one shape for callers."""
    return {key: e.get(key) for key in MOCK_FIELDS}


def _log_mock(store: Store, slug: str, score, by_domain, questions, minutes, full, source, session,
              when=None) -> dict:
    track = store.track(slug)
    if by_domain is not None and not isinstance(by_domain, dict):
        raise PolymathError('by_domain must map area ids to scores, such as {"basics": 0.8}.')
    areas = {d.get("id") for d in track.get("domains", [])}
    scores = {}
    for key, raw in (by_domain or {}).items():
        if key not in areas:
            raise PolymathError(f"No area '{key}' in {slug}. Areas: {', '.join(sorted(a for a in areas if a))}.")
        scores[key] = as_fraction(raw)
    event = {"type": "mock", "track": slug, "score": as_fraction(score),
             "questions": positive_number("questions", questions), "minutes": positive_number("minutes", minutes),
             "by_domain": scores or None, "full": full if isinstance(full, bool) else None, "source": source,
             "session": session}
    if when is not None:
        event["ts"] = iso(when)
    return mock_record(store.append(event)[0])


def void_attempt(store: Store, attempt=None, concept=None, reason=None, source=None, session=None) -> dict:
    """Remove a disputed answer (a wrong question or answer key) from the learner model."""
    with store.writing():
        return _void_attempt(store, attempt, concept, reason, source, session)


def _void_attempt(store: Store, attempt, concept, reason, source=None, session=None) -> dict:
    events = store.events()
    voided = {e.get("target") for e in events if e.get("type") == "void"}
    attempts = [e for e in events if e.get("type") == "attempt" and attempt_key(e) not in voided]
    if attempt:
        matches = [e for e in attempts if e.get("id") == attempt]
    else:  # the most recent by when it was given (one can be logged late), the tutor's own first, as undo does
        matches = [e for e in attempts if not concept or e.get("concept") == concept]
        matches = in_time_order([e for e in matches if not dashboard_record(e)] or matches)
    if not matches:
        raise PolymathError("No matching attempt to void (it may already be voided).")
    target = matches[-1]
    before = Model(store)
    b = before.mastery(before.state(target["concept"]))
    store.append({"type": "void", "target": attempt_key(target), "concept": target["concept"], "reason": reason,
                  "source": source, "session": session})
    after = Model(store)
    a = after.mastery(after.state(target["concept"]))
    return {"voided": attempt_key(target), "attempt": target.get("id"), "concept": target["concept"],
            "before": round(b, 3), "after": round(a, 3)}


# --- commands -------------------------------------------------------------------
def emit(args, data, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        print(text)


def concept_or_fail(registry: dict, cid: str, field: str = "concept") -> dict:
    if not isinstance(cid, str) or not cid.strip():
        raise PolymathError(f"{field} must be a concept id, such as salesforce.apex.triggers (got {shown(cid)}).")
    if cid in registry:
        return registry[cid]
    hints = difflib.get_close_matches(cid, list(registry), n=3, cutoff=0.5)
    raise PolymathError(f"Unknown concept '{cid}'." + (f" Close matches: {', '.join(hints)}." if hints else "")
                        + " Use `search <words>` to find ids.")


LEARNER_MD = """# Learner profile: {name}

_The tutor reads this at the start of every session and updates it when it learns
something durable about how you learn. Edit it freely._

## Background and existing knowledge

## Goals and motivation

## How I learn best

## Recurring misconceptions and weak spots

## Coaching notes
"""

HOME_README = """# Your Polymath data

Everything the tutor knows about your learning lives here, as plain files.

- `learner.json`: your preferences (session length, retention target, tutor persona).
- `learner.md`: the tutor's notes on how you learn. Edit it freely.
- `events.jsonl`: every answer, lesson, session and mock exam, append-only. This is
  the source of truth; mastery and review dates are recomputed from it.
- `concepts.json`: every concept you have studied, shared across tracks.
- `tracks/<slug>/`: one folder per goal (`track.json`, `plan.md`, `notes.md`).
- `journal/`: a dated log of your sessions.

Back it up: `cd ~/.polymath && git init && git add -A && git commit -m progress`.
"""


def init_learner(store: Store, name: str = None, persona_name: str = None, start_level: str = None) -> tuple:
    """Create the learner profile, or update it. Returns (learner, created). Shared by `init` and the dashboard."""
    if start_level is not None and start_level not in START_LEVELS:
        raise PolymathError(f"start_level must be one of {', '.join(START_LEVELS)}.")
    private_dir(store.root, tighten=True)  # the learning record is yours alone, an older folder too
    with store.writing():  # the dashboard may be saving learner.json at this moment: take turns
        for d in (store.tracks_dir, store.journal_dir):
            d.mkdir(exist_ok=True)
        learner = store.learner()
        created = not learner
        learner.setdefault("schema", SCHEMA)
        learner.setdefault("created", iso(now()))
        if name:
            learner["name"] = name
        learner.setdefault("name", "Learner")
        persona = learner.setdefault("persona", {})
        if persona_name:
            persona["name"] = persona_name
        persona.setdefault("name", "Sage")
        persona.setdefault("tone", "warm, direct and concise")
        prefs = learner.setdefault("preferences", {})
        prefs.setdefault("session_minutes", 30)
        prefs.setdefault("desired_retention", 0.9)
        prefs.setdefault("use_question_ui", True)
        prefs.setdefault("start_level", "intermediate")
        prefs.setdefault("block_size", 1)
        if start_level:
            prefs["start_level"] = start_level
        store.save_learner(learner)
        if not store.profile_md.exists():
            store.profile_md.write_text(LEARNER_MD.format(name=learner["name"]), encoding="utf-8")
        readme = store.root / "README.md"
        if not readme.exists():
            readme.write_text(HOME_README, encoding="utf-8")
    return learner, created


def cmd_init(args, store: Store):
    learner, created = init_learner(store, args.name, args.persona)
    persona = learner["persona"]
    data = {"home": str(store.root), "created": created, "learner": learner}
    verb = "Created" if created else "Updated"
    emit(args, data, f"{verb} profile for {learner['name']} (tutor: {persona['name']}) in {store.root}")


def cmd_where(args, store: Store):
    emit(args, {"home": str(store.root), "ready": store.ready()}, str(store.root))


def cmd_brief(args, store: Store):
    """Compact status for injecting into a skill. Never fails."""
    try:
        if not store.ready():
            print(f"Polymath is not set up yet (no profile in {store.root}). "
                  "Onboard the learner: ask their name and what they want to learn, then run `init`.")
            return
        model = Model(store)
        learner = model.learner
        prefs = learner.get("preferences", {})
        lines = [f"Learner: {learner.get('name')} · tutor persona: {learner.get('persona', {}).get('name', 'Sage')} · "
                 f"streak {model.streak()} days · {model.activity.get(local_day(model.at), 0)} answers today · "
                 f"today is {local_day(model.at).isoformat()} · session {prefs.get('session_minutes', 30)} min · "
                 f"question blocks of {prefs.get('block_size', 1)}"]
        slugs = store.slugs()
        if not slugs:
            lines.append("No tracks yet. Ask what they want to learn and run the onboarding flow.")
        due_all = 0
        for slug in slugs:
            rep = model.track_report(store.track(slug))
            due_all += rep["due"]
            bits = [f"- {slug}: \"{rep['title']}\" [{rep['status']}]"]
            if rep["target_date"]:
                bits.append(f"target {rep['target_date']} ({rep['projection']['days_left']}d left)")
            if rep["predicted_score"] is not None and rep["passing_score"] is not None:
                bits.append(f"readiness {pct(rep['predicted_score'])} vs pass {pct(rep['passing_score'])} ({rep['verdict']})")
            bits.append(f"mastered {rep['mastered']}/{rep['concepts']}, started {rep['started']}, due {rep['due']}, "
                        f"open mistakes {rep['mistakes_open']}")
            bits.append(f"placement {rep['mode']} ({pct(rep['placement'])} first-try)")
            if rep["projection"]["status"] != "too early to tell":
                bits.append(f"pace: {rep['projection']['status']}")
            lines.append(" · ".join(bits))
        if slugs:
            lines.append(f"Reviews due across all tracks: {due_all}")
            top = model.open_mistakes()[:3]
            if top:
                lines.append("Open mistakes to fix first: " + "; ".join(
                    f"{m['id']} {m['concept']}: {(m['text'] or m.get('error') or '')[:90]}" for m in top))
        lines.append(f"Data: {store.root}")
        if store.profile_md.exists():
            notes = [l for l in store.profile_md.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith("_")]
            lines.append("Learner notes (learner.md; add to them with `note`):")
            lines.extend(notes[:80])
        print("\n".join(lines))
    except Exception as exc:  # the brief must never break a skill
        print(f"Polymath brief unavailable ({type(exc).__name__}: {exc}). Run `status` to see the error.")


def cmd_track_import(args, store: Store):
    store.require()
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolymathError(f"Syllabus is not valid JSON: {exc}")
    result = import_track(store, data, slug=args.slug, target_date=args.target_date)
    text = (f"{'Updated' if result['replaced'] else 'Created'} track '{result['track']}': "
            f"{result['concepts']} concepts in {result['domains']} areas.")
    if result["warnings"]:
        text += "\nNotes:\n  - " + "\n  - ".join(result["warnings"])
    emit(args, result, text)


def cmd_track_list(args, store: Store):
    store.require()
    model = Model(store)
    reports = [model.track_report(store.track(s)) for s in store.slugs()]
    lines = []
    for r in reports:
        ready = f" · readiness {pct(r['predicted_score'])}" if r["passing_score"] is not None else ""
        target = f" · target {r['target_date']}" if r["target_date"] else ""
        lines.append(f"{r['slug']:<28} {r['status']:<7} mastered {r['mastered']}/{r['concepts']}{ready}{target} · due {r['due']}")
    emit(args, reports, "\n".join(lines) or "No tracks yet.")


def cmd_track_show(args, store: Store):
    store.require()
    model = Model(store)
    track = store.track(args.slug)
    out = {"track": track["slug"], "title": track.get("title"), "domains": []}
    lines = [f"{track.get('title')} [{track['slug']}]"]
    for d in track.get("domains", []):
        if args.domain and args.domain not in (d.get("id"), d.get("name")):
            continue
        views = [model.concept_view(c) for c in d.get("concepts", [])]
        out["domains"].append({"id": d["id"], "name": d["name"], "weight": d["weight"], "concepts": views})
        lines.append(f"\n{d['name']} ({pct(d['weight'])})")
        for v in views:
            nxt = f" · review {local_day(parse_ts(v['next_review'])).isoformat()}" if v["next_review"] else ""
            lines.append(f"  {v['status']:<10} {pct(v['mastery']):>4}  {v['concept']}  {v['title']}"
                         f" · {v['attempts']} tries{nxt}")
    emit(args, out, "\n".join(lines))


def cmd_track_set(args, store: Store):
    store.require()
    changes = {}
    for pair in args.pairs:
        if "=" not in pair:
            raise PolymathError(f"'{pair}' should look like key=value.")
        key, raw = pair.split("=", 1)
        changes[key] = parse_value(raw)
    track = update_track(store, args.slug, changes, raw=args.pairs)
    emit(args, track, f"Updated {args.slug}: {', '.join(args.pairs)}")


def cmd_next(args, store: Store):
    store.require()
    if args.track:
        slugs = [args.track]
    else:
        slugs = [s for s in store.slugs() if store.track(s).get("status", "active") == "active"]
    if not slugs:
        raise PolymathError("No active tracks. Create one with `track import`.")
    model = Model(store)
    plan = model.plan(slugs, args.limit)
    mode = plan["mode"] if isinstance(plan["mode"], str) else ", ".join(f"{k} {v}" for k, v in plan["mode"].items())
    lines = [f"Session plan ({', '.join(slugs)}) · mode {mode} · first-try accuracy {pct(plan['placement'])}"]
    for n, it in enumerate(plan["items"], 1):
        if it.get("mistake"):
            m = it["mistake"]
            extra = f" · {m['id']} ({m['error']}): {m['text']}"
        elif it["misconceptions"]:
            extra = f" · watch for: {it['misconceptions'][-1]}"
        else:
            extra = ""
        lines.append(f"{n:>2}. [{it['kind']}] {it['concept']} ({it['title']}) · mastery {pct(it['mastery'])}"
                     f" · difficulty {it['difficulty']}/5 · {it['format']}{extra}")
    lines.append(f"Mistakes due {plan['mistakes_due']} · reviews due {plan['due_total']} · in progress "
                 f"{plan['practice_total']} · new to probe {plan['new_available']} (this session {plan['new_quota']})"
                 f" · locked by prerequisites {plan['locked']}")
    if not plan["items"]:
        lines.append("Nothing scheduled: every concept is mastered and nothing is due. Suggest a mock exam or a new goal.")
    emit(args, plan, "\n".join(lines))


NAME_FIELDS = ("qtype", "format", "rating", "error", "confused_with", "targets", "mistake", "track", "ref",
               "source", "session")   # fields that name something, so must be text; free text is made text


def build_attempt(item: dict, registry: dict) -> dict:
    """Check one answer (from the CLI or a batch) and turn it into the event to log. A field of the wrong
    kind is a PolymathError naming the field, never a crash."""
    cid = item.get("concept")
    concept_or_fail(registry, cid)
    result = normalize_result(item.get("result", ""))
    names = {key: name_field(key, item.get(key)) for key in NAME_FIELDS}
    fmt = names["qtype"] or names["format"] or "mcq"
    if fmt not in GUESS:
        raise PolymathError(f"qtype must be one of {', '.join(GUESS)} (got '{fmt}').")
    rating = names["rating"]
    if rating and rating not in FIRST_STABILITY:
        raise PolymathError("rating must be again, hard, good or easy.")
    error = names["error"]
    if error and error not in ERRORS:
        raise PolymathError(f"error must be one of {', '.join(ERRORS)} (got '{error}').")
    if names["confused_with"]:
        concept_or_fail(registry, names["confused_with"], "confused_with")
    for field in ("targets", "mistake"):
        if names[field] and not MISTAKE_RE.match(names[field]):
            raise PolymathError(f"{field} must be a mistake id like M3 (got '{names[field]}').")
    event = {"type": "attempt", "concept": cid, "result": result, "qtype": fmt,
             "difficulty": whole_number("difficulty", item.get("difficulty"), 1, 5, default=3),
             "track": names["track"]}
    confidence = whole_number("confidence", item.get("confidence"), 1, 5)
    if confidence is not None:
        event["confidence"] = confidence
    if item.get("options"):
        event["guess"] = round(1.0 / whole_number("options", item["options"], 2), 3)
    if item.get("seconds") not in (None, ""):
        try:
            seconds = float(item["seconds"])
        except (TypeError, ValueError, OverflowError):
            seconds = None
        if seconds is not None and math.isfinite(seconds) and seconds > 0:
            event["seconds"] = int(round(min(seconds, 86400)))
    for key in ("rating", "misconception", "question", "note", "error", "confused_with",
                "targets", "mistake", "ref", "source", "session"):
        value = names[key] if key in names else item.get(key)
        if value not in (None, ""):
            event[key] = value if key in names else str(value)
    if event.get("question"):
        event["question"] = event["question"][:300]
    return event


def record_events(store: Store, events: list, at=None) -> list:
    """Write answers and say, for each, what changed and what the tutor should do next.

    Everything is computed before anything is written, so a bad item leaves the log untouched. An answer
    whose ref is already in the log (or earlier in the batch) is not written again. `at` (a timezone-aware
    datetime) stamps the answers with when they were given instead of now (never later than now), and the
    results are worked out as of that moment, from what happened before it: the model replays the log in
    time order, so whatever was logged since doesn't move it."""
    with store.writing():
        return _record_events(store, events, at)


def answer_time(at) -> datetime:
    """The moment to stamp answers given at `at`: as asked, but no later than now, to the second (as the log
    keeps time). A naive datetime is UTC."""
    if isinstance(at, str):
        try:
            when = parse_ts(at)
        except ValueError:
            raise PolymathError(f"'{at}' is not a date and time; use ISO 8601, such as 2026-11-18T09:30:00Z.")
    elif isinstance(at, datetime):
        when = at
    else:
        raise TypeError(f"at must be a datetime or an ISO 8601 string, not {type(at).__name__}")
    when = min(when if when.tzinfo else when.replace(tzinfo=timezone.utc), now())
    return parse_ts(iso(when))


def _record_events(store: Store, events: list, at=None) -> list:
    logged = store.events()
    if at is None:  # given now: after everything logged, even if the clock has stepped back since
        when = now()
        for e in logged:
            try:
                when = max(when, parse_ts(e["ts"]))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
    else:
        when = answer_time(at)
    model = Model(store, at=when, events=logged, until=when if at is not None else None)
    stamp = iso(when)
    results, fresh = [], []
    seen = {e.get("ref"): e for e in logged if e.get("type") == "attempt" and e.get("ref")}
    for e in events:  # an answer with a ref already in the log (or in this batch) was recorded before: skip it
        if e.get("ref") and e["ref"] in seen:
            results.append(seen[e["ref"]])
        else:
            if e.get("ref"):
                seen[e["ref"]] = e
            fresh.append(e)
            results.append(None)
    it = iter(_record_fresh(store, model, stamp, fresh))
    # a repeat is described from its first copy, which by now has its attempt and mistake ids
    return [next(it) if old is None else {"attempt": old.get("id"), "concept": old.get("concept"),
                                          "result": old.get("result"), "duplicate": True,
                                          "mistake": old.get("mistake")} for old in results]


def _record_fresh(store: Store, model, stamp: str, events: list) -> list:
    results = []
    for n, e in enumerate(events, model.raw_attempts + 1):
        e["ts"], e["id"] = stamp, f"A{n}"
        model.assign_mistake(e)
        target = e.get("targets")
        gone = bool(target) and target not in model.mistakes  # its answers were undone or voided meanwhile
        s = model.state(e["concept"])
        before, known = model.mastery(s), s.p
        model.apply(e)
        effects = dict(model.effects)
        due = model.due_at(s)
        results.append({
            "attempt": e["id"], "concept": e["concept"], "result": e["result"],
            "targets": target, "target_gone": gone,
            "before": round(before, 3), "after": round(model.mastery(s), 3), "status": model.label(s),
            "knowledge_before": round(known, 3), "knowledge_after": round(s.p, 3),
            "next_review": iso(due) if due else None,
            "review_in_days": round(model.interval(s), 1) if s.S else None,
            **model.signal(e),
            "mistake": effects.get("mistake"),
            "repeat": bool(effects.get("mistake")) and model.mistakes[effects["mistake"]]["misses"] >= 2,
            "fixing": effects.get("fixing", []),
            "resolved": effects.get("resolved", []), "inferred": effects.get("inferred", []),
        })
    if events:
        store.append(*events)
    return results


def describe_results(results: list) -> str:
    out = []
    for r in results:
        if r.get("duplicate"):
            out.append(f"{r['attempt'] or '-'} {r['concept']}: already recorded (same ref), so not recorded again")
            continue
        mark = {"correct": "✓", "partial": "½", "incorrect": "✗"}[r["result"]]
        when = f" · next review in {r['review_in_days']} days" if r["review_in_days"] is not None else ""
        out.append(f"{r['attempt']} {mark} {r['concept']}: mastery {pct(r['before'])} -> {pct(r['after'])}"
                   f" ({r['status']}){when}")
        line = f"   signal {r['signal']} -> action {r['action']} · next difficulty {r['next_difficulty']}/5"
        if r["prereqs_to_check"]:
            line += " · check prerequisites: " + ", ".join(r["prereqs_to_check"])
        out.append(line)
        if r.get("target_gone"):
            gone = f"   {r['targets']} is gone (the answers that showed it were undone or voided)"
            out.append(gone + (": this miss opens it again" if r["mistake"] == r["targets"]
                               else ", so this answer counts toward no mistake"))
        if r["mistake"] and r["repeat"]:
            out.append(f"   mistake {r['mistake']} is a repeat: explain it from a new angle (a worked example or a"
                       f" contrasting pair), then re-ask with --targets {r['mistake']}")
        elif r["mistake"]:
            out.append(f"   mistake {r['mistake']} is open: re-ask it in a new form with --targets {r['mistake']}")
        if r["fixing"]:
            out.append(f"   fixing {', '.join(r['fixing'])}: right today; re-checked from tomorrow")
        if r["resolved"]:
            out.append(f"   resolved {', '.join(r['resolved'])}")
        if r["inferred"]:
            out.append("   also credited (prerequisites; checked in ~3 days): " + ", ".join(r["inferred"]))
    return "\n".join(out)


def cmd_record(args, store: Store):
    store.require()
    item = {k: getattr(args, k) for k in ("concept", "result", "qtype", "difficulty", "confidence", "seconds",
                                          "rating", "options", "misconception", "question", "note", "track",
                                          "error", "confused_with", "targets", "mistake")}
    results = record_events(store, [build_attempt(item, store.concepts())])
    emit(args, results[0], describe_results(results))


def cmd_record_batch(args, store: Store):
    store.require()
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8")
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolymathError(f"Batch is not valid JSON: {exc}")
    if not isinstance(items, list) or not items:
        raise PolymathError("Batch must be a non-empty JSON list of attempts.")
    if not all(isinstance(it, dict) for it in items):
        raise PolymathError("Each answer in the batch must be a JSON object, such as "
                            '{"concept": "a.b.c", "result": "correct"}.')
    registry = store.concepts()
    events = [build_attempt({**it, "track": it.get("track") or args.track}, registry) for it in items]
    results = record_events(store, events)
    written = [r for r in results if not r.get("duplicate")]
    right = sum(SCORE[r["result"]] for r in written)
    score = right / len(written) if written else None
    repeats = len(results) - len(written)
    emit(args, {"recorded": len(written), "score": None if score is None else round(score, 3), "answers": results},
         f"Recorded {len(written)} answers" + (f" ({pct(score)})" if written else "")
         + (f"; {repeats} already recorded" if repeats else "") + ".\n" + describe_results(results))


def cmd_assess(args, store: Store):
    store.require()
    registry = store.concepts()
    events = []
    for pair in args.pairs:
        if "=" not in pair:
            raise PolymathError(f"'{pair}' should look like concept.id=0.7")
        cid, raw = pair.split("=", 1)
        concept_or_fail(registry, cid)
        events.append({"type": "assess", "concept": cid, "level": as_fraction(raw), "source": args.source})
    store.append(*events)
    emit(args, {"assessed": len(events)}, f"Set starting level for {len(events)} concept(s).")


def cmd_lesson(args, store: Store):
    store.require()
    concept_or_fail(store.concepts(), args.concept)
    store.append({"type": "lesson", "concept": args.concept, "minutes": args.minutes, "track": args.track})
    emit(args, {"concept": args.concept}, f"Logged a lesson on {args.concept}. Now check understanding with a question.")


def cmd_mock(args, store: Store):
    store.require()
    by_domain = {}
    for pair in args.domain or []:
        if "=" not in pair:
            raise PolymathError(f"'{pair}' should look like domain-id=0.75")
        key, raw = pair.split("=", 1)
        by_domain[key] = raw
    event = log_mock(store, args.track, args.score, by_domain, args.questions, args.minutes)
    emit(args, event, f"Logged mock exam for {args.track}: {pct(event['score'])}.")


def cmd_session(args, store: Store):
    store.require()
    if args.action == "start":
        store.append({"type": "session_start", "track": args.track, "focus": args.focus})
        emit(args, {"started": iso(now())}, f"Session started{' on ' + args.track if args.track else ''}.")
        return
    data = end_session(store, track=args.track, summary=args.summary)
    accuracy = data["accuracy"]
    if data["journal_error"]:
        print(f"warning: {data['journal_error']}", file=sys.stderr)
    emit(args, data, f"Session closed: {round(data['minutes'])} min, {data['answers']} answers"
                     + (f" at {pct(accuracy)}" if accuracy is not None else "")
                     + (f". Journal: {data['journal']}" if data["journal"] else ". No journal entry was written."))


def worked_minutes(stamps: list, longest_gap: float = 600.0) -> float:
    """A Claude Code session's working time: from its start to each answer, counting no pause longer than
    longest_gap seconds. The time after the last answer isn't counted, so a session left open stays short."""
    ordered = sorted(stamps)
    gaps = ((b - a).total_seconds() for a, b in zip(ordered, ordered[1:]))
    return round(sum(g for g in gaps if 0 < g <= longest_gap) / 60.0, 1)


def end_session(store: Store, track=None, summary=None, session=None, minutes=None, answers=None,
                accuracy=None, at=None) -> dict:
    """Close a study session and write its journal entry. `at` dates the end (no later than now): a dashboard
    session found idle after the computer slept ended when the learner stopped, not at the wake-up.

    A dashboard session passes its own id and figures, and is closed once: asked again with the same
    id, this returns the same kind of result and writes nothing more. Otherwise (the CLI tutor) it
    pairs with the most recent start that has no session id and no end of its own, and counts only the
    answers that carry no session id, so sessions in the browser and in Claude Code can overlap.

    The journal entry is a best effort: if it can't be written, the session is still closed and the
    result has journal None and journal_error saying why. The session's end records whether its entry
    was written, so a repeat reports the same. The entry is written first and taken back if the end
    can't be logged, so the two never disagree."""
    with store.writing():
        events = store.events()
        if session:
            done = next((e for e in reversed(events)
                         if e.get("type") == "session_end" and e.get("session") == session), None)
            if done is not None:
                return _closed_before(store, done, answers, accuracy)
        at = now() if at is None else answer_time(at)
        start = None
        if session is None:
            for e in reversed(events):
                if e.get("session"):
                    continue
                if e.get("type") == "session_end":
                    break
                if e.get("type") == "session_start":
                    start = e
                    break
            attempts = []
            minutes = 0.0
            if start:
                started = parse_ts(start["ts"])
                voided = {e.get("target") for e in events if e.get("type") == "void"}
                attempts = [e for e in events if e.get("type") == "attempt" and not e.get("session")
                            and attempt_key(e) not in voided and parse_ts(e["ts"]) >= started]
                minutes = worked_minutes([started] + [parse_ts(e["ts"]) for e in attempts])
            answers = len(attempts)
            accuracy = statistics.mean(SCORE[e["result"]] for e in attempts) if attempts else None
            track = track or (start or {}).get("track")
        minutes = round(float(minutes or 0), 1)
        journal, error, size = _write_journal(store, at, track, minutes, answers or 0, accuracy, summary, session)
        try:
            store.append({"ts": iso(at), "type": "session_end", "track": track, "minutes": minutes,
                          "summary": summary, "session": session, "journal": journal is not None})
        except BaseException:
            if journal:
                _cut_journal(Path(journal), size)  # the session wasn't closed, so it has no entry either
            raise
        return {"minutes": minutes, "answers": answers or 0, "accuracy": accuracy, "journal": journal,
                "journal_error": error}


def _closed_before(store: Store, done: dict, answers, accuracy) -> dict:
    """The result for a session that was already closed: its logged minutes, and the journal file of the
    day it closed on, or why there is none. (An end an older version logged doesn't say whether its
    entry was written, so for one of those it is that day's file, if it is there.)"""
    try:
        journal = store.journal_dir / f"{local_day(parse_ts(done['ts'])).isoformat()}.md"
    except (KeyError, TypeError, ValueError):
        journal = None
    error = None
    if done.get("journal") is False:
        journal, error = None, "This session was closed earlier, but its journal entry couldn't be written then."
    return {"minutes": done.get("minutes") or 0.0, "answers": answers or 0, "accuracy": accuracy,
            "journal": str(journal) if journal and journal.exists() else None, "journal_error": error}


def _write_journal(store: Store, at: datetime, track, minutes, answers, accuracy, summary, session) -> tuple:
    """Add a closed session's entry to the day's journal. Returns (its path, None, the file's size before,
    None if it was new), or (None, why, None) if it couldn't be written, with nothing of it left behind.
    A character the file can't hold (a lone surrogate) is written as ?."""
    day = local_day(at).isoformat()
    journal = store.journal_dir / f"{day}.md"
    where = " · in the dashboard" if session else ""
    size, opened = None, False
    try:
        store.journal_dir.mkdir(parents=True, exist_ok=True)
        if journal.exists():
            size = journal.stat().st_size
        header = "" if size is not None else f"# {day}\n"
        entry = (f"{header}\n## {at.astimezone().strftime('%H:%M')} · {track or 'mixed'} · {round(minutes)} min · "
                 f"{answers} answers{f' · {pct(accuracy)}' if accuracy is not None else ''}{where}\n\n"
                 f"{(summary or '').strip()}\n")
        with journal.open("a", encoding="utf-8", errors="replace") as f:
            opened = True
            f.write(entry)
    except (OSError, UnicodeError) as exc:
        if opened:
            _cut_journal(journal, size)
        return None, f"The session was closed, but its journal entry couldn't be written to {journal} ({exc}).", None
    return str(journal), None, size


def _cut_journal(path: Path, size) -> None:
    """Take back an entry just added to a journal file: cut the file to its old size, or remove it if
    the entry made it. A best effort."""
    try:
        if size is None:
            path.unlink()
        else:
            os.truncate(path, size)
    except OSError:
        pass


def cmd_status(args, store: Store):
    store.require()
    model = Model(store)
    if args.track:
        rep = model.track_report(store.track(args.track))
        p = rep["projection"]
        lines = [f"{rep['title']} [{rep['slug']}] · {rep['status']}"]
        if rep.get("goal"):
            lines.append(f"Goal: {rep['goal']}")
        if rep["target_date"]:
            lines.append(f"Target: {rep['target_date']} ({p['days_left']} days left)")
        if rep["passing_score"] is not None:
            lines.append(f"Readiness: predicted {pct(rep['predicted_score'])} vs pass {pct(rep['passing_score'])}"
                         f" -> {rep['verdict']} · coverage {pct(rep['coverage'])}")
        lines.append(f"Mastered {rep['mastered']}/{rep['concepts']} · started {rep['started']} · due now {rep['due']}"
                     f" · open mistakes {rep['mistakes_open']}")
        lines.append(f"Placement: {rep['mode']} (first-try accuracy {pct(rep['placement'])})")
        if p["velocity_per_day"] is not None:
            lines.append(f"Pace: {p['velocity_per_day']} concepts mastered/day · finish ~{p['finish_estimate'] or '-'}"
                         f" · {p['status']}")
        if p["needed_per_day"] is not None:
            lines.append(f"Needed: {p['needed_per_day']} concepts/day to finish with review time before the target")
        for m in rep["mocks"]:
            lines.append(f"Mock {m['date']}: {pct(m['score'])}")
        lines.append("By area:")
        for d in rep["domains"]:
            lines.append(f"  {d['name'][:34]:<34} {pct(d['weight']):>4}  {bar(d['mastery'])} {pct(d['mastery']):>4}"
                         f"  mastered {d['mastered']}/{d['concepts']}  due {d['due']}")
        emit(args, rep, "\n".join(lines))
        return
    learner = model.learner_report()
    tracks = [model.track_report(store.track(s)) for s in store.slugs()]
    lines = [f"{learner['name']} · streak {learner['streak']} days · {learner['active_days']} active days · "
             f"{learner['attempts']} answers · {learner['due_now']} reviews due"]
    for r in tracks:
        ready = f" · readiness {pct(r['predicted_score'])}/{pct(r['passing_score'])}" if r["passing_score"] is not None else ""
        lines.append(f"- {r['slug']} [{r['status']}]: mastered {r['mastered']}/{r['concepts']}{ready}"
                     f" · {r['projection']['status']}")
    emit(args, {"learner": learner, "tracks": tracks}, "\n".join(lines))


def cmd_profile(args, store: Store):
    store.require()
    if args.action == "set":
        with store.writing():  # take turns with the dashboard's settings, or one change is lost
            learner = store.learner()
            for pair in args.pairs:
                if "=" not in pair:
                    raise PolymathError(f"'{pair}' should look like preferences.session_minutes=45")
                key, raw = pair.split("=", 1)
                node = learner
                parts = key.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                    if not isinstance(node, dict):
                        raise PolymathError(f"'{key}' runs through a value that is not an object.")
                node[parts[-1]] = parse_value(raw)
            store.save_learner(learner)
        emit(args, learner, f"Updated {', '.join(p.split('=')[0] for p in args.pairs)}.")
        return
    rep = Model(store).learner_report()
    lines = [f"{rep['name']} · learning since {rep['since'] or 'today'} · {rep['attempts']} answers"
             f" · accuracy {pct(rep['accuracy'])}"]
    pace = rep["pace"]
    pace_note = "baseline" if rep["pace_samples"] < 5 else (
        "faster than baseline" if pace > 1.1 else "slower than baseline" if pace < 0.9 else "about baseline")
    lines.append(f"Pace: {pace}x ({pace_note}; {rep['pace_samples']} concepts measured)")
    mem = rep["memory_factor"]
    mem_note = "default" if rep["memory_samples"] < 5 else (
        "remembers longer than the curve predicts" if mem > 1.1 else
        "forgets faster than the curve predicts" if mem < 0.9 else "matches the curve")
    lines.append(f"Memory: {mem}x ({mem_note}; {rep['memory_samples']} spaced reviews)")
    cal = rep["calibration"]
    if cal:
        tone = "overconfident" if cal["gap"] > 0.1 else "underconfident" if cal["gap"] < -0.1 else "well calibrated"
        lines.append(f"Confidence: says {pct(cal['stated'])}, scores {pct(cal['actual'])} -> {tone}"
                     f" · {cal['confident_misses']} confident misses in the last 30")
    lines.append(f"Placement: {rep['mode']} (first-try accuracy {pct(rep['placement'])})")
    tech = rep["technique"]
    if tech["timed_answers"] or tech["slips"] or tech["traps"]:
        timing = (f" · median {tech['median_seconds']}s per question vs {tech['budget_seconds']}s exam budget"
                  if tech["median_seconds"] is not None else "")
        lines.append(f"Exam technique: {tech['slips']} slips, {tech['traps']} traps{timing}")
    mk = rep["mistakes"]
    lines.append(f"Mistakes: {mk['open']} open, {mk['fixing']} fixing, {mk['resolved']} resolved")
    if rep["by_format"]:
        lines.append("By format: " + " · ".join(f"{k} {pct(v['accuracy'])} ({v['n']})" for k, v in rep["by_format"].items()))
    if rep["by_difficulty"]:
        lines.append("By difficulty: " + " · ".join(f"d{k} {pct(v['accuracy'])} ({v['n']})" for k, v in rep["by_difficulty"].items()))
    if rep["weakest"]:
        lines.append("Weakest: " + "; ".join(f"{w['title']} {pct(w['mastery'])}" for w in rep["weakest"]))
    if rep["strongest"]:
        lines.append("Strongest: " + "; ".join(f"{w['title']} {pct(w['mastery'])}" for w in rep["strongest"]))
    lines.append(f"Streak {rep['streak']} days · {rep['active_days']} active days · {rep['session_hours']} h in sessions")
    emit(args, rep, "\n".join(lines))


def cmd_search(args, store: Store):
    store.require()
    registry = store.concepts()
    model = Model(store)
    words = [w for w in re.split(r"[\s._/-]+", args.query.lower()) if w]
    scored = []
    for cid, c in registry.items():
        hay = " ".join([cid, c.get("title", ""), " ".join(c.get("tags", []))]).lower()
        hits = sum(1 for w in words if w in hay)
        if hits:
            scored.append((hits, difflib.SequenceMatcher(None, args.query.lower(), c.get("title", "").lower()).ratio(), cid))
    scored.sort(reverse=True)
    rows = [{**model.concept_view(cid), "tracks": registry[cid].get("tracks", [])} for _, _, cid in scored[:args.limit]]
    lines = [f"{r['concept']}  {r['title']} · {r['status']} {pct(r['mastery'])} · tracks: {', '.join(r['tracks']) or '-'}"
             for r in rows]
    emit(args, rows, "\n".join(lines) or "No matching concepts. Define new ones in a syllabus and import it.")


def cmd_concept(args, store: Store):
    store.require()
    registry = store.concepts()
    info = concept_or_fail(registry, args.concept)
    model = Model(store)
    s = model.state(args.concept)
    view = model.concept_view(args.concept)
    view.update({
        "objective": info.get("objective", ""), "tags": info.get("tags", []), "tracks": info.get("tracks", []),
        "prereqs": [{"concept": p, "status": model.label(model.state(p)),
                     "mastery": round(model.mastery(model.state(p)), 3)} for p in info.get("prereqs", [])],
        "related": info.get("related", []), "recent": s.recent,
    })
    lines = [f"{view['title']} [{args.concept}]", f"Objective: {view['objective'] or '-'}",
             f"Status {view['status']} · mastery {pct(view['mastery'])} (knowledge {pct(view['knowledge'])}, "
             f"memory {pct(view['memory'])}) · {view['attempts']} tries · accuracy {pct(view['accuracy'])}",
             f"Next review: {view['next_review'] or '-'} · suggested difficulty {view['difficulty']}/5"]
    if view["prereqs"]:
        lines.append("Prerequisites: " + ", ".join(f"{p['concept']} ({p['status']} {pct(p['mastery'])})" for p in view["prereqs"]))
    if view["misconceptions"]:
        lines.append("Misconceptions seen: " + " | ".join(view["misconceptions"]))
    for r in s.recent[-5:]:
        q = f" · {r['question']}" if r.get("question") else ""
        lines.append(f"  {local_day(parse_ts(r['ts'])).isoformat()} {r['result']:<9} {r['format']} d{r['difficulty']}{q}")
    emit(args, view, "\n".join(lines))


def cmd_due(args, store: Store):
    store.require()
    model = Model(store)
    slugs = [args.track] if args.track else store.slugs()
    ids = []
    for slug in slugs:
        for cid, _, _ in model.track_rows(store.track(slug)):
            if cid not in ids:
                ids.append(cid)
    due = [model.concept_view(c) for c in ids if model.is_due(model.state(c))]
    due.sort(key=lambda v: v["memory"])
    lines = [f"{v['concept']}  {v['title']} · memory {pct(v['memory'])} · mastery {pct(v['mastery'])}" for v in due]
    emit(args, due, "\n".join(lines) or "Nothing due. Nice.")


def cmd_dashboard(args, store: Store):
    store.require()
    path = store.root / "dashboard.html"
    path.write_text(load_web().snapshot_html(store), encoding="utf-8")
    if args.open:
        webbrowser.open(path.as_uri())
    emit(args, {"path": str(path)}, f"Dashboard snapshot written to {path}. For a live one, run `serve`.")


def read_token_file(path: str) -> str:
    """The sign-in token from a file, or from standard input for -: kept out of the command line. A
    byte-order mark, as some Windows editors write, is not part of it; any other invisible character is
    refused, since a token nobody can type back would turn every sign-in away."""
    where = "standard input" if path == "-" else path
    try:
        if path == "-":
            stream = getattr(sys.stdin, "buffer", None)
            raw = decode_text(stream.read()) if stream is not None else sys.stdin.read()
        else:
            raw = decode_text(Path(path).expanduser().read_bytes())
    except (OSError, UnicodeError) as exc:
        raise PolymathError(f"Can't read the token from {where} ({getattr(exc, 'strerror', None) or exc}).")
    token = raw.lstrip("\ufeff").strip()
    if not token:
        raise PolymathError(f"No token in {where}: it is empty.")
    if any(c.isspace() for c in token):
        raise PolymathError(f"The token in {where} must be one word on one line.")
    hidden = next((c for c in token if not c.isprintable()), None)
    if hidden is not None:
        raise PolymathError(f"The token in {where} holds an invisible character (U+{ord(hidden):04X}). "
                            "Save it again as plain text.")
    return token


def decode_text(data: bytes) -> str:
    """A small text file's contents: UTF-8 (with or without a byte-order mark), or UTF-16 when it starts
    with one, as Windows PowerShell saves text by default."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def cmd_serve(args, store: Store):
    if args.token_file:
        token = read_token_file(args.token_file)
    elif args.token is not None:
        token = args.token
        if token:
            print("warning: --token shows the token to every account on this machine (in `ps`); set "
                  "POLYMATH_WEB_TOKEN or use --token-file instead.", file=sys.stderr)
    else:
        token = os.environ.get("POLYMATH_WEB_TOKEN") or None
    web = load_web()  # (main() has already closed the data folder to other accounts)
    options = dict(host=args.host, port=args.port, token=token, read_only=args.read_only,
                   allow_hosts=args.allow_host or [], open_browser=args.open, tls_cert=args.tls_cert,
                   tls_key=args.tls_key, verbose=args.verbose, rotate_token=args.rotate_token,
                   tutor=args.tutor, model=args.model, effort=args.effort)
    if args.demo:
        web.serve_demo(**options)
    else:
        web.serve(store, **options)


def cmd_mistakes(args, store: Store):
    store.require()
    model = Model(store)
    ids = {c for c, _, _ in model.track_rows(store.track(args.track))} if args.track else None
    rows = [m for m in model.mistakes.values()
            if (ids is None or m["concept"] in ids)
            and (args.status == "all" or m["status"] == args.status
                 or (args.status == "unresolved" and m["status"] != "resolved"))]
    rows.sort(key=lambda m: (m["status"] == "resolved", m["status"] != "open", -m["misses"], m["last_miss"]))
    lines = [f"{m['id']} [{m['status']}] {m['concept']} · {m.get('error') or '-'} · missed {m['misses']}x"
             + (f" · confused with {m['confused_with']}" if m.get("confused_with") else "")
             + f": {m['text'] or '-'}" for m in rows]
    emit(args, rows, "\n".join(lines) or "No mistakes in this view.")


def cmd_void(args, store: Store):
    """Remove a disputed answer (a wrong question or answer key) from the learner model."""
    store.require()
    data = void_attempt(store, args.attempt, args.concept, args.reason)
    emit(args, data, f"Voided {data['attempt'] or data['voided']} on {data['concept']}: "
                     f"mastery {pct(data['before'])} -> {pct(data['after'])}.")


def add_note(path: Path, text: str, section=None, title=None, store: Store = None) -> None:
    """Append a dated bullet, under a `## section` heading when one is given (atomically, under the
    data folder's write lock, since the tutor and the dashboard both write these files)."""
    store = store or Store(path.parent if path.name == "learner.md" else path.parent.parent.parent)
    with store.writing():
        _add_note(path, text, section, title)


def _add_note(path: Path, text: str, section=None, title=None) -> None:
    bullet = f"- {local_day(now()).isoformat()}: {' '.join(str(text).split())}"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = [f"# {title}", ""] if title else []
    if section:
        start = next((i for i, line in enumerate(lines)
                      if line.startswith("## ") and section.lower() in line.lower()), None)
        if start is None:
            lines += ["", f"## {section}"]
            start = len(lines) - 1
        end = start + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, bullet)
    else:
        lines.append(bullet)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    os.replace(tmp, path)


def cmd_notes(args, store: Store):
    store.require()
    if args.track:
        store.track(args.track)
        path = store.tracks_dir / args.track / ("plan.md" if args.plan else "notes.md")
    else:
        path = store.profile_md
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    print(text or f"(nothing yet in {path})")


def cmd_note(args, store: Store):
    store.require()
    if args.track:
        store.track(args.track)
        path = store.tracks_dir / args.track / "notes.md"
        add_note(path, args.text, title=f"Notes: {args.track}")
    else:
        path = store.profile_md
        add_note(path, args.text, section=args.section or "Coaching notes")
    emit(args, {"path": str(path)}, f"Noted in {path}")


def cmd_guide(args, store: Store):
    """Print the tutor's instruction files, so skills can load them from any working folder."""
    folder = PLUGIN_ROOT / "references"
    available = sorted(p.stem for p in folder.glob("*.md"))
    chunks = []
    for name in args.names:
        path = folder / f"{name}.md"
        if name not in available:
            raise PolymathError(f"No guide '{name}'. Available: {', '.join(available)}.")
        chunks.append(path.read_text(encoding="utf-8").strip())
    print("\n\n".join(chunks))


def cmd_blueprints(args, store: Store):
    folder = PLUGIN_ROOT / "blueprints"
    files = {p.stem: p for p in sorted(folder.glob("*.json"))}
    if not args.name:
        rows = []
        for name, path in files.items():
            data = read_json(path, {})
            exam = data.get("exam") or {}
            rows.append({"name": name, "title": data.get("title"), "kind": data.get("kind"),
                         "concepts": sum(len(d.get("concepts", [])) for d in data.get("domains", [])),
                         "verified": exam.get("verified"), "slug": data.get("slug")})
        lines = [f"{r['name']}: {r['title']} · {r['concepts']} concepts"
                 + (f" · exam facts verified: {r['verified']}" if r["verified"] is not None else "") for r in rows]
        emit(args, rows, "\n".join(lines) or "No bundled blueprints.")
        return
    if args.name not in files:
        hint = difflib.get_close_matches(args.name, list(files), n=1)
        raise PolymathError(f"No blueprint '{args.name}'." + (f" Did you mean '{hint[0]}'?" if hint else "")
                            + f" Available: {', '.join(files) or 'none'}.")
    data = read_json(files[args.name], {})
    if args.to_track:
        if not SLUG_RE.match(args.to_track):
            raise PolymathError(f"'{args.to_track}' is not a valid track slug.")
        data["slug"] = args.to_track
        dest = store.tracks_dir / args.to_track / "syllabus.json"
        if dest.exists() and not args.force:
            raise PolymathError(f"{dest} already exists. Edit it, or pass --force to overwrite it.")
        write_json(dest, data)
        emit(args, {"path": str(dest)}, f"Copied to {dest}. Review or edit it, then run `track import {dest}`.")
        return
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_undo(args, store: Store):
    store.require()
    if not 1 <= args.count <= 20:
        raise PolymathError("--count must be between 1 and 20.")
    with store.writing():
        removed = undo_events(store, args.count)
    emit(args, removed, "Undone:\n" + "\n".join(json.dumps(e, ensure_ascii=False) for e in removed))


UNDO_STUDY_KINDS = ("attempt", "assess", "lesson", "mock", "void", "session_start", "session_end")
UNDO_REACH = 12 * 3600  # seconds: how old a tutor record undo may reach once it has stepped past the dashboard's


def _undo_reachable(e: dict) -> bool:
    """Whether undo may take this tutor record after stepping past the dashboard's newer ones: a study
    record made recently, never an import or a syllabus update."""
    if e.get("type") not in UNDO_STUDY_KINDS:
        return False
    try:
        return (now() - parse_ts(e.get("ts"))).total_seconds() <= UNDO_REACH
    except (TypeError, ValueError, AttributeError):
        return False


def undo_events(store: Store, count: int) -> list:
    """Undo the most recent events. An answer is voided rather than deleted, so answer and mistake
    ids are never reused; anything else is removed. Taking back a void brings its answer back, so
    that answer is the next one in line (as two single undos would have it). The web dashboard's
    records are left alone: undo steps past them to the tutor's own, but then only to a recent study
    record, so it never reaches back to an import or to an answer from days ago."""
    lines = [l for l in (store.events_path.read_text(encoding="utf-8").splitlines()
                         if store.events_path.exists() else []) if l.strip()]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append(None)
    voids = Counter(e.get("target") for e in events if e and e.get("type") == "void")
    web_voided = {e.get("target") for e in events if e and e.get("type") == "void" and dashboard_record(e)}
    undone, remove, tombstones, skipped_web, stopped_at = [], set(), [], 0, None
    i = len(events) - 1
    while i >= 0 and len(undone) < count:
        e = events[i]
        if e and e.get("type") == "attempt" and attempt_key(e) in web_voided and not dashboard_record(e):
            stopped_at = e.get("id")  # the tutor's answer was voided in the dashboard: it is already undone,
            break                     # and reaching past it would undo an older answer nobody meant
        skip = e is None or (e.get("type") == "void" and e.get("reason") == "undo") or \
            (e.get("type") == "attempt" and voids[attempt_key(e)] > 0)
        if not skip and dashboard_record(e):
            skip, skipped_web = True, skipped_web + 1  # the dashboard's own records: void one by its id instead
        if not skip and skipped_web and not _undo_reachable(e):
            break
        if not skip:
            if e.get("type") == "attempt":
                tombstones.append({"type": "void", "target": attempt_key(e), "concept": e.get("concept"),
                                   "reason": "undo"})
            else:
                remove.add(i)
                if e.get("type") == "void":
                    voids[e.get("target")] -= 1  # the answer it voided counts again, so it is next in line
            undone.append(e)
        i -= 1
    if not undone:
        if stopped_at:
            raise PolymathError(f"Nothing to undo: the newest answer you recorded, {stopped_at}, was already voided "
                                "in the web dashboard. Use `void <id>` for a specific older answer.")
        raise PolymathError("Nothing to undo." + (" The newest records are the web dashboard's, which undo leaves "
                                                 "alone, and the tutor recorded nothing recent before them. Use "
                                                 "`void <id>` for a specific answer." if skipped_web else ""))
    keep = [line for n, line in enumerate(lines) if n not in remove]
    fd, tmp = tempfile.mkstemp(dir=str(store.root), prefix=".tmp-", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + ("\n" if keep else ""))
    os.replace(tmp, store.events_path)
    if tombstones:
        store.append(*tombstones)
    return undone


# --- argument parsing -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    parser = argparse.ArgumentParser(prog="polymath.py", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"polymath {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], help="create or update the learner profile")
    p.add_argument("--name")
    p.add_argument("--persona", help="name of the tutor persona (default Sage)")
    p.set_defaults(func=cmd_init)

    sub.add_parser("where", parents=[common], help="print the data folder").set_defaults(func=cmd_where)
    sub.add_parser("brief", help="one-screen summary for the tutor (never fails)").set_defaults(func=cmd_brief)

    track = sub.add_parser("track", help="manage goals (tracks)").add_subparsers(dest="track_cmd", required=True)
    p = track.add_parser("import", parents=[common], help="create or update a track from a syllabus JSON file")
    p.add_argument("file", help="path to syllabus JSON, or - for stdin")
    p.add_argument("--slug")
    p.add_argument("--target-date", dest="target_date")
    p.set_defaults(func=cmd_track_import)
    track.add_parser("list", parents=[common]).set_defaults(func=cmd_track_list)
    p = track.add_parser("show", parents=[common])
    p.add_argument("slug")
    p.add_argument("--domain")
    p.set_defaults(func=cmd_track_show)
    p = track.add_parser("set", parents=[common], help="e.g. track set pd1 target_date=2027-02-15 status=paused")
    p.add_argument("slug")
    p.add_argument("pairs", nargs="+")
    p.set_defaults(func=cmd_track_set)

    p = sub.add_parser("next", parents=[common], help="what to study now")
    p.add_argument("--track")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("record", parents=[common], help="record one graded answer")
    p.add_argument("concept")
    p.add_argument("result", help="correct | partial | incorrect")
    p.add_argument("--qtype", default="mcq", choices=sorted(GUESS))
    p.add_argument("--difficulty", type=int, default=3)
    p.add_argument("--confidence", type=int, help="1 (guessing) to 5 (certain)")
    p.add_argument("--seconds", type=int)
    p.add_argument("--rating", choices=sorted(FIRST_STABILITY))
    p.add_argument("--options", type=int, help="number of choices, if not 4")
    p.add_argument("--misconception")
    p.add_argument("--question", help="short summary of the question asked")
    p.add_argument("--note")
    p.add_argument("--track")
    p.add_argument("--error", choices=ERRORS, help="why it was missed")
    p.add_argument("--confused-with", dest="confused_with", help="the concept id it was confused with")
    p.add_argument("--targets", help="the mistake id this question re-asks, e.g. M3")
    p.add_argument("--mistake", help="link this miss to an existing mistake id")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("record-batch", parents=[common], help="record a JSON list of answers (file or -)")
    p.add_argument("file")
    p.add_argument("--track")
    p.set_defaults(func=cmd_record_batch)

    p = sub.add_parser("assess", parents=[common], help="set starting levels: concept.id=0.7 ...")
    p.add_argument("pairs", nargs="+")
    p.add_argument("--source", default="diagnostic", choices=["diagnostic", "self"])
    p.set_defaults(func=cmd_assess)

    p = sub.add_parser("lesson", parents=[common], help="log that a concept was taught")
    p.add_argument("concept")
    p.add_argument("--minutes", type=float)
    p.add_argument("--track")
    p.set_defaults(func=cmd_lesson)

    p = sub.add_parser("mock", parents=[common], help="log a mock exam result")
    p.add_argument("track")
    p.add_argument("score", help="0-1 or a percentage")
    p.add_argument("--domain", nargs="*", help="domain-id=score ...")
    p.add_argument("--questions", type=int)
    p.add_argument("--minutes", type=float)
    p.set_defaults(func=cmd_mock)

    p = sub.add_parser("session", parents=[common], help="session start | end")
    p.add_argument("action", choices=["start", "end"])
    p.add_argument("--track")
    p.add_argument("--focus")
    p.add_argument("--summary")
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("status", parents=[common], help="progress overall or for one track")
    p.add_argument("--track")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("profile", parents=[common], help="learner analytics, or `profile set key=value`")
    p.add_argument("action", nargs="?", default="show", choices=["show", "set"])
    p.add_argument("pairs", nargs="*")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("search", parents=[common], help="find concept ids")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("concept", parents=[common], help="everything about one concept")
    p.add_argument("concept")
    p.set_defaults(func=cmd_concept)

    p = sub.add_parser("due", parents=[common], help="concepts due for review")
    p.add_argument("--track")
    p.set_defaults(func=cmd_due)

    p = sub.add_parser("dashboard", parents=[common],
                       help="write a self-contained snapshot of the web dashboard (dashboard.html)")
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("serve", help="run the live web dashboard, on this machine or a server",
                       description="Run the live progress dashboard. On 127.0.0.1 (the default) it needs no "
                                   "sign-in; on any other address it requires a token, printed at start.")
    p.add_argument("--host", default=os.environ.get("POLYMATH_WEB_HOST") or "127.0.0.1",
                   help="address to listen on (default 127.0.0.1; 0.0.0.0 means every interface)")
    p.add_argument("--port", type=int, default=env_int("POLYMATH_WEB_PORT", 8765), help="default 8765")
    p.add_argument("--open", action="store_true", help="open it in your browser")
    given = p.add_mutually_exclusive_group()
    given.add_argument("--token-file", metavar="PATH",
                       help="read the sign-in token from this file, or - for standard input")
    given.add_argument("--token", help="sign-in token; other accounts can see it in `ps`, so prefer "
                                       "POLYMATH_WEB_TOKEN or --token-file (default: POLYMATH_WEB_TOKEN, else "
                                       "none on 127.0.0.1 and a saved random one elsewhere)")
    p.add_argument("--rotate-token", action="store_true", help="replace the saved token with a new one")
    p.add_argument("--read-only", action="store_true", help="refuse every change made from the browser")
    p.add_argument("--allow-host", action="append", metavar="NAME",
                   help="another host name to answer to, e.g. behind a reverse proxy; NAME:PORT when the proxy "
                        "listens on a port other than 80/443 and rewrites the host (repeatable)")
    p.add_argument("--tls-cert", help="serve HTTPS with this certificate (PEM)")
    p.add_argument("--tls-key", help="the certificate's private key (PEM)")
    p.add_argument("--demo", action="store_true", help="serve a simulated learner, to preview the dashboard")
    p.add_argument("--tutor", choices=["auto", "claude", "api", "fake", "off"],
                   default=os.environ.get("POLYMATH_TUTOR") or None,
                   help="who writes study questions: auto (Claude Code, else the Anthropic API), claude, api, "
                        "fake (built-in practice questions, no AI) or off (default: auto; fake with --demo)")
    p.add_argument("--model", default=os.environ.get("POLYMATH_MODEL") or "opus",
                   help="model for writing questions (default opus)")
    p.add_argument("--effort", default=os.environ.get("POLYMATH_EFFORT") or "low",
                   choices=["low", "medium", "high", "xhigh", "max"], help="effort for writing questions (default low)")
    p.add_argument("--verbose", action="store_true", help="log every request")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("notes", help="print learner.md, or a track's notes.md (or plan.md with --plan)")
    p.add_argument("--track")
    p.add_argument("--plan", action="store_true")
    p.set_defaults(func=cmd_notes)

    p = sub.add_parser("note", parents=[common], help="add a dated note to learner.md or a track's notes.md")
    p.add_argument("text")
    p.add_argument("--track")
    p.add_argument("--section", help="learner.md heading, e.g. 'How I learn best' (default: Coaching notes)")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("mistakes", parents=[common], help="the mistake bank")
    p.add_argument("--track")
    p.add_argument("--status", default="unresolved", choices=["unresolved", "open", "fixing", "resolved", "all"])
    p.set_defaults(func=cmd_mistakes)

    p = sub.add_parser("void", parents=[common], help="void a disputed answer (bad question or answer key)")
    p.add_argument("attempt", nargs="?", help="attempt id such as A12 (default: the most recent)")
    p.add_argument("--concept", help="void the most recent attempt on this concept")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_void)

    p = sub.add_parser("guide", help="print the tutor's instructions: persona, teaching, questions, onboarding, engine")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("blueprints", parents=[common], help="list bundled syllabi, print one, or copy one to a track")
    p.add_argument("name", nargs="?")
    p.add_argument("--to-track", dest="to_track", help="copy into ~/.polymath/tracks/<slug>/syllabus.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_blueprints)

    p = sub.add_parser("undo", parents=[common], help="remove the most recent event(s)")
    p.add_argument("--count", type=int, default=1)
    p.set_defaults(func=cmd_undo)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not be UTF-8, and output uses ✓ and ✗
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    mask = os.umask(0o077)  # what the engine creates (answers, journal, lock) is readable by you alone
    try:
        store = Store()
        keep_private(store.root)  # and a folder an older version left open is closed, whatever the command
        args.func(args, store)
        return 0
    except PolymathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        os.umask(mask)


if __name__ == "__main__":
    sys.exit(main())
