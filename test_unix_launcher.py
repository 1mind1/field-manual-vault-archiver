"""Offline launch tests for Bash entrypoints on Linux/macOS."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
CAN_USE_BASH = sys.platform != "win32" and shutil.which("bash") is not None


@unittest.skipUnless(CAN_USE_BASH, "requires Bash on Linux/macOS")
class UnixLauncherTests(unittest.TestCase):
    def launch(self, name: str, *, stdin: str = "", args: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROOT / name), *args],
            input=stdin,
            text=True,
            capture_output=True,
            timeout=20,
            cwd="/",  # Verify the launcher resolves paths relative to itself.
        )

    def test_both_wrappers_expose_shared_cli(self) -> None:
        for launcher in ("START_LINUX.sh", "START_MACOS.command"):
            with self.subTest(launcher=launcher):
                result = self.launch(launcher, args=("--version",))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("5.0.2-rc3", result.stdout)

    def test_offline_menu_writes_to_directory_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Field Manual Vault ") as temp:
            vault = Path(temp) / "Library With Spaces"
            result = self.launch("START_LINUX.sh", stdin=f"{vault}\n6\n")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((vault / "Field_Manual_Catalog.html").is_file())
            self.assertTrue((vault / "trex_cache.sqlite3").is_file())
            self.assertIn("Completed.", result.stdout)

    def test_full_download_requires_explicit_yes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trex-disallow-") as temp:
            vault = Path(temp) / "test vault"
            result = self.launch("START_LINUX.sh", stdin=f"{vault}\n5\n2\nNO\n")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Cancelled. No download started.", result.stdout)
            self.assertFalse(vault.exists())

    def test_bad_menu_option_fails_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trex-invalid-") as temp:
            vault = Path(temp) / "test vault"
            result = self.launch("START_LINUX.sh", stdin=f"{vault}\n9\n")
            self.assertEqual(result.returncode, 2)
            self.assertIn("Invalid choice", result.stderr)
            self.assertFalse(vault.exists())


if __name__ == "__main__":
    unittest.main()
