import fcntl
import json
import os
import pty
import shutil
import subprocess
import tempfile
import termios
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


@unittest.skipUnless(shutil.which("jq"), "installer checks require jq")
class InstallEffortStyleTest(unittest.TestCase):
    def test_saved_styles_render_in_every_size_and_allow_environment_override(self):
        styles = {
            "original": ("(Low)", "(Medium)", "(High)"),
            "dots": ("●○○", "●●○", "●●●"),
            "bars": ("▰▱▱", "▰▰▱", "▰▰▰"),
            "minimal": ("L", "M", "H"),
            "TEXT": ("(Low)", "(Medium)", "(High)"),
        }
        with tempfile.TemporaryDirectory() as directory:
            for size in ("micro", "small", "medium", "large"):
                target = Path(directory) / size
                target.mkdir()
                (target / "settings.json").write_text(json.dumps({"custom": "preserved"}))
                env = {**os.environ, "AGY_CONFIG_DIR": str(target), "AGY_STATUSLINE_LIVE_QUOTA": "0"}
                env.pop("AGY_STATUSLINE_EFFORT_STYLE", None)
                for style, labels in styles.items():
                    with self.subTest(size=size, style=style):
                        subprocess.run(
                            ["bash", str(INSTALLER), size, "dracula", style],
                            stdin=subprocess.DEVNULL, env=env, capture_output=True,
                            text=True, check=True, timeout=5,
                        )
                        settings = json.loads((target / "settings.json").read_text())
                        self.assertEqual("preserved", settings["custom"])
                        self.assertEqual(f"bash {target / 'statusline.sh'}", settings["statusLine"]["command"])
                        for level, label in zip(("Low", "Medium", "High"), labels):
                            result = subprocess.run(
                                ["bash", str(target / "statusline.sh"), "--no-autoshrink"],
                                input=json.dumps({"model": f"Gemini 4 Argon ({level})"}),
                                env=env, capture_output=True, text=True, check=True, timeout=3,
                            )
                            self.assertIn(f"Gemini 4 Argon 🔥 {label}", result.stdout)
                        result = subprocess.run(
                            ["bash", str(target / "statusline.sh"), "--no-autoshrink"],
                            input=json.dumps({"model": "Gemini 4 Argon (Medium)"}),
                            env={**env, "AGY_STATUSLINE_EFFORT_STYLE": "text"},
                            capture_output=True, text=True, check=True, timeout=3,
                        )
                        self.assertIn("Gemini 4 Argon 🔥 (Medium)", result.stdout)

    def test_noninteractive_default_and_invalid_style(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config"
            env = {**os.environ, "AGY_CONFIG_DIR": str(target)}
            subprocess.run(
                ["bash", str(INSTALLER), "micro", "mocha"],
                stdin=subprocess.DEVNULL, env=env, capture_output=True, check=True, timeout=5,
            )
            wrapper = (target / "statusline.sh").read_text()
            self.assertIn("AGY_STATUSLINE_EFFORT_STYLE:-dots", wrapper)
            result = subprocess.run(
                ["bash", str(INSTALLER), "micro", "mocha", "invalid"],
                stdin=subprocess.DEVNULL, env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Unknown effort style", result.stderr)
            self.assertEqual(wrapper, (target / "statusline.sh").read_text())

    @unittest.skipUnless(shutil.which("curl"), "piped installer check requires curl")
    def test_piped_installer_prompts_on_terminal_and_saves_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config"
            env = {
                **os.environ, "AGY_CONFIG_DIR": str(target), "LC_ALL": "C",
                "AGY_STATUSLINE_RAW": INSTALLER.parent.as_uri(),
                "AGY_STATUSLINE_LIVE_QUOTA": "0",
            }
            env.pop("AGY_STATUSLINE_EFFORT_STYLE", None)
            master, slave = pty.openpty()

            def attach_terminal():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            try:
                with subprocess.Popen(
                    ["bash"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=env, pass_fds=(slave,),
                    preexec_fn=attach_terminal,
                ) as process:
                    os.write(master, b"2\n2\n3\n")
                    try:
                        stdout, stderr = process.communicate(INSTALLER.read_bytes(), timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                        raise
                    self.assertEqual(0, process.returncode, stderr.decode())
                self.assertIn("How should model effort be displayed?", stdout.decode())
                self.assertIn("No local checkout found", stdout.decode())
                wrapper = (target / "statusline.sh").read_text()
                self.assertIn("--size small --theme tokyo-night", wrapper)
                self.assertIn("AGY_STATUSLINE_EFFORT_STYLE:-bars", wrapper)
            finally:
                os.close(master)
                os.close(slave)


    @unittest.skipUnless(shutil.which("curl"), "remote fetch failure check requires curl")
    def test_remote_fetch_failure_reports_tip_and_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config"
            env = {
                **os.environ, "AGY_CONFIG_DIR": str(target), "LC_ALL": "C",
                "AGY_STATUSLINE_RAW": "http://127.0.0.1:9",
            }
            master, slave = pty.openpty()

            def attach_terminal():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            try:
                with subprocess.Popen(
                    ["bash"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=env, pass_fds=(slave,),
                    preexec_fn=attach_terminal,
                ) as process:
                    os.write(master, b"2\n2\n3\n")
                    try:
                        stdout, stderr = process.communicate(INSTALLER.read_bytes(), timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                        raise
                    self.assertNotEqual(0, process.returncode)
                output = stdout.decode() + stderr.decode()
                self.assertIn("No local checkout found — fetching from GitHub…", output)
                self.assertIn("Failed to fetch scripts/awesome-statusline-small.sh from GitHub.", output)
                self.assertIn("git clone https://github.com/Hadisd/agy-statusline.git", output)
            finally:
                os.close(master)
                os.close(slave)


if __name__ == "__main__":
    unittest.main()
