"""What this release promises: Claude only, the benchmark off, the current PD1 weights, and a clean download."""
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


class ClaudeOnly(unittest.TestCase):
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

    def test_only_claude_is_offered(self):
        self.assertEqual(models.ENABLED, ("claude", "anthropic"))
        self.assertFalse(bench.ENABLED)
        with mock.patch.object(models, "has_sdk", lambda: True):
            self.assertEqual([p["id"] for p in self.manager.view()["providers"]], ["claude", "anthropic"])
        with mock.patch.object(models, "has_sdk", lambda: False):
            self.assertEqual([p["id"] for p in self.manager.view()["providers"]], ["claude"])
        self.assertFalse(self.manager.view()["benchmark"])

    def test_other_providers_are_refused_and_never_contacted(self):
        for pid in set(models.PROVIDERS) - set(models.ENABLED):
            with mock.patch.object(models.HTTPModel, "_send", side_effect=AssertionError("contacted")):
                with self.assertRaises(writer.WriterError):
                    self.manager.use({"provider": pid, "model": "m", "url": "http://127.0.0.1:9/v1", "key": "k" * 12})
                with self.assertRaises(writer.WriterError):
                    self.manager.list({"provider": pid, "url": "http://127.0.0.1:9/v1", "key": "k" * 12})
        self.assertEqual(self.manager.scan(), {"servers": []})

    def test_a_saved_other_provider_is_reported_not_used(self):
        (self.store.root / models.CONFIG).write_text(json.dumps(
            {"active": {"provider": "ollama", "model": "m", "url": "http://127.0.0.1:11434"}}), encoding="utf-8")
        pen = models.startup_writer(self.store)
        self.assertIsInstance(pen, models.NoModel)
        self.assertEqual(pen.code, "no_model")


class Pd1Weights(unittest.TestCase):
    def test_blueprint_uses_the_current_weights(self):
        data = json.loads((ROOT / "blueprints" / "salesforce-platform-developer-1.json").read_text(encoding="utf-8"))
        self.assertEqual({d["id"]: d["weight"] for d in data["domains"]},
                         {"developer-fundamentals": 0.27, "process-automation-logic": 0.28,
                          "user-interface": 0.25, "testing-debugging-deployment": 0.20})
        self.assertFalse(data["exam"]["verified"])


class Download(unittest.TestCase):
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
