import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vm_verify", ROOT / "tests/vm/verify.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def baseline():
    return {
        "boot_id": "first-boot",
        "preserved": {
            "machine_id": "original-identity",
            "os_release": "real-arch-metadata",
            "opensc": "existing-smartcard-policy",
            "dropin": "unrelated-user-service-setting",
            "nss": "existing-synthetic-certificate",
        },
    }


def ready():
    observed = baseline()
    observed.update({
        "packages": {name: "installed" for name in ("intune-portal", "microsoft-identity-broker", "mdatp-bin")},
        "services": {name: True for name in ("display", "desktop", "intune_socket", "intune_timer", "smartcard", "mde")},
        "timer_enabled": True,
        "portal_visible": True,
        "broker_backend": "x11",
        "checkin_result": "success",
        "sc_modules": 1,
        "mde": {"licensed": False, "engine_version": "test-engine", "uuid_errors": 0},
        "onboarding_present": False,
    })
    return observed


class VMVerifierTests(unittest.TestCase):
    def test_changed_machine_identity_is_rejected(self):
        before = baseline()
        after = baseline()
        after["preserved"]["machine_id"] = "replaced-identity"

        failures = verify.check(before, after, phase="installed")

        self.assertIn("Preserved machine_id changed or disappeared.", failures)

    def test_incomplete_installation_cannot_pass(self):
        broken_cases = (
            ("packages", "mdatp-bin", None),
            ("services", "desktop", False),
            ("services", "mde", False),
            ("mde", "licensed", None),
            ("mde", "engine_version", None),
            ("mde", "uuid_errors", 1),
            (None, "portal_visible", False),
            (None, "broker_backend", None),
            (None, "timer_enabled", False),
            (None, "checkin_result", "exit-code"),
            (None, "sc_modules", 2),
            (None, "onboarding_present", True),
        )
        self.assertEqual(verify.check(baseline(), ready(), "installed"), [])
        for section, key, value in broken_cases:
            with self.subTest(section=section, key=key):
                observed = ready()
                target = observed[section] if section else observed
                target[key] = value
                self.assertTrue(verify.check(baseline(), observed, "installed"))

    def test_reboot_requires_a_different_boot_identity(self):
        observed = ready()
        self.assertIn("Reboot did not produce a new boot ID.", verify.check(baseline(), observed, "rebooted"))

        observed["boot_id"] = "second-boot"
        self.assertEqual(verify.check(baseline(), observed, "rebooted"), [])
