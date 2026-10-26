"""Validate a built release archive without using live user configuration or data."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler, Request
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "app.py").read_text(encoding="utf-8"), re.M)[1]
ARCHIVE = Path(os.environ.get("LOCALFLOW_RELEASE_ZIP", str(ROOT / "dist" / f"LocalTimerAI-{VERSION}-windows-x64.zip")))
RELEASE_FILES = {
    "app.py", "tracker.py", "integrations.py", "Launcher.cs", "BuildLauncher.ps1",
    "Start.cmd", "Stop.cmd", "StartHidden.ps1", "StartHidden.vbs",
    "BuffedTestVoid.vbs",
    "EnableAutostart.ps1", "DisableAutostart.ps1", "README.md", "INTEGRATIONS.md", "TESTING.md",
    "web/index.html", "web/app.js", "web/styles.css", "config.json", "LocalFlow.exe", "RELEASE.txt",
}


@unittest.skipUnless(ARCHIVE.is_file(), "Build the release archive with scripts/Build-Release.ps1 first")
class ReleaseArchiveTests(unittest.TestCase):
    def test_manifest_checksum_and_clean_configuration(self):
        with ZipFile(ARCHIVE) as archive:
            self.assertEqual(set(archive.namelist()), RELEASE_FILES)
            self.assertIsNone(archive.testzip(), "Archive CRC validation failed")
            self.assertEqual(json.loads(archive.read("config.json")),
                             json.loads((ROOT / "config.example.json").read_text(encoding="utf-8-sig")))
            notice = archive.read("RELEASE.txt").decode("utf-8")
            self.assertIn("Python 3.10 or newer", notice)
            self.assertIn("does not bundle Python", notice)
        expected = ARCHIVE.with_name("SHA256SUMS").read_text(encoding="utf-8").strip()
        self.assertEqual(expected, hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() + "  " + ARCHIVE.name)

    def test_extracted_release_starts_serves_pages_and_shuts_down_cleanly(self):
        with tempfile.TemporaryDirectory(prefix="LocalTimerAI release startup ") as folder:
            root = Path(folder)
            with ZipFile(ARCHIVE) as archive:
                self.assertEqual(set(archive.namelist()), RELEASE_FILES)
                archive.extractall(root)
            self.assertFalse((root / "data").exists())
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = reserved.getsockname()[1]
            config_path = root / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            config["server"]["port"] = port
            config_path.write_text(json.dumps(config), encoding="utf-8")
            process = subprocess.Popen([
                sys.executable, str(root / "app.py"), "--no-browser", "--no-tracking",
                "--config", str(config_path), "--data-dir", str(root / "data"),
            ], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            opener = build_opener(ProxyHandler({}))
            address = f"http://127.0.0.1:{port}"
            try:
                status = None
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        self.fail(f"Extracted service exited early: {stdout}\n{stderr}")
                    try:
                        with opener.open(address + "/api/status", timeout=1) as response:
                            status = json.load(response)
                        break
                    except (URLError, OSError):
                        time.sleep(0.1)
                self.assertIsNotNone(status, "Extracted service did not become ready")
                self.assertEqual(status["name"], "LocalFlow")
                self.assertEqual(status["version"], VERSION)
                self.assertTrue(status["no_tracking"])
                for path in ("/", "/app.js", "/styles.css", "/api/dashboard", "/api/config"):
                    with opener.open(address + path, timeout=3) as response:
                        self.assertEqual(response.status, 200, path)
                        self.assertGreater(len(response.read()), 0, path)
                request = Request(address + "/api/shutdown", data=b"{}", headers={
                    "Content-Type": "application/json", "X-LocalFlow-Token": status["token"],
                })
                with opener.open(request, timeout=3) as response:
                    self.assertTrue(json.load(response)["ok"])
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                with socket.socket() as check:
                    check.settimeout(1)
                    self.assertNotEqual(check.connect_ex(("127.0.0.1", port)), 0)
                self.assertTrue((root / "data" / "activity.sqlite3").is_file())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)

    @unittest.skipUnless(os.name == "nt", "Native Windows launcher required")
    def test_extracted_native_launcher_self_test(self):
        with tempfile.TemporaryDirectory(prefix="LocalTimerAI release native ") as folder:
            with ZipFile(ARCHIVE) as archive:
                self.assertEqual(set(archive.namelist()), RELEASE_FILES)
                archive.extractall(folder)
            result = subprocess.run([str(Path(folder) / "LocalFlow.exe"), "--self-test"],
                                    cwd=folder, capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
