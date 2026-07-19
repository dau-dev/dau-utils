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
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

DEFAULT_UNIT = "dau-deadman"
DEFAULT_TIMEOUT_S = 180

# sysrq 'b' = emergency_restart(): reboot past a wedged driver, where a clean
# `systemctl reboot` would block on it. Best-effort sync first so a live
# filesystem lands its journal; if the box is too wedged to sync, the reboot
# still proceeds.
RESET_SCRIPT = "echo s > /proc/sysrq-trigger; echo b > /proc/sysrq-trigger"

# systemctl reports these when a unit is not running; anything else (notably
# "active"/"activating") means the pending reset is still live.
_INACTIVE_STATES = frozenset({"inactive", "failed", "unknown", "dead"})


class DeadmanError(RuntimeError):
    """A deadman operation could not be confirmed -- treat the host as unsafe."""


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


def _is_active(name: str) -> bool:
    """True only if systemctl positively reports ``name`` running. A failed
    query (D-Bus down, sudo denied) is treated as active -- we cannot claim a
    unit is stopped unless systemctl confirms it."""
    result = subprocess.run(("systemctl", "is-active", name), check=False, capture_output=True, text=True)
    return result.stdout.strip() not in _INACTIVE_STATES


def is_armed(*, unit: str = DEFAULT_UNIT) -> bool:
    """True if a deadman timer or its service is still live for ``unit``."""
    return _is_active(f"{unit}.timer") or _is_active(f"{unit}.service")


def arm(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> None:
    """Schedule the forced reset. Refuses to stomp an already-armed timer so
    concurrent callers cannot silently cancel each other's protection; clears
    only inactive stale state before scheduling."""
    if is_armed(unit=unit):
        raise DeadmanError(f"{unit} is already armed; disarm it before arming again")
    subprocess.run(("sudo", "systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service"), check=False, capture_output=True)
    subprocess.run(arm_command(timeout_s, unit=unit), check=True)


def disarm(*, unit: str = DEFAULT_UNIT) -> None:
    """Cancel the pending reset and confirm it is gone. Raises ``DeadmanError``
    if the timer cannot be verified inactive -- the caller must not treat the
    host as safe until this returns cleanly."""
    subprocess.run(("sudo", "systemctl", "stop", f"{unit}.timer", f"{unit}.service"), check=False, capture_output=True)
    if is_armed(unit=unit):
        raise DeadmanError(f"{unit} still armed after stop; reset may still fire -- intervene before trusting the host")
    subprocess.run(("sudo", "systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service"), check=False, capture_output=True)


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
        try:
            arm(args.timeout, unit=args.unit)
        except DeadmanError as error:
            print(f"deadman NOT armed: {error}", file=sys.stderr)
            return 1
        print(f"deadman armed: reset in {args.timeout}s (unit {args.unit}); disarm before then")
        return 0

    if args.action == "disarm":
        if args.dry_run:
            for command in disarm_commands(unit=args.unit):
                print(" ".join(command))
            return 0
        try:
            disarm(unit=args.unit)
        except DeadmanError as error:
            print(f"deadman DISARM FAILED: {error}", file=sys.stderr)
            return 1
        print(f"deadman disarmed (unit {args.unit})")
        return 0

    subprocess.run(status_command(unit=args.unit), check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
