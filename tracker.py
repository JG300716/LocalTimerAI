"""Local Windows process tracking. No dependencies and no cloud requests.

Foreground mode is active-user time; running mode is process-presence time.
Concurrent running-mode processes are independent counters, not human work time.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import date, timedelta
import os
import re
import threading
import time
from typing import Callable


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class WindowsProbe:
    """Read-only Win32 sampling, with explicit pointer-sized API declarations."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("Desktop tracking requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        signatures = [
            (self.user, "GetForegroundWindow", [], wintypes.HWND),
            (self.user, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user, "GetLastInputInfo", [ctypes.POINTER(_LASTINPUTINFO)], wintypes.BOOL),
            (self.user, "GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
            (self.user, "GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user, "OpenInputDesktop", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.user, "CloseDesktop", [wintypes.HANDLE], wintypes.BOOL),
            (self.user, "GetUserObjectInformationW", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.kernel, "GetTickCount64", [], ctypes.c_ulonglong),
            (self.kernel, "CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel, "Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)], wintypes.BOOL),
            (self.kernel, "Process32NextW", [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)], wintypes.BOOL),
            (self.kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            (self.wts, "WTSQuerySessionInformationW", [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.wts, "WTSFreeMemory", [ctypes.c_void_p], None),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes = args
            function.restype = result

    def sample(self, capture_titles=False):
        session_data = ctypes.c_void_p()
        session_size = wintypes.DWORD()
        session_active = False
        if self.wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 8, ctypes.byref(session_data), ctypes.byref(session_size)):
            try:
                if session_data and session_size.value >= ctypes.sizeof(wintypes.DWORD):
                    session_active = ctypes.cast(session_data, ctypes.POINTER(wintypes.DWORD)).contents.value == 0
            finally:
                self.wts.WTSFreeMemory(session_data)
        # On a locked/secure desktop, OpenInputDesktop or name lookup fails,
        # or the input desktop has a different name. Fail closed in that case.
        unlocked = False
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)
        if desktop:
            try:
                name = ctypes.create_unicode_buffer(256)
                needed = wintypes.DWORD()
                if self.user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
                    unlocked = session_active and name.value.casefold() == "default"
            finally:
                self.user.CloseDesktop(desktop)
        last_input = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
        idle = float("inf")
        if self.user.GetLastInputInfo(ctypes.byref(last_input)):
            idle = ((self.kernel.GetTickCount64() & 0xFFFFFFFF) - last_input.dwTime) & 0xFFFFFFFF
            idle /= 1000.0
        foreground = self.user.GetForegroundWindow()
        foreground_pid = wintypes.DWORD()
        if foreground:
            self.user.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
        processes = set()
        foreground_exe = ""
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value or not snapshot:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            valid = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while valid:
                exe = entry.szExeFile.casefold()
                processes.add(exe)
                if foreground_pid.value and entry.th32ProcessID == foreground_pid.value:
                    foreground_exe = exe
                valid = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        result = {"unlocked": unlocked, "idle_seconds": idle,
                  "foreground_exe": foreground_exe, "processes": processes}
        if capture_titles and foreground:
            length = min(self.user.GetWindowTextLengthW(foreground), 2048)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                self.user.GetWindowTextW(foreground, title, length + 1)
                result["window_title"] = title.value
        return result


def select_active(config: dict, sample: dict) -> dict:
    """Pure selection. A process name is counted at most once in either mode."""
    if not config.get("tracking", {}).get("enabled", True) or not sample.get("unlocked"):
        return {}
    tracking = config.get("tracking", {})
    idle_limit = float(tracking.get("idle_seconds", 120))
    active = {}
    for application in config.get("applications", []):
        if application.get("enabled", True) is False:
            continue
        exe = str(application.get("exe", "")).strip().casefold()
        mode = application.get("mode", "foreground")
        if not exe or exe in active:
            continue
        if mode == "running":
            eligible = exe in sample.get("processes", set())
        elif mode == "foreground":
            eligible = sample.get("foreground_exe") == exe and sample.get("idle_seconds", float("inf")) < idle_limit
        else:
            continue
        if eligible:
            meta = {"mode": mode}
            if tracking.get("capture_titles", False) and mode == "foreground" and sample.get("window_title"):
                meta["window_title"] = sample["window_title"]
            active[exe] = {
                "source": "desktop", "project": str(application.get("project") or application.get("label") or exe),
                "entity": exe, "editor": str(application.get("label") or exe),
                "category": str(application.get("category") or "coding"),
                "language": "", "meta": meta,
            }
    return active


