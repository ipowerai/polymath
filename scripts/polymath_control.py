#!/usr/bin/env python3
"""The `polymath` terminal command: start, stop, restart, status and open.

`stop` keeps it off, including after a restart of the computer, until `polymath start`. Nothing here prints the
sign-in token.
"""
from __future__ import annotations

import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
APP = SCRIPTS.parent
ROOT = APP.parent
ENGINE = SCRIPTS / "polymath.py"
DEFAULT_LABEL = "com.polymath.dashboard"
UNIT = "polymath.service"
START_SECONDS, STOP_SECONDS = 30, 15
USAGE = """Polymath

  polymath status    is it running, and where
  polymath start     start it, and keep it starting at login
  polymath stop      stop it, and keep it off until you start it
  polymath restart   stop it and start it again
  polymath open      open it in your browser, starting it if needed
"""


def port() -> int:
    try:
        number = int((ROOT / "port").read_text(encoding="utf-8").strip())
        if 1 <= number <= 65535:
            return number
    except (OSError, ValueError):
        pass
    return 8765


def label() -> str:
    try:
        return str(json.loads((ROOT / "install.json").read_text(encoding="utf-8"))["label"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_LABEL


def answering(number: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", number)) == 0


def run(args: list):
    try:
        return subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


class Background:
    """A plain background process, for an install with no login item."""

    def log_dir(self) -> Path:
        if platform.system() == "Darwin":
            return Path.home() / "Library" / "Logs" / "Polymath"
        if platform.system() == "Windows":
            return ROOT / "logs"
        return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "polymath"

    def pid(self):
        try:
            number = int((ROOT / "pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        command = run(["ps", "-o", "command=", "-p", str(number)]).stdout
        return number if str(ENGINE) in command else None

    def at_login(self):
        return False

    def start(self):
        folder = self.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "server.log", "ab") as log:
            child = subprocess.Popen([sys.executable, str(ENGINE), "serve", "--port", str(port())], cwd=str(APP),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                     env={**os.environ, "PYTHONUTF8": "1"}, **self.detached())
        (ROOT / "pid").write_text(str(child.pid), encoding="utf-8")

    @staticmethod
    def detached() -> dict:
        return {"start_new_session": True}

    def stop(self):
        number = self.pid()
        if number:
            try:
                os.kill(number, signal.SIGTERM)
            except OSError:
                pass
        try:
            (ROOT / "pid").unlink()
        except OSError:
            pass


class Launchd(Background):
    """The macOS login item."""

    def __init__(self):
        self.name = label()
        self.domain = f"gui/{os.getuid()}"
        self.job = f"{self.domain}/{self.name}"
        self.plist = Path.home() / "Library" / "LaunchAgents" / f"{self.name}.plist"

    def pid(self):
        found = run(["launchctl", "print", self.job])
        if found.returncode:
            return None
        match = re.search(r"^\s*pid = (\d+)", found.stdout, re.M)
        return int(match.group(1)) if match else None

    def at_login(self):
        listing = run(["launchctl", "print-disabled", self.domain]).stdout
        return not re.search(rf'"{re.escape(self.name)}"\s*=>\s*(disabled|true)', listing)

    def start(self):
        run(["launchctl", "enable", self.job])
        if run(["launchctl", "print", self.job]).returncode:
            run(["launchctl", "bootstrap", self.domain, str(self.plist)])
        else:
            run(["launchctl", "kickstart", self.job])

    def stop(self):
        run(["launchctl", "disable", self.job])
        run(["launchctl", "bootout", self.job])


class Systemd(Background):
    """The Linux user service."""

    def pid(self):
        value = run(["systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT]).stdout.strip()
        return int(value) if value.isdigit() and int(value) > 0 else None

    def at_login(self):
        return run(["systemctl", "--user", "is-enabled", UNIT]).stdout.strip() == "enabled"

    def start(self):
        run(["systemctl", "--user", "enable", "--now", UNIT])

    def stop(self):
        run(["systemctl", "--user", "disable", "--now", UNIT])


class Windows(Background):
    """Windows: a hidden process, and a file in the Startup folder while it should start at login."""

    def __init__(self):
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        self.on, self.off = startup / "Polymath.cmd", ROOT / "Polymath-startup.off"

    def log_dir(self) -> Path:
        return ROOT / "logs"

    def pid(self):
        query = ("Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | Where-Object { $_.CommandLine -and "
                 "$_.CommandLine.Contains($env:POLYMATH_ENGINE) -and $_.CommandLine.Contains(' serve') -and -not "
                 "$_.CommandLine.Contains('--open') } | Select-Object -First 1 -ExpandProperty ProcessId")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", query], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=30, env={**os.environ, "POLYMATH_ENGINE": str(ENGINE)},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return int(out) if out.isdigit() else None

    def at_login(self):
        return self.on.exists()

    @staticmethod
    def detached() -> dict:
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)}

    def start(self):
        if self.off.exists() and not self.on.exists():
            os.replace(self.off, self.on)
        super().start()

    def stop(self):
        number = self.pid()
        if number:
            try:
                os.kill(number, signal.SIGTERM)
            except OSError:
                pass
        if self.on.exists():
            os.replace(self.on, self.off)


def manager():
    system = platform.system()
    if system == "Windows":
        return Windows()
    if system == "Darwin":
        service = Launchd()
        return service if service.plist.exists() else Background()
    unit = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT
    if unit.exists() and run(["systemctl", "--user", "show-environment"]).returncode == 0:
        return Systemd()
    return Background()


def wait_for(check, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.25)
    return check()


def address() -> str:
    return f"http://127.0.0.1:{port()}"


def start(quiet: bool = False) -> int:
    service, number = manager(), port()
    if service.pid() and answering(number):
        if not quiet:
            print(f"Polymath is already running at {address()}")
        return 0
    if answering(number):
        print(f"Port {number} is being used by another program, so Polymath can't start.", file=sys.stderr)
        return 1
    service.start()
    if not wait_for(lambda: answering(number) and service.pid(), START_SECONDS):
        print(f"Polymath didn't start. Details are in {service.log_dir() / 'server.log'}", file=sys.stderr)
        return 1
    if not quiet:
        print(f"Polymath is running at {address()}")
    return 0


def stop(quiet: bool = False) -> int:
    service, number = manager(), port()
    was_running = bool(service.pid())
    service.stop()
    if was_running and not wait_for(lambda: not service.pid() and not answering(number), STOP_SECONDS):
        print("Polymath didn't stop in time. Run polymath status to check it.", file=sys.stderr)
        return 1
    if not quiet:
        print("Polymath is stopped. It stays off until you run: polymath start" if was_running
              else "Polymath was already stopped. It stays off until you run: polymath start")
    return 0


def restart() -> int:
    code = stop(quiet=True)
    return code if code else start()


def status() -> int:
    service, number = manager(), port()
    pid = service.pid()
    if pid and answering(number):
        print(f"Polymath is running at {address()} (process {pid}).")
        print("It starts when you log in." if service.at_login() else "It won't start by itself when you log in.")
        return 0
    if pid:
        print(f"Polymath is starting (process {pid}).")
        return 0
    print("Polymath is stopped. Start it with: polymath start")
    return 3


def open_app() -> int:
    code = start(quiet=True)
    if code:
        return code
    subprocess.run([sys.executable, str(ENGINE), "serve", "--port", str(port()), "--open"], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
    print(f"Opening {address()}")
    return 0


COMMANDS = {"start": start, "stop": stop, "restart": restart, "status": status, "open": open_app}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("help", "-h", "--help"):
        print(USAGE, end="")
        return 0
    if args[0] not in COMMANDS or len(args) > 1:
        print(USAGE, end="", file=sys.stderr)
        return 2
    return COMMANDS[args[0]]()


if __name__ == "__main__":
    sys.exit(main())
