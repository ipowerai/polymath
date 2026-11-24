"""Active study time: the time you were actually using the dashboard.

The page reports the seconds between your own actions (a click, a key, a scroll, the pointer moving)
while it is on screen and in focus. A pause longer than its idle limit, a hidden tab or another window
counts as nothing. Here those reports are added up per day, per track and per session.

A report can't count for more time than has passed since the one before it, so two open tabs, a retry
or a page that misreports its clock can never add up to more than the time that really went by.

Kept in `active-time.json`, apart from the event log: it changes every few seconds and the learner
model doesn't depend on it. Standard library only.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import polymath as engine

FILE = "active-time.json"
MAX_REPORT = 240     # seconds one report may claim (the page's idle limit, plus its sending interval)
SAVE_EVERY = 20      # seconds between saves while time is coming in
KEEP_SESSIONS = 300  # sessions whose time is remembered
SESSION_ID = engine.re.compile(r"^[a-f0-9]{16}$")


class ActiveTime:
    def __init__(self, store: engine.Store):
        self.store = store
        self.path = store.root / FILE
        self.lock = threading.Lock()
        self.data = self._read()
        self._last, self._saved, self._dirty = None, 0.0, False

    def _read(self) -> dict:
        data = engine.read_json(self.path, None)
        if not isinstance(data, dict):
            data = {}
        days = data.get("days") if isinstance(data.get("days"), dict) else {}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
        return {"schema": 1, "days": days, "sessions": sessions}

    def report(self, seconds, track=None, session=None) -> dict:
        """Add the time the page saw you working, and return the running totals."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds != seconds or seconds <= 0:
            return self.summary()
        now = time.monotonic()
        with self.lock:
            limit = MAX_REPORT if self._last is None else min(MAX_REPORT, now - self._last)
            credit = min(float(seconds), limit)
            self._last = now
            if credit > 0:
                day = self.data["days"].setdefault(engine.local_day(engine.now()).isoformat(), {"seconds": 0.0, "tracks": {}})
                day["seconds"] = round(float(day.get("seconds") or 0) + credit, 1)
                if track and engine.SLUG_RE.match(str(track)):
                    tracks = day.setdefault("tracks", {})
                    tracks[track] = round(float(tracks.get(track) or 0) + credit, 1)
                if session and SESSION_ID.match(str(session)):
                    sessions = self.data["sessions"]
                    sessions[session] = round(float(sessions.pop(session, 0) or 0) + credit, 1)  # newest last
                    for old in list(sessions)[:max(0, len(sessions) - KEEP_SESSIONS)]:
                        del sessions[old]
                self._dirty = True
            if self._dirty and now - self._saved >= SAVE_EVERY:
                self._save(now)
        return self.summary()

    def _save(self, now: float = None) -> None:
        try:
            engine.write_json(self.path, self.data)
            self._dirty = False
            self._saved = time.monotonic() if now is None else now
        except OSError:
            pass  # kept in memory and saved with the next report

    def flush(self) -> None:
        with self.lock:
            if self._dirty:
                self._save()

    def session_seconds(self, session: str) -> float:
        with self.lock:
            return float(self.data["sessions"].get(session) or 0)

    def sessions(self) -> dict:
        with self.lock:
            return dict(self.data["sessions"])

    def summary(self) -> dict:
        with self.lock:
            return summarize(self.data)


def summarize(data: dict) -> dict:
    """Today, the last seven days and all time, overall and per track, in whole seconds."""
    today = engine.local_day(engine.now())
    week = {(today - timedelta(days=n)).isoformat() for n in range(7)}
    totals, tracks, days = {"today": 0.0, "week": 0.0, "total": 0.0}, {}, []
    for key, row in sorted((data.get("days") or {}).items()):
        if not isinstance(row, dict):
            continue
        seconds = float(row.get("seconds") or 0)
        days.append({"date": key, "seconds": round(seconds)})
        totals["total"] += seconds
        totals["week"] += seconds if key in week else 0
        totals["today"] += seconds if key == today.isoformat() else 0
        for slug, value in (row.get("tracks") or {}).items():
            t = tracks.setdefault(slug, {"today": 0.0, "week": 0.0, "total": 0.0})
            t["total"] += float(value or 0)
            t["week"] += float(value or 0) if key in week else 0
            t["today"] += float(value or 0) if key == today.isoformat() else 0
    whole = lambda d: {k: round(v) for k, v in d.items()}
    return {**whole(totals), "tracks": {slug: whole(t) for slug, t in tracks.items()}, "days": days}
