"""The AI model that writes questions. This release offers Claude only: Claude Code, or an Anthropic API key.
The other providers stay in the code, switched off by ENABLED, until they have been benchmarked.

Standard library only (an Anthropic key goes through the `anthropic` package, as polymath_writer does).

Keys are kept in the data folder's models.json, readable by you alone and out of git. They are never sent to
the page, never logged, and go only to the address they were saved for. A connection never follows a redirect,
so a server can't pass a key on to somewhere else.
"""
from __future__ import annotations

import http.client
import importlib.util
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import polymath as engine
import polymath_writer as writer

CONFIG = "models.json"
MODEL_NAME = re.compile(r"^[\w.:/@+\-]{1,160}$")
KEY_TEXT = re.compile(r"^[\x21-\x7e]{8,512}$")  # printable, no spaces: it goes in a request header
MAX_RESPONSE = 8 * 1024 * 1024
CONNECT_SECONDS = 4
CHECK_SECONDS = 3       # a health check: short, so a dead server is noticed quickly
LIST_SECONDS = 15
TEST_SECONDS = 180      # a first call may have to load the model into memory
LOCAL_SECONDS = 420     # a local model can be slow; the session's own stale-writing limit is longer
CLOUD_AT_ONCE = 3
NOT_CHAT = ("embed", "whisper", "tts", "dall-e", "moderation", "image", "audio", "realtime", "transcribe", "rerank")
CLAUDE_MODELS = ("opus", "sonnet", "haiku")

