"""The AI model layer: local servers, API keys, going offline, and switching. Everything talks to a fake server on
this machine: no test reaches a real model, a real provider or the real Claude Code."""
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import polymath as engine               # noqa: E402
import polymath_models as models        # noqa: E402
import polymath_study as study_mod      # noqa: E402
import polymath_writer as writer        # noqa: E402
import test_web                         # noqa: E402

import polymath_bench as bench_mod        # noqa: E402

_RELEASE = {}


def setUpModule():
    """These tests cover every provider and the benchmark, which this release switches off."""
    _RELEASE.update(enabled=models.ENABLED, sdk=models.has_sdk, bench=bench_mod.ENABLED)
    models.ENABLED, models.has_sdk, bench_mod.ENABLED = tuple(models.PROVIDERS), (lambda: True), True


def tearDownModule():
    models.ENABLED, models.has_sdk, bench_mod.ENABLED = _RELEASE["enabled"], _RELEASE["sdk"], _RELEASE["bench"]


KEY = "sk-test-do-not-leak-12345"
SCHEMAS = [v for k, v in vars(writer).items() if k.endswith("_SCHEMA") and isinstance(v, dict)]


class FakeModelServer:
    """Ollama's API and an OpenAI-compatible one on one port, writing through FakeWriter."""

    def __init__(self, names=("small:8b",), need_key=None, port=0):
        self.names, self.need_key = list(names), need_key
        self.seen, self.refuse_schema, self.redirect, self.fail = [], False, None, None
        self.pen = writer.FakeWriter()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data, headers=()):
                raw = json.dumps(data).encode()
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def guard(self):
                outer.seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
                if outer.redirect:
                    self.reply(302, {}, [("Location", outer.redirect)])
                    return False
                if outer.fail:
                    self.reply(outer.fail[0], {"error": {"message": outer.fail[1]}})
                    return False
                if outer.need_key and self.headers.get("Authorization") != f"Bearer {outer.need_key}":
                    self.reply(401, {"error": {"message": "bad key"}})
                    return False
                return True

            def do_GET(self):
                if not self.guard():
                    return
                if self.path == "/api/tags":
                    return self.reply(200, {"models": [{"name": n} for n in outer.names]})
                if self.path == "/v1/models":
                    return self.reply(200, {"data": [{"id": n} for n in outer.names] + [{"id": "text-embedding-3"}]})
                self.reply(404, {"error": "not found"})

            def answer(self, messages, schema):
                system = next((m["content"] for m in messages if m["role"] == "system"), "")
                user = next((m["content"] for m in messages if m["role"] == "user"), "")
                if schema == models.TEST_SCHEMA or "Set ok to true" in user:
                    return json.dumps({"ok": True})
                known = next((s for s in SCHEMAS if s == schema), schema)
                return json.dumps(outer.pen.run(system, user, known))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not self.guard():
                    return
                outer.seen[-1]["body"] = body
                if body.get("model") not in outer.names:
                    return self.reply(404, {"error": f"model '{body.get('model')}' not found"})
                if self.path == "/api/chat":
                    return self.reply(200, {"message": {"content": self.answer(body["messages"], body.get("format"))},
                                            "done_reason": "stop", "prompt_eval_count": 11, "eval_count": 7})
                if self.path == "/v1/chat/completions":
                    fmt = body.get("response_format") or {}
                    if outer.refuse_schema and fmt.get("type") == "json_schema":
                        return self.reply(400, {"error": {"message": "response_format json_schema not supported"}})
                    schema = (fmt.get("json_schema") or {}).get("schema") or models.TEST_SCHEMA
                    return self.reply(200, {"choices": [{"message": {"content": self.answer(body["messages"], schema)},
                                                         "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 5, "completion_tokens": 3}})
                self.reply(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.env = mock.patch.dict(os.environ, {"POLYMATH_HOME": str(self.root)})
        self.env.start()
        self.store = engine.Store(self.root)
        self.fakes, self.studies = [], []

    def tearDown(self):
        for st in self.studies:
            st.stop()
        for fake in self.fakes:
            fake.close()
        self.env.stop()
        self.tmp.cleanup()

    def fake(self, **kw) -> FakeModelServer:
        server = FakeModelServer(**kw)
        self.fakes.append(server)
        return server

    def seed(self):
        with redirect_stdout(StringIO()):
            engine.cmd_init(Namespace(name="Ada", persona=None, json=True), self.store)
        engine.import_track(self.store, test_web.syllabus(), target_date="2027-01-01")

    def study(self, pen):
        st = study_mod.Study(self.store, pen)
        self.studies.append(st)
        return st

    def wait(self, st, sid, states, limit=8):
        end = time.time() + limit
        while time.time() < end:
            v = st.view(sid)
            if all(q["state"] in states for q in v["questions"]):
                return v
            time.sleep(0.03)
        self.fail(f"questions never reached {states}: {[q['state'] for q in st.view(sid)['questions']]}")


class Addresses(unittest.TestCase):
    def test_a_local_address_is_tidied_for_its_protocol(self):
        self.assertEqual(models.clean_url("http://127.0.0.1:8080/", "openai"), "http://127.0.0.1:8080/v1")
        self.assertEqual(models.clean_url("http://localhost:11434/v1", "ollama"), "http://localhost:11434")
        self.assertEqual(models.clean_url("http://192.168.1.20:1234/v1"), "http://192.168.1.20:1234/v1")

    def test_addresses_that_must_never_be_used(self):
        for bad in ("http://169.254.169.254/latest", "http://0.0.0.0:1234", "ftp://127.0.0.1/x", "not a url", "",
                    "http://user:pw@127.0.0.1:1234", "http://127.0.0.1:1234/v1?x=1", "http://8.8.8.8/v1",
                    "http://[::ffff:169.254.169.254]/v1"):
            with self.assertRaises(writer.WriterError, msg=bad) as ctx:
                models.clean_url(bad)
            self.assertEqual(ctx.exception.code, "bad_url")

    def test_where_it_runs(self):
        self.assertEqual(models.where("http://localhost:1"), "device")
        self.assertEqual(models.where("http://127.0.0.1:1"), "device")
        self.assertEqual(models.where("http://10.0.0.5:1"), "network")
        self.assertEqual(models.where("https://8.8.8.8/v1"), "internet")


class Replies(unittest.TestCase):
    def test_json_is_found_around_what_models_add(self):
        for text in ('{"a":1}', '<think>hmm {no}</think>{"a":1}', '```json\n{"a":1}\n```', 'Here it is: {"a":1} done'):
            self.assertEqual(models.parse_json(text), {"a": 1}, text)

    def test_no_json_is_a_clear_failure(self):
        for text in ("", "sorry", "[1,2]"):
            with self.assertRaises(writer.WriterError) as ctx:
                models.parse_json(text)
            self.assertEqual(ctx.exception.code, "invalid")


class Local(Base):
    def test_ollama_call_keeps_the_context_large_and_asks_for_the_schema(self):
        fake = self.fake()
        pen = models.HTTPModel("ollama", "small:8b", fake.url)
        self.assertEqual(pen.run("s", "Set ok to true.", models.TEST_SCHEMA), {"ok": True})
        body = fake.seen[-1]["body"]
        self.assertEqual(body["format"], models.TEST_SCHEMA)
        self.assertGreaterEqual(body["options"]["num_ctx"], 16384)
        self.assertEqual(pen.last["cost_usd"], 0.0)
        self.assertEqual(pen.last["tokens"], {"input_tokens": 11, "output_tokens": 7})

    def test_a_server_that_is_not_running_is_named_with_what_to_do(self):
        url = f"http://127.0.0.1:{free_port()}"
        pen = models.HTTPModel("ollama", "small:8b", url)
        state = pen.auth_status()
        self.assertEqual((state["ok"], state["code"]), (False, "model_offline"))
        self.assertIn("Open Ollama", state["problem"])
        with self.assertRaises(writer.WriterError) as ctx:
            pen.run("s", "p", models.TEST_SCHEMA)
        self.assertEqual(ctx.exception.code, "model_offline")

    def test_a_server_that_dies_is_noticed_and_coming_back_is_too(self):
        fake = self.fake()
        pen = models.HTTPModel("ollama", "small:8b", fake.url)
        self.assertTrue(pen.auth_status()["ok"])
        port = fake.port
        fake.close()
        self.assertEqual(pen.auth_status()["code"], "model_offline")
        self.fake(port=port)                                  # the same address answers again
        self.assertTrue(pen.auth_status()["ok"])

    def test_a_removed_model_is_reported_as_missing(self):
        fake = self.fake(names=["other:1b"])
        pen = models.HTTPModel("ollama", "small:8b", fake.url)
        state = pen.auth_status()
        self.assertEqual((state["ok"], state["code"]), (False, "model_missing"))
        self.assertIn("ollama pull small:8b", state["problem"])

    def test_latest_is_the_same_model(self):
        fake = self.fake(names=["small:latest"])
        self.assertTrue(models.HTTPModel("ollama", "small", fake.url).auth_status()["ok"])

    def test_not_enough_memory_says_so(self):
        fake = self.fake()
        fake.fail = (500, "model requires more system memory (9 GiB) than is available")
        with self.assertRaises(writer.WriterError) as ctx:
            models.HTTPModel("ollama", "small:8b", fake.url).run("s", "p", models.TEST_SCHEMA)
        self.assertEqual(ctx.exception.code, "model_memory")

    def test_web_lookups_are_refused_with_a_reason(self):
        fake = self.fake()
        with self.assertRaises(writer.WriterError) as ctx:
            models.HTTPModel("ollama", "small:8b", fake.url).run("s", "p", models.TEST_SCHEMA, web=True)
        self.assertEqual(ctx.exception.code, "no_search")
        self.assertEqual([s for s in fake.seen if s["path"] == "/api/chat"], [])

    def test_a_local_model_runs_one_call_at_a_time(self):
        pen = models.HTTPModel("ollama", "small:8b", self.fake().url)
        self.assertTrue(pen.slots.acquire(blocking=False))
        self.assertFalse(pen.slots.acquire(blocking=False))
        pen.slots.release()

    def test_a_stopping_writer_does_not_wait_for_a_slot(self):
        pen = models.HTTPModel("ollama", "small:8b", self.fake().url)
        pen.slots.acquire()
        threading.Timer(0.1, pen.stop).start()
        with self.assertRaises(writer.WriterError) as ctx:
            pen.run("s", "p", models.TEST_SCHEMA)
        self.assertEqual(ctx.exception.code, "stopped")
        pen.slots.release()


class OpenAICompatible(Base):
    def test_a_server_without_schemas_falls_back_and_remembers(self):
        fake = self.fake()
        fake.refuse_schema = True
        pen = models.HTTPModel("local", "small:8b", fake.url + "/v1")
        self.assertEqual(pen.run("s", "Set ok to true.", models.TEST_SCHEMA), {"ok": True})
        self.assertEqual(pen.format, "json_object")
        calls = [s for s in fake.seen if s["path"] == "/v1/chat/completions"]
        self.assertEqual(len(calls), 2)
        pen.run("s", "Set ok to true.", models.TEST_SCHEMA)
        self.assertEqual(len([s for s in fake.seen if s["path"] == "/v1/chat/completions"]), 3)

    def test_the_key_goes_in_the_header_and_a_bad_key_stays_reported(self):
        fake = self.fake(need_key=KEY)
        good = models.HTTPModel("custom", "small:8b", fake.url + "/v1", KEY)
        self.assertEqual(good.run("s", "Set ok to true.", models.TEST_SCHEMA), {"ok": True})
        self.assertEqual(fake.seen[-1]["auth"], f"Bearer {KEY}")
        bad = models.HTTPModel("custom", "small:8b", fake.url + "/v1", "sk-wrong-key-000")
        with self.assertRaises(writer.WriterError) as ctx:
            bad.run("s", "p", models.TEST_SCHEMA)
        self.assertEqual(ctx.exception.code, "key_rejected")
        self.assertEqual(bad.auth_status()["code"], "key_rejected")

    def test_a_redirect_is_never_followed_with_the_key(self):
        elsewhere = self.fake()
        fake = self.fake()
        fake.redirect = elsewhere.url + "/v1/chat/completions"
        pen = models.HTTPModel("custom", "small:8b", fake.url + "/v1", KEY)
        with self.assertRaises(writer.WriterError):
            pen.run("s", "p", models.TEST_SCHEMA)
        self.assertEqual(elsewhere.seen, [])

    def test_only_chat_models_are_offered(self):
        fake = self.fake(names=["chat-1", "whisper-1"])
        self.assertEqual(models.HTTPModel("local", "x", fake.url + "/v1").list_models(), ["chat-1"])

    def test_out_of_credit_is_its_own_message(self):
        fake = self.fake()
        fake.fail = (429, "You exceeded your current quota")
        with self.assertRaises(writer.WriterError) as ctx:
            models.HTTPModel("custom", "small:8b", fake.url + "/v1", KEY).run("s", "p", models.TEST_SCHEMA)
        self.assertEqual(ctx.exception.code, "usage_limit")


class Switching(Base):
    def manager(self, fake=None, **kw):
        pen = models.HTTPModel("ollama", "small:8b", fake.url) if fake else models.NoModel()
        st = self.study(pen)
        return st, models.Models(self.store, st, **kw)

    def test_a_choice_that_works_is_saved_and_used(self):
        fake = self.fake()
        st, m = self.manager()
        out = m.use({"provider": "ollama", "model": "small:8b", "url": fake.url})
        self.assertTrue(out["status"]["ok"])
        self.assertIsInstance(st.pen, models.HTTPModel)
        self.assertEqual(models.read_config(self.store)["active"]["model"], "small:8b")
        again = models.startup_writer(self.store)
        self.assertEqual((again.provider, again.model, again.url), ("ollama", "small:8b", fake.url))

    def test_the_page_shows_the_server_actually_in_use(self):
        fake = self.fake()
        st, m = self.manager()
        m.use({"provider": "ollama", "model": "small:8b", "url": fake.url})
        self.assertEqual(next(p for p in m.view()["providers"] if p["id"] == "ollama")["url"], fake.url)

    def test_a_choice_that_fails_changes_nothing(self):
        fake = self.fake()
        st, m = self.manager(fake)
        before = st.pen
        with self.assertRaises(writer.WriterError):
            m.use({"provider": "ollama", "model": "absent:1b", "url": fake.url})
        self.assertIs(st.pen, before)
        self.assertEqual(models.read_config(self.store), {})

    def test_a_key_is_private_and_never_shown(self):
        fake = self.fake(need_key=KEY)
        st, m = self.manager()
        out = m.use({"provider": "custom", "model": "small:8b", "url": fake.url + "/v1", "key": KEY})
        self.assertNotIn(KEY, json.dumps(out))
        self.assertNotIn(KEY, json.dumps(m.status()))
        self.assertTrue(next(p for p in out["providers"] if p["id"] == "custom")["has_key"])
        path = self.root / models.CONFIG
        self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
        self.assertIn(models.CONFIG, (self.root / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_a_saved_key_is_reused_and_can_be_removed_only_when_not_in_use(self):
        fake = self.fake(need_key=KEY)
        local = self.fake()
        st, m = self.manager()
        m.use({"provider": "custom", "model": "small:8b", "url": fake.url + "/v1", "key": KEY})
        self.assertEqual(m.list({"provider": "custom", "url": fake.url + "/v1"})["models"], ["small:8b"])
        with self.assertRaises(writer.WriterError) as ctx:
            m.forget({"provider": "custom"})
        self.assertEqual(ctx.exception.code, "in_use")
        m.use({"provider": "ollama", "model": "small:8b", "url": local.url})
        m.forget({"provider": "custom"})
        self.assertNotIn("custom", models.read_config(self.store).get("keys", {}))

    def test_a_key_that_could_break_a_header_is_refused(self):
        st, m = self.manager()
        with self.assertRaises(writer.WriterError) as ctx:
            m.use({"provider": "openai", "model": "gpt-x", "key": "sk-abc\r\nX-Evil: 1"})
        self.assertEqual(ctx.exception.code, "bad_key")

    def test_a_saved_choice_that_cannot_run_is_reported_not_swapped(self):
        self.root.mkdir(parents=True)
        (self.root / models.CONFIG).write_text(json.dumps({"active": {"provider": "openai", "model": "gpt-x"}}), "utf-8")
        with mock.patch.object(writer, "pick_writer") as claude:
            pen = models.startup_writer(self.store)
        claude.assert_not_called()
        self.assertIsInstance(pen, models.NoModel)
        self.assertEqual(pen.auth_status()["code"], "key_missing")

    def test_a_fixed_model_cannot_be_changed(self):
        fake = self.fake()
        st, m = self.manager(fake, locked=True)
        with self.assertRaises(writer.WriterError) as ctx:
            m.use({"provider": "ollama", "model": "small:8b", "url": fake.url})
        self.assertEqual(ctx.exception.code, "locked")

    def test_scan_finds_servers_and_says_which_are_down(self):
        fake = self.fake(names=["a:1b", "b:2b"])
        down = f"http://127.0.0.1:{free_port()}/v1"
        st = self.study(models.NoModel())
        m = models.Models(self.store, st, defaults={"ollama": fake.url, "lmstudio": down})
        rows = {r["provider"]: r for r in m.scan()["servers"]}
        self.assertEqual((rows["ollama"]["running"], rows["ollama"]["models"]), (True, ["a:1b", "b:2b"]))
        self.assertEqual((rows["lmstudio"]["running"], rows["lmstudio"]["code"]), (False, "model_offline"))


class InASession(Base):
    def test_with_no_model_a_session_explains_instead_of_starting(self):
        self.seed()
        st = self.study(models.NoModel())
        with self.assertRaises(study_mod.StudyError) as ctx:
            st.start("learn", "demo")
        self.assertEqual(ctx.exception.code, "no_model")

    def test_starting_while_the_model_is_down_says_so(self):
        self.seed()
        st = self.study(models.HTTPModel("ollama", "small:8b", f"http://127.0.0.1:{free_port()}"))
        with self.assertRaises(study_mod.StudyError) as ctx:
            st.start("learn", "demo")
        self.assertEqual(ctx.exception.code, "model_offline")

    def test_a_model_going_down_mid_session_keeps_the_session_and_retry_recovers(self):
        self.seed()
        fake = self.fake()
        pen = models.HTTPModel("ollama", "small:8b", fake.url)
        st = self.study(pen)
        sid = st.start("learn", "demo")["id"]
        self.wait(st, sid, ("ready",))
        live = FakeModelServer()
        self.fakes.append(live)
        down = f"http://127.0.0.1:{free_port()}"
        pen.url = down
        st.recheck()
        v = st.view(sid)
        for q in v["questions"]:
            st._get(sid)["questions"][q["id"]].update(state="failed", code="model_offline", error="down")
        q = v["questions"][0]["id"]
        with self.assertRaises((study_mod.StudyError, writer.WriterError)):
            st._call("question", "s", "Set ok to true.", models.TEST_SCHEMA)
        self.assertEqual(st.view(sid)["status"], "active")
        pen.url = live.url
        st.recheck()
        st.retry(sid, q)
        self.assertEqual(self.wait(st, sid, ("ready", "failed"))["questions"][0]["state"], "ready")

    def test_a_call_that_never_reached_a_model_is_not_usage(self):
        self.seed()
        pen = models.HTTPModel("ollama", "small:8b", f"http://127.0.0.1:{free_port()}")
        st = self.study(pen)
        st._auth = (engine.now(), {"ok": True})
        with self.assertRaises(writer.WriterError):
            st._call("question", "s", "p", models.TEST_SCHEMA)
        self.assertEqual(st.usage.today()["calls"], 0)

    def test_a_running_call_is_recorded_on_the_model_that_answered(self):
        self.seed()
        fake = self.fake()
        first = models.HTTPModel("ollama", "small:8b", fake.url)
        st = self.study(first)
        original = first.run

        def slow(*args, **kw):
            out = original(*args, **kw)
            st.set_pen(writer.FakeWriter())   # switched while it ran
            return out
        first.run = slow
        st._call("question", "s", "Set ok to true.", models.TEST_SCHEMA)
        rows = [json.loads(x) for x in (self.root / "web-usage.jsonl").read_text("utf-8").splitlines()]
        self.assertEqual(rows[-1]["model"], "small:8b")

    def test_stopping_also_stops_a_replaced_model(self):
        fake = self.fake()
        first = models.HTTPModel("ollama", "small:8b", fake.url)
        st = study_mod.Study(self.store, first)
        st.set_pen(models.HTTPModel("ollama", "small:8b", fake.url))
        st.stop()
        self.assertTrue(first.stopping.is_set())


class Routes(unittest.TestCase):
    """The page's API, signed in the way the dashboard is, with the helpers of the dashboard tests."""

    def configured(self, fake, **extra):
        self.seed(answers=False)
        config = {"active": {"provider": "ollama", "model": "small:8b", "url": fake.url}, **extra}
        (self.root / models.CONFIG).write_text(json.dumps(config), encoding="utf-8")
        self.addCleanup(fake.close)
        return self.serve(tutor=None, token="t" * 40)

    def test_status_and_page_data_never_carry_a_key(self):
        fake = FakeModelServer()
        server = self.configured(fake, keys={"openai": KEY})
        status, data = self.get_json(server, "/api/models")
        self.assertEqual(status, 200)
        self.assertNotIn(KEY, json.dumps(data))
        self.assertTrue(next(p for p in data["providers"] if p["id"] == "openai")["has_key"])
        self.assertTrue(self.get_json(server, "/api/models/status")[1]["ok"])
        self.assertNotIn(KEY, self.call(server, "GET", "/api/state")[2].decode("utf-8"))

    def test_a_change_must_come_from_the_page(self):
        fake = FakeModelServer()
        server = self.configured(fake)
        status, _ = self.post_json(server, "/api/models/use", {"provider": "ollama", "model": "small:8b"}, page=False)
        self.assertEqual(status, 403)

    def test_status_needs_sign_in(self):
        fake = FakeModelServer()
        server = self.configured(fake)
        self.assertEqual(self.call(server, "GET", "/api/models", auth=False)[0], 401)

    def test_the_offline_answer_reaches_the_page_with_its_code(self):
        fake = FakeModelServer()
        server = self.configured(fake)
        self.assertTrue(self.get_json(server, "/api/models/status")[1]["ok"])
        fake.close()
        server.app.study.recheck()
        state = self.get_json(server, "/api/models/status")[1]
        self.assertEqual((state["ok"], state["code"]), (False, "model_offline"))
        status, err = self.post_json(server, "/api/study/start", {"mode": "learn", "track": "demo"})
        self.assertEqual((status, err["code"]), (400, "model_offline"))

    def test_switching_from_the_page(self):
        fake = FakeModelServer()
        other = FakeModelServer(names=["bigger:12b"])
        self.addCleanup(other.close)
        server = self.configured(fake)
        status, out = self.post_json(server, "/api/models/use", {"provider": "local", "model": "bigger:12b",
                                                                 "url": other.url + "/v1"})
        self.assertEqual(status, 200)
        self.assertEqual((out["status"]["provider"], out["status"]["model"]), ("local", "bigger:12b"))
        self.assertIn("bigger:12b", self.get_json(server, "/api/state")[1]["app"]["study"]["writer"])


for _name, _value in vars(test_web.Web).items():
    if not _name.startswith(("test_", "__")):
        setattr(Routes, _name, _value)


if __name__ == "__main__":
    unittest.main()
