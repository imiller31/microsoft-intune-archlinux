#!/usr/bin/env python3
"""Initialize a new, stopped, unenrolled AUR Defender installation."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from onboard_mde import publish_private


STATE = Path("/var/opt/microsoft/mdatp/wdavstate")
CONFIG = Path("/etc/opt/microsoft/mdatp")
DMI_UUID = Path("/sys/class/dmi/id/product_uuid")


def create_state(destination):
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Existing Defender state must not be replaced or reset.")

    identity = None
    try:
        identity = uuid.UUID(DMI_UUID.read_text().strip())
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    if identity is not None and identity.int:
        document = {
            "machineGuid": str(identity),
            "machineUuidType": "Dmid",
            "platformDmidecodeUuid": str(identity),
        }
        source = "existing system DMI UUID"
    else:
        document = {
            "machineGuid": str(uuid.uuid4()),
            "machineUuidType": "Uuid",
        }
        source = "generated MDE UUID (system DMI unavailable or invalid)"

    document.update(releaseRing="Production", validateId=True)
    publish_private(destination, (json.dumps(document) + "\n").encode())
    print(f"Initialized private Defender state using {source}; system identifiers were not changed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create initial state; never replace existing state.")
    args = parser.parse_args(argv)
    if not args.apply:
        print(f"Preview: create {STATE} only for a new, stopped, unenrolled installation. Use --apply after review.")
        return 0

    try:
        if os.geteuid() != 0:
            raise ValueError("Applying initial state requires root.")
        if not Path("/opt/microsoft/mdatp/sbin/wdavdaemon").is_file():
            raise ValueError("Install and review mdatp-bin before initializing Defender.")
        if STATE.exists() or STATE.is_symlink():
            raise FileExistsError("Existing Defender state requires manual review; it will not be reset.")
        if any((CONFIG / name).exists() or (CONFIG / name).is_symlink()
               for name in ("mdatp_onboard.json", "mdatp_offboard.json")):
            raise ValueError("Enrollment configuration exists; administrator review is required.")

        service = subprocess.run(
            ["systemctl", "is-active", "--quiet", "mdatp.service"], check=False, timeout=20,
        )
        if service.returncode != 3:
            raise ValueError("Defender must be installed and inactive before first initialization.")
        if STATE.parent.is_symlink():
            raise ValueError("Defender state directory must not be symlinked.")

        STATE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        create_state(STATE)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Initialization refused: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
