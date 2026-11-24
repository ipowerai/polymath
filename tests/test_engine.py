"""End-to-end tests for scripts/polymath.py. Run: python3 -m unittest discover tests"""
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "polymath.py"
BLUEPRINT = ROOT / "blueprints" / "salesforce-platform-developer-1.json"
sys.path.insert(0, str(ROOT / "scripts"))
import polymath as engine  # noqa: E402  (some tests use it in this process, on their own data folder)

DAY1 = "2026-11-17T18:00:00Z"
SESSION = "0123456789abcdef"  # a dashboard study session's id


def tzset():
    if hasattr(time, "tzset"):
        time.tzset()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def syllabus(**overrides):
    data = {
        "slug": "demo",
        "title": "Demo track",
        "goal": "Pass the demo exam",
        "kind": "certification",
        "exam": {"passing_score": 0.7},
        "domains": [
            {"id": "basics", "name": "Basics", "weight": 0.5, "concepts": [
                {"id": "demo.basics.a", "title": "Concept A", "prereqs": []},
                {"id": "demo.basics.b", "title": "Concept B", "prereqs": ["demo.basics.a"]},
            ]},
            {"id": "advanced", "name": "Advanced", "weight": 0.5, "concepts": [
                {"id": "demo.advanced.c", "title": "Concept C", "prereqs": ["demo.basics.b"]},
            ]},
        ],
    }
    data.update(overrides)
    return data


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.clock = DAY1
        self.inside = False

    def tearDown(self):
        self.tmp.cleanup()

    def cli_env(self):
        return {**os.environ, "POLYMATH_HOME": str(self.home), "POLYMATH_NOW": self.clock, "TZ": "UTC"}

    def run_cli(self, *args, stdin=None, ok=True, umask=None):
        extra = {} if umask is None else {"umask": umask}
        proc = subprocess.run([sys.executable, str(ENGINE), *args], input=stdin, capture_output=True,
                              text=True, env=self.cli_env(), **extra)
        if ok and proc.returncode != 0:
            self.fail(f"{args} failed ({proc.returncode}): {proc.stderr}")
        return proc

    def start_cli(self, *args):
        """Start a command without waiting for it; it is reaped when the test ends, whatever happens."""
        proc = subprocess.Popen([sys.executable, str(ENGINE), *args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.cli_env())

        def reap():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
        self.addCleanup(reap)
        return proc

    def in_process(self):
        """Use the engine in this process too, on this test's data folder and clock, in UTC (undone at the
        end). Returns the Store."""
        env = mock.patch.dict(os.environ, {"POLYMATH_HOME": str(self.home), "POLYMATH_NOW": self.clock, "TZ": "UTC"})
        env.start()
        self.addCleanup(tzset)       # cleanups run last-first: the environment comes back, then the zone
        self.addCleanup(env.stop)
        tzset()
        self.inside = True
        return engine.Store(self.home)

    def at(self, ts):
        """Move the clock for the CLI, and for this process once it uses the engine in-process."""
        self.clock = ts
        if self.inside:
            os.environ["POLYMATH_NOW"] = ts

    @contextmanager
    def lock_held(self):
        """Hold the data folder's write lock, as another writer (the dashboard) would."""
        with open(self.home / ".lock", "a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def serve_in_process(self, *args, stdin=""):
        """Run `serve` in this process with a stand-in for the web server: what would the server be given?
        Returns (exit code, the server's options or None, stderr). Needs in_process() first. stdin given
        as bytes arrives the way a pipe's does (text over a byte stream)."""
        self.assertEqual(os.environ.get("POLYMATH_HOME"), str(self.home))  # main() must never see a real folder
        calls = []
        web = types.SimpleNamespace(serve=lambda store, **options: calls.append(options),
                                    serve_demo=lambda **options: calls.append(options))
        err = io.StringIO()
        given = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8") if isinstance(stdin, bytes) else io.StringIO(stdin)
        with mock.patch.object(engine, "load_web", return_value=web), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(sys, "stdin", given), redirect_stdout(io.StringIO()):
            code = engine.main(["serve", "--host", "127.0.0.1", "--port", "8781", "--tutor", "off", *args])
        return code, (calls[0] if calls else None), err.getvalue()

    def json_cli(self, *args, **kw):
        return json.loads(self.run_cli(*args, "--json", **kw).stdout)

    def setup_demo(self, data=None):
        self.run_cli("init", "--name", "Ada")
        path = Path(self.tmp.name) / "syllabus.json"
        path.write_text(json.dumps(data or syllabus()))
        return self.run_cli("track", "import", str(path))

    def view(self, cid):
        return self.json_cli("concept", cid)

    # --- setup and import ---------------------------------------------------
    def test_init_creates_profile_and_is_idempotent(self):
        self.run_cli("init", "--name", "Ada", "--persona", "Atlas")
        learner = json.loads((self.home / "learner.json").read_text())
        self.assertEqual(learner["name"], "Ada")
        self.assertEqual(learner["persona"]["name"], "Atlas")
        self.assertTrue((self.home / "learner.md").exists())
        (self.home / "learner.md").write_text("my notes")
        self.run_cli("init")
        self.assertEqual((self.home / "learner.md").read_text(), "my notes")
        self.assertEqual(json.loads((self.home / "learner.json").read_text())["persona"]["name"], "Atlas")

    def test_brief_never_fails_before_setup(self):
        proc = self.run_cli("brief")
        self.assertIn("not set up yet", proc.stdout)
        self.assertFalse(self.home.exists())                    # and makes no data folder of its own

    def test_commands_require_setup(self):
        proc = self.run_cli("next", ok=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("init", proc.stderr)

    def test_bundled_blueprint_imports_cleanly(self):
        self.run_cli("init", "--name", "Ada")
        out = self.json_cli("track", "import", str(BLUEPRINT), "--target-date", "2027-02-15")
        self.assertEqual(out["track"], "salesforce-pd1")
        self.assertGreaterEqual(out["concepts"], 40)
        self.assertEqual(out["warnings"], [])

    def test_reimport_keeps_status_and_progress(self):
        self.setup_demo()
        self.run_cli("track", "set", "demo", "status=paused")
        self.run_cli("record", "demo.basics.a", "correct")
        data = syllabus()
        data["domains"][0]["concepts"].append({"id": "demo.basics.d", "title": "Concept D"})
        path = Path(self.tmp.name) / "s2.json"
        path.write_text(json.dumps(data))
        out = self.json_cli("track", "import", str(path))
        self.assertTrue(out["replaced"])
        self.assertEqual(out["concepts"], 4)
        track = json.loads((self.home / "tracks" / "demo" / "track.json").read_text())
        self.assertEqual(track["status"], "paused")
        self.assertEqual(self.view("demo.basics.a")["attempts"], 1)

    def test_rejects_prerequisite_cycle(self):
        data = syllabus()
        data["domains"][0]["concepts"][0]["prereqs"] = ["demo.advanced.c"]
        self.run_cli("init")
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps(data))
        proc = self.run_cli("track", "import", str(path), ok=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cycle", proc.stderr)

    def test_rejects_unknown_prereq_bad_id_and_bad_weights(self):
        self.run_cli("init")
        cases = []
        d1 = syllabus()
        d1["domains"][0]["concepts"][0]["prereqs"] = ["demo.nowhere"]
        cases.append((d1, "not defined"))
        d2 = syllabus()
        d2["domains"][0]["concepts"][0]["id"] = "Not An Id"
        cases.append((d2, "dotted lowercase"))
        d3 = syllabus()
        d3["domains"][0]["weight"] = 0.9
        cases.append((d3, "must sum to 1.0"))
        for data, message in cases:
            path = Path(self.tmp.name) / "bad.json"
            path.write_text(json.dumps(data))
            proc = self.run_cli("track", "import", str(path), ok=False)
            self.assertIn(message, proc.stderr)
        self.assertFalse((self.home / "tracks" / "demo").exists())

    def test_general_tracks_normalize_weights(self):
        data = syllabus(kind="general")
        data["domains"][0]["weight"] = 3
        data["domains"][1]["weight"] = 1
        self.run_cli("init")
        path = Path(self.tmp.name) / "s.json"
        path.write_text(json.dumps(data))
        out = self.json_cli("track", "import", str(path))
        self.assertTrue(any("normalized" in w for w in out["warnings"]))
        track = json.loads((self.home / "tracks" / "demo" / "track.json").read_text())
        self.assertAlmostEqual(sum(d["weight"] for d in track["domains"]), 1.0, places=3)

    # --- mastery and memory ---------------------------------------------------
    def test_correct_raises_and_incorrect_lowers_mastery(self):
        self.setup_demo()
        up = self.json_cli("record", "demo.basics.a", "correct")
        self.assertGreater(up["after"], up["before"])
        down = self.json_cli("record", "demo.basics.a", "incorrect")
        self.assertLess(down["after"], down["before"])

    def test_recall_is_stronger_evidence_than_multiple_choice(self):
        self.setup_demo()
        mcq = self.json_cli("record", "demo.basics.a", "correct", "--qtype", "mcq")
        recall = self.json_cli("record", "demo.basics.b", "correct", "--qtype", "recall")
        self.assertGreater(recall["after"], mcq["after"])

    def test_mastery_needs_a_spaced_recall(self):
        self.setup_demo()
        for _ in range(3):
            self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        self.assertNotEqual(self.view("demo.basics.a")["status"], "mastered")
        self.clock = "2026-11-21T18:00:00Z"
        self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        self.assertEqual(self.view("demo.basics.a")["status"], "mastered")

    def test_wrong_answer_comes_back_sooner(self):
        self.setup_demo()
        wrong = self.json_cli("record", "demo.basics.a", "incorrect")
        right = self.json_cli("record", "demo.basics.b", "correct", "--confidence", "4")
        self.assertLess(wrong["review_in_days"], 1)
        self.assertGreater(right["review_in_days"], wrong["review_in_days"])

    def test_spaced_success_grows_interval_more_than_massed(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct")
        massed = self.json_cli("record", "demo.basics.a", "correct")["review_in_days"]
        self.run_cli("record", "demo.basics.b", "correct")
        self.clock = "2026-11-20T18:00:00Z"
        spaced = self.json_cli("record", "demo.basics.b", "correct")["review_in_days"]
        self.assertGreater(spaced, massed * 2)

    def test_memory_decays_over_time(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        fresh = self.view("demo.basics.a")["mastery"]
        self.clock = "2027-01-17T18:00:00Z"
        self.assertLess(self.view("demo.basics.a")["mastery"], fresh)

    def test_misconceptions_are_kept(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "incorrect", "--misconception", "mixes up A and B")
        self.assertIn("mixes up A and B", self.view("demo.basics.a")["misconceptions"])

    # --- planning ---------------------------------------------------------
    def test_prerequisites_gate_new_concepts(self):
        self.setup_demo()
        plan = self.json_cli("next", "--track", "demo")
        self.assertEqual([i["concept"] for i in plan["items"]], ["demo.basics.a"])
        self.assertEqual(plan["locked"], 2)
        self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        plan = self.json_cli("next", "--track", "demo")
        self.assertIn("demo.basics.b", [i["concept"] for i in plan["items"]])

    def test_mistakes_lead_the_next_session(self):
        self.setup_demo()
        self.run_cli("assess", "demo.basics.a=0.9", "demo.basics.b=0.9")
        self.run_cli("record", "demo.basics.a", "incorrect")
        self.clock = "2026-11-18T20:00:00Z"
        plan = self.json_cli("next", "--track", "demo")
        first = plan["items"][0]
        self.assertEqual((first["kind"], first["concept"]), ("mistake", "demo.basics.a"))
        self.assertEqual([i["concept"] for i in plan["items"]].count("demo.basics.a"), 1)

    def test_self_assessment_is_checked_later(self):
        self.setup_demo()
        self.run_cli("assess", "demo.basics.a=0.9", "--source", "self")
        self.assertEqual(self.view("demo.basics.a")["status"], "mastered")
        self.clock = "2026-11-23T18:00:00Z"
        self.assertEqual(self.view("demo.basics.a")["status"], "due")

    def test_concepts_are_shared_across_tracks(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        second = {
            "slug": "demo-two", "title": "Second track", "kind": "general",
            "domains": [{"id": "x", "name": "X", "weight": 1, "concepts": [
                {"id": "demo.basics.a", "title": "Concept A"},
                {"id": "demo.next.e", "title": "Concept E", "prereqs": ["demo.basics.a"], "related": ["demo.basics.a"]},
            ]}],
        }
        path = Path(self.tmp.name) / "two.json"
        path.write_text(json.dumps(second))
        self.run_cli("track", "import", str(path))
        report = self.json_cli("status", "--track", "demo-two")
        self.assertEqual(report["started"], 1)
        self.assertGreater(self.view("demo.next.e")["knowledge"], 0.1)  # related concept gives a head start

    # --- reporting ----------------------------------------------------------
    def test_readiness_verdict(self):
        self.setup_demo()
        self.assertEqual(self.json_cli("status", "--track", "demo")["verdict"], "not yet")
        self.run_cli("assess", "demo.basics.a=0.97", "demo.basics.b=0.97", "demo.advanced.c=0.97")
        self.assertEqual(self.json_cli("status", "--track", "demo")["verdict"], "ready")
        self.run_cli("mock", "demo", "55")
        self.assertNotEqual(self.json_cli("status", "--track", "demo")["verdict"], "ready")

    def test_readiness_verdict_agrees_with_the_booking_bar_at_the_boundary(self):
        # PD1's pass mark: 68% + 5 is 0.7300000000000001 in floating point, so a 73% mock used to fall short
        self.setup_demo(syllabus(exam={"passing_score": 0.68}))
        self.run_cli("assess", "demo.basics.a=0.97", "demo.basics.b=0.97", "demo.advanced.c=0.97")
        self.run_cli("mock", "demo", "73")
        self.assertEqual(self.json_cli("status", "--track", "demo")["verdict"], "ready")
        self.run_cli("mock", "demo", "72")
        self.assertEqual(self.json_cli("status", "--track", "demo")["verdict"], "close")
        self.assertEqual(engine.readiness_bars(0.68), (0.75, 0.73))
        # 80% + 7 is 0.8700000000000001: a predicted 87% is enough too
        self.home = Path(self.tmp.name) / "eighty"
        self.setup_demo(syllabus(exam={"passing_score": 0.80}))
        self.run_cli("assess", "demo.basics.a=0.8857", "demo.basics.b=0.8857", "demo.advanced.c=0.8857")
        report = self.json_cli("status", "--track", "demo")
        self.assertEqual((report["predicted_score"], report["verdict"]), (0.87, "ready"))

    def test_record_batch_and_undo(self):
        self.setup_demo()
        batch = json.dumps([
            {"concept": "demo.basics.a", "result": "correct", "qtype": "mcq", "confidence": 4},
            {"concept": "demo.basics.b", "result": "wrong", "misconception": "x"},
        ])
        out = self.json_cli("record-batch", "-", stdin=batch)
        self.assertEqual(out["recorded"], 2)
        self.assertEqual(out["score"], 0.5)
        self.run_cli("undo")
        self.assertEqual(self.view("demo.basics.b")["attempts"], 0)
        self.assertEqual(self.view("demo.basics.a")["attempts"], 1)

    def test_bad_batch_writes_nothing(self):
        self.setup_demo()
        before = (self.home / "events.jsonl").read_text()
        batch = json.dumps([{"concept": "demo.basics.a", "result": "correct"},
                            {"concept": "demo.basics.zzz", "result": "correct"}])
        proc = self.run_cli("record-batch", "-", stdin=batch, ok=False)
        self.assertIn("Unknown concept", proc.stderr)
        self.assertEqual((self.home / "events.jsonl").read_text(), before)

    def test_unknown_concept_suggests_a_match(self):
        self.setup_demo()
        proc = self.run_cli("record", "demo.basics.aa", "correct", ok=False)
        self.assertIn("demo.basics.a", proc.stderr)

    def test_session_writes_journal(self):
        self.setup_demo()
        self.run_cli("session", "start", "--track", "demo")
        self.clock = "2026-11-17T18:20:00Z"
        self.run_cli("record", "demo.basics.a", "correct")
        out = self.json_cli("session", "end", "--summary", "Covered A.")
        self.assertEqual(out["minutes"], 0.0)  # its one gap, 20 minutes, is longer than a working pause
        self.assertEqual(out["answers"], 1)
        self.assertIn("Covered A.", Path(out["journal"]).read_text())

    def test_profile_set_and_learner_report(self):
        self.setup_demo()
        self.run_cli("profile", "set", "preferences.session_minutes=45", "persona.tone=strict coach")
        learner = json.loads((self.home / "learner.json").read_text())
        self.assertEqual(learner["preferences"]["session_minutes"], 45)
        self.assertEqual(learner["persona"]["tone"], "strict coach")
        for i in range(10):
            self.run_cli("record", "demo.basics.a", "incorrect" if i % 2 else "correct", "--confidence", "5")
        report = self.json_cli("profile")
        self.assertGreater(report["calibration"]["gap"], 0.1)  # said certain, scored half

    def test_dashboard_snapshot_is_self_contained_and_escaped(self):
        self.setup_demo(syllabus(title="Demo <script>alert(1)</script>"))
        self.run_cli("record", "demo.basics.a", "correct")
        path = Path(self.json_cli("dashboard")["path"])
        page = path.read_text()
        self.assertNotIn("<script>alert", page)              # the title travels as escaped JSON
        self.assertIn("\\u003cscript\\u003ealert(1)", page)
        self.assertIn('id="polymath-data"', page)
        self.assertNotIn('src="app.js"', page)               # one file: styles and script are inline
        self.assertIn("prefers-color-scheme", page)


    # --- question-first learning ------------------------------------------------
    def test_difficulty_follows_answers(self):
        self.setup_demo()
        up = self.json_cli("record", "demo.basics.a", "correct", "--difficulty", "3", "--confidence", "4")
        self.assertEqual(up["next_difficulty"], 4)
        down = self.json_cli("record", "demo.basics.a", "incorrect", "--difficulty", "4", "--confidence", "2")
        self.assertEqual(down["next_difficulty"], 3)
        hesitant = self.json_cli("record", "demo.basics.b", "correct", "--difficulty", "3", "--confidence", "1")
        self.assertEqual(hesitant["next_difficulty"], 3)

    def test_signal_and_action(self):
        self.setup_demo()
        cases = [
            (["correct", "--confidence", "5"], ("solid", "advance")),
            (["correct", "--confidence", "1"], ("fragile", "confirm")),
            (["partial"], ("partial", "fill-gap")),
            (["incorrect", "--confidence", "2"], ("gap", "teach")),
            (["incorrect", "--confidence", "5"], ("misconception", "refute")),
            (["incorrect", "--error", "trap"], ("trap", "technique")),
            (["incorrect", "--confidence", "5", "--error", "gap"], ("gap", "teach")),  # "I was only guessing"
        ]
        for args, expected in cases:
            out = self.json_cli("record", "demo.basics.a", *args)
            self.assertEqual((out["signal"], out["action"]), expected, args)

    def test_two_misses_point_to_the_weak_prerequisite(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.b", "incorrect")
        out = self.json_cli("record", "demo.basics.b", "incorrect")
        self.assertEqual(out["action"], "check-prerequisites")
        self.assertEqual(out["prereqs_to_check"], ["demo.basics.a"])

    def test_a_repeated_misconception_is_refuted_again_not_sent_to_prerequisites(self):
        self.setup_demo()
        first = self.json_cli("record", "demo.basics.b", "incorrect", "--confidence", "5", "--misconception", "x")
        self.assertFalse(first["repeat"])
        second = self.json_cli("record", "demo.basics.b", "incorrect", "--confidence", "5",
                               "--targets", first["mistake"])
        self.assertEqual((second["action"], second["repeat"]), ("refute", True))
        self.assertEqual(second["prereqs_to_check"], [])

    def test_mistake_lifecycle(self):
        self.setup_demo()
        miss = self.json_cli("record", "demo.basics.a", "incorrect", "--misconception", "thinks A means B")
        mid = miss["mistake"]
        self.assertEqual(mid, "M1")
        fixing = self.json_cli("record", "demo.basics.a", "correct", "--targets", mid)
        self.assertEqual(fixing["fixing"], [mid])
        self.assertEqual(self.json_cli("mistakes")[0]["status"], "fixing")
        self.clock = "2026-11-19T18:00:00Z"
        resolved = self.json_cli("record", "demo.basics.a", "correct", "--targets", mid)
        self.assertEqual(resolved["resolved"], [mid])
        self.assertEqual(self.json_cli("mistakes"), [])
        again = self.json_cli("record", "demo.basics.a", "incorrect", "--misconception", "Thinks A means B.")
        self.assertEqual(again["mistake"], mid)  # the same wrong belief reopens the same mistake
        self.assertEqual(self.json_cli("mistakes")[0]["misses"], 2)

    def test_unknown_mistake_id_writes_nothing(self):
        self.setup_demo()
        before = (self.home / "events.jsonl").read_text()
        proc = self.run_cli("record", "demo.basics.a", "correct", "--targets", "M9", ok=False)
        self.assertIn("Unknown mistake", proc.stderr)
        self.assertEqual((self.home / "events.jsonl").read_text(), before)

    def test_slips_cost_less_than_real_misses(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        slip = self.json_cli("record", "demo.basics.a", "incorrect", "--error", "slip", "--seconds", "30")
        self.run_cli("record", "demo.basics.b", "correct", "--qtype", "recall")
        miss = self.json_cli("record", "demo.basics.b", "incorrect")
        self.assertGreater(slip["after"], miss["after"])
        self.assertIsNone(slip["mistake"])
        self.assertEqual(miss["mistake"], "M1")
        technique = self.json_cli("profile")["technique"]
        self.assertEqual((technique["slips"], technique["timed_answers"]), (1, 1))

    def test_a_hard_correct_answer_credits_prerequisites(self):
        self.setup_demo()
        easy = self.json_cli("record", "demo.basics.b", "correct", "--difficulty", "2", "--confidence", "5")
        self.assertEqual(easy["inferred"], [])
        out = self.json_cli("record", "demo.advanced.c", "correct", "--difficulty", "4", "--confidence", "5")
        self.assertEqual(out["inferred"], ["demo.basics.a"])  # b was already answered directly
        self.assertAlmostEqual(self.view("demo.basics.a")["knowledge"], 0.65, places=2)
        plan = self.json_cli("next", "--track", "demo")
        self.assertNotIn("demo.basics.a", [i["concept"] for i in plan["items"]])  # waits for its check
        self.clock = "2026-11-21T18:00:00Z"
        self.assertEqual(self.view("demo.basics.a")["status"], "due")

    def test_inference_reaches_two_levels(self):
        self.setup_demo()
        out = self.json_cli("record", "demo.advanced.c", "correct", "--difficulty", "5", "--confidence", "4")
        self.assertEqual(out["inferred"], ["demo.basics.b", "demo.basics.a"])
        self.assertAlmostEqual(self.view("demo.basics.b")["knowledge"], 0.75, places=2)

    def test_placement_sets_probe_order(self):
        self.setup_demo()
        self.run_cli("profile", "set", "preferences.start_level=expert")
        plan = self.json_cli("next", "--track", "demo")
        self.assertEqual(plan["mode"], "top-down")
        self.assertEqual(plan["items"][0]["concept"], "demo.advanced.c")  # deepest first, no gating
        self.assertEqual(plan["locked"], 0)
        self.run_cli("profile", "set", "preferences.start_level=beginner")
        plan = self.json_cli("next", "--track", "demo")
        self.assertEqual(plan["mode"], "primer")
        self.assertEqual([i["concept"] for i in plan["items"]], ["demo.basics.a"])
        self.assertIn("primer", plan["items"][0]["format"])

    def test_probe_quota_grows_with_first_try_accuracy(self):
        data = syllabus(kind="general")
        data["domains"] = [{"id": "x", "name": "X", "weight": 1, "concepts": [
            {"id": f"demo.many.c{i}", "title": f"Concept {i}"} for i in range(15)]}]
        self.setup_demo(data)
        self.run_cli("profile", "set", "preferences.start_level=beginner")
        slow = self.json_cli("next", "--track", "demo")["new_quota"]
        for i in range(8):
            self.run_cli("record", f"demo.many.c{i}", "correct", "--confidence", "5")
        plan = self.json_cli("next", "--track", "demo")
        self.assertGreater(plan["new_quota"], slow)
        self.assertGreater(plan["placement"], 0.65)

    def test_one_miss_does_not_knock_an_expert_out_of_top_down(self):
        self.setup_demo()
        self.run_cli("profile", "set", "preferences.start_level=expert")
        self.run_cli("record", "demo.advanced.c", "incorrect")
        self.assertEqual(self.json_cli("next", "--track", "demo")["mode"], "top-down")
        self.run_cli("record", "demo.basics.b", "incorrect")
        self.assertEqual(self.json_cli("next", "--track", "demo")["mode"], "bottom-up")

    def test_a_misconception_is_reasked_at_the_same_level(self):
        self.setup_demo()
        out = self.json_cli("record", "demo.basics.a", "incorrect", "--difficulty", "3", "--confidence", "5")
        self.assertEqual((out["action"], out["next_difficulty"]), ("refute", 3))
        out = self.json_cli("record", "demo.basics.b", "incorrect", "--difficulty", "3", "--confidence", "2")
        self.assertEqual((out["action"], out["next_difficulty"]), ("teach", 2))

    def test_pace_is_not_judged_on_a_handful_of_answers(self):
        self.setup_demo()
        self.run_cli("track", "set", "demo", "target_date=2027-01-31")
        self.run_cli("record", "demo.basics.a", "incorrect")
        self.clock = "2026-11-21T18:00:00Z"
        self.assertEqual(self.json_cli("status", "--track", "demo")["projection"]["status"], "too early to tell")

    def web_event(self, kind="session_end", **fields):
        """A record the web dashboard wrote (tagged with its session), appended as the dashboard does."""
        with (self.home / "events.jsonl").open("a") as f:
            f.write(json.dumps({"type": kind, "ts": self.clock, "session": "0123456789abcdef", "source": "web",
                                **fields}) + "\n")

    def test_undo_steps_past_the_dashboard_only_to_recent_tutor_records(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct")
        self.clock = "2026-11-17T18:05:00Z"
        self.web_event()                                    # the dashboard closed an idle session after it
        self.run_cli("undo")                                # the tutor's own answer is still the one undone
        self.assertEqual(self.view("demo.basics.a")["attempts"], 0)
        self.clock = "2026-11-19T18:00:00Z"
        self.web_event()
        before = (self.home / "events.jsonl").read_text()
        proc = self.run_cli("undo", ok=False)               # the next tutor record is the import, two days old
        self.assertIn("web dashboard", proc.stderr)
        self.assertEqual((self.home / "events.jsonl").read_text(), before)

    def test_undo_stops_at_an_answer_already_voided_in_the_dashboard(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct")                   # A1
        a2 = self.json_cli("record", "demo.basics.b", "incorrect")["attempt"]
        self.web_event("void", target=a2, concept="demo.basics.b", reason="voided on its concept page")
        before = (self.home / "events.jsonl").read_text()
        proc = self.run_cli("undo", ok=False)              # the tutor means A2, which the learner voided already
        self.assertIn(f"{a2}, was already voided", proc.stderr)
        self.assertEqual((self.home / "events.jsonl").read_text(), before)  # A1 is left alone
        self.assertEqual(self.view("demo.basics.a")["attempts"], 1)

    def test_void_removes_a_disputed_answer(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct", "--qtype", "recall")
        self.run_cli("record", "demo.basics.a", "incorrect", "--misconception", "x")
        out = self.json_cli("void", "--concept", "demo.basics.a")
        self.assertGreater(out["after"], out["before"])
        self.assertEqual(self.view("demo.basics.a")["attempts"], 1)
        self.assertEqual(self.json_cli("mistakes"), [])
        nxt = self.json_cli("record", "demo.basics.b", "incorrect")
        self.assertEqual((nxt["attempt"], nxt["mistake"]), ("A3", "M2"))  # ids never reused

    def test_a_re_ask_of_a_voided_mistake_credits_no_other_mistake(self):
        self.setup_demo()
        m1 = self.json_cli("record", "demo.basics.a", "incorrect", "--misconception", "thinks A runs async")["mistake"]
        self.clock = "2026-11-18T18:00:00Z"                                  # the next day
        miss = self.json_cli("record", "demo.basics.a", "incorrect", "--misconception", "thinks A needs a lock")
        m2 = miss["mistake"]
        fix = self.json_cli("record", "demo.basics.a", "correct", "--targets", m2)   # re-asks M2 alone
        self.assertEqual((fix["fixing"], fix["resolved"]), ([m2], []))
        self.run_cli("void", miss["attempt"], "--reason", "that question's key was wrong")
        rows = {m["id"]: m for m in self.json_cli("mistakes", "--status", "all")}
        self.assertEqual(list(rows), [m1])                                    # M2 went with its answer...
        self.assertEqual((rows[m1]["status"], rows[m1]["probes"]), ("open", 0))  # ...M1 was never re-asked
        plan = self.json_cli("next", "--track", "demo")
        self.assertIn(m1, [(i.get("mistake") or {}).get("id") for i in plan["items"]])  # so it is still due
        late = self.json_cli("record", "demo.basics.a", "correct", "--targets", m2)  # its re-ask, answered now
        self.assertEqual((late["fixing"], late["resolved"]), ([], []))
        self.assertEqual(self.json_cli("mistakes")[0]["status"], "open")
        self.assertIn("Unknown mistake",
                      self.run_cli("record", "demo.basics.a", "correct", "--targets", "M9", ok=False).stderr)

    def test_undo_count_across_a_void_matches_undo_one_at_a_time(self):
        outcomes = []
        for split in (True, False):
            self.home, self.clock = Path(self.tmp.name) / ("split" if split else "count"), DAY1
            self.setup_demo()
            self.run_cli("record", "demo.basics.a", "correct")                                     # A1
            self.clock = "2026-11-17T18:01:00Z"
            self.run_cli("record", "demo.basics.b", "incorrect", "--misconception", "thinks B is A")  # A2, M1
            self.clock = "2026-11-17T18:02:00Z"
            self.run_cli("void", "A2", "--reason", "bad key")
            self.clock = "2026-11-17T18:03:00Z"
            if split:
                self.run_cli("undo")
                self.run_cli("undo")
            else:                                                 # take back the void, then A2 itself
                undone = self.json_cli("undo", "--count", "2")
                self.assertEqual([(e["type"], e.get("id") or e.get("target")) for e in undone],
                                 [("void", "A2"), ("attempt", "A2")])
            outcomes.append((self.view("demo.basics.a")["attempts"], self.view("demo.basics.b")["attempts"],
                             self.json_cli("mistakes", "--status", "all")))
        self.assertEqual(outcomes[1], outcomes[0])
        self.assertEqual(outcomes[1], (1, 0, []))                 # A1 kept; A2 and its mistake gone

    def test_undo_steps_past_dashboard_changes_made_outside_a_session(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct")                                         # A1
        self.clock = "2026-11-17T18:01:00Z"
        self.run_cli("record", "demo.basics.b", "incorrect", "--misconception", "thinks B is A")      # A2, M1
        self.run_cli("record", "demo.advanced.c", "correct")                                      # A3
        store = self.in_process()
        self.at("2026-11-17T18:05:00Z")
        # on dashboard pages, not in a study session: void A2, log a practice mock, move the exam date
        engine.void_attempt(store, attempt="A2", reason="its key was wrong", source="web")
        engine.log_mock(store, "demo", 80, source="web")
        engine.update_track(store, "demo", {"target_date": "2027-01-31"}, source="web")
        mine = [e for e in store.events() if e.get("source") == "web"]
        self.assertEqual([e["type"] for e in mine], ["void", "mock", "track_update"])
        self.assertFalse(any(e.get("session") for e in mine))
        undone = self.json_cli("undo")
        self.assertEqual([(e["type"], e.get("id")) for e in undone], [("attempt", "A3")])  # the tutor's own last
        after = store.events()
        self.assertTrue(all(e in after for e in mine))           # the dashboard's changes are all still there
        self.assertEqual([self.view(c)["attempts"] for c in ("demo.basics.a", "demo.basics.b", "demo.advanced.c")],
                         [1, 0, 0])                                # A2 stays voided, A3 is taken back
        self.assertEqual(self.json_cli("mistakes"), [])
        self.assertTrue(engine.dashboard_record({"source": "web-page"}))
        self.assertFalse(engine.dashboard_record({"source": "diagnostic"}))

    # --- writing safely -------------------------------------------------------
    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_the_data_folder_and_what_the_engine_writes_are_private(self):
        self.home.mkdir(mode=0o755)
        os.chmod(self.home, 0o755)                               # as an older version left it
        path = Path(self.tmp.name) / "syllabus.json"
        path.write_text(json.dumps(syllabus()))
        for args in (("init", "--name", "Ada"), ("track", "import", str(path)), ("session", "start"),
                     ("record", "demo.basics.a", "correct"), ("session", "end", "--summary", "A")):
            self.run_cli(*args, umask=0o022)                     # under the usual umask
        self.assertEqual(mode(self.home), 0o700)
        files = [self.home / name for name in ("learner.json", "learner.md", "README.md", "events.jsonl", ".lock",
                                               "concepts.json")]
        files += [self.home / "tracks" / "demo" / "track.json"] + list((self.home / "journal").glob("*.md"))
        self.assertEqual(len(files), 8)
        for p in files + [self.home / "tracks", self.home / "tracks" / "demo", self.home / "journal"]:
            with self.subTest(path=str(p.relative_to(self.home))):
                self.assertEqual(mode(p) & 0o077, 0, oct(mode(p)))
        self.in_process()
        os.chmod(self.home, 0o755)
        token = Path(self.tmp.name) / "token"
        token.write_text("a-long-random-token")
        mask = os.umask(0o022)
        try:
            self.assertEqual(self.serve_in_process("--token-file", str(token))[0], 0)
            self.assertEqual(mode(self.home), 0o700)              # serving it tightens it again
            self.assertEqual(os.umask(0o022), 0o022)              # and main() puts the umask back
            fresh = engine.Store(Path(self.tmp.name) / "fresh")   # a folder first made by a writer, not `init`
            fresh.append({"type": "note"})
            self.assertEqual(mode(fresh.root), 0o700)
        finally:
            os.umask(mask)

    @unittest.skipIf(fcntl is None, "needs POSIX file locks")
    def test_profile_set_and_init_take_turns_with_the_dashboard(self):
        self.setup_demo()
        learner_path = self.home / "learner.json"
        for args, wanted in ((("profile", "set", "preferences.session_minutes=45"), ("session_minutes", 45)),
                             (("init", "--name", "Bea"), ("name", "Bea"))):
            with self.subTest(command=args[0]):
                with self.lock_held():                            # the dashboard is saving a setting
                    proc = self.start_cli(*args)
                    with self.assertRaises(subprocess.TimeoutExpired):
                        proc.wait(timeout=0.8)                    # so the command waits its turn
                    learner = json.loads(learner_path.read_text())
                    learner["preferences"]["block_size"] = 5
                    learner_path.write_text(json.dumps(learner))
                out, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err)
                learner = json.loads(learner_path.read_text())
                key, value = wanted
                self.assertEqual((learner["preferences"] if key == "session_minutes" else learner)[key], value)
                self.assertEqual(learner["preferences"]["block_size"], 5)    # neither change is lost

    def test_bad_numbers_in_a_batch_are_errors_not_crashes(self):
        self.setup_demo()
        before = (self.home / "events.jsonl").read_text()
        for field, raw in (("difficulty", '"hard"'), ("confidence", '"high"'), ("options", '"four"'),
                           ("difficulty", "true"), ("confidence", "[4]"), ("difficulty", "NaN")):
            with self.subTest(field=field, value=raw):
                batch = f'[{{"concept": "demo.basics.a", "result": "correct", "{field}": {raw}}}]'
                proc = self.run_cli("record-batch", "-", stdin=batch, ok=False)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(f"error: {field} must be a whole number", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
        proc = self.run_cli("record-batch", "-", stdin='["demo.basics.a"]', ok=False)
        self.assertEqual((proc.returncode, "must be a JSON object" in proc.stderr), (2, True))
        self.assertEqual((self.home / "events.jsonl").read_text(), before)    # nothing was written
        batch = [{"concept": "demo.basics.a", "result": "correct", "difficulty": None, "confidence": None,
                  "options": None},                                            # null: not given
                 {"concept": "demo.basics.b", "result": "correct", "difficulty": "4", "confidence": 4.0,
                  "options": "5"}]                                             # numbers as text still count
        self.assertEqual(self.json_cli("record-batch", "-", stdin=json.dumps(batch))["recorded"], 2)
        written = [json.loads(line) for line in (self.home / "events.jsonl").read_text().splitlines()][-2:]
        self.assertEqual([(e["difficulty"], e.get("confidence"), e.get("guess")) for e in written],
                         [(3, None, None), (4, 4, 0.2)])

    def test_a_mock_whose_areas_are_not_an_object_is_refused(self):
        self.setup_demo()
        store = self.in_process()
        for by_domain in ([["basics", 0.5]], "basics=0.5", True, 5):
            with self.subTest(by_domain=by_domain):
                with self.assertRaisesRegex(engine.PolymathError, "by_domain must map area ids"):
                    engine.log_mock(store, "demo", 0.8, by_domain)
        self.assertEqual([e for e in store.events() if e["type"] == "mock"], [])
        self.assertEqual(engine.log_mock(store, "demo", 80, {"basics": 75})["by_domain"], {"basics": 0.75})

    def test_a_repeated_ref_reports_the_answer_it_repeats(self):
        self.setup_demo()
        store = self.in_process()
        registry = store.concepts()
        item = {"concept": "demo.basics.a", "result": "incorrect", "track": "demo", "ref": "x:1"}
        first, again = engine.record_events(store, [engine.build_attempt(item, registry),
                                                    engine.build_attempt(item, registry)])
        self.assertEqual((first["attempt"], first["mistake"]), ("A1", "M1"))
        self.assertEqual({k: again[k] for k in ("attempt", "duplicate", "mistake")},
                         {"attempt": "A1", "duplicate": True, "mistake": "M1"})
        later = engine.record_events(store, [engine.build_attempt(item, registry)])[0]   # already in the log
        self.assertEqual((later["attempt"], later["duplicate"]), ("A1", True))
        self.assertEqual(sum(1 for e in store.events() if e["type"] == "attempt"), 1)
        batch = json.dumps([dict(item, ref="x:2"), dict(item, ref="x:2", result="correct")])
        out = self.run_cli("record-batch", "-", stdin=batch).stdout        # the CLI says so instead of crashing
        self.assertIn("Recorded 1 answers (0%); 1 already recorded.", out)
        self.assertIn("A2 demo.basics.a: already recorded", out)
        out = self.json_cli("record-batch", "-", stdin=json.dumps([item]))
        self.assertEqual((out["recorded"], out["score"], out["answers"][0]["attempt"]), (0, None, "A1"))

    def test_serve_takes_the_token_from_a_file_or_stdin_not_the_command_line(self):
        self.setup_demo()
        self.in_process()
        os.environ.pop("POLYMATH_WEB_TOKEN", None)
        path = Path(self.tmp.name) / "token"
        path.write_text("  s3cret-token-from-a-file \n")
        code, options, err = self.serve_in_process("--token-file", str(path))
        self.assertEqual((code, options["token"], err), (0, "s3cret-token-from-a-file", ""))
        code, options, err = self.serve_in_process("--token-file", "-", stdin="from-stdin\n")
        self.assertEqual((code, options["token"], err), (0, "from-stdin", ""))
        for content, message in (("\n", "empty"), ("two words", "one word")):
            path.write_text(content)
            code, options, err = self.serve_in_process("--token-file", str(path))
            self.assertEqual((code, options), (2, None))
            self.assertIn(message, err)
        code, _, err = self.serve_in_process("--token-file", str(Path(self.tmp.name) / "missing"))
        self.assertEqual((code, "Can't read the token" in err), (2, True))
        # as Windows tools save text: a UTF-8 byte-order mark (Notepad, PowerShell's -Encoding UTF8), or
        # UTF-16 (PowerShell's > and Out-File); the mark is not part of the token
        for data in (b"\xef\xbb\xbfbom-token-abcdef\r\n", "ps-token-abcdef\r\n".encode("utf-16")):  # (mark + text)
            with self.subTest(data=data[:4]):
                path.write_bytes(data)
                code, options, err = self.serve_in_process("--token-file", str(path))
                self.assertEqual((code, err), (0, ""))
                self.assertIn(options["token"], ("bom-token-abcdef", "ps-token-abcdef"))
        code, options, _ = self.serve_in_process("--token-file", "-", stdin=b"\xef\xbb\xbfpiped-token\n")
        self.assertEqual((code, options["token"]), (0, "piped-token"))
        path.write_text("zero\u200bwidth", encoding="utf-8")          # an invisible character inside it
        code, options, err = self.serve_in_process("--token-file", str(path))
        self.assertEqual((code, options), (2, None))
        self.assertIn("invisible character (U+200B)", err)
        code, options, err = self.serve_in_process("--token", "on-the-command-line")
        self.assertEqual((code, options["token"]), (0, "on-the-command-line"))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("warning: --token", err)
        self.assertIn("--token-file", err)
        os.environ["POLYMATH_WEB_TOKEN"] = "from-the-environment"          # the other private way: no warning
        code, options, err = self.serve_in_process()
        self.assertEqual((code, options["token"], err), (0, "from-the-environment", ""))
        both = self.run_cli("serve", "--token", "a", "--token-file", str(path), ok=False)
        self.assertEqual((both.returncode, "not allowed with" in both.stderr), (2, True))

    # --- what the dashboard's study sessions rely on ---------------------------
    def test_answers_can_be_stamped_with_when_they_were_given(self):
        self.setup_demo()
        store = self.in_process()
        registry = store.concepts()

        def answer(cid, result="correct"):
            return engine.build_attempt({"concept": cid, "result": result, "qtype": "recall", "track": "demo"}, registry)

        engine.record_events(store, [answer("demo.basics.a")])
        self.at("2026-12-17T18:00:00Z")                                      # a month on...
        given = engine.parse_ts("2026-12-01T09:30:00Z")                      # ...an answer given two weeks in
        then, today = engine.Model(store, at=given), engine.Model(store)
        before = round(then.mastery(then.state("demo.basics.a")), 3)
        r = engine.record_events(store, [answer("demo.basics.a")], at=given)[0]
        self.assertEqual((store.events()[-1]["ts"], r["before"]), ("2026-12-01T09:30:00Z", before))
        self.assertGreater(r["before"], today.mastery(today.state("demo.basics.a")))  # as of then, not now
        early = engine.parse_ts("2026-11-20T00:00:00Z")                      # given before the Dec 1 answer,
        mid = engine.Model(store, at=early, until=early)                      # logged after it
        self.assertEqual(mid.state("demo.basics.a").attempts, 1)             # (as of then, only the first)
        r = engine.record_events(store, [answer("demo.basics.b"), answer("demo.basics.a")], at=early)[1]
        self.assertEqual([e["ts"] for e in store.events()[-2:]], ["2026-11-20T00:00:00Z"] * 2)  # dated as given
        self.assertEqual(r["before"], round(mid.mastery(mid.state("demo.basics.a")), 3))  # worked out as of then
        self.assertNotEqual(r["before"], before)                             # not with Dec 1's answer in
        engine.record_events(store, [answer("demo.advanced.c")], at=engine.parse_ts("2027-06-01T00:00:00Z"))
        self.assertEqual(store.events()[-1]["ts"], "2026-12-17T18:00:00Z")   # never after now
        engine.record_events(store, [answer("demo.basics.a", "incorrect")])
        self.assertEqual(store.events()[-1]["ts"], "2026-12-17T18:00:00Z")   # without `at`: now, as before
        self.assertEqual([e["id"] for e in store.events() if e["type"] == "attempt"],
                         ["A1", "A2", "A3", "A4", "A5", "A6"])
        model = engine.Model(store)                                          # replayed in the order they were given
        self.assertEqual([e["id"] for e in model.events if e.get("type") == "attempt"],
                         ["A1", "A3", "A4", "A2", "A5", "A6"])
        self.assertEqual((model.state("demo.basics.b").attempts, model.state("demo.basics.a").attempts), (1, 4))
        with self.assertRaisesRegex(TypeError, "not date"):                  # a day is not a moment
            engine.record_events(store, [answer("demo.basics.b")], at=engine.date(2026, 12, 17))
        with self.assertRaisesRegex(engine.PolymathError, "not a date and time"):
            engine.record_events(store, [answer("demo.basics.b")], at="yesterday")
        self.assertEqual(sum(1 for e in store.events() if e["type"] == "attempt"), 6)  # neither was written

    def test_answers_logged_late_are_replayed_when_they_were_given(self):
        """A mock submitted by the clock after a pause is logged after records that came later. Before, it
        was dated after them: a right answer given before a miss fixed the mistake that miss opened, and a
        miss given before a hard answer on what builds on it was replayed as 'known before'."""
        self.setup_demo()
        self.at("2026-11-16T19:30:00Z")
        store = self.in_process()
        registry = store.concepts()

        def attempt(cid, result, **kw):
            return engine.build_attempt({"concept": cid, "result": result, "qtype": "mcq", "track": "demo", **kw},
                                        registry)
        engine.record_events(store, [attempt("demo.basics.a", "incorrect", confidence=2)])     # Claude Code: M1
        engine.record_events(store, [attempt("demo.advanced.c", "correct", confidence=5, difficulty=5)])  # credits B
        self.assertTrue(engine.Model(store).state("demo.basics.b").known_before)
        self.at("2026-11-16T20:00:00Z")                                      # the dashboard starts again:
        sat = engine.parse_ts("2026-11-15T18:44:00Z")                        # its mock, sat the day before
        r = engine.record_events(store, [attempt("demo.basics.a", "correct", confidence=4)], at=sat)[0]
        self.assertEqual((r["fixing"], r["resolved"]), ([], []))             # M1 didn't exist yet then
        self.assertEqual(engine.Model(store).mistakes["M1"]["status"], "open")  # was: fixing, by an earlier answer
        engine.record_events(store, [attempt("demo.basics.b", "incorrect", confidence=2)],
                             at=engine.parse_ts("2026-11-15T18:45:00Z"))
        model = engine.Model(store)
        self.assertFalse(model.state("demo.basics.b").known_before)         # was: credited first, then missed
        self.assertEqual([e["id"] for e in model.events if e.get("type") == "attempt"], ["A3", "A4", "A1", "A2"])

    def test_a_late_guess_is_not_merged_into_another_mistake_through_a_re_ask(self):
        """Round 4: the model worked out as of a late answer took its mistake index from every later attempt,
        re-asks included, so a guess (a gap) took the id of a mistake built on a stated belief."""
        self.setup_demo()
        self.at("2026-11-16T19:30:00Z")
        store = self.in_process()
        registry = store.concepts()

        def attempt(result, **kw):
            return engine.build_attempt({"concept": "demo.basics.a", "result": result, "qtype": "mcq",
                                         "track": "demo", **kw}, registry)
        engine.record_events(store, [attempt("incorrect", confidence=5, misconception="A works like B")])  # M1
        engine.record_events(store, [attempt("incorrect", confidence=2, targets="M1")])  # a re-ask, missed
        r = engine.record_events(store, [attempt("incorrect", confidence=2, error="gap")],
                                 at=engine.parse_ts("2026-11-15T18:12:00Z"))[0]   # a mock's guess, the day before
        self.assertEqual(r["mistake"], "M2")                                 # was: M1
        model = engine.Model(store)
        self.assertEqual((model.mistakes["M1"]["error"], model.mistakes["M1"]["belief"]), ("misconception", True))
        again = engine.record_events(store, [attempt("incorrect", confidence=5, misconception="A works like B")])[0]
        self.assertEqual((again["mistake"], again["repeat"]), ("M1", True))  # the belief is still recognised

    def test_void_by_concept_takes_the_tutors_latest_answer_by_time(self):
        self.setup_demo()
        self.at("2026-11-16T18:05:00Z")
        store = self.in_process()
        registry = store.concepts()

        def attempt(result, **kw):
            return engine.build_attempt({"concept": "demo.basics.a", "result": result, "qtype": "mcq",
                                         "track": "demo", **kw}, registry)
        mine = engine.record_events(store, [attempt("incorrect", confidence=2)])[0]        # the tutor, 18:05
        engine.record_events(store, [attempt("incorrect", confidence=5, source="web", session=SESSION,
                                             ref=f"web:{SESSION}:q1")],
                             at=engine.parse_ts("2026-11-16T18:01:00Z"))                  # the dashboard, logged late
        out = engine.void_attempt(store, concept="demo.basics.a", reason="the key was wrong")
        self.assertEqual(out["attempt"], mine["attempt"])                    # was: the dashboard's answer

    def test_a_record_made_after_the_clock_stepped_back_still_comes_last(self):
        self.at("2026-11-16T18:10:00Z")
        self.setup_demo()
        store = self.in_process()
        registry = store.concepts()

        def attempt(result, **kw):
            return engine.build_attempt({"concept": "demo.basics.a", "result": result, "qtype": "mcq",
                                         "track": "demo", **kw}, registry)
        engine.record_events(store, [attempt("incorrect", confidence=2)])                 # opens M1 at 18:10
        self.at("2026-11-16T18:07:00Z")                                      # the clock steps back 3 minutes
        r = engine.record_events(store, [attempt("correct", confidence=4, targets="M1")])[0]
        self.assertEqual((r["fixing"], store.events()[-1]["ts"]), (["M1"], "2026-11-16T18:10:00Z"))
        self.assertEqual(engine.Model(store).mistakes["M1"]["status"], "fixing")  # was: open, replayed first

    def test_end_session_can_be_dated_when_it_ended(self):
        self.setup_demo()
        self.at("2026-11-16T08:00:00Z")                                      # found idle the next morning
        store = self.in_process()
        out = engine.end_session(store, track="demo", summary="Learn in the dashboard: 3 answers.", session=SESSION,
                                 minutes=12, answers=3, accuracy=1.0, at=engine.parse_ts("2026-11-15T18:20:00Z"))
        self.assertTrue(out["journal"].endswith("2026-11-15.md"))           # the day it ended, not the wake-up
        self.assertEqual(store.events()[-1]["ts"], "2026-11-15T18:20:00Z")

    def test_learning_since_is_the_learners_own_day(self):
        self.at("2026-11-16T01:30:00Z")                                      # 17:30 on Nov 15 in California
        self.setup_demo()
        store = self.in_process()
        with mock.patch.dict(os.environ, {"TZ": "America/Los_Angeles"}):
            tzset()
            engine.record_events(store, [engine.build_attempt({"concept": "demo.basics.a", "result": "correct",
                                                               "track": "demo"}, store.concepts())])
            self.assertEqual(engine.Model(store).learner_report()["since"], "2026-11-15")  # was: Nov 16 (UTC)
        tzset()

    def test_mock_dates_are_the_learners_own_day(self):
        self.setup_demo()
        store = self.in_process()
        with mock.patch.dict(os.environ, {"TZ": "America/Los_Angeles"}):
            tzset()
            engine.log_mock(store, "demo", 0.8, questions=10, at=engine.parse_ts("2026-11-16T02:08:00Z"))
            rep = engine.Model(store).track_report(store.track("demo"))
            self.assertEqual(rep["mocks"][-1]["date"], "2026-11-15")         # was: the UTC day, Nov 16
        tzset()

    def test_log_mock_takes_the_write_lock_and_logs_a_session_once(self):
        self.setup_demo()
        store = self.in_process()
        first = engine.log_mock(store, "demo", 0.8, questions=10, source="web", session=SESSION)
        again = engine.log_mock(store, "demo", 0.8, questions=10, source="web", session=SESSION)
        mocks = [e for e in store.events() if e["type"] == "mock"]
        self.assertEqual(len(mocks), 1)
        self.assertEqual(again, first)                                       # the mock already in the log,
        self.assertEqual(list(again), list(engine.MOCK_FIELDS))              # in one shape either way:
        self.assertEqual((again["ts"], again["minutes"], again["by_domain"]), (mocks[0]["ts"], None, None))
        engine.log_mock(store, "demo", 0.7)
        engine.log_mock(store, "demo", 0.7)                                  # without a session: two mocks
        self.assertEqual(sum(1 for e in store.events() if e["type"] == "mock"), 3)
        sat = engine.parse_ts(mocks[0]["ts"]) - engine.timedelta(hours=3)    # sat earlier, logged now:
        dated = engine.log_mock(store, "demo", 0.75, source="web", session="00112233445566aa", at=sat)
        self.assertEqual(dated["ts"], engine.iso(sat))                       # dated when it was sat
        later = engine.log_mock(store, "demo", 0.75, at=engine.now() + engine.timedelta(days=2))
        self.assertEqual(later["ts"], engine.iso(engine.now()))              # never after now
        self.assertEqual(sum(1 for e in store.events() if e["type"] == "mock"), 5)
        other, done = "fedcba9876543210", []
        with store.writing():                                                # another writer holds the lock...
            worker = threading.Thread(target=lambda: done.append(
                engine.log_mock(store, "demo", 0.9, source="web", session=other)))
            worker.start()
            worker.join(0.5)
            self.assertTrue(worker.is_alive())                               # ...so the mock waits, unread,
            store.append({"type": "mock", "track": "demo", "score": 0.9, "source": "web", "session": other})
        worker.join(30)
        self.assertEqual(sum(1 for e in store.events() if e.get("session") == other), 1)  # and sees it after
        self.assertEqual(done[0]["session"], other)

    def test_end_session_closes_a_dashboard_session_once(self):
        self.setup_demo()
        store = self.in_process()
        store.append({"type": "session_start", "track": "demo", "session": SESSION, "source": "web"})
        first = engine.end_session(store, track="demo", summary="Covered A.", session=SESSION, minutes=12,
                                   answers=3, accuracy=2 / 3)
        self.at("2026-11-17T19:00:00Z")
        again = engine.end_session(store, track="demo", summary="Covered A.", session=SESSION, minutes=14,
                                   answers=3, accuracy=2 / 3)
        self.assertEqual(sum(1 for e in store.events() if e["type"] == "session_end"), 1)
        self.assertEqual(set(again), set(first))                             # the same kind of result
        self.assertEqual((again["minutes"], again["journal"], again["journal_error"]), (12.0, first["journal"], None))
        self.assertEqual(Path(first["journal"]).read_text().count("Covered A."), 1)

    def test_a_journal_that_cannot_be_written_still_closes_the_session(self):
        self.setup_demo()
        store = self.in_process()
        shutil.rmtree(store.journal_dir)
        store.journal_dir.write_text("a file where the journal folder should be")
        for _ in range(2):                                                   # a retry adds nothing
            out = engine.end_session(store, track="demo", summary="x", session=SESSION, minutes=5, answers=1,
                                     accuracy=1.0)
            self.assertIsNone(out["journal"])
            self.assertEqual(sum(1 for e in store.events() if e["type"] == "session_end"), 1)  # it still counts
        self.assertIn("journal entry couldn't be written", engine.end_session(store, summary="cli")["journal_error"])
        self.run_cli("session", "start", "--track", "demo")
        proc = self.run_cli("session", "end", "--summary", "y")              # the CLI's close still succeeds
        self.assertIn("No journal entry was written", proc.stdout)
        self.assertIn("warning: The session was closed", proc.stderr)

    def test_a_repeat_close_reports_the_journal_entry_the_session_got(self):
        self.setup_demo()
        store = self.in_process()
        journal = store.journal_dir / "2026-11-17.md"

        def close(session, summary="Covered A."):
            return engine.end_session(store, track="demo", summary=summary, session=session, minutes=12, answers=3,
                                      accuracy=0.5)

        shutil.rmtree(store.journal_dir)
        store.journal_dir.write_text("a file where the journal folder should be")
        self.assertIsNone(close(SESSION)["journal"])                          # the first close couldn't write it
        store.journal_dir.unlink()
        store.journal_dir.mkdir()
        journal.write_text("# 2026-11-17\n\n## 17:30 · demo · another session's entry\n")
        again = close(SESSION)                                                # a retry, once it could be written
        self.assertIsNone(again["journal"])                                   # still says there is no entry
        self.assertIn("couldn't be written then", again["journal_error"])
        self.assertNotIn("Covered A.", journal.read_text())
        ends = [(e["session"], e["journal"]) for e in store.events() if e["type"] == "session_end"]
        self.assertEqual(ends, [(SESSION, False)])                            # the end says so itself
        store.append({"type": "session_end", "track": "demo", "minutes": 5.0, "session": "a" * 16, "source": "web"})
        older = close("a" * 16)                                               # an end logged before ends said
        self.assertEqual((older["journal"], older["journal_error"]), (str(journal), None))  # goes by the file
        before = journal.read_text()
        with mock.patch.object(store, "append", side_effect=OSError("disk full")), self.assertRaises(OSError):
            close("b" * 16, "Lost?")                                          # the end can't be logged...
        self.assertEqual(journal.read_text(), before)                         # ...so its entry is taken back
        done = close("b" * 16, "Lost?")                                       # and a retry writes it once
        self.assertEqual((done["journal"], journal.read_text().count("Lost?")), (str(journal), 1))
        self.at("2026-11-18T09:00:00Z")                                       # a day whose file is new
        with mock.patch.object(store, "append", side_effect=OSError("disk full")), self.assertRaises(OSError):
            close("c" * 16)
        self.assertFalse((store.journal_dir / "2026-11-18.md").exists())

    def test_one_event_that_cannot_be_logged_leaves_the_log_untouched(self):
        self.setup_demo()
        store = self.in_process()
        before = store.events_path.read_text()
        with self.assertRaisesRegex(engine.PolymathError, "isn't finite"):
            store.append({"type": "note", "text": "fine"}, {"type": "note", "value": float("nan")})
        self.assertEqual(store.events_path.read_text(), before)              # not even the first one
        store.append({"type": "note", "text": "bad \ud800 text"})            # a lone surrogate, as JSON can carry
        self.assertEqual(store.events()[-1]["text"], "bad ? text")
        out = engine.end_session(store, track="demo", summary="odd \udcff summary", session=SESSION, minutes=5)
        self.assertEqual((out["journal_error"], store.events()[-1]["summary"]), (None, "odd ? summary"))
        self.assertIn("odd ? summary", Path(out["journal"]).read_text())
        if os.name != "nt":                                                  # an argument that isn't UTF-8
            self.run_cli("record", "demo.basics.a", "correct", "--question", b"caf\xff au lait")
            self.assertEqual(json.loads(store.events_path.read_text().splitlines()[-1])["question"], "caf? au lait")

    def test_a_batch_answer_with_the_wrong_kind_of_field_is_an_error(self):
        self.setup_demo()
        before = (self.home / "events.jsonl").read_text()
        base = {"concept": "demo.basics.a", "result": "correct"}
        for item, message in (({"result": "correct"}, "concept must be a concept id, such as"),
                              (dict(base, concept=5), "concept must be a concept id"),
                              (dict(base, qtype=["mcq"]), "qtype must be text (got '[\"mcq\"]')"),
                              (dict(base, rating=["good"]), "rating must be text"),
                              (dict(base, confused_with=["demo.basics.b"]), "confused_with must be text"),
                              (dict(base, confused_with="demo.nope"), "Unknown concept 'demo.nope'"),
                              (dict(base, track=["demo"]), "track must be text"),
                              (dict(base, ref={"id": 1}), "ref must be text"),
                              (dict(base, targets=3), "targets must be text"),
                              (dict(base, error=5), "error must be text"),
                              (dict(base, session=True), "session must be text")):
            with self.subTest(item=item):
                proc = self.run_cli("record-batch", "-", stdin=json.dumps([item]), ok=False)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(f"error: {message}", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual((self.home / "events.jsonl").read_text(), before)    # nothing was written
        batch = '[{"concept": "demo.basics.a", "result": "incorrect", "misconception": 42, "note": NaN}]'
        self.assertEqual(self.json_cli("record-batch", "-", stdin=batch)["recorded"], 1)  # free text: kept as text
        written = json.loads((self.home / "events.jsonl").read_text().splitlines()[-1])
        self.assertEqual((written["misconception"], written["note"]), ("42", "nan"))

    def test_an_answer_aimed_at_a_mistake_that_is_gone_says_so(self):
        self.setup_demo()
        self.assertEqual(self.json_cli("record", "demo.basics.a", "incorrect", "--misconception", "async")["mistake"], "M1")
        self.run_cli("undo")                                                 # the miss, and M1 with it, taken back
        out = self.run_cli("record", "demo.basics.a", "correct", "--targets", "M1").stdout
        self.assertIn("M1 is gone (the answers that showed it were undone or voided), so this answer counts "
                      "toward no mistake", out)
        r = self.json_cli("record", "demo.basics.a", "partial", "--targets", "M1")
        self.assertEqual((r["targets"], r["target_gone"], r["mistake"]), ("M1", True, None))
        out = self.run_cli("record", "demo.basics.a", "incorrect", "--targets", "M1").stdout
        self.assertIn("M1 is gone (the answers that showed it were undone or voided): this miss opens it again", out)
        self.assertEqual([(m["id"], m["status"]) for m in self.json_cli("mistakes")], [("M1", "open")])
        r = self.json_cli("record", "demo.basics.a", "correct", "--targets", "M1")   # a live target again
        self.assertEqual((r["target_gone"], r["fixing"]), (False, ["M1"]))
        self.assertNotIn("is gone", self.run_cli("record", "demo.basics.a", "correct").stdout)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_any_command_closes_a_folder_an_older_version_left_open(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "correct")
        for p in [self.home, *self.home.rglob("*")]:                         # as an older version left it
            os.chmod(p, 0o755 if p.is_dir() else 0o644)
        self.run_cli("brief")                                                # what opens every session
        self.assertEqual(mode(self.home), 0o700)
        self.assertEqual(mode(self.home / "events.jsonl"), 0o644)            # unreachable now, so left alone
        os.chmod(self.home, 0o750)
        self.run_cli("where")
        self.assertEqual(mode(self.home), 0o700)
        os.chmod(self.home, 0o555)
        try:
            self.run_cli("where")
            self.assertEqual(mode(self.home), 0o500)                         # your own access is kept as it was
        finally:
            os.chmod(self.home, 0o700)
        self.in_process()
        os.chmod(self.home, 0o755)
        with mock.patch.object(engine.os, "getuid", return_value=os.getuid() + 1):
            engine.keep_private(self.home)                                   # a folder that isn't yours
        self.assertEqual(mode(self.home), 0o755)                             # is left as it is
        engine.keep_private(Path(self.tmp.name) / "nowhere")
        self.assertFalse((Path(self.tmp.name) / "nowhere").exists())         # and none is made
        self.home = Path(self.tmp.name) / "documents"                        # POLYMATH_HOME set to a folder that
        self.home.mkdir()                                                    # exists but holds no record yet
        os.chmod(self.home, 0o755)
        self.run_cli("where")
        self.run_cli("brief")
        self.assertEqual(mode(self.home), 0o755)                             # is left to `init`,
        self.run_cli("init", "--name", "Ada")
        self.assertEqual(mode(self.home), 0o700)                             # which closes it

    def test_brief_lists_open_mistakes(self):
        self.setup_demo()
        self.run_cli("record", "demo.basics.a", "incorrect", "--misconception", "mixes up A and B")
        brief = self.run_cli("brief").stdout
        for text in ("M1", "mixes up A and B", "placement", "open mistakes 1"):
            self.assertIn(text, brief)

    def test_notes_round_trip_and_reach_the_brief(self):
        self.setup_demo()
        self.run_cli("note", "Prefers code-first explanations", "--section", "How I learn best")
        self.run_cli("note", "Covered A; next B", "--track", "demo")
        lines = self.run_cli("notes").stdout.splitlines()
        heading = lines.index("## How I learn best")
        bullet = next(i for i, line in enumerate(lines) if "code-first" in line)
        following = next(i for i in range(heading + 1, len(lines)) if lines[i].startswith("## "))
        self.assertTrue(heading < bullet < following)  # filed under the right heading
        self.assertIn("Covered A; next B", self.run_cli("notes", "--track", "demo").stdout)
        self.assertIn("Prefers code-first explanations", self.run_cli("brief").stdout)

    # --- packaging ----------------------------------------------------------
    def test_docs_are_kept_on_concepts(self):
        data = syllabus()
        data["domains"][0]["concepts"][0]["docs"] = ["https://example.com/a"]
        self.setup_demo(data)
        self.assertEqual(self.view("demo.basics.a")["docs"], ["https://example.com/a"])
        bad = syllabus(slug="demo-bad")
        bad["domains"][0]["concepts"][0]["docs"] = "not a list"
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps(bad))
        self.assertIn("docs must be", self.run_cli("track", "import", str(path), ok=False).stderr)

    def test_guide_and_blueprints(self):
        self.run_cli("init")
        self.assertIn("personal tutor", self.run_cli("guide", "persona").stdout)
        self.assertEqual(self.run_cli("guide", "nope", ok=False).returncode, 2)
        names = [b["name"] for b in self.json_cli("blueprints")]
        self.assertIn("salesforce-platform-developer-1", names)
        out = self.json_cli("blueprints", "salesforce-platform-developer-1", "--to-track", "pd1")
        self.assertEqual(json.loads(Path(out["path"]).read_text())["slug"], "pd1")
        again = self.run_cli("blueprints", "salesforce-platform-developer-1", "--to-track", "pd1", ok=False)
        self.assertIn("already exists", again.stderr)
        self.assertEqual(self.json_cli("track", "import", out["path"])["track"], "pd1")

    def test_every_blueprint_imports_alone_and_together(self):
        paths = sorted((ROOT / "blueprints").glob("*.json"))
        for path in paths:
            with self.subTest(blueprint=path.name):
                self.home = Path(self.tmp.name) / path.stem
                self.run_cli("init")
                self.assertEqual(self.json_cli("track", "import", str(path))["warnings"], [])
        self.home = Path(self.tmp.name) / "all-together"
        self.run_cli("init")
        for path in paths:
            with self.subTest(together=path.name):
                self.assertEqual(self.json_cli("track", "import", str(path))["warnings"], [])

    def test_skill_files_load_real_guides_and_name_files_fully(self):
        guides = {p.stem for p in (ROOT / "references").glob("*.md")}
        for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
            text = skill.read_text()
            with self.subTest(skill=skill.parent.name):
                self.assertTrue(text.startswith("---\nname: "))
                self.assertIn("!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/polymath.py brief`", text)
                for names in re.findall(r"polymath\.py guide ([a-z ]+)`", text):
                    self.assertLessEqual(set(names.split()), guides)
                self.assertNotIn("cat ${CLAUDE_PLUGIN_ROOT}", text)  # Claude Code blocks cat outside the project
                # file names keep their .md (a bare `notes` or `~/.polymath/learner` was the old breakage)
                self.assertNotRegex(text, r"`(?:[^`\s]*/)?(learner|notes|plan)`")

if __name__ == "__main__":
    unittest.main()
