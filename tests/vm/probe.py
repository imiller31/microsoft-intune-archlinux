"""Collect only non-enrollment evidence from a disposable guest."""

import hashlib
import json
from pathlib import Path
import pwd
import subprocess


def command(arguments, user=False):
    if user:
        uid = pwd.getpwnam("tester").pw_uid
        arguments = [
            "runuser", "-u", "tester", "--", "env", "HOME=/home/tester",
            f"XDG_RUNTIME_DIR=/run/user/{uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
            "DISPLAY=:99", *arguments,
        ]

    return subprocess.run(arguments, capture_output=True, text=True, timeout=30)


def digest(path):
    file = Path(path)
    return hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None


def active(unit, user=False):
    arguments = ["systemctl"]
    if user:
        arguments.append("--user")

    return command([*arguments, "is-active", "--quiet", unit], user=user).returncode == 0


def health(field):
    result = command(["mdatp", "health", "--field", field, "--output", "json"])
    if result.returncode:
        return None

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    return response.get("value") if isinstance(response, dict) and response.get("name") == field else None


def main():
    certificate = command([
        "certutil", "-L", "-n", "arch-e2e-fixture", "-a",
        "-d", "sql:/home/tester/.pki/nssdb",
    ], user=True)
    packages = {}
    for package in ("intune-portal", "microsoft-identity-broker", "mdatp-bin"):
        result = command(["pacman", "-Q", package])
        packages[package] = result.stdout.strip() if result.returncode == 0 else None

    service = command([
        "busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "StartServiceByName", "su", "com.microsoft.identity.broker1", "0",
    ], user=True)
    broker_backend = None
    if service.returncode == 0:
        process = command([
            "busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", "com.microsoft.identity.broker1",
        ], user=True)
        if process.returncode == 0:
            pid = int(process.stdout.split()[1])
            environment = Path(f"/proc/{pid}/environ")
            try:
                entries = environment.read_bytes().split(b"\0")
            except FileNotFoundError:
                entries = []

            for entry in entries:
                if entry.startswith(b"GDK_BACKEND="):
                    broker_backend = entry.partition(b"=")[2].decode()

    journal = command(["journalctl", "-b", "-u", "mdatp", "--no-pager", "-o", "cat"]).stdout
    checkin_start = command(["systemctl", "--user", "start", "intune-agent.service"], user=True)
    checkin = command([
        "systemctl", "--user", "show", "intune-agent.service", "--property=Result", "--value",
    ], user=True)
    modules = command(["modutil", "-dbdir", "sql:/home/tester/.pki/nssdb", "-list"], user=True)
    result = {
        "boot_id": digest("/proc/sys/kernel/random/boot_id"),
        "preserved": {
            "machine_id": digest("/etc/machine-id"),
            "os_release": digest("/etc/os-release"),
            "opensc": digest("/etc/opensc.conf"),
            "dropin": digest("/etc/systemd/user/microsoft-identity-broker.service.d/preserve.conf"),
            "nss": hashlib.sha256(certificate.stdout.encode()).hexdigest()
            if certificate.returncode == 0 else None,
        },
        "packages": packages,
        "services": {
            "display": active("arch-e2e-display.service"),
            "desktop": active("arch-e2e-desktop.service", user=True),
            "intune_socket": active("intune-daemon.socket"),
            "intune_timer": active("intune-agent.timer", user=True),
            "smartcard": active("pcscd.service"),
            "mde": active("mdatp.service"),
        },
        "timer_enabled": command([
            "systemctl", "--user", "is-enabled", "--quiet", "intune-agent.timer",
        ], user=True).returncode == 0,
        "portal_visible": command([
            "xdotool", "search", "--onlyvisible", "--name", "Intune",
        ], user=True).returncode == 0,
        "broker_backend": broker_backend,
        "checkin_result": checkin.stdout.strip() if checkin_start.returncode == 0 else "start-failed",
        "sc_modules": modules.stdout.count("SC Module"),
        "mde": {
            "licensed": health("licensed") if packages["mdatp-bin"] else None,
            "engine_version": health("engine_version") if packages["mdatp-bin"] else None,
            "uuid_errors": journal.count("uuid.h"),
            "invocation": command([
                "systemctl", "show", "mdatp.service", "--property=InvocationID", "--value",
            ]).stdout.strip(),
        },
        "onboarding_present": Path("/etc/opt/microsoft/mdatp/mdatp_onboard.json").exists(),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
