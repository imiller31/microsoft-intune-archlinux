"""Independent assertions over snapshots collected from a disposable guest."""


PRESERVED = ("machine_id", "os_release", "opensc", "dropin", "nss")
PACKAGES = ("intune-portal", "microsoft-identity-broker", "mdatp-bin")
SERVICES = ("display", "desktop", "intune_socket", "intune_timer", "smartcard", "mde")


def check(before, after, phase):
    if phase not in ("installed", "repeated", "rebooted"):
        raise ValueError(f"Unknown VM verification phase: {phase}")

    failures = []
    for name in PRESERVED:
        original = before.get("preserved", {}).get(name)
        current = after.get("preserved", {}).get(name)
        if not original or original != current:
            failures.append(f"Preserved {name} changed or disappeared.")

    for package in PACKAGES:
        if not after.get("packages", {}).get(package):
            failures.append(f"Package {package} is missing.")
    for service in SERVICES:
        if after.get("services", {}).get(service) is not True:
            failures.append(f"Service {service} is not active.")

    required = {
        "timer_enabled": True,
        "portal_visible": True,
        "broker_backend": "x11",
        "checkin_result": "success",
        "sc_modules": 1,
        "onboarding_present": False,
    }
    for field, expected in required.items():
        if after.get(field) != expected or type(after.get(field)) is not type(expected):
            failures.append(f"Unexpected {field}: expected {expected!r}, observed {after.get(field)!r}.")

    mde = after.get("mde", {})
    if mde.get("licensed") is not False:
        failures.append("Unonboarded Defender must report licensed=false.")
    version = mde.get("engine_version")
    if not isinstance(version, str) or not version or version == "unavailable":
        failures.append("Defender did not report an engine version.")
    if mde.get("uuid_errors") != 0:
        failures.append("Defender logged UUID initialization failures or its journal was not inspected.")
    if not mde.get("invocation"):
        failures.append("Defender has no running service invocation.")

    if phase == "rebooted" and (
        not before.get("boot_id") or not after.get("boot_id") or before["boot_id"] == after["boot_id"]
    ):
        failures.append("Reboot did not produce a new boot ID.")

    return failures
