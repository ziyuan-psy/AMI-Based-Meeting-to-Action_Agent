"""Launcher checks: no package downloads, browser launches, or real processes."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import preview_ui


class PreviewLauncherTests(unittest.TestCase):
    def fake_environment(self, root):
        environment = root / ".venv-ui"
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        python.parent.mkdir(parents=True)
        python.touch()
        (root / "ui").mkdir()
        (root / "ui/requirements.txt").write_text("jsonschema>=4.23,<5\n", encoding="utf-8")
        return environment, python

    def test_repeat_launch_skips_install_for_unchanged_requirements(self):
        with tempfile.TemporaryDirectory(prefix="AMI preview ") as folder:
            root = Path(folder)
            environment, python = self.fake_environment(root)
            fingerprint = hashlib.sha256((root / "ui/requirements.txt").read_bytes()).hexdigest()
            (environment / ".preview-requirements.sha256").write_text(fingerprint)
            with patch("preview_ui.subprocess.run") as run:
                self.assertEqual(preview_ui.ensure_environment(root), python)
                run.assert_not_called()

    def test_changed_requirements_trigger_install_with_paths_as_arguments(self):
        with tempfile.TemporaryDirectory(prefix="AMI preview ") as folder:
            root = Path(folder)
            environment, python = self.fake_environment(root)
            (environment / ".preview-requirements.sha256").write_text("old")
            with patch("preview_ui.subprocess.run") as run:
                preview_ui.ensure_environment(root)
                args = run.call_args.args[0]
                self.assertEqual(args[0], str(python))
                self.assertEqual(args[-1], str(root / "ui/requirements.txt"))
                self.assertEqual(run.call_args.kwargs["cwd"], root)
            self.assertNotEqual((environment / ".preview-requirements.sha256").read_text().strip(), "old")

    def test_failed_install_is_not_marked_as_complete(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            environment, _ = self.fake_environment(root)
            with patch("preview_ui.subprocess.run", side_effect=subprocess.CalledProcessError(1, "pip")):
                with self.assertRaises(subprocess.CalledProcessError):
                    preview_ui.ensure_environment(root)
            self.assertFalse((environment / ".preview-requirements.sha256").exists())

    def test_existing_ui_is_reused_without_setup_or_browser_when_requested(self):
        with patch("preview_ui.is_ui_running", return_value=True), patch("preview_ui.ensure_environment") as setup, patch("preview_ui.webbrowser.open") as browser:
            self.assertEqual(preview_ui.main(["--no-browser"]), 0)
            setup.assert_not_called()
            browser.assert_not_called()

    def test_unrelated_service_on_port_is_not_reused_or_stopped(self):
        with patch("preview_ui.is_ui_running", return_value=False), patch("preview_ui.port_in_use", return_value=True), patch("preview_ui.ensure_environment") as setup:
            self.assertEqual(preview_ui.main(["--no-browser"]), 1)
            setup.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows shortcut")
    def test_windows_shortcut_finds_python_without_a_local_virtual_environment(self):
        with tempfile.TemporaryDirectory(prefix="AMI shortcut ") as folder:
            root = Path(folder)
            launcher = root / "preview-ui.cmd"
            launcher.write_bytes((preview_ui.ROOT / "preview-ui.cmd").read_bytes())
            (root / "preview_ui.py").write_text("print('Preview launcher reached Python')\n", encoding="utf-8")
            environment = dict(os.environ)
            environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
            result = subprocess.run([environment.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher), "--no-browser"],
                                    cwd=root, env=environment, input="", text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Preview launcher reached Python", result.stdout)


if __name__ == "__main__":
    unittest.main()
