---
name: arch-intune-mde
description: Sets up and diagnoses Microsoft Intune and Defender for Endpoint on Arch Linux and CachyOS. Use when enrolling an Arch device, configuring smartcard or Wayland authentication, onboarding MDE/mdatp, or investigating missing-license and Intune check-in failures.
---

# Arch Intune and MDE

## 1. Establish scope

Confirm the target is a disposable VM or a user-approved workstation, the work
account's organization, and the required components: Intune, MDE, smartcard, VPN.
Enrollment grants the organization device-management access. Confirm approval
for enrollment and system changes before applying either.

Read this checkout's `README.md`, `setup-intune-cachyos.sh`, and
[SETUP.md](SETUP.md). Inspect the current branch and worktree before fetching
upstream; preserve local changes. Keep organization-provided archives outside
the repository. Use current official instructions for that organization.

## 2. Inspect before changing

Identify the real distribution, architecture, desktop, disk encryption,
password policy, installed packages, services, and existing enrollment.
Arch/CachyOS is a community configuration, not an officially supported distro.
Report that distinction without treating it as proof of the failure.

Review the setup script's privileged changes before running it: full Arch
upgrade, Intune-only Ubuntu namespaces, `lsb_release` move, environment,
PAM policy, smartcard, and optional VPN configuration. Preserve existing
enrollment databases, machine IDs, passwords, keys, and tenant policy.

## 3. Set up Intune

Use the reviewed checkout's setup script as the desktop user, never root.
Use `--skip-vpn` when VPN is outside scope. Use `--enable-pin-cache` only after
the user approves that smartcard behavior. Follow the Intune section of
[SETUP.md](SETUP.md), including dependencies, keyring, MFA, and check-in.

Sign-in, hardware-key PIN entry, keyring unlocking, and corporate consent are
human steps. Pause for them. Confirm Intune enrollment and a successful
check-in separately from browser SSO; SSO alone is not device compliance.

## 4. Set up MDE separately

Review the current `mdatp-bin` AUR build before installing it as the desktop user.
Keep the real distribution visible to Defender and package managers. For a fresh
AUR installation, follow [SETUP.md](SETUP.md)'s first-start initialization before
enabling `mdatp.service`. Preserve existing state for administrator review.
Then check health in an environment without injected loader libraries.

Preview a local organization-provided ZIP or script without executing it:

```sh
python3 .github/skills/arch-intune-mde/scripts/onboard_mde.py /private/path/onboarding.zip
```

After approval, use the same command with `sudo python3` and `--apply`.
The helper changes only onboarding JSON, not services or protection policy.
If existing same-tenant data differs, inspect the difference privately and use
`--apply --replace` only with approval; a private backup is retained.
Conflicting tenants or offboarding files require administrator review.

For UUID crashes, check for missing package initialization before attributing
failure to an onboarding payload. Compare legacy and current signed packages
privately when investigating missing licensing.
Syntactically valid JSON is not proof that the agent accepts it.
Preserve the complete signed payload rather than editing individual fields.

## 5. Verify the actual outcome

After the approved service restart, require stable service operation,
`licensed: true`, `healthy: true`, the expected organization, an EDR device ID,
and successful cloud connectivity. Check logs for recurring crashes.
Report antivirus engine state, passive/active mode, real-time protection,
and corporate compliance separately. Change policy only with approval.

If health fails, use the diagnostic branches in [SETUP.md](SETUP.md), not an
automatic reinstall or state reset. Summarize what changed, backups, exact
remaining failures, and any human/admin steps still required.

## Testing away from the workstation

Use the fork's GitHub Actions checks and the disposable VM procedure in
[SETUP.md](SETUP.md). The manual VM installation lane exercises real packages,
session activation, preservation, reruns, and reboot. Its agent and corporate
enrollment lanes are explicitly not run. Synthetic CI and installation smoke
checks do not prove agent skill execution, hardware MFA, antivirus protection,
or corporate compliance.
