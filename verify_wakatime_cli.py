"""Optional real WakaTime CLI smoke test, with fully isolated temporary state.

Usage: python verify_wakatime_cli.py [--cli PATH]
No real WakaTime configuration, offline queue or LocalFlow data is modified.
"""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import threading
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", default=str(Path.home() / ".wakatime" / "wakatime-cli-windows-amd64.exe"))
    parser.add_argument("--mirror", action="store_true", help="Sprawdź [api_urls] z drugim, także lokalnym serwerem")
    args = parser.parse_args()
    cli = Path(args.cli)
    if not cli.is_file():
        parser.error("Brak WakaTime CLI; podaj --cli PATH")
    with tempfile.TemporaryDirectory(prefix="localflow-cli-test-") as temporary:
        test_dir = Path(temporary)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        config = json.loads((root / "config.json").read_text(encoding="utf-8-sig"))
        config["server"]["port"] = port
        config["tracking"]["enabled"] = False
        config["ai"]["enabled"] = False
        config_path = test_dir / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        data_dir = test_dir / "data"
        local_url = f"http://127.0.0.1:{port}"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        server = subprocess.Popen(
            [sys.executable, str(root / "app.py"), "--config", str(config_path),
             "--data-dir", str(data_dir), "--no-browser", "--no-tracking"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        keys = None
        primary = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with urlopen(local_url + "/api/status", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Testowy serwer LocalFlow nie wystartował")
            keys = json.loads((data_dir / "local.json").read_text(encoding="utf-8"))
            waka_path = test_dir / ".wakatime.cfg"
            waka_path.write_text(
                "[settings]\napi_url = " + local_url + "/api/v1\napi_key = " + keys["api_key"]
                + "\noffline = false\nsync_ai_disabled = true\nmetrics = false\n",
                encoding="utf-8")
            if args.mirror:
                from integrations import configure_wakatime
                class PrimaryHandler(BaseHTTPRequestHandler):
                    def log_message(self, *_):
                        pass
                    def do_POST(self):
                        self.server.requests += 1
                        content = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                        value = {"responses": [[{"data": {"id": "primary-smoke"}}, 201] for _ in content]} if isinstance(content, list) else {"data": {"id": "primary-smoke"}}
                        payload = json.dumps(value).encode()
                        self.send_response(201)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                primary = ThreadingHTTPServer(("127.0.0.1", 0), PrimaryHandler)
                primary.requests = 0
                threading.Thread(target=primary.serve_forever, daemon=True).start()
                primary_key = "00000000-0000-4000-8000-000000000000"
                waka_path.write_text("[settings]\napi_url = http://127.0.0.1:" + str(primary.server_port) + "/api/v1\napi_key = " + primary_key + "\noffline = false\nsync_ai_disabled = true\nmetrics = false\n", encoding="utf-8")
                configure_wakatime(waka_path, local_url, keys["api_key"], mode="mirror")
            env = dict(os.environ, WAKATIME_HOME=str(test_dir), NO_PROXY="127.0.0.1,localhost")
            command = [str(cli), "--config", str(waka_path), "--internal-config", str(test_dir / "internal.cfg"),
                 "--log-file", str(test_dir / "wakatime.log"), "--entity", "LocalFlow-Smoke.exe", "--entity-type", "app",
                 "--project", "LocalFlow-Smoke", "--plugin", "rider/2026.2 rider-wakatime/16.0.0",
                 "--hostname", "localflow-smoke", "--disable-offline", "--sync-ai-disabled",
                 "--heartbeat-rate-limit-seconds", "0", "--timeout", "5"]
            result = subprocess.run(command,
                env=env, cwd=test_dir, capture_output=True, timeout=20, creationflags=flags)
            if result.returncode:
                # The isolated log contains only dummy data. Do not print keys
                # or the full log, even when the CLI rejects the response.
                raise AssertionError(f"WakaTime CLI zakończył się kodem {result.returncode}")
            with closing(sqlite3.connect(data_dir / "activity.sqlite3")) as db:
                rows = db.execute("SELECT source,project,entity,editor FROM events").fetchall()
            assert len(rows) == 1, f"Oczekiwano 1 heartbeat, otrzymano {len(rows)}"
            assert rows[0] == ("wakatime", "LocalFlow-Smoke", "LocalFlow-Smoke.exe", "Rider"), rows
            if primary:
                assert primary.requests > 0, "Główny lokalny serwer nie otrzymał heartbeat"
            print(json.dumps({"real_cli_exit_code": result.returncode, "heartbeats": len(rows),
                              "editor": rows[0][3], "mirror": args.mirror, "isolated_data": True, "cloud_contact": False}))
        finally:
            if primary:
                primary.shutdown()
                primary.server_close()
            if keys and server.poll() is None:
                try:
                    request = Request(local_url + "/api/shutdown", data=b"{}",
                                      headers={"Content-Type": "application/json", "X-LocalFlow-Token": keys["token"]})
                    urlopen(request, timeout=3).close()
                except OSError:
                    pass
            try:
                server.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate(timeout=5)


if __name__ == "__main__":
    main()
