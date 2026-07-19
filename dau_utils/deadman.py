"""Prime a forced reboot before a risky PCIe operation, cancel it on success.

The dpv1 wedge class that needs a power cycle is a driver/PCIe hang where the
kernel and systemd stay alive -- they keep petting the hardware watchdog, so
systemd's ``RuntimeWatchdogSec`` never fires, yet ``systemctl reboot`` hangs on
the stuck device. The recovery is ``sysrq-b`` (``emergency_restart()``), which
resets immediately without touching the wedged driver.

``arm`` schedules that reset as a transient systemd timer so it survives the
controlling SSH session; ``disarm`` cancels it. Run ``arm`` before a rescan,
flash, or register probe and ``disarm`` once it returns cleanly. If the box
wedges before ``disarm``, the timer fires and reboots it. A full kernel lock
(systemd itself dead) is still caught by systemd's own hardware watchdog.
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

DEFAULT_UNIT = "dau-deadman"
DEFAULT_TIMEOUT_S = 180

# sysrq 'b' = emergency_restart(): reboot past a wedged driver, where a clean
# `systemctl reboot` would block on it. Best-effort sync first so a live
# filesystem lands its journal; if the box is too wedged to sync, the reboot
# still proceeds.
RESET_SCRIPT = "echo s > /proc/sysrq-trigger; echo b > /proc/sysrq-trigger"


def arm_command(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> tuple[str, ...]:
    """The ``systemd-run`` invocation that schedules the reset ``timeout_s`` from now."""
    if timeout_s < 1:
        raise ValueError(f"deadman timeout must be at least 1 second, got {timeout_s}")
    return (
        "sudo",
        "systemd-run",
        f"--unit={unit}",
        f"--on-active={timeout_s}",
        "--timer-property=AccuracySec=1s",
        "--collect",
        "/bin/sh",
        "-c",
        RESET_SCRIPT,
    )


def disarm_commands(*, unit: str = DEFAULT_UNIT) -> tuple[tuple[str, ...], ...]:
    """Stop the pending timer/service and clear any failed state, idempotently."""
    return (
        ("sudo", "systemctl", "stop", f"{unit}.timer", f"{unit}.service"),
        ("sudo", "systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service"),
    )


def status_command(*, unit: str = DEFAULT_UNIT) -> tuple[str, ...]:
    """List the pending deadman timer, if armed."""
    return ("systemctl", "list-timers", "--all", f"{unit}.timer")


def arm(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> None:
    """Clear any stale unit, then schedule the forced reset."""
    for command in disarm_commands(unit=unit):
        subprocess.run(command, check=False, capture_output=True)
    subprocess.run(arm_command(timeout_s, unit=unit), check=True)


def disarm(*, unit: str = DEFAULT_UNIT) -> None:
    """Cancel the pending reset. Safe to call when nothing is armed."""
    for command in disarm_commands(unit=unit):
        subprocess.run(command, check=False, capture_output=True)


@contextmanager
def armed(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> Iterator[None]:
    """Arm around a risky block; disarm on the way out, success or exception."""
    arm(timeout_s, unit=unit)
    try:
        yield
    finally:
        disarm(unit=unit)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prime a forced reboot before a risky PCIe op; cancel it on success")
    parser.add_argument("action", choices=("arm", "disarm", "status"), help="Deadman action")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Seconds before the reset fires when arming")
    parser.add_argument("--unit", default=DEFAULT_UNIT, help="Transient systemd unit name")
    parser.add_argument("--dry-run", action="store_true", help="Print the command(s) without running them")
    args = parser.parse_args(argv)

    if args.action == "arm":
        if args.dry_run:
            print(" ".join(arm_command(args.timeout, unit=args.unit)))
            return 0
        arm(args.timeout, unit=args.unit)
        print(f"deadman armed: reset in {args.timeout}s (unit {args.unit}); disarm before then")
        return 0

    if args.action == "disarm":
        if args.dry_run:
            for command in disarm_commands(unit=args.unit):
                print(" ".join(command))
            return 0
        disarm(unit=args.unit)
        print(f"deadman disarmed (unit {args.unit})")
        return 0

    subprocess.run(status_command(unit=args.unit), check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
