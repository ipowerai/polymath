"""Tests for study in the browser (scripts/polymath_study.py), run against the built-in writer.

Every test drives a real session through the engine and checks what reaches the event log. None
calls a model."""
import io
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from collections import Counter
from contextlib import contextmanager, redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import polymath as engine  # noqa: E402
import polymath_study as study_mod  # noqa: E402
import polymath_web as web  # noqa: E402
import polymath_writer as writer  # noqa: E402

DAY1, DAY2 = "2026-11-15T18:00:00Z", "2026-11-16T18:30:00Z"
StudyError = study_mod.StudyError


def syllabus():
    return {
        "slug": "demo", "title": "Demo", "short": "DEMO", "goal": "Pass the demo exam", "kind": "certification",
        "exam": {"name": "Demo Exam", "passing_score": 0.7, "questions": 6, "minutes": 12},
        "domains": [
            {"id": "basics", "name": "Basics", "weight": 0.5, "concepts": [
                {"id": "demo.basics.a", "title": "Concept A", "prereqs": [], "tags": ["apex"]},
                {"id": "demo.basics.b", "title": "Concept B", "prereqs": ["demo.basics.a"]},
                {"id": "demo.basics.c", "title": "Concept C", "prereqs": ["demo.basics.b"]},
            ]},
            {"id": "advanced", "name": "Advanced", "weight": 0.5, "concepts": [
                {"id": "demo.advanced.d", "title": "Concept D", "prereqs": ["demo.basics.c"]},
                {"id": "demo.advanced.e", "title": "Concept E", "prereqs": []},
                {"id": "demo.advanced.f", "title": "Concept F", "prereqs": ["demo.advanced.e"]},
            ]},
        ],
    }


def until(check, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out waiting")


class Base(unittest.TestCase):
    level = "expert"   # top-down: the plan probes D, C, F, B, E, A (deepest first, areas interleaved)
    block = 3

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.env = mock.patch.dict(os.environ, {"POLYMATH_NOW": DAY1, "TZ": "UTC"})
        self.env.start()
        if hasattr(time, "tzset"):
            time.tzset()
        self.store = engine.Store(self.root)
        with redirect_stdout(io.StringIO()):
            engine.cmd_init(Namespace(name="Ada", persona=None, json=True), self.store)
        engine.import_track(self.store, syllabus(), target_date="2026-12-20")
        engine.set_preferences(self.store, {"start_level": self.level, "block_size": self.block})
        self.studies = []

    def tearDown(self):
        for st in self.studies:
            st.stop()
        self.env.stop()
        if hasattr(time, "tzset"):
            time.tzset()
        self.tmp.cleanup()

    # --- helpers --------------------------------------------------------------------------
    def study(self, pen=None, **kw):
        st = study_mod.Study(self.store, pen or writer.FakeWriter(), **kw)
        self.studies.append(st)
        return st

    def at(self, ts):
        os.environ["POLYMATH_NOW"] = ts

    def ready(self, st, sid):
        def check():
            v = st.view(sid)
            qs = v["questions"]
            return v if qs and all(q["state"] in ("ready", "answered", "failed") for q in qs) else None
        return until(check)

    @staticmethod
    def question(st, sid, qid, twin=False):
        return st.sessions[sid]["questions"][qid]["twin" if twin else "q"]

    def right(self, st, sid, qid, twin=False):
        return self.question(st, sid, qid, twin)["answer"]

    def wrong(self, st, sid, qid, twin=False):
        """The b1 option (and b2 on a choose-two), so the miss is the writer's 'misconception' option."""
        q = self.question(st, sid, qid, twin)
        by_form = {o["form"]: o["key"] for o in q["options"]}
        return sorted(by_form[f] for f in ("b1", "b2", "b3")[:q["select"]])

    def concept(self, st, sid, qid):
        return st.sessions[sid]["questions"][qid]["item"]["concept"]

    def events(self, kind=None):
        return [e for e in self.store.events() if kind is None or e.get("type") == kind]

    def attempt(self, sid, qid, suffix=""):
        rows = [e for e in self.events("attempt") if e.get("ref") == f"web:{sid}:{qid}{suffix}"]
        return rows[0] if rows else None

    def start_block(self, st, mode="learn", **kw):
        v = st.start(mode, "demo", **kw)
        return v["id"], self.ready(st, v["id"])

    def answer_rest(self, st, sid, v, skip=(), confidence=3):
        return [{"qid": q["id"], "choice": self.right(st, sid, q["id"]), "confidence": confidence}
                for q in v["questions"] if q["id"] not in skip]

    def offered(self, st, sid, n=0, states=("offered",)):
        return until(lambda: (lambda v: v if (v["questions"][n].get("followup") or {}).get("state") in states
                              else None)(st.view(sid)))


class Learn(Base):
    def test_a_session_asks_first_and_keeps_the_key_from_the_page(self):
        st = self.study()
        sid, v = self.start_block(st)
        self.assertEqual(v["mode"], "learn")
        self.assertEqual([q["kind"] for q in v["questions"]], ["probe"] * 3)
        self.assertEqual([self.concept(st, sid, q["id"]) for q in v["questions"]],
                         ["demo.advanced.d", "demo.basics.c", "demo.advanced.f"])
        self.assertEqual({q["difficulty"] for q in v["questions"]}, {4})  # an expert starts high
        text = json.dumps(v)
        for secret in ('"correct"', '"answer"', '"explanation"', '"belief"', '"why"', '"form"'):
            self.assertNotIn(secret, text)
        self.assertNotIn("concept", v["questions"][0])  # the topic is named after the answer
        self.assertTrue(v["next"]["exists"])           # the next block is written ahead
        self.assertEqual(st.status()["usage"]["calls"], 6)

    def test_answers_are_recorded_once_with_the_session_and_the_option_count(self):
        st = self.study()
        sid, v = self.start_block(st)
        answers = [dict(a, seconds=40) for a in self.answer_rest(st, sid, v, confidence=4)]
        v = st.answer(sid, answers, block=1)
        self.assertTrue(all(q["result"]["result"] == "correct" for q in v["questions"]))
        attempts = self.events("attempt")
        self.assertEqual(len(attempts), 3)
        for e in attempts:
            self.assertEqual((e["session"], e["source"], e["seconds"]), (sid, "web", 40))
            self.assertTrue(e["ref"].startswith(f"web:{sid}:q"))
            if e["qtype"] == "multi":
                self.assertNotIn("guess", e)            # choose-two keeps its own guess rate
            else:
                self.assertEqual(e["guess"], 0.25)       # 1 in 4, whatever the format
        self.assertEqual(len(self.events("session_start")), 1)
        st.answer(sid, answers, block=1)                 # a double submit
        st.answer(sid, answers, block=9)                 # an answer for a block that isn't current
        self.assertEqual(len(self.events("attempt")), 3)
        again = engine.build_attempt({"concept": attempts[0]["concept"], "result": "incorrect", "qtype": "mcq",
                                      "ref": attempts[0]["ref"]}, self.store.concepts())
        self.assertTrue(engine.record_events(self.store, [again])[0]["duplicate"])  # a retried request
        self.assertEqual(len(self.events("attempt")), 3)

    def test_bad_answers_are_refused(self):
        st = self.study()
        sid, v = self.start_block(st)
        qid = v["questions"][0]["id"]
        select = self.question(st, sid, qid)["select"]
        for bad, code in (([{"qid": qid, "choice": ["Z"], "confidence": 3}], "bad_request"),
                          ([{"qid": qid, "choice": ["A", "B", "C"][:3 - select], "confidence": 3}], "bad_request"),
                          ([{"qid": qid, "choice": self.right(st, sid, qid)}], "bad_request"),
                          ([{"qid": qid, "choice": self.right(st, sid, qid), "confidence": float("nan")}], "bad_request"),
                          ([{"qid": "q99", "choice": ["A"], "confidence": 3}], "stale"),
                          ([], "bad_request")):
            with self.assertRaises(StudyError) as ctx:
                st.answer(sid, bad)
            self.assertEqual(ctx.exception.code, code, bad)
        self.assertEqual(self.events("attempt"), [])

    def test_an_unsure_miss_is_taught_then_re_asked_once_the_check_passes(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        v = st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                      + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        result = v["questions"][0]["result"]
        self.assertEqual((result["result"], result["pending"]), ("incorrect", False))
        self.assertEqual(result["teach"]["action"], "teach")   # unsure and wrong: a gap
        self.assertIn("explanation", result)
        self.assertEqual(v["questions"][0]["concept"], "demo.advanced.d")
        miss = self.attempt(sid, q1)
        self.assertEqual((miss["error"], miss["mistake"]), ("gap", "M1"))
        hit = v["questions"][1]["result"]["engine"]
        self.assertLess(hit["knowledge_before"], hit["knowledge_after"])  # the right answers raise knowledge
        self.assertIn("knowledge_after", result["engine"])
        v = self.offered(st, sid)
        self.assertIn("question", v["questions"][0]["followup"])
        self.assertNotIn("answer", json.dumps(v["questions"][0]["followup"]["question"]))
        v = st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        self.assertEqual(v["questions"][0]["followup"]["state"], "answered")
        twin = self.attempt(sid, q1, ":t")
        self.assertEqual(twin["targets"], "M1")
        self.assertEqual(engine.Model(self.store).mistakes["M1"]["status"], "fixing")
        self.assertEqual(len(st.sessions[sid]["closers"]), 1)  # re-checked again before the session ends
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})  # a double submit
        self.assertEqual(len(self.events("attempt")), 4)

    def test_a_confident_miss_asks_what_made_them_pick_it_first(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1, q2, q3 = [q["id"] for q in v["questions"]]
        v = st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 5},
                            {"qid": q2, "choice": self.wrong(st, sid, q2), "confidence": 4},
                            {"qid": q3, "choice": self.right(st, sid, q3), "confidence": 3}], block=1)
        pending = v["questions"][0]
        self.assertTrue(pending["result"]["pending"])
        for hidden in ("answer", "explanation", "options", "teach"):
            self.assertNotIn(hidden, pending["result"])   # nothing is explained before they say why
        reasons = [r["id"] for r in pending["ask_reason"]]
        self.assertEqual((reasons[0], reasons[-1]), ("belief", "skip"))
        self.assertTrue(pending["ask_reason"][0]["label"].startswith("I thought"))
        self.assertIsNone(self.attempt(sid, q1))           # not recorded yet
        self.assertIsNotNone(self.attempt(sid, q3))
        v = st.reason(sid, q1, "belief")
        e = self.attempt(sid, q1)
        self.assertEqual(e["error"], "misconception")
        self.assertTrue(e["misconception"].startswith("believes myth b1"))
        self.assertEqual(v["questions"][0]["result"]["teach"]["action"], "refute")
        with self.assertRaises(StudyError):
            st.reason(sid, q2, "other", "")                  # "something else" needs words
        st.reason(sid, q2, "misread")
        e = self.attempt(sid, q2)
        self.assertEqual(e["error"], "slip")
        self.assertNotIn("mistake", e)                       # a slip is exam technique, not a mistake
        st.reason(sid, q1, "guessed")                        # already recorded: a no-op
        self.assertEqual(len(self.events("attempt")), 3)

    def test_an_unanswered_reason_is_recorded_when_moving_on(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 5}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        until(lambda: st.view(sid)["next"]["ready"] == st.view(sid)["next"]["total"])
        st.next_block(sid, block=1)
        self.assertEqual(self.attempt(sid, q1)["error"], "misconception")  # read from the option's label

    def test_a_drifting_follow_up_is_dropped_and_the_mistake_stays_open(self):
        st = self.study(writer.FakeWriter(drift=True))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        v = self.offered(st, sid, states=("dropped",))
        self.assertNotIn("question", v["questions"][0]["followup"])
        with self.assertRaises(StudyError):
            st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        self.assertEqual(engine.Model(self.store).mistakes["M1"]["status"], "open")
        self.assertIsNone(self.attempt(sid, q1, ":t"))

    def test_a_dropped_follow_up_re_asks_the_concept_later_and_the_close_re_checks_it(self):
        st = self.study(writer.FakeWriter(drift=True))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        cid = self.concept(st, sid, q1)
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        self.offered(st, sid, states=("dropped",))
        v = self.ready(st, st.next_block(sid, block=1)["id"])       # the block written ahead
        st.answer(sid, self.answer_rest(st, sid, v), block=2)
        v = self.ready(st, st.next_block(sid, block=2)["id"])
        self.assertEqual([(q["kind"], q["mistake"]) for q in v["questions"]], [("mistake", "M1")])  # alone
        qid = v["questions"][0]["id"]
        self.assertEqual(self.concept(st, sid, qid), cid)
        st.answer(sid, [{"qid": qid, "choice": self.right(st, sid, qid), "confidence": 4}], block=3)
        self.assertEqual(engine.Model(self.store).mistakes["M1"]["status"], "fixing")
        until(lambda: st.view(sid)["closers"] == 1)                 # fixed by a block answer: re-checked at the close
        v = st.end(sid)
        self.assertEqual((v["status"], [q["kind"] for q in v["questions"]]), ("closing", ["close"]))

    def test_nothing_runs_unless_the_writer_is_on_a_subscription_login(self):
        class SignedOut(writer.FakeWriter):
            def auth_status(self):
                return {"ok": False, "code": "not_subscription", "problem": "Claude Code uses an API key."}
        st = self.study(SignedOut())
        with self.assertRaises(StudyError) as ctx:
            st.start("learn", "demo")
        self.assertEqual(ctx.exception.code, "not_subscription")
        self.assertEqual(st.pen.calls, [])

    def test_a_quiz_on_one_concept_asks_one_question_at_a_time(self):
        st = self.study()
        sid, v = self.start_block(st, "quiz", scope={"concept": "demo.basics.a"}, count=3)
        self.assertEqual((len(v["questions"]), v["next"]), (1, {"exists": False, "more": True}))
        qid = v["questions"][0]["id"]
        first = v["questions"][0]["difficulty"]
        st.answer(sid, [{"qid": qid, "choice": self.right(st, sid, qid), "confidence": 5}], block=1)
        v = self.ready(st, st.next_block(sid, block=1)["id"])
        self.assertEqual(v["questions"][0]["difficulty"], min(5, first + 1))  # pitched by the answer just given

    def test_a_key_voided_in_claude_code_reaches_the_writer(self):
        item = {"concept": "demo.basics.a", "result": "incorrect", "qtype": "mcq", "track": "demo",
                "question": "Which runs first?"}
        r = engine.record_events(self.store, [engine.build_attempt(item, self.store.concepts())])[0]
        engine.void_attempt(self.store, attempt=r["attempt"], reason="the answer key was wrong")
        payload = study_mod.item_payload(engine.Model(self.store), self.store, self.store.track("demo"),
                                         {"concept": "demo.basics.a"}, "mcq", [])
        self.assertEqual(len(payload["disputed_questions"]), 1)
        self.assertIn("Which runs first?", payload["disputed_questions"][0])

    def test_a_hard_hit_credits_what_it_builds_on(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v, confidence=5), block=1)
        credited = set(st.sessions[sid]["credited"])
        self.assertTrue({"demo.basics.b", "demo.basics.a", "demo.advanced.e"} <= credited)
        until(lambda: st.view(sid)["next"]["ready"] == st.view(sid)["next"]["total"])
        with self.assertRaises(StudyError) as ctx:     # the block written ahead was all credited
            st.next_block(sid, block=1)
        self.assertEqual(ctx.exception.code, "nothing")
        dropped = [q for q in st.sessions[sid]["questions"].values() if q["state"] == "dropped"]
        self.assertEqual(len(dropped), 3)


