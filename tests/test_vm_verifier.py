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


class VMVerifierTests(unittest.TestCase):
    def test_changed_machine_identity_is_rejected(self):
        before = baseline()
        after = baseline()
        after["preserved"]["machine_id"] = "replaced-identity"

        failures = verify.check(before, after, phase="installed")

        self.assertIn("Preserved machine_id changed or disappeared.", failures)
