#!/usr/bin/env python3
"""Run installation smoke tests only on a disposable GitHub-hosted QEMU guest."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time

from verify import check


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SESSION = (
    "env XDG_RUNTIME_DIR=/run/user/$(id -u) "
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus "
    "DISPLAY=:99 XDG_CURRENT_DESKTOP=XFCE"
)


def execute(arguments, *, log=None, **options):
    if log is not None:
        with log.open("wb") as stream:
            return subprocess.run(arguments, stdout=stream, stderr=subprocess.STDOUT, check=True, **options)

    return subprocess.run(arguments, capture_output=True, check=True, **options)


def stop_vm(process, ssh, output):
    if process.poll() is not None:
        return

    diagnostics = {
        "guest-journal.log": "sudo journalctl -b --no-pager -n 400",
        "intune-journal.log": (
            f"{SESSION} journalctl --user -b --no-pager -n 120 "
            "-u intune-agent.service -u arch-e2e-portal.service"
        ),
        "mde-journal.log": "sudo journalctl -b --no-pager -n 120 -u mdatp.service",
        "desktop-windows.log": (
            f"{SESSION} sh -c '"
            "systemctl --user status arch-e2e-portal.service --no-pager; "
            "xdotool search --onlyvisible --name . getwindowname %@'"
        ),
    }
    for name, command in diagnostics.items():
        try:
            with (output / name).open("wb") as log:
                subprocess.run(
                    [*ssh, command], stdout=log, stderr=subprocess.STDOUT, timeout=30, check=False,
                )
        except subprocess.TimeoutExpired:
            print(f"Diagnostic collection timed out: {name}", file=sys.stderr)

    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/vm"))
    args = parser.parse_args(argv)
    if (os.environ.get("GITHUB_ACTIONS"), os.environ.get("RUNNER_ENVIRONMENT")) != ("true", "github-hosted"):
        parser.error("This harness may run only on a disposable GitHub-hosted runner.")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        parser.error("The VM test requires an explicit manual workflow dispatch.")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        parser.error("Readable/writable /dev/kvm is required; software emulation is not a silent fallback.")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "installation_smoke": "failed",
        "agent_execution": "not_run_no_model_credentials",
        "corporate_enrollment": "not_run_not_authorized",
        "phases": [],
    }
    image = json.loads((HERE / "image.json").read_text())
    try:
        with tempfile.TemporaryDirectory(
            prefix="arch-intune-vm-", dir=os.environ["RUNNER_TEMP"],
        ) as temporary, ExitStack() as cleanup:
            work = Path(temporary)
            disk = work / "base.qcow2"
            for url, destination in ((image["url"], disk), (image["url"] + ".sig", work / "image.sig")):
                execute([
                    "curl", "--fail", "--location", "--proto", "=https", "--retry", "3",
                    "--connect-timeout", "30", "--max-time", "600", "--output", str(destination), url,
                ], timeout=650)

            with disk.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != image["sha256"]:
                    raise ValueError("Pinned Arch image checksum does not match.")

            execute([
                "gpg", "--batch", "--dearmor", "--output", str(work / "keys.gpg"),
                str(HERE / "arch-boxes.asc"),
            ])
            execute([
                "gpgv", "--keyring", str(work / "keys.gpg"), str(work / "image.sig"), str(disk),
            ], log=output / "image-verification.log")
            execute(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(work / "key")])
            configuration = {
                "hostname": "arch-enrollment-test",
                "users": [{
                    "name": "tester", "groups": ["wheel"], "shell": "/bin/bash",
                    "lock_passwd": True, "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
                    "ssh_authorized_keys": [(work / "key.pub").read_text().strip()],
                }],
                "ssh_pwauth": False,
                "disable_root": True,
                "write_files": [
                    {"path": "/etc/arch-intune-e2e", "content": "disposable-github-actions-vm\n"},
                    {"path": "/etc/pacman.d/mirrorlist",
                     "content": "Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n"},
                ],
                "runcmd": [["systemctl", "enable", "--now", "sshd"]],
            }
            (work / "user-data").write_text("#cloud-config\n" + json.dumps(configuration))
            (work / "meta-data").write_text("instance-id: arch-intune-e2e\nlocal-hostname: arch-enrollment-test\n")
            execute(["cloud-localds", str(work / "seed.iso"), str(work / "user-data"), str(work / "meta-data")])
            execute([
                "qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2",
                "-b", str(disk), str(work / "guest.qcow2"), "32G",
            ])
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]

            (work / "ssh-config").write_text(
                f"Host guest\n  HostName 127.0.0.1\n  Port {port}\n  User tester\n"
                f"  IdentityFile {work / 'key'}\n  UserKnownHostsFile {work / 'known_hosts'}\n"
                "  IdentitiesOnly yes\n  BatchMode yes\n  StrictHostKeyChecking accept-new\n"
                "  ConnectTimeout 5\n  ServerAliveInterval 15\n  LogLevel ERROR\n"
            )
            ssh = ["ssh", "-F", str(work / "ssh-config"), "guest"]
            with (output / "qemu.log").open("wb") as log:
                process = subprocess.Popen([
                    "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
                    "-drive", f"file={work / 'guest.qcow2'},format=qcow2,if=virtio",
                    "-drive", f"file={work / 'seed.iso'},format=raw,if=virtio,readonly=on",
                    "-netdev", f"user,id=net,hostfwd=tcp:127.0.0.1:{port}-:22",
                    "-device", "virtio-net-pci,netdev=net", "-display", "none",
                    "-serial", f"file:{output / 'serial.log'}", "-monitor", "none",
                ], stdout=log, stderr=subprocess.STDOUT)

            cleanup.callback(stop_vm, process, ssh, output)

            def remote(command, **options):
                return execute([*ssh, command], **options)

            def wait_for_ssh():
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("QEMU exited; inspect serial.log and qemu.log.")
                    result = subprocess.run([*ssh, "true"], capture_output=True, timeout=15)
                    if result.returncode == 0:
                        return
                    time.sleep(3)

                raise TimeoutError("Guest SSH did not become ready in 10 minutes.")

            def snapshot(name):
                observed = json.loads(remote(
                    "sudo python3 -", input=(HERE / "probe.py").read_bytes(), timeout=240,
                ).stdout)
                (output / f"{name}.json").write_text(json.dumps(observed, indent=2) + "\n")
                return observed

            def verify_phase(name, before):
                print(f"Verifying {name}...", flush=True)
                remote(
                    f"{SESSION} sh -c '"
                    "if ! systemctl --user is-active --quiet arch-e2e-portal.service; then "
                    "systemd-run --user --unit arch-e2e-portal --collect "
                    "--setenv=DISPLAY=:99 /usr/bin/intune-portal; fi'",
                    timeout=30, log=output / f"{name}-portal.log",
                )
                deadline = time.monotonic() + 180
                failures = []
                while time.monotonic() < deadline:
                    observed = snapshot(name)
                    failures = check(before, observed, name)
                    if not failures:
                        time.sleep(10)
                        stable = snapshot(name)
                        failures = check(before, stable, name)
                        if stable["mde"].get("invocation") != observed["mde"].get("invocation"):
                            failures.append("MDE restarted during stability observation.")
                        if not failures:
                            summary["phases"].append(name)
                            return stable
                    time.sleep(10)

                raise ValueError("; ".join(failures))

            print("Booting verified Arch image...", flush=True)
            wait_for_ssh()
            remote("sudo cloud-init status --wait", timeout=600, log=output / "cloud-init.log")
            archive = execute(["git", "-C", str(ROOT), "archive", "HEAD"]).stdout
            remote("mkdir checkout && tar -xf - -C checkout", input=archive)
            print("Provisioning desktop and preservation fixtures...", flush=True)
            remote("sudo bash -s", input=(HERE / "provision.sh").read_bytes(),
                   timeout=1200, log=output / "provision.log")
            before = snapshot("baseline")
            print("Running actual Intune setup as tester...", flush=True)
            remote(f"{SESSION} bash checkout/setup-intune-cachyos.sh --skip-vpn",
                   timeout=1200, log=output / "setup.log")
            commit = shlex.quote(image["mdatp_aur_commit"])
            print("Installing reviewed, pinned AUR Defender package...", flush=True)
            remote(
                "git clone https://aur.archlinux.org/mdatp-bin.git mdatp-build && "
                f"cd mdatp-build && git checkout --detach {commit} && makepkg -si --noconfirm",
                timeout=1200, log=output / "mdatp-install.log",
            )
            remote(
                "sudo systemctl daemon-reload && "
                "sudo python3 checkout/.github/skills/arch-intune-mde/scripts/initialize_mde.py --apply && "
                "sudo systemctl enable --now mdatp.service",
                timeout=60, log=output / "mdatp-initialize.log",
            )
            verify_phase("installed", before)
            print("Repeating setup to check preservation...", flush=True)
            remote(f"{SESSION} bash checkout/setup-intune-cachyos.sh --skip-vpn",
                   timeout=1200, log=output / "rerun.log")
            verify_phase("repeated", before)
            print("Rebooting the guest...", flush=True)
            result = subprocess.run([*ssh, "sudo systemctl reboot"], capture_output=True, timeout=30)
            if result.returncode not in (0, 255):
                raise RuntimeError("Guest rejected the reboot request.")
            time.sleep(10)
            wait_for_ssh()
            verify_phase("rebooted", before)
            summary["installation_smoke"] = "passed"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        summary["failure"] = str(error)
        print(f"VM smoke test failed: {error}", file=sys.stderr)
    finally:
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    return 0 if summary["installation_smoke"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
