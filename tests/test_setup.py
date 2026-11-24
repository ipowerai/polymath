"""The installer's model check and the polymath command. Claude Code and every model are stand-ins: nothing real runs."""
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import polymath_control as control      # noqa: E402
import polymath_models as models        # noqa: E402
import polymath_setup as setup          # noqa: E402
import polymath_writer as writer        # noqa: E402

KEY = "sk-test-do-not-leak-12345"
READY = {"state": "ready", "installed": True, "signed_in": True, "plan": "Max"}


def claude(state="ready", plan=""):
    if state == "ready":
        value = {**READY, "plan": plan or "Max"}
    else:
        value = {"state": state, "installed": state != "not_installed", "plan": "",
                 "signed_in": state in ("wrong_login", "no_subscription")}
    return mock.patch.object(models, "claude_state", lambda pen: value)


def local(**servers):
    """Which local servers answer: ollama=["a"], and anything left out is not running."""
    def listing(self, timeout=None):
        if self.provider in servers:
            return servers[self.provider]
        raise writer.WriterError("not running", "model_offline")
    return mock.patch.object(models.HTTPModel, "list_models", listing)


class Script:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, prompt=""):
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"POLYMATH_HOME": str(Path(self.tmp.name) / "data")})
        env.start()
        self.addCleanup(env.stop)
        self.out = str(Path(self.tmp.name) / "choice.json")
        self.lines = []

    def check(self, *answers, env=None, wanted=None, interactive=True):
        self.ask = Script(*answers)
        return setup.run_check(self.out, wanted, env=env or {}, ask=self.ask, say=self.lines.append, interactive=interactive)

    @property
    def text(self):
        return "\n".join(self.lines)

    def choice(self):
        return json.loads(Path(self.out).read_text(encoding="utf-8"))


class Gate(Base):
    def test_no_working_model_stops_the_install_for_each_claude_problem(self):
        for state, words in (("not_installed", "not installed"), ("signed_out", "not signed in"),
                             ("wrong_login", "API key or another provider"), ("no_subscription", "no active subscription"),
                             ("error", "couldn't be checked")):
            self.lines.clear()
            with claude(state), local():
                self.assertEqual(self.check(), 1, state)
            self.assertIn(words, self.text)
            self.assertIn("Nothing was installed", self.text)
            self.assertFalse(Path(self.out).exists())
            self.assertEqual(self.ask.asked, [])

    def test_a_running_server_with_no_models_does_not_pass(self):
        with claude("not_installed"), local(ollama=[]):
            self.assertEqual(self.check(), 1)
        self.assertIn("running, but has no models", self.text)

    def test_an_unconfirmed_install_stops(self):
        with claude(), local():
            self.assertEqual(self.check(interactive=False), 1)
            self.assertIn("confirmed", self.text)
            self.assertEqual(self.check(), 1)  # closed the prompt
        self.assertFalse(Path(self.out).exists())

    def test_a_free_or_missing_plan_is_not_a_subscription(self):
        script = Path(self.tmp.name) / "claude"
        for plan in ("free", None, ""):
            script.write_text("#!/bin/sh\necho '" + json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                                "apiProvider": "firstParty", "subscriptionType": plan})
                              + "'\n")
            script.chmod(0o700)
            self.assertEqual(models.claude_state(writer.ClaudeCLI("opus", None, binary=str(script)))["state"],
                             "no_subscription")

    def test_a_new_paid_plan_name_is_not_blocked(self):
        script = Path(self.tmp.name) / "claude"
        script.write_text("#!/bin/sh\necho '" + json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                            "apiProvider": "firstParty", "subscriptionType": "ultra"})
                          + "'\n")
        script.chmod(0o700)
        self.assertEqual(models.claude_state(writer.ClaudeCLI("opus", None, binary=str(script)))["state"], "ready")


class Choosing(Base):
    def test_claude_is_first_and_recommended_and_says_it_works_best(self):
        with claude(), local(ollama=["qwen3:8b"]):
            self.assertEqual(self.check(""), 0)
        self.assertEqual(self.choice(), {"provider": "claude", "model": "opus", "url": ""})
        self.assertIn(setup.BEST, self.text)
        self.assertIn("1. Claude Opus (Max plan, recommended)", self.text)
        self.assertIn("Ollama: qwen3:8b", self.text)

    def test_another_model_needs_a_second_confirmation(self):
        with claude(), local(ollama=["qwen3:8b"]):
            self.assertEqual(self.check("4", "n", "1"), 0)
            self.assertEqual(self.choice()["provider"], "claude")
            self.assertEqual(self.check("4", "y"), 0)
        self.assertEqual((self.choice()["provider"], self.choice()["model"]), ("ollama", "qwen3:8b"))

    def test_without_claude_another_model_can_be_the_default(self):
        with claude("not_installed"), local(lmstudio=["local-model"]):
            self.assertEqual(self.check("1", "y"), 0)
        self.assertEqual(self.choice()["provider"], "lmstudio")
        self.assertIn("Claude: not installed", self.text)
        self.assertIn("To use Claude", self.text)

    def test_a_wrong_number_asks_again(self):
        with claude(), local():
            self.assertEqual(self.check("9", "abc", "2"), 0)
        self.assertEqual(self.choice()["model"], "sonnet")

    def test_the_current_default_is_kept_on_reinstall(self):
        data = Path(os.environ["POLYMATH_HOME"])
        data.mkdir()
        (data / "models.json").write_text(json.dumps({"active": {"provider": "claude", "model": "haiku"}}))
        with claude(), local():
            self.assertEqual(self.check(""), 0)
        self.assertEqual(self.choice()["model"], "haiku")

    def test_llm_flag_needs_no_keyboard(self):
        with claude(), local(ollama=["qwen3:8b"]):
            self.assertEqual(self.check(wanted="claude:sonnet", interactive=False), 0)
            self.assertEqual(self.choice()["model"], "sonnet")
            self.assertEqual(self.check(wanted="ollama:qwen3:8b", interactive=False), 0)
            self.assertEqual(self.choice()["model"], "qwen3:8b")
            self.assertEqual(self.check(wanted="ollama:missing", interactive=False), 1)
            self.assertEqual(self.check(wanted="lmstudio", interactive=False), 1)


