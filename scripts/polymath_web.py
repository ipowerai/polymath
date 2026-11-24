"""Polymath web dashboard: a small, dependency-free server for your learning progress.

Start it through the engine:

    python3 scripts/polymath.py serve            # http://127.0.0.1:8765; prints a sign-in link
    python3 scripts/polymath.py serve --host 0.0.0.0   # any interface (the sign-in token guards it)

It reads the same files the tutor writes, so the page updates live while you study.
`polymath.py dashboard` writes the same page as one self-contained HTML file instead.
Standard library only.
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import math
import os
import random
import secrets
import shutil
import signal
import socket
import socketserver
import ssl
import statistics
import sys
import tempfile
import threading
import traceback
import urllib.parse
import webbrowser
from argparse import Namespace
from collections import Counter
from contextlib import redirect_stdout
from datetime import date, datetime, time as dtime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

import polymath as engine
import polymath_bench as bench_mod
import polymath_goals as goals_mod
import polymath_interview as interview_mod
import polymath_lessons as lessons_mod
import polymath_models as models_mod
import polymath_study as study_mod
import polymath_time as time_mod
import polymath_writer

WEB_ROOT = engine.PLUGIN_ROOT / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
HISTORY_DAYS = 180          # days of readiness history sent to the page
FORECAST_DAYS = 14          # days of upcoming reviews
RECENT_ANSWERS = 40         # answers in the live feed
SNAPSHOT_ANSWERS = 400      # answers kept in a snapshot file
LIVE_SESSION_HOURS = 6      # a session started this recently and not closed counts as in progress
MAX_BODY = 64 * 1024
MAX_UPLOAD = 8 * 1024 * 1024  # one resume file, base64-encoded (the only request allowed to be this big)
COOKIE = "polymath_session"  # each dashboard adds its port, so two dashboards never share one cookie
TOKEN_FILE = "web-token"     # the saved sign-in token
AUTH_FILE = "web-auth.json"  # the browsers signed in (a keyed hash of each sign-in, never the id itself)
PING_KEY_FILE = "web-ping-key"  # a random key that lets a dashboard prove it serves this folder (see ping_proof)
PRIVATE_FILES = (TOKEN_FILE, AUTH_FILE, PING_KEY_FILE)
SESSION_DAYS = 30            # a sign-in lasts this long (the server checks), or until you sign out
MAX_SESSIONS = 200           # sign-ins kept per data folder; the oldest go first
TLS_HANDSHAKE_SECONDS = 10   # a connection that hasn't finished its TLS handshake by then is dropped
IDLE_SECONDS = 60            # an open connection with no request for this long is closed
PROBE_SECONDS = 3            # how long a second start waits to hear who holds the port
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
WILDCARD = {"", "0.0.0.0", "::"}
PROBE_TARGET = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1", "localhost": "127.0.0.1"}
ATTEMPT_ID = engine.re.compile(r"^A\d{1,9}$")
CHALLENGE = engine.re.compile(r"[0-9a-fA-F]{16,128}")   # a ping challenge (matched whole)
PING_KEY = engine.re.compile(r"[0-9a-f]{64}")           # what web-ping-key holds (matched whole)
CONTENT_LENGTH = engine.re.compile(r"[0-9]{1,15}")      # matched whole: no sign, no spaces, no other digits

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
PUBLIC_PATHS = {"/app.css", "/app.js", "/favicon.svg", "/api/ping"}  # needed by the sign-in page
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class NotFound(Exception):
    """No such page, concept or API route (answered with a 404)."""


# --- reading the data ----------------------------------------------------------------
def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def data_version(store: engine.Store) -> str:
    """Changes whenever any file the dashboard shows changes (and at midnight)."""
    paths = [store.learner_path, store.profile_md, store.concepts_path, store.events_path]
    if store.tracks_dir.exists():
        paths += sorted(store.tracks_dir.glob("*/*.json")) + sorted(store.tracks_dir.glob("*/*.md"))
    if store.journal_dir.exists():
        paths += sorted(store.journal_dir.glob("*.md"))
    goals = store.root / goals_mod.FOLDER
    if goals.exists():
        paths += sorted(goals.glob("*.json"))
    digest = hashlib.sha1(engine.local_day(engine.now()).isoformat().encode())
    for path in paths:
        try:
            st = path.stat()
            digest.update(f"{path}|{st.st_mtime_ns}|{st.st_size}\n".encode())
        except OSError:
            digest.update(f"{path}|-\n".encode())
    return digest.hexdigest()[:16]


def next_midnight(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), dtime.min).astimezone()


def booking_gate(rep: dict):
    """Book the exam when all three hold: the plan's rule, checked from the engine's numbers."""
    passing, predicted = rep.get("passing_score"), rep.get("predicted_score")
    if passing is None or predicted is None:
        return None
    book_at, mock_at = engine.readiness_bars(passing)  # the same bars as the engine's readiness verdict
    last_two = [m["score"] for m in rep.get("full_mocks", [])][-2:]  # only exam-length mocks count
    checks = [
        {"id": "predicted", "label": "Predicted score", "value": predicted, "target": book_at,
         "met": round(predicted, 4) >= book_at},
        {"id": "coverage", "label": "Coverage", "value": rep.get("coverage", 0.0), "target": 0.9,
         "met": rep.get("coverage", 0.0) >= 0.9},
        {"id": "mocks", "label": "Last two full mock exams", "value": last_two, "target": mock_at,
         "met": len(last_two) == 2 and min(round(s, 4) for s in last_two) >= mock_at},
    ]
    return {"checks": checks, "passed": sum(c["met"] for c in checks), "met": all(c["met"] for c in checks)}


def mini(model: engine.Model, cid: str) -> dict:
    s = model.state(cid)
    return {"concept": cid, "title": model.concepts.get(cid, {}).get("title", cid),
            "status": model.label(s), "mastery": round(model.mastery(s), 3)}


def attempt_row(model: engine.Model, e: dict, voided: bool = False) -> dict:
    cid = e.get("concept")
    return {
        "id": e.get("id"), "ts": e.get("ts"), "concept": cid,
        "title": model.concepts.get(cid, {}).get("title", cid),
        "track": e.get("track"), "result": e.get("result"), "qtype": e.get("qtype"),
        "difficulty": e.get("difficulty"), "confidence": e.get("confidence"), "seconds": e.get("seconds"),
        "question": e.get("question"), "error": e.get("error"), "misconception": e.get("misconception"),
        "mistake": e.get("mistake"), "targets": e.get("targets"), "voided": voided,
    }


def concept_rows(model: engine.Model, track: dict) -> list:
    rows, seen = [], set()
    for d in track.get("domains", []):
        for cid in d.get("concepts", []):
            if cid in seen:
                continue
            seen.add(cid)
            s = model.state(cid)
            view = model.concept_view(cid)
            last = s.recent[-1]["ts"] if s.recent else None
            view.update({
                "domain": d.get("id"), "domain_name": d.get("name"),
                "difficulty": model.difficulty(s, track["slug"]),
                "last_answer": last,
                "open_mistakes": sum(1 for m in model.mistakes.values()
                                     if m["concept"] == cid and m["status"] != "resolved"),
            })
            rows.append(view)
    return rows


def forecast(model: engine.Model, track: dict, days: int = FORECAST_DAYS) -> list:
    """Reviews coming due each day, starting today (overdue ones count today)."""
    today = engine.local_day(model.at)
    counts = Counter()
    for cid in {c for c, _, _ in model.track_rows(track)}:
        due = model.due_at(model.state(cid))
        if due is not None:
            counts[max(engine.local_day(due), today)] += 1
    return [{"date": (today + timedelta(days=i)).isoformat(), "due": counts.get(today + timedelta(days=i), 0)}
            for i in range(days)]


def history(model: engine.Model, tracks: dict) -> tuple:
    """Each track's predicted score, coverage and mastered count at the end of every day,
    plus its starting point (the moment before its first answer).

    One pass over the log: events are applied in order and the report is taken at each
    midnight. The last point is computed exactly as the live numbers are."""
    out = {slug: [] for slug in tracks}
    starts = {}
    events = []
    for e in model.events:
        try:
            events.append((engine.parse_ts(e["ts"]), e))
        except (KeyError, ValueError, TypeError):
            continue
    if not events or not tracks:
        return out, starts
    replay = engine.Model(model.store, at=model.at, events=[])
    replay.pace, replay.pace_n, replay.learn_rate = model.pace, model.pace_n, model.learn_rate
    members = {slug: {c for c, _, _ in replay.track_rows(track)} for slug, track in tracks.items()}
    today = engine.local_day(model.at)
    day = min(engine.local_day(ts) for ts, _ in events)
    show_from = max(day, today - timedelta(days=HISTORY_DAYS - 1))
    answers, points = Counter(), Counter()

    def report(slug, at):
        """A report for one moment, leaving the replay exactly as it was."""
        replay.at = at
        replay.ff, replay.ff_n = engine.compute_forgetting(replay.reviews)
        existing = set(replay.states)
        rep = replay.track_report(tracks[slug])
        # Reports create states for concepts never seen; drop them so each is created later,
        # as the live model does, with a prior that reflects what is known by then.
        for cid in set(replay.states) - existing:
            del replay.states[cid]
        return rep

    i = 0
    while day <= today:
        cutoff = next_midnight(day)
        while i < len(events) and (day == today or events[i][0] < cutoff):
            ts, e = events[i]
            if e.get("type") == "attempt":
                for slug, ids in members.items():
                    if slug not in starts and e.get("concept") in ids:
                        rep = report(slug, ts)
                        starts[slug] = {"date": engine.local_day(ts).isoformat(), "predicted": rep["predicted_score"],
                                        "mastered": rep["mastered"], "coverage": rep["coverage"]}
            try:
                replay.apply(e)
                if e.get("type") == "attempt":
                    for slug, ids in members.items():
                        if e.get("concept") in ids:
                            answers[slug] += 1
                            points[slug] += engine.SCORE.get(e.get("result"), 0.0)
            except (KeyError, ValueError, TypeError):
                pass
            i += 1
        if day >= show_from:
            moment = model.at if day == today else cutoff - timedelta(seconds=1)
            for slug in tracks:
                rep = report(slug, moment)
                out[slug].append({"date": day.isoformat(), "predicted": rep["predicted_score"],
                                  "coverage": rep["coverage"], "mastered": rep["mastered"],
                                  "started": rep["started"], "due": rep["due"],
                                  "answers": answers[slug], "points": round(points[slug], 2)})
        answers.clear()
        points.clear()
        day += timedelta(days=1)
    return out, starts


def _slope(values: list) -> float:
    """Least-squares slope of evenly spaced values (per step)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x, mean_y = (n - 1) / 2, sum(values) / n
    den = sum((i - mean_x) ** 2 for i in range(n))
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / den


