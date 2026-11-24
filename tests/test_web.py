"""Tests for the web dashboard (scripts/polymath_web.py). Run: python3 -m unittest discover tests"""
import http.client
import io
import json
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import webbrowser
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import polymath as engine  # noqa: E402
import polymath_web as web  # noqa: E402
import polymath_writer  # noqa: E402

DAY1, DAY2, DAY3 = "2026-11-15T18:00:00Z", "2026-11-16T18:00:00Z", "2026-11-17T18:00:00Z"
STATUS = re.compile(rb"HTTP/1\.1 (\d{3}) ")  # every answer's status line (a body may run straight into the next)


def syllabus():
    return {
        "slug": "demo", "title": "Demo <b>track</b>", "short": "DEMO", "goal": "Pass the demo exam",
        "kind": "certification", "exam": {"passing_score": 0.7, "questions": 10, "minutes": 20},
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


class Web(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.env = mock.patch.dict(os.environ, {"POLYMATH_NOW": DAY1, "TZ": "UTC"})
        self.env.start()
        if hasattr(time, "tzset"):
            time.tzset()
        self.store = engine.Store(self.root)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            if server.app.study:
                server.app.study.stop()
            server.server_close()
        self.env.stop()
        if hasattr(time, "tzset"):
            time.tzset()
        self.tmp.cleanup()

    # --- helpers --------------------------------------------------------------------------
    def at(self, ts):
        os.environ["POLYMATH_NOW"] = ts

    def record(self, cid, result, **extra):
        item = {"concept": cid, "result": result, "qtype": "mcq", "difficulty": 3, "track": "demo", **extra}
        return engine.record_events(self.store, [engine.build_attempt(item, self.store.concepts())])[0]

    def seed(self, answers=True):
        with redirect_stdout(io.StringIO()):
            engine.cmd_init(Namespace(name="Ada", persona=None, json=True), self.store)
        engine.import_track(self.store, syllabus(), target_date="2026-12-01")
        if answers:
            self.record("demo.basics.a", "correct", confidence=4)
            self.record("demo.basics.b", "incorrect", confidence=5, misconception="b means a")
            self.at(DAY2)
            self.record("demo.basics.b", "correct", confidence=4, targets="M1")
            self.at(DAY3)
            self.record("demo.advanced.c", "correct", confidence=3)

    def serve(self, **options):
        options.setdefault("tutor", "off")  # never start the real Claude Code from a test
        options.setdefault("port", 0)
        server, link, lines = web.build_server(self.store, **options)
        server.link, server.lines = link, lines  # what `serve` would print and open
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.servers.append(server)
        return server

    def stop(self, server):
        """Stop a dashboard the way Ctrl+C does, freeing its port."""
        self.servers.remove(server)
        server.shutdown()
        server.server_close()

    def files(self) -> set:
        """Every file in the data folder, by path."""
        return {str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()}

    def raw(self, server, data: bytes, wait: float = 3.0) -> tuple:
        """Send bytes exactly as given on one connection. Returns (everything that came back, whether
        the server closed the connection within `wait` seconds)."""
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=wait) as conn:
            conn.sendall(data)
            out = b""
            try:
                while chunk := conn.recv(65536):
                    out += chunk
            except (TimeoutError, socket.timeout):
                return out, False
            except ConnectionResetError:
                pass
            return out, True

    def squatter(self, answer) -> tuple:
        """Another program holding a port on this machine: it answers every GET with the JSON answer(path)
        gives and remembers every path it was sent. Returns (its port, those paths)."""
        seen = []

        class Squat(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                body = json.dumps(answer(self.path)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        squat = HTTPServer(("127.0.0.1", 0), Squat)
        threading.Thread(target=squat.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(squat.server_close)
        self.addCleanup(squat.shutdown)
        return squat.server_address[1], seen

    def tls_files(self) -> tuple:
        """A throwaway self-signed certificate and key, made with the openssl command."""
        openssl = shutil.which("openssl")
        if not openssl:
            self.skipTest("openssl is not installed")
        folder = Path(self.tmp.name) / "tls"
        folder.mkdir()
        cert, key = folder / "cert.pem", folder / "key.pem"
        proc = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                               "-out", str(cert), "-days", "2", "-subj", "/CN=127.0.0.1"],
                              capture_output=True, text=True)
        if proc.returncode or not cert.exists():
            self.skipTest(f"openssl couldn't make a certificate: {proc.stderr.strip()[:200]}")
        return str(cert), str(key)

    def call(self, server, method, path, body=None, headers=None, auth=True):
        """One request. With auth, it carries the dashboard's token the way a script would (the page
        uses the cookie the sign-in link sets)."""
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        sent = {"Host": f"127.0.0.1:{port}"}
        if auth and server.app.token:
            sent["Authorization"] = f"Bearer {server.app.token}"
        sent.update(headers or {})
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body)
            sent.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=sent)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, resp.headers, payload

    def get_json(self, server, path, **kw):
        status, _, payload = self.call(server, "GET", path, **kw)
        return status, json.loads(payload or b"null")

    def post_json(self, server, path, body, page=True, headers=None, auth=True):
        sent = {"X-Polymath": "1"} if page else {}
        sent.update(headers or {})
        status, _, payload = self.call(server, "POST", path, body=body, headers=sent, auth=auth)
        return status, json.loads(payload or b"null")

    @staticmethod
    def browser(headers) -> dict:
        """What a browser that a sign-in answered with these headers sends with an API call: the cookie it was
        given, and the page key its page took from the address it was sent to (/#k=...)."""
        key = urllib.parse.urlsplit(headers["Location"]).fragment.partition("k=")[2]
        return {"Cookie": headers["Set-Cookie"].split(";")[0], "X-Polymath-Key": key}

    # --- the numbers ----------------------------------------------------------------------
    def test_state_matches_the_engine(self):
        self.seed()
        state = web.collect_state(self.store)
        rep = engine.Model(self.store).track_report(self.store.track("demo"))
        track = state["tracks"][0]
        self.assertEqual(track["predicted_score"], rep["predicted_score"])
        self.assertEqual([p["date"] for p in track["history"]], ["2026-11-15", "2026-11-16", "2026-11-17"])
        self.assertEqual(track["history"][-1]["predicted"], rep["predicted_score"])  # replay ends where live is
        self.assertEqual([p["answers"] for p in track["history"]], [2, 1, 1])
        self.assertEqual(track["start"]["date"], "2026-11-15")
        self.assertLess(track["start"]["predicted"], track["history"][-1]["predicted"])
        self.assertEqual(track["short"], "DEMO")
        self.assertEqual(state["mistakes"][0]["belief"], "b means a")
        self.assertEqual(state["learner"]["calibration_levels"][4]["n"], 1)

    def test_history_shows_memory_fading_on_days_off(self):
        self.seed(answers=False)
        self.record("demo.basics.a", "correct", confidence=5)
        self.at("2026-11-25T18:00:00Z")  # ten days without study
        points = web.collect_state(self.store)["tracks"][0]["history"]
        self.assertEqual(len(points), 11)
        self.assertGreater(points[0]["predicted"], points[-1]["predicted"])
        self.assertEqual(sum(p["answers"] for p in points), 1)

    def test_trend_direction_and_a_projection_that_levels_off(self):
        today = date(2026, 11, 17)
        rising = [{"date": f"2026-11-{d:02d}", "predicted": 0.40 + 0.04 * i, "mastered": i, "coverage": 0.1 * i,
                   "answers": 10, "points": 7.0} for i, d in enumerate(range(10, 18))]
        rep = {"passing_score": 0.68, "target_date": "2026-12-08", "projection": {"days_left": 21}}
        tr = web.trend(rising, rep, {}, today, start={"date": "2026-11-10", "predicted": 0.32})
        self.assertTrue(tr["enough"])
        self.assertEqual(tr["direction"], "up")
        self.assertAlmostEqual(tr["predicted_delta"], 0.28, places=3)
        values = [p["predicted"] for p in tr["projection"]]
        self.assertEqual(values, sorted(values))                    # keeps rising...
        self.assertLessEqual(max(values), 1 - engine.EXAM_SLIP)     # ...but never past the model's ceiling
        self.assertEqual(tr["outlook"], "on track")
        self.assertGreaterEqual(tr["goal_progress"], tr["time_used"])
        self.assertEqual((tr["answers_7d"], tr["accuracy_7d"]), (70, 0.7))

        falling = [dict(p, predicted=0.70 - 0.02 * i) for i, p in enumerate(rising)]
        tr = web.trend(falling, rep, {}, today)
        self.assertEqual(tr["direction"], "down")
        self.assertIn(tr["outlook"], ("at risk", "off track"))
        self.assertNotIn("goal_progress", tr)                       # no first answer, no climb to show

        self.assertFalse(web.trend(rising[-1:], rep, {}, today)["enough"])
        idle = [dict(p, answers=0, points=0.0) for p in rising]
        self.assertFalse(web.trend(idle, rep, {}, today)["enough"])  # no study days, no direction

    def test_booking_gate(self):
        rep = {"passing_score": 0.68, "predicted_score": 0.80, "coverage": 0.95,
               "full_mocks": [{"score": 0.70}, {"score": 0.80}],
               "mocks": [{"score": 0.90}, {"score": 0.95}]}           # short mocks don't count toward booking
        self.assertEqual([c["met"] for c in web.booking_gate(rep)["checks"]], [True, True, False])
        rep["full_mocks"].append({"score": 0.75})                     # the last two full mocks: 80% and 75%
        self.assertTrue(web.booking_gate(rep)["met"])
        self.assertTrue(web.booking_gate(dict(rep, predicted_score=0.75))["checks"][0]["met"])  # 0.68 + 0.07
        self.assertFalse(web.booking_gate(dict(rep, coverage=0.89))["met"])
        self.assertIsNone(web.booking_gate({"passing_score": None, "predicted_score": 0.5}))

    # --- study through the API ------------------------------------------------------------
    def wait_ready(self, server, sid):
        end = time.time() + 8
        while time.time() < end:
            _, v = self.get_json(server, f"/api/study/{sid}")
            if v["questions"] and all(q["state"] == "ready" for q in v["questions"]):
                return v
            time.sleep(0.03)
        raise AssertionError("questions were not written")

    def test_study_through_the_api(self):
        self.seed(answers=False)
        server = self.serve(tutor="fake")
        status, info = self.get_json(server, "/api/study")
        self.assertEqual((status, info["available"], info["reason"]), (200, True, None))
        self.assertTrue(json.loads(self.call(server, "GET", "/api/state")[2])["app"]["study"]["available"])
        status, v = self.post_json(server, "/api/study/start", {"mode": "learn", "track": "demo"})
        self.assertEqual(status, 200, v)
        sid = v["id"]
        v = self.wait_ready(server, sid)
        answers = [{"qid": q["id"], "choice": [o["key"] for o in q["question"]["options"]][:q["question"]["select"]],
                    "confidence": 3} for q in v["questions"]]
        status, v = self.post_json(server, f"/api/study/{sid}/answer", {"answers": answers, "block": 1})
        self.assertEqual(status, 200, v)
        self.assertTrue(all("result" in q for q in v["questions"]))
        self.assertIn("data_version", v)
        status, err = self.post_json(server, "/api/study/start", {"mode": "nope", "track": "demo"})
        self.assertEqual((status, err["code"]), (400, "bad_request"))
        status, err = self.post_json(server, f"/api/study/{sid}/answer", {"answers": [{"qid": "q99"}]})
        self.assertEqual((status, err["code"]), (400, "stale"))
        status, err = self.post_json(server, f"/api/study/{sid}/dispute", {"qid": v["questions"][0]["id"],
                                                                           "which": "both"})
        self.assertEqual((status, err["code"]), (400, "bad_request"))
        self.assertEqual(self.get_json(server, "/api/study/zzzz")[0], 400)
        self.assertEqual(self.post_json(server, f"/api/study/{sid}/explode", {})[0], 404)
        status, v = self.post_json(server, f"/api/study/{sid}/end", {"skip_close": True})
        self.assertEqual((status, v["status"]), (200, "done"))
        self.assertEqual(self.get_json(server, "/api/coach")[1]["messages"], [])

    def test_preferences_through_the_api(self):
        self.seed(answers=False)
        server = self.serve()
        headers = {"X-Polymath": "1", "Content-Type": "application/json"}
        for raw in ('{"block_size": NaN}', '{"block_size": Infinity}', '{"block_size": -Infinity}'):
            self.assertEqual(self.call(server, "POST", "/api/preferences", body=raw, headers=headers)[0], 400, raw)
        status, out = self.post_json(server, "/api/preferences", {"block_size": 2, "start_level": "Expert"})
        self.assertEqual((status, out["preferences"]["block_size"], out["preferences"]["start_level"]),
                         (200, 2, "expert"))
        for bad in ({"block_size": 9}, {"block_size": True}, {"web_daily_usd": "lots"}, {"name": "Eve"}, {}):
            self.assertEqual(self.post_json(server, "/api/preferences", bad)[0], 400, bad)
        self.assertEqual(self.store.learner()["preferences"]["block_size"], 2)

    def test_study_is_off_without_a_writer_or_when_read_only(self):
        self.seed(answers=False)
        server = self.serve()
        info = self.get_json(server, "/api/study")[1]
        self.assertEqual((info["available"], info["reason"]), (False, "no-writer"))
        status, err = self.post_json(server, "/api/study/start", {"mode": "learn", "track": "demo"})
        self.assertEqual((status, err["code"]), (400, "writer_missing"))
        locked = self.serve(tutor="fake", read_only=True)
        self.assertEqual(self.get_json(locked, "/api/study")[1]["reason"], "read-only")
        self.assertEqual(self.post_json(locked, "/api/study/start", {"mode": "learn", "track": "demo"})[0], 403)

    def test_verbose_logs_never_show_a_query_string(self):
        self.seed(answers=False)
        server = self.serve(verbose=True)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.call(server, "GET", "/api/ping?token=secret-value")
        self.assertIn("/api/ping?", err.getvalue())
        self.assertNotIn("secret-value", err.getvalue())

    # --- the server ------------------------------------------------------------------------
    def test_serves_the_page_with_security_headers(self):
        self.seed()
        server = self.serve()
        status, headers, body = self.call(server, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'<script src="app.js" defer>', body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, headers, _ = self.call(server, "GET", "/app.js")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.call(server, "GET", "/app.js", headers={"If-None-Match": headers["ETag"]})[0], 304)
        for path in ("/../scripts/polymath.py", "/%2e%2e/scripts/polymath.py", "/nope", "/api/nope"):
            self.assertEqual(self.call(server, "GET", path)[0], 404, path)

    def test_localhost_answers_only_to_local_host_names(self):
        self.seed()
        server = self.serve(read_only=True)                          # open on this machine: no token
        self.assertIsNone(server.app.token)
        self.assertEqual(self.call(server, "GET", "/api/state")[0], 200)
        self.assertEqual(self.call(server, "GET", "/api/state", headers={"Host": "localhost"})[0], 200)
        self.assertEqual(self.call(server, "GET", "/api/state", headers={"Host": "evil.example:8765"})[0], 403)
        proxied = self.serve(read_only=True, allow_hosts=["box.example.ts.net"])
        self.assertEqual(self.call(proxied, "GET", "/api/state", headers={"Host": "box.example.ts.net"})[0], 200)

    def test_this_machine_needs_the_token_once_the_dashboard_can_change_things(self):
        self.seed(answers=False)
        server = self.serve()
        self.assertTrue(server.app.token)
        self.assertEqual(server.app.token, (self.root / "web-token").read_text().strip())
        self.assertEqual(self.call(server, "GET", "/api/state", auth=False)[0], 401)
        self.assertEqual(self.post_json(server, "/api/preferences", {"block_size": 2}, auth=False)[0], 401)
        self.assertEqual(self.post_json(server, "/api/study/start", {"mode": "learn", "track": "demo"},
                                        auth=False)[0], 401)
        self.assertEqual(self.call(server, "GET", "/api/state")[0], 200)
        fake = polymath_writer.FakeWriter()
        self.assertFalse(web.needs_token("127.0.0.1", False, True, fake))      # the demo with the practice writer
        self.assertFalse(web.needs_token("::1", True, False, None))            # read-only
        self.assertTrue(web.needs_token("127.0.0.1", False, True, object()))   # the demo with a real writer
        self.assertTrue(web.needs_token("localhost", False, False, fake))      # your own data
        self.assertTrue(web.needs_token("0.0.0.0", True, True, None))          # beyond this machine

    def test_a_token_guards_everything_but_the_sign_in_page(self):
        self.seed()
        server = self.serve(token="s3cret-token-value")
        call = lambda *a, **kw: self.call(server, *a, auth=False, **kw)  # noqa: E731
        self.assertEqual(call("GET", "/api/state")[0], 401)
        status, headers, _ = call("GET", "/")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertEqual(call("GET", "/login")[0], 200)
        self.assertEqual(call("GET", "/api/ping")[0], 200)
        self.assertEqual(call("GET", "/?token=wrong")[0], 401)
        status, headers, _ = call("GET", "/?token=s3cret-token-value")
        self.assertEqual(status, 303)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertTrue(cookie.startswith(f"polymath_session_{server.server_address[1]}="))
        self.assertNotIn("s3cret-token-value", cookie)              # a random sign-in id, never the token
        signed = self.browser(headers)
        self.assertRegex(headers["Location"], r"^/#k=[0-9a-f]{64}$")  # the page key, in a fragment no server sees
        self.assertEqual(call("GET", "/api/state", headers=signed)[0], 200)
        self.assertEqual(call("GET", "/api/state", headers={"Cookie": cookie})[0], 401)  # the cookie alone: no
        self.assertEqual(call("GET", "/api/state", headers={**signed, "X-Polymath-Key": "0" * 64})[0], 401)
        self.assertEqual(call("GET", "/", headers={"Cookie": cookie})[0], 200)   # the page itself holds no data
        self.assertEqual(call("GET", "/login", headers=signed)[0], 200)  # always the form: never a loop to /
        bearer = {"Authorization": "Bearer s3cret-token-value"}
        self.assertEqual(call("GET", "/api/state", headers=bearer)[0], 200)
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(call("POST", "/login", body="token=s3cret-token-value", headers=form)[0], 303)
        self.assertEqual(call("POST", "/login", body="token=nope", headers=form)[0], 401)
        # with a token, a reverse proxy's host name is fine: the token is what lets you in
        self.assertEqual(call("GET", "/api/state", headers={**signed, "Host": "box.example"})[0], 200)

    def test_tokens_are_saved_privately_and_reused(self):
        self.seed(answers=False)
        self.assertTrue(web.is_loopback("127.0.0.1") and web.is_loopback("::1") and web.is_loopback("localhost"))
        self.assertFalse(web.is_loopback("0.0.0.0") or web.is_loopback("192.168.1.20"))
        token = web.load_token(self.store)
        path = self.root / "web-token"
        self.assertGreaterEqual(len(token), 20)
        self.assertEqual(web.load_token(self.store), token)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn("web-token", (self.root / ".gitignore").read_text())
        server, url, _ = web.build_server(self.store, port=0, rotate_token=True, tutor="off")
        try:
            self.assertNotEqual(server.app.token, token)
            self.assertIn(f"?token={server.app.token}", url)
            self.assertEqual(path.read_text().strip(), server.app.token)
        finally:
            server.server_close()

    def test_changes_come_only_from_the_page_and_respect_read_only(self):
        self.seed()
        server = self.serve()
        path = "/api/tracks/demo/settings"
        self.assertEqual(self.post_json(server, path, {"status": "paused"}, page=False)[0], 403)
        evil = {"Origin": "https://evil.example"}
        self.assertEqual(self.post_json(server, path, {"status": "paused"}, headers=evil)[0], 403)
        status, out = self.post_json(server, path, {"status": "paused", "target_date": "2026-12-15", "daily_minutes": 90})
        self.assertEqual(status, 200, out)
        track = self.store.track("demo")
        self.assertEqual((track["status"], track["target_date"], track["daily_minutes"]), ("paused", "2026-12-15", 90))
        self.assertEqual(self.post_json(server, path, {"status": "sleeping"})[0], 400)
        self.assertEqual(self.post_json(server, path, {"target_date": "soon"})[0], 400)
        self.assertEqual(self.post_json(server, path, {"daily_minutes": -5})[0], 400)
        status, _, _ = self.call(server, "POST", path, body="status=active",
                                 headers={"X-Polymath": "1", "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        read_only = self.serve(read_only=True)
        self.assertEqual(self.post_json(read_only, path, {"status": "active"})[0], 403)

    def test_mock_note_and_void_go_through_the_engine(self):
        self.seed()
        server = self.serve()
        status, out = self.post_json(server, "/api/tracks/demo/mocks", {"score": 72, "questions": 10, "by_domain": {"basics": 80}})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.post_json(server, "/api/tracks/demo/mocks", {"score": 72, "by_domain": {"nope": 50}})[0], 400)
        self.assertEqual(self.post_json(server, "/api/tracks/demo/mocks", {"score": "lots"})[0], 400)
        self.assertEqual(self.post_json(server, "/api/tracks/demo/notes", {"text": "Drill sharing rules"})[0], 200)
        self.assertIn("Drill sharing rules", (self.root / "tracks" / "demo" / "notes.md").read_text())
        self.assertEqual(self.post_json(server, "/api/notes", {"text": "Likes rapid blocks", "section": "How I learn best"})[0], 200)
        self.assertIn("Likes rapid blocks", self.store.profile_md.read_text())
        self.assertEqual(self.post_json(server, "/api/notes", {"text": "   "})[0], 400)
        status, out = self.post_json(server, "/api/void", {"attempt": "A1", "reason": "bad key"})
        self.assertEqual((status, out["concept"]), (200, "demo.basics.a"))
        self.assertEqual(self.post_json(server, "/api/void", {"attempt": "A1"})[0], 400)  # already voided
        self.assertEqual(self.post_json(server, "/api/void", {"attempt": "drop table"})[0], 400)
        _, state = self.get_json(server, "/api/state")
        self.assertEqual(state["tracks"][0]["mocks"][-1]["score"], 0.72)
        _, detail = self.get_json(server, "/api/concept/demo.basics.a")
        self.assertTrue(detail["answers"][0]["voided"])
        self.assertEqual(detail["attempts"], 0)                      # a voided answer no longer counts

    def test_concept_and_answer_endpoints(self):
        self.seed()
        server = self.serve()
        status, detail = self.get_json(server, "/api/concept/demo.basics.b")
        self.assertEqual(status, 200)
        self.assertEqual((detail["answer_count"], detail["attempts"]), (2, 2))
        self.assertEqual(detail["prereqs"][0]["concept"], "demo.basics.a")
        self.assertEqual(detail["dependents"][0]["concept"], "demo.advanced.c")
        self.assertEqual(detail["mistakes"][0]["id"], "M1")
        self.assertEqual(self.get_json(server, "/api/concept/demo.nope")[0], 404)
        _, page = self.get_json(server, "/api/attempts?limit=2&offset=1")
        self.assertEqual((page["total"], len(page["items"]), page["items"][0]["id"]), (4, 2, "A3"))
        self.assertEqual(self.get_json(server, "/api/attempts?track=other")[1]["total"], 0)
        self.assertEqual(self.get_json(server, "/api/attempts?limit=zzz")[1]["limit"], 50)

    def test_version_changes_when_the_tutor_records_an_answer(self):
        self.seed()
        server = self.serve()
        _, before = self.get_json(server, "/api/version")
        _, state = self.get_json(server, "/api/state")
        self.assertEqual(state["app"]["data_version"], before["v"])
        self.record("demo.basics.a", "correct")
        _, after = self.get_json(server, "/api/version")
        self.assertNotEqual(before["v"], after["v"])
        self.assertEqual(self.get_json(server, "/api/state")[1]["learner"]["attempts"], 5)

    def test_an_empty_folder_serves_a_setup_page(self):
        server = self.serve()
        status, state = self.get_json(server, "/api/state")
        self.assertEqual((status, state["ready"]), (200, False))
        self.assertEqual(self.post_json(server, "/api/notes", {"text": "x"})[0], 400)

    def test_a_second_start_finds_the_running_dashboard(self):
        self.seed(answers=False)
        server = self.serve()
        port = server.server_address[1]
        again, url, lines = web.build_server(self.store, port=port, tutor="off")
        self.assertIsNone(again)
        self.assertEqual(url, f"http://127.0.0.1:{port}/?token={server.app.token}")  # the link signs in
        self.assertEqual(lines, [f"Polymath is already running at http://127.0.0.1:{port}/"])
        _, url, _ = web.build_server(self.store, port=port, tutor="off", read_only=True)
        self.assertEqual(url, f"http://127.0.0.1:{port}/")  # a --read-only start never opens a full-access link
        other = engine.Store(Path(self.tmp.name) / "other")
        with self.assertRaises(engine.PolymathError) as err:  # a different data folder on that port
            web.build_server(other, port=port, tutor="off")
        self.assertIn(f"can't be checked for this data folder: no web-ping-key could be read or made in {other.root}",
                      str(err.exception))
        self.assertFalse(other.root.exists())  # nothing is made for it, not even its folder

    def test_a_second_start_hands_the_token_only_to_this_folders_dashboard(self):
        self.seed(answers=False)
        token = web.load_token(self.store)
        real = self.serve()                                    # this folder's dashboard, on its own port
        fake_port, fake_seen = self.squatter(                  # says it is Polymath, can't prove it
            lambda path: {"app": "polymath", "version": engine.VERSION, "proof": "0" * 64})
        relay_port, relay_seen = self.squatter(                # passes the challenge on to the real one
            lambda path: self.get_json(real, path, auth=False)[1])
        opened = []
        with mock.patch.object(webbrowser, "open", lambda url, *a, **kw: opened.append(url)):
            for port in (fake_port, relay_port):
                with self.assertRaises(engine.PolymathError) as err:  # what `serve --open` meets there
                    web.serve(self.store, open_browser=True, port=port, tutor="off")
                self.assertIn(f"Port {port} is already in use", str(err.exception))
            time.sleep(0.4)                                    # serve() would open the browser after 0.3 s
        self.assertEqual(opened, [])
        self.assertTrue(relay_seen and fake_seen)
        for path in fake_seen + relay_seen:                    # each was asked one question, with no token
            self.assertTrue(path.startswith("/api/ping?challenge="), path)
            self.assertNotIn(token, path)
        again, url, _ = web.build_server(self.store, port=real.server_address[1], tutor="off")
        self.assertIsNone(again)
        self.assertIn(f"?token={token}", url)                  # while the real one is still found

    def test_ping_says_only_who_answers_and_can_prove_the_data_folder(self):
        self.seed(answers=False)
        self.root.joinpath("web-token").write_text("hunter2\n")  # a weak token, written by hand
        os.chmod(self.root / "web-token", 0o600)
        server = self.serve()
        port = server.server_address[1]
        self.assertEqual(server.app.token, "hunter2")
        status, ping = self.get_json(server, "/api/ping", auth=False)
        self.assertEqual((status, ping), (200, {"app": "polymath", "version": engine.VERSION}))  # no home_id
        self.assertFalse(hasattr(server.app, "home_id"))
        challenge = "0f" * 16
        ask = f"/api/ping?challenge={challenge}"
        self.assertNotIn("proof", self.get_json(server, ask, auth=False)[1])  # no ping key yet: none is made at start
        key = web.ensure_ping_key(self.root)                    # what a second start makes before it asks
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        status, ping = self.get_json(server, ask, auth=False, headers={"Host": "anything.example"})
        self.assertEqual(status, 200)
        self.assertEqual(ping["proof"], web.ping_proof(key, challenge, f"127.0.0.1:{port}"))
        self.assertNotEqual(ping["proof"], web.ping_proof(key, challenge, "127.0.0.1:1"))  # for this address only
        self.assertNotEqual(ping["proof"], web.ping_proof("hunter2", challenge, f"127.0.0.1:{port}"))
        # (the proof involves only the random ping key: it is no way to test guesses of the sign-in token)
        for bad in ("not-hex", "ab" * 4, "ab" * 65):
            self.assertEqual(self.get_json(server, f"/api/ping?challenge={bad}", auth=False)[0], 400, bad)
        open_one = self.serve(read_only=True)                  # no sign-in, and it can still prove it
        self.assertIsNone(open_one.app.token)
        _, ping = self.get_json(open_one, ask)
        self.assertEqual(ping["proof"], web.ping_proof(key, challenge, f"127.0.0.1:{open_one.server_address[1]}"))
        if os.name == "posix":                                  # a key other accounts can read proves nothing...
            os.chmod(self.root / "web-ping-key", 0o644)
            self.assertNotIn("proof", self.get_json(open_one, ask)[1])
            fresh = web.ensure_ping_key(self.root)              # ...and the next second start replaces it
            self.assertNotEqual(fresh, key)
            self.assertEqual(stat.S_IMODE((self.root / "web-ping-key").stat().st_mode), 0o600)
            self.assertEqual(self.get_json(open_one, ask)[1]["proof"],
                             web.ping_proof(fresh, challenge, f"127.0.0.1:{open_one.server_address[1]}"))

    def test_rotating_the_token_is_refused_while_this_folders_dashboard_runs(self):
        self.seed(answers=False)
        server = self.serve()
        before = (self.root / "web-token").read_text()
        with self.assertRaises(engine.PolymathError) as err:
            web.build_server(self.store, port=server.server_address[1], rotate_token=True, tutor="off")
        self.assertIn("Stop it first", str(err.exception))
        self.assertEqual((self.root / "web-token").read_text(), before)  # nothing replaced underneath it
        self.assertEqual(self.call(server, "GET", "/api/state")[0], 200)
        older, _ = self.squatter(lambda path: {"app": "polymath", "version": "0.1.0", "home_id": "0" * 16})
        with self.assertRaises(engine.PolymathError) as err:      # an older dashboard can't answer the challenge
            web.build_server(self.store, port=older, rotate_token=True, tutor="off")
        self.assertIn("older version serving this data folder, stop it before rotating the token", str(err.exception))
        self.assertEqual((self.root / "web-token").read_text(), before)

    def test_a_sign_in_is_a_random_id_that_expires_and_ends_at_sign_out(self):
        self.seed(answers=False)
        server = self.serve()
        port, token = server.server_address[1], server.app.token

        def sign_in():
            status, headers, _ = self.call(server, "GET", f"/?token={token}", auth=False)
            self.assertEqual(status, 303)
            self.assertIn(f"Max-Age={30 * 86400}", headers["Set-Cookie"])
            return self.browser(headers)

        def state(signed, on=server):
            return self.call(on, "GET", "/api/state", headers=signed, auth=False)[0]

        signed = sign_in()
        cookie = signed["Cookie"]
        name, _, sid = cookie.partition("=")
        self.assertEqual(name, f"polymath_session_{port}")      # one cookie per dashboard, named for its port
        again = sign_in()
        self.assertNotEqual((again["Cookie"], again["X-Polymath-Key"]), (cookie, signed["X-Polymath-Key"]))
        signed, cookie = again, again["Cookie"]                  # random each time, not derived from the token
        self.assertEqual(state(signed), 200)
        saved = self.root / "web-auth.json"
        self.assertNotIn(sid, saved.read_text())                 # only a keyed hash of the id is kept
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertIn("web-auth.json", (self.root / ".gitignore").read_text().split())
        # signing out: refused from another site, and it ends the sign-in on the server, not only in the browser
        evil = {"Cookie": cookie, "Origin": "https://evil.example",
                "Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(self.call(server, "POST", "/logout", body="x=1", headers=evil, auth=False)[0], 403)
        self.assertEqual(state(signed), 200)
        status, headers, _ = self.call(server, "POST", "/logout", body="", headers={"Cookie": cookie}, auth=False)
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertIn(f"polymath_session_{port}=;", headers["Set-Cookie"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(state(signed), 401)                     # a copy kept anywhere else stops working too
        # the server, not the browser, ends a sign-in after 30 days
        signed = sign_in()
        self.at("2026-12-15T17:59:00Z")
        self.assertEqual(state(signed), 200)
        self.at("2026-12-15T18:00:01Z")
        self.assertEqual(state(signed), 401)
        # a restart keeps a sign-in...
        self.at(DAY1)
        signed = sign_in()
        sid = signed["Cookie"].partition("=")[2]
        self.stop(server)
        server = self.serve(port=port)                           # the same dashboard, started again
        self.assertEqual(state(signed, on=server), 200)
        # ...for that dashboard only: one for this folder on another port never takes the id
        other = self.serve()
        self.assertNotEqual(other.app.cookie, server.app.cookie)
        self.assertEqual(state({**signed, "Cookie": f"{other.app.cookie}={sid}"}, on=other), 401)
        # and a new token signs every browser out
        with mock.patch.object(sys, "stderr", io.StringIO()):
            rotated, _, _ = web.build_server(self.store, port=0, rotate_token=True, tutor="off")
            try:
                self.assertFalse(saved.exists())
                self.assertEqual(state(signed, on=server), 401)  # the running dashboard follows web-token
            finally:
                rotated.server_close()

    def test_the_pages_own_forms_sign_in_and_out_as_browsers_send_them(self):
        """Browsers post a form from a page whose referrer policy is no-referrer (index.html's) with Origin
        "null", and say where it came from in Sec-Fetch-Site, which no page can set. Before, every such
        form was taken for another site's, so typing the token into the sign-in page always failed."""
        self.seed(answers=False)
        server = self.serve()
        port, token = server.server_address[1], server.app.token
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        ours = {**form, "Origin": "null", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate"}

        def post(path, headers, body=""):
            return self.call(server, "POST", path, body=body, headers=headers, auth=False)

        def state(signed):
            return self.call(server, "GET", "/api/state", headers=signed, auth=False)[0]

        status, headers, _ = post("/login", ours, f"token={token}")
        self.assertEqual(status, 303)
        signed = self.browser(headers)
        cookie = signed["Cookie"]
        foreign = [
            {**form, "Origin": "null"},                                             # nothing vouches for it
            {**form, "Origin": "null", "Sec-Fetch-Site": "cross-site"},              # a sandboxed frame, say
            {**form, "Origin": "http://127.0.0.1:3000", "Sec-Fetch-Site": "same-site"},  # another port here
            {**form, "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-site"},
            {**form, "Origin": "http://localhost:3000", "Sec-Fetch-Site": "cross-site"},
            {**form, "Origin": ""},
        ]
        for headers in foreign:
            self.assertEqual(post("/login", headers, f"token={token}")[0], 403, headers)
            self.assertEqual(post("/logout", {**headers, "Cookie": cookie})[0], 403, headers)
            page = {**headers, **signed, "X-Polymath": "1", "Content-Type": "application/json"}
            self.assertEqual(post("/api/preferences", page, '{"block_size": 2}')[0], 403, headers)
        self.assertEqual(state(signed), 200)                     # none of them signed it out
        fetched = {**signed, "X-Polymath": "1", "Content-Type": "application/json",
                   "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(post("/api/preferences", fetched, '{"block_size": 2}')[0], 200)  # the page's own fetch
        status, headers, _ = post("/logout", {**ours, "Cookie": cookie})
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertEqual(state(signed), 401)
        # the sign-in page's own policy is same-origin, so its form carries its real Origin in any browser
        status, headers, _ = self.call(server, "GET", "/login", auth=False)
        self.assertEqual((status, headers.get_all("Referrer-Policy")), (200, ["same-origin"]))
        self.assertEqual(self.call(server, "GET", "/api/state")[1].get_all("Referrer-Policy"), ["no-referrer"])
        status, headers, _ = post("/login", {**form, "Origin": f"http://127.0.0.1:{port}"}, f"token={token}")
        self.assertEqual(status, 303)

    def test_only_a_dashboard_that_uses_the_saved_token_writes_it(self):
        """A read-only dashboard (on a synced copy, say) and one given its own token (Docker's
        POLYMATH_WEB_TOKEN) write nothing into the data folder: no web-token, no .gitignore line."""
        self.seed(answers=False)
        before = self.files()
        viewer = self.serve(read_only=True)
        given = self.serve(token="a-token-of-its-own-1234")
        self.assertIsNone(viewer.app.token)
        self.assertEqual(self.call(viewer, "GET", "/api/state")[0], 200)
        self.assertEqual(self.call(given, "GET", "/api/state")[0], 200)
        self.assertEqual(self.files(), before)
        port = viewer.server_address[1]                          # a second start finds the viewer...
        again, url, _ = web.build_server(self.store, port=port, tutor="off", read_only=True)
        self.assertIsNone(again)
        self.assertEqual(url, f"http://127.0.0.1:{port}/")      # ...hands out no token...
        self.assertEqual(self.files() - before, {"web-ping-key", ".gitignore"} - before)  # ...and makes only its key
        self.assertFalse((self.root / "web-token").exists())
        own = self.serve()                                       # one that signs in with the saved token makes it
        self.assertEqual((self.root / "web-token").read_text().strip(), own.app.token)

    def test_a_dashboard_using_the_saved_token_follows_a_new_one(self):
        """--rotate-token while this folder's dashboard runs on another port: there too, the old token and
        the sign-ins made with it stop working, and the new token works. Before, they kept working until
        that dashboard restarted."""
        self.seed(answers=False)
        server = self.serve()
        given = self.serve(token="a-token-of-its-own-1234")    # not the saved token: rotating that leaves it be
        old = server.app.token
        signed = self.browser(self.call(server, "GET", f"/?token={old}", auth=False)[1])

        def state(on, headers):
            return self.call(on, "GET", "/api/state", headers=headers, auth=False)[0]

        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            new = self.serve(rotate_token=True).app.token
            self.assertNotEqual(new, old)
            self.assertEqual(state(server, {"Authorization": f"Bearer {old}"}), 401)
            self.assertEqual(state(server, signed), 401)
            self.assertEqual(state(server, {"Authorization": f"Bearer {new}"}), 200)
        self.assertIn("was replaced", err.getvalue())
        self.assertEqual(state(given, {"Authorization": "Bearer a-token-of-its-own-1234"}), 200)
        port = server.server_address[1]                          # a second start there still finds it
        again, url, _ = web.build_server(self.store, port=port, tutor="off")
        self.assertIsNone(again)
        self.assertEqual(url, f"http://127.0.0.1:{port}/?token={new}")
        os.unlink(self.root / "web-token")                       # a token that goes missing is no new token
        self.assertEqual(state(server, {"Authorization": f"Bearer {new}"}), 200)

    def test_two_dashboards_for_one_folder_keep_each_others_sign_ins(self):
        """Two dashboards for one data folder (here two sign-in stores, as two processes have) write
        web-auth.json at the same moment: after a restart, every sign-in is still there, and none that
        was signed out has come back."""
        self.seed(answers=False)
        token = web.load_token(self.store)
        a, b = web.Sessions(self.store, token, 8001), web.Sessions(self.store, token, 8002)
        ended = [a.create() for _ in range(6)]
        made = {"a": [], "b": []}

        def sign_ins(name, sessions):
            for _ in range(25):
                made[name].append(sessions.create())

        def sign_outs():
            for sid in ended:
                a.revoke(sid)

        workers = [threading.Thread(target=sign_ins, args=("a", a)), threading.Thread(target=sign_ins, args=("b", b)),
                   threading.Thread(target=sign_outs)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        a, b = web.Sessions(self.store, token, 8001), web.Sessions(self.store, token, 8002)  # restarted
        self.assertEqual(sum(a.valid(sid) for sid in made["a"]), 25)
        self.assertEqual(sum(b.valid(sid) for sid in made["b"]), 25)
        self.assertEqual(sum(a.valid(sid) for sid in ended), 0)
        self.assertEqual(sum(b.valid(sid) for sid in made["a"]), 0)  # an id opens only the dashboard that made it

    def test_a_cookie_planted_by_another_port_never_hides_the_sign_in(self):
        """Cookies aren't kept apart by port, so a page on another port of this host can set one named like
        the dashboard's, with a narrower path; the browser then sends it first. Before, only the first value
        was read: every API call answered 401 and the page reloaded between / and /login for good."""
        self.seed(answers=False)
        server = self.serve()
        name = server.app.cookie
        status, headers, _ = self.call(server, "GET", f"/?token={server.app.token}", auth=False)
        signed = self.browser(headers)
        real = signed["Cookie"].partition("=")[2]
        both = {**signed, "Cookie": f"{name}=planted-by-another-port; {name}={real}"}
        self.assertEqual(self.call(server, "GET", "/api/state", headers=both, auth=False)[0], 200)
        status, _, _ = self.call(server, "POST", "/logout", body="", headers=both, auth=False)
        self.assertEqual(status, 303)
        self.assertEqual(self.call(server, "GET", "/api/state", headers=signed,
                                   auth=False)[0], 401)              # signing out ended the real one too

    def test_over_plain_http_by_name_another_port_cant_sign_you_out(self):
        """Browsers send Sec-Fetch-Site only to secure addresses, so a dashboard reached over plain HTTP by
        name decides by the Origin alone: before, it compared names without ports and trusted any loopback
        name, so a form on another port of that host (or on localhost) could sign the learner out."""
        self.seed(answers=False)
        server = self.serve(host="127.0.0.1", allow_hosts=["lan.test"])
        port = server.server_address[1]
        status, headers, _ = self.call(server, "GET", f"/?token={server.app.token}", auth=False)
        signed = self.browser(headers)
        cookie = signed["Cookie"]

        def logout(origin):
            sent = {"Cookie": cookie, "Host": f"lan.test:{port}", "Origin": origin,
                    "Content-Type": "application/x-www-form-urlencoded"}
            return self.call(server, "POST", "/logout", body="x=1", headers=sent, auth=False)[0]
        for origin in ("http://lan.test:9999", "http://localhost:9999", f"http://localhost:{port}",
                       "http://lan.test", "https://lan.test"):
            self.assertEqual(logout(origin), 403, origin)            # was: 303, signed out
        self.assertEqual(self.call(server, "GET", "/api/state", headers=signed, auth=False)[0], 200)
        self.assertEqual(logout(f"http://lan.test:{port}"), 303)     # its own page still can

    def test_a_proxy_in_front_keeps_its_own_origin(self):
        self.seed(answers=False)
        server = self.serve(allow_hosts=["polymath.example.com"])
        status, headers, _ = self.call(server, "GET", f"/?token={server.app.token}", auth=False)
        sent = {**self.browser(headers), "X-Polymath": "1", "Content-Type": "application/json",
                "Origin": "https://polymath.example.com", "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "polymath.example.com"}
        self.assertEqual(self.call(server, "POST", "/api/preferences", body='{"block_size": 2}', headers=sent,
                                   auth=False)[0], 200)              # as Caddy forwards it
        sent["Origin"] = "https://polymath.example.com:8443"
        self.assertEqual(self.call(server, "POST", "/api/preferences", body='{"block_size": 2}', headers=sent,
                                   auth=False)[0], 403)              # another port behind the same name

    def test_the_saved_token_reads_through_a_byte_order_mark_or_utf16(self):
        """Before, re-saving web-token with a BOM (older Notepad) switched a running dashboard to a token
        nobody could type, and a UTF-16 one stopped the next start with a misleading 'chmod 600'."""
        self.seed(answers=False)
        server = self.serve()
        token, path = server.app.token, self.root / "web-token"
        status, headers, _ = self.call(server, "GET", f"/?token={token}", auth=False)
        signed = self.browser(headers)
        path.write_bytes(b"\xef\xbb\xbf" + token.encode() + b"\r\n")
        self.assertEqual(self.call(server, "GET", "/api/state", headers=signed, auth=False)[0], 200)
        self.assertEqual((web.read_token(self.store), server.app.token), (token, token))
        path.write_bytes(token.encode("utf-16"))
        self.assertEqual(web.read_token(self.store), token)
        path.write_bytes(b"two words\n")
        with self.assertRaisesRegex(engine.PolymathError, "space or an invisible character"):
            web.read_token(self.store)
        self.assertEqual(self.call(server, "GET", "/api/state", headers=signed, auth=False)[0], 200)

    def test_a_token_with_accents_works_as_bearer_from_utf8_clients(self):
        self.seed(answers=False)
        token = "pässwörd-ü-123"
        server = self.serve(token=token)
        request = (f"GET /api/version HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                   f"Authorization: Bearer {token}\r\nConnection: close\r\n\r\n").encode("utf-8")  # as curl sends it
        self.assertEqual(STATUS.findall(self.raw(server, request)[0]), [b"200"])  # was: 401
        wrong = request.replace("ü".encode("utf-8"), b"u")
        self.assertEqual(STATUS.findall(self.raw(server, wrong)[0]), [b"401"])

    def test_links_for_a_host_name_use_the_address_bound(self):
        """A name like localhost can resolve to [::1] first, where another account could be listening
        on the same port, and the sign-in link would take the token there."""
        self.seed(answers=False)
        server = self.serve(host="localhost")
        port = server.server_address[1]
        self.assertTrue(server.link.startswith(f"http://127.0.0.1:{port}/?token="), server.link)
        self.assertEqual(web.link_host("localhost", "127.0.0.1", False), "127.0.0.1")
        self.assertEqual(web.link_host("localhost", "127.0.0.1", True), "localhost")  # TLS: the certificate's name
        self.assertEqual((web.link_host("::1", "::1", False), web.link_host("0.0.0.0", None, False)),
                         ("[::1]", "127.0.0.1"))
        with mock.patch.object(sys, "stderr", io.StringIO()):
            again, link, _ = web.build_server(self.store, host="localhost", port=port, tutor="off")
        self.assertIsNone(again)                                     # already running: the link it opens
        self.assertTrue(link.startswith(f"http://127.0.0.1:{port}/?token="), link)

    def test_many_planted_cookies_never_hide_the_sign_in(self):
        """Round 3: only the first 8 values were tested, and a page on another port can plant more (named,
        nameless and partitioned, at several paths) ahead of the real one."""
        self.seed(answers=False)
        server = self.serve()
        name = server.app.cookie
        status, headers, _ = self.call(server, "GET", f"/?token={server.app.token}", auth=False)
        signed = self.browser(headers)
        planted = "; ".join(f"{name}=planted-{n}" for n in range(20))
        sent = {**signed, "Cookie": f"{planted}; {signed['Cookie']}"}
        self.assertEqual(self.call(server, "GET", "/api/state", headers=sent, auth=False)[0], 200)

    def test_behind_a_proxy_that_rewrites_host_the_dashboard_still_takes_changes(self):
        """nginx forwards Host as its upstream (127.0.0.1:port) by default. Round 3: the Origin check then
        refused the sign-in form and every change."""
        self.seed(answers=False)
        server = self.serve(allow_hosts=["polymath.example.com"])
        port = server.server_address[1]
        form = {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://polymath.example.com",
                "Host": f"127.0.0.1:{port}"}
        status, headers, _ = self.call(server, "POST", "/login", body=f"token={server.app.token}", headers=form,
                                       auth=False)
        self.assertEqual(status, 303)                                # a browser without Fetch Metadata
        change = {**self.browser(headers), "X-Polymath": "1", "Content-Type": "application/json",
                  "Origin": "https://polymath.example.com", "Host": f"127.0.0.1:{port}"}
        body = '{"block_size": 2}'
        self.assertEqual(self.call(server, "POST", "/api/preferences", body=body, headers=change, auth=False)[0], 200)
        fetch = {**change, "Origin": "https://polymath.example.com:8443", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.call(server, "POST", "/api/preferences", body=body, headers=fetch, auth=False)[0], 200)
        other = {**change, "Origin": "https://polymath.example.com:8443"}  # another port, nothing vouching for it
        self.assertEqual(self.call(server, "POST", "/api/preferences", body=body, headers=other, auth=False)[0], 403)
        named = {**change, "Origin": "http://polymath.example.com", "Host": f"polymath.example.com:{port}"}
        self.assertEqual(self.call(server, "POST", "/api/preferences", body=body, headers=named, auth=False)[0], 403)

    def test_a_token_ending_in_an_accented_letter_works_as_bearer(self):
        self.seed(answers=False)
        for token in ("parolà-123", "Pass-Å", "dagger†"):
            server = self.serve(token=token)                         # UTF-8 ends in 0xA0 or 0x85
            request = (f"GET /api/version HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                       f"Authorization: Bearer {token}\r\nConnection: close\r\n\r\n").encode("utf-8")
            self.assertEqual(STATUS.findall(self.raw(server, request)[0]), [b"200"], token)  # was: 401

    def test_answers_logged_late_are_charted_on_the_day_they_were_given(self):
        self.seed(answers=False)
        self.at(DAY2)
        self.record("demo.basics.a", "correct", confidence=4)       # a record made the next day...
        items = [engine.build_attempt({"concept": c, "result": "correct", "qtype": "mcq", "track": "demo"},
                                      self.store.concepts()) for c in ("demo.basics.b", "demo.advanced.c")]
        engine.record_events(self.store, items, at=engine.parse_ts(DAY1))  # ...then answers given the day before
        state = web.collect_state(self.store)
        points = {p["date"]: p["answers"] for p in state["tracks"][0]["history"]}
        self.assertEqual((points.get("2026-11-15"), points.get("2026-11-16")), (2, 1))  # was: 0 and 3
        recent = [r["concept"] for r in state["recent"]][:3]
        self.assertEqual(recent[0], "demo.basics.a")                 # newest first, by when it was given
        model, raw = engine.Model(self.store), self.store.events()
        page = web.concept_detail(model, "demo.basics.b", raw, set())["answers"]
        self.assertEqual([a["ts"] for a in page], sorted((a["ts"] for a in page), reverse=True))

    def test_a_request_stuffed_with_cookie_values_costs_little(self):
        """Round 4: duplicate values were removed with a list search, so a request carrying tens of thousands of
        values held the CPU for minutes, before any sign-in check."""
        self.seed(answers=False)
        server = self.serve()
        lines = "".join("Cookie: " + "; ".join(f"{server.app.cookie}=v{k}-{n}" for n in range(1500)) + "\r\n"
                        for k in range(30))                          # 45,000 values, each line under 64 KB
        request = (f"GET /api/version HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n{lines}"
                   "Connection: close\r\n\r\n").encode()
        started = time.time()
        out, _ = self.raw(server, request, wait=30)
        self.assertEqual(STATUS.findall(out)[:1], [b"401"])
        self.assertLess(time.time() - started, 2.0)                  # was: minutes, quadratic in the values

    def test_allow_host_can_name_the_proxys_port(self):
        self.seed(answers=False)
        server = self.serve(allow_hosts=["lan.test:8080"])            # nginx on :8080, rewriting Host
        port = server.server_address[1]
        status, headers, _ = self.call(server, "GET", f"/?token={server.app.token}", auth=False)
        change = {**self.browser(headers), "X-Polymath": "1", "Content-Type": "application/json",
                  "Origin": "http://lan.test:8080", "Host": f"127.0.0.1:{port}"}
        body = '{"block_size": 2}'
        self.assertEqual(self.call(server, "POST", "/api/preferences", body=body, headers=change, auth=False)[0], 200)
        other = {**change, "Origin": "http://lan.test:9090"}
        self.assertEqual(self.call(server, "POST", "/api/preferences", body=body, headers=other, auth=False)[0], 403)
        self.assertEqual(self.call(server, "GET", "/api/version", headers={"Host": "lan.test:8080"})[0], 200)

    def test_too_much_cookie_data_says_what_to_do(self):
        self.seed(answers=False)
        server = self.serve()
        big = "x" * 70000                                            # past the 64 KB a header line may hold
        out, _ = self.raw(server, (f"GET /api/version HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                                   f"Cookie: junk={big}\r\nConnection: close\r\n\r\n").encode())
        self.assertEqual(STATUS.findall(out)[:1], [b"431"])
        self.assertIn(b"Clear this site", out)                       # was: only 'Line too long'

    def test_sign_in_links_carry_any_token_intact(self):
        self.seed(answers=False)
        server = self.serve(token="Qm9v+ay/zd&e#f%g")            # e.g. from `openssl rand -base64 24`
        self.assertIn("?token=Qm9v%2Bay%2Fzd%26e%23f%25g", server.link)
        self.assertIn(f"Sign in:  {server.link}", "\n".join(server.lines))
        self.assertNotIn("web-token", "\n".join(server.lines))  # that file doesn't hold a --token
        target = urllib.parse.urlsplit(server.link)
        status, headers, _ = self.call(server, "GET", f"{target.path}?{target.query}", auth=False)
        self.assertEqual(status, 303)                            # opening the printed link signs in
        self.assertIn("polymath_session_", headers["Set-Cookie"])

    @unittest.skipUnless(os.name == "posix", "file permissions")
    def test_the_token_file_is_made_private_and_kept_out_of_git(self):
        self.root.mkdir(parents=True)
        (self.root / ".gitignore").write_text("dashboard.html")  # the learner's own, with no final newline
        path = self.root / "web-token"
        path.write_text("an-old-token-copied-in-with-cp\n")
        os.chmod(path, 0o644)
        self.assertEqual(web.load_token(self.store), "an-old-token-copied-in-with-cp")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)  # made private when it is read
        os.chmod(path, 0o644)
        new = web.load_token(self.store, rotate=True)
        self.assertEqual((path.read_text().strip(), stat.S_IMODE(path.stat().st_mode)), (new, 0o600))
        lines = (self.root / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], "dashboard.html")             # the learner's last line is left intact
        for name in ("web-token", "web-auth.json", "web-ping-key"):
            self.assertEqual(lines.count(name), 1, name)

    def test_a_body_the_server_doesnt_read_never_becomes_another_request(self):
        self.seed(answers=False)
        server = self.serve()
        host = f"Host: 127.0.0.1:{server.server_address[1]}\r\n"
        hidden = f"GET /api/ping HTTP/1.1\r\n{host}\r\n".encode()  # smuggled in as a "body"
        bearer = f"Authorization: Bearer {server.app.token}\r\n"
        cases = [
            (f"POST /api/notes HTTP/1.1\r\n{host}Content-Type: application/json\r\n"
             "Content-Length: 70000\r\n\r\n", b"413"),
            (f"POST /api/notes HTTP/1.1\r\n{host}Content-Length: 12abc\r\n\r\n", b"400"),
            (f"POST /api/notes HTTP/1.1\r\n{host}Content-Length: {len(hidden)}\r\nContent-Length: 0\r\n\r\n", b"400"),
            (f"POST /api/notes HTTP/1.1\r\n{host}Transfer-Encoding: chunked\r\n\r\n", b"411"),
            (f"GET /api/version HTTP/1.1\r\n{host}{bearer}Content-Length: {len(hidden)}\r\n\r\n", b"200"),
        ]
        open_one = self.serve(read_only=True)                    # no token: it refuses unknown host names
        cases = [(server, head, status) for head, status in cases] + [
            (open_one, f"POST /api/notes HTTP/1.1\r\nHost: evil.example\r\nContent-Length: {len(hidden)}\r\n\r\n",
             b"403")]
        for target, head, expected in cases:
            out, closed = self.raw(target, head.encode() + hidden)
            statuses = STATUS.findall(out)
            self.assertEqual(statuses, [expected], (head, out))  # one request in, one answer out
            self.assertTrue(closed, head)
            self.assertIn(b"Connection: close", out)
        two = hidden + hidden                                    # plain keep-alive still serves both
        out, closed = self.raw(server, two, wait=0.5)
        self.assertEqual(STATUS.findall(out), [b"200", b"200"])
        self.assertFalse(closed)

    def test_an_idle_connection_is_closed(self):
        self.assertEqual(web.Handler.timeout, 60)
        self.seed(answers=False)
        server = self.serve()
        ping = f"GET /api/ping HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n\r\n".encode()
        with mock.patch.object(web.Handler, "timeout", 0.3):
            out, closed = self.raw(server, ping)
        self.assertTrue(out.startswith(b"HTTP/1.1 200"))
        self.assertTrue(closed)                                  # kept open after the answer, then closed

    def test_with_tls_an_idle_connection_holds_up_only_itself(self):
        cert, key = self.tls_files()
        self.seed(answers=False)
        server = self.serve(tls_cert=cert, tls_key=key)
        port = server.server_address[1]
        self.assertTrue(server.link.startswith(f"https://127.0.0.1:{port}/?token="))
        context = ssl.create_default_context()
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE

        def state():
            conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=4, context=context)
            try:
                conn.request("GET", "/api/state", headers={"Authorization": f"Bearer {server.app.token}"})
                resp = conn.getresponse()
                resp.read()
                return resp.status
            finally:
                conn.close()

        self.assertEqual(state(), 200)
        with socket.create_connection(("127.0.0.1", port), timeout=5):  # connects, then never says a word
            time.sleep(0.2)
            self.assertEqual(state(), 200)                       # everyone else is still answered
        with mock.patch.object(web, "TLS_HANDSHAKE_SECONDS", 0.3), \
                socket.create_connection(("127.0.0.1", port), timeout=5) as idle:
            try:
                self.assertEqual(idle.recv(1), b"")              # dropped once the handshake deadline passes
            except ConnectionResetError:
                pass
        self.assertEqual(state(), 200)
        again, url, _ = web.build_server(self.store, port=port, tutor="off", tls_cert=cert, tls_key=key)
        self.assertIsNone(again)                                 # a second start finds it over HTTPS too
        self.assertTrue(url.startswith(f"https://127.0.0.1:{port}/?token="))

    def test_undo_in_claude_code_leaves_the_dashboards_changes_alone(self):
        self.seed()
        server = self.serve()
        self.assertEqual(self.post_json(server, "/api/void", {"attempt": "A2", "reason": "bad key"})[0], 200)
        self.assertEqual(self.post_json(server, "/api/tracks/demo/mocks", {"score": 70})[0], 200)
        self.assertEqual(self.post_json(server, "/api/tracks/demo/settings", {"target_date": "2026-12-20"})[0], 200)
        mine = self.store.events()[-3:]
        self.assertEqual([(e["type"], e.get("source")) for e in mine],
                         [("void", "web"), ("mock", "web"), ("track_update", "web")])
        with self.store.writing():
            undone = engine.undo_events(self.store, 1)            # the tutor takes back its own last answer
        self.assertEqual([e.get("id") for e in undone], ["A4"])
        events = self.store.events()
        for e in mine:                                            # the dashboard's three changes all stay
            self.assertIn(e, events)
        self.assertEqual(self.store.track("demo")["target_date"], "2026-12-20")

    # --- snapshot, demo, assets -----------------------------------------------------------------
    def test_snapshot_is_one_safe_file(self):
        self.seed()
        page = web.snapshot_html(self.store)
        self.assertNotIn('src="app.js"', page)
        self.assertNotIn("<b>track</b>", page)                       # titles travel as escaped JSON
        marker = '<script id="polymath-data" type="application/json">'
        start = page.index(marker) + len(marker)
        data = json.loads(page[start:page.index("</script>", start)])
        self.assertEqual((data["app"]["mode"], data["app"]["read_only"]), ("snapshot", True))
        self.assertEqual(data["tracks"][0]["title"], "Demo <b>track</b>")
        self.assertIn("demo.basics.b", data["concept_details"])

    def test_demo_learner(self):
        self.at("2026-11-18T08:00:00Z")
        store = web.build_demo(Path(self.tmp.name) / "demo")
        self.assertEqual(os.environ["POLYMATH_NOW"], "2026-11-18T08:00:00Z")  # the clock is put back
        state = web.collect_state(store)
        track = state["tracks"][0]
        self.assertGreater(state["learner"]["attempts"], 100)
        self.assertTrue(track["trend"]["enough"])
        self.assertEqual(track["history"][-1]["predicted"], track["predicted_score"])
        self.assertTrue(track["mocks"])

    def test_page_script_parses_and_can_be_inlined(self):
        source = (ROOT / "web" / "app.js").read_text()
        self.assertNotIn("</script", source.lower())                 # snapshots inline it
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        proc = subprocess.run([node, "--check", str(ROOT / "web" / "app.js")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_serve_command_is_wired_up(self):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "polymath.py"), "serve", "--help"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--host", "--token", "--read-only", "--allow-host", "--tls-cert", "--demo"):
            self.assertIn(flag, proc.stdout)


if __name__ == "__main__":
    unittest.main()