PROVIDERS = {
    "claude": {"label": "Claude Code", "kind": "subscription", "protocol": "claude", "url": ""},
    "ollama": {"label": "Ollama", "kind": "local", "protocol": "ollama", "url": "http://127.0.0.1:11434"},
    "lmstudio": {"label": "LM Studio", "kind": "local", "protocol": "openai", "url": "http://127.0.0.1:1234/v1"},
    "local": {"label": "Another local server", "kind": "local", "protocol": "openai", "url": "", "needs_url": True},
    "anthropic": {"label": "Anthropic", "kind": "key", "protocol": "anthropic", "url": ""},
    "openai": {"label": "OpenAI", "kind": "key", "protocol": "openai", "url": "https://api.openai.com/v1"},
    "gemini": {"label": "Google Gemini", "kind": "key", "protocol": "openai",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "openrouter": {"label": "OpenRouter", "kind": "key", "protocol": "openai", "url": "https://openrouter.ai/api/v1"},
    "custom": {"label": "Another API", "kind": "key", "protocol": "openai", "url": "", "needs_url": True},
}
ENABLED = ("claude", "anthropic")  # what this release offers
TEST_SCHEMA = writer._obj({"ok": {"type": "boolean"}})


def has_sdk() -> bool:
    return importlib.util.find_spec("anthropic") is not None


def offered(pid: str) -> bool:
    """Whether this provider can be chosen here. An API key needs the optional anthropic package."""
    return pid in PROVIDERS and pid in ENABLED and (pid != "anthropic" or has_sdk())


# --- addresses ---------------------------------------------------------------------------------------
def _ip(host: str):
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _place_of(ip) -> str:
    if ip.is_loopback:
        return "device"
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "refused"  # never a model server; link-local is where cloud metadata services live
    return "network" if ip.is_private else "internet"


def where(url: str) -> str:
    """Where an address is: device, network, internet, or refused."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return "device"
    ip = _ip(host)
    if ip is not None:
        return _place_of(ip)
    if host.endswith(".local"):
        return "network"
    try:
        found = {_place_of(_ip(row[4][0].split("%")[0])) for row in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError, AttributeError):
        return "internet"  # can't tell, so the stricter rules apply
    if "refused" in found:
        return "refused"
    return "internet" if "internet" in found or not found else "network"


def clean_url(raw, protocol: str = "openai") -> str:
    """A base address the model calls can use, or a clear refusal."""
    text = str(raw or "").strip().rstrip("/")
    if not text or len(text) > 300 or any(ord(c) < 33 for c in text):
        raise writer.WriterError("Enter the server's address, like http://127.0.0.1:8080/v1.", "bad_url")
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        raise writer.WriterError("That address isn't valid.", "bad_url")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise writer.WriterError("The address must start with http:// or https://.", "bad_url")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise writer.WriterError("Leave names, passwords and ?options out of the address.", "bad_url")
    path = parts.path.rstrip("/")
    if protocol == "ollama":
        path = re.sub(r"/(v1|api)$", "", path)
    elif protocol == "openai" and not path:
        path = "/v1"
    netloc = parts.netloc
    out = urllib.parse.urlunsplit((parts.scheme, netloc, path, "", ""))
    place = where(out)
    if place == "refused":
        raise writer.WriterError("That address can't be a model server.", "bad_url")
    if place == "internet" and parts.scheme != "https":
        raise writer.WriterError("An address on the internet must use https, so your study data and key stay "
                                 "encrypted.", "bad_url")
    return out if port is None or port > 0 else text


def parse_json(text) -> dict:
    """The JSON object in a model's reply, allowing for reasoning tags and a code fence around it."""
    body = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S | re.I).strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    for candidate in (body, body[body.find("{"):body.rfind("}") + 1] if "{" in body and "}" in body else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise writer.WriterError("The model didn't return valid JSON.", "invalid")


def _chat_names(names) -> list:
    out = {str(n) for n in names if isinstance(n, str) and MODEL_NAME.match(n)}
    return sorted(n for n in out if not any(word in n.lower() for word in NOT_CHAT))


def _same_model(a: str, b: str) -> bool:
    strip = lambda n: n[:-len(":latest")] if n.endswith(":latest") else n  # noqa: E731
    return strip(a) == strip(b)


# --- writers -----------------------------------------------------------------------------------------
class NoModel(writer.Backend):
    """Nothing chosen yet (or a saved choice that can't be used): the app runs and says what to do."""

    name = "none"
    model = ""
    auth_ttl = (30, 30)

    def __init__(self, problem: str = None, code: str = "no_model"):
        super().__init__()
        self.problem = problem or "Choose an AI model to write your questions."
        self.code = code

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return "no AI model yet"

    def auth_status(self) -> dict:
        return {"ok": False, "code": self.code, "problem": self.problem}

    def run(self, system, prompt, schema=None, web=False, timeout=None, effort=None):
        raise writer.WriterError(self.problem, self.code)


class HTTPModel(writer.Backend):
    """A model reached over HTTP: Ollama's own API, or any OpenAI-compatible one."""

    def __init__(self, provider: str, model: str, url: str, key: str = ""):
        super().__init__()
        self.provider, self.model, self.url, self.key = provider, model, url.rstrip("/"), key
        self.spec = PROVIDERS[provider]
        self.name = provider
        self.label = self.spec["label"]
        self.local = self.spec["kind"] == "local"
        self.protocol = self.spec["protocol"]
        self.place = where(self.url)
        self.auth_ttl = (15, 4) if self.local else (300, 30)
        self.slots = threading.BoundedSemaphore(1 if self.local else CLOUD_AT_ONCE)  # a local model runs one at a time
        self.format = "json_schema"
        self.rejected = None  # the key was refused: stays so until a key works
        self._conns, self._conns_lock = set(), threading.Lock()

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        if self.local:
            return f"{self.model} on {self.label}"
        return f"{self.model} through {self.label} (billed to your API key)"

    def stop(self) -> int:
        self.stopping.set()
        with self._conns_lock:
            conns = list(self._conns)
        for conn in conns:
            try:
                if conn.sock:
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return len(conns)

    # -- one request --
    def _offline(self) -> writer.WriterError:
        if self.provider == "ollama":
            hint = "Open Ollama (or run ollama serve), then try again."
        elif self.provider == "lmstudio":
            hint = "Open LM Studio and start its local server, then try again."
        elif self.local:
            hint = "Start the server, then try again."
        else:
            hint = "Check your internet connection, then try again."
        return writer.WriterError(f"{self.label} isn't responding at {self.url}. {hint}", "model_offline")

    def _send(self, method: str, path: str, payload=None, timeout: float = 30) -> tuple:
        self._check_stopping()
        parts = urllib.parse.urlsplit(self.url + path)
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=CONNECT_SECONDS,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=CONNECT_SECONDS)
        headers = {"Accept": "application/json", "User-Agent": "polymath"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        with self._conns_lock:
            self._conns.add(conn)
        try:
            conn.connect()
            conn.sock.settimeout(timeout)
            conn.request(method, parts.path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise writer.WriterError("The model's reply was too large.", "invalid")
            return response.status, raw
        except (TimeoutError, socket.timeout):
            if conn.sock is None:
                raise self._offline()
            raise writer.WriterError(f"{self.label} took too long to answer.", "timeout")
        except (OSError, http.client.HTTPException):
            self._check_stopping()
            raise self._offline()
        finally:
            with self._conns_lock:
                self._conns.discard(conn)
            conn.close()

    @staticmethod
    def _error_text(raw: bytes) -> str:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return raw.decode("utf-8", "replace")[:200].strip()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            err = err.get("message")
        return str(err or "")[:200].strip()

    def _problem(self, status: int, raw: bytes) -> writer.WriterError:
        text = self._error_text(raw)
        low = text.lower()
        if status in (401, 403):
            return writer.WriterError(f"{self.label} rejected the API key. Add a working key on the AI model page.",
                                      "key_rejected")
        if status in (402, 429) or "quota" in low or "insufficient" in low and "credit" in low:
            return writer.WriterError(f"{self.label} says this account is out of credit or over its rate limit. Try "
                                      "again later, or choose another model.", "usage_limit")
        if status == 404 and ("model" in low or self.provider == "ollama"):
            hint = f" Run ollama pull {self.model}, or choose another." if self.provider == "ollama" else \
                " Choose another on the AI model page."
            return writer.WriterError(f"{self.label} doesn't have the model {self.model}.{hint}", "model_missing")
        if status == 404:
            return writer.WriterError(f"Nothing at {self.url} answers like a model server. Check the address.",
                                      "model_offline")
        if "memory" in low and ("requires" in low or "not enough" in low or "insufficient" in low):
            return writer.WriterError(f"{self.model} needs more memory than this computer has free. Close other apps "
                                      "or choose a smaller model.", "model_memory")
        if status >= 500:
            return writer.WriterError(f"{self.label} had a problem answering ({status}). {text}".strip(), "failed")
        return writer.WriterError(f"{self.label} refused the request ({status}). {text}".strip(), "failed")

    # -- what is installed --
    def list_models(self, timeout: float = LIST_SECONDS) -> list:
        path = "/api/tags" if self.protocol == "ollama" else "/models"
        status, raw = self._send("GET", path, timeout=timeout)
        if status != 200:
            raise self._problem(status, raw)
        try:
            data = json.loads(raw.decode("utf-8"))
            rows = data["models"] if self.protocol == "ollama" else data["data"]
            names = [row.get("name" if self.protocol == "ollama" else "id") for row in rows]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise writer.WriterError(f"Nothing at {self.url} answers like a model server. Check the address.",
                                     "model_offline")
        return _chat_names(names)

    def auth_status(self) -> dict:
        """Whether it can be used right now. A local server is asked (it costs nothing). A cloud provider isn't:
        its key is judged by the calls it answers."""
        method = "local" if self.local else "api-key"
        if not self.local:
            if self.rejected:
                return {"ok": False, "method": method, "code": self.rejected.code, "problem": str(self.rejected)}
            return {"ok": True, "method": method, "plan": None}
        try:
            names = self.list_models(CHECK_SECONDS)
        except writer.WriterError as exc:
            return {"ok": False, "method": method, "code": exc.code, "problem": str(exc)}
        if names and not any(_same_model(self.model, n) for n in names):
            exc = self._problem(404, json.dumps({"error": "model not found"}).encode())
            return {"ok": False, "method": method, "code": "model_missing", "problem": str(exc)}
        return {"ok": True, "method": method, "plan": None}

    # -- one model call --
    def run(self, system: str, prompt: str, schema: dict = None, web: bool = False, timeout: int = None,
            effort: str = None):
        if web:
            raise writer.WriterError(f"{self.model} can't look things up on the web, so this step needs a web search "
                                     "connection.", "no_search")
        self._check_stopping()
        limit = max(timeout or 0, LOCAL_SECONDS) if self.local else (timeout or writer.TIMEOUT)
        while not self.slots.acquire(timeout=0.5):
            self._check_stopping()
        started = time.time()
        self.last = {"backend": self.name, "model": self.model, "cost_usd": 0.0 if self.local else None}
        try:
            if self.protocol == "ollama":
                text, tokens = self._ollama(system, prompt, schema, limit)
            else:
                text, tokens = self._openai(system, prompt, schema, limit)
            self.rejected = None
        except writer.WriterError as exc:
            if exc.code == "key_rejected":
                self.rejected = exc
            raise
        finally:
            self.slots.release()
            self.last["seconds"] = round(time.time() - started, 1)
        self._check_stopping()
        if tokens:
            self.last["tokens"] = tokens
        return parse_json(text) if schema else writer.clean(text, 8000, lines=True)

    def _ollama(self, system: str, prompt: str, schema, limit: float) -> tuple:
        size = (len(system) + len(prompt)) / 3
        body = {"model": self.model, "stream": False, "think": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                # Set on purpose: the default can be small enough to cut off a long prompt without any error.
                "options": {"num_ctx": 32768 if size > 8000 else 16384, "temperature": 0.3, "num_predict": 12000}}
        if schema:
            body["format"] = schema
        status, raw = self._send("POST", "/api/chat", body, limit)
        if status == 400 and "think" in self._error_text(raw).lower():  # a model that can't turn thinking off
            body.pop("think")
            status, raw = self._send("POST", "/api/chat", body, limit)
        if status != 200:
            raise self._problem(status, raw)
        try:
            data = json.loads(raw.decode("utf-8"))
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise writer.WriterError("The model's reply couldn't be read.", "invalid")
        if data.get("done_reason") == "length":
            raise writer.WriterError("The model ran out of room before finishing.", "invalid")
        return text, {"input_tokens": int(data.get("prompt_eval_count") or 0),
                      "output_tokens": int(data.get("eval_count") or 0)}

    def _openai(self, system: str, prompt: str, schema, limit: float) -> tuple:
        modes = ("json_schema", "json_object", "plain")
        start = modes.index(self.format)
        for n, mode in enumerate(modes[start:] if schema else ("plain",)):
            told = system
            body = {"model": self.model, "stream": False}
            if self.local:
                body["temperature"] = 0.3
            if schema and mode == "json_schema":
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "polymath", "strict": True, "schema": schema}}
            elif schema:
                told = (f"{system}\n\nReturn one JSON object and nothing else. It must match this JSON schema:\n"
                        + json.dumps(schema, separators=(",", ":")))
                if mode == "json_object":
                    body["response_format"] = {"type": "json_object"}
            body["messages"] = [{"role": "system", "content": told}, {"role": "user", "content": prompt}]
            status, raw = self._send("POST", "/chat/completions", body, limit)
            more = schema and start + n + 1 < len(modes)
            if status in (400, 422) and more and re.search(r"response_format|json_schema|json_object|schema|strict",
                                                           self._error_text(raw), re.I):
                self.format = modes[start + n + 1]  # this server can't do that format: don't ask it again
                continue
            if status != 200:
                raise self._problem(status, raw)
            try:
                data = json.loads(raw.decode("utf-8"))
                choice = data["choices"][0]
                text = choice["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise writer.WriterError("The model's reply couldn't be read.", "invalid")
            if choice.get("finish_reason") == "length":
                raise writer.WriterError("The model ran out of room before finishing.", "invalid")
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            return text or "", {"input_tokens": int(usage.get("prompt_tokens") or 0),
                                "output_tokens": int(usage.get("completion_tokens") or 0)}
        raise writer.WriterError("The server wouldn't accept any response format.", "failed")


def _anthropic_names(key: str) -> list:
    try:
        import anthropic
    except ImportError:
        raise writer.WriterError("Anthropic keys need the anthropic Python package on this computer.", "writer_missing")
    try:
        rows = anthropic.Anthropic(api_key=key, timeout=float(LIST_SECONDS), max_retries=1).models.list(limit=100)
        return sorted(m.id for m in rows.data if m.id in writer.PRICES)
    except anthropic.AuthenticationError:
        raise writer.WriterError("Anthropic rejected the API key.", "key_rejected")
    except anthropic.APIConnectionError:
        raise writer.WriterError("Anthropic isn't reachable. Check your internet connection.", "model_offline")
    except anthropic.APIError as exc:
        raise writer.WriterError(f"Anthropic couldn't list its models: {str(exc)[:160]}", "failed")


def build(provider: str, model: str, url: str = "", key: str = "", effort: str = "low"):
    """The writer for a choice (nothing is called yet)."""
    spec = PROVIDERS.get(provider)
    if not spec:
        raise writer.WriterError("Choose a model provider.", "bad_request")
    if spec["protocol"] == "claude":
        pen = writer.ClaudeCLI(model if model in CLAUDE_MODELS else "opus", effort)
        if not pen.available():
            raise writer.WriterError("Claude Code isn't installed on this computer.", "writer_missing")
        return pen
    if not MODEL_NAME.match(str(model or "")):
        raise writer.WriterError("Choose a model.", "bad_request")
    if spec["kind"] == "key" and not key:
        raise writer.WriterError(f"Enter your {spec['label']} API key.", "key_missing")
    if spec["protocol"] == "anthropic":
        return writer.AnthropicAPI(model, effort, api_key=key)
    base = clean_url(url or spec["url"], spec["protocol"])
    return HTTPModel(provider, model, base, key)


def read_config(store) -> dict:
    try:
        data = json.loads((store.root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def startup_writer(store, model: str = "opus", effort: str = "low"):
    """What to run on: the saved choice; with none saved, Claude Code if it is here; else nothing yet. A saved
    choice that can't be used is reported, never swapped for another provider your data wasn't meant for."""
    config = read_config(store)
    active = config.get("active")
    if isinstance(active, dict) and active.get("provider") in PROVIDERS:
        pid = active["provider"]
        if not offered(pid):
            return NoModel("This version of Polymath works with Claude only. Choose a Claude model on the AI model "
                           "page.", "no_model")
        try:
            return build(pid, active.get("model"), active.get("url") or "", (config.get("keys") or {}).get(pid) or "",
                         effort)
        except writer.WriterError as exc:
            return NoModel(f"Your saved model can't be used: {exc} Choose one on the AI model page.", exc.code)
    return writer.pick_writer("auto", model, effort) or NoModel()


# --- the settings behind the AI model page -------------------------------------------------------------
class Models:
    def __init__(self, store, study, locked: bool = False, effort: str = "low", defaults: dict = None):
        self.store, self.study, self.locked, self.effort = store, study, locked, effort
        self.defaults = defaults or {}  # provider -> address, in place of the usual one (tests)
        self.lock = threading.Lock()
        self.switching = threading.Lock()
        self.bench = None       # the benchmark, whose results label each model

    def _url(self, pid: str, config: dict = None) -> str:
        config = read_config(self.store) if config is None else config
        active = config.get("active") if isinstance(config.get("active"), dict) else {}
        if active.get("provider") == pid and active.get("url"):
            return active["url"]  # the page shows the server actually in use
        return (config.get("urls") or {}).get(pid) or self.defaults.get(pid) or PROVIDERS[pid]["url"]

    def _save(self, change) -> None:
        with self.lock:
            config = read_config(self.store)
            change(config)
            root = self.store.root
            root.mkdir(parents=True, exist_ok=True)
            engine.ensure_gitignore(root, (CONFIG,))
            fd, tmp = tempfile.mkstemp(dir=str(root), prefix=f".{CONFIG}-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    if hasattr(os, "fchmod"):
                        os.fchmod(f.fileno(), 0o600)
                    json.dump(config, f, ensure_ascii=False, indent=1)
                os.replace(tmp, root / CONFIG)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def _refuse_if_locked(self) -> None:
        if self.locked:
            raise writer.WriterError("This dashboard was started with a fixed model (--tutor), so it can't be "
                                     "changed here.", "locked")

    def current(self) -> dict:
        pen = self.study.pen
        name = getattr(pen, "provider", None) or {"claude": "claude", "api": "anthropic"}.get(getattr(pen, "name", ""))
        spec = PROVIDERS.get(name or "")
        if not spec:
            return {"provider": None, "label": "None", "kind": "none", "model": "", "place": None,
                    "writer": pen.describe() if pen else None}
        place = getattr(pen, "place", None) or ("cloud" if spec["kind"] != "local" else "device")
        return {"provider": name, "label": spec["label"], "kind": spec["kind"], "model": getattr(pen, "model", ""),
                "place": "internet" if place == "cloud" else place, "url": getattr(pen, "url", ""),
                "writer": pen.describe()}

    def status(self) -> dict:
        state = self.study.auth() or {}
        now = self.current()
        return {"ok": bool(state.get("ok")), "code": state.get("code"), "problem": state.get("problem"),
                "provider": now["provider"], "label": now["label"], "kind": now["kind"], "model": now["model"],
                "place": now["place"], "writer": now["writer"]}

    def view(self) -> dict:
        config = read_config(self.store)
        keys = config.get("keys") or {}
        rows = []
        for pid, spec in PROVIDERS.items():
            if not offered(pid):
                continue
            row = {"id": pid, "label": spec["label"], "kind": spec["kind"], "needs_url": bool(spec.get("needs_url")),
                   "url": self._url(pid, config)}
            if spec["kind"] == "key":
                row["has_key"] = bool(keys.get(pid))  # whether one is saved: the key itself never leaves the server
            if pid == "claude":
                row.update(installed=writer.ClaudeCLI().available(), models=list(CLAUDE_MODELS))
            rows.append(row)
        return {"locked": self.locked, "status": self.status(), "providers": rows, "benchmark": bool(self.bench),
                "ratings": self.bench.ratings() if self.bench else {}}

    def scan(self) -> dict:
        """Which local servers are running, and the models each has."""
        config = read_config(self.store)
        wanted = [pid for pid, spec in PROVIDERS.items()
                  if offered(pid) and spec["kind"] == "local" and self._url(pid, config)]

        def look(pid):
            spec = PROVIDERS[pid]
            row = {"provider": pid, "label": spec["label"], "url": self._url(pid, config), "running": False, "models": []}
            try:
                base = clean_url(row["url"], spec["protocol"])
                row["url"] = base
                row["models"] = HTTPModel(pid, "scan", base).list_models(CHECK_SECONDS)
                row["running"] = True
            except writer.WriterError as exc:
                row.update(code=exc.code, problem=str(exc))
            return row

        with ThreadPoolExecutor(max_workers=max(1, len(wanted))) as pool:
            return {"servers": list(pool.map(look, wanted))}

    def list(self, data: dict) -> dict:
        pid = str(data.get("provider") or "")
        spec = PROVIDERS.get(pid) if offered(pid) else None
        if not spec:
            raise writer.WriterError("Choose a model provider.", "bad_request")
        if pid == "claude":
            return {"models": list(CLAUDE_MODELS)}
        config = read_config(self.store)
        key = self._key(pid, data, config)
        if spec["kind"] == "key" and not key:
            raise writer.WriterError(f"Enter your {spec['label']} API key.", "key_missing")
        if spec["protocol"] == "anthropic":
            return {"models": _anthropic_names(key)}
        base = clean_url(data.get("url") or self._url(pid, config), spec["protocol"])
        return {"models": HTTPModel(pid, "list", base, key).list_models(), "url": base}

    @staticmethod
    def _key(pid: str, data: dict, config: dict) -> str:
        given = str(data.get("key") or "").strip()
        if given and not KEY_TEXT.match(given):
            raise writer.WriterError("That doesn't look like an API key.", "bad_key")
        return given or (config.get("keys") or {}).get(pid) or ""

    def use(self, data: dict) -> dict:
        """Try the choice with one real call, and switch only if it works. Nothing is saved otherwise."""
        self._refuse_if_locked()
        pid = str(data.get("provider") or "")
        spec = PROVIDERS.get(pid) if offered(pid) else None
        if not spec:
            raise writer.WriterError("Choose a model provider.", "bad_request")
        if not self.switching.acquire(blocking=False):
            raise writer.WriterError("Another model is being tested. Wait for it to finish.", "busy")
        try:
            config = read_config(self.store)
            key = self._key(pid, data, config) if spec["kind"] == "key" else ""
            url = ""
            if spec["protocol"] in ("openai", "ollama"):
                url = clean_url(data.get("url") or self._url(pid, config), spec["protocol"])
            model = str(data.get("model") or "").strip()
            pen = build(pid, model, url, key, self.effort)
            self._try(pen)

            def change(c):
                c["active"] = {"provider": pid, "model": getattr(pen, "model", model), "url": url}
                if url and url != spec["url"]:
                    c.setdefault("urls", {})[pid] = url
                elif url:
                    (c.get("urls") or {}).pop(pid, None)
                if key:
                    c.setdefault("keys", {})[pid] = key
            self._save(change)
            self.study.set_pen(pen)
        finally:
            self.switching.release()
        return self.view()

    @staticmethod
    def _try(pen) -> None:
        if isinstance(pen, writer.ClaudeCLI):  # the login check costs nothing
            state = pen.auth_status()
            if not state.get("ok"):
                raise writer.WriterError(state.get("problem") or "Claude Code isn't ready.",
                                         state.get("code") or "signed_out")
            return
        out = pen.run("You reply only with the requested JSON.", "Set ok to true.", TEST_SCHEMA, timeout=TEST_SECONDS)
        if not isinstance(out, dict) or out.get("ok") is not True:
            raise writer.WriterError("The model answered, but not in the format Polymath needs. Try another model.",
                                     "invalid")

    def test(self) -> dict:
        """One real call on the current model (free on a local one, a few tokens on a key)."""
        pen = self.study.pen
        if isinstance(pen, NoModel):
            raise writer.WriterError(pen.problem, pen.code)
        began = time.time()
        self._try(pen)
        self.study.recheck()
        return {"seconds": round(time.time() - began, 1), "status": self.status()}

    def forget(self, data: dict) -> dict:
        self._refuse_if_locked()
        pid = str(data.get("provider") or "")
        if PROVIDERS.get(pid, {}).get("kind") != "key":
            raise writer.WriterError("Only a saved API key can be removed.", "bad_request")
        if self.current()["provider"] == pid:
            raise writer.WriterError("This key is in use. Switch to another model first.", "in_use")
        self._save(lambda c: (c.get("keys") or {}).pop(pid, None))
        return self.view()
