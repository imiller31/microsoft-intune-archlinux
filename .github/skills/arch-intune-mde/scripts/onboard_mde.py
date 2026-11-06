#!/usr/bin/env python3
"""Install local Defender onboarding data without executing deployment scripts."""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile


CONFIG_DIR = Path("/etc/opt/microsoft/mdatp")
SCRIPT_NAMES = {
    "onboarding-script-prod.sh",
    "onboarding-script-prod-manual.sh",
    "MicrosoftDefenderATPOnboardingLinuxServer.py",
}
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024


class EnrollmentError(Exception):
    """An actionable error that contains no enrollment data."""


def organization(payload):
    document = json.loads(payload)
    envelope = json.loads(document["onboardingInfo"])
    body = json.loads(envelope["body"])
    tenant = str(uuid.UUID(body["orgId"]))

    if not all(isinstance(envelope.get(key), str) and envelope[key] for key in ("sig", "cert")):
        raise EnrollmentError("Onboarding data must include a signature and certificate.")

    return tenant


def read_payload(source):
    if source.suffix.lower() == ".zip":
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise EnrollmentError("Onboarding archive exceeds 8 MiB.")

        with zipfile.ZipFile(source) as archive:
            members = [entry for entry in archive.infolist() if entry.filename in SCRIPT_NAMES]
            if len(members) != 1 or members[0].file_size > MAX_SCRIPT_BYTES:
                raise EnrollmentError("Expected exactly one recognized onboarding script under 1 MiB.")

            name = members[0].filename
            script = archive.read(members[0]).decode("utf-8-sig")
    else:
        if source.suffix.lower() not in (".sh", ".py"):
            raise EnrollmentError("Supply an onboarding ZIP, shell script, or Python script.")
        if source.stat().st_size > MAX_SCRIPT_BYTES:
            raise EnrollmentError("Onboarding script exceeds 1 MiB.")

        name = source.name
        script = source.read_text(encoding="utf-8-sig")

    if name.endswith(".sh"):
        literals = re.findall(r"(?ms)^BLOB_PAYLOAD='(.*?)'\s*$", script)
        if len(literals) != 1:
            raise EnrollmentError("Expected one single-quoted, literal BLOB_PAYLOAD assignment.")

        payload = literals[0]
    else:
        writes = [
            node for node in ast.walk(ast.parse(script))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write"
        ]
        if len(writes) != 1 or len(writes[0].args) != 1:
            raise EnrollmentError("Expected one literal Python onboarding write.")

        payload = ast.literal_eval(writes[0].args[0])

    if not isinstance(payload, str):
        raise EnrollmentError("The onboarding payload must be a literal string.")

    raw = payload.encode("utf-8")
    return raw, organization(raw)


def agent_identity():
    environment = os.environ.copy()
    for key in ("LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH"):
        environment.pop(key, None)

    fields = {}
    for field in ("licensed", "org_id"):
        result = subprocess.run(
            ["mdatp", "health", "--field", field, "--output", "json"],
            env=environment, capture_output=True, text=True, check=True, timeout=20,
        )
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or response.get("name") != field:
            raise EnrollmentError("Defender returned an unexpected health response.")

        fields[field] = response["value"]

    if not isinstance(fields["licensed"], bool):
        raise EnrollmentError("Defender did not report a boolean license status.")

    tenant = fields["org_id"]
    if tenant not in (None, ""):
        tenant = str(uuid.UUID(tenant))
    if fields["licensed"] and not tenant:
        raise EnrollmentError("Licensed Defender agent has no organization; review it manually.")

    return fields["licensed"], tenant


def publish_private(destination, data, replace=False):
    descriptor, name = tempfile.mkstemp(prefix=".mdatp-onboard-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        if replace:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)

        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def apply_payload(payload, tenant, replace=False):
    if not CONFIG_DIR.is_dir() or CONFIG_DIR.is_symlink():
        raise EnrollmentError("Defender configuration directory is missing or symlinked; review installation.")

    offboard = CONFIG_DIR / "mdatp_offboard.json"
    destination = CONFIG_DIR / "mdatp_onboard.json"
    if offboard.exists() or offboard.is_symlink():
        raise EnrollmentError("An offboarding file exists; resolve it with your administrator first.")
    if destination.is_symlink():
        raise EnrollmentError("Refusing to overwrite a symlinked onboarding file.")

    licensed, current_tenant = agent_identity()
    if current_tenant and current_tenant != tenant:
        raise EnrollmentError("Defender belongs to another organization; no changes made.")
    if licensed:
        return "Defender is already licensed to this organization; no changes made."

    if destination.exists():
        existing = destination.read_bytes()
        if organization(existing) != tenant:
            raise EnrollmentError("Existing onboarding data targets another organization; no changes made.")
        if json.loads(existing) == json.loads(payload):
            return "Identical onboarding data is already installed; investigate agent logs."
        if not replace:
            raise EnrollmentError("Onboarding data differs; review it, then use --replace for a same-tenant update.")

        descriptor, name = tempfile.mkstemp(prefix="mdatp_onboard.json.backup-", dir=CONFIG_DIR)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(existing)
            stream.flush()
            os.fsync(stream.fileno())

        publish_private(destination, payload, replace=True)
        return f"Updated onboarding data; original saved privately at {name}."

    publish_private(destination, payload)
    return "Installed onboarding data with mode 0600."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Local organization-provided onboarding ZIP, .sh, or .py")
    parser.add_argument("--apply", action="store_true", help="Write configuration as root; otherwise preview only")
    parser.add_argument("--replace", action="store_true", help="Back up and replace differing same-tenant data")
    args = parser.parse_args(argv)

    try:
        if args.replace and not args.apply:
            raise EnrollmentError("--replace requires --apply.")
        if args.apply and os.geteuid() != 0:
            raise EnrollmentError("--apply requires root; preview first, then use sudo.")

        payload, tenant = read_payload(args.source)
        if not args.apply:
            print("Valid onboarding structure found; Defender must verify its signature.")
            print(f"Would install {CONFIG_DIR}/mdatp_onboard.json, root-owned with mode 0600.")
            print("Preview only: no scripts executed, configuration written, or services changed.")
            return 0

        print(apply_payload(payload, tenant, args.replace))
        print("Configuration alone does not confirm enrollment. Restart mdatp, then verify health and connectivity.")
        return 0
    except EnrollmentError as error:
        print(f"Onboarding refused: {error}", file=sys.stderr)
    except subprocess.SubprocessError:
        print("Cannot query Defender health. Check mdatp.service and its logs before retrying.", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError, zipfile.BadZipFile) as error:
        print(
            f"Onboarding failed ({type(error).__name__}); check the local source and filesystem. "
            "Payload contents were not logged.",
            file=sys.stderr,
        )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