def trend(points: list, rep: dict, track: dict, today: date, start: dict = None) -> dict:
    """Which way a goal is heading, from the last week of daily readiness.

    The projection works on the gap to the best predicted score the model allows (95%: even
    full mastery leaves room for a slip): each coming day closes, or opens, the same share of
    what is left as the last week did, so it levels off instead of running past the ceiling.
    It says where the recent pace leads, not what will happen."""
    out = {"enough": False, "direction": None}
    usable = [p for p in points if p.get("predicted") is not None]
    if not usable:
        return out
    last = usable[-1]
    p0 = last["predicted"]
    week = usable[-8:]  # today and the seven days before it
    span = (date.fromisoformat(last["date"]) - date.fromisoformat(week[0]["date"])).days
    if span:
        out.update(span_days=span, predicted_delta=round(p0 - week[0]["predicted"], 3),
                   mastered_delta=last["mastered"] - week[0]["mastered"],
                   coverage_delta=round(last["coverage"] - week[0]["coverage"], 3))

    def window(first: date, final: date):
        rows = [p for p in points if first.isoformat() <= p["date"] <= final.isoformat()]
        n = sum(p.get("answers", 0) for p in rows)
        return n, (round(sum(p.get("points", 0.0) for p in rows) / n, 3) if n else None)

    out["answers_7d"], out["accuracy_7d"] = window(today - timedelta(days=6), today)
    out["answers_prev"], out["accuracy_prev"] = window(today - timedelta(days=13), today - timedelta(days=7))

    passing, target = rep.get("passing_score"), rep.get("target_date")
    book = engine.readiness_bars(passing)[0] if passing is not None else None
    if passing is not None and target and book < 1.0 and start and start.get("predicted") is not None:
        exam = engine.parse_date(target)
        begin = date.fromisoformat(start["date"])
        total = (exam - begin).days
        if total > 0 and begin < today:
            climb = book - start["predicted"]
            out.update(start_date=start["date"], start_predicted=start["predicted"],
                       time_used=round(engine.clamp((today - begin).days / total, 0.0, 1.0), 3),
                       goal_progress=round(1.0 if climb <= 0 else
                                           engine.clamp((p0 - start["predicted"]) / climb, 0.0, 1.0), 3))

    studied = sum(1 for p in week if p.get("answers"))
    if len(week) < 3 or span < 2 or studied < 2:
        return out  # too little to call a direction
    ceiling = 1.0 - engine.EXAM_SLIP
    k = _slope([math.log(max(1e-3, ceiling - p["predicted"])) for p in week])  # below 0: the gap is closing
    out.update(enough=True, gap_closed_per_day=round(-k, 4),
               direction="up" if k < -0.003 else "down" if k > 0.003 else "flat")

    def at(days: int) -> float:
        return engine.clamp(ceiling - max(0.0, ceiling - p0) * math.exp(k * days), engine.EXAM_GUESS, ceiling)

    days_left = (rep.get("projection") or {}).get("days_left")
    if days_left is not None and days_left > 0:
        out["projection"] = [{"date": (today + timedelta(days=d)).isoformat(), "predicted": round(at(d), 3)}
                             for d in range(min(days_left, 366) + 1)]
        out["projected_at_exam"] = round(at(days_left), 3)
    if book is not None and book < ceiling:
        if p0 >= book:
            out["book_in_days"] = 0
        elif k < 0:
            need = math.log((ceiling - book) / (ceiling - p0)) / k
            out["book_in_days"] = int(math.ceil(need)) if need < 3650 else None
        if "projected_at_exam" in out:
            reach = out["projected_at_exam"]
            out["outlook"] = "on track" if reach >= book else "at risk" if reach >= passing else "off track"
    return out


def sessions(model: engine.Model, limit: int = 60, worked: dict = None) -> list:
    """Study sessions, newest first. A dashboard session carries its own id on every event; a
    session in Claude Code has none, so each is paired separately and the two can overlap. A session
    still open shows the active time reported for it (`worked`: session id -> seconds)."""
    out, cli, web, worked = [], None, {}, worked or {}

    def new(e, where):
        return {"start": e.get("ts"), "end": None, "track": e.get("track"), "focus": e.get("focus"),
                "minutes": None, "summary": None, "status": "unclosed", "where": where, "_n": 0, "_points": 0.0}

    def close(s):
        n, points = s.pop("_n"), s.pop("_points")
        s["answers"] = n
        s["accuracy"] = round(points / n, 3) if n else None
        out.append(s)

    for e in model.events:
        kind, sid = e.get("type"), e.get("session")
        if kind == "session_start":
            if sid:
                web[sid] = new(e, "dashboard")
            else:
                if cli:
                    close(cli)
                cli = new(e, "claude")
        elif kind == "attempt":
            target = web.get(sid) if sid else cli
            if target is not None:
                target["_n"] += 1
                target["_points"] += engine.SCORE.get(e.get("result"), 0.0)
        elif kind == "session_end":
            s = web.pop(sid, None) if sid else cli
            if s is None:
                s = new({"track": e.get("track")}, "dashboard" if sid else "claude")
                s["start"] = None
            if not sid:
                cli = None
            s.update(end=e.get("ts"), minutes=e.get("minutes"), summary=e.get("summary"), status="done")
            s["track"] = s.get("track") or e.get("track")
            close(s)
    for sid, current in [(None, cli)] + list(web.items()):
        if not current:
            continue
        try:
            hours = engine.days_between(engine.parse_ts(current["start"]), model.at) * 24
        except (KeyError, ValueError, TypeError):
            hours = None
        if hours is not None and 0 <= hours <= LIVE_SESSION_HOURS:
            current["status"] = "live"
            current["minutes"] = round(float(worked.get(sid) or 0) / 60, 1) if sid else None
        close(current)
    out.sort(key=lambda s: s.get("start") or s.get("end") or "")
    return list(reversed(out))[:limit]


def activity(model: engine.Model, active_days: list = ()) -> list:
    """Answers and minutes per day. A day the page measured counts its active time; an earlier one, what its
    sessions logged."""
    minutes = Counter()
    measured = {date.fromisoformat(d["date"]): d["seconds"] / 60 for d in active_days}
    for e in model.events:
        if e.get("type") == "session_end":
            try:
                minutes[engine.local_day(engine.parse_ts(e["ts"]))] += float(e.get("minutes") or 0)
            except (KeyError, ValueError, TypeError):
                continue
    minutes.update({d: 0 for d in measured})
    for d, value in measured.items():
        minutes[d] = value
    days = sorted(set(model.activity) | set(minutes))
    return [{"date": d.isoformat(), "answers": model.activity.get(d, 0), "minutes": round(minutes.get(d, 0))}
            for d in days]


def calibration_levels(model: engine.Model) -> list:
    n, points = Counter(), Counter()
    for e in model.events:
        if e.get("type") == "attempt" and e.get("confidence"):
            try:
                level = int(engine.clamp(int(e["confidence"]), 1, 5))
            except (TypeError, ValueError):
                continue
            n[level] += 1
            points[level] += engine.SCORE.get(e.get("result"), 0.0)
    return [{"confidence": c, "n": n[c], "expected": engine.CONF_P[c],
             "accuracy": round(points[c] / n[c], 3) if n[c] else None} for c in range(1, 6)]


def mistake_rows(model: engine.Model) -> list:
    beliefs, questions = {}, {}
    for e in model.events:  # the wrong belief behind each mistake, and the question that opened it
        mid = e.get("mistake")
        if e.get("type") == "attempt" and mid:
            questions.setdefault(mid, e.get("question"))
            if str(e.get("misconception") or "").strip():
                beliefs[mid] = str(e["misconception"]).strip()
    rows = []
    for m in model.mistakes.values():
        if m["status"] == "open":
            next_check = engine.iso(model.at)
        elif m["status"] == "fixing":
            next_check = engine.iso(engine.parse_ts(m["last_miss"]) + timedelta(days=1))
        else:
            next_check = None
        rows.append({**m, "title": model.concepts.get(m["concept"], {}).get("title", m["concept"]),
                     "tracks": model.concepts.get(m["concept"], {}).get("tracks", []),
                     "due": model.mistake_due(m), "next_check": next_check,
                     "belief": beliefs.get(m["id"]), "question": questions.get(m["id"])})
    rows.sort(key=lambda r: (r["status"] == "resolved", r["status"] != "open", -r["misses"], r["last_miss"]))
    return rows


def next_up(model: engine.Model, slug: str, limit: int = 8):
    try:
        plan = model.plan([slug], limit)
    except engine.PolymathError:
        return None
    keep = ("concept", "title", "kind", "difficulty", "mastery", "status", "domain", "mistake")
    plan["items"] = [{k: it.get(k) for k in keep} for it in plan["items"]]
    plan.pop("locked_sample", None)
    return plan


def voided_keys(raw: list) -> set:
    return {e.get("target") for e in raw if e.get("type") == "void"}


def concept_detail(model: engine.Model, cid: str, raw: list, voided: set, limit=None) -> dict:
    info = model.concepts.get(cid)
    if info is None:
        raise engine.PolymathError(f"Unknown concept '{cid}'.")
    s = model.state(cid)
    view = model.concept_view(cid)
    attempts = [attempt_row(model, e, engine.attempt_key(e) in voided) for e in engine.in_time_order(
                [e for e in raw if e.get("type") == "attempt" and e.get("concept") == cid])][::-1]  # newest first, by
    # when each was given (an answer can be logged late)
    view.update({
        "objective": info.get("objective", ""), "tags": info.get("tags", []), "tracks": info.get("tracks", []),
        "prereqs": [mini(model, p) for p in info.get("prereqs", [])],
        "dependents": [mini(model, c) for c, i in sorted(model.concepts.items()) if cid in i.get("prereqs", [])],
        "related": [mini(model, r) for r in info.get("related", []) if r in model.concepts],
        "first_seen": engine.iso(s.first) if s.first else None,
        "answer_count": len(attempts),
        "answers": attempts[:limit] if limit else attempts,
        "mistakes": [m for m in mistake_rows(model) if m["concept"] == cid],
    })
    return view


