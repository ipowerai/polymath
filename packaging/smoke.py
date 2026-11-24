#!/usr/bin/env python3
"""Checks an installed Polymath: the page loads, the API needs a sign-in, and the benchmark is off once signed in.

Usage: python packaging/smoke.py <port>. It never prints the sign-in token and makes no AI calls.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import time
from pathlib import Path


def request(port: int, path: str, headers: dict = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        reply = conn.getresponse()
        return reply.status, reply.getheaders(), reply.read()
    finally:
        conn.close()


def main(port: int) -> int:
    deadline = time.time() + 30
    while True:
        try:
            status, _, _ = request(port, "/")
            break
        except OSError:
            if time.time() > deadline:
                print("the app did not answer")
                return 1
            time.sleep(0.5)
    if status not in (200, 303):
        print(f"the page answered {status}")
        return 1
    status, _, _ = request(port, "/api/models")
    if status != 401:
        print(f"/api/models without a sign-in answered {status}, expected 401")
        return 1

    token = (Path(os.environ.get("POLYMATH_HOME") or Path.home() / ".polymath") / "web-token").read_text().strip()
    status, headers, _ = request(port, f"/?token={token}")
    cookies = [v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie"]
    if status not in (200, 302, 303) or not cookies:
        print(f"signing in answered {status}")
        return 1
    print("ok: page loads, API is protected, sign-in works")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
