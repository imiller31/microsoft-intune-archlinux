import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/skills/arch-intune-mde/scripts/onboard_mde.py"
SPEC = importlib.util.spec_from_file_location("onboard_mde", HELPER)
onboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(onboard)
TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"


def payload(tenant=TENANT, version="test"):
    body = {"orgId": tenant, "version": version}
    envelope = {
        "body": json.dumps(body),
        "sig": "synthetic-signature-not-for-enrollment",
        "cert": "synthetic-certificate-not-for-enrollment",
    }
    return json.dumps({"onboardingInfo": json.dumps(envelope)}).encode()


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config = self.directory / "config"
        self.config.mkdir()
        self.destination = self.config / "mdatp_onboard.json"
        self.addCleanup(patch.stopall)
        patch.object(onboard, "CONFIG_DIR", self.config).start()

    def source(self, name="onboarding-script-prod-manual.sh", data=None):
        data = payload() if data is None else data
        path = self.directory / name
        if name.endswith(".py"):
            script = f"open('/never-written', 'w').write({data.decode()!r})\n"
        else:
            script = f"BLOB_PAYLOAD='{data.decode()}'\n"

        path.write_text(script)
        return path

    def identity(self, licensed=False, tenant=None):
        return patch.object(onboard, "agent_identity", return_value=(licensed, tenant))

    def test_reads_both_script_formats_and_archives_without_executing_them(self):
        marker = self.directory / "executed"
        for name in sorted(onboard.SCRIPT_NAMES):
            with self.subTest(script=name):
                source = self.source(name)
                if name.endswith(".sh"):
                    extra = f"touch '{marker}'\n"
                else:
                    extra = f"__import__('pathlib').Path({str(marker)!r}).touch()\n"
                source.write_text(source.read_text() + extra)

                self.assertEqual(onboard.read_payload(source), (payload(), TENANT))
                archive = self.directory / "onboarding.zip"
                with zipfile.ZipFile(archive, "w") as stream:
                    stream.write(source, arcname=name)

                self.assertEqual(onboard.read_payload(archive), (payload(), TENANT))
                self.assertFalse(marker.exists())

    def test_refuses_ambiguous_and_oversized_archives(self):
        source = self.source()
        archive = self.directory / "onboarding.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.write(source, arcname="onboarding-script-prod.sh")
            stream.write(source, arcname="onboarding-script-prod-manual.sh")

        with self.assertRaises(onboard.EnrollmentError):
            onboard.read_payload(archive)

        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as stream:
            stream.writestr(source.name, "x" * (onboard.MAX_SCRIPT_BYTES + 1))

        with self.assertRaises(onboard.EnrollmentError):
            onboard.read_payload(archive)

    def test_refuses_nonliteral_python_without_running_it(self):
        marker = self.directory / "executed"
        source = self.directory / "onboard.py"
        source.write_text(f"output.write(__import__('pathlib').Path({str(marker)!r}).touch())")
        with self.assertRaises(ValueError):
            onboard.read_payload(source)

        self.assertFalse(marker.exists())

    def test_requires_signature_and_valid_tenant(self):
        unsigned = json.dumps({
            "onboardingInfo": json.dumps({"body": json.dumps({"orgId": TENANT})}),
        }).encode()
        with self.assertRaises(onboard.EnrollmentError):
            onboard.organization(unsigned)
        with self.assertRaises(ValueError):
            onboard.organization(payload(tenant="not-a-uuid"))

    def test_preview_has_no_agent_or_filesystem_side_effects(self):
        with patch.object(onboard, "agent_identity") as identity:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = onboard.main([str(self.source())])

        self.assertEqual(result, 0)
        self.assertFalse(self.destination.exists())
        identity.assert_not_called()
        self.assertNotIn(TENANT, output.getvalue())
        self.assertNotIn("synthetic-signature", output.getvalue())

    def test_apply_requires_explicit_root_and_replace_requires_apply(self):
        source = self.source()
        for arguments in ([str(source), "--apply"], [str(source), "--replace"]):
            with self.subTest(arguments=arguments):
                with patch.object(onboard.os, "geteuid", return_value=1000):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(onboard.main(arguments), 1)

        self.assertFalse(self.destination.exists())

    def test_installs_privately_and_identical_data_is_a_noop(self):
        with self.identity():
            onboard.apply_payload(payload(), TENANT)
            before = self.destination.stat()
            result = onboard.apply_payload(payload(), TENANT)

        self.assertEqual(self.destination.read_bytes(), payload())
        self.assertEqual(before.st_mode & 0o777, 0o600)
        self.assertEqual(before.st_uid, os.geteuid())
        self.assertEqual(self.destination.stat().st_ino, before.st_ino)
        self.assertIn("already installed", result)

    def test_same_tenant_replacement_requires_flag_and_retains_private_backup(self):
        old = payload(version="legacy")
        self.destination.write_bytes(old)
        with self.identity():
            with self.assertRaises(onboard.EnrollmentError):
                onboard.apply_payload(payload(), TENANT)
            self.assertEqual(self.destination.read_bytes(), old)
            onboard.apply_payload(payload(), TENANT, replace=True)

        backups = list(self.config.glob("mdatp_onboard.json.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.destination.read_bytes(), payload())
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_refuses_agent_or_file_tenant_conflict_even_with_replace(self):
        with self.identity(tenant=OTHER_TENANT):
            with self.assertRaises(onboard.EnrollmentError):
                onboard.apply_payload(payload(), TENANT, replace=True)

        self.destination.write_bytes(payload(OTHER_TENANT))
        with self.identity():
            with self.assertRaises(onboard.EnrollmentError):
                onboard.apply_payload(payload(), TENANT, replace=True)

        self.assertEqual(self.destination.read_bytes(), payload(OTHER_TENANT))

    def test_licensed_same_tenant_is_a_noop(self):
        with self.identity(licensed=True, tenant=TENANT):
            result = onboard.apply_payload(payload(), TENANT, replace=True)

        self.assertIn("already licensed", result)
        self.assertFalse(self.destination.exists())

    def test_preserves_offboarding_and_symlinked_files(self):
        offboard = self.config / "mdatp_offboard.json"
        offboard.symlink_to(self.directory / "absent")
        with self.identity():
            with self.assertRaises(onboard.EnrollmentError):
                onboard.apply_payload(payload(), TENANT)

        self.assertTrue(offboard.is_symlink())
        offboard.unlink()
        target = self.directory / "other-file"
        target.write_bytes(b"untouched")
        self.destination.symlink_to(target)
        with self.identity():
            with self.assertRaises(onboard.EnrollmentError):
                onboard.apply_payload(payload(), TENANT, replace=True)

        self.assertEqual(target.read_bytes(), b"untouched")

    def test_failed_atomic_replace_preserves_original_and_backup(self):
        old = payload(version="legacy")
        self.destination.write_bytes(old)
        with self.identity():
            with patch.object(onboard.os, "replace", side_effect=OSError("test interruption")):
                with self.assertRaises(OSError):
                    onboard.apply_payload(payload(), TENANT, replace=True)

        self.assertEqual(self.destination.read_bytes(), old)
        backup = next(self.config.glob("mdatp_onboard.json.backup-*"))
        self.assertEqual(backup.read_bytes(), old)
        self.assertEqual(list(self.config.glob(".mdatp-onboard-*")), [])

    def test_initial_publish_never_overwrites_a_raced_in_file(self):
        self.destination.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            onboard.publish_private(self.destination, payload())

        self.assertEqual(self.destination.read_bytes(), b"existing")
        self.assertEqual(list(self.config.glob(".mdatp-onboard-*")), [])

    def test_malformed_data_errors_do_not_disclose_payload(self):
        source = self.source(data=b"PRIVATE-ENROLLMENT-DATA")
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(onboard.main([str(source)]), 1)

        self.assertNotIn("PRIVATE-ENROLLMENT-DATA", error.getvalue())

    def test_agent_queries_use_clean_environment_and_accept_unonboarded_identity(self):
        executable = self.directory / "mdatp"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "assert not any(k in os.environ for k in ('LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH'))\n"
            "field = sys.argv[sys.argv.index('--field') + 1]\n"
            "print(json.dumps({'name': field, 'value': False if field == 'licensed' else None}))\n"
        )
        executable.chmod(0o755)
        environment = {
            "PATH": f"{self.directory}{os.pathsep}{os.environ['PATH']}",
            "LD_PRELOAD": "/not-a-real-library",
            "LD_AUDIT": "/not-a-real-audit",
            "LD_LIBRARY_PATH": "/not-a-real-directory",
        }
        with patch.dict(os.environ, environment):
            self.assertEqual(onboard.agent_identity(), (False, None))

    def test_health_failure_prevents_writes(self):
        failure = subprocess.CalledProcessError(1, ["mdatp"], stderr="private server output")
        source = self.source()
        with patch.object(onboard.os, "geteuid", return_value=0):
            with patch.object(onboard, "agent_identity", side_effect=failure):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    self.assertEqual(onboard.main([str(source), "--apply"]), 1)

        self.assertFalse(self.destination.exists())
        self.assertNotIn("private server output", error.getvalue())
