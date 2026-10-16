import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import statusline_engine


class ArgonQuotaDisplayTest(unittest.TestCase):
    def test_regular_gemini_does_not_use_argon_bucket_when_argon_comes_first(self):
        payload = {
            "model": "Gemini Flash",
            "quota": {
                "gemini-argon-wtu-weekly": {"remaining_fraction": 0.8308},
                "gemini-weekly": {"remaining_fraction": 0.9673},
            },
        }
        output = io.StringIO()
        with redirect_stdout(output):
            statusline_engine.render_micro(payload)
        plain = re.sub(r"\x1b\[[0-9;]*[mK]", "", output.getvalue())
        self.assertIn("7D 3%", plain)

    def test_argon_weekly_quota_wins_over_general_gemini_quota(self):
        payload = {
            "model": {"display_name": "Gemini 4 Argon"},
            "quota": {
                "gemini-weekly": {"remaining_fraction": 0.9673},
                "gemini-argon-wtu-weekly": {"remaining_fraction": 0.8308},
            },
        }
        output = io.StringIO()

        with redirect_stdout(output):
            statusline_engine.render_micro(payload)

        self.assertIn("17%", output.getvalue())

    def test_argon_does_not_show_gemini_five_hour_limit(self):
        payload = {
            "model": {"display_name": "Gemini 4 Argon (Medium)"},
            "plan_tier": "Google AI Pro",
            "quota": {
                "3p-5h": {"remaining_fraction": 1},
                "gemini-5h": {"remaining_fraction": 0.8857},
                "gemini-argon-wtu-weekly": {"remaining_fraction": 0.8308},
                "gemini-weekly": {"remaining_fraction": 0.9673},
            },
        }
        for size, render in statusline_engine.RENDERERS.items():
            with self.subTest(size=size):
                output = io.StringIO()
                with redirect_stdout(output):
                    render(payload)
                plain = re.sub(r"\x1b\[[0-9;]*[mK]", "", output.getvalue())
                five_hour = re.search(r"5H[^│\n]*", plain).group()
                self.assertIn("N/A", five_hour)
                self.assertIn("17%", plain)


