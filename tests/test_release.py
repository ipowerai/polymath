"""What this release promises: its model choices, the benchmark off, the current PD1 weights, and a clean download."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packaging"))
import build_release                    # noqa: E402
import polymath as engine               # noqa: E402
import polymath_bench as bench          # noqa: E402
import polymath_models as models        # noqa: E402
import polymath_study as study_mod      # noqa: E402
import polymath_writer as writer        # noqa: E402


class ModelChoices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"POLYMATH_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.store = engine.Store()
        self.study = study_mod.Study(self.store, writer.FakeWriter())
        self.addCleanup(self.study.stop)
        self.manager = models.Models(self.store, self.study)

    def test_every_provider_is_offered_and_the_benchmark_stays_off(self):
        self.assertEqual(models.ENABLED, tuple(models.PROVIDERS))
        self.assertFalse(bench.ENABLED)
        with mock.patch.object(models, "has_sdk", lambda: False):
            self.assertNotIn("anthropic", [p["id"] for p in self.manager.view()["providers"]])
        self.assertFalse(self.manager.view()["benchmark"])

    def test_a_switched_off_provider_is_refused_and_never_contacted(self):
        with mock.patch.object(models, "ENABLED", ("claude",)), \
                mock.patch.object(models.HTTPModel, "_send", side_effect=AssertionError("contacted")):
            for pid in set(models.PROVIDERS) - {"claude"}:
                with self.assertRaises(writer.WriterError):
                    self.manager.use({"provider": pid, "model": "m", "url": "http://127.0.0.1:9/v1", "key": "k" * 12})
                with self.assertRaises(writer.WriterError):
                    self.manager.list({"provider": pid, "url": "http://127.0.0.1:9/v1", "key": "k" * 12})
            self.assertEqual(self.manager.scan(), {"servers": []})

    def test_a_saved_switched_off_provider_is_reported_not_used(self):
        (self.store.root / models.CONFIG).write_text(json.dumps(
            {"active": {"provider": "ollama", "model": "m", "url": "http://127.0.0.1:11434"}}), encoding="utf-8")
        with mock.patch.object(models, "ENABLED", ("claude",)):
            pen = models.startup_writer(self.store)
        self.assertIsInstance(pen, models.NoModel)
        self.assertEqual(pen.code, "no_model")


class ClaudeAccount(unittest.TestCase):
    """The subscription panel. Claude Code is a stand-in here: no test runs the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"POLYMATH_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.store = engine.Store()

    def manager(self, pen=None, **kw):
        study = study_mod.Study(self.store, pen or models.NoModel())
        self.addCleanup(study.stop)
        return models.Models(self.store, study, **kw)

    @staticmethod
    def stand_in(result=None, installed=True, calls=None):
        class Stand(writer.ClaudeCLI):
            def __init__(self, *args, **kw):
                super().__init__(*args, binary="/stand-in/claude")

            def available(self):
                return installed

            def auth_status(self):
                if calls is not None:
                    calls.append(1)
                return dict(result or {})
        return mock.patch.object(writer, "ClaudeCLI", Stand)

    def test_each_state_says_what_is_true(self):
        cases = [({"ok": True, "plan": "max"}, True, ("ready", True, True, "Max")),
                 ({"ok": False, "code": "signed_out"}, True, ("signed_out", True, False, "")),
                 ({"ok": False, "code": "not_subscription", "plan": None}, True, ("wrong_login", True, True, "")),
                 ({"ok": False, "code": "no_subscription", "plan": None}, True, ("no_subscription", True, True, "")),
                 ({"ok": False, "code": "writer_missing"}, True, ("error", True, False, "")),
                 (None, False, ("not_installed", False, False, ""))]
        for result, installed, want in cases:
            with self.stand_in(result, installed):
                out = self.manager(writer.FakeWriter()).claude()
            self.assertEqual((out["state"], out["installed"], out["signed_in"], out["plan"]), want)

    @unittest.skipUnless(sys.platform != "win32", "uses a shell script as the stand-in")
    def test_email_and_organization_never_reach_the_page(self):
        script = Path(self.tmp.name) / "claude"
        script.write_text("#!/bin/sh\necho '" + json.dumps(
            {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max",
             "email": "private@example.com", "orgId": "org-secret", "orgName": "Private Org"}) + "'\n")
        script.chmod(0o700)
        real = writer.ClaudeCLI
        class Stand(real):
            def __init__(self, *args, **kw):
                super().__init__(*args, binary=str(script))
        with mock.patch.object(writer, "ClaudeCLI", Stand):
            out = json.dumps(self.manager(writer.FakeWriter()).claude())
        self.assertIn('"plan": "Max"', out)
        for private in ("private@example.com", "org-secret", "Private Org"):
            self.assertNotIn(private, out)

    def test_an_unknown_plan_name_is_not_shown(self):
        self.assertEqual(models.plan_label("max"), "Max")
        for odd in ("", None, "free", "<b>x</b>", "someone@example.com"):
            self.assertEqual(models.plan_label(odd), "")

    def test_signing_in_connects_it_when_nothing_else_was_chosen(self):
        m = self.manager()
        with self.stand_in({"ok": True, "plan": "pro"}):
            m.claude()
            self.assertIsInstance(m.study.pen, writer.ClaudeCLI)

    def test_a_saved_choice_is_never_swapped(self):
        (self.store.root / models.CONFIG).write_text(json.dumps({"active": {"provider": "anthropic", "model": "m"}}),
                                                     encoding="utf-8")
        pen = models.NoModel("saved key can't run")
        m = self.manager(pen)
        with self.stand_in({"ok": True, "plan": "pro"}):
            m.claude()
        self.assertIs(m.study.pen, pen)

    def test_a_demo_never_asks_claude_code(self):
        calls = []
        with self.stand_in({"ok": True, "plan": "max"}, calls=calls):
            out = self.manager(writer.FakeWriter(), locked=True).claude()
        self.assertEqual((out["state"], calls), ("locked", []))


