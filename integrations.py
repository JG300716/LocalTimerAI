"""Local AI metadata import and an opt-in WakaTime configuration helper.

Only allowlisted accounting metadata leaves the parser. Conversation text, code,
tool arguments and tool outputs are never included in emitted events or state.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

LOG = logging.getLogger(__name__)
MAX_LINE_BYTES = 16 * 1024 * 1024
READ_BUDGET = 64 * 1024 * 1024


def parse_editor(user_agent):
    """Extract the plugin product, excluding the CLI and its runtime prefix.

    CLI format: wakatime/VERSION (OS-CORE-PLATFORM) goVERSION PLUGIN_PRODUCTS.
    Call with heartbeat.user_agent first, then the HTTP header as a fallback.
    """
    value = str(user_agent or "")[:2000]
    value = re.sub(r"^wakatime/\S+\s+\([^)]*\)\s+\S+\s*", "", value, flags=re.I)
    aliases = {
        "rider": "Rider", "vscode": "VS Code", "vscode-insiders": "VS Code Insiders",
        "code": "VS Code", "visualstudio": "Visual Studio", "vs": "Visual Studio",
        "idea": "IntelliJ IDEA", "intellij": "IntelliJ IDEA", "intellijidea": "IntelliJ IDEA",
        "pycharm": "PyCharm", "clion": "CLion", "webstorm": "WebStorm",
        "goland": "GoLand", "rustrover": "RustRover", "phpstorm": "PhpStorm",
        "androidstudio": "Android Studio", "cursor": "Cursor", "zed": "Zed",
        "vim": "Vim", "neovim": "Neovim", "nvim": "Neovim", "emacs": "Emacs",
        "claude": "Claude Code", "claude-code": "Claude Code", "codex": "Codex",
    }
    for match in re.finditer(r"(?:^|\s)([A-Za-z][A-Za-z0-9_+.-]*)/([^\s]+)", value):
        name = match.group(1)
        lower = name.casefold()
        if lower in ("wakatime", "wakatime-cli", "unknown", "python-requests", "curl") or lower.endswith("-wakatime"):
            continue
        return aliases.get(lower, name)[:100]
    return "WakaTime"


def _timestamp(value, fallback=0.0):
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        return result if math.isfinite(result) and result >= 0 else fallback
    except (ValueError, TypeError, OverflowError):
        return fallback


def _count(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def _project(value):
    # pathlib on Linux would not understand Windows paths in imported logs.
    return re.split(r"[\\/]", str(value or "").rstrip("\\/"))[-1] or "Bez projektu"


def _usage(value, claude=False):
    value = value if isinstance(value, dict) else {}
    if claude:
        cached = _count(value.get("cache_read_input_tokens"))
        written = _count(value.get("cache_creation_input_tokens"))
        # Claude input_tokens excludes the two cache categories; normalize to
        # total input, with tokens_cached explicitly a subset of input.
        return {"tokens_input": _count(value.get("input_tokens")) + cached + written,
                "tokens_output": _count(value.get("output_tokens")),
                "tokens_cached": cached, "cache_write_tokens": written}
    return {"tokens_input": _count(value.get("input_tokens")),
            "tokens_output": _count(value.get("output_tokens")),
            "tokens_cached": _count(value.get("cached_input_tokens")),
            "cache_write_tokens": _count(value.get("cache_write_input_tokens"))}


def _valid_event(event):
    try:
        timestamp, duration = float(event["timestamp"]), float(event.get("duration", 0))
        return (math.isfinite(timestamp) and math.isfinite(duration)
                and 946684800 <= timestamp <= time.time() + 300
                and 0 <= duration <= 7 * 86400
                and timestamp + duration <= time.time() + 300)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class AICollector:
    """Tail Codex and Claude Code JSONL files, preserving byte offsets in SQLite.

    Historical files are imported once by default. Durable external IDs make
    replays after a crash idempotent when emit stores them with a UNIQUE index.
    """

    def __init__(self, config_getter, emit, state_get, state_set):
        self.config_getter = config_getter
        self.emit = emit
        self.state_get = state_get
        self.state_set = state_set
        self._stop = threading.Event()
        self._thread = None
        self._status = {"enabled": False, "running": False, "files": 0,
                        "events": 0, "last_scan": None, "last_error": None,
                        "sources": [], "runtime_kind": "agent_turn_wall"}
        self._lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="LocalFlow-AI", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def status(self):
        with self._lock:
            return dict(self._status)

    def _update(self, **values):
        with self._lock:
            self._status.update(values)

    def _run(self):
        self._update(running=True)
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                # Do not write raw JSON lines or exception details containing
                # conversation data into logs.
                self._update(last_error=type(exc).__name__)
                LOG.warning("AI collector scan failed (%s)", type(exc).__name__)
            try:
                delay = max(3.0, float(self.config_getter().get("ai", {}).get("poll_seconds", 15)))
            except (TypeError, ValueError):
                delay = 15
            self._stop.wait(delay)
        self._update(running=False)

    def scan_once(self):
        config = self.config_getter().get("ai", {})
        self._update(enabled=bool(config.get("enabled", True)))
        if not config.get("enabled", True):
            return
        files, roots, errors = {}, [], 0
        for kind, setting, default in (
                ("codex", "codex_paths", ["~/.codex/sessions"]),
                ("claude", "claude_paths", ["~/.claude/projects"])):
            paths = config.get(setting, default)
            if not isinstance(paths, list):
                continue
            for raw in paths:
                root = Path(os.path.expandvars(os.path.expanduser(str(raw))))
                if not root.exists():
                    continue
                roots.append(kind)
                candidates = [root] if root.is_file() else root.rglob("*.jsonl")
                try:
                    for path in candidates:
                        if self._stop.is_set():
                            break
                        # A file configured under overlapping roots is tailed once.
                        files[str(path.resolve())] = (kind, path)
                except OSError:
                    errors += 1
        for kind, path in files.values():
            if self._stop.is_set():
                break
            try:
                self._read_file(kind, path)
            except (OSError, ValueError, TypeError):
                errors += 1
        self._update(files=len(files), sources=sorted(set(roots)), last_scan=time.time(),
                     last_error=f"Nieodczytane pliki: {errors}" if errors else None)

    def _read_file(self, kind, path):
        path_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:32]
        key = "ai-file:" + path_id
        saved = self.state_get(key, {}) or {}
        if not isinstance(saved, dict):
            saved = {}
        stat = path.stat()
        identity = f"{stat.st_dev}:{stat.st_ino}"
        with path.open("rb") as prefix_stream:
            prefix_bytes = prefix_stream.read(512)
        prefix = hashlib.sha256(prefix_bytes).hexdigest()
        old_prefix = hashlib.sha256(prefix_bytes[:_count(saved.get("prefix_length", len(prefix_bytes)))]).hexdigest()
        offset = _count(saved.get("offset"))
        if (offset > stat.st_size or saved.get("identity", identity) != identity
                or saved.get("prefix", old_prefix) != old_prefix):
            saved, offset = {}, 0
        if offset == stat.st_size and saved:
            return
        context = saved.get("context", {})
        if not isinstance(context, dict):
            context = {}
        records = []
        read = 0
        with path.open("rb") as stream:
            stream.seek(offset)
            while read < READ_BUDGET:
                if self._stop.is_set():
                    return
                start = stream.tell()
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # Incomplete append is retried. Very large lines are skipped
                    # until their newline, without holding their content in RAM.
                    if len(line) <= MAX_LINE_BYTES:
                        stream.seek(start)
                        break
                    while line and not line.endswith(b"\n"):
                        if self._stop.is_set():
                            return
                        line = stream.readline(MAX_LINE_BYTES + 1)
                    offset = stream.tell()
                    continue
                offset = stream.tell()
                read += len(line)
                try:
                    obj = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(obj, dict):
                    records.append((start, obj))
            # New Codex logs have per-response records followed by token_count
            # mirrors. Prefer the former, including when both are in one batch.
            if any(obj.get("type") == "token_usage_record" for _, obj in records):
                context["has_usage_records"] = True
            for start, obj in records:
                if self._stop.is_set():
                    # Leave the durable cursor at its previous position. Any
                    # events already committed are deduplicated on replay.
                    return
                parser = self._codex if kind == "codex" else self._claude
                for event in parser(obj, context, path_id, start):
                    if not _valid_event(event):
                        continue
                    self.emit(event)
                    with self._lock:
                        self._status["events"] += 1
        self.state_set(key, {"identity": identity, "prefix": prefix, "prefix_length": len(prefix_bytes),
                             "offset": offset, "context": context})

    @staticmethod
    def _event(context, timestamp, source, external_id):
        return {"source": "ai", "timestamp": timestamp, "duration": 0.0,
                "project": context.get("project", "Bez projektu"), "entity": source,
                "editor": source, "category": "ai", "language": "",
                "model": context.get("model", "nieznany"),
                "tokens_input": 0, "tokens_output": 0, "tokens_cached": 0,
                "cost_usd": None, "external_id": external_id,
                "meta": {"runtime_kind": "usage_only"}}

    @classmethod
    def _codex(cls, obj, context, path_id, position):
        output = []
        kind, payload = obj.get("type"), obj.get("payload", {})
        if not isinstance(payload, dict):
            return output
        timestamp = _timestamp(obj.get("timestamp"))
        if kind == "session_meta":
            context["project"] = _project(payload.get("cwd"))
            context["session"] = str(payload.get("id") or payload.get("session_id") or path_id)
        elif kind == "turn_context":
            context["project"] = _project(payload.get("cwd"))
            if payload.get("model"):
                context["model"] = str(payload["model"])
        elif kind == "token_usage_record":
            usage = _usage(payload.get("usage"))
            response = str(payload.get("response_id") or f"{path_id}:{position}")
            event = cls._event(context, timestamp, "Codex", "codex-usage:" + response)
            event.update({k: usage[k] for k in ("tokens_input", "tokens_output", "tokens_cached")})
            event["meta"]["cache_write_tokens"] = usage["cache_write_tokens"]
            if sum(usage.values()) > 0 and timestamp:
                output.append(event)
        elif kind == "event_msg":
            event_kind = payload.get("type")
            if event_kind == "token_count" and not context.get("has_usage_records"):
                info = payload.get("info") or {}
                if not isinstance(info, dict) or not isinstance(info.get("total_token_usage"), dict):
                    return output
                total = _usage(info.get("total_token_usage"))
                previous = context.get("last_usage", {})
                reset = any(total[key] < _count(previous.get(key)) for key in ("tokens_input", "tokens_output"))
                if reset:
                    context["counter_epoch"] = _count(context.get("counter_epoch")) + 1
                delta = {key: max(0, value - (0 if reset else _count(previous.get(key)))) for key, value in total.items()}
                context["last_usage"] = total
                if sum(delta.values()) and timestamp:
                    usage_id = hashlib.sha256(json.dumps(total, sort_keys=True).encode()).hexdigest()[:24]
                    event = cls._event(context, timestamp, "Codex", f"codex-legacy:{context.get('session', path_id)}:{context.get('counter_epoch', 0)}:{usage_id}")
                    event.update({key: delta[key] for key in ("tokens_input", "tokens_output", "tokens_cached")})
                    event["meta"]["cache_write_tokens"] = delta["cache_write_tokens"]
                    event["meta"]["accounting"] = "cumulative_delta"
                    output.append(event)
            elif event_kind in ("task_started", "turn_started"):
                turn = str(payload.get("turn_id") or context.get("session", path_id))
                active = context.setdefault("active_turns", {})
                active[turn] = {"start": _timestamp(payload.get("started_at"), timestamp),
                                "model": context.get("model", "nieznany"),
                                "project": context.get("project", "Bez projektu")}
            elif event_kind in ("task_complete", "task_completed", "turn_complete", "turn_completed", "turn_aborted"):
                active = context.setdefault("active_turns", {})
                turn = str(payload.get("turn_id") or context.get("session", path_id))
                record = active.pop(turn, None)
                # Some older abort records omit turn_id; match only when unique.
                if not record and not payload.get("turn_id") and len(active) == 1:
                    turn, record = active.popitem()
                completed_at = _timestamp(payload.get("completed_at"), timestamp)
                if not record and payload.get("started_at"):
                    record = {"start": _timestamp(payload["started_at"]),
                              "model": context.get("model", "nieznany"),
                              "project": context.get("project", "Bez projektu")}
                if record and completed_at and completed_at > record.get("start", completed_at):
                    event = cls._event(context, record["start"], "Codex", f"codex-turn:{context.get('session', path_id)}:{turn}")
                    event["duration"] = completed_at - record["start"]
                    event["model"] = context.get("model", record["model"])
                    event["project"] = record["project"]
                    event["meta"] = {"runtime_kind": "agent_turn_wall", "completed": event_kind != "turn_aborted"}
                    output.append(event)
        return output

    @classmethod
    def _claude(cls, obj, context, path_id, position):
        output = []
        if obj.get("cwd"):
            context["project"] = _project(obj["cwd"])
        timestamp = _timestamp(obj.get("timestamp"))
        if obj.get("type") == "assistant" and isinstance(obj.get("message"), dict):
            message = obj["message"]
            if message.get("model"):
                context["model"] = str(message["model"])
            raw_usage = message.get("usage")
            if not isinstance(raw_usage, dict) or not timestamp:
                return output
            usage = _usage(raw_usage, claude=True)
            message_id = str(message.get("id") or obj.get("requestId") or obj.get("uuid") or f"{path_id}:{position}")
            # Claude streams can repeat one response id. A small high-water map
            # emits only increments without unbounded persisted state growth.
            seen = context.setdefault("response_usage", {})
            previous = seen.get(message_id, {})
            delta = {key: max(0, value - _count(previous.get(key))) for key, value in usage.items()}
            high_water = {key: max(value, _count(previous.get(key))) for key, value in usage.items()}
            seen[message_id] = high_water
            while len(seen) > 256:
                del seen[next(iter(seen))]
            if sum(delta.values()):
                usage_id = hashlib.sha256(json.dumps(high_water, sort_keys=True).encode()).hexdigest()[:24]
                event = cls._event(context, timestamp, "Claude Code", f"claude-usage:{message_id}:{usage_id}")
                event.update({key: delta[key] for key in ("tokens_input", "tokens_output", "tokens_cached")})
                event["meta"]["cache_write_tokens"] = delta["cache_write_tokens"]
                output.append(event)
        elif obj.get("type") == "system" and obj.get("subtype") == "turn_duration":
            milliseconds = _count(obj.get("durationMs"))
            if milliseconds and timestamp:
                duration = milliseconds / 1000
                event = cls._event(context, max(0, timestamp - duration), "Claude Code",
                                   f"claude-turn:{obj.get('uuid') or f'{path_id}:{position}'}")
                event["duration"] = duration
                event["meta"] = {"runtime_kind": "agent_turn_wall", "completed": True}
                output.append(event)
        return output


def configure_wakatime(config_path, local_url, api_key, mode="mirror"):
    """Opt-in change, with a byte-for-byte backup. Never called by the collector.

    mirror adds an api_urls route while preserving the primary WakaTime endpoint;
    local replaces settings.api_url and api_key. The base URL must be loopback.
    """
    parsed = urlparse(local_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("WakaTime musi wskazywać lokalny adres HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Nieprawidłowy bazowy adres WakaTime")
    if mode not in ("mirror", "local"):
        raise ValueError("Tryb musi mieć wartość mirror lub local")
    if not api_key or any(char in str(api_key) for char in "\r\n|"):
        raise ValueError("Nieprawidłowy klucz API")
    base = local_url.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    path = Path(config_path).expanduser()
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    parser = configparser.RawConfigParser(strict=False)
    parser.read_string(original)
    backup = None
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(path.name + ".localflow-backup-" + stamp)
        shutil.copy2(path, backup)
    # Line editing preserves comments, ordering and pre-existing duplicate regex
    # routes, which ConfigParser would otherwise collapse on write.
    lines = original.splitlines()
    section = "api_urls" if mode == "mirror" else "settings"
    start = next((i for i, line in enumerate(lines) if line.strip().lower() == f"[{section}]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    if mode == "mirror":
        route = f".* = {base}|{api_key}"
        # Only replace our matching destination, preserving any other mirrors.
        replaced = False
        for i in range(start + 1, end):
            if re.match(r"\s*\.\*\s*=", lines[i]) and base in lines[i]:
                lines[i] = route
                replaced = True
        if not replaced:
            lines.insert(end, route)
    else:
        updates = {"api_url": base, "api_key": str(api_key)}
        for i in range(start + 1, end):
            match = re.match(r"\s*(api_url|api_key)\s*=", lines[i], re.I)
            if match:
                name = match.group(1).lower()
                lines[i] = f"{name} = {updates.pop(name, base if name == 'api_url' else str(api_key))}"
        for name, value in updates.items():
            lines.insert(end, f"{name} = {value}")
            end += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".localflow.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return {"path": str(path), "backup": str(backup) if backup else None, "mode": mode,
            "api_url": base, "restart_ide": True}
