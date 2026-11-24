#!/usr/bin/env python3
"""The installer's model check. It runs before anything is installed or changed.

    polymath_setup.py check --out FILE [--llm claude:sonnet]   find what works, and confirm your default
    polymath_setup.py save --from FILE                         save that default, once the app is installed

A model counts only if it passes its check: Claude Code must be installed and signed in with an active subscription,
a local server must be running with a model, and an API key must be accepted by its provider. If none passes, the
install stops. A key found in the environment is sent to its own provider only after you choose it, and is never
written to the check's output file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polymath as engine               # noqa: E402
import polymath_models as models        # noqa: E402
import polymath_writer as writer        # noqa: E402

BEST = "Polymath works best with Claude. Other models can write wrong answers more often."
CLAUDE_CHOICES = (("opus", "Claude Opus"), ("sonnet", "Claude Sonnet"), ("haiku", "Claude Haiku"))
KEY_VARS = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "openrouter": ("OPENROUTER_API_KEY",)}
LOGIN = "run claude, type /login and choose your Claude subscription"
CLAUDE_PROBLEM = {
    "not_installed": ("not installed", "Install Claude Code from https://claude.com/claude-code, then " + LOGIN + "."),
    "signed_out": ("not signed in", "In a terminal, " + LOGIN + "."),
    "wrong_login": ("signed in with an API key or another provider, not a subscription", "In a terminal, " + LOGIN + "."),
    "no_subscription": ("no active subscription", "Choose a plan at https://claude.com/pricing, then " + LOGIN + "."),
    "error": ("couldn't be checked", "Check that claude starts in a terminal."),
}
SHOWN_NAMES = 12


def detect(env=None) -> dict:
    """What can be used here. Costs nothing and sends no key anywhere."""
    env = os.environ if env is None else env
    options, blocked, fixes = [], [], []
    claude = models.claude_state(writer.ClaudeCLI("opus", None))
    if claude["state"] == "ready":
        plan = f"{claude['plan']} plan" if claude["plan"] else "your plan"
        for index, (model, label) in enumerate(CLAUDE_CHOICES):
            options.append({"provider": "claude", "model": model, "url": "", "label": label,
                            "note": plan + (", recommended" if index == 0 else "")})
    else:
        why, fix = CLAUDE_PROBLEM.get(claude["state"], CLAUDE_PROBLEM["error"])
        blocked.append({"label": "Claude", "why": why})
        fixes.append(f"To use Claude: {fix}")

    local = [("ollama", models.PROVIDERS["ollama"]["url"]), ("lmstudio", models.PROVIDERS["lmstudio"]["url"])]
    if env.get("POLYMATH_LLM_URL"):
        local.append(("local", env["POLYMATH_LLM_URL"]))
    for pid, address in local:
        if not models.offered(pid):
            continue
        spec = models.PROVIDERS[pid]
        try:
            base = models.clean_url(address, spec["protocol"])
            names = models.HTTPModel(pid, "scan", base).list_models(models.CHECK_SECONDS)
        except writer.WriterError:
            blocked.append({"label": spec["label"], "why": "not running"})
            continue
        if not names:
            blocked.append({"label": spec["label"], "why": "running, but has no models"})
        for name in names:
            options.append({"provider": pid, "model": name, "url": base, "label": f"{spec['label']}: {name}",
                            "note": "on this computer" if models.where(base) == "device" else "on your network"})

    for pid, names in KEY_VARS.items():
        var = next((name for name in names if env.get(name)), None)
        if not var or pid not in models.PROVIDERS:
            continue
        if pid == "anthropic" and not models.has_sdk():
            blocked.append({"label": "Anthropic API key", "why": "needs the anthropic Python package"})
        elif models.offered(pid):
            options.append({"provider": pid, "model": "", "url": "", "key_env": var,
                            "label": f"{models.PROVIDERS[pid]['label']} API key",
                            "note": f"from {var}, billed to your key, checked if you choose it"})
    return {"options": options, "blocked": blocked, "fixes": fixes}


def key_models(option: dict, env) -> list:
    """Ask the provider which models this key can use. A free call, made only once you have chosen it."""
    pid = option["provider"]
    key = env.get(option["key_env"], "").strip()
    if not models.KEY_TEXT.match(key):
        raise writer.WriterError("That API key doesn't look valid.", "bad_key")
    if models.PROVIDERS[pid]["protocol"] == "anthropic":
        names = models._anthropic_names(key)
    else:
        spec = models.PROVIDERS[pid]
        names = models.HTTPModel(pid, "list", models.clean_url(spec["url"], spec["protocol"]), key).list_models()
    if not names:
        raise writer.WriterError("That key has no models to choose from.", "failed")
    return names


def current_default(store) -> tuple:
    active = models.read_config(store).get("active")
    return (active.get("provider"), active.get("model")) if isinstance(active, dict) else (None, None)


def _result(option: dict, model: str = None) -> dict:
    out = {"provider": option["provider"], "model": model or option["model"], "url": option.get("url") or ""}
    if option.get("key_env"):
        out["key_env"] = option["key_env"]
    return out


def _pick_named(names: list, ask, say) -> str:
    for index, name in enumerate(names[:SHOWN_NAMES], 1):
        say(f"  {index}. {name}")
    if len(names) > SHOWN_NAMES:
        say(f"  ...and {len(names) - SHOWN_NAMES} more. You can type any model's name.")
    while True:
        answer = ask("Which model? [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= min(len(names), SHOWN_NAMES):
            return names[int(answer) - 1]
        if answer in names:
            return answer
        say("Enter a number from the list, or a model's exact name.")


def from_flag(wanted: str, found: dict, env, say):
    """--llm provider[:model], for an install with nobody at the keyboard."""
    provider, _, model = wanted.strip().partition(":")
    rows = [o for o in found["options"] if o["provider"] == provider]
    if not rows:
        say(f"{wanted} isn't available on this computer.")
        return None
    if rows[0].get("key_env"):
        try:
            names = key_models(rows[0], env)
        except writer.WriterError as exc:
            say(str(exc))
            return None
        if model and model not in names:
            say(f"{model} isn't available with that key.")
            return None
        return _result(rows[0], model or names[0])
    match = [o for o in rows if o["model"] == model] if model else rows[:1]
    if not match:
        say(f"{wanted} isn't available on this computer.")
        return None
    return _result(match[0])


def choose(found: dict, ask, say, env=None, current=(None, None)):
    """Show what passed, and confirm the default with the person installing. None means stop."""
    env = os.environ if env is None else env
    options = found["options"]
    default = next((i for i, o in enumerate(options, 1) if (o["provider"], o["model"]) == tuple(current)), 1)
    say("")
    say(BEST)
    say("")
    say("Available on this computer:")
    for index, option in enumerate(options, 1):
        say(f"  {index}. {option['label']} ({option['note']})")
    if found["blocked"]:
        say("")
        say("Not available:")
        for row in found["blocked"]:
            say(f"  - {row['label']}: {row['why']}")
    for fix in found["fixes"]:
        say(f"  {fix}")
    say("")
    while True:
        answer = ask(f"Which should be your default? [{default}]: ").strip() or str(default)
        if not answer.isdigit() or not 1 <= int(answer) <= len(options):
            say(f"Enter a number from 1 to {len(options)}.")
            continue
        option = options[int(answer) - 1]
        model = None
        if option.get("key_env"):
            try:
                names = key_models(option, env)
            except writer.WriterError as exc:
                say(f"{option['label']} didn't pass: {exc}")
                continue
            model = _pick_named(names, ask, say)
        if option["provider"] not in ("claude", "anthropic"):
            name = model or option["model"]
            sure = ask(f"{BEST} Use {name} as your default? [y/N]: ").strip().lower()
            if sure not in ("y", "yes"):
                continue
        return _result(option, model)


def run_check(out: str, wanted: str = None, env=None, ask=input, say=print, interactive: bool = None) -> int:
    env = os.environ if env is None else env
    say("Checking which AI models you can use...")
    found = detect(env)
    if not found["options"]:
        say("")
        say("Polymath needs a working AI model, and none passed the check. Nothing was installed.")
        say("")
        for row in found["blocked"]:
            say(f"  - {row['label']}: {row['why']}")
        for fix in found["fixes"]:
            say(f"  {fix}")
        say("  To use another model, start Ollama or LM Studio, or set your provider's API key, then run this again.")
        return 1
    interactive = sys.stdin.isatty() if interactive is None else interactive
    try:
        if wanted:
            choice = from_flag(wanted, found, env, say)
        elif interactive:
            choice = choose(found, ask, say, env, current_default(engine.Store()))
        else:
            say("Your default model has to be confirmed. Run this in a terminal, or choose one with --llm claude.")
            choice = None
    except (EOFError, KeyboardInterrupt):
        say("")
        choice = None
    if not choice:
        say("Nothing was installed.")
        return 1
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(choice, f)
    say(f"Default model: {choice['model']} ({models.PROVIDERS[choice['provider']]['label']})")
    return 0


def run_save(path: str, env=None) -> int:
    env = os.environ if env is None else env
    choice = json.loads(Path(path).read_text(encoding="utf-8"))
    pid = choice["provider"]
    key = env.get(choice["key_env"], "").strip() if choice.get("key_env") else ""
    mask = os.umask(0o077)
    try:
        store = engine.Store()
        store.root.mkdir(parents=True, exist_ok=True)
        engine.keep_private(store.root)

        def change(config):
            config["active"] = {"provider": pid, "model": choice["model"], "url": choice.get("url") or ""}
            if key:
                config.setdefault("keys", {})[pid] = key
        models.Models(store, None)._save(change)
    finally:
        os.umask(mask)
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Polymath's installer model check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--out", required=True)
    check.add_argument("--llm", default=os.environ.get("POLYMATH_LLM") or None)
    save = sub.add_parser("save")
    save.add_argument("--from", dest="source", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        return run_check(args.out, args.llm)
    return run_save(args.source)


if __name__ == "__main__":
    sys.exit(main())