class Pd1Weights(unittest.TestCase):
    def test_blueprint_uses_the_current_weights(self):
        data = json.loads((ROOT / "blueprints" / "salesforce-platform-developer-1.json").read_text(encoding="utf-8"))
        self.assertEqual({d["id"]: d["weight"] for d in data["domains"]},
                         {"developer-fundamentals": 0.27, "process-automation-logic": 0.28,
                          "user-interface": 0.25, "testing-debugging-deployment": 0.20})
        self.assertFalse(data["exam"]["verified"])


class Download(unittest.TestCase):
    def test_the_version_is_the_same_everywhere(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], engine.VERSION)

    def test_the_zip_holds_the_app_and_nothing_private(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "polymath.zip"
            result = build_release.build(out)
            first = out.read_bytes()
            self.assertEqual(build_release.build(out)["sha256"], result["sha256"])
            self.assertEqual(out.read_bytes(), first)
            with zipfile.ZipFile(out) as bundle:
                names = bundle.namelist()
                license_text = bundle.read("polymath/LICENSE").decode("utf-8")
        for needed in ("LICENSE", "NOTICE", "install.sh", "install.ps1", "scripts/polymath.py", "web/app.js",
                       "blueprints/salesforce-platform-developer-1.json", "skills/learn/SKILL.md"):
            self.assertIn(f"polymath/{needed}", names)
        self.assertFalse([n for n in names if "/tests/" in n or "__pycache__" in n or n.endswith(".pyc")])
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)

    def test_a_private_file_stops_the_build(self):
        for name in ("scripts/.env", "web/models.json", "scripts/web-token", "references/events.jsonl"):
            self.assertTrue(build_release.NEVER.search(name), name)
        self.assertTrue(build_release.SECRET.search(b"key = sk-ant-" + b"a" * 24))


if __name__ == "__main__":
    unittest.main()
