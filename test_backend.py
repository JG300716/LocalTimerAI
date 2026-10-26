"""Isolated correctness and HTTP boundary tests. Never touches real user settings."""
import base64
import copy
import csv
from datetime import date, datetime, timedelta, timezone
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import Config, Handler, Server, Service, Store, WarsawTime, dashboard, split_days, union_activity


BASE_TIME = datetime(2026, 10, 20, 10, tzinfo=timezone.utc).timestamp()


def configuration():
    return {"server": {"port": 48763}, "timezone": "Europe/Warsaw",
            "tracking": {"enabled": False, "poll_seconds": 2, "idle_seconds": 120},
            "applications": [], "ai": {"enabled": False, "poll_seconds": 15,
                                         "codex_paths": [], "claude_paths": []}}


def event(index=1, offset=0, seconds=60, source="desktop", project="Desktop", mode="foreground", **extra):
    return {"id": index, "source": source, "timestamp": BASE_TIME + offset, "duration": seconds,
            "project": project, "entity": "test.exe", "editor": "Test editor", "language": "Python",
            "category": "coding", "model": "", "tokens_input": 0, "tokens_output": 0,
            "tokens_cached": 0, "cost_usd": None, "meta": {"mode": mode}, **extra}


class ActivityTests(unittest.TestCase):
    def test_overlaps_are_unioned_with_ide_priority(self):
        rows = [event(1, seconds=100), event(2, 20, 40, "wakatime", "IDE"),
                event(3, 30, 50, project="Other desktop")]
        segments = union_activity(rows, BASE_TIME, BASE_TIME + 100)
        self.assertEqual(sum(row["duration"] for row in segments), 100)
        self.assertEqual([(r["project"], r["duration"]) for r in segments],
                         [("Desktop", 20), ("IDE", 40), ("Other desktop", 20), ("Desktop", 20)])

    def test_range_clips_half_open_intervals_and_excludes_background_and_ai(self):
        rows = [event(1, -10, 40), event(2, 0, 100, mode="running"),
                event(3, 0, 100, source="ai"), event(4, 40, 20),
                event(5, 0, 100, source="wakatime", category="ai coding")]
        segments = union_activity(rows, BASE_TIME, BASE_TIME + 40)
        self.assertEqual([(r["id"], r["duration"]) for r in segments], [(1, 30)])

    def test_project_filter_cannot_resurrect_lower_priority_estimate(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.emit(event(1, seconds=120, project="Desktop"))
        store.emit(event(2, 0, 0, "wakatime", "IDE", external_id="waka-test"))
        query = {"start": ["2026-10-20"], "end": ["2026-10-20"], "project": ["Desktop"]}
        result = dashboard(store, configuration(), query)
        self.assertEqual(result["summary"]["total_seconds"], 0)
        self.assertEqual(result["projects"], [])


class TimezoneTests(unittest.TestCase):
    def test_dst_days_have_23_and_25_hours(self):
        zone = WarsawTime()
        for first, hours in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            start = datetime.combine(first, datetime.min.time(), zone).timestamp()
            end = datetime.combine(first + timedelta(days=1), datetime.min.time(), zone).timestamp()
            self.assertEqual(end - start, hours * 3600)
            self.assertEqual(list(split_days(start, end - start, zone)), [(first.isoformat(), hours * 3600)])

    def test_split_midnight_preserves_duration(self):
        zone = WarsawTime()
        start = datetime(2026, 10, 24, 23, 30, tzinfo=zone).timestamp()
        parts = list(split_days(start, 27 * 3600, zone))
        self.assertEqual(parts, [("2026-10-24", 1800), ("2026-10-25", 25 * 3600), ("2026-10-26", 5400)])
        self.assertEqual(sum(seconds for _, seconds in parts), 27 * 3600)

    def test_fallback_matches_zoneinfo_on_transition_instants_and_folds(self):
        try:
            reference = ZoneInfo("Europe/Warsaw")
        except ZoneInfoNotFoundError:
            self.skipTest("Independent tzdata reference is not installed")
        fallback = WarsawTime()
        for year in range(2024, 2031):
            for month in (3, 10):
                boundary = fallback.boundary(year, month).replace(tzinfo=timezone.utc)
                for minutes in (-121, -61, -60, -1, 0, 1, 59, 60, 61, 121):
                    instant = boundary + timedelta(minutes=minutes)
                    expected, actual = instant.astimezone(reference), instant.astimezone(fallback)
                    self.assertEqual(actual.isoformat(), expected.isoformat(), str(instant))
                    self.assertEqual(actual.fold, expected.fold, str(instant))
                    self.assertEqual(actual.timestamp(), expected.timestamp(), str(instant))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="localflow-config-test-")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "config.json"
        self.good = configuration()
        self.path.write_text(json.dumps(self.good), encoding="utf-8")
        self.config = Config(self.path)

    def test_bad_disk_edits_keep_last_good_then_valid_edit_recovers(self):
        self.path.write_text("{invalid", encoding="utf-8")
        self.assertEqual(self.config.get(), self.good)
        self.assertTrue(self.config.error)
        changed = copy.deepcopy(self.good)
        changed["tracking"]["idle_seconds"] = 300
        self.path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertEqual(self.config.get(), changed)
        self.assertIsNone(self.config.error)

    def test_invalid_save_is_atomic_and_valid_save_backs_up(self):
        before = self.path.read_bytes()
        invalid = copy.deepcopy(self.good)
        invalid["applications"] = [{"exe": "../bad.exe"}]
        with self.assertRaises(ValueError):
            self.config.save(invalid)
        self.assertEqual(self.path.read_bytes(), before)
        changed = copy.deepcopy(self.good)
        changed["server"]["port"] += 1
        self.assertTrue(self.config.save(changed))
        self.assertEqual(self.path.with_suffix(".json.bak").read_bytes(), before)
        self.assertEqual(self.config.get(), changed)

    def test_returned_config_cannot_mutate_live_settings(self):
        self.config.get()["tracking"]["idle_seconds"] = 999
        self.assertEqual(self.config.get()["tracking"]["idle_seconds"], 120)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_external_ids_are_idempotent(self):
        item = event(source="ai", external_id="unique", tokens_input=100)
        self.assertIsNotNone(self.store.emit(item))
        self.assertIsNone(self.store.emit(item))
        rows = self.store.events(BASE_TIME, BASE_TIME + 1000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tokens_input"], 100)

    def test_adjacent_desktop_samples_coalesce_but_distinct_modes_do_not(self):
        self.store.emit(event(1, 0, 2))
        self.store.emit(event(2, 2, 2))
        self.store.emit(event(3, 4, 2, mode="running"))
        rows = self.store.events(BASE_TIME, BASE_TIME + 100)
        self.assertEqual([(r["duration"], r["meta"]["mode"]) for r in rows], [(4, "foreground"), (2, "running")])

    def test_coalescing_stays_bounded_and_does_not_join_across_idle_gap(self):
        self.store.emit(event(1, 0, 299))
        self.store.emit(event(2, 299, 2))
        self.store.emit(event(3, 310, 2))
        self.assertEqual(len(self.store.events(BASE_TIME, BASE_TIME + 400)), 3)

    def test_heartbeats_are_timeout_bounded_and_retries_do_not_add_time(self):
        self.store.emit(event(1, 0, 0, source="wakatime", external_id="h1"))
        self.store.emit(event(2, 30, 0, source="wakatime", external_id="h2"))
        rows = self.store.events(BASE_TIME, BASE_TIME + 1000, heartbeat_timeout=120)
        self.assertEqual([r["duration"] for r in rows], [30, 120])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="localflow-http-test-")
        cls.config_path = Path(cls.folder.name) / "config.json"
        cls.config_path.write_text(json.dumps(configuration()), encoding="utf-8")
        cls.service = Service(cls.config_path, Path(cls.folder.name) / "data", no_tracking=True)
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.server.service = cls.service
        cls.service.http = cls.server
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.service.store.close()
        cls.folder.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            supplied = {"Content-Type": "application/json", **(headers or {})}
            connection.request(method, path, json.dumps(body) if body is not None else None, supplied)
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8-sig")
        finally:
            connection.close()

    def test_host_origin_and_csrf_boundaries(self):
        self.assertEqual(self.request("GET", "/api/status", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/status", headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/tracking", {"paused": True})[0], 401)
        token = {"X-LocalFlow-Token": self.service.keys["token"]}
        self.assertEqual(self.request("POST", "/api/tracking", {"paused": True}, token)[0], 200)
        self.assertTrue(self.service.tracker.status()["paused"])
        self.assertEqual(self.request("POST", "/api/tracking", {"paused": False}, token)[0], 200)

    def test_basic_single_and_bulk_heartbeat_contract(self):
        authorization = "Basic " + base64.b64encode((self.service.keys["api_key"] + ":").encode()).decode()
        route = "/api/v1/users/current/heartbeats"
        self.assertEqual(self.request("POST", route, {"time": BASE_TIME})[0], 401)
        heartbeat = {"time": BASE_TIME + 600, "entity": "C:\\private\\project\\test.py",
                     "project": "HTTP heartbeat", "type": "file", "language": "Python"}
        code, raw = self.request("POST", route, heartbeat, {"Authorization": authorization})
        self.assertEqual(code, 201)
        event_id = json.loads(raw)["data"]["id"]
        code, raw = self.request("POST", route + ".bulk", [heartbeat, {"entity": "missing time"}], {"Authorization": authorization})
        self.assertEqual(code, 201)
        responses = json.loads(raw)["responses"]
        self.assertEqual([r[1] for r in responses], [201, 400])
        self.assertEqual(responses[0][0]["data"]["id"], event_id)
        rows = [r for r in self.service.store.events(BASE_TIME, BASE_TIME + 1000) if r["project"] == "HTTP heartbeat"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entity"], "test.py")

    def test_csv_neutralizes_formula_project_names(self):
        self.service.store.emit(event(offset=1000, project="=SUM(1,2)"))
        code, raw = self.request("GET", "/api/export?format=csv&start=2026-10-20&end=2026-10-20")
        self.assertEqual(code, 200)
        rows = list(csv.DictReader(io.StringIO(raw)))
        formula = next(row for row in rows if "SUM" in row["project"])
        self.assertEqual(formula["project"], "'=SUM(1,2)")

    def test_malformed_json_shape_returns_bad_request(self):
        token = {"X-LocalFlow-Token": self.service.keys["token"]}
        self.assertEqual(self.request("POST", "/api/import/legacy", [], token)[0], 400)


class ProcessLifecycleTests(unittest.TestCase):
    def test_shutdown_restart_preserves_config_history_and_heartbeat_data(self):
        with tempfile.TemporaryDirectory(prefix="localflow-lifecycle-test-") as folder:
            root = Path(folder)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(configuration()), encoding="utf-8")
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            command = [sys.executable, str(Path(__file__).with_name("app.py")),
                       "--config", str(config_path), "--data-dir", str(root / "data"),
                       "--port", str(port), "--no-browser", "--no-tracking"]

            def request(method, path, body=None, headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request(method, path, json.dumps(body) if body is not None else None,
                                       {"Content-Type": "application/json", **(headers or {})})
                    response = connection.getresponse()
                    raw = response.read().decode("utf-8-sig")
                    return response.status, raw
                finally:
                    connection.close()

            def launch():
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("Isolated server exited: " + process.stderr.read().decode(errors="replace"))
                    try:
                        code, body = request("GET", "/api/status")
                        if code == 200:
                            return process, json.loads(body)
                    except OSError:
                        time.sleep(0.05)
                process.terminate()
                process.wait(timeout=5)
                self.fail("Isolated server did not become ready")

            process = None
            try:
                process, status = launch()
                token = {"X-LocalFlow-Token": status["token"]}
                self.assertEqual(status["name"], "LocalFlow")
                self.assertEqual(request("POST", "/api/shutdown", {})[0], 401)
                self.assertEqual(request("POST", "/api/tracking", {"paused": True}, token)[0], 200)
                self.assertTrue(json.loads(request("GET", "/api/status")[1])["tracking"]["paused"])
                self.assertEqual(request("POST", "/api/tracking", {"paused": False}, token)[0], 200)
                self.assertFalse(json.loads(request("GET", "/api/status")[1])["tracking"]["paused"])

                changed = configuration()
                changed["tracking"]["idle_seconds"] = 180
                changed["applications"] = [{"exe": "example.exe", "mode": "running", "label": "Example"}]
                self.assertEqual(request("POST", "/api/config", changed, token)[0], 200)
                self.assertEqual(json.loads(request("GET", "/api/config")[1]), changed)
                self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), changed)
                invalid = copy.deepcopy(changed)
                invalid["applications"][0]["exe"] = "../unsafe.exe"
                self.assertEqual(request("POST", "/api/config", invalid, token)[0], 400)
                self.assertEqual(json.loads(request("GET", "/api/config")[1]), changed)

                key = json.loads((root / "data" / "local.json").read_text())["api_key"]
                heartbeat = {"time": BASE_TIME, "entity": "C:/Private/Fixture/main.py",
                             "project": "Lifecycle", "type": "file", "language": "Python",
                             "user_agent": "rider/2026.2 rider-wakatime/16.0.0"}
                for _ in range(2):
                    self.assertEqual(request("POST", "/api/v1/users/current/heartbeats", heartbeat,
                                             {"X-API-Key": key})[0], 201)
                legacy = {"text": "2026 Week: 10.19-10.25 Duration: 2h:03m:04s"}
                self.assertEqual(request("POST", "/api/import/legacy", legacy, token)[0], 200)
                route = "/api/export?format=json&start=2026-10-19&end=2026-10-25"
                exported = json.loads(request("GET", route)[1])
                self.assertEqual(len(exported["events"]), 1)
                self.assertEqual(exported["events"][0]["entity"], "main.py")
                self.assertEqual(exported["events"][0]["editor"], "Rider")
                self.assertEqual(exported["legacy"][0]["seconds"], 7384)
                dashboard_data = json.loads(request("GET", "/api/dashboard?start=2026-10-19&end=2026-10-25&project=Lifecycle")[1])
                self.assertEqual(dashboard_data["summary"]["total_seconds"], 180)
                self.assertEqual(dashboard_data["projects"], [{"name": "Lifecycle", "seconds": 180}])
                csv_text = request("GET", "/api/export?format=csv&start=2026-10-19&end=2026-10-25")[1]
                self.assertEqual(len(list(csv.DictReader(io.StringIO(csv_text)))), 1)
                for asset in ("/", "/app.js", "/styles.css"):
                    code, body = request("GET", asset)
                    self.assertEqual(code, 200, asset)
                    self.assertTrue(body, asset)

                self.assertEqual(request("POST", "/api/shutdown", {}, token)[0], 200)
                self.assertEqual(process.wait(timeout=8), 0)
                process.stderr.close()
                process, restarted = launch()
                self.assertEqual(restarted["token"], status["token"])
                self.assertEqual(json.loads(request("GET", "/api/config")[1]), changed)
                self.assertEqual(json.loads(request("GET", route)[1]), exported)
                self.assertEqual(request("POST", "/api/shutdown", {}, token)[0], 200)
                self.assertEqual(process.wait(timeout=8), 0)
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