def interval_events(previous: dict | None, current: dict, poll_seconds: float) -> list[dict]:
    """Conservative sampled intervals. Never bridge suspend, clock jumps or changes."""
    if previous is None:
        return []
    elapsed = current["monotonic"] - previous["monotonic"]
    wall_elapsed = current["timestamp"] - previous["timestamp"]
    if elapsed <= 0 or elapsed > max(5.0, poll_seconds * 2.5) or abs(wall_elapsed - elapsed) > 2:
        return []
    events = []
    for key, activity in previous["active"].items():
        other = current["active"].get(key)
        # Metadata may contain a title that changes while typing; title equality
        # must not reset the process timer. Routing settings must stay identical.
        if other and all(activity.get(field) == other.get(field) for field in ("entity", "editor", "project", "category")) and activity["meta"]["mode"] == other["meta"]["mode"]:
            events.append({**activity, "timestamp": previous["timestamp"], "duration": elapsed})
    return events


class DesktopTracker:
    def __init__(self, config_getter: Callable, emit: Callable):
        self._config_getter = config_getter
        self._emit = emit
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._generation = 0
        self._state = {"supported": os.name == "nt", "running": False, "paused": False,
                       "foreground_exe": "", "active": [], "idle_seconds": 0,
                       "unlocked": False, "error": None, "last_sample": None}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="LocalFlow-desktop", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def pause(self, paused: bool):
        with self._lock:
            if paused:
                self._paused.set()
            else:
                self._paused.clear()
            self._generation += 1
            self._state["paused"] = bool(paused)
            self._state["active"] = []

    def status(self):
        with self._lock:
            return {**self._state, "active": list(self._state["active"])}

    def _run(self):
        previous = None
        try:
            probe = WindowsProbe()
        except Exception as error:
            with self._lock:
                self._state["error"] = str(error)
            return
        with self._lock:
            self._state["running"] = True
        while not self._stop.is_set():
            poll = 2.0
            try:
                config = self._config_getter()
                poll = max(0.25, min(60.0, float(config.get("tracking", {}).get("poll_seconds", 2))))
                with self._lock:
                    generation = self._generation
                sample = probe.sample(config.get("tracking", {}).get("capture_titles", False))
                active = {} if self._paused.is_set() else select_active(config, sample)
                current = {"timestamp": time.time(), "monotonic": time.monotonic(), "active": active,
                           "generation": generation}
                with self._lock:
                    # Stop may arrive while the Win32 sample is in flight.
                    # Discard that sample and commit under the same lock used
                    # by pause(), so no stale interval is saved after Stop.
                    if generation != self._generation:
                        previous = None
                        continue
                    if previous and previous["generation"] == generation:
                        for event in interval_events(previous, current, poll):
                            self._emit(event)
                    previous = current
                    self._state.update({"foreground_exe": sample["foreground_exe"],
                                        "active": [{"exe": key, "label": value["editor"], "mode": value["meta"]["mode"]} for key, value in active.items()],
                                        "idle_seconds": round(sample["idle_seconds"], 1) if sample["idle_seconds"] != float("inf") else None,
                                        "unlocked": sample["unlocked"], "error": None, "last_sample": current["timestamp"]})
            except Exception as error:
                previous = None
                with self._lock:
                    self._state["error"] = str(error)
                    self._state["active"] = []
            self._stop.wait(poll)
        with self._lock:
            self._state["running"] = False
            self._state["active"] = []


def parse_legacy(text: str) -> list[dict]:
    """Read NostaleTestingTimer weekly rows without inventing daily sessions.

    Duplicate week rows use the latest value, matching the cumulative file format.
    Explicit "Test - " rows and # / // / ; comments are not work measurements.
    Other invalid nonblank input is rejected instead of returning partial data.
    """
    pattern = re.compile(r"^\s*(\d{4})\s+Week:\s*(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\s+Duration:\s*(\d+)h:(\d{2})m:(\d{2})s\s*$")
    rows = {}
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("Test - ", "#", "//", ";")):
            continue
        match = pattern.match(line)
        if not match:
            raise ValueError(f"Nieprawidłowy format danych w wierszu {number}: {line[:160]!r}")
        year, sm, sd, em, ed, hours, minutes, seconds = map(int, match.groups())
        if minutes > 59 or seconds > 59:
            raise ValueError(f"Nieprawidłowy czas w wierszu {number}")
        try:
            start = date(year, sm, sd)
            end = date(year + (em < sm), em, ed)
        except ValueError as error:
            raise ValueError(f"Nieprawidłowa data w wierszu {number}") from error
        if end - start != timedelta(days=6):
            raise ValueError(f"Zakres w wierszu {number} nie obejmuje 7 dni")
        rows[start.isoformat()] = {"week_start": start.isoformat(), "week_end": end.isoformat(),
                                   "duration": hours * 3600 + minutes * 60 + seconds,
                                   "source": "NostaleTestingTimer", "precision": "week"}
    return sorted(rows.values(), key=lambda row: row["week_start"])
