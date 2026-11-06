import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/skills/arch-intune-mde/scripts"
SPEC = importlib.util.spec_from_file_location("initialize_mde", SCRIPTS / "initialize_mde.py")
initialize = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    SPEC.loader.exec_module(initialize)


class InitialStateTests(unittest.TestCase):
    def test_initialization_creates_valid_identity_without_overwriting_existing_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "wdavstate"
            initialize.create_state(state)
            before = state.read_bytes()
            document = json.loads(before)

            self.assertEqual(str(uuid.UUID(document["machineGuid"])), document["machineGuid"])
            self.assertTrue(document["validateId"])
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                initialize.create_state(state)

            self.assertEqual(state.read_bytes(), before)

    def test_preview_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "missing" / "wdavstate"
            with patch.object(initialize, "STATE", state):
                self.assertEqual(initialize.main([]), 0)

            self.assertFalse(state.parent.exists())

    def test_active_service_is_refused_before_state_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "wdavstate"
            with (
                patch.object(initialize, "STATE", state),
                patch.object(initialize, "CONFIG", Path(temporary)),
                patch.object(initialize.os, "geteuid", return_value=0),
                patch.object(Path, "is_file", return_value=True),
                patch.object(initialize.subprocess, "run") as service,
            ):
                service.return_value.returncode = 0
                self.assertEqual(initialize.main(["--apply"]), 1)

            self.assertFalse(state.exists())
