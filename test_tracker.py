import unittest
from unittest.mock import patch

from tracker import DesktopTracker, interval_events, parse_legacy, select_active


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.config = {"tracking": {"idle_seconds": 120}, "applications": [
            {"exe": "Rider64.exe", "label": "Rider", "mode": "foreground"},
            {"exe": "TestVoid.exe", "label": "Test", "category": "testing", "mode": "running"},
        ]}
        self.sample = {"unlocked": True, "idle_seconds": 5, "foreground_exe": "rider64.exe",
                       "processes": {"rider64.exe", "testvoid.exe"}, "window_title": "secret"}

    def test_background_presence_is_separate_from_idle_foreground(self):
        active = select_active(self.config, {**self.sample, "idle_seconds": 121})
        self.assertEqual(list(active), ["testvoid.exe"])
        self.assertEqual(active["testvoid.exe"]["meta"]["mode"], "running")

    def test_lock_and_disabled_are_excluded(self):
        self.assertFalse(select_active(self.config, {**self.sample, "unlocked": False}))
        self.config["tracking"]["enabled"] = False
        self.assertFalse(select_active(self.config, self.sample))

    def test_titles_are_opt_in_and_names_case_insensitive(self):
        active = select_active(self.config, self.sample)
        self.assertIn("rider64.exe", active)
        self.assertNotIn("window_title", active["rider64.exe"]["meta"])
        self.config["tracking"]["capture_titles"] = True
        self.assertEqual(select_active(self.config, self.sample)["rider64.exe"]["meta"]["window_title"], "secret")

    def test_suspend_first_sample_and_clock_changes_do_not_accrue(self):
        active = select_active(self.config, self.sample)
        before = {"timestamp": 1000, "monotonic": 1, "active": active}
        self.assertEqual(interval_events(None, before, 2), [])
        self.assertEqual(interval_events(before, {"timestamp": 2000, "monotonic": 1001, "active": active}, 2), [])
        self.assertEqual(interval_events(before, {"timestamp": 900, "monotonic": 3, "active": active}, 2), [])
        self.assertEqual(len(interval_events(before, {"timestamp": 1002, "monotonic": 3, "active": active}, 2)), 2)

    def test_config_change_does_not_reassign_old_interval(self):
        previous = {"timestamp": 1000, "monotonic": 1, "active": select_active(self.config, self.sample)}
        self.config["applications"][0]["project"] = "Other project"
        current = {"timestamp": 1002, "monotonic": 3, "active": select_active(self.config, self.sample)}
        self.assertEqual([e["entity"] for e in interval_events(previous, current, 2)], ["testvoid.exe"])

    def test_stop_discards_an_inflight_sample_before_it_can_save_time(self):
        self.config["tracking"]["poll_seconds"] = 0.25
        events = []
        collector = DesktopTracker(lambda: self.config, events.append)
        sample = self.sample
        class Probe:
            def sample(self, *_):
                return sample
        calls = 0
        def select_then_stop(config, observed):
            nonlocal calls
            calls += 1
            active = select_active(config, observed)
            if calls == 2:
                collector.pause(True)
                collector._stop.set()
            return active
        with patch("tracker.WindowsProbe", Probe), patch("tracker.select_active", select_then_stop):
            collector._run()
        self.assertEqual(events, [])
        self.assertTrue(collector.status()["paused"])
        self.assertFalse(collector.status()["running"])

    def test_stop_then_start_does_not_bridge_the_paused_interval(self):
        self.config["tracking"]["poll_seconds"] = 0.25
        events = []
        collector = DesktopTracker(lambda: self.config, events.append)
        sample = self.sample
        class Probe:
            def sample(self, *_):
                return sample
        calls = 0
        def select_then_resume(config, observed):
            nonlocal calls
            calls += 1
            active = select_active(config, observed)
            if calls == 2:
                collector.pause(True)
                collector.pause(False)
            if calls == 4:
                collector._stop.set()
            return active
        with patch("tracker.WindowsProbe", Probe), patch("tracker.select_active", select_then_resume):
            collector._run()
        # Two tracked applications, each counted only for samples 3 -> 4.
        self.assertEqual(len(events), 2)
        self.assertFalse(collector.status()["paused"])

    def test_legacy_keeps_week_precision_and_latest_value(self):
        rows = parse_legacy("2025 Week: 12.29-01.04 Duration: 132h:04m:17s\n2025 Week: 12.29-01.04 Duration: 133h:04m:17s")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["week_end"], "2026-01-04")
        self.assertEqual(rows[0]["duration"], 133 * 3600 + 4 * 60 + 17)
        with self.assertRaises(ValueError):
            parse_legacy("2025 Week: 12.29-01.04 Duration: 133h:64m:17s")

    def test_legacy_skips_explicit_test_rows_and_comments_only(self):
        rows = parse_legacy("# Imported history\nTest - 2025 Week: 03.03-03.09 Duration: 99h:00m:00s\n"
                            "2025 Week: 03.03-03.09 Duration: 2h:03m:04s\n; note\n// note")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["duration"], 2 * 3600 + 3 * 60 + 4)
        with self.assertRaisesRegex(ValueError, "wierszu 2.*Unknown"):
            parse_legacy("# comment\nUnknown - 2025 Week: 03.03-03.09 Duration: 2h:03m:04s")


if __name__ == "__main__":
    unittest.main()