class Keys(Base):
    def test_a_key_is_not_sent_anywhere_until_it_is_chosen(self):
        with claude(), local(), mock.patch.object(setup, "key_models", side_effect=AssertionError("sent")):
            found = setup.detect({"OPENAI_API_KEY": KEY})
            self.assertEqual(self.check("1", env={"OPENAI_API_KEY": KEY}), 0)
        self.assertTrue(any(o.get("key_env") == "OPENAI_API_KEY" for o in found["options"]))

    def test_a_rejected_key_does_not_pass(self):
        rejected = writer.WriterError("OpenAI rejected the API key.", "key_rejected")
        with claude("not_installed"), local(), mock.patch.object(setup, "key_models", side_effect=rejected):
            self.assertEqual(self.check("1", env={"OPENAI_API_KEY": KEY}), 1)
        self.assertIn("didn't pass", self.text)
        self.assertFalse(Path(self.out).exists())

    def test_the_key_never_reaches_the_choice_file_and_is_saved_privately(self):
        env = {"OPENAI_API_KEY": KEY}
        with claude("not_installed"), local(), mock.patch.object(setup, "key_models", return_value=["gpt-a", "gpt-b"]):
            self.assertEqual(self.check("1", "2", "y", env=env), 0)
        raw = Path(self.out).read_text(encoding="utf-8")
        self.assertNotIn(KEY, raw)
        self.assertNotIn(KEY, self.text)
        self.assertEqual(self.choice()["model"], "gpt-b")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o600)
        self.assertEqual(setup.run_save(self.out, env), 0)
        saved = Path(os.environ["POLYMATH_HOME"]) / "models.json"
        data = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(data["active"]["model"], "gpt-b")
        self.assertEqual(data["keys"]["openai"], KEY)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)

    def test_an_anthropic_key_without_its_package_is_not_offered(self):
        with claude("not_installed"), local(), mock.patch.object(models, "has_sdk", lambda: False):
            found = setup.detect({"ANTHROPIC_API_KEY": KEY})
        self.assertEqual(found["options"], [])
        self.assertIn("needs the anthropic Python package", json.dumps(found))


class InstallerOrder(unittest.TestCase):
    def test_the_check_runs_before_anything_changes(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        body = text[text.index("main() {"):]
        for later in ("stop_running", "copy_app", "start_app"):
            self.assertLess(body.index("choose_model"), body.index(later), later)
        windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertLess(windows.index("polymath_setup.py') , 'check'".replace(" ,", ",")), windows.index("Stop-Running\n        New-Item"))


class Command(unittest.TestCase):
    def run_it(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = control.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_help_lists_every_command(self):
        code, out, _ = self.run_it("help")
        self.assertEqual(code, 0)
        for word in control.COMMANDS:
            self.assertIn(f"polymath {word}", out)

    def test_an_unknown_command_changes_nothing(self):
        with mock.patch.object(control, "manager", side_effect=AssertionError("touched")):
            self.assertEqual(self.run_it("launch")[0], 2)
            self.assertEqual(self.run_it("stop", "now")[0], 2)

    def test_start_refuses_a_port_another_program_holds(self):
        service = mock.Mock()
        service.pid.return_value = None
        with mock.patch.object(control, "manager", return_value=service), mock.patch.object(control, "answering", return_value=True):
            code, _, err = self.run_it("start")
        self.assertEqual(code, 1)
        self.assertIn("another program", err)
        service.start.assert_not_called()

    def test_status_says_when_it_is_stopped(self):
        service = mock.Mock()
        service.pid.return_value = None
        with mock.patch.object(control, "manager", return_value=service):
            code, out, _ = self.run_it("status")
        self.assertEqual(code, 3)
        self.assertIn("stopped", out)

    def test_stop_always_turns_off_the_login_start(self):
        service = mock.Mock()
        service.pid.return_value = None
        with mock.patch.object(control, "manager", return_value=service), mock.patch.object(control, "answering", return_value=False):
            self.assertEqual(self.run_it("stop")[0], 0)
        service.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
