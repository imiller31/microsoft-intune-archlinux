import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SETUP = Path(__file__).resolve().parents[1] / "setup-intune-cachyos.sh"


class SetupEntrypointTests(unittest.TestCase):
    def run_setup(self, *arguments):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        marker = directory / "privileged-command"
        sudo = directory / "sudo"
        sudo.write_text(f"#!/bin/bash\ntouch '{marker}'\nexit 99\n")
        sudo.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{directory}{os.pathsep}{environment['PATH']}"
        result = subprocess.run(
            ["bash", str(SETUP), *arguments], env=environment,
            capture_output=True, text=True, timeout=10,
        )
        self.assertFalse(marker.exists(), "Entrypoint validation must not reach privileged commands.")
        return result

    def test_help_does_not_run_setup(self):
        result = self.run_setup("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--skip-vpn", result.stdout)
        self.assertIn("--enable-pin-cache", result.stdout)

    def test_unknown_option_fails_before_setup(self):
        result = self.run_setup("--unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown option", result.stderr)

    @unittest.skipUnless(os.geteuid() == 0, "Root rejection is exercised in the disposable CI container.")
    def test_root_cannot_run_user_setup(self):
        result = self.run_setup("--skip-vpn")
        self.assertEqual(result.returncode, 1)
        self.assertIn("desktop user, not root", result.stderr)
