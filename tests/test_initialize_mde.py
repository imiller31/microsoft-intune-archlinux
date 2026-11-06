import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/skills/arch-intune-mde/scripts"
SPEC = importlib.util.spec_from_file_location("initialize_mde", SCRIPTS / "initialize_mde.py")
initialize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(initialize)


class InitialStateTests(unittest.TestCase):
    def test_initialization_creates_valid_identity_without_overwriting_existing_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "wdavstate"
            initialize.create_state(state)
            before = state.read_bytes()
            document = json.loads(before)

            self.assertTrue(document["machineGuid"])
            self.assertTrue(document["validateId"])
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                initialize.create_state(state)

            self.assertEqual(state.read_bytes(), before)
