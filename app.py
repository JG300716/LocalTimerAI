"""LocalFlow: a loopback-only activity service. Python 3.10+, standard library."""
from __future__ import annotations

import argparse
import base64
import copy
import csv
from datetime import date, datetime, time as day_time, timedelta, timezone, tzinfo
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import math
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
LOG = logging.getLogger("localflow")


class WarsawTime(tzinfo):
    """EU DST fallback on Windows systems without the optional tzdata package."""
    @staticmethod
    def boundary(year, month):
        d = date(year, month + 1, 1) - timedelta(days=1)
        return datetime.combine(d - timedelta(days=(d.weekday() + 1) % 7), day_time(1))

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=None)
        start, end = self.boundary(dt.year, 3), self.boundary(dt.year, 10)
        summer = start <= utc < end
        local = utc + timedelta(hours=2 if summer else 1)
        return local.replace(tzinfo=self, fold=int(end <= utc < end + timedelta(hours=1)))

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        naive = dt.replace(tzinfo=None)
        start = self.boundary(dt.year, 3) + timedelta(hours=1)
        end = self.boundary(dt.year, 10) + timedelta(hours=2)
        if end - timedelta(hours=1) <= naive < end:
            return timedelta(hours=0 if dt.fold else 1)
        if start <= naive < start + timedelta(hours=1):
            return timedelta(hours=1 if dt.fold else 0)
        return timedelta(hours=int(start + timedelta(hours=1) <= naive < end))

    def utcoffset(self, dt):
        return timedelta(hours=1) + self.dst(dt)

    def tzname(self, dt):
        return "CEST" if self.dst(dt) else "CET"