def attempts_page(model: engine.Model, raw: list, voided: set, offset=0, limit=50, track=None) -> dict:
    rows = []
    for e in raw:
        if e.get("type") != "attempt":
            continue
        if track and e.get("track") != track and track not in model.concepts.get(e.get("concept"), {}).get("tracks", []):
            continue
        rows.append(e)
    rows = engine.in_time_order(rows)[::-1]  # newest first by when each was given (one can be logged late)
    page = rows[offset:offset + limit]
    return {"total": len(rows), "offset": offset, "limit": limit,
            "items": [attempt_row(model, e, engine.attempt_key(e) in voided) for e in page]}


def collect_state(store: engine.Store, snapshot: bool = False, clock=None) -> dict:
    """Everything the page shows, in one JSON document."""
    app = {"version": engine.VERSION, "mode": "snapshot" if snapshot else "live",
           "generated_at": engine.iso(engine.now()), "data_version": data_version(store), "home": str(store.root)}
    if not store.ready():
        return {"app": app, "ready": False}
    model = engine.Model(store)
    app["today"] = engine.local_day(model.at).isoformat()
    tracks = {slug: model.tracks[slug] for slug in store.slugs() if slug in model.tracks}
    hist, starts = history(model, tracks)
    out_tracks = []
    for slug, track in tracks.items():
        rep = model.track_report(track)
        rep.update({
            "short": track.get("short"), "exam": track.get("exam") or None, "sources": track.get("sources", []),
            "daily_minutes": track.get("daily_minutes"), "created": track.get("created"),
            "gate": booking_gate(rep), "history": hist.get(slug, []), "forecast": forecast(model, track),
            "start": starts.get(slug),
            "trend": trend(hist.get(slug, []), rep, track, engine.local_day(model.at), starts.get(slug)),
            "concept_rows": concept_rows(model, track), "next": next_up(model, slug),
            "plan_md": read_text(store.tracks_dir / slug / "plan.md"),
            "notes_md": read_text(store.tracks_dir / slug / "notes.md"),
        })
        out_tracks.append(rep)
    learner = model.learner_report()
    learner.update({"preferences": model.learner.get("preferences", {}),
                    "persona": model.learner.get("persona", {}),
                    "calibration_levels": calibration_levels(model),
                    "notes_md": read_text(store.profile_md)})
    attempts = [e for e in model.events if e.get("type") == "attempt"]
    clock = clock or time_mod.ActiveTime(store)
    active = clock.summary()
    state = {
        "app": app, "ready": True, "learner": learner, "tracks": out_tracks, "goals": goals_mod.summaries(store, model),
        "time": {k: active[k] for k in ("today", "week", "total", "tracks")},
        "mistakes": mistake_rows(model), "sessions": sessions(model, worked=clock.sessions()),
        "activity": activity(model, active["days"]),
        "recent": [attempt_row(model, e) for e in attempts[::-1][:SNAPSHOT_ANSWERS if snapshot else RECENT_ANSWERS]],
    }
    if snapshot:
        raw = store.events()
        voided = voided_keys(raw)
        ids = {c for t in tracks.values() for c, _, _ in model.track_rows(t)}
        state["concept_details"] = {cid: concept_detail(model, cid, raw, voided, limit=30)
                                    for cid in sorted(ids) if cid in model.concepts}
    return state


