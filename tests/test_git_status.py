import io
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import statusline_engine


class GitSyncTest(unittest.TestCase):
    def test_upstream_counts_and_rendering_through_branch_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, local, peer = (root / name for name in ("remote.git", "local", "peer"))

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *map(str, args)],
                    capture_output=True, text=True, check=True, timeout=5,
                ).stdout

            def check_sync(ahead, behind):
                _, _, counts, _ = statusline_engine.get_git_status(str(local))
                self.assertEqual(ahead, counts.get("ahead", 0))
                self.assertEqual(behind, counts.get("behind", 0))
                for size in ("small", "medium", "large"):
                    with self.subTest(size=size, ahead=ahead, behind=behind):
                        output = io.StringIO()
                        with redirect_stdout(output):
                            statusline_engine.RENDERERS[size]({"cwd": str(local)})
                        plain = re.sub(r"\x1b\[[0-9;]*[mK]", "", output.getvalue())
                        for arrow, count in (("↑", ahead), ("↓", behind)):
                            if count:
                                self.assertIn(f"{arrow}{count}", plain)
                            else:
                                self.assertNotIn(arrow, plain)
                return counts

            git("init", "--bare", remote)
            git("init", "-b", "main", local)
            tracked = local / "tracked.txt"
            tracked.write_text("initial\n")
            git("-C", local, "add", "tracked.txt")
            git("-C", local, "commit", "-m", "initial")
            check_sync(0, 0)  # No upstream yet.
            git("-C", local, "remote", "add", "origin", remote)
            git("-C", local, "push", "-u", "origin", "main")
            git("clone", "--branch", "main", remote, peer)
            counts = check_sync(0, 0)
            self.assertEqual(0, counts["staged"])
            self.assertEqual(0, counts["unstaged"])

            git("-C", local, "commit", "--allow-empty", "-m", "local one")
            git("-C", local, "commit", "--allow-empty", "-m", "local two")
            check_sync(2, 0)
            git("-C", peer, "commit", "--allow-empty", "-m", "remote one")
            git("-C", peer, "push")
            git("-C", local, "fetch", "origin")
            check_sync(2, 1)

            tracked.write_text("changed\n")
            counts = check_sync(2, 1)
            self.assertEqual(0, counts["staged"])
            self.assertEqual(1, counts["unstaged"])
            git("-C", local, "add", "tracked.txt")
            counts = check_sync(2, 1)
            self.assertEqual(1, counts["staged"])
            self.assertEqual(0, counts["unstaged"])
            git("-C", local, "reset", "--hard", "main~2")
            check_sync(0, 1)
            git("-C", local, "switch", "-c", "no-upstream")
            check_sync(0, 0)
            git("-C", local, "switch", "--detach", "main")
            check_sync(0, 0)


if __name__ == "__main__":
    unittest.main()


class GitOptionalLocksTest(unittest.TestCase):
    def test_status_does_not_rewrite_index(self):
        """Rendering must not take .git/index.lock to refresh the index."""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*args):
                subprocess.run(
                    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", "-C", str(repo), *args],
                    capture_output=True, check=True, timeout=5,
                )

            git("init", "-b", "main")
            (repo / "f.txt").write_text("x\n")
            git("add", "f.txt")
            git("commit", "-m", "init")
            # Make the cached stat info stale so a plain `git status` would refresh (rewrite) the index.
            os.utime(repo / "f.txt", (1_000_000_000, 1_000_000_000))
            index = repo / ".git" / "index"
            before = index.read_bytes()
            statusline_engine.get_git_status(str(repo))
            self.assertEqual(before, index.read_bytes())
            self.assertFalse((repo / ".git" / "index.lock").exists())