class QuotaRefreshTest(unittest.TestCase):
    def test_malformed_cache_timestamps_do_not_blank_the_statusline(self):
        payload = {"model": "Gemini Flash", "quota": {"gemini-weekly": {"remaining_fraction": 0.99}}}
        states = (
            {"ts": None}, {"ts": "bad"}, {"ts": True},
            {"ts": float("nan")}, {"ts": float("inf")},
            {"ts": 0, "retry_after": None}, {"ts": 0, "retry_after": "bad"},
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "XDG_CACHE_HOME": directory, "AGY_STATUSLINE_LIVE_QUOTA": "1",
        }), patch.object(statusline_engine.subprocess, "Popen") as spawn:
            cache = statusline_engine._quota_cache_path()
            cache.parent.mkdir(parents=True)
            for state in states:
                with self.subTest(state=state):
                    cache.write_text(json.dumps(state))
                    cache.with_suffix(".lock").unlink(missing_ok=True)
                    spawn.reset_mock()
                    output = io.StringIO()
                    with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), patch.object(
                        sys, "argv", ["statusline_engine.py", "--size", "micro", "--no-autoshrink"]
                    ), redirect_stdout(output):
                        statusline_engine.main()
                    plain = re.sub(r"\x1b\[[0-9;]*[mK]", "", output.getvalue())
                    self.assertIn("7D 1%", plain)
                    spawn.assert_called_once()

    def test_cached_reset_countdown_ticks_and_expires_without_a_refresh(self):
        now = 1800000000.0
        reset = datetime.fromtimestamp(now + 300, timezone.utc).isoformat()
        with patch.object(statusline_engine.time, "time", return_value=now):
            quota = statusline_engine.parse_usage_quota(
                f"Gemini Models\tFive Hour Limit Remaining\t50%\t{reset}\n"
            )
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "XDG_CACHE_HOME": directory, "AGY_STATUSLINE_LIVE_QUOTA": "1",
        }):
            cache = statusline_engine._quota_cache_path()
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({"ts": now, "quota": quota, "retry_after": now + 2000}))
            for elapsed, remaining in ((60, 240), (600, 0)):
                with self.subTest(elapsed=elapsed), patch.object(
                    statusline_engine.time, "time", return_value=now + elapsed
                ):
                    payload = {"model": "Gemini Flash"}
                    statusline_engine.apply_live_quota(payload)
                    _, bucket = statusline_engine.find_quota_buckets(payload)
                    self.assertEqual(remaining, bucket["reset_in_seconds"])

    def test_cached_buckets_win_over_session_aliases_and_keep_missing_limits(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "XDG_CACHE_HOME": directory, "AGY_STATUSLINE_LIVE_QUOTA": "1",
        }):
            cache = statusline_engine._quota_cache_path()
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({"ts": now, "quota": {"gemini-weekly": {"remaining_fraction": 0.6}}}))
            for key in ("week_gemini", "gemini-weekly"):
                with self.subTest(key=key):
                    payload = {"model": "Gemini Flash", "quota": {
                        key: {"remaining_fraction": 0.99},
                        "five_hour_gemini": {"remaining_fraction": 0.8},
                        "3p-weekly": {"remaining_fraction": 0.1},
                    }}
                    statusline_engine.apply_live_quota(payload)
                    weekly, five_hour = statusline_engine.find_quota_buckets(payload)
                    self.assertEqual("40%", statusline_engine._quota_text(weekly))
                    self.assertEqual("20%", statusline_engine._quota_text(five_hour))
                    payload["model"] = "Claude Sonnet"
                    weekly, _ = statusline_engine.find_quota_buckets(payload)
                    self.assertEqual("90%", statusline_engine._quota_text(weekly))

    def test_cli_refresh_replaces_stale_argon_quota_in_rendered_output(self):
        engine = Path(statusline_engine.__file__).resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agy = root / "agy"
            usage = (
                "Gemini Models\tWeekly Limit Remaining\t97%\t2026-10-22T23:46:51Z\n"
                "Gemini Models\tFive Hour Limit Remaining\t89%\t2026-10-16T04:46:51Z\n"
                "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-10-23T04:10:35Z\n"
                "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t69%\t2026-10-23T03:24:21Z\n"
            )
            agy.write_text(
                f"#!{sys.executable}\nimport sys\n"
                "assert sys.argv[sys.argv.index('-p') + 1] == '/usage'\n"
                f"print({usage!r}, end='')\n"
            )
            agy.chmod(0o700)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "XDG_CACHE_HOME": str(root / "cache"),
                "AGY_STATUSLINE_LIVE_QUOTA": "1",
            }
            payload = {
                "model": "Gemini 4 Argon (Medium)",
                "plan_tier": "Google AI Pro",
                "quota": {
                    "gemini-argon-wtu-weekly": {"remaining_fraction": 0.8962845},
                },
            }
            def render():
                rendered = subprocess.run(
                    [sys.executable, str(engine), "--size", "micro", "--no-autoshrink"],
                    input=json.dumps(payload), env=env, capture_output=True,
                    text=True, check=True, timeout=3,
                )
                return re.sub(r"\x1b\[[0-9;]*[mK]", "", rendered.stdout)

            # The first render draws immediately and starts the real worker.
            self.assertIn("7D 10%", render())
            deadline = time.monotonic() + 3
            plain = render()
            while "7D 31%" not in plain and time.monotonic() < deadline:
                time.sleep(0.02)
                plain = render()
            self.assertIn("7D 31%", plain)
            self.assertIn("5H N/A", plain)

            # A failed refresh must retain the last valid quota, not show zero.
            agy.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
            failed = subprocess.run(
                [sys.executable, str(engine), "--refresh-quota"],
                env=env, capture_output=True, text=True, timeout=3,
            )
            self.assertEqual(1, failed.returncode)
            self.assertIn("7D 31%", render())


if __name__ == "__main__":
    unittest.main()
