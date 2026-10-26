"""Native tray smoke test against an isolated fake loopback service."""
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest

LAUNCHER = Path(os.environ.get("LOCALFLOW_TEST_EXE", str(Path(__file__).with_name("LocalFlow.exe"))))
ROOT = Path(__file__).resolve().parent
COMPILER = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


@unittest.skipUnless(os.name == "nt" and COMPILER.exists(), "Windows .NET Framework compiler required")
class NativeNetworkingTests(unittest.TestCase):
    """Call the launcher's real C# networking methods against isolated sockets/API."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="LocalFlow native regression ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.harness = Path(cls.temporary.name) / "LauncherRegressionHarness.exe"
        result = subprocess.run([
            str(COMPILER), "/nologo", "/target:exe", "/main:LauncherRegressionHarness",
            "/platform:anycpu", "/optimize+", "/codepage:65001",
            "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
            "/reference:System.Web.Extensions.dll", "/out:" + str(cls.harness),
            str(ROOT / "Launcher.cs"), str(ROOT / "LauncherRegressionHarness.cs"),
        ], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def probe(self, port):
        result = subprocess.run([str(self.harness), "probe", str(port)],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_bound_but_closed_port_is_not_a_live_service(self):
        # Windows may take >500ms to refuse connections. Merely timing out
        # a connect after500ms used to report this closed port as still alive.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
            for _ in range(3):
                result = self.probe(port)
                self.assertFalse(result["listening"], result)

    def test_listener_is_detected_and_disappears_after_close(self):
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            self.assertTrue(self.probe(port)["listening"])
        finally:
            listener.close()
        self.assertFalse(self.probe(port)["listening"])

    def test_production_shutdown_is_idempotent_when_service_is_not_running(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            result = subprocess.run([str(self.harness), "stop-idempotent", str(reserved.getsockname()[1])],
                                    capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("inactive-service shutdown twice OK", result.stdout)

    def test_native_stop_start_shutdown_with_real_isolated_backend(self):
        self.run_backend_harness("cycle", "stop/start/shutdown/closed-port OK")

    def test_context_exit_closes_message_loop_and_real_backend(self):
        self.run_backend_harness("context-exit", "message loop exited and service closed OK")

    def test_context_exit_queued_during_boot_closes_message_loop_and_real_backend(self):
        self.run_backend_harness("context-exit-during-boot", "message loop exited and service closed OK")

    def run_backend_harness(self, mode, expected):
        from app import Handler, Server, Service

        with tempfile.TemporaryDirectory(prefix="LocalFlow lifecycle API ") as folder:
            root = Path(folder)
            server = Server(("127.0.0.1", 0), Handler)
            config = root / "config.json"
            config.write_text(json.dumps({
                "server": {"port": server.server_port}, "timezone": "Europe/Warsaw",
                "tracking": {"enabled": True, "poll_seconds": 2, "idle_seconds": 120},
                "applications": [], "ai": {"enabled": False, "codex_paths": [], "claude_paths": []},
            }), encoding="utf-8")
            service = Service(config, root / "data", no_tracking=True)
            server.service = service
            service.http = server

            def serve():
                try:
                    server.serve_forever(poll_interval=0.05)
                finally:
                    server.server_close()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                result = subprocess.run([str(self.harness), mode, str(server.server_port), str(root)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive(), "Backend did not exit after native shutdown request")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                service.stop_collectors()
                service.store.close()


def close_test_tray(process_id):
    class ThreadEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("thread", wintypes.DWORD), ("process", wintypes.DWORD),
                    ("base", wintypes.LONG), ("delta", wintypes.LONG), ("flags", wintypes.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostThreadMessageW.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        valid = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while valid:
            if entry.process == process_id:
                # Only this test's tray process receives WM_QUIT; its UI thread
                # disposes its NotifyIcon through the normal message-loop exit.
                user.PostThreadMessageW(entry.thread, 0x0012, 0, 0)
            valid = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)


@unittest.skipUnless(os.name == "nt" and LAUNCHER.exists(), "Built Windows launcher required")
class LauncherTests(unittest.TestCase):
    def test_tray_stays_alive_checks_service_and_reuses_folder_mutex(self):
        ready = threading.Event()
        start_marker = None

        class FakeService(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if start_marker is None or not start_marker.exists():
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps({"name": "LocalFlow", "token": "isolated-test-token",
                                   "tracking": {"paused": False, "running": True, "enabled": True,
                                                "active": [], "error": None}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                ready.set()

        service = ThreadingHTTPServer(("127.0.0.1", 0), FakeService)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="LocalFlow native tray test ") as folder:
                root = Path(folder)
                executable = root / "LocalFlow.exe"
                shutil.copyfile(LAUNCHER, executable)
                start_marker = root / "started.flag"
                (root / "Start.cmd").write_text('@echo off\n> "%~dp0started.flag" echo ready\nexit /b 0\n')
                (root / "config.json").write_text(json.dumps({"server": {"port": service.server_port}}))
                tray = subprocess.Popen([str(executable), "--no-browser", "--background"], cwd=folder)
                try:
                    self.assertTrue(ready.wait(7), "Tray did not query isolated service")
                    self.assertTrue(start_marker.exists(), "Tray did not start its hidden Start.cmd")
                    self.assertIsNone(tray.poll(), "Tray exited before its menu could be used")
                    duplicate = subprocess.run([str(executable), "--no-browser", "--background"], cwd=folder, timeout=5)
                    self.assertEqual(duplicate.returncode, 0)
                    self.assertIsNone(tray.poll(), "Duplicate launch terminated the existing tray")
                    close_test_tray(tray.pid)
                    self.assertEqual(tray.wait(timeout=5), 0)
                finally:
                    if tray.poll() is None:
                        tray.terminate()
                        tray.wait(timeout=3)
        finally:
            service.shutdown()
            service.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