# --- changes from the browser -----------------------------------------------------------
def clean_text(value, field: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise engine.PolymathError(f"{field} is empty.")
    if len(text) > limit:
        raise engine.PolymathError(f"{field} is longer than {limit} characters.")
    return text


def apply_change(store: engine.Store, path: str, body: dict) -> dict:
    """Run one change sent by the page. Every change goes through the engine's own functions, and every
    event it logs is marked as the dashboard's (source "web"), so `undo` in Claude Code leaves it alone."""
    store.require()
    parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
    if parts[:2] == ["api", "tracks"] and len(parts) == 4:
        slug, action = parts[2], parts[3]
        if not engine.SLUG_RE.match(slug):
            raise engine.PolymathError(f"'{slug}' is not a track.")
        if action == "settings":
            changes = {k: body[k] for k in ("target_date", "status", "daily_minutes") if k in body}
            if not changes:
                raise engine.PolymathError("Nothing to change.")
            if changes.get("target_date") == "":
                changes["target_date"] = None
            track = engine.update_track(store, slug, changes, source="web")
            return {"ok": True, "track": slug, "settings": {k: track.get(k) for k in changes}}
        if action == "mocks":
            event = engine.log_mock(store, slug, body.get("score"), body.get("by_domain") or None,
                                    body.get("questions"), body.get("minutes"), source="web")
            return {"ok": True, "mock": event}
        if action == "notes":
            store.track(slug)
            engine.add_note(store.tracks_dir / slug / "notes.md", clean_text(body.get("text"), "The note", 2000),
                            title=f"Notes: {slug}")
            return {"ok": True}
    if parts == ["api", "preferences"]:
        keys = list(engine.PREFERENCE_RANGES) + ["start_level"]
        prefs = engine.set_preferences(store, {k: body[k] for k in keys if k in body})
        return {"ok": True, "preferences": prefs}
    if parts == ["api", "notes"]:
        section = clean_text(body.get("section") or "Coaching notes", "The section", 80)
        engine.add_note(store.profile_md, clean_text(body.get("text"), "The note", 2000), section=section)
        return {"ok": True}
    if parts == ["api", "void"]:
        attempt = str(body.get("attempt") or "")
        if not ATTEMPT_ID.match(attempt):
            raise engine.PolymathError("Give the id of the answer to void, such as A12.")
        reason = " ".join(str(body.get("reason") or "").split())[:300] or None
        return {"ok": True, **engine.void_attempt(store, attempt=attempt, reason=reason, source="web")}
    raise NotFound(path)


# --- study sessions from the browser -----------------------------------------------------------
STUDY_ROUTE = engine.re.compile(r"^/api/study/([a-f0-9]{16})/(answer|reason|keep|followup|next|retry|dispute|end|"
                                r"mock/begin|mock/save|mock/submit|mock/finish)$")


def models_post(app, path: str, body: dict) -> dict:
    """The AI model page's actions. Keys are read from the body here and never returned."""
    models = app.models
    if not models:
        raise NotFound(path)
    action = {"/api/models/scan": models.scan, "/api/models/test": models.test}.get(path)
    if action:
        return action()
    action = {"/api/models/list": models.list, "/api/models/use": models.use,
              "/api/models/forget": models.forget}.get(path)
    if not action:
        raise NotFound(path)
    return action(body)


BENCH_ROUTE = engine.re.compile(r"^/api/bench/([a-f0-9]{16})/(pause|resume|review)$")


def bench_post(app, path: str, body: dict) -> dict:
    """The benchmark's actions. A run starts only from an explicit, confirmed request."""
    bench = app.bench
    if not bench:
        raise NotFound(path)
    if path == "/api/bench/estimate":
        return bench.estimate(body)
    if path == "/api/bench/start":
        return bench.start(body)
    match = BENCH_ROUTE.match(path)
    if not match:
        raise NotFound(path)
    rid, action = match.groups()
    if action == "pause":
        return bench.pause(rid)
    if action == "resume":
        return bench.resume(rid, body)
    return bench.review(rid, body)


def study_post(app, path: str, body: dict) -> dict:
    """Run one study action. The session logic lives in polymath_study; this only routes."""
    study = app.study
    if not study:
        raise study_mod.StudyError("Study isn't available: no question writer is set up (install Claude Code and "
                                   "log in), then restart the dashboard.", "writer_missing")
    if path == "/api/study/start":
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else None
        return study.start(str(body.get("mode") or "learn"), str(body.get("track") or ""), scope, body.get("count"))
    if path == "/api/coach":
        return study.chat(body.get("message"))
    match = STUDY_ROUTE.match(path)
    if not match:
        raise NotFound(path)
    sid, action = match.groups()
    qid = str(body.get("qid") or "")
    if action == "answer":
        return study.answer(sid, body.get("answers"), body.get("block"))
    if action == "reason":
        return study.reason(sid, qid, str(body.get("reason") or ""), str(body.get("other") or ""))
    if action == "keep":
        return study.keep_key(sid, qid)
    if action == "followup":
        return study.followup(sid, qid, body.get("answer"), skip=body.get("skip") is True)
    if action == "next":
        return study.next_block(sid, body.get("block"))
    if action == "retry":
        return study.retry(sid, qid)
    if action == "dispute":
        which = str(body.get("which") or "question")
        if which not in ("question", "twin"):
            raise study_mod.StudyError("Say which to dispute: 'question' or 'twin' (the follow-up).", "bad_request")
        return study.dispute(sid, qid, str(body.get("reason") or ""), force=body.get("force") is True, which=which)
    if action == "end":
        return study.end(sid, skip_close=body.get("skip_close") is True)
    if action == "mock/begin":
        return study.mock_begin(sid)
    if action == "mock/save":
        return study.mock_save(sid, body.get("answers"))
    if action == "mock/submit":
        return study.mock_submit(sid, body.get("answers"))
    return study.mock_finish(sid)


GOAL_ROUTE = engine.re.compile(r"^/api/goals/([a-f0-9]{16})/(message|retry|accept|archive|settings|milestone|"
                               r"interview|practice|reveal)$")


def goals_post(app, path: str, body: dict) -> dict:
    """Run one action on a goal. The logic lives in polymath_goals; this only routes."""
    goals = app.goals
    if not goals:
        raise NotFound(path)
    if path == "/api/goals":
        return goals.create(body.get("text"))
    if path == "/api/goals/interview":
        return goals.interviews.create(body)
    if path == "/api/goals/extract":
        return {"text": interview_mod.extract_text(str(body.get("name") or ""), interview_mod.decode_upload(body.get("data")))}
    match = GOAL_ROUTE.match(path)
    if not match:
        raise NotFound(path)
    gid, action = match.groups()
    if action == "message":
        return goals.say(gid, body.get("text"))
    if action == "retry":
        return goals.retry(gid)
    if action == "accept":
        return goals.accept(gid)
    if action == "archive":
        return goals.archive(gid)
    if action == "settings":
        return goals.settings(gid, body)
    if action == "interview":
        return goals.interviews.update(gid, body)
    if action == "practice":
        return goals.interviews.practice(gid, body.get("qid"), body.get("answer"), followup=body.get("followup") is True)
    if action == "reveal":
        return goals.interviews.reveal(gid, body.get("qid"))
    return goals.milestone(gid, body.get("milestone"), body.get("action"))


def _no_constants(name):
    raise ValueError(f"{name} isn't a number")


# --- the server ---------------------------------------------------------------------------
def query_int(query: dict, name: str, default: int, low: int, high: int) -> int:
    try:
        value = int((query.get(name) or [default])[0])
    except (TypeError, ValueError):
        value = default
    return int(engine.clamp(value, low, high))


def host_name(header: str) -> str:
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else header
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def is_loopback(host: str) -> bool:
    return host in LOOPBACK or host.startswith("127.")


def split_host_port(entry: str) -> tuple:
    """(name, port) for an --allow-host entry, which can name a port (name:port, or [ipv6]:port); port None
    without one."""
    entry = entry.strip()
    if entry.startswith("["):
        host, _, rest = entry[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else None
    if entry.count(":") == 1:
        host, _, port = entry.partition(":")
        if port.isdigit():
            return host, int(port)
    return entry, None


def origin_parts(netloc: str, scheme: str):
    """(host, port) for an origin's or a Host header's address, the port filled in from the scheme when it
    is left out; None if it isn't an address."""
    try:
        parts = urllib.parse.urlsplit(f"{scheme}://{netloc.strip()}")
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if not host:
        return None
    return host, port or (443 if scheme == "https" else 80)


def bearer_matches(given: str, token: str) -> bool:
    """Whether an Authorization: Bearer value is the token. The header arrives decoded as Latin-1 (as the
    HTTP library reads header bytes), so its bytes are compared with the token's UTF-8 ones: a token with
    an accented letter works from curl too. A client that sent it as Latin-1 text also matches."""
    want = token.encode("utf-8")
    try:
        sent = given.encode("latin-1")  # the bytes that came over the wire
    except UnicodeEncodeError:
        sent = given.encode("utf-8")
    return hmac.compare_digest(sent, want) or hmac.compare_digest(given.encode("utf-8"), want)


def content_length(headers):
    """The request body's length: 0 without a Content-Length, None if it is malformed or given twice with
    different values (a proxy in front could then read the body differently from this server)."""
    values = {value.strip() for value in headers.get_all("Content-Length") or ()}
    if not values:
        return 0
    value = values.pop() if len(values) == 1 else ""
    return int(value) if CONTENT_LENGTH.fullmatch(value) else None


def endpoint(address) -> str:
    """A socket address as 'ip:port', written the same at both ends of one connection (an IPv4 address
    reached through an IPv6 socket is written as IPv4)."""
    ip = ipaddress.ip_address(str(address[0]).split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return f"[{ip}]:{address[1]}" if ip.version == 6 else f"{ip}:{address[1]}"


def ping_proof(key: str, challenge: str, where: str) -> str:
    """The answer to a ping challenge, which only a dashboard that can read the data folder's ping key
    (web-ping-key: random, and readable only by you) can give. The key is never the sign-in token, so an
    answer, which anyone may ask for, tells nothing about the token. It covers the address the
    connection reached too (`where`, as endpoint() writes it), so a program holding another port or
    address can't pass a challenge on to the real dashboard and hand back its answer."""
    message = f"polymath-ping-v1:{where}:{challenge}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def read_ping_key(root: Path) -> str:
    """The data folder's ping key, or "" if there is none or it can't be trusted (other accounts could
    read it, or it isn't one Polymath wrote). A dashboard reads it whenever it is asked for a proof, so a
    key made after it started (or made again) still works."""
    path = Path(root) / PING_KEY_FILE
    try:
        if os.name == "posix" and path.stat().st_mode & 0o077:
            return ""
        key = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return key if PING_KEY.fullmatch(key) else ""


def ensure_ping_key(root: Path) -> str:
    """The data folder's ping key, made now if there is none: a second start makes it just before it
    asks the dashboard holding the port for a proof, so no dashboard writes it at start. "" if it can't
    be made (and nothing is made for a folder that doesn't exist)."""
    key = read_ping_key(root)
    if key or not Path(root).is_dir():
        return key
    try:
        write_private(Path(root) / PING_KEY_FILE, secrets.token_hex(32) + "\n")
    except OSError:
        return ""
    return read_ping_key(root)  # what the dashboard will read, should another start have made one too


class Sessions:
    """Browser sign-ins. Each is a random id in a cookie, good for 30 days (the server checks), until you
    sign out, or until the token changes. web-auth.json (readable only by you) keeps a hash of each id,
    keyed by the token and the dashboard's port, never the id itself: a restart keeps you signed in, and
    an id made on one dashboard opens no other. Dashboards for one data folder take turns writing the
    file, under the folder's write lock, so none loses another's changes."""

    def __init__(self, store: engine.Store, token: str, port):
        self.store = store
        self.path = store.root / AUTH_FILE
        self.secret = token.encode("utf-8")
        self.scope = f"polymath-session-v2:{port}:".encode("ascii")
        self.lock = threading.Lock()
        self.live = self._saved()

    def _key(self, sid: str) -> str:
        return hmac.new(self.secret, self.scope + sid.encode("utf-8"), hashlib.sha256).hexdigest()

    def _saved(self) -> dict:
        """The sign-ins in the file that haven't expired: {key: expiry}."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows = data.get("sessions") if isinstance(data, dict) else None
        now, out = engine.now(), {}
        for key, expires in (rows.items() if isinstance(rows, dict) else ()):
            try:
                when = engine.parse_ts(expires)
            except (TypeError, ValueError):
                continue
            if when > now:
                out[key] = when
        return out

    def _store(self, key: str, expires=None) -> None:
        """Add (with an expiry) or remove one sign-in in the file, keeping the others there: another
        dashboard for this data folder may have saved some. The file is read and written again under
        the folder's write lock, so two dashboards writing at once never drop each other's sign-ins (or
        bring back one that was signed out)."""
        try:
            with self.store.writing():
                rows = self._saved()
                if expires:
                    rows[key] = expires
                else:
                    rows.pop(key, None)
                newest = sorted(rows.items(), key=lambda kv: kv[1])[-MAX_SESSIONS:]
                text = json.dumps({"version": 1, "sessions": {k: engine.iso(v) for k, v in newest}}, indent=1)
                write_private(self.path, text + "\n")
        except OSError:
            pass  # this run still knows the change; only a restart would forget it

    def create(self) -> str:
        sid = secrets.token_urlsafe(32)
        key, now = self._key(sid), engine.now()
        expires = now + timedelta(days=SESSION_DAYS)
        with self.lock:
            live = sorted(((k, v) for k, v in self.live.items() if v > now), key=lambda kv: kv[1])
            self.live = dict(live[-(MAX_SESSIONS - 1):])
            self.live[key] = expires
        self._store(key, expires)  # outside the lock: waiting for the folder's lock holds up no other request
        return sid

    def valid(self, sid) -> bool:
        if not sid or len(sid) > 256:
            return False
        key = self._key(sid)
        with self.lock:
            expires = self.live.get(key)
        return expires is not None and expires > engine.now()

    def revoke(self, sid) -> None:
        if not sid or len(sid) > 256:
            return
        key = self._key(sid)
        with self.lock:
            known = self.live.pop(key, None) is not None
        if known:
            self._store(key)

    def page_key(self, sid: str) -> str:
        """The key the page keeps for one sign-in, in its own storage, and sends with every API call. Browsers
        send a host's cookies to all its ports, so a program on another port of this machine can be handed
        the sign-in id; storage stays with the dashboard's own origin, and the key can't be worked out from
        the id without the token."""
        return hmac.new(self.secret, b"polymath-page-v1:" + self.scope + sid.encode("utf-8"), hashlib.sha256).hexdigest()


class App:
    """What the request handlers share: the data folder, the sign-in rule and a cache."""

    def __init__(self, store, token=None, read_only=False, allowed_hosts=(), secure=False, demo=False, study=None):
        self.store = store
        self.study = study
        self.goals = None
        self.lessons = None
        self.models = None
        self.bench = None
        self.clock = time_mod.ActiveTime(store)
        self.read_only = read_only
        parsed = [split_host_port(h.lower()) for h in allowed_hosts if h]
        self.allowed_hosts = LOOPBACK | {name for name, _ in parsed}
        self.allowed_origins = {(name, port) for name, port in parsed if port}  # --allow-host name:port
        self.secure = secure
        self.demo = demo
        self.auth_lock = threading.Lock()
        self.set_sign_in(token)
        self.lock = threading.Lock()
        self._state = (None, b"")
        self._model = (None, None, None, None)

    def set_sign_in(self, token=None, saved=False, port=None):
        """The sign-in rule, set once the port is bound: the token in use (None: no sign-in), whether it
        is the one saved in web-token (not one given with --token) and the port (it names the cookie and
        scopes each sign-in, so dashboards on two ports keep a cookie each)."""
        self.port = port
        self.cookie = f"{COOKIE}_{port}" if port else COOKIE
        self.token_in_file = bool(token) and saved
        self._token_seen = token_stamp(self.store) if self.token_in_file else None
        self.auth = (token or None, Sessions(self.store, token, port) if token else None)

    @property
    def token(self):
        """The token in use now (None: no sign-in)."""
        return self.current()[0]

    def current(self) -> tuple:
        """(token, sign-ins) in use now. A dashboard using the saved token follows web-token: once another
        start replaces it (--rotate-token on another port, say), the old token and every sign-in made
        with it stop working here too, and the new token works."""
        if not self.token_in_file or token_stamp(self.store) == self._token_seen:
            return self.auth
        with self.auth_lock:
            stamp = token_stamp(self.store)
            if stamp != self._token_seen:
                self._token_seen = stamp
                try:
                    token = read_token(self.store)
                except engine.PolymathError:
                    token = ""  # unreadable for now: keep the token in use
                if token and token != self.auth[0]:
                    self.auth = (token, Sessions(self.store, token, self.port))
                    sys.stderr.write(f"The sign-in token in {self.store.root / TOKEN_FILE} was replaced: this "
                                     "dashboard takes the new one, and browsers signed in with the old one must "
                                     "sign in again.\n")
            return self.auth

    def key(self):
        return data_version(self.store), engine.now().strftime("%Y%m%d%H%M")

    def state_json(self) -> bytes:
        key = self.key()
        with self.lock:
            if self._state[0] != key:
                state = collect_state(self.store, clock=self.clock)
                state["app"].update(read_only=self.read_only, auth=bool(self.token), demo=self.demo,
                                    study=self.study_info())
                self._state = (key, json.dumps(state, ensure_ascii=False, default=str).encode("utf-8"))
            return self._state[1]

    def study_info(self) -> dict:
        study = self.study
        pen = study.pen if study else None
        if self.read_only:
            reason = "read-only"
        elif not pen:
            reason = "no-writer"
        elif not study.owner:
            reason = "not-owner"
        else:
            reason = None
        return {"available": reason is None, "writer": pen.describe() if pen else None, "reason": reason}

    def model(self):
        key = self.key()
        with self.lock:
            if self._model[0] != key:
                raw = self.store.events()
                self._model = (key, engine.Model(self.store), raw, voided_keys(raw))
            return self._model[1:]

    def invalidate(self):
        with self.lock:
            self._state = (None, b"")
            self._model = (None, None, None, None)


def login_page(error: str = "", saved: bool = True) -> bytes:
    message = f'<p class="login-error" role="alert">{html.escape(error)}</p>' if error else ""
    where = "\n(also saved in <code>web-token</code> in your Polymath data folder)" if saved else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Polymath</title><link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/app.css"></head>
<body class="login-body"><main class="login">
<h1>Polymath</h1>
<p>This dashboard needs the sign-in token printed when the server started{where}.</p>
{message}
<form method="post" action="/login" class="login-form">
<label for="token">Token</label>
<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
<button type="submit" class="btn btn-primary">Sign in</button>
</form></main></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = f"Polymath/{engine.VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = IDLE_SECONDS  # an idle connection (a browser's keep-alive) is closed after this long
    unread = 0              # bytes of the request's body not read yet
    auth = (None, None)     # the sign-in rule for this request: (token, sign-ins), from App.current()

    @property
    def app(self) -> App:
        return self.server.app

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            line = engine.re.sub(r"\?[^\s\"]*", "?…", fmt % args)  # never log a query string: tokens travel in them
            sys.stderr.write(f"{self.address_string()} {line}\n")

    def send_error(self, code, message=None, explain=None):
        """The HTTP library's own refusals (a malformed request). One with too much cookie data says what to do:
        another program on this machine can set cookies for this address, and browsers send them all."""
        if code == 431:
            message = "Too much cookie data"
            explain = ("This browser sent more cookie data than a request may carry: something else on this machine "
                       "has set cookies for this address. Clear this site's cookies in the browser, then sign in "
                       "again.")
        super().send_error(code, message, explain)

    # -- responses --
    def send(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8", headers=None):
        if self.unread:  # a body left unread must never be taken for the next request on this connection
            self.close_connection = True
        self.send_response(status)
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():  # a page may set its own policy
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        if status != 304:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and status != 304:
            self.wfile.write(body)

    def send_json(self, status: int, data, raw: bytes = None):
        body = raw if raw is not None else json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def redirect(self, location: str, cookie: str = None):
        headers = {"Location": location, "Cache-Control": "no-store"}
        if cookie:
            headers["Set-Cookie"] = cookie
        self.send(303, b"", headers=headers)

    def send_static(self, path: str):
        name, ctype = STATIC[path]
        body = (WEB_ROOT / name).read_bytes()
        etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            return self.send(304, headers={"ETag": etag})
        self.send(200, body, ctype, {"ETag": etag, "Cache-Control": "no-cache"})

    def send_login(self, status: int, error: str = ""):
        """The sign-in page. Its referrer policy is same-origin (not no-referrer), so the browser sends the
        page's own Origin with the form, where no-referrer would send "null"."""
        self.send(status, login_page(error, self.app.token_in_file), "text/html; charset=utf-8",
                  {"Referrer-Policy": "same-origin", "Cache-Control": "no-store"})

    # -- checks --
    def host_ok(self) -> bool:
        """Without a token, answer only to known host names (this blocks DNS-rebinding pages)."""
        if self.auth[0]:
            return True
        return host_name(self.headers.get("Host", "")) in self.app.allowed_hosts

    def session_ids(self) -> list:
        """Every sign-in id this browser sent in this dashboard's cookie. A page on another port of this
        host can set a cookie of the same name too (browsers don't keep cookies apart by port), and the
        browser then sends both, so a planted one must never hide the real one. Browsers keep at most about 180
        cookies for a host, so the first 256 values always include the real one; reading stops there, so a
        request stuffed with values costs no more than that."""
        out, seen = [], set()
        for header in self.headers.get_all("Cookie") or ():
            for part in header.split(";"):
                name, _, value = part.strip().partition("=")
                if name == self.app.cookie and value and value not in seen:
                    seen.add(value)
                    out.append(value)
                    if len(out) >= 256:
                        return out
        return out

    def signed_in(self, api: bool = True) -> bool:
        """Whether this request is signed in: a script's Bearer token, or a browser's sign-in id in the
        cookie. For the API the cookie must come with its page key (X-Polymath-Key, see Sessions.page_key),
        so the cookie alone, which other ports of this host are sent too, opens nothing. The page's own
        files (its HTML, script and styles), which hold no data, need only the cookie."""
        token, sessions = self.auth
        if not token:
            return True
        key = (self.headers.get("X-Polymath-Key") or "").strip().encode("utf-8")
        for sid in self.session_ids():
            if sessions.valid(sid) and (not api or hmac.compare_digest(key, sessions.page_key(sid).encode("utf-8"))):
                return True
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return bearer_matches(auth[7:].strip(" \t"), token)  # (U+0085 and U+00A0 can end a UTF-8 token)
        return False

    def same_site(self) -> bool:
        """Whether a form or change comes from this dashboard's own pages, not from another site. Browsers
        say where a request comes from in Sec-Fetch-Site, which no page can set, and in Origin. A form
        posted from a page whose referrer policy is no-referrer (index.html's) carries Origin "null",
        so there Sec-Fetch-Site decides. A request with neither header isn't a browser's (a script or
        curl): no other site can have sent it, and the token or cookie decides.

        Sec-Fetch-Site same-origin is the browser's own word for the page's own request, so it is enough,
        behind any proxy. Browsers send it only to secure addresses (HTTPS, localhost, 127.0.0.1), so over
        plain HTTP by name (a dashboard on the network) the Origin decides alone: it must name the host and
        port this request came to (by Host, or X-Forwarded-Host behind a proxy), or a name given with
        --allow-host on its scheme's default port (a proxy that rewrites Host, as nginx does by default),
        so a page on another port of the same host is refused."""
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if fetch_site in ("cross-site", "same-site"):  # same-site: another port on this host, say
            return False
        if fetch_site == "same-origin":
            return True
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        if origin.strip() == "null":
            return fetch_site == "same-origin"
        try:
            parts = urllib.parse.urlsplit(origin.strip())
        except ValueError:
            return False
        scheme = (parts.scheme or "").lower()
        theirs = origin_parts(parts.netloc, scheme) if scheme in ("http", "https") else None
        if theirs is None:
            return False
        named = set()
        for header in (self.headers.get("Host"), self.headers.get("X-Forwarded-Host")):
            if header:
                mine = origin_parts(header.split(",")[0], scheme)
                if mine == theirs:
                    return True
                named.add((mine or ("",))[0])
        # A proxy that put its own address in Host hides the name the page was served from: then a name given
        # with --allow-host counts, on its scheme's default port. Where the request names it itself, the port
        # had to match above.
        hidden = theirs[0] in self.app.allowed_hosts and not is_loopback(theirs[0]) and theirs[0] not in named
        return hidden and (theirs in self.app.allowed_origins or theirs[1] == (443 if scheme == "https" else 80))

    def session_cookie(self, value: str, max_age: int) -> str:
        secure = self.app.secure or self.headers.get("X-Forwarded-Proto") == "https"
        return (f"{self.app.cookie}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
                + ("; Secure" if secure else ""))

    def sign_in(self, given: str):
        token, sessions = self.auth
        if not token:
            return self.redirect("/")
        if given and hmac.compare_digest(given.strip().encode(), token.encode()):
            for sid in self.session_ids():  # a new id at every sign-in; the old ones end
                sessions.revoke(sid)
            sid = sessions.create()
            # The page key rides in the address's fragment, which browsers never send to any server; the page
            # moves it into its own storage at once and takes it out of the address.
            return self.redirect(f"/#k={sessions.page_key(sid)}", self.session_cookie(sid, SESSION_DAYS * 86400))
        self.send_login(401, "That token doesn't match. Check for a missing character.")

    def sign_out(self):
        sessions = self.auth[1]
        if sessions:
            for sid in self.session_ids():  # ended here, not only forgotten by the browser
                sessions.revoke(sid)
        return self.redirect("/login", self.session_cookie("", 0))

    def ping(self, query: dict) -> dict:
        """Who answers here. Given a challenge, also the proof that this is the dashboard for a data folder:
        a second `serve` asks for it before it hands a sign-in link to whatever holds the port."""
        out = {"app": "polymath", "version": engine.VERSION}
        challenge = (query.get("challenge") or [None])[0]
        if challenge is None:
            return out
        if not CHALLENGE.fullmatch(challenge):
            raise engine.PolymathError("The challenge must be 16 to 128 hexadecimal digits.")
        key = read_ping_key(self.app.store.root)
        if key:
            try:
                where = endpoint(self.connection.getsockname())
            except (OSError, ValueError):
                return out
            out["proof"] = ping_proof(key, challenge, where)
        return out

    # -- routing --
    def do_GET(self):
        self.dispatch(self.handle_get)

    def do_HEAD(self):
        self.dispatch(self.handle_get)

    def do_POST(self):
        self.dispatch(self.handle_post)

    def dispatch(self, handler):
        try:
            self.auth = self.app.current()  # one rule for the whole request, even if the token changes meanwhile
            self.unread = 1  # until the body's length is known, take it that one is waiting
            if self.headers.get("Transfer-Encoding") is not None:
                return self.send_json(411, {"error": "Send the body with a Content-Length "
                                                     "(chunked bodies aren't accepted)."})
            length = content_length(self.headers)
            if length is None:
                return self.send_json(400, {"error": "Bad Content-Length."})
            self.unread = length  # GET and HEAD never read theirs, so such a request closes the connection
            if not self.host_ok():
                return self.send(403, (f"Polymath doesn't answer to the host name "
                                       f"'{self.headers.get('Host', '')}'. If you reach it through a "
                                       "proxy, restart it with --allow-host <name>.\n").encode())
            url = urllib.parse.urlsplit(self.path)
            handler(url.path, urllib.parse.parse_qs(url.query))
        except (engine.PolymathError, polymath_writer.WriterError) as exc:
            self.send_json(400, {"error": str(exc), "code": getattr(exc, "code", None)})
        except NotFound:
            self.send_json(404, {"error": "Not found."})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # keep serving; the details go to the terminal, not the browser
            traceback.print_exc()
            try:
                self.send_json(500, {"error": "Something went wrong; the server's terminal has the details."})
            except OSError:
                pass

    def handle_get(self, path: str, query: dict):
        if path == "/api/ping":
            return self.send_json(200, self.ping(query))
        if path == "/login":
            if query.get("token"):
                return self.sign_in(query["token"][0])
            if not self.auth[0]:
                return self.redirect("/")
            # Always the form: the page comes here when the API turned it away, and a cookie without its page key
            # (cleared storage, another browser profile) must not bounce between / and /login.
            return self.send_login(200)
        if path == "/" and query.get("token"):
            return self.sign_in(query["token"][0])
        if path not in PUBLIC_PATHS and not self.signed_in(api=path.startswith("/api/")):
            if path.startswith("/api/"):
                return self.send_json(401, {"error": "Sign in first.", "login": "/login"})
            return self.redirect("/login")
        if path in STATIC:
            return self.send_static(path)
        if path == "/api/version":
            return self.send_json(200, {"v": data_version(self.app.store), "now": engine.iso(engine.now())})
        if path == "/api/state":
            return self.send_json(200, None, raw=self.app.state_json())
        if path.startswith("/api/concept/"):
            cid = urllib.parse.unquote(path[len("/api/concept/"):])
            model, raw, voided = self.app.model()
            if cid not in model.concepts:
                raise NotFound(cid)
            return self.send_json(200, concept_detail(model, cid, raw, voided))
        if path == "/api/study":
            info = {}
            if self.app.study:
                info.update(self.app.study.status())
            info.update(self.app.study_info())
            return self.send_json(200, info)
        if path.startswith("/api/study/"):
            if not self.app.study:
                raise NotFound(path)
            return self.send_json(200, self.app.study.view(path[len("/api/study/"):]))
        if path == "/api/coach":
            if not self.app.study:
                return self.send_json(200, {"messages": [], "busy": False, "available": False})
            return self.send_json(200, self.app.study.coach_view())
        if path in ("/api/models", "/api/models/status"):
            if not self.app.models:
                raise NotFound(path)
            return self.send_json(200, self.app.models.view() if path == "/api/models" else self.app.models.status())
        if path == "/api/bench" or path.startswith("/api/bench/"):
            if not self.app.bench:
                raise NotFound(path)
            rid = path[len("/api/bench/"):] if path != "/api/bench" else None
            return self.send_json(200, self.app.bench.run_view(rid) if rid else self.app.bench.view())
        if path == "/api/goals":
            return self.send_json(200, {"goals": self.app.goals.list() if self.app.goals else []})
        if path == "/api/goals/interview-defaults":
            return self.send_json(200, self.app.goals.interviews.defaults() if self.app.goals else {"resume": ""})
        if path.startswith("/api/goals/"):
            if not self.app.goals:
                raise NotFound(path)
            return self.send_json(200, self.app.goals.view(path[len("/api/goals/"):]))
        if path.startswith("/api/learn/"):
            if not self.app.lessons:
                raise NotFound(path)
            return self.send_json(200, self.app.lessons.view(path[len("/api/learn/"):]))
        if path == "/api/attempts":
            model, raw, voided = self.app.model()
            offset = query_int(query, "offset", 0, 0, 10 ** 9)
            limit = query_int(query, "limit", 50, 1, 200)
            track = (query.get("track") or [None])[0]
            return self.send_json(200, attempts_page(model, raw, voided, offset, limit, track))
        raise NotFound(path)

    def handle_post(self, path: str, query: dict):
        upload = path == "/api/goals/extract"
        if upload and self.unread > MAX_BODY and not self.signed_in():
            return self.send_json(401, {"error": "Sign in first.", "login": "/login"})   # before reading a big body
        if self.unread > (MAX_UPLOAD if upload else MAX_BODY):
            return self.send_json(413, {"error": "That file is too large. Paste the text instead." if upload
                                        else "That request is too large."})
        try:
            body = self.rfile.read(self.unread) if self.unread else b""
        except (TimeoutError, socket.timeout):  # separate types before Python 3.10
            self.close_connection = True  # the rest of the body never came
            return
        self.unread = 0
        if path == "/login":
            if not self.same_site():
                return self.send(403, b"Cross-site sign-in refused.\n")
            token = urllib.parse.parse_qs(body.decode("utf-8", "replace")).get("token", [""])[0]
            return self.sign_in(token)
        if path == "/logout":
            if not self.same_site():
                return self.send(403, b"Cross-site sign-out refused.\n")
            return self.sign_out()
        if not self.signed_in():
            return self.send_json(401, {"error": "Sign in first.", "login": "/login"})
        if not path.startswith("/api/"):
            raise NotFound(path)
        if self.app.read_only:
            return self.send_json(403, {"error": "This dashboard was started read-only (--read-only)."})
        if self.headers.get("X-Polymath") != "1" or not self.same_site():
            return self.send_json(403, {"error": "Changes must come from the dashboard page itself."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send_json(415, {"error": "Send JSON."})
        try:
            data = json.loads(body.decode("utf-8") or "{}", parse_constant=_no_constants)
        except (UnicodeDecodeError, ValueError):
            return self.send_json(400, {"error": "That isn't valid JSON."})
        if not isinstance(data, dict):
            return self.send_json(400, {"error": "Send a JSON object."})
        if path == "/api/activity":   # every few seconds: changes nothing the page has to reload
            track = data.get("track") if isinstance(data.get("track"), str) else None
            session = data.get("session") if isinstance(data.get("session"), str) else None
            total = self.app.clock.report(data.get("seconds"), track, session)
            return self.send_json(200, {k: total[k] for k in ("today", "week", "total", "tracks")})
        if path.startswith("/api/study") or path == "/api/coach":
            result = study_post(self.app, path, data)
        elif path.startswith("/api/goals"):
            result = goals_post(self.app, path, data)
        elif path.startswith("/api/models/"):
            result = models_post(self.app, path, data)
        elif path.startswith("/api/bench/"):
            result = bench_post(self.app, path, data)
        elif path == "/api/learn":
            if not self.app.lessons:
                raise NotFound(path)
            result = self.app.lessons.open(data.get("term"), data.get("track"), data.get("concept"),
                                           retry=data.get("retry") is True)
        else:
            result = apply_change(self.app.store, path, data)
        self.app.invalidate()
        result["data_version"] = data_version(self.app.store)
        self.send_json(200, result)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app: App, verbose: bool = False, tls=None):
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.app = app
        self.verbose = verbose
        self.tls = tls  # an ssl.SSLContext for HTTPS, or None
        super().__init__(address, Handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        socketserver.TCPServer.server_bind(self)  # skips HTTPServer's slow reverse-DNS lookup
        self.server_name, self.server_port = self.server_address[:2]

    def finish_request(self, request, client_address):
        """Answer one connection, on its own thread. With TLS the handshake happens here, within a
        deadline, so a connection that never finishes one holds up only itself (done in accept(), on the
        serving thread, it held up everyone)."""
        if not self.tls:
            return super().finish_request(request, client_address)
        request.settimeout(TLS_HANDSHAKE_SECONDS)  # do_handshake takes it as the deadline for the whole handshake
        try:
            conn = self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
        except OSError:
            return  # the caller closes the plain socket
        try:
            conn.do_handshake()
            super().finish_request(conn, client_address)
        except OSError as exc:  # a failed handshake, or the client went away
            if self.verbose:
                sys.stderr.write(f"{client_address[0]} TLS connection ended: {exc}\n")
        finally:
            self.shutdown_request(conn)


# --- the sign-in token ---------------------------------------------------------------------
def write_private(path: Path, text: str) -> None:
    """Write a file only you can read, replacing any old copy in one step (whatever its permissions
    were), and keep the dashboard's private files out of git if the folder becomes a repository."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine.ensure_gitignore(path.parent, PRIVATE_FILES)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), 0o600)
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_token(store: engine.Store) -> str:
    """The saved sign-in token, or "" if there is none. A copy other accounts can read (copied or restored
    with wider permissions) is made private first. It is read as --token-file reads its file: a byte-order
    mark that an editor added is no part of it, and UTF-16 (as Windows PowerShell writes) is read, so
    saving it unchanged never turns it into a token nobody can type."""
    path = store.root / TOKEN_FILE
    try:
        if os.name == "posix" and path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
        data = path.read_bytes()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise engine.PolymathError(f"Can't read the saved sign-in token in {path} ({exc.strerror or exc}). "
                                   "Make it yours and private (chmod 600), or delete it, and start again.")
    try:
        token = engine.decode_text(data).lstrip("﻿").strip()
    except UnicodeError:
        raise engine.PolymathError(f"The saved sign-in token in {path} isn't plain text. Delete the file and "
                                   "start again for a new one.")
    if any(c.isspace() or not c.isprintable() for c in token):
        raise engine.PolymathError(f"The saved sign-in token in {path} holds a space or an invisible character. "
                                   "Save it again as one word of plain text, or delete it and start again.")
    return token


def load_token(store: engine.Store, rotate: bool = False) -> str:
    """The saved sign-in token: created on first use (or replaced, with rotate), readable only by you and
    kept out of git."""
    if not rotate:
        token = read_token(store)
        if token:
            engine.ensure_gitignore(store.root, PRIVATE_FILES)
            return token
    token = secrets.token_urlsafe(24)
    path = store.root / TOKEN_FILE
    try:
        write_private(path, token + "\n")
    except OSError as exc:
        raise engine.PolymathError(f"Can't save the sign-in token in {path} ({exc.strerror or exc}).")
    return token


def token_stamp(store: engine.Store):
    """What changes whenever web-token is written again (None if it can't be seen): a dashboard using the
    saved token checks it, so a token replaced by another start is taken up (see App.current)."""
    try:
        info = (store.root / TOKEN_FILE).stat()
    except OSError:
        return None
    return info.st_ino, info.st_mtime_ns, info.st_size


def clear_sign_ins(root: Path) -> None:
    """Sign every browser out (after a new token): forget the saved sign-ins, and the ping key too, so a
    copy of the folder taken before can't stand in for this dashboard either."""
    for name in (AUTH_FILE, PING_KEY_FILE):
        path = Path(root) / name
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise engine.PolymathError(f"Can't clear {path} ({exc.strerror or exc}): delete it, and start again.")


def bound_ip(address) -> str:
    """An IP as a link writes it (an IPv4 address reached through an IPv6 socket written as IPv4)."""
    ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    return str(ip.ipv4_mapped) if ip.version == 6 and ip.ipv4_mapped else str(ip)


def link_host(host: str, ip, tls: bool) -> str:
    """The host to put in the links printed and opened. A wildcard reads as 127.0.0.1. A name (localhost,
    say) is written as the address actually bound or reached, `ip`: a browser could resolve the name to
    another address first ([::1] before 127.0.0.1), where another program might be listening for the
    token. With TLS the name stays, since the certificate names it."""
    if host in WILDCARD:
        name = "127.0.0.1"
    else:
        try:
            ipaddress.ip_address(host)
            name = host
        except ValueError:
            name = host if tls or not ip else ip
    return f"[{name}]" if ":" in name else name


def sign_in_link(url: str, token) -> str:
    """The address with the token in it, encoded so that every character survives (a base64 '+' or '/')."""
    return f"{url}?token={urllib.parse.quote(token, safe='')}" if token else url


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # picks the outgoing interface; sends nothing
            return s.getsockname()[0]
    except OSError:
        return None


def probe(host: str, port: int, key, tls: bool = False):
    """Who holds the port: "ours" (the dashboard for this data folder: it answered a fresh challenge with
    the proof only the folder's ping key `key` gives, for the very address this connection reached),
    "polymath" (something that says it is Polymath but gave no such proof) or None, with the address that
    answered (an IP; None if nothing did). It gives up after PROBE_SECONDS in all, however slowly the other
    end answers."""
    found = []
    asking = threading.Thread(target=lambda: found.append(ask_ping(host, port, key, tls)), daemon=True,
                              name="polymath-probe")
    asking.start()
    asking.join(PROBE_SECONDS)
    return found[0] if found else (None, None)


def ask_ping(host: str, port: int, key, tls: bool):
    """probe()'s question: GET /api/ping with a fresh challenge, and check the proof that comes back.
    Returns (who it is, the IP that answered)."""
    target = PROBE_TARGET.get(host, host)
    challenge = secrets.token_hex(16)
    if tls:
        context = ssl.create_default_context()  # the proof, not the certificate, says who this is
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(target, port, timeout=1.5, context=context)
    else:
        conn = http.client.HTTPConnection(target, port, timeout=1.5)
    try:
        conn.request("GET", f"/api/ping?challenge={challenge}", headers={"Accept": "application/json"})
        peer = conn.sock.getpeername()
        where, ip = endpoint(peer), bound_ip(peer[0])
        data = json.loads(conn.getresponse().read(4096).decode("utf-8"))
    except Exception:
        return None, None
    finally:
        conn.close()
    if not isinstance(data, dict) or data.get("app") != "polymath":
        return None, ip
    proof = data.get("proof")
    if key and isinstance(proof, str) and hmac.compare_digest(proof.encode("utf-8"),
                                                              ping_proof(key, challenge, where).encode("utf-8")):
        return "ours", ip
    return "polymath", ip


def needs_token(host: str, read_only: bool, demo: bool, pen) -> bool:
    """Whether the dashboard needs a sign-in token. Anything beyond this machine does. On this machine
    too, other accounts and programs can reach 127.0.0.1, so a dashboard that can change your data or
    spend your Claude usage needs one. Only a read-only dashboard, or the demo with the built-in
    practice writer (simulated data, no usage), opens without one."""
    if not is_loopback(host):
        return True
    if read_only:
        return False
    return not (demo and (pen is None or isinstance(pen, polymath_writer.FakeWriter)))


def tls_context(cert, key) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as exc:
        raise engine.PolymathError(f"Can't load the TLS certificate or key ({exc}).")
    return context


def already_running(store: engine.Store, host: str, port: int, scheme: str, token, rotate: bool, needed: bool):
    """The port is taken. Only the dashboard for this very data folder gets a link, and only once it has
    proved who it is: anything else might be a program waiting to be handed the token. The link carries a
    token only if this start would have needed one itself (the one given, else the saved one), so a
    `--read-only` start never opens a link that signs in with full access."""
    key = ensure_ping_key(store.root)
    holder, ip = probe(host, port, key, tls=scheme == "https")
    if holder == "polymath" and not key:
        raise engine.PolymathError(f"Port {port} is already in use by a Polymath dashboard, which can't be checked "
                                   f"for this data folder: no {PING_KEY_FILE} could be read or made in "
                                   f"{store.root}. Pass --port with a free one.")
    if holder == "polymath":
        stop = ("stop it before rotating the token (it would keep accepting the old one)" if rotate
                else "stop it, then start again")
        raise engine.PolymathError(f"Port {port} is already in use by another Polymath dashboard. If it is an "
                                   f"older version serving this data folder, {stop}; otherwise pass --port "
                                   "with a free one.")
    if holder != "ours":
        raise engine.PolymathError(f"Port {port} is already in use. Pass --port with a free one.")
    url = f"{scheme}://{link_host(host, ip, scheme == 'https')}:{port}/"  # where the proof came from
    if rotate:
        raise engine.PolymathError(f"Polymath is already running at {url} for this data folder. Stop it first "
                                   "(Ctrl+C where it runs), then start it again with --rotate-token.")
    if not token and needed:
        try:
            token = read_token(store)
        except engine.PolymathError:
            token = None
    return None, sign_in_link(url, token), [f"Polymath is already running at {url}"]


def start_tokens(store: engine.Store, token, rotate: bool, needed: bool) -> tuple:
    """The token in use once the port is ours, and whether it is the one saved in web-token. The saved
    token is read, or made, only when this dashboard uses it, so a read-only one, or one given a token
    (--token, POLYMATH_WEB_TOKEN), writes nothing. With rotate it is replaced, and every browser is
    signed out."""
    if rotate:
        saved = load_token(store, rotate=True)
        clear_sign_ins(store.root)
        return (token, False) if token else (saved, True)
    if token or not needed:
        return token, False
    return load_token(store), True


def build_server(store: engine.Store, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, token=None,
                 read_only: bool = False, allow_hosts=(), tls_cert=None, tls_key=None, verbose: bool = False,
                 rotate_token: bool = False, demo: bool = False, tutor: str = None, model: str = "opus",
                 effort: str = "low"):
    """Bind the dashboard. Returns (server, sign-in URL, lines to print); server is None if one is running."""
    if bool(tls_cert) != bool(tls_key):
        raise engine.PolymathError("Pass both --tls-cert and --tls-key, or neither.")
    if not (WEB_ROOT / "index.html").exists():
        raise engine.PolymathError(f"The dashboard files are missing from {WEB_ROOT}.")
    host = (host or DEFAULT_HOST).strip("[]")
    token = str(token or "").strip() or None
    try:
        if read_only:
            pen = None
        elif tutor or demo:  # fixed for this run by --tutor, or the demo's practice writer
            pen = polymath_writer.pick_writer(tutor or "fake", model, effort)
        else:
            pen = models_mod.startup_writer(store, model, effort)
    except polymath_writer.WriterError as exc:
        raise engine.PolymathError(str(exc))
    tls = tls_context(tls_cert, tls_key) if tls_cert else None
    scheme = "https" if tls else "http"
    allowed = set(allow_hosts or [])
    if host not in WILDCARD:
        allowed.add(host)
    app = App(store, read_only=read_only, allowed_hosts=allowed, secure=bool(tls), demo=demo)
    needed = needs_token(host, read_only, demo, pen)
    try:
        server = Server((host, port), app, verbose=verbose, tls=tls)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
            return already_running(store, host, port, scheme, token, rotate_token, needed)
        raise engine.PolymathError(f"Can't listen on {host}:{port} ({exc.strerror or exc}).")
    port = server.server_address[1]
    try:  # the token is created or replaced only now that the port is ours
        token, saved = start_tokens(store, token, rotate_token, needed)
    except engine.PolymathError:
        server.server_close()
        raise
    app.set_sign_in(token, saved, port)
    if pen:  # only once the port is ours, so a second copy of the dashboard never claims the study files
        app.study = study_mod.Study(store, pen)
        app.study.clock = app.clock
        app.models = models_mod.Models(store, app.study, locked=bool(tutor or demo), effort=effort)
        if bench_mod.ENABLED:
            app.bench = bench_mod.Benchmark(store, app.study, app.models)
            app.models.bench = app.bench
    app.goals = goals_mod.Goals(store, app.study, model_fn=lambda: app.model()[0])
    app.lessons = lessons_mod.Lessons(store, app.study)
    local_url = f"{scheme}://{link_host(host, bound_ip(server.server_address[0]), bool(tls))}:{port}/"
    sign_in = sign_in_link(local_url, token)
    lines = [f"Polymath dashboard{' (demo data)' if demo else ''}{' (read-only)' if read_only else ''}"
             f" · reading {store.root}"]
    if token:
        lines.append(f"  Sign in:  {sign_in}")
        lan = lan_address() if host in WILDCARD else None
        if lan:
            lines.append(f"  Network:  {sign_in_link(f'{scheme}://{lan}:{port}/', token)}")
        lines.append("  Open the sign-in link once; the browser stays signed in for 30 days"
                     + (f". The token is also in {store.root / TOKEN_FILE}." if app.token_in_file else "."))
        lines.append("  Anyone with the token can see your progress and study on your Claude usage; keep it private.")
    else:
        lines.append(f"  Open:     {local_url}")
    if not tls_cert and not is_loopback(host):
        lines.append("  Traffic is unencrypted: beyond a network you trust, use a tunnel or --tls-cert/--tls-key.")
    if pen and app.study.owner:
        lines.append(f"  Study:    questions written by {pen.describe()}")
    elif pen:
        lines.append("  Study:    off here (another dashboard is running study for this data folder)")
    elif not read_only:
        lines.append("  Study:    off (no AI model)")
    lines.append("Press Ctrl+C to stop.")
    return server, sign_in, lines


def serve(store: engine.Store, open_browser: bool = False, **options):
    """Serve the dashboard until Ctrl+C (options as for build_server)."""
    server, url, lines = build_server(store, **options)
    print("\n".join(lines), flush=True)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # `kill` or `docker stop`: shut down cleanly
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    if server is None:
        return
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if server.app.study:
            if server.app.bench:
                server.app.bench.close()  # a running benchmark pauses, to be resumed later
            server.app.study.stop()  # stops any question being written; open sessions resume next time
        for part in (server.app.goals, server.app.lessons):
            if part:
                part.stop()
        server.app.clock.flush()
        server.server_close()


# --- snapshot -------------------------------------------------------------------------------
def script_json(data) -> str:
    """JSON that is safe inside a <script> element."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    for raw, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                         (" ", "\\u2028"), (" ", "\\u2029")):
        text = text.replace(raw, escaped)
    return text


def snapshot_html(store: engine.Store) -> str:
    """The dashboard as one self-contained file: the same page, with the data inside it."""
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "app.css").read_text(encoding="utf-8")
    js = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    icon = (WEB_ROOT / "favicon.svg").read_text(encoding="utf-8")
    if "</script" in js.lower() or "</style" in css.lower():
        raise engine.PolymathError("The dashboard assets can't be inlined (they contain a closing tag).")
    state = collect_state(store, snapshot=True)
    state["app"].update(read_only=True, auth=False, demo=False)
    swaps = {
        '<link rel="stylesheet" href="app.css">': f"<style>{css}</style>",
        '<link rel="icon" href="favicon.svg">':
            f'<link rel="icon" href="data:image/svg+xml,{urllib.parse.quote(icon)}">',
        '<script src="app.js" defer></script>':
            f'<script id="polymath-data" type="application/json">{script_json(state)}</script>\n<script>{js}</script>',
    }
    for old, new in swaps.items():
        if page.count(old) != 1:
            raise engine.PolymathError(f"index.html no longer contains {old!r}.")
        page = page.replace(old, new)
    return page


# --- demo learner ----------------------------------------------------------------------------
DEMO_BELIEFS = {
    "salesforce.apex.async-schedulable": "a scheduled job can make a callout directly",
    "salesforce.apex.sharing-keywords": "a class with no sharing keyword runs with sharing",
    "salesforce.automation.order-of-execution": "before-save flows run after before triggers",
    "salesforce.soql.aggregates-binding-dynamic": "dynamic SOQL accepts field references as bind variables",
    "salesforce.ui.ui-security": "Visualforce escapes merge fields inside script tags",
    "salesforce.apex.dml-and-database-methods": "Database.insert with allOrNone=false rolls back every record",
    "salesforce.testing.async-and-limits": "async code runs as soon as it's enqueued in a test",
    "salesforce.ui.lwc-communication": "a child component can change a parent's @api property",
    "salesforce.apex.async-queueable": "a queueable job can chain any number of jobs in tests",
    "salesforce.apex.triggers-context": "Trigger.old is available in before insert",
    "salesforce.platform.platform-events": "publishing a platform event rolls back with the transaction",
    "salesforce.apex.governor-limits-in-code": "the SOQL limit is 100 queries in async Apex too",
}
DEMO_PLAN = """# PD1 plan (demo)

**Exam:** in three weeks. 60 questions, 105 minutes, pass mark 68%.

## Phases
1. **Probe and fix.** Probe every concept, hardest first; fix each miss the same day.
2. **Consolidate and mock.** Full mocks twice, weakest area in between.
3. **Final review.** Open mistakes and due reviews only.

## The gate
Book only when the predicted score is 75% or more, coverage is 90% or more, and the last
two mocks are 73% or more.
"""


def _quiet(fn, *args):
    with redirect_stdout(StringIO()):
        return fn(*args)


def build_demo(root: Path, days: int = 12, seed: int = 5) -> engine.Store:
    """A simulated learner: two weeks of PD1 study, so the dashboard can be previewed."""
    blueprint = engine.PLUGIN_ROOT / "blueprints" / "salesforce-platform-developer-1.json"
    rng = random.Random(seed)
    store = engine.Store(root)
    saved = os.environ.get("POLYMATH_NOW")
    today = engine.local_day(engine.now())

    def clock(day: date, minutes: float):
        moment = datetime.combine(day, dtime(19, 0)).astimezone() + timedelta(minutes=minutes)
        os.environ["POLYMATH_NOW"] = engine.iso(moment)

    try:
        first = today - timedelta(days=days)
        clock(first, -60)
        _quiet(engine.cmd_init, Namespace(name="Demo learner", persona=None, json=True), store)
        learner = store.learner()
        learner["preferences"].update(start_level="expert", block_size=3, session_minutes=120)
        store.save_learner(learner)
        data = json.loads(blueprint.read_text(encoding="utf-8"))
        slug = data["slug"]
        engine.import_track(store, data, target_date=(today + timedelta(days=21)).isoformat())
        (store.tracks_dir / slug / "plan.md").write_text(DEMO_PLAN, encoding="utf-8")
        strength = {"developer-fundamentals": 0.66, "process-automation-logic": 0.52,
                    "user-interface": 0.44, "testing-debugging-deployment": 0.5}
        truth = {}
        for d in data["domains"]:
            for c in d["concepts"]:
                truth[c["id"]] = engine.clamp(strength.get(d["id"], 0.6) + rng.uniform(-0.18, 0.15), 0.25, 0.95)
        rest_days = {3, 8}
        for n in range(days):
            day = first + timedelta(days=n)
            if n in rest_days:
                continue
            minute = rng.uniform(-30, 45)
            clock(day, minute)
            store.append({"type": "session_start", "track": slug, "focus": "placement" if n < 3 else None})
            for _ in range(rng.randint(5, 8)):
                minute += rng.uniform(4, 8)
                clock(day, minute)
                model = engine.Model(store)
                plan = model.plan([slug], 9)["items"]
                if not plan:
                    break
                items = plan[:2] + rng.sample(plan[2:], min(1, len(plan) - 2))  # what's due first, then a mix
                batch = []
                for it in items:
                    cid, kind = it["concept"], it["kind"]
                    chance = truth[cid] - 0.07 * (it["difficulty"] - 3) + (0.18 if kind == "mistake" else 0)
                    roll = rng.random()
                    result = ("correct" if roll < chance else
                              "partial" if roll < chance + 0.06 else "incorrect")
                    confidence = rng.choice([3, 4, 4, 5, 5] if result == "correct" else [2, 3, 3, 4, 5])
                    item = {"concept": cid, "result": result, "qtype": rng.choice(["scenario", "scenario", "mcq", "code"]),
                            "difficulty": it["difficulty"], "confidence": confidence,
                            "seconds": rng.randint(35, 160), "track": slug,
                            "question": rng.choice(["What happens at run time", "Which fix works", "Best approach here",
                                                    "What fails, and why", "Which statement is true", "Pick the right tool"])}
                    if kind == "mistake" and it.get("mistake"):
                        item["targets"] = it["mistake"]["id"]
                    if result == "incorrect":
                        if rng.random() < 0.08:
                            item["error"] = rng.choice(["slip", "trap"])
                        elif confidence >= 4 and cid in DEMO_BELIEFS:
                            item.update(error="misconception", misconception=DEMO_BELIEFS[cid])
                    batch.append(engine.build_attempt(item, store.concepts()))
                    truth[cid] = min(0.95, truth[cid] + rng.uniform(0.015, 0.05))  # practice teaches
                engine.record_events(store, batch)
            minute += rng.uniform(3, 6)
            clock(day, minute)
            summary = rng.choice([
                "Probed async Apex and triggers; fixed two misses on sharing.",
                "Worked through UI security and LWC data access; one repeat on escaping.",
                "Reviews first, then new probes in testing and deployment.",
                "Mixed review; order of execution still shaky.",
            ])
            _quiet(engine.cmd_session, Namespace(action="end", track=slug, focus=None, summary=summary, json=True),
                   store)
            if n == days - 3:  # a full mock, scored a little below what the practice suggests
                by_area = {d["id"]: round(engine.clamp(statistics.mean(truth[c["id"]] for c in d["concepts"]) - 0.06,
                                                       0.3, 0.95), 2) for d in data["domains"]}
                overall = sum(by_area[d["id"]] * d["weight"] for d in data["domains"])
                engine.log_mock(store, slug, round(overall, 2), by_area, 60, 104)
            if n in (2, days - 2):
                engine.add_note(store.tracks_dir / slug / "notes.md",
                                "Order of execution keeps tripping me: drill before-save flows vs triggers."
                                if n == 2 else "Mock 1 at 66%. UI security is the weakest area.",
                                title=f"Notes: {slug}")
    finally:
        if saved is None:
            os.environ.pop("POLYMATH_NOW", None)
        else:
            os.environ["POLYMATH_NOW"] = saved
    return store


def serve_demo(**options):
    root = Path(tempfile.mkdtemp(prefix="polymath-demo-"))
    try:
        print("Building a demo learner (two weeks of simulated PD1 study)...", flush=True)
        store = build_demo(root)
        return serve(store, demo=True, **options)
    finally:
        shutil.rmtree(root, ignore_errors=True)
