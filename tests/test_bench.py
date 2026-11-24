"""The model benchmark. The model under test and the judge are both fakes: no test reaches a real model or Claude."""
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import polymath as engine               # noqa: E402
import polymath_bench as bench          # noqa: E402
import polymath_models as models        # noqa: E402
import polymath_study as study_mod      # noqa: E402
import polymath_writer as writer        # noqa: E402
import test_models                      # noqa: E402
import test_web                         # noqa: E402



_RELEASE = {}


def setUpModule():
    """These tests cover every provider and the benchmark, which this release switches off."""
    _RELEASE.update(enabled=models.ENABLED, sdk=models.has_sdk, bench=bench.ENABLED)
    models.ENABLED, models.has_sdk, bench.ENABLED = tuple(models.PROVIDERS), (lambda: True), True


def tearDownModule():
    models.ENABLED, models.has_sdk, bench.ENABLED = _RELEASE["enabled"], _RELEASE["sdk"], _RELEASE["bench"]


def tiny_suite(exams=2, concepts=4) -> dict:
    out = {"version": 1, "checked": "2026-11-23", "note": "test", "exams": []}
    for e in range(exams):
        domains = [{"name": "Alpha Topics", "low": 0.6, "high": 0.6}, {"name": "Beta Topics", "low": 0.4, "high": 0.4}]
        out["exams"].append({"id": f"exam{e}", "name": f"Exam {e}", "format": "multiple choice", "source": "https://example.com/x",
                             "official_sites": ["example.com"], "domains": domains,
                             "concepts": [{"id": f"t.e{e}.c{n}", "title": f"Concept {e}-{n}", "domain": "Alpha Topics",
                                           "objective": "Apply it."} for n in range(concepts)]})
    return out


class Judge(writer.Backend):
    """A judge that answers from the question text: the option starting 'The documented' is right."""

    name = "judge"
    model = "fake-judge"

    def __init__(self, deep="correct", doubt=False, fail=None):
        super().__init__()
        self.deep, self.doubt, self.fail = deep, doubt, fail
        self.calls = []

    def available(self):
        return True

    def auth_status(self):
        return {"ok": True}

    def run(self, system, prompt, schema=None, web=False, timeout=None, effort=None):
        self.calls.append({"schema": schema, "web": web, "prompt": prompt})
        self.last = {"cost_usd": 0.5 if web else 0.05}
        if self.fail:
            raise writer.WriterError("judge failed", self.fail)
        if schema is bench.BLIND_SCHEMA:
            data = writer._fake_data(prompt)
            right = [o["key"] for o in data["question"]["options"] if o["text"].startswith("The documented")]
            return {"answer": right, "defensible": not self.doubt, "confidence": 5, "problem": "unclear" if self.doubt else ""}
        return {"verdict": self.deep, "right_answer": ["A"], "reason": "The docs say so.",
                "sources": ["https://example.com/doc", "https://evil.test/x"]}


class Target(writer.FakeWriter):
    """The model under test: FakeWriter, with a syllabus that matches the outline and optional failures."""

    def __init__(self, write_fails=False, syllabus_fails=False):
        super().__init__()
        self.write_fails, self.syllabus_fails = write_fails, syllabus_fails
        self.provider = "fake"

    def auth_status(self):
        return {"ok": True, "method": "local"}

    def run(self, system, prompt, schema=None, web=False, timeout=None, effort=None):
        if schema is writer.GOAL_SYLLABUS_SCHEMA:
            if self.syllabus_fails:
                raise writer.WriterError("no", "invalid")
            return {"title": "T", "short": "T", "goal": "g", "sources": [], "exam": {"name": "E", "questions": 0, "minutes": 0, "passing_score": 0},
                    "domains": [{"id": "alpha", "name": "Alpha Topics", "weight": 0.6, "concepts": [
                        {"id": "x.alpha.a", "title": "A", "tags": [], "prereqs": [], "objective": "Do A.", "docs": []}]},
                        {"id": "beta", "name": "Beta Topics", "weight": 0.4, "concepts": [
                            {"id": "x.beta.b", "title": "B", "tags": [], "prereqs": ["x.alpha.a"], "objective": "Do B.", "docs": []}]}]}
        if schema is writer.SOLO_SCHEMA and self.write_fails:
            raise writer.WriterError("no", "invalid")
        return super().run(system, prompt, schema)