class Foundations(Base):
    block = 1

    def test_two_misses_in_a_row_check_the_foundation_first(self):
        st = self.study()
        sid, v = self.start_block(st)
        for n in (1, 2):  # D, then C, answered right without confidence enough to credit anything
            qid = v["questions"][0]["id"]
            st.answer(sid, [{"qid": qid, "choice": self.right(st, sid, qid), "confidence": 3}], block=n)
            until(lambda: st.view(sid)["next"]["ready"] == 1)
            v = self.ready(st, st.next_block(sid, block=n)["id"])
        qid = v["questions"][0]["id"]
        self.assertEqual(self.concept(st, sid, qid), "demo.advanced.f")
        st.answer(sid, [{"qid": qid, "choice": self.wrong(st, sid, qid), "confidence": 2}], block=3)
        self.offered(st, sid)
        v = st.followup(sid, qid, {"choice": self.wrong(st, sid, qid, twin=True), "confidence": 2})
        teach = v["questions"][0]["followup"]["result"]["teach"]
        self.assertEqual(teach["action"], "check-prerequisites")
        self.assertEqual([p["concept"] for p in teach["prereqs"]], ["demo.advanced.e"])
        self.assertEqual(teach["checked_next"], ["demo.advanced.e"])  # and the next question checks it
        v = st.next_block(sid, block=3)
        self.assertEqual(v["questions"][0]["kind"], "prereq")
        self.assertEqual(st.sessions[sid]["questions"][v["questions"][0]["id"]]["item"]["concept"], "demo.advanced.e")


class Disputes(Base):
    def miss_first(self, st):
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        return sid, q1

    def test_a_doubted_key_is_flagged_and_a_won_dispute_voids_the_answer(self):
        st = self.study(writer.FakeWriter(verdict={"question_answer": ["Z"], "dispute_wins": True}))
        sid, q1 = self.miss_first(st)
        v = until(lambda: (lambda v: v if v["questions"][0].get("key_doubt") else None)(st.view(sid)))
        self.assertEqual(v["questions"][0]["key_doubt"]["blind"], ["Z"])
        self.assertEqual(v["questions"][0]["followup"]["state"], "dropped")  # no follow-up on a doubtful key
        self.assertIn("M1", engine.Model(self.store).mistakes)
        rule = self.question(st, sid, q1)["rule"]
        v = st.dispute(sid, q1, "the docs say otherwise")
        self.assertEqual(v["questions"][0]["dispute"]["state"], "voided")
        self.assertTrue(v["questions"][0]["result"]["voided"])
        self.assertEqual(len(self.events("void")), 1)
        model = engine.Model(self.store)
        self.assertNotIn("M1", model.mistakes)          # the mistake came only from the voided answer
        self.assertEqual(model.state("demo.advanced.d").attempts, 0)
        self.assertEqual(study_mod.disputed_rules(self.store, "demo", "demo.advanced.d"), [rule])
        self.assertIn("Voided A1", (self.root / "tracks" / "demo" / "notes.md").read_text())
        with self.assertRaises(StudyError):
            st.dispute(sid, q1, "again")
        payload = study_mod.item_payload(engine.Model(self.store), self.store, self.store.track("demo"),
                                         {"concept": "demo.advanced.d"}, "mcq", [])
        self.assertEqual(payload["do_not_assert"], [rule])  # the writer is told not to assert it again

    def test_an_upheld_dispute_keeps_the_answer_and_a_forced_one_voids_it(self):
        st = self.study()
        sid, q1 = self.miss_first(st)
        v = st.dispute(sid, q1, "I think my answer was right")
        self.assertEqual(v["questions"][0]["dispute"]["state"], "upheld")
        self.assertEqual(self.events("void"), [])
        v = st.dispute(sid, q1, "still sure", force=True)
        self.assertEqual(v["questions"][0]["dispute"]["state"], "voided")
        self.assertEqual(len(self.events("void")), 1)
        self.assertEqual(study_mod.disputed_rules(self.store, "demo", "demo.advanced.d"), [])  # not a known-bad rule

    def test_a_question_and_its_follow_up_are_voided_together(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, q1 = self.miss_first(st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.wrong(st, sid, q1, twin=True), "confidence": 2})
        st.dispute(sid, q1, "the key is wrong")
        voided = {e["target"] for e in self.events("void")}
        self.assertEqual(voided, {self.attempt(sid, q1)["id"], self.attempt(sid, q1, ":t")["id"]})

    def test_a_follow_up_can_be_disputed_on_its_own(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, q1 = self.miss_first(st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.wrong(st, sid, q1, twin=True), "confidence": 2})
        v = st.dispute(sid, q1, "the follow-up's key is wrong", which="twin")
        card = v["questions"][0]
        self.assertEqual(card["followup"]["dispute"]["state"], "voided")
        self.assertEqual({e["target"] for e in self.events("void")}, {self.attempt(sid, q1, ":t")["id"]})
        self.assertEqual({(e["source"], e["session"]) for e in self.events("void")}, {("web", sid)})
        self.assertFalse(card["result"]["voided"])                  # the question itself still counts
        self.assertTrue(card["can_dispute"])
        self.assertFalse(card["followup"]["can_dispute"])
        with self.assertRaises(StudyError):
            st.dispute(sid, q1, "again", which="twin")

    def test_a_closing_re_check_can_be_disputed(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, q1 = self.miss_first(st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 1)
        cq = st.end(sid)["questions"][0]["id"]
        st.answer(sid, [{"qid": cq, "choice": self.wrong(st, sid, cq), "confidence": 2}])
        self.assertIsNotNone(st.sessions[sid]["questions"][cq].get("verify"))  # a missed re-check's key is checked too
        st.dispute(sid, cq, "the key is wrong")
        self.assertIn(self.attempt(sid, cq, ":c")["id"], {e["target"] for e in self.events("void")})

    def test_a_pending_answer_can_be_disputed_before_it_is_recorded(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 5}], block=1)
        v = st.dispute(sid, q1, "my answer is right")
        self.assertEqual(v["questions"][0]["dispute"]["state"], "voided")
        self.assertIsNone(self.attempt(sid, q1))
        self.assertNotIn("ask_reason", v["questions"][0])


