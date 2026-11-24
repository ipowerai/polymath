"""Tests for interview prep, run against the built-in writer. None calls a model."""
import base64
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import polymath as engine  # noqa: E402
import polymath_goals as goals_mod  # noqa: E402
import polymath_interview as iv  # noqa: E402
import polymath_study as study_mod  # noqa: E402
import polymath_web as web  # noqa: E402
import polymath_writer as writer  # noqa: E402

NOW = "2026-11-22T18:00:00Z"
RESUME = "RESUME-SECRET Ada Lovelace, ada@example.com. Led the analytical engine programme for ten years."
DESCRIPTION = "JD-PRIVATE We need a principal architect to own integration design."
NOTES = "NOTES-PRIVATE The recruiter said the team is rebuilding billing."
BIO = "BIO-PRIVATE Grace told me she likes concise answers."


def until(check, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting")


def job(**extra):
    return {"company": "Acme", "role": "Principal Architect", "level": "Principal", "location": "Remote",
            "job_url": "https://example.com/job", "company_url": "https://example.com", "round": "Panel",
            "interview_date": "2026-12-02", "notes": NOTES, "description": DESCRIPTION, "resume": RESUME,
            "interviewers": [{"name": "Grace Hopper", "title": "VP Engineering", "url": "", "round": "Technical", "notes": BIO}], **extra}


def tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    at = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % at
    return bytes(out)


def tiny_docx(paragraphs) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as box:
        box.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


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
        self.parts = []

    def tearDown(self):
        for part in reversed(self.parts):
            part.stop()
        self.env.stop()
        if hasattr(time, "tzset"):
            time.tzset()
        self.tmp.cleanup()

    def goals(self, pen=None):
        st = study_mod.Study(self.store, pen or writer.FakeWriter())
        self.parts.append(st)
        g = goals_mod.Goals(self.store, st)
        self.parts.append(g)
        return g

    @staticmethod
    def settled(g, gid):
        return until(lambda: (lambda v: None if v.get("busy") else v)(g.view(gid)))

    def prepared(self, pen=None, **extra):
        g = self.goals(pen)
        gid = g.interviews.create(job(**extra))["id"]
        return g, gid, self.settled(g, gid)

    @staticmethod
    def checked(g, gid, qid):
        def done():
            v = g.view(gid)
            q = next(x for x in v["prep"]["questions"] if x["id"] == qid)
            return v if not q.get("checking") else None
        return until(done)

    @staticmethod
    def q(view, qid):
        return next(x for x in view["prep"]["questions"] if x["id"] == qid)


class JobDetails(unittest.TestCase):
    def test_says_what_is_missing(self):
        with self.assertRaisesRegex(study_mod.StudyError, "the company, the job description"):
            iv.clean_job(job(company=" ", description=""))

    def test_a_bad_link_is_refused(self):
        for bad in ({"job_url": "javascript:alert(1)"}, {"company_url": "ftp://x"}):
            with self.assertRaises(study_mod.StudyError):
                iv.clean_job(job(**bad))
        with self.assertRaises(study_mod.StudyError):
            iv.clean_job(job(interviewers=[{"name": "A", "url": "javascript:alert(1)"}]))

    def test_a_bad_date_is_refused(self):
        with self.assertRaises(study_mod.StudyError):
            iv.clean_job(job(interview_date="soon"))

    def test_blank_interviewer_rows_are_ignored(self):
        self.assertEqual(iv.clean_job(job(interviewers=[{"name": " "}, {"name": "Grace"}]))["interviewers"][0]["name"], "Grace")

    def test_interviewers_are_optional(self):
        self.assertEqual(iv.clean_job(job(interviewers=[]))["interviewers"], [])

    def test_too_many_or_duplicate_interviewers_are_refused(self):
        with self.assertRaises(study_mod.StudyError):
            iv.clean_job(job(interviewers=[{"name": f"P{n}"} for n in range(iv.MAX_INTERVIEWERS + 1)]))
        with self.assertRaises(study_mod.StudyError):
            iv.clean_job(job(interviewers=[{"name": "Grace"}, {"name": "grace"}]))


class Upload(unittest.TestCase):
    LINE = "Ada Lovelace, Principal Architect, led the analytical engine programme"

    def test_text_and_markdown(self):
        for name in ("r.txt", "r.md"):
            self.assertIn("analytical", iv.extract_text(name, self.LINE.encode()))
        self.assertIn("Lovelace", iv.extract_text("r.txt", self.LINE.encode("utf-16")))

    def test_word(self):
        out = iv.extract_text("r.docx", tiny_docx([self.LINE, "Skills &amp; tools"]))
        self.assertEqual(out.splitlines(), [self.LINE, "Skills & tools"])

    @unittest.skipUnless(shutil.which("pdftotext") or sys.platform == "darwin", "no PDF reader here")
    def test_pdf(self):
        self.assertIn("analytical engine", iv.extract_text("r.pdf", tiny_pdf(self.LINE)))

    @unittest.skipUnless(sys.platform == "darwin", "the built-in PDF reader is macOS's")
    def test_pdf_with_only_the_built_in_mac_reader(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIn("analytical engine", iv.extract_text("r.pdf", tiny_pdf(self.LINE)))

    def test_a_file_pretending_to_be_a_pdf_is_refused(self):
        with self.assertRaises(study_mod.StudyError):
            iv.extract_text("r.pdf", b"this is not a pdf at all")

    def test_a_broken_word_file_is_refused(self):
        with self.assertRaises(study_mod.StudyError):
            iv.extract_text("r.docx", b"not a zip")

    def test_a_zip_bomb_is_refused_without_unpacking(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as box:
            box.writestr("word/document.xml", b"0" * (iv.MAX_UNPACKED + 1))
        with self.assertRaisesRegex(study_mod.StudyError, "too large"):
            iv.extract_text("r.docx", buf.getvalue())

    def test_other_types_empty_and_oversized_are_refused(self):
        for name, raw in (("r.exe", b"MZ" + b"x" * 80), ("r.txt", b""), ("r.txt", b"hi"), ("r.txt", b"x" * (iv.MAX_FILE + 1))):
            with self.assertRaises(study_mod.StudyError):
                iv.extract_text(name, raw)

    def test_bad_base64_is_refused(self):
        with self.assertRaises(study_mod.StudyError):
            iv.decode_upload("not base64 !!")


class Privacy(Base):
    def test_web_calls_get_only_the_public_subject(self):
        pen = writer.FakeWriter()
        self.prepared(pen)
        web_calls = [c for c in pen.calls if c["web"]]
        self.assertEqual(len(web_calls), 2)
        for call in web_calls:
            for private in ("RESUME-SECRET", "JD-PRIVATE", "NOTES-PRIVATE", "BIO-PRIVATE", "Lovelace", "ada@example.com"):
                self.assertNotIn(private, call["prompt"])
        self.assertIn("Grace Hopper", next(c["prompt"] for c in web_calls if c["schema"] is writer.INTERVIEW_PEOPLE_SCHEMA))

    def test_the_calls_that_see_the_resume_have_no_web(self):
        pen = writer.FakeWriter()
        g, gid, v = self.prepared(pen)
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, "A full and honest answer about the work I led there.")
        self.checked(g, gid, qid)
        seeing = [c for c in pen.calls if "RESUME-SECRET" in c["prompt"]]
        self.assertEqual({c["schema"] is writer.INTERVIEW_PREP_SCHEMA or c["schema"] is writer.INTERVIEW_FEEDBACK_SCHEMA for c in seeing}, {True})
        self.assertFalse(any(c["web"] for c in seeing))

    def test_the_dashboard_state_never_carries_the_resume(self):
        g, gid, _ = self.prepared()
        state = json.dumps(web.collect_state(self.store), default=str)
        self.assertIn("Principal Architect at Acme", state)
        for private in ("RESUME-SECRET", "JD-PRIVATE", "NOTES-PRIVATE", "BIO-PRIVATE"):
            self.assertNotIn(private, state)

    def test_goal_files_are_private(self):
        g, gid, _ = self.prepared()
        self.assertEqual((self.root / "goals" / f"{gid}.json").stat().st_mode & 0o077, 0)
        self.assertEqual((self.root / "goals").stat().st_mode & 0o077, 0)


class Interviewers(Base):
    def test_someone_not_confirmed_leaves_nothing_behind(self):
        out = iv.clean_person({"match": "not_found", "current_role": "CEO", "background": ["x"], "sources": [{"title": "t", "url": "https://e.com"}]}, {"name": "A"})
        self.assertEqual((out["current_role"], out["background"], out["sources"]), ("", [], []))

    def test_a_match_with_no_source_does_not_count(self):
        self.assertEqual(iv.clean_person({"match": "confirmed", "current_role": "CEO", "sources": []}, {"name": "A"})["match"], "not_found")
        kept = iv.clean_person({"match": "likely", "current_role": "CTO", "sources": []}, {"name": "A", "url": "https://e.com/a"})
        self.assertEqual((kept["match"], kept["current_role"]), ("likely", "CTO"))

    def test_an_unconfirmed_person_is_not_described_to_the_question_writer(self):
        pen = writer.FakeWriter()
        g, gid, v = self.prepared(pen, interviewers=[{"name": "Unknown Person", "title": "", "url": "", "round": "", "notes": ""}])
        key = iv.person_key(v["job"]["interviewers"][0], v["job"])
        self.assertEqual(v["people"][key]["match"], "not_found")
        self.assertFalse(any(q["interviewer"] for q in v["prep"]["questions"]))
        prep = next(c for c in pen.calls if c["schema"] is writer.INTERVIEW_PREP_SCHEMA)
        self.assertNotIn("VP Engineering", prep["prompt"])

    def test_a_question_cannot_be_tied_to_someone_not_on_the_list(self):
        data = writer._fake_prep({"interviewers": []})
        data["questions"][0]["interviewer"] = "Somebody Else"
        prep, _ = iv.clean_prep(data, {"Grace Hopper"})
        self.assertFalse(any(q["interviewer"] for q in prep["questions"]))

    def test_the_prompt_forbids_personal_details(self):
        for word in ("family", "home", "health", "politics", "data-broker", "not_found"):
            self.assertIn(word, iv.PEOPLE_SYSTEM)


class Prep(Base):
    def test_from_details_to_questions(self):
        g, gid, v = self.prepared()
        self.assertEqual((v["kind"], v["stage"], v["status"]), ("interview", "practice", "active"))
        self.assertGreaterEqual(len(v["prep"]["questions"]), iv.MIN_QUESTIONS)
        self.assertTrue(v["company"]["summary"])
        self.assertTrue(v["prep"]["gaps"])
        self.assertTrue(v["prep"]["ask_them"])
        self.assertEqual(v["days_left"], 10)
        self.assertEqual(v["title"], "Principal Architect at Acme")

    def test_most_likely_questions_come_first(self):
        _, _, v = self.prepared()
        order = [iv.WEIGHT[q["likelihood"]] for q in v["prep"]["questions"]]
        self.assertEqual(order, sorted(order, reverse=True))

    def test_too_few_questions_is_retried_then_an_error(self):
        class Few(writer.FakeWriter):
            def run(self, system, prompt, schema=None, **kw):
                out = super().run(system, prompt, schema, **kw)
                if schema is writer.INTERVIEW_PREP_SCHEMA:
                    out["questions"] = out["questions"][:2]
                return out
        pen = Few()
        _, _, v = self.prepared(pen)
        self.assertIn("usable", v["error"]["text"])
        self.assertEqual(sum(1 for c in pen.calls if c["schema"] is writer.INTERVIEW_PREP_SCHEMA), 2)

    def test_failed_research_still_gives_questions_and_says_so(self):
        class NoWeb(writer.FakeWriter):
            def run(self, system, prompt, schema=None, **kw):
                if kw.get("web"):
                    raise writer.WriterError("Search failed.", "failed")
                return super().run(system, prompt, schema, **kw)
        _, _, v = self.prepared(NoWeb())
        self.assertTrue(v["prep"]["questions"])
        self.assertEqual(len(v["warnings"]), 2)

    def test_a_used_up_limit_stops_rather_than_carrying_on(self):
        class Limit(writer.FakeWriter):
            def run(self, system, prompt, schema=None, **kw):
                if kw.get("web"):
                    raise writer.WriterError("Your Claude usage limit was reached.", "usage_limit")
                return super().run(system, prompt, schema, **kw)
        _, _, v = self.prepared(Limit())
        self.assertEqual(v["error"]["code"], "usage_limit")
        self.assertIsNone(v["prep"])

    def test_retry_researches_only_what_is_missing(self):
        class Flaky(writer.FakeWriter):
            broken = True

            def run(self, system, prompt, schema=None, **kw):
                if schema is writer.INTERVIEW_PEOPLE_SCHEMA and self.broken:
                    raise writer.WriterError("Search failed.", "failed")
                return super().run(system, prompt, schema, **kw)
        pen = Flaky()
        g, gid, v = self.prepared(pen)
        self.assertEqual(len(v["warnings"]), 1)
        pen.broken = False
        g.retry(gid)
        v = self.settled(g, gid)
        self.assertEqual(v["warnings"], [])
        self.assertEqual(sum(1 for c in pen.calls if c["schema"] is writer.INTERVIEW_COMPANY_SCHEMA), 1)

    def test_changing_details_researches_only_what_changed(self):
        pen = writer.FakeWriter()
        g, gid, v = self.prepared(pen)
        people = v["job"]["interviewers"] + [{"name": "Alan Turing", "title": "CTO", "url": "", "round": "", "notes": ""}]
        g.interviews.update(gid, job(interviewers=people))
        v = self.settled(g, gid)
        self.assertEqual(sum(1 for c in pen.calls if c["schema"] is writer.INTERVIEW_COMPANY_SCHEMA), 1)
        last = [c for c in pen.calls if c["schema"] is writer.INTERVIEW_PEOPLE_SCHEMA][-1]
        self.assertIn("Alan Turing", last["prompt"])
        self.assertNotIn("Grace Hopper", last["prompt"])
        self.assertEqual(len(v["people"]), 2)

    def test_a_new_company_is_researched_again(self):
        pen = writer.FakeWriter()
        g, gid, _ = self.prepared(pen)
        g.interviews.update(gid, job(company="Globex"))
        v = self.settled(g, gid)
        self.assertEqual(sum(1 for c in pen.calls if c["schema"] is writer.INTERVIEW_COMPANY_SCHEMA), 2)
        self.assertIn("Globex", v["company"]["summary"])

    def test_a_restart_during_research_can_be_picked_up(self):
        g, gid, _ = self.prepared()
        qid = g.goals[gid]["prep"]["questions"][0]["id"]
        g.goals[gid]["busy"] = {"stage": "planning", "label": "x", "since": NOW}
        self.q({"prep": g.goals[gid]["prep"]}, qid)["checking"] = NOW
        g._save(g.goals[gid])
        again = goals_mod.Goals(self.store, g.study)
        self.parts.append(again)
        v = again.view(gid)
        self.assertEqual((v["busy"], v["error"]["code"]), (None, "interrupted"))
        self.assertFalse(self.q(v, qid).get("checking"))
        self.assertTrue(self.q(v, qid)["feedback_error"])

    def test_several_interviews_sit_with_other_goals(self):
        g = self.goals()
        one = g.interviews.create(job())["id"]
        two = g.interviews.create(job(company="Globex"))["id"]
        self.settled(g, one), self.settled(g, two)
        self.assertEqual({x["id"] for x in g.list()}, {one, two})
        self.assertEqual({x["kind"] for x in g.list()}, {"interview"})

    def test_the_last_resume_is_offered_for_the_next_one(self):
        g, _, _ = self.prepared()
        self.assertEqual(g.interviews.defaults()["resume"], RESUME)

    def test_a_chat_message_does_not_reach_an_interview(self):
        g, gid, _ = self.prepared()
        with self.assertRaises(study_mod.StudyError):
            g.say(gid, "hello")


class Practice(Base):
    GOOD = "I led the analytical engine programme, set the architecture, and shipped it to every customer in the region."

    def test_the_model_answer_stays_back_until_you_try(self):
        g, gid, v = self.prepared()
        q = v["prep"]["questions"][0]
        for hidden in ("covers", "your_material", "watch_out", "follow_ups"):
            self.assertNotIn(hidden, q)
        self.assertTrue(q["why"])
        g.interviews.practice(gid, q["id"], self.GOOD)
        self.assertIn("covers", self.q(self.checked(g, gid, q["id"]), q["id"]))

    def test_you_can_ask_to_see_it(self):
        g, gid, v = self.prepared()
        qid = v["prep"]["questions"][0]["id"]
        v = g.interviews.reveal(gid, qid)
        self.assertIn("covers", self.q(v, qid))
        self.assertEqual(self.q(v, qid)["status"], "new")      # reading it isn't practising it

    def test_a_good_answer_makes_it_ready_and_moves_progress(self):
        g, gid, v = self.prepared()
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, self.GOOD)
        v = self.checked(g, gid, qid)
        q = self.q(v, qid)
        self.assertEqual((q["best"], q["status"], v["done_count"]), (4, "ready", 1))
        self.assertGreater(v["progress"], 0)
        self.assertNotEqual(v["next"]["id"], qid)

    def test_a_weak_answer_counts_for_less_and_stays_open(self):
        g, gid, v = self.prepared()
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, "I did some work on that.")
        v = self.checked(g, gid, qid)
        self.assertEqual(self.q(v, qid)["status"], "practiced")
        self.assertEqual(v["done_count"], 0)

    def test_an_empty_answer_is_refused(self):
        g, gid, v = self.prepared()
        with self.assertRaises(study_mod.StudyError):
            g.interviews.practice(gid, v["prep"]["questions"][0]["id"], "yes")

    def test_a_follow_up_is_recorded_but_does_not_make_the_question_ready(self):
        g, gid, v = self.prepared()
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, "I did some work on that.")
        self.checked(g, gid, qid)
        g.interviews.practice(gid, qid, self.GOOD, followup=True)
        v = self.checked(g, gid, qid)
        q = self.q(v, qid)
        self.assertEqual((len(q["attempts"]), q["attempts"][-1]["followup"], q["status"]), (2, True, "practiced"))
        self.assertEqual(q["attempts"][-1]["question"], "What would you do differently?")

    def test_no_follow_up_before_a_first_answer(self):
        g, gid, v = self.prepared()
        with self.assertRaises(study_mod.StudyError):
            g.interviews.practice(gid, v["prep"]["questions"][0]["id"], self.GOOD, followup=True)

    def test_a_failed_check_says_so(self):
        class Fails(writer.FakeWriter):
            def run(self, system, prompt, schema=None, **kw):
                if schema is writer.INTERVIEW_FEEDBACK_SCHEMA:
                    raise writer.WriterError("Claude is busy.", "failed")
                return super().run(system, prompt, schema, **kw)
        g, gid, v = self.prepared(Fails())
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, self.GOOD)
        q = self.q(self.checked(g, gid, qid), qid)
        self.assertEqual((q["feedback_error"], q["attempts"]), ("Claude is busy.", []))

    def test_practice_survives_updated_details(self):
        g, gid, v = self.prepared()
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, self.GOOD)
        self.checked(g, gid, qid)
        g.interviews.update(gid, job(round="Final"))
        v = self.settled(g, gid)
        self.assertEqual(len(self.q(v, qid)["attempts"]), 1)

    def test_practice_on_a_dropped_question_is_kept(self):
        class Different(writer.FakeWriter):
            changed = False

            def run(self, system, prompt, schema=None, **kw):
                out = super().run(system, prompt, schema, **kw)
                if schema is writer.INTERVIEW_PREP_SCHEMA and self.changed:
                    for q in out["questions"]:
                        q["question"] = "New: " + q["question"]
                return out
        pen = Different()
        g, gid, v = self.prepared(pen)
        qid = v["prep"]["questions"][0]["id"]
        g.interviews.practice(gid, qid, self.GOOD)
        self.checked(g, gid, qid)
        pen.changed = True
        g.interviews.update(gid, job(round="Final"))
        v = self.settled(g, gid)
        self.assertEqual(len(self.q(v, qid)["attempts"]), 1)

    def test_scores_are_kept_in_range(self):
        self.assertEqual(iv.clean_feedback({"score": 99})["score"], 5)
        self.assertEqual(iv.clean_feedback({"score": "bad"})["score"], 1)

    def test_the_coach_is_told_not_to_invent_experience(self):
        self.assertIn("Never invent", iv.FEEDBACK_SYSTEM)
        self.assertIn("Never invent", iv.PREP_SYSTEM)


class Server(Base):
    def test_a_large_upload_needs_a_sign_in_before_it_is_read(self):
        server, _, _ = web.build_server(self.store, host="127.0.0.1", port=0, token="a" * 32, tutor="fake")
        self.addCleanup(server.server_close)
        self.parts.append(server.app.study)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.putrequest("POST", "/api/goals/extract")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(web.MAX_UPLOAD))
        conn.endheaders()                       # no body sent: it must answer without waiting for one
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()

    def test_only_the_upload_route_may_be_large(self):
        self.assertGreater(web.MAX_UPLOAD, base64.b64encode(b"x" * iv.MAX_FILE).__len__())
        self.assertEqual(web.MAX_BODY, 64 * 1024)


if __name__ == "__main__":
    unittest.main()