def get_timezone(name="Europe/Warsaw"):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except (ImportError, KeyError):
        if name == "Europe/Warsaw":
            return WarsawTime()
        if name in ("UTC", "Etc/UTC"):
            return timezone.utc
        raise ValueError("Ta strefa wymaga pakietu tzdata; dostępne: Europe/Warsaw, UTC")


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Konfiguracja musi być obiektem JSON")
    for section in ("server", "tracking", "ai"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Brak sekcji {section}")
    def numeric(section, field, low, high, default):
        value = config[section].get(field, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{section}.{field}: dozwolone {low}–{high}")
    numeric("server", "port", 1024, 65535, 48763)
    if not isinstance(config["server"].get("port", 48763), int):
        raise ValueError("Port musi być liczbą całkowitą")
    numeric("tracking", "poll_seconds", 0.25, 60, 2)
    numeric("tracking", "idle_seconds", 10, 3600, 120)
    numeric("ai", "poll_seconds", 2, 3600, 15)
    for section, fields in (("tracking", ("enabled", "capture_titles")), ("ai", ("enabled",))):
        for field in fields:
            if field in config[section] and not isinstance(config[section][field], bool):
                raise ValueError(f"{section}.{field} musi mieć wartość true/false")
    apps = config.get("applications")
    if not isinstance(apps, list) or len(apps) > 500:
        raise ValueError("applications musi być listą, maks. 500 wpisów")
    names = set()
    for app in apps:
        if not isinstance(app, dict):
            raise ValueError("Wpis aplikacji musi być obiektem")
        exe = app.get("exe", "")
        if not isinstance(exe, str) or not re.fullmatch(r"[^\\/:*?\"<>|\x00-\x1f]{1,200}\.exe", exe, re.I):
            raise ValueError("Podaj samą nazwę pliku .exe, np. rider64.exe")
        if exe.casefold() in names:
            raise ValueError(f"Powtórzona nazwa procesu: {exe}")
        names.add(exe.casefold())
        if app.get("mode", "foreground") not in ("foreground", "running"):
            raise ValueError("Tryb aplikacji: foreground lub running")
        for key in ("label", "project", "category"):
            if key in app and (not isinstance(app[key], str) or len(app[key]) > 300):
                raise ValueError(f"Nieprawidłowe pole aplikacji: {key}")
        if "enabled" in app and not isinstance(app["enabled"], bool):
            raise ValueError("enabled musi mieć wartość true/false")
    for field in ("codex_paths", "claude_paths"):
        paths = config["ai"].get(field, [])
        if not isinstance(paths, list) or len(paths) > 50 or any(not isinstance(p, str) or not p or len(p) > 2000 for p in paths):
            raise ValueError(f"ai.{field} musi być listą ścieżek")
    get_timezone(config.get("timezone", "Europe/Warsaw"))
    return config


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.error = None
        self.stamp = None
        self.value = validate_config(json.loads(self.path.read_text(encoding="utf-8-sig")))
        self.stamp = self.path.stat().st_mtime_ns

    def get(self):
        with self.lock:
            try:
                stamp = self.path.stat().st_mtime_ns
                if stamp != self.stamp:
                    value = validate_config(json.loads(self.path.read_text(encoding="utf-8-sig")))
                    self.value, self.stamp, self.error = value, stamp, None
            except (OSError, ValueError, TypeError) as error:
                self.error = str(error)
            return copy.deepcopy(self.value)

    def save(self, value):
        validate_config(value)
        with self.lock:
            previous_port = self.value["server"].get("port", 48763)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.path.with_suffix(".json.bak").write_bytes(self.path.read_bytes())
            temporary.replace(self.path)
            self.value, self.stamp, self.error = copy.deepcopy(value), self.path.stat().st_mtime_ns, None
            return previous_port != value["server"].get("port", 48763)


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, source TEXT NOT NULL, timestamp REAL NOT NULL,
              duration REAL NOT NULL DEFAULT 0, project TEXT NOT NULL DEFAULT '',
              entity TEXT NOT NULL DEFAULT '', editor TEXT NOT NULL DEFAULT '',
              language TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL DEFAULT '', tokens_input INTEGER NOT NULL DEFAULT 0,
              tokens_output INTEGER NOT NULL DEFAULT 0, tokens_cached INTEGER NOT NULL DEFAULT 0,
              cost_usd REAL, external_id TEXT UNIQUE, meta TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
            CREATE INDEX IF NOT EXISTS events_desktop ON events(source,entity,timestamp);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS legacy (
              week_start TEXT PRIMARY KEY,week_end TEXT NOT NULL,seconds REAL NOT NULL);
        """)

    def state_get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def state_set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def emit(self, event):
        source = event.get("source")
        if source not in ("desktop", "wakatime", "ai"):
            raise ValueError("Nieznane źródło danych")
        timestamp, duration = float(event.get("timestamp", 0)), float(event.get("duration", 0))
        if not math.isfinite(timestamp) or not math.isfinite(duration) or timestamp < 946684800 or timestamp > time.time() + 300 or not 0 <= duration <= 7 * 86400:
            raise ValueError("Nieprawidłowy czas zdarzenia")
        row = {"source": source, "timestamp": timestamp, "duration": duration}
        for field in ("project", "entity", "editor", "language", "category", "model"):
            row[field] = str(event.get(field) or "")[:2000]
        for field in ("tokens_input", "tokens_output", "tokens_cached"):
            row[field] = max(0, min(10**15, int(event.get(field) or 0)))
        cost = event.get("cost_usd")
        row["cost_usd"] = float(cost) if cost is not None and math.isfinite(float(cost)) and float(cost) >= 0 else None
        row["external_id"] = str(event["external_id"])[:400] if event.get("external_id") else None
        row["meta"] = json.dumps(event.get("meta") or {}, ensure_ascii=False)
        if len(row["meta"]) > 16000:
            raise ValueError("Zbyt duże metadane")
        with self.lock, self.db:
            if source == "desktop" and not row["external_id"]:
                previous = self.db.execute("SELECT * FROM events WHERE source='desktop' AND entity=? ORDER BY timestamp DESC LIMIT 1", (row["entity"],)).fetchone()
                if previous and previous["duration"] + duration <= 300 and abs(timestamp - previous["timestamp"] - previous["duration"]) < 0.2 and all(previous[k] == row[k] for k in ("project", "editor", "language", "category", "meta")):
                    self.db.execute("UPDATE events SET duration=? WHERE id=?", (timestamp + duration - previous["timestamp"], previous["id"]))
                    return previous["id"]
            fields = list(row)
            cursor = self.db.execute(f"INSERT OR IGNORE INTO events ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", tuple(row.values()))
            return cursor.lastrowid if cursor.rowcount else None

    def events(self, start, end, heartbeat_timeout=120):
        with self.lock:
            rows = self.db.execute("SELECT * FROM events WHERE timestamp < ? AND (timestamp+duration > ? OR (source='wakatime' AND timestamp >= ?) OR (source='ai' AND timestamp>=?)) ORDER BY timestamp,id", (end, start, start - heartbeat_timeout, start)).fetchall()
        result = []
        for record in rows:
            row = dict(record)
            row["meta"] = json.loads(row["meta"])
            result.append(row)
        # Heartbeats represent activity up to the next heartbeat, capped by the
        # configured idle timeout. Keep raw data so estimates can be recomputed.
        streams = {}
        for row in result:
            if row["source"] == "wakatime" and row.get("category", "").casefold() not in ("ai coding", "ai", "ai_coding"):
                key = row["meta"].get("machine", "local")
                streams.setdefault(key, []).append(row)
        now = time.time()
        for rows in streams.values():
            for i, row in enumerate(rows):
                next_time = rows[i + 1]["timestamp"] if i + 1 < len(rows) else now
                row["duration"] = max(0, min(next_time, now, row["timestamp"] + heartbeat_timeout) - row["timestamp"])
        return result

    def import_legacy(self, rows):
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO legacy VALUES (?,?,?)", [(r["week_start"], r["week_end"], r["duration"]) for r in rows])

    def legacy(self, start, end):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM legacy WHERE week_end >= ? AND week_start <= ? ORDER BY week_start", (start, end))]

    def close(self):
        with self.lock:
            self.db.close()


def union_activity(rows, start, end):
    """Select one human activity at each instant; IDE heartbeats have priority."""
    points = []
    for i, row in enumerate(rows):
        if row["source"] == "ai" or row.get("meta", {}).get("mode") == "running" or row.get("category", "").casefold() in ("ai coding", "ai", "ai_coding"):
            continue
        a, b = max(start, row["timestamp"]), min(end, row["timestamp"] + row["duration"])
        if b > a:
            points.extend(((a, 1, i), (b, -1, i)))
    points.sort()
    active, segments, previous, pos = set(), [], None, 0
    while pos < len(points):
        stamp = points[pos][0]
        if previous is not None and stamp > previous and active:
            winner = max(active, key=lambda i: (rows[i]["source"] == "wakatime", rows[i]["timestamp"], i))
            segments.append({**rows[winner], "timestamp": previous, "duration": stamp - previous})
        while pos < len(points) and points[pos][0] == stamp:
            _, direction, index = points[pos]
            if direction > 0:
                active.add(index)
            else:
                active.discard(index)
            pos += 1
        previous = stamp
    # Rejoin fragments split only by a lower priority sample boundary.
    compact = []
    for row in segments:
        if compact and compact[-1]["id"] == row["id"] and abs(compact[-1]["timestamp"] + compact[-1]["duration"] - row["timestamp"]) < 0.001:
            compact[-1]["duration"] += row["duration"]
        else:
            compact.append(row)
    return compact


def split_days(timestamp, duration, zone):
    end = timestamp + duration
    while timestamp < end:
        local = datetime.fromtimestamp(timestamp, zone)
        tomorrow = datetime.combine(local.date() + timedelta(days=1), day_time(), zone).timestamp()
        finish = min(end, tomorrow)
        yield local.date().isoformat(), finish - timestamp
        timestamp = finish


def range_args(query, zone):
    today = datetime.now(zone).date()
    try:
        first = date.fromisoformat(query.get("start", [(today - timedelta(days=6)).isoformat()])[0])
        last = date.fromisoformat(query.get("end", [today.isoformat()])[0])
    except (ValueError, TypeError):
        raise ValueError("Podaj daty w formacie RRRR-MM-DD")
    if first > last or (last - first).days > 3660:
        raise ValueError("Zakres musi obejmować od 1 do 3661 dni")
    start = datetime.combine(first, day_time(), zone).timestamp()
    end = datetime.combine(last + timedelta(days=1), day_time(), zone).timestamp()
    return first, last, start, end


def dashboard(store, config, query):
    zone = get_timezone(config.get("timezone", "Europe/Warsaw"))
    first, last, start, end = range_args(query, zone)
    rows = store.events(start, end, config["tracking"].get("idle_seconds", 120))
    # Apply project filter after resolving overlaps; filtering first could
    # resurrect a lower-priority desktop estimate under an IDE interval.
    segments = union_activity(rows, start, end)
    project_filter = query.get("project", [""])[0]
    if project_filter:
        segments = [r for r in segments if r["project"] == project_filter]
    names = ("projects", "languages", "editors", "categories")
    groups = {name: {} for name in names}
    day_rows = {}
    for n in range((last - first).days + 1):
        key = (first + timedelta(days=n)).isoformat()
        day_rows[key] = {"date": key, "seconds": 0, "projects": {}}
    for row in segments:
        seconds = row["duration"]
        for name, field, fallback in (("projects", "project", "Bez projektu"), ("languages", "language", "Nierozpoznany"), ("editors", "editor", "Nieznany"), ("categories", "category", "coding")):
            key = row[field] or fallback
            groups[name][key] = groups[name].get(key, 0) + seconds
        for key, part in split_days(row["timestamp"], seconds, zone):
            if key in day_rows:
                day_rows[key]["seconds"] += part
                name = row["project"] or "Bez projektu"
                day_rows[key]["projects"][name] = day_rows[key]["projects"].get(name, 0) + part
    total = sum(r["duration"] for r in segments)
    active_days = sum(r["seconds"] > 0 for r in day_rows.values())
    best = max(day_rows.values(), key=lambda r: r["seconds"])
    today_key = datetime.now(zone).date().isoformat()
    today = day_rows.get(today_key, {}).get("seconds", 0)
    applications, sources = {}, {}
    ai = {"tokens_input": 0, "tokens_output": 0, "tokens_cached": 0, "total_seconds": 0, "events_count": 0, "models": [], "daily": [], "sessions": [], "cost_usd": None,
          "runtime_note": "Czas AI to zaobserwowany czas tur agenta, nie czas obliczeń modelu. Może nakładać się z Twoją pracą.", "cached_note": "Tokeny cache są częścią tokenów wejściowych."}
    models, ai_days = {}, {}
    for row in rows:
        if project_filter and row["project"] != project_filter:
            continue
        seconds = max(0, min(end, row["timestamp"] + row["duration"]) - max(start, row["timestamp"]))
        sources[row["source"]] = sources.get(row["source"], 0) + seconds
        if row["source"] == "desktop":
            mode = row["meta"].get("mode", "foreground")
            key = (row["entity"], mode)
            app = applications.setdefault(key, {"name": row["editor"], "exe": row["entity"], "seconds": 0, "mode": mode})
            app["seconds"] += seconds
        if row["source"] != "ai":
            continue
        in_range = start <= row["timestamp"] < end
        if not in_range and not seconds:
            continue
        model = row["model"] or "Nieznany model"
        target = models.setdefault(model, {"name": model, "tokens_input": 0, "tokens_output": 0, "tokens_cached": 0, "seconds": 0})
        if in_range:
            day = datetime.fromtimestamp(row["timestamp"], zone).date().isoformat()
            bucket = ai_days.setdefault(day, {"date": day, "tokens_input": 0, "tokens_output": 0, "seconds": 0})
            for field in ("tokens_input", "tokens_output", "tokens_cached"):
                ai[field] += row[field]
                target[field] += row[field]
                if field in bucket:
                    bucket[field] += row[field]
            ai["events_count"] += int(bool(row["tokens_input"] or row["tokens_output"]))
        target["seconds"] += seconds
        ai["total_seconds"] += seconds
        for day, part in split_days(max(start, row["timestamp"]), seconds, zone):
            bucket = ai_days.setdefault(day, {"date": day, "tokens_input": 0, "tokens_output": 0, "seconds": 0})
            bucket["seconds"] += part
        ai["sessions"].append({**row, "duration": seconds})
    ai["models"] = sorted(models.values(), key=lambda r: r["tokens_input"] + r["tokens_output"], reverse=True)
    ai["daily"] = sorted(ai_days.values(), key=lambda r: r["date"])
    ai["sessions"] = ai["sessions"][-500:][::-1]
    response = {"range": {"start": first.isoformat(), "end": last.isoformat(), "timezone": config.get("timezone", "Europe/Warsaw")},
                "available_projects": sorted({r["project"] for r in rows if r["project"]}),
                "summary": {"total_seconds": total, "today_seconds": today, "daily_average_seconds": total / active_days if active_days else 0,
                            "active_days": active_days, "calendar_average_seconds": total / len(day_rows), "best_day": {"date": best["date"], "seconds": best["seconds"]}, "projects_count": len(groups["projects"])},
                "daily": list(day_rows.values()), "applications": sorted(applications.values(), key=lambda r: r["seconds"], reverse=True),
                "sessions": sorted(segments, key=lambda r: r["timestamp"], reverse=True)[:1000], "sessions_total": len(segments),
                "ai": ai, "legacy": store.legacy(first.isoformat(), last.isoformat()), "sources": sources,
                "measurement_note": "Czas pracy to suma unikalnych przedziałów aktywności. IDE ma pierwszeństwo przed próbką .exe. Procesy w tle i AI są liczone osobno."}
    for name in names:
        response[name] = [{"name": k, "seconds": v} for k, v in sorted(groups[name].items(), key=lambda pair: pair[1], reverse=True)]
    return response


class Service:
    def __init__(self, config_path, data_dir, no_tracking=False):
        from tracker import DesktopTracker
        from integrations import AICollector
        self.config = Config(config_path)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_path = self.data_dir / "local.json"
        if key_path.exists():
            self.keys = json.loads(key_path.read_text(encoding="utf-8"))
        else:
            self.keys = {"api_key": str(uuid.uuid4()), "token": secrets.token_urlsafe(32)}
            key_path.write_text(json.dumps(self.keys, indent=2), encoding="utf-8")
        self.store = Store(self.data_dir / "activity.sqlite3")
        self.tracker = DesktopTracker(self.config.get, self.store.emit)
        self.ai = AICollector(self.config.get, self.store.emit, self.store.state_get, self.store.state_set)
        self.no_tracking = no_tracking
        self.http = None

    def status(self):
        config = self.config.get()
        tracking = {**self.tracker.status(), "enabled": bool(config["tracking"].get("enabled", True))}
        result = {"name": "LocalFlow", "version": VERSION, "tracking": tracking,
                  "integrations": {"ai": self.ai.status(), "wakatime": {"last_heartbeat": self.store.state_get("last_heartbeat"), "url": self.url + "/api/v1"}},
                  "config_path": str(self.config.path), "data_path": str(self.data_dir / "activity.sqlite3"),
                  "token": self.keys["token"], "now": time.time(), "config_error": self.config.error,
                  "url": self.url, "no_tracking": self.no_tracking}
        return result

    @property
    def url(self):
        return f"http://127.0.0.1:{self.http.server_port}" if self.http else ""

    def start_collectors(self):
        if not self.no_tracking:
            self.tracker.start()
            self.ai.start()

    def stop_collectors(self):
        self.tracker.stop()
        self.ai.stop()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalFlow/" + VERSION

    @property
    def service(self):
        return self.server.service

    def log_message(self, format, *args):
        # Never log request paths: they could contain an integration API key.
        LOG.debug("HTTP request %s", args[1] if len(args) > 1 else "")

    def send(self, value, status=200, kind="application/json; charset=utf-8", filename=None):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(payload)

    def check_host(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            self.send({"error": "Niedozwolony host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in allowed}:
            self.send({"error": "Niedozwolone pochodzenie żądania"}, 403)
            return False
        return True

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Nieprawidłowa długość żądania")
        if not 0 <= length <= 4 * 1024 * 1024:
            raise ValueError("Maksymalny rozmiar żądania: 4 MB")
        return json.loads(self.rfile.read(length) or b"{}")

    def authorized(self, waka=False):
        if waka:
            value = self.headers.get("Authorization", "")
            supplied = self.headers.get("X-API-Key", "")
            if value.lower().startswith("basic "):
                try:
                    supplied = base64.b64decode(value[6:], validate=True).decode().split(":", 1)[0]
                except (ValueError, UnicodeError):
                    supplied = ""
            elif value.lower().startswith("bearer "):
                supplied = value[7:]
            key = self.service.keys["api_key"]
        else:
            supplied, key = self.headers.get("X-LocalFlow-Token", ""), self.service.keys["token"]
        if not hmac.compare_digest(supplied.encode("utf-8"), key.encode("utf-8")):
            self.send({"error": "Nieprawidłowy klucz lokalnego API"}, 401)
            return False
        return True

    def do_GET(self):
        if not self.check_host():
            return
        route = urlparse(self.path)
        try:
            if route.path == "/api/status":
                self.send(self.service.status())
            elif route.path == "/api/config":
                self.send(self.service.config.get())
            elif route.path == "/api/dashboard":
                self.send(dashboard(self.service.store, self.service.config.get(), parse_qs(route.query)))
            elif route.path == "/api/export":
                self.export(parse_qs(route.query))
            else:
                name = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}.get(route.path)
                if not name:
                    self.send({"error": "Nie znaleziono"}, 404)
                    return
                mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[Path(name).suffix]
                self.send((ROOT / "web" / name).read_bytes(), kind=mime + "; charset=utf-8")
        except (ValueError, TypeError, KeyError) as error:
            self.send({"error": str(error)}, 400)
        except (OSError, sqlite3.Error):
            LOG.exception("Read failure")
            self.send({"error": "Błąd lokalnego pliku lub bazy danych; zobacz data/localflow.log"}, 500)

    def do_POST(self):
        if not self.check_host():
            return
        path = urlparse(self.path).path.rstrip("/")
        waka = path in ("/api/v1/users/current/heartbeats", "/api/v1/users/current/heartbeats.bulk")
        if not self.authorized(waka):
            return
        try:
            body = self.read_body()
            if not waka and not isinstance(body, dict):
                raise ValueError("Treść żądania musi być obiektem JSON")
            if waka:
                bulk = path.endswith(".bulk")
                if bulk and (not isinstance(body, list) or not 1 <= len(body) <= 25):
                    raise ValueError("Heartbeat bulk wymaga listy od 1 do 25 wpisów")
                responses = []
                for item in body if bulk else [body]:
                    try:
                        event_id = self.heartbeat(item)
                        responses.append([{"data": {"id": event_id}}, 201])
                    except (ValueError, TypeError) as error:
                        responses.append([{"error": str(error)}, 400])
                self.send({"responses": responses} if bulk else responses[0][0], 201 if bulk else responses[0][1])
            elif path == "/api/tracking":
                if not isinstance(body, dict) or not isinstance(body.get("paused"), bool):
                    raise ValueError("paused musi mieć wartość true/false")
                self.service.tracker.pause(body["paused"])
                self.send(self.service.tracker.status())
            elif path == "/api/config":
                restart = self.service.config.save(body)
                self.send({"ok": True, "restart_required": restart})
            elif path == "/api/integrations/wakatime/setup":
                from integrations import configure_wakatime
                mode = body.get("mode", "mirror")
                if mode not in ("mirror", "local-only"):
                    raise ValueError("Tryb: mirror lub local-only")
                result = configure_wakatime(Path.home() / ".wakatime.cfg", self.service.url, self.service.keys["api_key"], mode="local" if mode == "local-only" else mode)
                self.send({"ok": True, "result": result})
            elif path == "/api/import/legacy":
                from tracker import parse_legacy
                rows = parse_legacy(body.get("text", ""))
                if not rows:
                    raise ValueError("Plik nie zawiera wyników tygodniowych")
                self.service.store.import_legacy(rows)
                self.send({"ok": True, "imported": len(rows)})
            elif path == "/api/shutdown":
                self.send({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send({"error": "Nie znaleziono"}, 404)
        except (ValueError, TypeError, KeyError) as error:
            self.send({"error": str(error)}, 400)
        except (OSError, sqlite3.Error):
            LOG.exception("Write failure")
            self.send({"error": "Błąd zapisu lokalnego pliku; zobacz data/localflow.log"}, 500)

    def heartbeat(self, item):
        from integrations import parse_editor
        if not isinstance(item, dict) or "time" not in item:
            raise ValueError("Brak czasu heartbeat")
        ua = item.get("user_agent") or self.headers.get("User-Agent", "")
        editor = str(item.get("editor") or parse_editor(ua))[:100]
        # No file contents, line numbers, cursor positions, prompts or code.
        raw_entity = str(item.get("entity", ""))
        filename = raw_entity.replace("\\", "/").rsplit("/", 1)[-1]
        machine = str(item.get("machine") or self.headers.get("X-Machine-Name", "local"))[:200]
        identity = json.dumps([item["time"], raw_entity, machine, item.get("type"), item.get("project")], sort_keys=True)
        event_id = hashlib.sha256(identity.encode()).hexdigest()
        self.service.store.emit({"source": "wakatime", "timestamp": item["time"], "duration": 0,
                                 "project": item.get("project") or "Bez projektu", "entity": filename,
                                 "editor": editor, "language": item.get("language", ""),
                                 "category": item.get("category") or "coding", "external_id": "waka:" + event_id,
                                 "meta": {"machine": machine, "is_write": bool(item.get("is_write")), "type": item.get("type", "file"), "branch": str(item.get("branch") or "")[:200]}})
        self.service.store.state_set("last_heartbeat", time.time())
        return event_id

    def export(self, query):
        config = self.service.config.get()
        zone = get_timezone(config.get("timezone", "Europe/Warsaw"))
        first, last, start, end = range_args(query, zone)
        rows = self.service.store.events(start, end, config["tracking"].get("idle_seconds", 120))
        if query.get("project", [""])[0]:
            rows = [r for r in rows if r["project"] == query["project"][0]]
        for row in rows:
            row["duration"] = max(0, min(end, row["timestamp"] + row["duration"]) - max(start, row["timestamp"]))
            row["timestamp"] = max(start, row["timestamp"])
        if query.get("format", ["json"])[0] == "csv":
            stream = io.StringIO(newline="")
            fields = ["source", "timestamp", "duration", "project", "entity", "editor", "language", "category", "model", "tokens_input", "tokens_output", "tokens_cached", "cost_usd"]
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                # Prevent spreadsheet formula execution when exporting names.
                writer.writerow({k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v) for k, v in row.items()})
            self.send(("\ufeff" + stream.getvalue()).encode("utf-8"), kind="text/csv; charset=utf-8", filename="localflow-events.csv")
        else:
            self.send({"version": VERSION, "range": {"start": first.isoformat(), "end": last.isoformat()}, "events": rows,
                       "legacy": self.service.store.legacy(first.isoformat(), last.isoformat()),
                       "note": "Surowe pomiary źródeł mogą nakładać się. Dashboard scala przedziały pracy człowieka."}, filename="localflow-events.json")


def main():
    parser = argparse.ArgumentParser(description="LocalFlow — lokalny tracker i dashboard")
    parser.add_argument("--port", type=int)
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tracking", action="store_true", help="Podgląd bez odczytu procesów i logów AI")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    config = Config(args.config)
    port = args.port or config.get()["server"].get("port", 48763)
    local_url = f"http://127.0.0.1:{port}"
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / "localflow.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    if args.stop:
        try:
            keys = json.loads((data_dir / "local.json").read_text(encoding="utf-8"))
            request = Request(local_url + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json", "X-LocalFlow-Token": keys["token"]})
            with urlopen(request, timeout=4) as response:
                print("LocalFlow stopped:", response.status)
        except (OSError, ValueError) as error:
            print("Cannot stop LocalFlow:", error)
            return 1
        return 0
    try:
        http = Server(("127.0.0.1", port), Handler)
    except OSError:
        try:
            with urlopen(local_url + "/api/status", timeout=2) as response:
                existing = json.load(response)
            if existing.get("name") == "LocalFlow":
                if not args.no_browser:
                    webbrowser.open(local_url)
                print("LocalFlow already running:", local_url)
                return 0
        except (OSError, ValueError):
            pass
        print(f"Port {port} is in use. Choose another with --port.")
        return 1
    service = Service(args.config, data_dir, args.no_tracking)
    service.http = http
    http.service = service
    service.start_collectors()
    print(f"LocalFlow {VERSION}  {local_url}", flush=True)
    print(f"Config: {args.config}\nData: {data_dir}", flush=True)
    if not args.no_browser:
        webbrowser.open(local_url)
    def shutdown(*_):
        threading.Thread(target=http.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        http.serve_forever(poll_interval=0.25)
    finally:
        http.server_close()
        service.stop_collectors()
        service.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
