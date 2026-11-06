# Setup and verification reference

## Intune

Use a fully updated x86_64 Arch/CachyOS desktop with systemd, a working user
session bus, and a password-protected, unlocked default login keyring. Follow
your organization's requirements for encryption, password policy, MFA, and
permitted distributions. A compatibility namespace does not grant policy approval.

From the reviewed repository root:

```sh
bash setup-intune-cachyos.sh --help
bash setup-intune-cachyos.sh --skip-vpn
```

The script performs a full system upgrade and installs the broker and portal.
It writes compatibility files and moves `lsb_release` aside if present. Review
its PAM and environment changes first. It does not enroll the device, install
Edge or Defender, or prove that encryption/password policy is compliant.
Install `microsoft-edge-stable-bin` separately if required by the organization.
Review AUR build files before installing; use a normal user for `makepkg`.

For smartcards, read the repository's `README_smartcard.md`. Verify `pcscd`,
the PIV certificate, NSS/OpenSC registration, and `p11-kit` before signing in.
The broker/portal wrappers use X11 under Wayland to avoid PIN-dialog loops.
PIN caching is opt-in with `--enable-pin-cache`; existing OpenSC policy is
preserved. It can reduce duplicate prompts, but never substitutes for the
organization's approved authentication method or user-consent policy.

Open `seahorse` to create/unlock the password-protected login keyring, then
log out and back in. Launch `intune-portal` as the desktop user and complete
the organization's enrollment flow.

```sh
systemctl --user is-active intune-agent.timer
systemctl --user start intune-agent.service
journalctl --user -u intune-agent.service -n 50 --no-pager
sudo journalctl -u microsoft-identity-device-broker.service -n 50 --no-pager
```

Require a successful check-in and the expected device in the corporate portal.
If check-in says no user has logged in, inspect `~/.config/intune` and
`~/.local/state/intune`. Upstream recommends linking the latter to the former.
Only do so after confirming the config exists and the state path is absent:

```sh
mkdir -p ~/.local/state
ln -s ~/.config/intune ~/.local/state/intune
systemctl --user restart intune-agent.timer
systemctl --user start intune-agent.service
```

If the state path already exists, preserve it and inspect its contents privately.
Avoid replacing a directory or link without understanding the existing enrollment.

## MDE installation and onboarding

Review and install the community `mdatp-bin` package using the user's approved
AUR workflow. Confirm package integrity, the `mdatp` account, and the vendor
systemd unit. Intune's namespace workaround should not be applied to Defender.
Do not modify `/etc/os-release` to convince a vendor installer that Arch is Ubuntu.

The reviewed AUR package omits the vendor Debian post-install state
initialization. A fresh, unenrolled VM reproduced UUID exceptions even without
an onboarding package. Before the **first** service start on a genuinely new
installation, review and preview the first-start helper:

```sh
python3 .github/skills/arch-intune-mde/scripts/initialize_mde.py
sudo systemctl daemon-reload
sudo python3 .github/skills/arch-intune-mde/scripts/initialize_mde.py --apply
```

It creates private initial MDE state using the vendor's identity fields and
DMI-first/generated-UUID fallback. It leaves system identifiers unchanged and
refuses existing state, enrollment configuration, or an active service.
Existing or previously started installations require private administrator
review instead; deleting state to rerun this helper risks device identity loss.
This is a community packaging workaround, not a vendor-supported Arch installer.

```sh
sudo systemctl enable --now mdatp.service
sudo env -u LD_PRELOAD -u LD_AUDIT -u LD_LIBRARY_PATH mdatp health
```

Obtain the current onboarding package through the organization's authenticated
portal. Treat it as sensitive, store it privately, and keep it outside the
checkout. The helper accepts these archive members, or a local `.sh`/`.py`:

- `onboarding-script-prod.sh`
- `onboarding-script-prod-manual.sh`
- `MicrosoftDefenderATPOnboardingLinuxServer.py`

It reads only literal onboarding data. It does not run downloaded code, fetch
credentials, fabricate licensing, rewrite machine identity, or verify Microsoft's
signature itself. The Defender agent verifies the signed content.

```sh
python3 .github/skills/arch-intune-mde/scripts/onboard_mde.py /private/path/onboarding.zip
sudo python3 .github/skills/arch-intune-mde/scripts/onboard_mde.py \
    /private/path/onboarding.zip --apply
sudo systemctl restart mdatp.service
```

For a reviewed same-tenant payload update, append `--replace` to the apply
command. The helper keeps a mode-0600 backup beside `mdatp_onboard.json`.
It refuses tenant conflicts, symlinked destination files, and offboarding files.
Replacement does not delete backups or offboarding data.

Allow initial agent startup and definition updates to finish, then inspect:

```sh
sudo env -u LD_PRELOAD -u LD_AUDIT -u LD_LIBRARY_PATH mdatp health
sudo env -u LD_PRELOAD -u LD_AUDIT -u LD_LIBRARY_PATH mdatp connectivity test
systemctl show mdatp -p ActiveState -p SubState -p NRestarts
sudo journalctl -u mdatp -n 50 --no-pager
```

Check `licensed`, `healthy`, the expected `org_id`, and `edr_machine_id`.
Repeat health and service checks after initialization and an approved reboot.
Confirm the device appears in the organization's Defender portal.
Never equate `healthy: true` with active antivirus: inspect `engine_load_status`,
`antivirus_enforcement_level`, `passive_mode_enabled`, and
`real_time_protection_enabled`. Keep passive mode when required by tenant policy.

