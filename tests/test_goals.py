"""Tests for goals, inline lessons and active time, run against the built-in writer. None calls a model."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import polymath as engine  # noqa: E402
import polymath_goals as goals_mod  # noqa: E402
import polymath_lessons as lessons_mod  # noqa: E402
import polymath_study as study_mod  # noqa: E402
import polymath_time as time_mod  # noqa: E402
import polymath_web as web  # noqa: E402
import polymath_writer as writer  # noqa: E402

NOW = "2026-11-22T18:00:00Z"
TODAY = date(2026, 11, 22)
PRIVATE = "Principal architect at Example Corp"


def until(check, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting")


def syllabus():
    return {"slug": "demo", "title": "Demo", "short": "DEMO", "kind": "certification",
            "exam": {"name": "Demo Exam", "passing_score": 0.7, "questions": 6, "minutes": 12},
            "domains": [{"id": "basics", "name": "Basics", "weight": 1.0, "concepts": [
                {"id": "demo.basics.a", "title": "Concept A", "prereqs": [], "tags": ["apex"], "docs": ["https://example.com/a"]},
                {"id": "demo.basics.b", "title": "Concept B", "prereqs": ["demo.basics.a"]}]}]}


def ms(mid, kind="exam", hours=10, deps=(), wait=0):
    return {"id": mid, "title": mid.upper(), "kind": kind, "hours": hours, "depends_on": list(deps), "wait_days": wait}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.env = mock.patch.dict(os.environ, {"POLYMATH_NOW": NOW, "TZ": "UTC"})
        self.env.start()
        if hasattr(time, "tzset"):
            time.tzset()
        self.store = engine.Store(self.root)
        with redirect_stdout(io.StringIO()):
            engine.cmd_init(Namespace(name="Ada Lovelace", persona=None, json=True), self.store)
        engine.add_note(self.store.profile_md, PRIVATE, section="Background and existing knowledge")
        engine.import_track(self.store, syllabus(), target_date="2026-12-08")
        self.parts = []

    def tearDown(self):
        for part in reversed(self.parts):
            part.stop()
        self.env.stop()
        if hasattr(time, "tzset"):
            time.tzset()
        self.tmp.cleanup()

    def study(self, pen=None):
        st = study_mod.Study(self.store, pen or writer.FakeWriter())
        self.parts.append(st)
        return st

    def goals(self, pen=None):
        st = self.study(pen)
        g = goals_mod.Goals(self.store, st)
        self.parts.append(g)
        return g

    @staticmethod
    def settled(g, gid):
        return until(lambda: (lambda v: None if v.get("busy") else v)(g.view(gid)))

    def to_plan(self, g):
        gid = g.create("I want the final credential")["id"]
        v = self.settled(g, gid)
        for _ in range(4):
            if v.get("plan"):
                break
            v = g.say(gid, "None yet")
            v = self.settled(g, gid)
        self.assertTrue(v.get("plan"))
        return gid, v


class Schedule(unittest.TestCase):
    def test_one_at_a_time_in_dependency_order(self):
        out = goals_mod.schedule([ms("b", deps=["a"]), ms("a")], 60, TODAY)
        self.assertEqual(out["days"]["a"], {"start": "2026-11-22", "end": "2026-12-01"})
        self.assertEqual(out["days"]["b"], {"start": "2026-12-02", "end": "2026-12-11"})
        self.assertEqual((out["finish"], out["hours_left"]), ("2026-12-11", 20))

    def test_more_minutes_finish_sooner(self):
        slow = goals_mod.schedule([ms("a", hours=20)], 30, TODAY)["finish"]
        fast = goals_mod.schedule([ms("a", hours=20)], 120, TODAY)["finish"]
        self.assertLess(fast, slow)

    def test_waiting_delays_what_depends_on_it(self):
        out = goals_mod.schedule([ms("a", hours=1, wait=30), ms("b", hours=1, deps=["a"])], 60, TODAY)
        self.assertEqual(out["days"]["b"]["start"], "2026-12-23")

    def test_done_steps_take_no_time(self):
        out = goals_mod.schedule([ms("a"), ms("b", deps=["a"])], 60, TODAY, done={"a"})
        self.assertEqual((out["days"]["a"]["start"], out["days"]["b"]["start"], out["hours_left"]), (None, "2026-11-22", 10))

    def test_a_credential_is_awarded_when_its_parts_are_done(self):
        out = goals_mod.schedule([ms("a"), ms("c", "credential", 0, ["a"])], 60, TODAY)
        self.assertEqual(out["days"]["c"]["start"], "2026-12-02")

    def test_a_booked_exam_keeps_its_date(self):
        out = goals_mod.schedule([ms("a", hours=1), ms("b", hours=1, deps=["a"])], 60, TODAY, fixed={"a": date(2026, 12, 8)})
        self.assertEqual((out["days"]["a"]["end"], out["days"]["b"]["start"]), ("2026-12-08", "2026-12-09"))

    def test_says_when_a_deadline_does_not_fit_and_what_would(self):
        out = goals_mod.schedule([ms("a", hours=30)], 60, TODAY, date(2026, 12, 1))
        self.assertIs(out["fits"], False)
        self.assertEqual(out["minutes_needed"], 180)     # 30 hours over the 10 days left
        self.assertIs(goals_mod.schedule([ms("a", hours=1)], 60, TODAY, date(2026, 12, 1))["fits"], True)


class Cleaning(unittest.TestCase):
    def plan(self, milestones):
        return {"summary": "s", "first_step": "f", "risks": [], "gaps": [], "milestones": milestones}

    def full(self, mid, **kw):
        return {"id": mid, "title": mid.upper(), "kind": "exam", "depends_on": [], "hours": 5, "wait_days": 0, "why": "",
                "held": False, "existing_track": "", "blueprint": "", **kw}

    def test_a_dependency_cycle_is_refused(self):
        _, problems = goals_mod.clean_plan(self.plan([self.full("a", depends_on=["b"]), self.full("b", depends_on=["a"])]), set(), {})
        self.assertTrue(any("cycle" in p for p in problems))

    def test_an_unknown_dependency_is_refused(self):
        _, problems = goals_mod.clean_plan(self.plan([self.full("a", depends_on=["ghost"])]), set(), {})
        self.assertTrue(any("ghost" in p for p in problems))

    def test_a_made_up_track_or_blueprint_is_dropped(self):
        plan, problems = goals_mod.clean_plan(self.plan([self.full("a", existing_track="nope", blueprint="nope")]), {"demo"}, {})
        self.assertEqual((problems, plan["milestones"][0]["existing_track"], plan["milestones"][0]["blueprint"]), ([], "", ""))

    def test_a_blueprint_already_being_studied_links_to_its_track(self):
        plan, _ = goals_mod.clean_plan(self.plan([self.full("a", blueprint="pd1")]), {"salesforce-pd1"}, {"pd1": "salesforce-pd1"})
        self.assertEqual(plan["milestones"][0]["track"], "salesforce-pd1")

    def test_research_keeps_only_real_links(self):
        out = goals_mod.clean_research({"title": "T", "summary": "S", "uncertain": [], "short": "", "requirements": [
            {"id": "A", "title": "A", "kind": "exam", "required": True, "depends_on": ["ghost"], "details": "", "hours": 5, "wait_days": 0,
             "exam_facts": "", "sources": ["javascript:alert(1)", "https://example.com/a"]}],
            "sources": [{"title": "x", "url": "javascript:alert(1)"}, {"title": "y", "url": "https://example.com/y"}]})
        self.assertEqual(out["requirements"][0]["sources"], ["https://example.com/a"])
        self.assertEqual(out["requirements"][0]["depends_on"], [])
        self.assertEqual([s["url"] for s in out["sources"]], ["https://example.com/y"])

    def test_research_that_found_nothing_is_an_error(self):
        with self.assertRaises(writer.WriterError):
            goals_mod.clean_research({"requirements": []})

    def test_a_syllabus_is_made_importable(self):
        data = {"title": "X", "short": "", "goal": "", "sources": [], "exam": {"name": "X", "questions": 60, "minutes": 90, "passing_score": 68},
                "domains": [{"id": "one", "name": "One", "weight": 30, "concepts": [
                    {"id": "x.y.a", "title": "A", "tags": [], "prereqs": ["x.y.b", "x.y.ghost", "x.y.a"], "objective": "o", "docs": ["ftp://bad"]},
                    {"id": "NOT VALID", "title": "bad", "tags": [], "prereqs": [], "objective": "", "docs": []}]},
                    {"id": "two", "name": "Two", "weight": 70, "concepts": [{"id": "x.y.b", "title": "B", "tags": [], "prereqs": [], "objective": "o", "docs": []}]}]}
        out = goals_mod.clean_syllabus(data, {"title": "X", "kind": "exam"}, {})
        self.assertEqual(engine.validate_syllabus(json.loads(json.dumps(out)), {}, "x-track")[0], [])
        self.assertAlmostEqual(sum(d["weight"] for d in out["domains"]), 1.0)
        self.assertEqual(out["domains"][0]["concepts"][0]["prereqs"], ["x.y.b"])
        self.assertEqual((out["exam"]["passing_score"], out["exam"]["verified"]), (0.68, False))


class Lockdown(unittest.TestCase):
    INIT = {"apiKeySource": "none", "model": "claude-opus-5"}

    def test_a_normal_call_may_have_no_tools(self):
        self.assertIsNotNone(writer._init_problem({**self.INIT, "tools": ["WebSearch"]}))

    def test_a_research_call_may_only_read_the_web(self):
        self.assertIsNone(writer._init_problem({**self.INIT, "tools": ["WebSearch", "WebFetch", "StructuredOutput"]}, web=True))
        for tool in ("Bash", "Read", "Write", "Edit", "Task"):
            self.assertIsNotNone(writer._init_problem({**self.INIT, "tools": ["WebSearch", tool]}, web=True), tool)

    def test_the_command_asks_for_web_tools_only_on_a_research_call(self):
        seen = []

        class Stop(Exception):
            pass

        def fake_popen(args, **kw):
            seen.append(args)
            raise Stop()
        cli = writer.ClaudeCLI(binary="/bin/false")
        with mock.patch("subprocess.Popen", fake_popen):
            for web_on in (False, True):
                with self.assertRaises(Stop):
                    cli.run("system", "prompt", None, web=web_on)
        self.assertEqual(seen[0][seen[0].index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", seen[0])
        self.assertEqual(seen[1][seen[1].index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(seen[1][seen[1].index("--allowedTools") + 1], "WebSearch,WebFetch")


class Conversation(Base):
    def test_from_a_sentence_to_a_plan(self):
        g = self.goals()
        gid, v = self.to_plan(g)
        self.assertEqual(v["stage"], "plan")
        self.assertEqual(len(v["plan"]["milestones"]), 4)
        self.assertTrue(v["research"]["sources"])
        self.assertTrue(v["schedule"]["finish"])
        self.assertEqual(v["status"], "draft")          # nothing starts until they accept it

    def test_it_asks_before_it_plans(self):
        g = self.goals()
        gid = g.create("A goal")["id"]
        v = self.settled(g, gid)
        self.assertIsNotNone(v["research"])
        self.assertIsNone(v["plan"])
        self.assertTrue(v["messages"][-1].get("options"))
        self.assertTrue(v["messages"][-1]["multi"])

    def test_the_web_calls_never_see_the_learner(self):
        pen = writer.FakeWriter()
        g = self.goals(pen)
        self.to_plan(g)
        web_calls = [c for c in pen.calls if c["web"]]
        self.assertTrue(web_calls)
        for call in web_calls:
            for private in ("Lovelace", "Ada", PRIVATE, "Example Corp"):
                self.assertNotIn(private, call["prompt"])

    def test_the_private_calls_have_no_web(self):
        pen = writer.FakeWriter()
        g = self.goals(pen)
        self.to_plan(g)
        self.assertTrue(any(PRIVATE in c["prompt"] for c in pen.calls))
        self.assertFalse(any(c["web"] and PRIVATE in c["prompt"] for c in pen.calls))

    def test_a_name_or_contact_in_the_topic_is_taken_out(self):
        out = goals_mod.neutral_subject(self.store, "Ada Lovelace CTA requirements ada@example.com +1 415 555 0100")
        self.assertEqual(out, "CTA requirements")

    def test_questions_are_capped(self):
        class Asker(writer.FakeWriter):
            def run(self, system, prompt, schema=None, **kw):
                out = super().run(system, prompt, schema, **kw)
                if schema is writer.GOAL_TURN_SCHEMA and '"research": null' not in prompt and '"plan"' not in prompt.split("conversation")[0]:
                    out.update(action="ask", reply="One more question?")
                return out
        g = self.goals(Asker())
        gid = g.create("A goal")["id"]
        v = self.settled(g, gid)
        for _ in range(goals_mod.MAX_QUESTIONS + 2):
            if v.get("plan"):
                break
            g.say(gid, "ok")
            v = self.settled(g, gid)
        self.assertTrue(v.get("plan"))
        self.assertLessEqual(v["questions"], goals_mod.MAX_QUESTIONS)

    def test_a_failure_says_so_and_can_be_retried(self):
        class Fails(writer.FakeWriter):
            fail = True

            def run(self, system, prompt, schema=None, **kw):
                if schema is writer.GOAL_RESEARCH_SCHEMA and self.fail:
                    raise writer.WriterError("Claude is busy.", "failed")
                return super().run(system, prompt, schema, **kw)
        pen = Fails()
        g = self.goals(pen)
        gid = g.create("A goal")["id"]
        v = self.settled(g, gid)
        self.assertEqual(v["error"]["text"], "Claude is busy.")
        pen.fail = False
        g.retry(gid)
        v = self.settled(g, gid)
        self.assertIsNone(v["error"])
        self.assertTrue(v["research"])

    def test_work_cut_off_by_a_restart_can_be_picked_up(self):
        g = self.goals()
        gid, _ = self.to_plan(g)
        g.goals[gid]["busy"] = {"stage": "planning", "label": "x", "since": NOW}
        g.goals[gid]["plan"]["milestones"][0]["building"] = NOW
        g._save(g.goals[gid])
        again = goals_mod.Goals(self.store, g.study)
        self.parts.append(again)
        v = again.view(gid)
        self.assertIsNone(v["busy"])
        self.assertEqual(v["error"]["code"], "interrupted")
        self.assertFalse(v["plan"]["milestones"][0].get("building"))

    def test_a_replan_keeps_what_is_already_done(self):
        g = self.goals()
        gid, v = self.to_plan(g)
        g.accept(gid)
        g.milestone(gid, "advanced", "done")
        g.say(gid, "Please change the plan")
        v = self.settled(g, gid)
        self.assertEqual(next(m for m in v["plan"]["milestones"] if m["id"] == "advanced")["status"], "done")

    def test_goals_survive_a_restart(self):
        g = self.goals()
        gid, before = self.to_plan(g)
        again = goals_mod.Goals(self.store, None)
        self.assertEqual(again.view(gid)["plan"]["summary"], before["plan"]["summary"])
        self.assertEqual(len(goals_mod.summaries(self.store, engine.Model(self.store))), 1)

    def test_several_goals_are_tracked_side_by_side(self):
        g = self.goals()
        one, _ = self.to_plan(g)
        two, _ = self.to_plan(g)
        self.assertEqual({x["id"] for x in g.list()}, {one, two})
        g.archive(one)
        self.assertEqual([x["id"] for x in g.list()], [two])
        self.assertEqual(self.store.slugs(), ["demo"])    # removing a goal leaves what was learned


class Milestones(Base):
    def active(self):
        g = self.goals()
        gid, _ = self.to_plan(g)
        return g, gid, g.accept(gid)

    @staticmethod
    def step(view, mid):
        return next(m for m in view["plan"]["milestones"] if m["id"] == mid)

    def test_a_step_opens_when_its_prerequisite_is_done(self):
        g, gid, v = self.active()
        self.assertEqual(self.step(v, "advanced")["status"], "locked")
        v = g.milestone(gid, "foundation", "done")
        self.assertEqual(self.step(v, "advanced")["status"], "next")

    def test_a_credential_completes_itself(self):
        g, gid, _ = self.active()
        g.milestone(gid, "foundation", "done")
        v = g.milestone(gid, "advanced", "done")
        self.assertEqual(self.step(v, "credential")["status"], "done")
        self.assertEqual(self.step(v, "board")["status"], "next")

    def test_undo_reopens_it(self):
        g, gid, _ = self.active()
        g.milestone(gid, "foundation", "done")
        v = g.milestone(gid, "foundation", "undo")
        self.assertNotEqual(self.step(v, "foundation")["status"], "done")
        self.assertEqual(self.step(v, "advanced")["status"], "locked")

    def test_a_step_uses_the_track_you_are_already_studying(self):
        g, gid, v = self.active()
        self.assertEqual(self.step(v, "foundation")["track"], "demo")
        self.assertEqual(self.step(v, "foundation")["status"], "studying")

    def test_passing_it_marks_the_track_done_and_undo_restores_it(self):
        g, gid, _ = self.active()
        g.milestone(gid, "foundation", "done")
        self.assertEqual(self.store.track("demo")["status"], "done")
        g.milestone(gid, "foundation", "undo")
        self.assertEqual(self.store.track("demo")["status"], "active")

    def test_building_a_syllabus_creates_a_track(self):
        g, gid, _ = self.active()
        g.milestone(gid, "foundation", "done")
        g.milestone(gid, "advanced", "start")
        v = until(lambda: (lambda x: x if self.step(x, "advanced").get("track") else None)(g.view(gid)))
        slug = self.step(v, "advanced")["track"]
        self.assertIn(slug, self.store.slugs())
        self.assertEqual(self.step(v, "advanced")["status"], "studying")
        self.assertEqual(self.store.track(slug)["target_date"], self.step(g.view(gid), "advanced")["end"] or self.store.track(slug)["target_date"])

    def test_the_syllabus_call_never_sees_the_learner(self):
        pen = writer.FakeWriter()
        g = self.goals(pen)
        gid, _ = self.to_plan(g)
        g.accept(gid)
        g.milestone(gid, "foundation", "done")
        g.milestone(gid, "advanced", "start")
        until(lambda: self.step(g.view(gid), "advanced").get("track"))
        call = next(c for c in pen.calls if c["schema"] is writer.GOAL_SYLLABUS_SCHEMA)
        self.assertTrue(call["web"])
        for private in ("Lovelace", PRIVATE):
            self.assertNotIn(private, call["prompt"])

    def test_a_bundled_syllabus_is_used_when_there_is_one(self):
        g, gid, _ = self.active()
        g.milestone(gid, "foundation", "done")
        with g.lock:
            m = g._milestone(g.goals[gid], "advanced")
            m["blueprint"] = "salesforce-platform-developer-2"
        v = g.milestone(gid, "advanced", "start")
        self.assertIn(self.step(v, "advanced")["track"], self.store.slugs())

    def test_a_failed_build_says_why_and_can_be_retried(self):
        class Fails(writer.FakeWriter):
            fail = True

            def run(self, system, prompt, schema=None, **kw):
                if schema is writer.GOAL_SYLLABUS_SCHEMA and self.fail:
                    raise writer.WriterError("No guide found.", "failed")
                return super().run(system, prompt, schema, **kw)
        pen = Fails()
        g = self.goals(pen)
        gid, _ = self.to_plan(g)
        g.accept(gid)
        g.milestone(gid, "foundation", "done")
        g.milestone(gid, "advanced", "start")
        v = until(lambda: (lambda x: x if self.step(x, "advanced").get("build_error") else None)(g.view(gid)))
        self.assertIn("No guide found", self.step(v, "advanced")["build_error"])
        pen.fail = False
        g.milestone(gid, "advanced", "start")
        until(lambda: self.step(g.view(gid), "advanced").get("track"))

    def test_progress_grows_with_finished_steps(self):
        g, gid, v = self.active()
        start = v["progress"]
        v = g.milestone(gid, "foundation", "done")
        self.assertGreater(v["progress"], start)
        v = g.milestone(gid, "advanced", "done")
        v = g.milestone(gid, "board", "done")
        self.assertEqual(v["progress"], 1.0)
        self.assertIsNone(v["next"])


class ActiveClock(Base):
    def clock(self):
        return time_mod.ActiveTime(self.store)

    def test_time_adds_up_by_day_track_and_session(self):
        c = self.clock()
        c.report(30, "demo", "a" * 16)
        with mock.patch("time.monotonic", return_value=time.monotonic() + 100):
            out = c.report(20, "demo", "a" * 16)
        self.assertEqual((out["today"], out["week"], out["total"]), (50, 50, 50))
        self.assertEqual(out["tracks"]["demo"]["today"], 50)
        self.assertEqual(c.session_seconds("a" * 16), 50)

    def test_two_tabs_cannot_count_the_same_time_twice(self):
        c = self.clock()
        base = time.monotonic()
        with mock.patch("time.monotonic", return_value=base):
            c.report(15)
        with mock.patch("time.monotonic", return_value=base + 2):
            out = c.report(15)                     # a second tab, two seconds later
        self.assertEqual(out["today"], 17)

    def test_one_report_cannot_claim_more_than_the_limit(self):
        out = self.clock().report(99999)
        self.assertEqual(out["today"], time_mod.MAX_REPORT)

    def test_nonsense_is_ignored(self):
        c = self.clock()
        for bad in (None, "30", True, -5, 0, float("nan")):
            self.assertEqual(c.report(bad)["today"], 0)

    def test_a_bad_track_or_session_id_is_not_stored(self):
        c = self.clock()
        c.report(10, "../../etc", "not-a-session")
        c.flush()
        self.assertEqual(c.data["sessions"], {})
        self.assertEqual(next(iter(c.data["days"].values()))["tracks"], {})

    def test_it_survives_a_restart(self):
        c = self.clock()
        c.report(42, "demo")
        c.flush()
        self.assertEqual(self.clock().summary()["today"], 42)

    def test_yesterday_stays_out_of_today(self):
        c = self.clock()
        c.report(60)
        os.environ["POLYMATH_NOW"] = "2026-11-23T18:00:00Z"
        out = c.summary()
        self.assertEqual((out["today"], out["week"]), (0, 60))

    def test_a_session_counts_what_the_page_measured(self):
        st = self.study()
        st.clock = self.clock()
        st.clock.report(90, "demo", "b" * 16)
        s = {"id": "b" * 16, "created": NOW, "activity": ["2026-11-22T22:00:00Z"]}   # four hours later: an idle gap
        self.assertEqual(st._active_minutes(s), 1.5)

    def test_without_measurements_a_long_pause_counts_for_nothing(self):
        st = self.study()
        s = {"id": "c" * 16, "created": "2026-11-22T18:00:00Z",
             "activity": ["2026-11-22T18:02:00Z", "2026-11-22T21:00:00Z", "2026-11-22T21:01:00Z"]}
        self.assertEqual(st._active_minutes(s), 3.0)  # 2 + 1 minutes; the three hours between them don't count

    def test_a_claude_code_session_left_open_stays_short(self):
        start = engine.parse_ts("2026-11-19T08:00:00Z")
        answer = engine.parse_ts("2026-11-19T08:05:00Z")
        self.assertEqual(engine.worked_minutes([start, answer]), 5.0)
        self.assertEqual(engine.worked_minutes([start]), 0.0)

    def test_the_day_view_uses_active_time(self):
        c = self.clock()
        c.report(120, "demo")
        rows = web.activity(engine.Model(self.store), c.summary()["days"])
        self.assertEqual(rows[-1]["minutes"], 2)

    def test_time_reports_do_not_make_the_page_reload(self):
        before = web.data_version(self.store)
        c = self.clock()
        c.report(30)
        c.flush()
        self.assertEqual(web.data_version(self.store), before)


class Lessons(Base):
    def lessons(self, pen=None):
        st = self.study(pen)
        l = lessons_mod.Lessons(self.store, st)
        self.parts.append(l)
        return l

    @staticmethod
    def ready(l, key):
        return until(lambda: (lambda v: v if v["state"] != "writing" else None)(l.view(key)))

    def test_a_term_opens_a_sourced_lesson(self):
        l = self.lessons()
        out = l.open("duplicate rules", "demo", "demo.basics.a")
        v = self.ready(l, out["key"])
        self.assertEqual(v["state"], "ready")
        self.assertTrue(v["lesson"]["sections"])
        self.assertTrue(v["lesson"]["sources"])

    def test_a_lesson_is_written_once(self):
        pen = writer.FakeWriter()
        l = self.lessons(pen)
        key = l.open("Duplicate Rules", "demo", "demo.basics.a")["key"]
        self.ready(l, key)
        again = l.open("  duplicate   rules ", "demo", "demo.basics.b")
        self.assertEqual((again["key"], again["state"]), (key, "ready"))
        self.assertEqual(sum(1 for c in pen.calls if c["schema"] is writer.LESSON_SCHEMA), 1)

    def test_the_lesson_call_can_search_but_never_sees_the_learner(self):
        pen = writer.FakeWriter()
        l = self.lessons(pen)
        self.ready(l, l.open("triggers Ada Lovelace", "demo", "demo.basics.a")["key"])
        call = next(c for c in pen.calls if c["schema"] is writer.LESSON_SCHEMA)
        self.assertTrue(call["web"])
        for private in ("Lovelace", "Ada", PRIVATE):
            self.assertNotIn(private, call["prompt"])
        self.assertIn("https://example.com/a", call["prompt"])   # it starts from the concept's own docs

    def test_a_lesson_with_no_source_is_refused(self):
        class NoSource(writer.FakeWriter):
            def run(self, system, prompt, schema=None, **kw):
                out = super().run(system, prompt, schema, **kw)
                if schema is writer.LESSON_SCHEMA:
                    out["sources"] = [{"title": "bad", "url": "javascript:alert(1)"}]
                return out
        l = self.lessons(NoSource())
        v = self.ready(l, l.open("triggers", "demo")["key"])
        self.assertEqual(v["state"], "failed")
        self.assertFalse(l._path(v["key"]).exists())

    def test_a_failed_lesson_can_be_retried(self):
        class Fails(writer.FakeWriter):
            fail = True

            def run(self, system, prompt, schema=None, **kw):
                if schema is writer.LESSON_SCHEMA and self.fail:
                    raise writer.WriterError("Busy.", "failed")
                return super().run(system, prompt, schema, **kw)
        pen = Fails()
        l = self.lessons(pen)
        key = l.open("triggers", "demo")["key"]
        self.assertEqual(self.ready(l, key)["state"], "failed")
        pen.fail = False
        self.assertEqual(l.open("triggers", "demo")["state"], "failed")      # opening it again doesn't spend a call
        l.open("triggers", "demo", retry=True)
        self.assertEqual(self.ready(l, key)["state"], "ready")

    def test_nothing_to_learn_is_refused(self):
        l = self.lessons()
        for bad in ("", " ", "a", "!!!"):
            with self.assertRaises(study_mod.StudyError):
                l.open(bad, "demo")

    def test_a_key_cannot_reach_outside_the_folder(self):
        l = self.lessons()
        for bad in ("../../web-token", "a" * 23, "A" * 24):
            with self.assertRaises(study_mod.StudyError):
                l.view(bad)

    def test_terms_reach_the_page_only_after_the_answer(self):
        q = writer.prepare(writer._fake_q({"title": "Triggers"}, 1, 1), "seed", "mcq")
        self.assertTrue(q["terms"])
        self.assertNotIn("terms", writer.public(q))
        view = study_mod.Study._result_view(q, {"result": "correct", "choice": q["answer"]})
        self.assertEqual(view["terms"], q["terms"])
        pending = study_mod.Study._result_view(q, {"result": "incorrect", "choice": ["A"], "pending": True})
        self.assertNotIn("terms", pending)


if __name__ == "__main__":
    unittest.main()