class Scoring(unittest.TestCase):
    def test_exact_sections_score_zero(self):
        gold = [{"name": "Design Secure Architectures", "low": .6, "high": .6}, {"name": "Design Cost-Optimized", "low": .4, "high": .4}]
        found = [{"name": "Secure Architectures", "weight": .6}, {"name": "Cost-Optimized", "weight": .4}]
        out = bench.score_sections(found, gold)
        self.assertEqual((out["matched"], out["invented"], out["weight_off"]), (2, 0, 0.0))

    def test_inside_a_published_range_counts_as_exact(self):
        gold = [{"name": "Cloud Concepts", "low": .25, "high": .30}, {"name": "Azure Services Governance", "low": .70, "high": .75}]
        found = [{"name": "Cloud Concepts", "weight": .28}, {"name": "Azure Services Governance", "weight": .72}]
        self.assertEqual(bench.score_sections(found, gold)["weight_off"], 0.0)

    def test_invented_and_missing_sections_are_counted(self):
        gold = [{"name": "Storage", "low": .5, "high": .5}, {"name": "Troubleshooting", "low": .5, "high": .5}]
        found = [{"name": "Storage", "weight": .5}, {"name": "Marketing", "weight": .5}]
        out = bench.score_sections(found, gold)
        self.assertEqual((out["matched"], out["invented"], out["missing"]), (1, 1, ["Troubleshooting"]))
        self.assertEqual(out["weight_off"], 50.0)

    def test_prerequisite_loops(self):
        self.assertTrue(bench.has_cycle([{"id": "a", "prereqs": ["b"]}, {"id": "b", "prereqs": ["a"]}]))
        self.assertFalse(bench.has_cycle([{"id": "a", "prereqs": ["b", "zz"]}, {"id": "b", "prereqs": []}]))

    def test_a_long_chain_is_not_a_recursion_problem(self):
        chain = [{"id": f"n{i}", "prereqs": [f"n{i + 1}"]} for i in range(5000)]
        self.assertFalse(bench.has_cycle(chain))

    def test_only_official_https_pages_count_as_sources(self):
        sites = ["docs.aws.amazon.com"]
        self.assertTrue(bench.official("https://docs.aws.amazon.com/x", sites))
        for bad in ("http://docs.aws.amazon.com/x", "https://docs.aws.amazon.com.evil.test/x", "https://evil.test/docs.aws.amazon.com", "nope"):
            self.assertFalse(bench.official(bad, sites), bad)

    def test_a_second_look_is_needed_on_any_doubt(self):
        ok = {"answer": ["A"], "defensible": True, "confidence": 5}
        self.assertFalse(bench.needs_second_look(ok, ["A"]))
        self.assertTrue(bench.needs_second_look({**ok, "answer": ["B"]}, ["A"]))
        self.assertTrue(bench.needs_second_look({**ok, "defensible": False}, ["A"]))
        self.assertTrue(bench.needs_second_look({**ok, "confidence": 3}, ["A"]))

    def test_a_verdict_outside_the_list_is_refused(self):
        with self.assertRaises(writer.WriterError):
            bench.clean_verdict({"verdict": "fine"}, "ABCD", ["example.com"])

    def test_a_person_outranks_the_judge(self):
        item = {"judge": {"verdict": "correct"}}
        self.assertFalse(bench.is_bad(item))
        self.assertTrue(bench.is_bad({**item, "human": {"key": "wrong"}}))
        self.assertFalse(bench.is_bad({"judge": {"verdict": "wrong_key"}, "human": {"key": "right"}}))
        self.assertTrue(bench.is_bad({"judge": {"verdict": "wrong_key"}, "human": {"key": "unsure"}}))

    def test_a_self_check_that_did_not_run_catches_nothing(self):
        self.assertFalse(bench.caught_by_itself({"self": {"ran": False}}))
        self.assertFalse(bench.caught_by_itself({}))
        self.assertTrue(bench.caught_by_itself({"self": {"ran": True, "agrees": False}}))

    def test_the_range_is_honest_about_a_small_sample(self):
        low, high = bench.wilson(0, 50)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.05)
        self.assertEqual(bench.wilson(0, 0), (0.0, 1.0))

    def test_every_model_gets_the_same_paper(self):
        suite = bench.load_suite()
        a, b = bench.new_items(suite), bench.new_items(suite)
        self.assertEqual(a, b)
        self.assertEqual(sum(1 for i in a.values() if i["kind"] == "question"), 50)
        self.assertEqual(sum(1 for i in a.values() if i["kind"] == "syllabus"), 5)

    def test_the_suite_is_consistent(self):
        suite = bench.load_suite()
        ids = [c["id"] for e in suite["exams"] for c in e["concepts"]]
        self.assertEqual(len(ids), len(set(ids)))
        for exam in suite["exams"]:
            names = {d["name"] for d in exam["domains"]}
            self.assertEqual({c["domain"] for c in exam["concepts"]} - names, set(), exam["id"])
            self.assertEqual({c["domain"] for c in exam["concepts"]}, names, exam["id"])
            self.assertLessEqual(sum(d["low"] for d in exam["domains"]), 1.0001)
            self.assertGreaterEqual(sum(d["high"] for d in exam["domains"]), 0.9999)
            self.assertTrue(exam["source"].startswith("https://"))