class Mistakes(Base):
    def test_a_mistake_is_re_asked_alone_the_next_day_and_resolved(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        self.assertEqual(st.end(sid, skip_close=True)["status"], "done")
        self.at(DAY2)
        sid2, v = self.start_block(st)
        self.assertEqual([(q["kind"], q["mistake"]) for q in v["questions"]], [("mistake", "M1")])
        qid = v["questions"][0]["id"]
        target = st.sessions[sid2]["questions"][qid]["payload"]["mistake_to_fix"]
        self.assertEqual((target["mistake"], target["kind"]), ("M1", "gap"))
        self.assertNotIn("wrong_belief", target)          # a gap has no belief to refute
        st.answer(sid2, [{"qid": qid, "choice": self.right(st, sid2, qid), "confidence": 4}], block=1)
        self.assertEqual(self.attempt(sid2, qid)["targets"], "M1")
        self.assertEqual(engine.Model(self.store).mistakes["M1"]["status"], "resolved")

    def test_a_reused_belief_is_recognised_as_the_same_mistake(self):
        registry = self.store.concepts()
        base = {"concept": "demo.basics.a", "qtype": "mcq", "track": "demo", "difficulty": 3}
        engine.record_events(self.store, [engine.build_attempt(dict(base, result="incorrect", confidence=5,
                                          error="misconception", misconception="thinks A runs async"), registry)])
        engine.record_events(self.store, [engine.build_attempt(dict(base, result="correct", confidence=4,
                                                                    targets="M1"), registry)])
        model = engine.Model(self.store)
        self.assertEqual(model.mistakes["M1"]["status"], "fixing")
        self.assertEqual(study_mod.known_beliefs(model, "demo.basics.a")[0],
                         {"belief": "thinks A runs async", "mistake": "M1", "ref": "B1"})
        st = self.study()
        sid, v = self.start_block(st, "quiz", scope={"concept": "demo.basics.a"}, count=1)
        qid = v["questions"][0]["id"]
        option = next(o for o in self.question(st, sid, qid)["options"] if o["form"] == "b1")
        self.assertEqual((option["belief"], option["belief_ref"]), ("thinks A runs async", "B1"))
        st.answer(sid, [{"qid": qid, "choice": self.wrong(st, sid, qid), "confidence": 4}], block=1)
        v = st.reason(sid, qid, "belief")
        result = v["questions"][0]["result"]["engine"]
        self.assertEqual((result["mistake"], result["repeat"]), ("M1", True))
        self.assertEqual(engine.Model(self.store).mistakes["M1"]["status"], "open")

    def test_the_close_re_checks_a_mistake_fixed_today(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 1)
        v = st.end(sid)
        self.assertEqual(v["status"], "closing")
        self.assertEqual([q["kind"] for q in v["questions"]], ["close"])
        self.assertEqual(st.end(sid)["status"], "closing")         # a second End (a double click) doesn't skip it
        cq = v["questions"][0]["id"]
        self.assertEqual(st.sessions[sid]["questions"][cq]["payload"]["rule_to_recheck"],
                         self.question(st, sid, q1)["rule"])
        st.answer(sid, [{"qid": cq, "choice": self.right(st, sid, cq), "confidence": 4}])
        self.assertEqual(self.attempt(sid, cq, ":c")["targets"], "M1")
        v = st.end(sid)
        self.assertEqual(v["status"], "done")
        self.assertIn("M1", v["summary"]["fixing"])
        self.assertEqual(v["summary"]["answers"], 5)  # the block's three, the follow-up and the re-check
        self.assertEqual((v["summary"]["opened"], v["summary"]["again"]), (["M1"], []))
        self.assertEqual(v["summary"]["next_check"], "2026-11-16T18:00:00Z")  # a day after the miss
        moved = {m["concept"]: m for m in v["summary"]["moved"]}
        self.assertLess(moved["demo.advanced.d"]["before"], moved["demo.advanced.d"]["after"])


class Sessions(Base):
    def test_dashboard_and_claude_code_sessions_are_paired_separately(self):
        self.store.append({"type": "session_start", "track": "demo", "focus": "cli"})
        engine.record_events(self.store, [engine.build_attempt(
            {"concept": "demo.basics.a", "result": "correct", "qtype": "mcq", "track": "demo"}, self.store.concepts())])
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        cli = engine.end_session(self.store, summary="cli session")
        self.assertEqual(cli["answers"], 1)               # only the answer given in Claude Code
        v = st.end(sid, skip_close=True)
        self.assertEqual((v["status"], v["summary"]["answers"]), ("done", 3))
        rows = {r["where"]: r for r in web.sessions(engine.Model(self.store))}
        self.assertEqual((rows["claude"]["answers"], rows["dashboard"]["answers"]), (1, 3))
        self.assertEqual(rows["dashboard"]["status"], "done")
        ends = self.events("session_end")
        self.assertEqual(sorted(bool(e.get("session")) for e in ends), [False, True])
        journal = "".join(p.read_text() for p in (self.root / "journal").glob("*.md"))
        self.assertIn("in the dashboard", journal)

    def test_undo_leaves_dashboard_answers_alone(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        with self.assertRaises(engine.PolymathError):
            engine.undo_events(self.store, 1)

    def test_an_idle_session_is_closed(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        self.at("2026-11-15T19:00:00Z")   # hours later: still waiting on the screen you left
        st.sweep()
        v = st.view(sid)
        self.assertEqual(v["status"], "active")
        self.assertTrue(all(q.get("result") for q in v["questions"]))
        self.at("2026-11-23T19:00:00Z")   # over a week
        st.sweep()
        v = st.view(sid)
        self.assertEqual((v["status"], v["summary"]["reason"]), ("done", "idle"))
        self.assertEqual(len(self.events("session_end")), 1)

    def test_one_open_session_per_goal(self):
        st = self.study()
        first = st.start("learn", "demo")["id"]
        self.assertEqual(st.start("learn", "demo")["id"], first)   # carry on where you left off
        quiz = st.start("quiz", "demo", count=2)["id"]
        self.assertNotEqual(quiz, first)
        self.assertEqual(st.view(first)["status"], "done")

    def test_open_sessions_resume_after_a_restart(self):
        st = self.study(writer.FakeWriter(delay=2))
        sid = st.start("learn", "demo")["id"]
        st.stop()                                                   # while its questions are being written
        self.assertTrue(all(q["state"] == "writing" for q in st.view(sid)["questions"]))
        again = self.study()
        v = self.ready(again, sid)
        self.assertEqual(v["status"], "active")
        self.assertTrue(all(q["state"] == "ready" for q in v["questions"]))

    def started(self, st, sid, index=0):
        block = st.sessions[sid]["blocks"][index]
        until(lambda: all(st.sessions[sid]["questions"][q]["started"] for q in block))

    def test_a_question_that_takes_too_long_fails_and_can_be_retried(self):
        st = self.study(writer.FakeWriter(delay=30))
        sid = st.start("learn", "demo")["id"]
        self.started(st, sid)                                        # the three calls are running
        self.at("2026-11-15T18:20:00Z")
        v = st.view(sid)
        self.assertTrue(all((q["state"], q.get("code")) == ("failed", "timeout") for q in v["questions"]))
        ahead = [st.sessions[sid]["questions"][q] for q in st.sessions[sid]["blocks"][1]]
        self.assertEqual({q["state"] for q in ahead}, {"writing"})  # still queued: the clock starts with the call
        v = st.retry(sid, v["questions"][0]["id"])
        self.assertEqual(v["questions"][0]["state"], "writing")
        with self.assertRaises(StudyError):
            st.retry(sid, v["questions"][0]["id"])                   # only a failed question

    def test_a_question_written_late_is_still_used(self):
        st = self.study(writer.FakeWriter(delay=2))
        sid = st.start("learn", "demo")["id"]
        self.started(st, sid)
        self.at("2026-11-15T18:20:00Z")
        self.assertEqual({q["state"] for q in st.view(sid)["questions"]}, {"failed"})
        until(lambda: {q["state"] for q in st.view(sid)["questions"]} == {"ready"})  # the calls finish after the timer gave up


class Limits(Base):
    def test_the_daily_cap_stops_new_work(self):
        learner = self.store.learner()
        learner["preferences"]["web_daily_calls"] = 2
        self.store.save_learner(learner)
        st = self.study(workers=1)
        sid = st.start("learn", "demo")["id"]
        v = self.ready(st, sid)
        self.assertEqual([q["state"] for q in v["questions"]], ["ready", "ready", "failed"])
        self.assertEqual(v["questions"][2]["code"], "daily_cap")
        with self.assertRaises(StudyError) as ctx:
            st.start("quiz", "demo")
        self.assertEqual(ctx.exception.code, "daily_cap")
        usage = st.status()["usage"]
        self.assertTrue(usage["over"])
        rows = [json.loads(line) for line in (self.root / "web-usage.jsonl").read_text().splitlines()]
        self.assertEqual(Counter(r["outcome"] for r in rows)["ok"], 2)

    def test_only_one_dashboard_runs_study_for_a_folder(self):
        first, second = self.study(), self.study()
        self.assertTrue(first.owner)
        self.assertFalse(second.owner)
        with self.assertRaises(StudyError) as ctx:
            second.start("learn", "demo")
        self.assertEqual(ctx.exception.code, "not_owner")
        self.assertIn("web-sessions/", (self.root / ".gitignore").read_text())
        self.assertEqual(os.stat(self.root / "web-sessions").st_mode & 0o777, 0o700)

    def test_a_second_dashboard_changes_nothing(self):
        first = self.study()
        sid = first.start("learn", "demo")["id"]
        self.ready(first, sid)
        second = self.study()
        self.at("2026-11-23T19:00:00Z")                              # idle long enough to be closed
        self.assertEqual(second.view(sid)["status"], "active")      # but only the owner closes it
        self.assertEqual(second.status()["active"], [])
        for call in (lambda: second.answer(sid, [{"qid": "q1", "choice": ["A"], "confidence": 3}]),
                     lambda: second.end(sid), lambda: second.next_block(sid), lambda: second.chat("hi"),
                     lambda: second.dispute(sid, "q1", "x")):
            with self.assertRaises(StudyError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "not_owner")
        second.stop()
        self.assertEqual(first.view(sid)["status"], "done")

    def test_without_a_writer_nothing_starts(self):
        st = study_mod.Study(self.store, None)
        self.assertFalse(st.available)
        with self.assertRaises(StudyError) as ctx:
            st.start("learn", "demo")
        self.assertEqual(ctx.exception.code, "writer_missing")

    def test_status_reports_the_writer_and_usage(self):
        st = self.study()
        status = st.status()
        self.assertEqual((status["available"], status["auth"]["ok"], status["usage"]["calls"]), (True, True, 0))
        sid = st.start("learn", "demo")["id"]
        self.ready(st, sid)
        until(lambda: st.status()["usage"]["calls"] == 6)
        self.assertEqual([a["id"] for a in st.status()["active"]], [sid])


class Mock(Base):
    def test_a_mock_exam(self):
        st = self.study()
        v = st.start("mock", "demo", count=6)
        sid = v["id"]
        self.assertEqual((v["mock"]["total"], v["mock"]["minutes"]), (6, 12))
        v = until(lambda: (lambda v: v if v["mock"]["ready"] == 6 else None)(st.view(sid)))
        areas = Counter(st.sessions[sid]["questions"][q["id"]]["item"]["domain_id"] for q in v["questions"])
        self.assertEqual(areas, {"basics": 3, "advanced": 3})       # split by weight
        for q in v["questions"]:
            self.assertIsNone(q["area"])                             # nothing gives the topic away
            self.assertNotIn("concept", q)
        with self.assertRaises(StudyError):
            st.mock_save(sid, {})                                    # not started yet
        v = st.mock_begin(sid)
        self.assertEqual(v["mock"]["deadline"], "2026-11-15T18:12:00Z")
        with self.assertRaises(StudyError) as ctx:
            st.start("learn", "demo")
        self.assertEqual(ctx.exception.code, "mock_running")
        q1, q2, q3 = [q["id"] for q in v["questions"][:3]]
        st.mock_save(sid, {q1: {"choice": self.right(st, sid, q1), "confidence": 4},
                           q2: {"choice": self.wrong(st, sid, q2), "confidence": 5},
                           q3: {"choice": self.right(st, sid, q3)}})
        self.assertNotIn("result", json.dumps(st.view(sid)["questions"]))
        with self.assertRaises(StudyError) as ctx:
            st.mock_submit(sid)
        self.assertEqual(ctx.exception.code, "confidence")
        v = st.mock_submit(sid, {q3: {"choice": self.right(st, sid, q3), "confidence": 3}})
        d = v["mock"]["debrief"]
        self.assertEqual((d["right"], d["total"], d["unanswered"], d["confident_misses"]), (2, 6, 3, 1))
        self.assertEqual(len(self.events("attempt")), 3)              # blanks count against the score only
        self.assertEqual(self.events("mock"), [])                     # logged when the review closes
        self.assertEqual(self.attempt(sid, q2)["mistake"], "M1")      # a miss opens a mistake
        self.assertIn("result", v["questions"][0])                    # the review shows the answers
        v = st.mock_finish(sid)
        mocks = self.events("mock")
        self.assertEqual((len(mocks), round(mocks[0]["score"], 3), mocks[0]["questions"]), (1, 0.333, 6))
        self.assertEqual(v["status"], "done")
        st.mock_finish(sid)
        self.assertEqual(len(self.events("mock")), 1)                 # logged once

    def test_a_disputed_question_leaves_the_score(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        v = st.mock_begin(sid)
        qids = [q["id"] for q in v["questions"]]
        with self.assertRaises(StudyError):
            st.dispute(sid, qids[0], "too early")                     # not while the exam is running
        answers = {qid: {"choice": self.right(st, sid, qid), "confidence": 4} for qid in qids[:4]}
        answers[qids[4]] = {"choice": self.wrong(st, sid, qids[4]), "confidence": 4}
        v = st.mock_submit(sid, answers)
        self.assertEqual((v["mock"]["debrief"]["right"], v["mock"]["debrief"]["total"]), (4, 5))
        v = st.dispute(sid, qids[4], "the key is wrong")
        self.assertEqual((v["mock"]["debrief"]["right"], v["mock"]["debrief"]["total"]), (4, 4))
        st.mock_finish(sid)
        self.assertEqual((self.events("mock")[0]["score"], self.events("mock")[0]["questions"]), (1.0, 4))
        with self.assertRaises(StudyError) as ctx:                   # logged: the score can't change now
            st.dispute(sid, qids[0], "too late")
        self.assertEqual(ctx.exception.code, "closed")

    def test_a_full_length_mock_stays_full_after_a_dispute(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid = st.start("mock", "demo", count="full")["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 6)
        qids = [q["id"] for q in st.mock_begin(sid)["questions"]]
        st.mock_submit(sid, {qid: {"choice": self.right(st, sid, qid), "confidence": 4} for qid in qids})
        st.dispute(sid, qids[0], "the key is wrong")
        st.mock_finish(sid)
        event = self.events("mock")[0]
        self.assertEqual((event["questions"], event["full"], event["source"], event["session"]), (5, True, "web", sid))
        self.assertEqual(len(engine.Model(self.store).track_report(self.store.track("demo"))["full_mocks"]), 1)

    def test_a_mock_whose_writing_failed_starts_with_what_was_written(self):
        class Fails(writer.FakeWriter):
            def run(self, system, prompt, schema=None):
                if schema is writer.BATCH_SCHEMA and "demo.advanced.d" in prompt:
                    raise writer.WriterError("Claude couldn't write it.", "invalid")
                return super().run(system, prompt, schema)
        st = self.study(Fails())
        sid = st.start("mock", "demo", count=6)["id"]
        until(lambda: st.view(sid)["mock"]["writing"] == 0)
        v = st.view(sid)
        self.assertEqual((v["mock"]["ready"], v["mock"]["failed"], v["mock"]["failed_codes"]), (1, 5, ["invalid"]))
        failed = [q["id"] for q in v["questions"] if q["state"] == "failed"]
        st.retry(sid, failed[-1])                                    # written again on its own
        until(lambda: st.view(sid)["mock"]["writing"] == 0)
        self.assertEqual(st.view(sid)["mock"]["ready"], 2)
        self.assertTrue(st.mock_begin(sid)["mock"]["started"])      # fewer than five, but nothing more is coming

    def test_time_runs_out(self):
        st = self.study()
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        v = st.mock_begin(sid)
        q1 = v["questions"][0]["id"]
        st.mock_save(sid, {q1: {"choice": self.right(st, sid, q1), "confidence": 4}})
        self.at("2026-11-15T18:30:00Z")                                # 10-minute exam, 2 minutes' grace
        with self.assertRaises(StudyError) as ctx:
            st.mock_save(sid, {q1: {"choice": self.wrong(st, sid, q1), "confidence": 4}})
        self.assertEqual(ctx.exception.code, "time_up")
        v = st.view(sid)
        self.assertTrue(v["mock"]["timed_out"])
        self.assertEqual((v["mock"]["debrief"]["right"], v["mock"]["debrief"]["total"]), (1, 5))

    def test_when_time_is_up_confidence_is_no_longer_required(self):
        st = self.study()
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        q1 = st.mock_begin(sid)["questions"][0]["id"]
        self.at("2026-11-15T18:10:30Z")                                # the timer hit zero, within the grace
        v = st.mock_submit(sid, {q1: {"choice": self.right(st, sid, q1)}})
        self.assertTrue(v["mock"]["timed_out"])
        self.assertEqual(v["mock"]["debrief"]["right"], 1)              # an answer sent at zero still counts

    def test_a_mock_is_submitted_by_the_clock_even_with_the_page_closed(self):
        st = self.study()
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        st.mock_begin(sid)
        self.at("2026-11-15T18:30:00Z")
        st.sweep()
        self.assertTrue(st.sessions[sid]["mock"]["submitted"])
        self.at("2026-11-15T21:00:00Z")                                # the review was never finished
        st.sweep()
        self.assertEqual(st.view(sid)["status"], "done")
        self.assertEqual(len(self.events("mock")), 1)


class Coach(Base):
    def test_the_coach_answers_in_the_background(self):
        st = self.study(writer.FakeWriter(delay=0.3))
        view = st.chat("How am I doing?")
        self.assertTrue(view["busy"])
        with self.assertRaises(StudyError) as ctx:
            st.chat("Hello?")
        self.assertEqual(ctx.exception.code, "busy")
        view = until(lambda: (lambda v: v if not v["busy"] else None)(st.coach_view()))
        self.assertEqual([m["role"] for m in view["messages"]], ["you", "coach"])
        self.assertEqual(view["messages"][-1]["proposals"],
                         [{"kind": "study", "track": "demo", "value": "review", "reason": "Open mistakes come first."}])
        self.assertEqual(os.stat(self.root / "web-chat.json").st_mode & 0o777, 0o600)

    def test_coach_facts_and_proposals(self):
        facts = study_mod.coach_facts(self.store)
        self.assertEqual(facts["tracks"][0]["slug"], "demo")
        self.assertIn("gate", facts["tracks"][0])
        self.assertIn("Learner: Ada", facts["brief"])
        clean = study_mod.clean_proposal
        self.assertIsNone(clean({"kind": "study", "track": "demo", "value": "rm -rf /"}, self.store))
        self.assertIsNone(clean({"kind": "target_date", "track": "demo", "value": "soon"}, self.store))
        self.assertIsNone(clean({"kind": "launch"}, self.store))
        self.assertEqual(clean({"kind": "study", "track": "x", "value": "quiz:basics", "reason": "r"}, self.store)["track"],
                         "demo")
        self.assertIsNone(clean({"kind": "study", "track": "demo", "value": "quiz:nowhere"}, self.store))
        self.assertEqual(clean({"kind": "block_size", "value": "4"}, self.store)["value"], "4")
        self.assertIsNone(clean({"kind": "block_size", "value": "9"}, self.store))
        self.assertIsNone(clean({"kind": "daily_minutes", "track": "demo", "value": "45 minutes"}, self.store))
        self.assertEqual(clean({"kind": "daily_minutes", "track": "demo", "value": "45"}, self.store)["value"], "45")
        self.assertEqual(clean({"kind": "desired_retention", "value": "0.9"}, self.store)["value"], "0.9")
        self.assertIsNone(clean({"kind": "session_minutes", "value": "NaN"}, self.store))
        self.assertEqual(clean({"kind": "target_date", "track": "demo", "value": "2026-12-08"}, self.store)["value"],
                         "2026-12-08")
        self.assertIsNone(clean({"kind": "start_level", "value": "guru"}, self.store))


class Hardening(Base):
    """Each test pins a defect found in review (the comments name what used to go wrong)."""

    def next_written(self, st, sid):
        until(lambda: (lambda n: n.get("ready") == n.get("total"))(st.view(sid)["next"]))

    def test_a_failed_write_keeps_the_answers_open_for_a_resend(self):
        st = self.study()
        sid, v = self.start_block(st)
        answers = self.answer_rest(st, sid, v)
        with mock.patch.object(engine, "record_events", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                st.answer(sid, answers, block=1)
        self.assertEqual({q["state"] for q in st.view(sid)["questions"]}, {"ready"})  # was: answered, never recorded
        st.answer(sid, answers, block=1)
        self.assertEqual((len(self.events("attempt")), len(self.events("session_start"))), (3, 1))

    def test_a_bad_answer_in_the_closing_re_check_changes_nothing(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1, q2 = v["questions"][0]["id"], v["questions"][1]["id"]
        st.answer(sid, [{"qid": q, "choice": self.wrong(st, sid, q), "confidence": 2} for q in (q1, q2)]
                  + self.answer_rest(st, sid, v, skip=(q1, q2)), block=1)
        for n, q in ((0, q1), (1, q2)):
            self.offered(st, sid, n=n)
            st.followup(sid, q, {"choice": self.right(st, sid, q, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 2)
        c1, c2 = [q["id"] for q in st.end(sid)["questions"]]
        with self.assertRaises(StudyError):
            st.answer(sid, [{"qid": c1, "choice": self.right(st, sid, c1), "confidence": 4},
                            {"qid": c2, "choice": self.right(st, sid, c2)}])  # no confidence on the second
        self.assertIsNone(st.sessions[sid]["questions"][c1].get("answer"))  # was: answered, never recorded
        st.answer(sid, [{"qid": c, "choice": self.right(st, sid, c), "confidence": 4} for c in (c1, c2)])
        self.assertEqual(len([e for e in self.events("attempt") if e["ref"].endswith(":c")]), 2)

    def test_end_waits_for_a_dispute_being_checked(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        q1 = v["questions"][0]["id"]
        answer = st.sessions[sid]["questions"][q1]["answer"]
        answer["dispute"] = {"state": "checking", "at": study_mod._now(), "run": "r1"}
        st._disputing.add("r1")                                      # its check is running here
        for call in (lambda: st.end(sid, skip_close=True), lambda: st.start("quiz", "demo")):
            with self.assertRaises(StudyError) as ctx:  # was: closed and recorded with the disputed answer
                call()
            self.assertEqual(ctx.exception.code, "busy")
        st._disputing.discard("r1")                                  # cut off (a crash, a restart): not waited for
        self.assertEqual(st.end(sid, skip_close=True)["status"], "done")
        self.assertIsNone(answer["dispute"])

    def test_moving_on_drops_what_was_left_unanswered(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.right(st, sid, q1), "confidence": 3}], block=1)
        self.next_written(st, sid)
        st.next_block(sid, block=1)
        left = [st.sessions[sid]["questions"][q["id"]]["state"] for q in v["questions"]]
        self.assertEqual(left, ["answered", "dropped", "dropped"])  # a late result can't bring them back

    def test_a_failed_rewrite_keeps_the_questions_that_passed(self):
        class Half(writer.FakeWriter):
            def run(self, system, prompt, schema=None):
                if "previous answer was rejected" in prompt:
                    raise writer.WriterError("Claude is busy.", "rate_limit")
                out = super().run(system, prompt, schema)
                if schema is writer.BATCH_SCHEMA:
                    out["questions"][1]["question"]["options"] = out["questions"][1]["question"]["options"][:2]
                return out
        st = self.study(Half())
        model, track = engine.Model(self.store), self.store.track("demo")
        payloads = [study_mod.item_payload(model, self.store, track, {"concept": c, "difficulty": 3}, "mcq", [])
                    for c in ("demo.basics.a", "demo.basics.b")]
        results, error = st._write(track, payloads, "", False)
        self.assertEqual((results[0] is not None, results[1], error.code), (True, None, "rate_limit"))

    def test_a_bad_follow_up_keeps_the_question_without_a_rewrite(self):
        class BadTwin(writer.FakeWriter):
            def run(self, system, prompt, schema=None):
                out = super().run(system, prompt, schema)
                if schema is writer.ONE_SCHEMA:
                    out["twin"] = json.loads(json.dumps(out["question"]))  # the question again: not a parallel form
                return out
        st = self.study(BadTwin())
        model, track = engine.Model(self.store), self.store.track("demo")
        payload = study_mod.item_payload(model, self.store, track, {"concept": "demo.basics.a", "difficulty": 3},
                                         "mcq", [])
        before = len(st.pen.calls)
        results, error = st._write(track, [payload], "", True)
        self.assertIsNone(error)
        self.assertIsNone(results[0]["twin"])                        # a miss is re-asked later instead
        self.assertEqual(len(st.pen.calls), before + 1)              # one call: the learner isn't kept waiting

    def test_a_written_block_goes_ahead_of_one_still_being_written(self):
        def order(first, second, **extra):
            s = {"mode": "learn", "status": "active", "current": 0, "blocks": [["q1"], ["q2"]],
                 "questions": {"q1": {"state": first}, "q2": {"state": second}}, **extra}
            study_mod.Study._show_ready_first(s)
            return s["blocks"]
        self.assertEqual(order("writing", "ready"), [["q2"], ["q1"]])
        self.assertEqual(order("ready", "ready"), [["q1"], ["q2"]])       # the one on screen can be answered
        self.assertEqual(order("answered", "ready"), [["q1"], ["q2"]])    # never moved once it has been answered
        self.assertEqual(order("failed", "ready"), [["q1"], ["q2"]])      # its Try again stays on screen
        self.assertEqual(order("writing", "writing"), [["q1"], ["q2"]])   # nothing ready to bring forward
        self.assertEqual(order("writing", "ready", mode="mock"), [["q1"], ["q2"]])  # a paper keeps its order

    def test_an_answer_voided_elsewhere_leaves_the_session_totals(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        engine.void_attempt(self.store, attempt=self.attempt(sid, v["questions"][0]["id"])["id"],
                            reason="the key was wrong (concept page)", source="web")
        self.assertEqual(st.view(sid)["totals"]["answered"], 2)
        self.assertEqual(st.end(sid, skip_close=True)["summary"]["answers"], 2)

    def test_a_dispute_drops_the_re_ask_written_ahead_for_its_mistake(self):
        st = self.study(writer.FakeWriter(verdict={"question_doubt": "Option C also fits.", "dispute_wins": True}))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        self.offered(st, sid, states=("dropped",))                   # a doubtful key: a re-ask later instead
        self.next_written(st, sid)
        st.next_block(sid, block=1)
        ahead = st.sessions[sid]["blocks"][2]
        self.assertEqual([st.sessions[sid]["questions"][q]["item"]["kind"] for q in ahead], ["mistake"])
        st.dispute(sid, q1, "the key is wrong")
        self.assertEqual(st.sessions[sid]["questions"][ahead[0]]["state"], "dropped")  # was: asked as "Fix M1"
        self.assertEqual(len(st.sessions[sid]["blocks"]), 2)

    def test_requests_on_a_finished_session_share_one_copy(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        st.end(sid, skip_close=True)
        self.assertIs(st._get(sid), st._get(sid))                    # was: a fresh copy from disk per request

    def test_calls_running_at_once_hold_their_cost_under_the_cap(self):
        learner = self.store.learner()
        learner["preferences"]["web_daily_usd"] = 0.5
        self.store.save_learner(learner)
        usage = study_mod.Usage(self.store)
        usage.record("question", {"cost_usd": 0.2}, "ok")
        with usage.reserve(), usage.reserve():                       # $0.20 spent + two running at about $0.20
            with self.assertRaises(StudyError):
                with usage.reserve():
                    pass

    def test_a_full_mock_counts_as_full_without_a_known_exam_length(self):
        track = self.store.track("demo")
        track["exam"].pop("questions")
        self.store.save_track(track)
        st = self.study()
        sid = st.start("mock", "demo", count="full")["id"]
        self.assertTrue(st.sessions[sid]["mock"]["full_size"])

    def test_ending_a_running_mock_follows_the_submit_rules(self):
        st = self.study()
        with self.assertRaises(StudyError) as ctx:                   # (a quiz scope must be ids: was a crash)
            st.start("quiz", "demo", scope={"area": ["basics"]})
        self.assertEqual(ctx.exception.code, "bad_request")
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        q1 = st.mock_begin(sid)["questions"][0]["id"]
        with self.assertRaises(StudyError):                          # more picks than the question takes
            st.mock_save(sid, {q1: {"choice": ["A", "B", "C"], "confidence": 3}})
        st.mock_save(sid, {q1: {"choice": self.right(st, sid, q1)}})
        with self.assertRaises(StudyError) as ctx:
            st.end(sid)
        self.assertEqual(ctx.exception.code, "confidence")

    def test_a_quiz_keeps_to_its_scope_after_two_misses(self):
        st = self.study()
        sid, v = self.start_block(st, "quiz", scope={"concept": "demo.advanced.f"}, count=3)
        for n in (1, 2):
            qid = v["questions"][0]["id"]
            st.answer(sid, [{"qid": qid, "choice": self.wrong(st, sid, qid), "confidence": 2}], block=n)
            v = self.ready(st, st.next_block(sid, block=n)["id"])
        self.assertEqual([self.concept(st, sid, q["id"]) for q in v["questions"]], ["demo.advanced.f"])
        self.assertNotIn("prereq", [it["kind"] for it in st.sessions[sid]["window"]])

    def test_a_confident_miss_left_without_a_reason_is_asked_again_later(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        cid = self.concept(st, sid, q1)
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 5}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        self.next_written(st, sid)
        st.next_block(sid, block=1)                                  # without saying why it was picked
        ahead = st.sessions[sid]["blocks"][2]
        self.assertEqual([(st.sessions[sid]["questions"][q]["item"]["kind"], self.concept(st, sid, q))
                          for q in ahead], [("mistake", cid)])

    def test_a_mock_submitted_late_by_the_clock_is_dated_when_time_ran_out(self):
        st = self.study()
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        q1 = st.mock_begin(sid)["questions"][0]["id"]
        st.mock_save(sid, {q1: {"choice": self.right(st, sid, q1), "confidence": 4}})
        self.at("2026-11-16T03:00:00Z")                              # the computer slept through the deadline
        st.sweep()
        deadline = engine.parse_ts(st.sessions[sid]["mock"]["deadline"])
        self.assertEqual(engine.parse_ts(self.attempt(sid, q1)["ts"]),
                         deadline + timedelta(seconds=study_mod.MOCK_GRACE))  # was: 03:00, the wake-up
        self.assertEqual(st.view(sid)["status"], "active")          # and the review is still open

    def test_a_restart_much_later_writes_unfinished_questions_again(self):
        st = self.study(writer.FakeWriter(delay=2))
        sid = st.start("learn", "demo")["id"]
        st.stop()
        self.at("2026-11-15T18:20:00Z")                              # past the writing timeout, not idle yet
        again = self.study()
        until(lambda: {q["state"] for q in again.view(sid)["questions"]} == {"ready"})  # was: failed

    def test_a_mock_writes_each_concepts_questions_in_one_call(self):
        st = self.study()
        sid = st.start("mock", "demo", count=12)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 12)
        prompts = [c["prompt"] for c in st.pen.calls if c["schema"] is writer.BATCH_SCHEMA]
        for cid in {q["item"]["concept"] for q in st.sessions[sid]["questions"].values()}:
            pattern = re.compile(r'"concept":\s*"' + re.escape(cid) + '"')
            self.assertEqual(sum(1 for p in prompts if pattern.search(p)), 1, cid)

    def test_a_close_that_fails_part_way_can_be_retried(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        real, failed = engine.end_session, []

        def flaky(*a, **kw):
            out = real(*a, **kw)
            if not failed:
                failed.append(True)
                raise OSError("disk full")
            return out
        with mock.patch.object(engine, "end_session", flaky):
            with self.assertRaises(OSError):
                st.end(sid, skip_close=True)
            self.assertEqual(st.view(sid)["status"], "active")
            self.assertEqual(st.status()["active"][0]["id"], sid)  # the page keeps working
            self.assertEqual(st.end(sid, skip_close=True)["status"], "done")
        self.assertEqual(len([e for e in self.events("session_end") if e.get("session") == sid]), 1)


class Primer(Base):
    level = "beginner"

    def test_the_closing_re_check_has_no_primer(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        self.assertTrue(st.sessions[sid]["questions"][q1]["payload"]["primer"])
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(q1,)), block=1)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 1)
        closer = st.sessions[sid]["questions"][st.sessions[sid]["closers"][0]]
        self.assertFalse(closer["payload"]["primer"])


class Pure(unittest.TestCase):
    def q(self, stem, error="trap", other=""):
        return {"stem": stem, "options": [
            {"key": "A", "correct": True, "error": "none", "belief": "", "confused_with": ""},
            {"key": "B", "correct": False, "error": error, "belief": "thinks X", "confused_with": other}]}

    def test_classifying_a_miss(self):
        c = study_mod.classify
        self.assertEqual(c(self.q("Which is true?"), ["B"], 4)[0], "misconception")  # a trap label alone isn't enough
        self.assertEqual(c(self.q("Which is NOT true?"), ["B"], 4)[0], "trap")
        self.assertEqual(c(self.q("Which is NOT true?"), ["B"], 2)[0], "gap")         # a guess is a gap
        self.assertEqual(c(self.q("x", "confusion", "demo.basics.a"), ["B"], 3),
                         ("confusion", "thinks X", "demo.basics.a"))
        self.assertEqual(c(self.q("x", "detail"), ["B"], 4), ("detail", "thinks X", None))
        self.assertEqual(c(self.q("x"), ["B"], 5, "misread")[0], "slip")
        self.assertEqual(c(self.q("x"), ["B"], 5, "guessed")[0], "gap")
        self.assertEqual(c(self.q("x"), ["B"], 5, "other", "I thought it ran later"),
                         ("misconception", "I thought it ran later", None))
        self.assertEqual(c(self.q("x"), ["B"], 5, "belief")[0], "misconception")       # never a trap by their word
        self.assertEqual(c(self.q("x", "confusion", "demo.basics.a"), ["B"], 5, "belief"),
                         ("confusion", "thinks X", "demo.basics.a"))                # the belief was a mix-up

    def test_a_mock_paper(self):
        domains = [{"id": "a", "weight": 0.23, "concepts": ["x"]}, {"id": "b", "weight": 0.30, "concepts": ["y"]},
                   {"id": "c", "weight": 0.25, "concepts": ["z"]}, {"id": "d", "weight": 0.22, "concepts": ["w"]}]
        counts = study_mod.allocate(60, domains)
        self.assertEqual(sum(counts.values()), 60)
        self.assertEqual(counts, {"a": 14, "b": 18, "c": 15, "d": 13})
        self.assertEqual(study_mod.allocate(3, domains + [{"id": "e", "weight": 0.01, "concepts": ["v"]}])["e"], 1)
        three = [{"id": "a", "weight": 0.46, "concepts": ["x"]}, {"id": "b", "weight": 0.18, "concepts": ["y"]},
                 {"id": "c", "weight": 0.36, "concepts": ["z"]}]
        self.assertEqual(study_mod.allocate(5, three), {"a": 2, "b": 1, "c": 2})  # was: b (share 0.9) got two
        lopsided = [{"id": "a", "weight": 0.9, "concepts": ["x"]}] + [
            {"id": k, "weight": 0.025, "concepts": [k]} for k in "bcde"]
        self.assertEqual(sum(study_mod.allocate(5, lopsided).values()), 5)  # was: a paper of 8
        levels = study_mod.mock_difficulties(20, "s")
        self.assertEqual(Counter(levels), {2: 2, 3: 8, 4: 8, 5: 2})
        self.assertEqual(levels, study_mod.mock_difficulties(20, "s"))  # repeatable

    def test_numbers_from_the_page(self):
        for raw, want in ((3, 3), ("4", 4), (2.9, 2), (True, None), (float("nan"), None), (float("inf"), None),
                          ("x", None), (None, None)):
            self.assertEqual(study_mod._int(raw, None), want, raw)

    def test_beliefs_read_in_the_first_person(self):
        self.assertEqual(study_mod.first_person("thinks a batch runs at once"), "I thought a batch runs at once")
        self.assertEqual(study_mod.first_person("confuses X with Y"), "I thought: confuses X with Y")


class Scheduling(unittest.TestCase):
    def test_a_full_queue_refuses_work_and_priorities_hold(self):
        sched, gate, order = study_mod.Scheduler(workers=1, limit=3), threading.Event(), []
        try:
            self.assertTrue(sched.submit(0, gate.wait))
            until(lambda: sched.load()["running"] == 1)
            self.assertTrue(sched.submit(3, order.append, "later"))
            self.assertTrue(sched.submit(0, order.append, "now"))
            self.assertTrue(sched.submit(2, order.append, "next"))
            self.assertFalse(sched.submit(0, order.append, "overflow"))  # never blocks the caller
            gate.set()
            until(lambda: len(order) == 3)
            self.assertEqual(order, ["now", "next", "later"])
        finally:
            gate.set()
            sched.stop()

    def test_cancel_drops_a_sessions_queued_jobs(self):
        sched, gate, done = study_mod.Scheduler(workers=1, limit=10), threading.Event(), []
        try:
            self.assertTrue(sched.submit(0, lambda key: gate.wait(), "busy"))
            until(lambda: sched.load()["running"] == 1)
            sched.submit(1, lambda key, x: done.append(x), "s1", "a")
            sched.submit(1, lambda key, x: done.append(x), "s2", "b")
            self.assertEqual(sched.cancel("s1"), 1)
            gate.set()
            until(lambda: done == ["b"])
        finally:
            gate.set()
            sched.stop()


class SaveHelpers:
    """Helpers for the review rounds' tests."""

    @contextmanager
    def failing_save(self, name, times=1):
        """The next `times` saves of the file called `name` from this thread fail, as on a full disk (the
        writers' threads still save)."""
        real, left = engine.write_json, [times]

        def flaky(path, obj):
            if left[0] and Path(path).name == name and threading.current_thread() is threading.main_thread():
                left[0] -= 1
                raise OSError(28, "No space left on device")
            return real(path, obj)
        with mock.patch.object(engine, "write_json", flaky):
            yield left

    def notes(self):
        path = self.root / "tracks" / "demo" / "notes.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def mock_paper(self, st, count=5):
        sid = st.start("mock", "demo", count=count)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == count)
        return sid, [q["id"] for q in st.mock_begin(sid)["questions"]]


class RoundTwo(SaveHelpers, Base):
    """Each test pins a defect found in the second review (the comments name what used to go wrong)."""

    # -- a failure after the log was written keeps what was logged --
    def test_a_save_that_fails_after_the_answers_are_logged_keeps_them(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        answers = [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 2}] \
            + self.answer_rest(st, sid, v, skip=(q1,))
        with self.failing_save(f"{sid}.json"):
            with self.assertRaises(OSError):
                st.answer(sid, answers, block=1)
        q = st.sessions[sid]["questions"][q1]
        self.assertEqual((q["state"], q["answer"]["teach"]["action"]), ("answered", "teach"))  # was: put back
        self.assertIsNotNone(q.get("followup"))                       # was: none, the resend being a duplicate
        self.assertEqual(st.answer(sid, answers, block=1)["totals"]["answered"], 3)
        self.assertEqual(len(self.events("attempt")), 3)
        st.sweep()                                                    # the save is tried again
        saved = json.loads((st.folder / f"{sid}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["questions"][q1]["state"], "answered")
        self.assertNotIn(sid, st._unsaved)

    def test_a_close_whose_save_fails_stays_closed_and_is_noted_once(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        with self.failing_save(f"{sid}.json", times=4):
            with self.assertRaises(OSError):
                st.end(sid, skip_close=True)
            for _ in range(3):                                        # the page polls, the sweep runs
                self.assertEqual(st.view(sid)["status"], "done")      # was: open again, a note at every try
                st.status()
                st.sweep()
        self.assertEqual(self.notes().count("Learn in the dashboard"), 0)  # not until it is filed as finished
        st.sweep()                                                    # the disk has room again
        st.sweep()
        self.assertEqual(self.notes().count("Learn in the dashboard"), 1)
        self.assertTrue((st.done_folder / f"{sid}.json").exists())
        self.assertEqual(len(self.events("session_end")), 1)

    def test_nested_rollbacks_keep_their_own_frames(self):
        st = self.study()
        s = {"id": "0123456789abcdef", "n": 0}
        with self.assertRaises(RuntimeError):
            with st._atomic(s):
                with st._atomic(s):
                    s["n"] = 1                                        # nothing logged: ends normally
                st._logged()                                          # then the outer block reaches the log
                s["n"] = 2
                raise RuntimeError("the save failed")
        self.assertEqual((s["n"], st._frames), (2, []))              # kept: the log already holds it
        st._unsaved.clear()                                          # (a stand-in session: nothing to save later)

    def test_a_closed_session_found_open_after_a_restart_is_closed_not_resumed(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        with self.failing_save(f"{sid}.json", times=10):
            with self.assertRaises(OSError):
                st.end(sid, skip_close=True)
            st.stop()                                                 # it stops before the disk has room again
        again = self.study()
        self.assertEqual(again.view(sid)["status"], "done")           # was: resumed after its end was logged
        self.assertEqual((len(self.events("session_end")), self.notes().count("Learn in the dashboard")), (1, 1))

    def test_a_mock_whose_save_fails_after_grading_stays_submitted(self):
        st = self.study()
        sid, qs = self.mock_paper(st)
        st.mock_save(sid, {qs[0]: {"choice": self.right(st, sid, qs[0]), "confidence": 4}})
        with self.failing_save(f"{sid}.json"):
            with self.assertRaises(OSError):
                st.mock_submit(sid)
        self.assertIsNotNone(st.view(sid)["mock"]["submitted"])      # was: the exam open again
        with self.assertRaises(StudyError):                          # so a changed answer can't go in unrecorded
            st.mock_save(sid, {qs[0]: {"choice": self.wrong(st, sid, qs[0]), "confidence": 4}})
        self.assertEqual(len(self.events("attempt")), 1)

    # -- disputes --
    def test_a_dispute_that_fails_part_way_can_be_disputed_again(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        answer = st.sessions[sid]["questions"][q1]["answer"]
        with self.failing_save(f"{sid}.json"):
            with self.assertRaises(OSError):
                st.dispute(sid, q1, "the key is wrong")               # its first save
        self.assertIsNone(answer.get("dispute"))
        with mock.patch.object(st, "_void_refs", side_effect=OSError("the log can't be written")):
            with self.assertRaises(OSError):
                st.dispute(sid, q1, "the key is wrong")               # after the verdict
        self.assertIsNone(answer.get("dispute"))                     # was: 'checking' for ten minutes
        self.assertEqual(st.end(sid, skip_close=True)["status"], "done")  # was: refused as busy

    def test_a_dispute_cut_off_by_a_restart_can_be_disputed_again(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        st.end(sid, skip_close=True)
        path = st.done_folder / f"{sid}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["questions"][q1]["answer"]["dispute"] = {"state": "checking", "at": study_mod._now(), "run": "lost"}
        engine.write_json(path, saved)                               # as a stop during the check left it
        st.stop()
        again = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        self.assertNotIn("dispute", again.view(sid)["questions"][0])  # was: 'checking' for good
        v = again.dispute(sid, q1, "the key is wrong")
        self.assertEqual(v["questions"][0]["dispute"]["state"], "voided")

    def test_a_question_voided_while_its_follow_ups_dispute_runs_settles_both(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.wrong(st, sid, q1, twin=True), "confidence": 2})
        f = st.sessions[sid]["questions"][q1]["followup"]
        st.pen.delay = 0.6                                           # the follow-up's check takes a while
        worker = threading.Thread(target=st.dispute, args=(sid, q1, "its key is wrong"), kwargs={"which": "twin"})
        worker.start()
        until(lambda: (f.get("dispute") or {}).get("state") == "checking")
        st.dispute(sid, q1, "the question's key is wrong", force=True)  # voids both, without a check
        worker.join(10)
        self.assertEqual(f["dispute"]["state"], "voided")            # was: 'checking', End refused as busy
        self.assertFalse(st._checking(st.sessions[sid]))
        self.assertEqual(st.end(sid, skip_close=True)["status"], "done")

    # -- voids from outside the session --
    def test_a_follow_up_for_an_answer_voided_on_its_concept_page_is_not_offered(self):
        st = self.study()
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        engine.void_attempt(self.store, attempt=self.attempt(sid, q1)["id"], reason="key wrong (concept page)",
                            source="web")
        card = st.view(sid)["questions"][0]
        self.assertEqual((card["result"]["voided"], card["can_dispute"], card["followup"]["state"]),
                         (True, False, "dropped"))                   # was: counted, disputable, still offered

    def test_no_closing_re_check_for_a_mistake_voided_on_its_concept_page(self):
        st = self.study()
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 1)
        engine.void_attempt(self.store, attempt=self.attempt(sid, q1)["id"], reason="key wrong (concept page)",
                            source="web")
        self.assertEqual(st.view(sid)["closers"], 0)                 # was: a re-check of M1, which is gone
        v = st.end(sid)
        self.assertEqual(v["status"], "done")
        self.assertNotIn("M1", engine.Model(self.store).mistakes)

    def test_a_mock_answer_voided_during_its_review_is_scored_out_at_once(self):
        st = self.study()
        sid, qs = self.mock_paper(st)
        answers = {q: {"choice": self.right(st, sid, q), "confidence": 4} for q in qs}
        answers[qs[4]] = {"choice": self.wrong(st, sid, qs[4]), "confidence": 4}
        st.mock_save(sid, answers)
        st.mock_submit(sid)
        engine.void_attempt(self.store, attempt=self.attempt(sid, qs[4])["id"], reason="key wrong (concept page)",
                            source="web")
        v = st.view(sid)
        self.assertEqual((v["mock"]["debrief"]["right"], v["mock"]["debrief"]["total"]), (4, 4))  # was: 4/5
        card = next(q for q in v["questions"] if q["id"] == qs[4])
        self.assertEqual((card["result"]["voided"], card["can_dispute"]), (True, False))
        calls = len(st.pen.calls)
        v = st.dispute(sid, qs[4], "the key is wrong")               # the view comes back as it is: voided
        self.assertEqual(len(st.pen.calls), calls)                   # was: a paid check, maybe 'upheld'
        self.assertTrue(next(q for q in v["questions"] if q["id"] == qs[4])["result"]["voided"])
        st.mock_finish(sid)
        logged = self.events("mock")[-1]
        self.assertEqual((logged["score"], logged["questions"], st._get(sid)["mock"]["debrief"]["voided"]), (1.0, 4, 1))

    def test_a_mock_logged_before_a_crash_is_logged_after_the_restart(self):
        st = self.study()
        sid, qs = self.mock_paper(st)
        st.mock_save(sid, {q: {"choice": self.right(st, sid, q), "confidence": 4} for q in qs})
        st.mock_submit(sid)
        engine.log_mock(self.store, "demo", 1.0, questions=5, source="web", session=sid)  # then the process died
        st.stop()
        again = self.study()
        with self.assertRaises(StudyError) as ctx:
            again.dispute(sid, qs[0], "the key is wrong")
        self.assertEqual(ctx.exception.code, "closed")               # was: re-scored after the log had the score
        again.mock_finish(sid)
        self.assertEqual(len(self.events("mock")), 1)

    # -- mock timing and dating --
    def test_a_mock_the_clock_submits_after_a_restart_keeps_its_date(self):
        st = self.study()
        sid = st.start("mock", "demo", count=5)["id"]
        until(lambda: st.view(sid)["mock"]["ready"] == 5)
        q1 = st.view(sid)["questions"][0]["id"]
        cid, registry = self.concept(st, sid, q1), self.store.concepts()
        engine.record_events(self.store, [engine.build_attempt(           # in Claude Code: a miss opens M1
            {"concept": cid, "result": "incorrect", "confidence": 2, "track": "demo"}, registry)])
        self.at("2026-11-15T18:05:00Z")
        st.mock_begin(sid)
        st.mock_save(sid, {q1: {"choice": self.right(st, sid, q1), "confidence": 4}})
        due = engine.parse_ts(st.sessions[sid]["mock"]["deadline"]) + timedelta(seconds=study_mod.MOCK_GRACE)
        st.stop()                                                    # stopped with the exam running
        self.at("2026-11-16T18:30:00Z")                              # the next evening, in Claude Code
        self.store.append({"type": "session_start", "track": "demo", "focus": "learn"})
        other = next(c for c in ("demo.basics.a", "demo.advanced.e", "demo.basics.b") if c != cid)
        engine.record_events(self.store, [engine.build_attempt({"concept": other, "result": "correct",
                                                                "track": "demo"}, registry)])
        self.at("2026-11-16T19:00:00Z")
        again = self.study()                                         # the clock submits it on the next start
        self.assertTrue(again.view(sid)["mock"]["submitted"])
        self.assertEqual(self.attempt(sid, q1)["ts"], engine.iso(due))  # was: the next evening's newest event
        self.assertEqual(engine.Model(self.store).mistakes["M1"]["status"], "fixing")  # was: resolved
        again.mock_finish(sid)
        self.assertEqual(self.events("mock")[-1]["ts"], engine.iso(due))  # dated when it was sat, not reviewed

    def test_a_full_mock_of_a_long_exam_counts_as_full(self):
        track = self.store.track("demo")
        track["exam"]["questions"] = 10
        self.store.save_track(track)
        with mock.patch.object(study_mod, "MAX_FULL_MOCK", 8):     # an exam longer than the dashboard's longest
            st = self.study()
            sid = st.start("mock", "demo", count="full")["id"]
            until(lambda: st.view(sid)["mock"]["ready"] == 8)
            st.mock_begin(sid)
            st.mock_submit(sid)
            st.mock_finish(sid)
        self.assertTrue(self.events("mock")[-1]["full"])             # was: never full

    def test_a_full_mock_is_timed_like_the_exam_when_its_length_is_unknown(self):
        track = self.store.track("demo")
        track["exam"].pop("questions")
        track["exam"]["minutes"] = 30
        self.store.save_track(track)
        st = self.study()
        full = st.start("mock", "demo", count="full")["id"]
        self.assertEqual(st.sessions[full]["mock"]["minutes"], 30)  # was: 105, at 1.75 a question
        mini = st.start("mock", "demo")["id"]
        self.assertEqual(st.sessions[mini]["mock"]["minutes"], 10)

    # -- writing --
    def test_a_mock_paper_takes_no_more_calls_than_its_batches(self):
        rng = random.Random(7)
        for _ in range(300):
            groups = [[f"{g}-{i}" for i in range(rng.randint(1, 7))] for g in range(rng.randint(1, 40))]
            calls, n = study_mod.pack(groups, 5), sum(len(g) for g in groups)
            self.assertEqual(len(calls), -(-n // 5))
            self.assertTrue(all(1 <= len(c) <= 5 for c in calls))
            self.assertEqual(sorted(q for c in calls for q in c), sorted(q for g in groups for q in g))
            whole = [g for g in groups if any(set(g) <= set(c) for c in calls)]
            self.assertGreaterEqual(len(whole), sum(1 for g in groups if len(g) <= 5) - len(calls))
        groups = [[f"c{g}-{i}" for i in range(3 if g < 40 else 2)] for g in range(80)]  # 200: threes and twos
        calls = study_mod.pack(groups, 5)
        self.assertEqual(len(calls), 40)                             # was: 64, the whole job queue
        self.assertTrue(all(any(set(g) <= set(c) for c in calls) for g in groups))

    def test_a_rewrite_asks_only_for_the_questions_that_failed(self):
        class SecondBad(writer.FakeWriter):
            def run(self, system, prompt, schema=None):
                out = super().run(system, prompt, schema)
                if schema is writer.BATCH_SCHEMA:
                    out["questions"][1]["question"]["options"] = out["questions"][1]["question"]["options"][:2]
                return out
        st = self.study(SecondBad())
        model, track = engine.Model(self.store), self.store.track("demo")
        payloads = [study_mod.item_payload(model, self.store, track, {"concept": "demo.basics.a", "difficulty": d},
                                           "mcq", []) for d in (2, 4)]
        results, error = st._write(track, payloads, "", False)
        self.assertIsNone(error)
        self.assertTrue(all(results))
        rewrite = st.pen.calls[-1]
        self.assertIs(rewrite["schema"], writer.SOLO_SCHEMA)        # only the one that failed (was: both again)
        self.assertIn("The item:", rewrite["prompt"])
        self.assertIn(results[0]["rule"], rewrite["prompt"])         # told what the kept one tests

    def test_a_retried_question_is_not_failed_by_the_first_tries_late_failure(self):
        class Gated(writer.FakeWriter):
            def __init__(self):
                super().__init__()
                self.gates, self.writes = [threading.Event(), threading.Event()], 0

            def run(self, system, prompt, schema=None):
                if schema in (writer.ONE_SCHEMA, writer.SOLO_SCHEMA, writer.BATCH_SCHEMA) and self.writes < 2:
                    n, self.writes = self.writes, self.writes + 1
                    self.gates[n].wait(10)
                    if n == 0:
                        raise writer.WriterError("Claude took too long.", "failed")
                return super().run(system, prompt, schema)
        pen = Gated()
        st = self.study(pen)
        sid = st.start("quiz", "demo", scope={"concept": "demo.advanced.f"}, count=2)["id"]
        qid = st.view(sid)["questions"][0]["id"]
        try:
            until(lambda: pen.writes == 1)                           # the first try is running
            self.at("2026-11-15T18:20:00Z")                          # and has passed the writing timeout
            self.assertEqual(st.view(sid)["questions"][0]["code"], "timeout")
            st.retry(sid, qid)
            until(lambda: pen.writes == 2)                           # the retry is running too
            pen.gates[0].set()                                       # the first try fails now
            time.sleep(0.3)
            self.assertEqual(st.sessions[sid]["questions"][qid]["state"], "writing")  # was: failed again
        finally:
            pen.gates[0].set()
            pen.gates[1].set()
        until(lambda: st.view(sid)["questions"][0]["state"] == "ready")  # was: the good question thrown away

    # -- the rest --
    def test_a_coach_reply_that_cant_be_saved_still_frees_the_coach(self):
        st = self.study()
        real, failed = st._chat_save, []

        def flaky(messages):
            if messages and messages[-1].get("role") == "coach" and not failed:
                failed.append(True)
                raise OSError(28, "No space left on device")
            return real(messages)
        with mock.patch.object(st, "_chat_save", flaky):
            st.chat("How am I doing?")
            until(lambda: not st.coach_view()["busy"])               # was: busy until a restart
            self.assertEqual(st.coach_view()["messages"][-1]["role"], "coach")
            st.chat("And now?")
            until(lambda: not st.coach_view()["busy"])
        saved = json.loads(st._chat_path().read_text(encoding="utf-8"))["messages"]
        self.assertEqual([m["role"] for m in saved], ["you", "coach", "you", "coach"])

    def test_the_cap_holds_what_a_call_of_that_kind_costs(self):
        learner = self.store.learner()
        learner["preferences"]["web_daily_usd"] = 1.0
        self.store.save_learner(learner)
        usage = study_mod.Usage(self.store)
        for _ in range(40):
            usage.record("check", {"cost_usd": 0.02}, "ok")
        usage.record("question", {"cost_usd": 0.15}, "ok")         # $0.95 spent
        with usage.reserve("question"):                             # holds $0.15
            with self.assertRaises(StudyError):                     # was: held the $0.02 average and let it by
                with usage.reserve("question"):
                    pass

    def test_a_quiz_says_no_foundation_check_follows(self):
        st = self.study()
        sid, v = self.start_block(st, "quiz", scope={"concept": "demo.advanced.f"}, count=3)
        for n in (1, 2):
            qid = v["questions"][0]["id"]
            out = st.answer(sid, [{"qid": qid, "choice": self.wrong(st, sid, qid), "confidence": 2}], block=n)
            teach = out["questions"][0]["result"]["teach"]
            if n == 1:
                v = self.ready(st, st.next_block(sid, block=n)["id"])
        self.assertEqual(teach["action"], "check-prerequisites")
        self.assertEqual(([p["concept"] for p in teach["prereqs"]], teach["checked_next"]),
                         (["demo.advanced.e"], []))                 # was: 'the next questions check that first'

    def test_a_notes_file_that_isnt_utf8_doesnt_stop_a_close(self):
        path = self.root / "tracks" / "demo" / "notes.md"
        old = "# Notes\n\n- café\n".encode("cp1252")
        path.write_bytes(old)
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        self.assertEqual(st.end(sid, skip_close=True)["status"], "done")  # was: a 500 at every try
        self.assertEqual((len(self.events("session_end")), path.read_bytes()), (1, old))


class GatedDispute(writer.FakeWriter):
    """A writer whose dispute check waits until the test lets it answer."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gate, self.waiting = threading.Event(), threading.Event()

    def run(self, system, prompt, schema=None):
        if schema is writer.DISPUTE_SCHEMA:
            self.waiting.set()
            self.gate.wait(10)
        return super().run(system, prompt, schema)


class RoundThree(SaveHelpers, Base):
    """Each test pins a defect found in the third review (the comments name what used to go wrong)."""

    def void_elsewhere(self, sid, qid, suffix="", source="web"):
        """Void an answer outside the session: on its concept page (source web), or in Claude Code (None)."""
        engine.void_attempt(self.store, attempt=self.attempt(sid, qid, suffix)["id"], reason="the key was wrong",
                            source=source)

    # -- voids made outside a session, and taken back --
    def test_a_void_taken_back_by_undo_counts_the_answer_again(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        self.void_elsewhere(sid, q1, source=None)                    # `void` in Claude Code...
        self.assertEqual(st.view(sid)["totals"]["answered"], 2)
        engine.undo_events(self.store, 1)                            # ...then `undo` there
        v = st.view(sid)
        card = v["questions"][0]
        self.assertEqual((v["totals"]["answered"], card["result"]["voided"], card["can_dispute"]), (3, False, True))
        self.assertEqual(st.end(sid, skip_close=True)["summary"]["answers"], 3)  # was: 2, the void kept for good

    def test_a_mock_answer_voided_then_brought_back_is_scored_again(self):
        st = self.study()
        sid, qs = self.mock_paper(st)
        answers = {q: {"choice": self.right(st, sid, q), "confidence": 4} for q in qs}
        answers[qs[4]] = {"choice": self.wrong(st, sid, qs[4]), "confidence": 4}
        st.mock_save(sid, answers)
        st.mock_submit(sid)
        self.void_elsewhere(sid, qs[4], source=None)
        self.assertEqual(st.view(sid)["mock"]["debrief"]["total"], 4)
        engine.undo_events(self.store, 1)
        self.assertEqual(st.view(sid)["mock"]["debrief"]["total"], 5)
        st.mock_finish(sid)
        logged = self.events("mock")[-1]
        self.assertEqual((logged["score"], logged["questions"]), (0.8, 5))  # was: 1.0 on 4

    def test_a_follow_up_is_refused_at_once_after_its_question_is_voided_elsewhere(self):
        st = self.study()
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        self.void_elsewhere(sid, q1)                                 # and no view comes in between
        v = st.followup(sid, q1, {"choice": self.wrong(st, sid, q1, twin=True), "confidence": 2})
        self.assertIsNone(self.attempt(sid, q1, ":t"))               # not recorded: the fresh view comes back
        self.assertTrue(v["questions"][0]["result"]["voided"])       # (round 4: not an error the page can't act on)
        self.assertNotIn("M1", engine.Model(self.store).mistakes)    # was: brought back by the follow-up's miss

    def test_a_closing_re_check_is_dropped_at_once_after_its_mistake_is_voided_elsewhere(self):
        st = self.study()
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 1)
        closer = st.end(sid)["questions"][0]["id"]
        self.void_elsewhere(sid, q1)
        st.answer(sid, [{"qid": closer, "choice": self.wrong(st, sid, closer), "confidence": 2}])
        self.assertIsNone(self.attempt(sid, closer, ":c"))           # was: recorded, opening M1 again
        self.assertNotIn("M1", engine.Model(self.store).mistakes)

    def test_a_question_voided_elsewhere_during_its_dispute_still_gets_the_rest_of_it(self):
        pen = GatedDispute(verdict={"dispute_wins": True})
        st = self.study(pen)
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.wrong(st, sid, q1, twin=True), "confidence": 2})
        rule = self.question(st, sid, q1)["rule"]
        worker = threading.Thread(target=st.dispute, args=(sid, q1, "the key is wrong"))
        worker.start()
        try:
            self.assertTrue(pen.waiting.wait(5))
            self.void_elsewhere(sid, q1)                             # on its concept page, while the check runs
        finally:
            pen.gate.set()
            worker.join(10)
        gone = {e["target"] for e in self.events("void")}
        self.assertIn(engine.attempt_key(self.attempt(sid, q1, ":t")), gone)  # was: the follow-up still counted
        self.assertEqual(study_mod.disputed_rules(self.store, "demo", self.concept(st, sid, q1)), [rule])
        self.assertEqual(st.sessions[sid]["questions"][q1]["answer"]["dispute"]["state"], "voided")

    # -- the note, the dispute timer, foundation checks, pending reasons --
    def test_a_finished_session_saved_by_another_path_still_gets_its_note(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        with self.failing_save(f"{sid}.json"):
            with self.assertRaises(OSError):
                st.end(sid, skip_close=True)
        st.dispute(sid, q1, "the key is wrong")                      # saves it before any sweep
        self.assertEqual(self.notes().count("Learn in the dashboard"), 1)  # was: never written
        st.sweep()
        self.assertEqual(self.notes().count("Learn in the dashboard"), 1)

    def test_a_slow_check_on_a_finished_session_is_given_up_too(self):
        pen = GatedDispute(verdict={"dispute_wins": True})
        st = self.study(pen)
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        st.end(sid, skip_close=True)
        worker = threading.Thread(target=st.dispute, args=(sid, q1, "the key is wrong"))
        worker.start()
        try:
            self.assertTrue(pen.waiting.wait(5))
            self.at("2026-11-15T18:11:00Z")                          # eleven minutes on, still checking
            self.assertNotIn("dispute", st.view(sid)["questions"][0])  # was: 'checking' while it stayed cached
        finally:
            pen.gate.set()
            worker.join(10)
        self.assertEqual(len(self.events("void")), 0)               # its late verdict is dropped

    def test_a_foundation_already_written_into_the_next_block_counts_as_checked_next(self):
        st = self.study()
        sid, v = self.start_block(st)                                # block 1: D, C, F; block 2: B, E, A
        qids = [q["id"] for q in v["questions"]]
        f = next(q for q in qids if self.concept(st, sid, q) == "demo.advanced.f")
        st.answer(sid, [{"qid": f, "choice": self.wrong(st, sid, f), "confidence": 2}]
                  + self.answer_rest(st, sid, v, skip=(f,)), block=1)
        n = qids.index(f)
        self.offered(st, sid, n=n)
        out = st.followup(sid, f, {"choice": self.wrong(st, sid, f, twin=True), "confidence": 2})
        teach = out["questions"][n]["followup"]["result"]["teach"]
        self.assertEqual(teach["action"], "check-prerequisites")
        self.assertEqual(teach["checked_next"], ["demo.advanced.e"])  # was: [], though the next block asks it

    def test_a_confident_miss_left_without_its_reason_is_dated_when_it_was_answered(self):
        st = self.study()
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, [{"qid": q1, "choice": self.wrong(st, sid, q1), "confidence": 5}], block=1)
        self.at("2026-11-23T08:00:00Z")                              # left over a week; the sweep
        st.sweep()                                                   # closes it, recording the miss
        self.assertEqual(st._get(sid)["status"], "done")
        self.assertEqual(self.attempt(sid, q1)["ts"], DAY1)          # was: 08:00, when it was closed

    # -- usage and the coach --
    def test_a_mock_batch_holds_what_its_questions_cost(self):
        learner = self.store.learner()
        learner["preferences"]["web_daily_usd"] = 1.0
        self.store.save_learner(learner)
        usage = study_mod.Usage(self.store)
        for _ in range(20):
            usage.record("question", {"cost_usd": 0.04}, "ok")     # a morning of learn mode: $0.80
        with usage.reserve("question", items=5):                    # a mock call writing five: $0.20 held
            with self.assertRaises(StudyError):                     # was: held $0.04, as for one question
                with usage.reserve("question", items=5):
                    pass
        usage.record("question", {"cost_usd": 0.10}, "ok", items=5)  # logged per question: $0.02 each
        self.assertEqual(round(usage.estimate("question", 5), 2), 0.20)

    def test_a_coach_reply_that_couldnt_be_saved_is_saved_by_the_sweep(self):
        st = self.study()
        real, failed = st._chat_save, []

        def flaky(messages):
            if messages and messages[-1].get("role") == "coach" and not failed:
                failed.append(True)
                raise OSError(28, "No space left on device")
            return real(messages)
        with mock.patch.object(st, "_chat_save", flaky):
            st.chat("How am I doing?")
            until(lambda: not st.coach_view()["busy"])
        st.sweep()                                                   # was: saved only with the next message
        saved = json.loads(st._chat_path().read_text(encoding="utf-8"))["messages"]
        self.assertEqual([m["role"] for m in saved], ["you", "coach"])


class RoundFour(SaveHelpers, Base):
    """Each test pins a defect found in the fourth review (the comments name what used to go wrong)."""

    def void_elsewhere(self, sid, qid, suffix="", source="web"):
        engine.void_attempt(self.store, attempt=self.attempt(sid, qid, suffix)["id"], reason="the key was wrong",
                            source=source)

    def test_an_idle_session_closed_after_a_sleep_ends_when_the_learner_stopped(self):
        st = self.study()
        sid, v = self.start_block(st)
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        self.at("2026-11-23T08:00:00Z")                              # the lid closed for over a week; then the sweep
        st.sweep()
        end = self.events("session_end")[-1]
        self.assertEqual(end["ts"], DAY1)                            # was: 08:00, the wake-up
        self.assertTrue((self.root / "journal" / "2026-11-15.md").exists())

    def test_a_won_dispute_holds_after_claude_code_undoes_its_own_void(self):
        st = self.study(writer.FakeWriter(verdict={"dispute_wins": True}))
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        st.followup(sid, q1, {"choice": self.wrong(st, sid, q1, twin=True), "confidence": 2})
        self.void_elsewhere(sid, q1, ":t", source=None)              # `void` of the follow-up in Claude Code
        st.dispute(sid, q1, "the key is wrong")                      # then the question's dispute wins
        engine.undo_events(self.store, 1)                            # and Claude Code takes its void back
        twin = self.attempt(sid, q1, ":t")["id"]
        self.assertIn(twin, st._voided_ids())                        # was: counted again in the log only
        v = st.view(sid)
        self.assertEqual(v["totals"]["answered"], 2)                 # the session and the log agree
        self.assertEqual(st.end(sid, skip_close=True)["summary"]["answers"], 2)

    def test_a_void_elsewhere_moves_the_sessions_rev(self):
        st = self.study()
        sid, v = self.start_block(st)
        v = st.answer(sid, self.answer_rest(st, sid, v), block=1)
        before = st.view(sid)["rev"]
        self.void_elsewhere(sid, v["questions"][0]["id"])
        after = st.view(sid)
        self.assertGreater(after["rev"], before)                     # was: the same, so the page kept its old view
        self.assertEqual(after["totals"]["answered"], 2)

    def test_claude_codes_undo_of_a_void_brings_back_the_follow_up_and_the_re_check(self):
        st = self.study()
        sid, q1 = Disputes.miss_first(self, st)
        self.offered(st, sid)
        self.void_elsewhere(sid, q1, source=None)                    # `void` in Claude Code...
        card = st.view(sid)["questions"][0]
        self.assertEqual(st.sessions[sid]["questions"][q1]["followup"]["state"], "dropped")
        engine.undo_events(self.store, 1)                            # ...taken back by `undo`
        card = st.view(sid)["questions"][0]
        self.assertEqual(card["followup"]["state"], "offered")       # was: dropped for good
        st.followup(sid, q1, {"choice": self.right(st, sid, q1, twin=True), "confidence": 4})
        until(lambda: st.view(sid)["closers"] == 1)
        self.void_elsewhere(sid, q1, source=None)
        self.assertEqual(st.view(sid)["closers"], 0)
        engine.undo_events(self.store, 1)
        self.assertEqual(st.view(sid)["closers"], 1)                 # was: the re-check lost, End skipped it

    def test_an_upheld_key_on_an_answer_voided_elsewhere_reads_upheld(self):
        pen = GatedDispute()                                         # the check finds the key right
        st = self.study(pen)
        sid, v = self.start_block(st)
        q1 = v["questions"][0]["id"]
        st.answer(sid, self.answer_rest(st, sid, v), block=1)
        worker = threading.Thread(target=st.dispute, args=(sid, q1, "the key is wrong"))
        worker.start()
        try:
            self.assertTrue(pen.waiting.wait(5))
            self.void_elsewhere(sid, q1)                             # voided on its concept page meanwhile
        finally:
            pen.gate.set()
            worker.join(10)
        card = st.view(sid)["questions"][0]
        self.assertEqual(card["dispute"]["state"], "upheld")         # was: 'voided', as if the check had agreed
        self.assertTrue(card["result"]["voided"])                    # (the card reads the void from the log)


if __name__ == "__main__":
    unittest.main()
