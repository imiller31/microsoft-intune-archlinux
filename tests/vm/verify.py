"""Independent assertions over snapshots collected from a disposable guest."""


PRESERVED = ("machine_id", "os_release", "opensc", "dropin", "nss")


def check(before, after, phase):
    failures = []
    for name in PRESERVED:
        original = before.get("preserved", {}).get(name)
        current = after.get("preserved", {}).get(name)
        if not original or original != current:
            failures.append(f"Preserved {name} changed or disappeared.")

    return failures