### Diagnostic branches

| Symptom | Next evidence |
| --- | --- |
| Cannot connect to daemon | Check service status and journal; compare a clean loader environment. |
| Missing license, no organization | Check whether onboarding JSON exists and matches the current approved package; inspect EDR startup logs before assuming the tenant lacks a subscription. |
| UUID exception | Check whether the package initialized `wdavstate`; fresh AUR installations can fail before onboarding. Preserve existing state and machine IDs. Then review payload structure and current versus legacy signed packages privately. |
| Organization mismatch or offboarding file | Stop and obtain administrator guidance. |
| Service works but CLI fails | Loader injection can be rejected by Defender. Use the clean-environment commands above; do not weaken the daemon's loader checks. |
| Cloud connectivity fails | Check required endpoints, proxy configuration, and TLS trust using Microsoft's connectivity guidance; retain certificate validation. |
| Healthy but passive/engine unloaded | Enrollment is not proof of antivirus readiness. Inspect tenant policy and engine logs before making protection changes. |

During development, licensing and EDR registration recovered after replacing
a legacy Python onboarding payload with the same organization's newer signed
deployment-tool payload and restarting the service. The exact cause was not
isolated; separate fresh-VM testing exposed missing package initialization.
Neither observation justifies editing signed JSON or resetting existing state.

## Testing without using the enrolled workstation

The `Arch setup checks` GitHub Actions workflow runs in a disposable Arch
container on a GitHub-hosted runner. It uses synthetic signatures and tenants,
tests the actual helper against temporary files and a fake `mdatp` executable,
and checks shell syntax, ShellCheck, and Arch package metadata. It has no
enrollment secrets and does not run setup on the user's machine.

CI is intentionally not an end-to-end enrollment test. For that, use a fresh,
disposable x86_64 Arch/CachyOS desktop VM with systemd, a user session, and an
organization-approved test account. Take a snapshot before setup:

1. Update the VM, clone the branch, and review `setup-intune-cachyos.sh`.
   Run `--skip-vpn` unless testing VPN. Require package installation, intact
   real distro metadata, working portal launch, and active user timer.
2. Run setup again before enrollment. Require existing keyring, NSS modules,
   OpenSC policy, and unrelated service drop-ins to survive.
3. If approved, complete Intune sign-in and require a successful check-in plus
   portal enrollment. For smartcards, pass the physical key through to the VM
   and have the user enter its PIN.
4. Install MDE. Preview an approved onboarding package, confirm preview changes
   nothing, then explicitly apply it. Require the MDE verification checks above.
5. Reboot and repeat checks. Record antivirus and compliance results separately.
   Have IT offboard the test device before deleting the VM; restore snapshots
   only under the organization's device-identity guidance.

Until that approved VM run is completed, report live setup, MFA, and corporate
compliance as unverified. Keep real onboarding packages out of CI artifacts.

### Automated VM installation lane

The manual `Arch VM installation smoke` workflow boots a pinned, checksum-checked
Arch cloud image and verifies its signature with the arch-boxes public key
published in the [Arch image project's README](https://gitlab.archlinux.org/archlinux/arch-boxes).
It runs only on a disposable GitHub-hosted runner with KVM. Its push trigger
registers the workflow from this fork's non-default branch; VM jobs run only
after an explicit manual dispatch.

```sh
gh workflow run arch-vm-smoke.yml \
    --repo imiller31/microsoft-intune-archlinux --ref cachyos-setup-script
```

The guest has a synthetic X11/Xfce user session, OpenSC policy, unrelated service
drop-in, and NSS certificate. Real Intune packages and a reviewed, pinned AUR
Defender package are installed as the desktop user. The host independently
verifies preservation, visible portal launch, broker activation/environment,
check-in service execution, unlicensed Defender engine startup, repeated setup,
and reboot persistence. It samples Defender twice per phase to detect restarts.

`tests/vm/image.json` pins the image digest and AUR commit. Review their upstream
changes before updating those pins. No tenant package, login, corporate account,
or model credential is supplied. No disk, SSH key, or private fixture key is
uploaded. Only installation logs and non-enrollment evidence are retained for
seven days; the runner terminates QEMU and removes its temporary disk and keys.

The artifacts explicitly distinguish `installation_smoke` from
`agent_execution: not_run_no_model_credentials` and
`corporate_enrollment: not_run_not_authorized`. A green installation job is not
an end-to-end skill pass. The future agent lane must use approved CI model
authentication, execute the skill as the desktop user, and reuse the host's
independent verifier; agent-written summaries cannot satisfy the oracle.

## Public references

- [Microsoft Linux enrollment](https://learn.microsoft.com/en-us/intune/intune-service/user-help/enroll-device-linux)
- [Microsoft Linux SSO and smartcards](https://learn.microsoft.com/en-us/entra/identity/devices/sso-linux)
- [Defender manual installation and onboarding](https://learn.microsoft.com/en-us/defender-endpoint/linux-install-manually)
- [Defender prerequisites](https://learn.microsoft.com/en-us/defender-endpoint/mde-linux-prerequisites)
- [Defender connectivity troubleshooting](https://learn.microsoft.com/en-us/defender-endpoint/linux-support-connectivity)
- [AUR mdatp-bin](https://aur.archlinux.org/packages/mdatp-bin)
