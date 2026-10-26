import json
from pathlib import Path
import tempfile
import unittest

from integrations import AICollector, configure_wakatime, parse_editor
from app import Store

BASE = 1760000000


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = []
        self.state = {}
        self.config = {"ai": {"enabled": True, "codex_paths": [str(self.root)], "claude_paths": []}}
        self.collector = AICollector(lambda: self.config, self.events.append,
                                     self.state.get, self.state.__setitem__)

    def write(self, records, name="session.jsonl", append=False):
        with (self.root / name).open("a" if append else "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def test_editor_user_agent_excludes_cli_and_runtime(self):
        self.assertEqual(parse_editor("wakatime/v2.5.0 (windows-10.0.26100-amd64) go1.25.9 rider/2026.2 rider-wakatime/16.0.0"), "Rider")
        self.assertEqual(parse_editor("wakatime/v2.5.0 (linux-6.19-x86_64) go1.25.9 Claude/unknown pycharm/2026.1 pycharm-wakatime/16.0.0"), "Claude Code")
        self.assertEqual(parse_editor("vscode/1.101.0 vscode-wakatime/25.3.0"), "VS Code")
        self.assertEqual(parse_editor("wakatime/v2.5.0 (windows-unknown-unknown) go1.25.9 Unknown/0"), "WakaTime")

    def test_codex_records_skip_mirrored_cumulative_counts(self):
        secret = "THIS_PROMPT_MUST_NOT_BE_STORED"
        self.write([
            {"type": "session_meta", "payload": {"id": "s1", "cwd": "D:\\Priv\\Project", "base_instructions": secret}},
            {"type": "turn_context", "payload": {"model": "gpt-example", "cwd": "D:\\Priv\\Project"}},
            {"timestamp": "2026-10-26T09:00:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
            {"timestamp": "2026-10-26T09:00:03Z", "type": "token_usage_record", "payload": {"response_id": "r1", "usage": {"input_tokens": 100, "output_tokens": 7, "cached_input_tokens": 90}}},
            {"timestamp": "2026-10-26T09:00:03Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 7}}}},
            {"timestamp": "2026-10-26T09:00:06Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1", "last_agent_message": secret}},
        ])
        self.collector.scan_once()
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[0]["tokens_input"], 100)
        self.assertEqual(self.events[0]["model"], "gpt-example")
        self.assertEqual(self.events[1]["duration"], 6)
        self.assertEqual(self.events[1]["project"], "Project")
        self.assertNotIn(secret, json.dumps([self.events, self.state]))
        self.collector.scan_once()
        self.assertEqual(len(self.events), 2)

    def test_offsets_partial_line_restart_and_append(self):
        self.write([{"timestamp": BASE, "type": "token_usage_record", "payload": {"response_id": "r1", "usage": {"input_tokens": 8}}}])
        self.collector.scan_once()
        partial = json.dumps({"timestamp": BASE + 2, "type": "token_usage_record", "payload": {"response_id": "r2", "usage": {"input_tokens": 12}}})
        with (self.root / "session.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(partial[:20])
        self.collector.scan_once()
        self.assertEqual(len(self.events), 1)
        with (self.root / "session.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(partial[20:] + "\n")
        restarted = AICollector(lambda: self.config, self.events.append, self.state.get, self.state.__setitem__)
        restarted.scan_once()
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[-1]["tokens_input"], 12)

    def test_stop_during_import_replays_without_lost_or_duplicate_usage(self):
        self.write([{"timestamp": BASE + i, "type": "token_usage_record", "payload": {
            "response_id": f"shutdown-{i}", "usage": {"input_tokens": 10}}} for i in range(4)])
        store = Store(self.root / "test.sqlite3")
        self.addCleanup(store.close)
        def emit_then_stop(event):
            store.emit(event)
            collector._stop.set()
        collector = AICollector(lambda: self.config, emit_then_stop, store.state_get, store.state_set)
        collector.scan_once()
        self.assertEqual(len(store.events(BASE, BASE + 10)), 1)
        resumed = AICollector(lambda: self.config, store.emit, store.state_get, store.state_set)
        resumed.scan_once()
        rows = store.events(BASE, BASE + 10)
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(row["tokens_input"] for row in rows), 40)

    def test_legacy_cumulative_deltas_and_counter_reset(self):
        def record(value):
            return {"timestamp": BASE + value + 1, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": value}}}}
        self.write([record(10), record(10), record(15)])
        self.collector.scan_once()
        self.assertEqual([event["tokens_input"] for event in self.events], [10, 5])
        self.write([record(3), record(7)], append=True)
        self.collector.scan_once()
        self.assertEqual([event["tokens_input"] for event in self.events], [10, 5, 3, 4])

    def test_null_usage_does_not_reset_cumulative_counter(self):
        self.write([
            {"timestamp": BASE, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 12}}}},
            {"timestamp": BASE + 1, "type": "event_msg", "payload": {"type": "token_count", "info": None}},
            {"timestamp": BASE + 2, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 15}}}},
        ])
        self.collector.scan_once()
        self.assertEqual([event["tokens_input"] for event in self.events], [12, 3])

    def test_claude_stream_dedup_and_cache_normalization(self):
        self.config["ai"].update(codex_paths=[], claude_paths=[str(self.root)])
        def record(output, uid):
            return {"type": "assistant", "uuid": uid, "timestamp": BASE, "cwd": "D:/Repo", "message": {"id": "m1", "model": "claude-test", "content": "SECRET", "usage": {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5, "output_tokens": output}}}
        self.write([record(2, "u1"), record(2, "u2"), record(5, "u3")])
        self.collector.scan_once()
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[0]["tokens_input"], 35)
        self.assertEqual(self.events[0]["tokens_cached"], 20)
        self.assertEqual(self.events[1]["tokens_input"], 0)
        self.assertEqual(self.events[1]["tokens_output"], 3)
        self.assertNotIn("SECRET", json.dumps([self.events, self.state]))

    def test_claude_regressed_partial_does_not_reduce_high_water(self):
        self.config["ai"].update(codex_paths=[], claude_paths=[str(self.root)])
        def record(value):
            return {"type": "assistant", "timestamp": BASE, "message": {"id": "m1", "model": "claude-test", "usage": {"output_tokens": value}}}
        self.write([record(10), record(3), record(12)])
        self.collector.scan_once()
        self.assertEqual([event["tokens_output"] for event in self.events], [10, 2])

    def test_invalid_time_and_long_turn_skipped_without_blocking_later_events(self):
        self.write([
            {"timestamp": "not-a-date", "type": "token_usage_record", "payload": {"response_id": "bad-time", "usage": {"input_tokens": 50}}},
            {"timestamp": BASE, "type": "event_msg", "payload": {"type": "task_started", "turn_id": "too-long", "started_at": BASE - 9 * 86400}},
            {"timestamp": BASE, "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "too-long"}},
            {"timestamp": BASE, "type": "token_usage_record", "payload": {"response_id": "valid", "usage": {"input_tokens": 8}}},
        ])
        self.collector.scan_once()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["tokens_input"], 8)
        self.collector.scan_once()
        self.assertEqual(len(self.events), 1)

    def test_config_helper_preserves_primary_settings_and_backup(self):
        path = self.root / ".wakatime.cfg"
        original = "# keep this comment\n[settings]\napi_key = old-key\napi_url = https://api.wakatime.com/api/v1\n[api_urls]\n^D:/Work/ = https://example.com/api/v1|another-key\n"
        path.write_text(original, encoding="utf-8")
        result = configure_wakatime(path, "http://127.0.0.1:8765", "local-key")
        updated = path.read_text(encoding="utf-8")
        self.assertIn("api_key = old-key", updated)
        self.assertIn("# keep this comment", updated)
        self.assertIn(".* = http://127.0.0.1:8765/api/v1|local-key", updated)
        self.assertEqual(Path(result["backup"]).read_text(encoding="utf-8"), original)
        configure_wakatime(path, "http://127.0.0.1:8765", "local-key")
        self.assertEqual(path.read_text(encoding="utf-8").count(".* = http://127.0.0.1:8765"), 1)
        with self.assertRaises(ValueError):
            configure_wakatime(path, "http://malicious.example", "key")


if __name__ == "__main__":
    unittest.main()