def _result(n_good=48, escaped=2, caught=0, failed=0):
    run = {"id": "a" * 16, "items": {}}
    for n in range(2):
        run["items"][f"s{n}"] = {"id": f"s{n}", "kind": "syllabus", "exam": "e", "state": "done",
                                 "score": {"sections": 4, "matched": 4, "weight_off": 0.0}}
    add = lambda name, **kw: run["items"].__setitem__(name, {"id": name, "kind": "question", "exam": "e", **kw})  # noqa: E731
    for n in range(n_good):
        add(f"g{n}", state="written", question={"answer": ["A"]}, self={"ran": True, "agrees": True}, judge={"verdict": "correct"})
    for n in range(escaped):
        add(f"e{n}", state="written", question={"answer": ["A"]}, self={"ran": True, "agrees": True}, judge={"verdict": "wrong_key"})
    for n in range(caught):
        add(f"c{n}", state="written", question={"answer": ["A"]}, self={"ran": True, "agrees": False}, judge={"verdict": "wrong_key"})
    for n in range(failed):
        add(f"f{n}", state="failed")
    return run


class Rating(unittest.TestCase):
    def test_under_the_bar_is_recommended(self):
        out = bench.summarize(_result(48, 2))
        self.assertEqual((out["rating"], out["escaped_rate"]), ("recommended", 0.04))

    def test_at_the_bar_is_not(self):
        out = bench.summarize(_result(19, 1))
        self.assertEqual(out["rating"], "not_recommended")
        self.assertTrue(any("wrong or unclear" in r for r in out["reasons"]))

    def test_a_caught_bad_question_does_not_count_as_misleading(self):
        out = bench.summarize(_result(40, 1, caught=9))
        self.assertEqual((out["bad"], out["escaped"], out["caught"], out["rating"]), (10, 1, 9, "recommended"))

    def test_too_many_failures_is_not_recommended(self):
        out = bench.summarize(_result(40, 0, failed=10))
        self.assertEqual(out["rating"], "not_recommended")

    def test_a_failed_syllabus_counts(self):
        run = _result(50, 0)
        run["items"]["s0"] = {"id": "s0", "kind": "syllabus", "exam": "e", "state": "failed"}
        self.assertEqual(bench.summarize(run)["rating"], "not_recommended")

    def test_an_unjudged_question_means_no_rating_yet(self):
        run = _result(50, 0)
        del run["items"]["g0"]["judge"]
        out = bench.summarize(run)
        self.assertEqual((out["rating"], out["unjudged"]), ("unfinished", 1))

    def test_a_persons_check_changes_the_score(self):
        run = _result(50, 0)
        for n in range(3):
            run["items"][f"g{n}"]["human"] = {"key": "wrong"}
        out = bench.summarize(run)
        self.assertEqual((out["escaped"], out["reviewed"], out["agreed"], out["rating"]), (3, 3, 0, "not_recommended"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.env = mock.patch.dict(os.environ, {"POLYMATH_HOME": str(self.root)})
        self.env.start()
        self.store = engine.Store(self.root)
        with redirect_stdout(StringIO()):
            engine.cmd_init(Namespace(name="Ada", persona=None, json=True), self.store)
        self.benches, self.studies = [], []

    def tearDown(self):
        for b in self.benches:
            b.close()
        for st in self.studies:
            st.stop()
        self.env.stop()
        self.tmp.cleanup()

    def make(self, target=None, judge=None, suite=None, **kw):
        st = study_mod.Study(self.store, models.NoModel())
        self.studies.append(st)
        manager = models.Models(self.store, st, **kw)
        b = bench.Benchmark(self.store, st, manager, judge=judge or Judge(), suite=suite or tiny_suite())
        self.target = target or Target()
        b._pen = lambda t: self.target
        self.benches.append(b)
        return b

    def go(self, b):
        with mock.patch.object(models, "PROVIDERS", models.PROVIDERS):
            out = b.start({"provider": "ollama", "model": "small:8b", "url": "http://127.0.0.1:1", "max_usd": 25, "confirm": True})
        return out["runs"][0]["id"]

    def finish(self, b, limit=15):
        end = time.time() + limit
        while b._busy() and time.time() < end:
            time.sleep(0.01)
        self.assertFalse(b._busy(), "the run never finished")


class Running(Base):
    def test_nothing_starts_without_confirmation(self):
        b = self.make()
        with self.assertRaises(bench.BenchError) as ctx:
            b.start({"provider": "ollama", "model": "small:8b"})
        self.assertEqual(ctx.exception.code, "confirm")
        self.assertEqual(b.judge_pen.calls, [])
        self.assertEqual(b.view()["runs"], [])

    def test_a_full_run_scores_every_question(self):
        b = self.make()
        rid = self.go(b)
        self.finish(b)
        v = b.run_view(rid)
        s = v["summary"]
        self.assertEqual((v["status"], s["questions"], s["written"], s["judged"], s["syllabi_done"]), ("done", 8, 8, 8, 2))
        self.assertEqual((s["rating"], s["sections_matched"], s["sections"]), ("recommended", 4, 4))
        self.assertEqual(v["usage"]["judge"]["calls"], 8)              # blind only: nothing to settle
        self.assertAlmostEqual(v["usage"]["judge"]["usd"], 0.4)

    def test_the_judge_never_sees_the_key_on_its_first_look(self):
        b = self.make()
        self.go(b)
        self.finish(b)
        for call in b.judge_pen.calls:
            self.assertIs(call["schema"], bench.BLIND_SCHEMA)
            self.assertFalse(call["web"])
            data = writer._fake_data(call["prompt"])
            self.assertNotIn("answer_key", data)
            for o in data["question"]["options"]:
                self.assertEqual(set(o), {"key", "text"})

    def test_a_disagreement_gets_one_web_look_with_official_sources_only(self):
        b = self.make(judge=Judge(deep="wrong_key", doubt=True))
        rid = self.go(b)
        self.finish(b)
        v = b.run_view(rid)
        web = [c for c in b.judge_pen.calls if c["web"]]
        self.assertEqual(len(web), 8)
        q = next(i for i in v["items"] if i["kind"] == "question")
        self.assertEqual(q["judge"]["verdict"], "wrong_key")
        self.assertEqual(q["judge"]["sources"], ["https://example.com/doc"])
        self.assertEqual(v["summary"]["rating"], "not_recommended")

    def test_no_learner_data_reaches_the_model_or_the_judge(self):
        learner = self.store.learner()
        self.store.profile_md.write_text("## Background\n- SECRET-LEARNER-FACT works at Acme\n", encoding="utf-8")
        self.assertIsNotNone(learner)
        b = self.make()
        self.go(b)
        self.finish(b)
        everything = json.dumps([c["prompt"] for c in b.judge_pen.calls]) + json.dumps([c["prompt"] for c in self.target.calls])
        self.assertNotIn("SECRET-LEARNER-FACT", everything)
        self.assertNotIn("Ada", everything)

    def test_write_failures_are_scored_not_hidden(self):
        b = self.make(target=Target(write_fails=True))
        rid = self.go(b)
        self.finish(b)
        s = b.run_view(rid)["summary"]
        self.assertEqual((s["failed"], s["written"], s["rating"]), (8, 0, "not_recommended"))

    def test_the_model_going_offline_pauses_and_resume_carries_on(self):
        target = Target()
        original, seen = target.run, {"n": 0}

        def flaky(system, prompt, schema=None, **kw):
            seen["n"] += 1
            if seen["n"] == 6 and not seen.get("back"):
                raise writer.WriterError("Ollama isn't responding", "model_offline")
            return original(system, prompt, schema, **kw)
        target.run = flaky
        b = self.make(target=target)
        rid = self.go(b)
        self.finish(b)
        v = b.run_view(rid)
        self.assertEqual((v["status"], v["code"]), ("paused", "model_offline"))
        before = v["summary"]["written"]
        seen["back"] = True
        b.resume(rid)
        self.finish(b)
        v = b.run_view(rid)
        self.assertEqual((v["status"], v["summary"]["written"]), ("done", 8))
        self.assertGreater(v["summary"]["written"], before - 1)

    def test_the_budget_stops_the_run_and_can_be_raised(self):
        b = self.make(judge=Judge(deep="wrong_key", doubt=True))     # $0.55 a question
        out = b.start({"provider": "ollama", "model": "small:8b", "url": "http://127.0.0.1:1", "max_usd": 1, "confirm": True})
        rid = out["runs"][0]["id"]
        self.finish(b)
        v = b.run_view(rid)
        self.assertEqual((v["status"], v["code"]), ("paused", "budget"))
        self.assertLess(v["usage"]["judge"]["usd"], 1.6)
        self.assertLess(v["summary"]["judged"], 8)
        b.resume(rid, {"max_usd": 50})
        self.finish(b)
        self.assertEqual(b.run_view(rid)["status"], "done")

    def test_a_claude_usage_limit_pauses_rather_than_failing_questions(self):
        b = self.make(judge=Judge(fail="usage_limit"))
        rid = self.go(b)
        self.finish(b)
        v = b.run_view(rid)
        self.assertEqual((v["status"], v["code"]), ("paused", "usage_limit"))
        self.assertEqual(len([c for c in b.judge_pen.calls]), 1)

    def test_a_judge_that_keeps_failing_leaves_questions_unjudged_not_passed(self):
        b = self.make(judge=Judge(fail="invalid"))
        rid = self.go(b)
        self.finish(b)
        v = b.run_view(rid)
        self.assertEqual((v["status"], v["code"], v["summary"]["rating"]), ("paused", "judge_failed", "unfinished"))

    def test_pause_keeps_the_work(self):
        target = writer.FakeWriter(delay=0.03)
        b = self.make(target=target)
        target.auth_status = lambda: {"ok": True}
        rid = self.go(b)
        time.sleep(0.15)
        b.pause(rid)
        self.assertFalse(b._busy())
        self.assertEqual(b.run_view(rid)["status"], "paused")

    def test_a_run_left_running_is_paused_at_the_next_start(self):
        b = self.make()
        rid = self.go(b)
        self.finish(b)
        run = b._read(rid)
        run["status"] = "running"
        b._save(run)
        again = bench.Benchmark(self.store, b.study, b.models, judge=Judge(), suite=tiny_suite())
        self.assertEqual((again._read(rid)["status"], again._read(rid)["code"]), ("paused", "restarted"))

    def test_only_one_run_at_a_time(self):
        target = writer.FakeWriter(delay=0.05)
        b = self.make(target=target)
        target.auth_status = lambda: {"ok": True}
        rid = self.go(b)
        with self.assertRaises(bench.BenchError) as ctx:
            self.go(b)
        self.assertEqual(ctx.exception.code, "busy")
        b.pause(rid)

    def test_a_fixed_model_dashboard_cannot_run_it(self):
        b = self.make(locked=True)
        with self.assertRaises(bench.BenchError) as ctx:
            self.go(b)
        self.assertEqual(ctx.exception.code, "locked")

    def test_a_person_can_check_and_undo(self):
        b = self.make()
        rid = self.go(b)
        self.finish(b)
        q = next(i for i in b.run_view(rid)["items"] if i["kind"] == "question")
        out = b.review(rid, {"item": q["id"], "key": "wrong"})
        self.assertEqual((out["summary"]["bad"], out["summary"]["reviewed"], out["summary"]["agreed"]), (1, 1, 0))
        out = b.review(rid, {"item": q["id"], "key": None})
        self.assertEqual((out["summary"]["bad"], out["summary"]["reviewed"]), (0, 0))
        with self.assertRaises(bench.BenchError):
            b.review(rid, {"item": q["id"], "key": "maybe"})

    def test_results_are_private_and_ids_cannot_escape_the_folder(self):
        b = self.make()
        rid = self.go(b)
        self.finish(b)
        self.assertEqual(stat.S_IMODE((self.root / "benchmark" / f"{rid}.json").stat().st_mode) & 0o077, 0)
        self.assertIn("benchmark/", (self.root / ".gitignore").read_text(encoding="utf-8").splitlines())
        for bad in ("../models", "..%2fmodels", "a" * 15, rid + "x"):
            with self.assertRaises(bench.BenchError):
                b.run_view(bad)

    def test_the_model_page_shows_the_newest_finished_result(self):
        b = self.make()
        rid = self.go(b)
        self.finish(b)
        self.assertEqual(b.ratings()["ollama:small:8b"]["run"], rid)
        self.assertEqual(b.ratings()["ollama:small:8b"]["rating"], "recommended")

    def test_the_estimate_learns_from_measured_runs(self):
        b = self.make()
        first = b.estimate({"provider": "ollama", "model": "small:8b"})
        self.assertFalse(first["measured"])
        self.assertEqual(first["judge_calls_low"], 8 + round(8 * .15))
        self.go(b)
        self.finish(b)
        self.assertTrue(b.estimate({"provider": "ollama", "model": "small:8b"})["measured"])


class Routes(unittest.TestCase):
    def serve_with(self, fake):
        self.seed(answers=False)
        (self.root / models.CONFIG).write_text(json.dumps({"active": {"provider": "ollama", "model": "small:8b", "url": fake.url}}),
                                               encoding="utf-8")
        self.addCleanup(fake.close)
        server = self.serve(tutor=None, token="t" * 40)
        server.app.bench.judge_pen = Judge()
        server.app.bench.suite = tiny_suite()
        server.app.bench.exams = {e["id"]: e for e in server.app.bench.suite["exams"]}
        return server

    def test_the_page_can_see_and_estimate_without_starting_anything(self):
        fake = test_models.FakeModelServer()
        server = self.serve_with(fake)
        status, data = self.get_json(server, "/api/bench")
        self.assertEqual((status, data["runs"], data["active"]), (200, [], None))
        status, est = self.post_json(server, "/api/bench/estimate", {"provider": "ollama", "model": "small:8b"})
        self.assertEqual(status, 200)
        self.assertEqual(est["questions"], 8)
        self.assertEqual(server.app.bench.judge_pen.calls, [])

    def test_start_needs_the_page_and_a_confirmation(self):
        fake = test_models.FakeModelServer()
        server = self.serve_with(fake)
        body = {"provider": "ollama", "model": "small:8b", "url": fake.url}
        self.assertEqual(self.post_json(server, "/api/bench/start", {**body, "confirm": True}, page=False)[0], 403)
        status, err = self.post_json(server, "/api/bench/start", body)
        self.assertEqual((status, err["code"]), (400, "confirm"))
        self.assertEqual(self.call(server, "GET", "/api/bench", auth=False)[0], 401)

    def test_a_run_from_the_page_finishes_through_a_real_http_model(self):
        fake = test_models.FakeModelServer()
        server = self.serve_with(fake)
        original = fake.pen.run

        def with_outline(system, prompt, schema=None, **kw):
            if schema is writer.GOAL_SYLLABUS_SCHEMA:
                return Target().run(system, prompt, schema)
            return original(system, prompt, schema, **kw)
        fake.pen.run = with_outline
        status, out = self.post_json(server, "/api/bench/start", {"provider": "ollama", "model": "small:8b", "url": fake.url, "confirm": True})
        self.assertEqual(status, 200)
        rid = out["runs"][0]["id"]
        end = time.time() + 20
        while server.app.bench._busy() and time.time() < end:
            time.sleep(0.02)
        status, v = self.get_json(server, f"/api/bench/{rid}")
        self.assertEqual((status, v["status"], v["summary"]["judged"]), (200, "done", 8))
        ratings = self.get_json(server, "/api/models")[1]["ratings"]
        self.assertEqual(ratings["ollama:small:8b"]["rating"], "recommended")


for _name, _value in vars(test_web.Web).items():
    if not _name.startswith(("test_", "__")):
        setattr(Routes, _name, _value)


if __name__ == "__main__":
    unittest.main()
