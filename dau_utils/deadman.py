"""Prime a forced reboot before a risky PCIe operation, cancel it on success.

The hang this recovers from is a driver or PCIe wedge where the kernel and
systemd stay alive: they keep petting the hardware watchdog, so systemd's
``RuntimeWatchdogSec`` never fires, yet ``systemctl reboot`` blocks on the
stuck device. The recovery is ``sysrq-b`` (``emergency_restart()``), which
resets immediately without touching the wedged driver.

``arm`` schedules that reset as a transient systemd timer so it survives the
controlling SSH session; ``disarm`` cancels it. Run ``arm`` before a rescan,
flash, or register probe and ``disarm`` once it returns cleanly. If the box
wedges before ``disarm``, the timer fires and reboots it. A full kernel lock
(systemd itself dead) is still caught by systemd's own hardware watchdog.

Every call into systemd is bounded and non-interactive (``sudo -n``), and a
call that fails, hangs or is missing surfaces as :class:`DeadmanError`: the
caller must not treat the host as protected, or as safe, on anything less
than a confirmed answer.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from collections.abc import Generator, Sequence
from contextlib import contextmanager

DEFAULT_UNIT = "dau-deadman"
# policy: long enough for a reprogram and rescan to complete, short enough
# that a wedged host comes back without anyone present
DEFAULT_TIMEOUT_S = 180
# systemd answers in well under a second; a call that takes this long is
# the hang this tool exists to survive, and must not hang the tool itself
COMMAND_TIMEOUT_S = 15

# the only units this tool may create, stop or reset: its own namespace. An
# arbitrary --unit would let `disarm` stop any service on the host.
_UNIT = re.compile(r"dau-deadman(-[a-z0-9]+)*")

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


def check_unit(unit: str) -> str:
    """The unit name, if it is in this tool's namespace."""
    if not _UNIT.fullmatch(unit):
        raise ValueError(f"deadman unit must be dau-deadman or dau-deadman-<suffix>, got {unit!r}")
    return unit


def arm_command(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> tuple[str, ...]:
    """The ``systemd-run`` invocation that schedules the reset ``timeout_s`` from now."""
    check_unit(unit)
    if timeout_s < 1:
        raise ValueError(f"deadman timeout must be at least 1 second, got {timeout_s}")
    return (
        "sudo",
        "-n",
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
    check_unit(unit)
    return (
        ("sudo", "-n", "systemctl", "stop", f"{unit}.timer", f"{unit}.service"),
        ("sudo", "-n", "systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service"),
    )


def status_command(*, unit: str = DEFAULT_UNIT) -> tuple[str, ...]:
    """List the pending deadman timer, if armed."""
    check_unit(unit)
    return ("systemctl", "list-timers", "--all", f"{unit}.timer")


def _run(command: Sequence[str], *, check: bool) -> subprocess.CompletedProcess:
    """One bounded, captured call; every failure mode is a DeadmanError."""
    try:
        return subprocess.run(tuple(command), check=check, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
    except FileNotFoundError as error:
        raise DeadmanError(f"{command[0]} is not installed: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise DeadmanError(f"{shlex.join(command)} did not answer within {COMMAND_TIMEOUT_S}s") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise DeadmanError(f"{shlex.join(command)} failed with exit {error.returncode}: {detail}") from error
    except OSError as error:
        raise DeadmanError(f"{shlex.join(command)} could not run: {error}") from error


def _is_active(name: str) -> bool:
    """True only if systemctl positively reports ``name`` not running. A failed
    query (D-Bus down, sudo denied, a hang) is treated as active -- we cannot
    claim a unit is stopped unless systemctl confirms it."""
    try:
        result = _run(("systemctl", "is-active", name), check=False)
    except DeadmanError:
        return True
    return result.stdout.strip() not in _INACTIVE_STATES


def is_armed(*, unit: str = DEFAULT_UNIT) -> bool:
    """True if a deadman timer or its service is still live for ``unit``."""
    check_unit(unit)
    return _is_active(f"{unit}.timer") or _is_active(f"{unit}.service")


def arm(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> None:
    """Schedule the forced reset and confirm the timer is live. Refuses to
    stomp an already-armed timer so concurrent callers cannot silently cancel
    each other's protection; clears only inactive stale state before
    scheduling."""
    command = arm_command(timeout_s, unit=unit)
    if is_armed(unit=unit):
        raise DeadmanError(f"{unit} is already armed; disarm it before arming again")
    _run(disarm_commands(unit=unit)[1], check=False)
    _run(command, check=True)
    if not is_armed(unit=unit):
        raise DeadmanError(f"systemd-run returned but {unit}.timer is not active; the host is NOT protected")


def disarm(*, unit: str = DEFAULT_UNIT) -> None:
    """Cancel the pending reset and confirm it is gone. Raises ``DeadmanError``
    if the timer cannot be verified inactive -- the caller must not treat the
    host as safe until this returns cleanly."""
    stop, reset_failed = disarm_commands(unit=unit)
    _run(stop, check=False)
    if is_armed(unit=unit):
        raise DeadmanError(f"{unit} still armed after stop; reset may still fire -- intervene before trusting the host")
    _run(reset_failed, check=False)


def status(*, unit: str = DEFAULT_UNIT) -> int:
    """Print the timer listing; return systemctl's exit code."""
    result = _run(status_command(unit=unit), check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


@contextmanager
def armed(timeout_s: int = DEFAULT_TIMEOUT_S, *, unit: str = DEFAULT_UNIT) -> Generator[None, None, None]:
    """Arm around a risky block; disarm on the way out, success or exception.

    This is for Python callers whose failure is a Python exception. A hardware
    plan that fails a step must leave the timer ARMED so the host reboots if
    the step wedged it; use ``arm``/``disarm`` directly there.
    """
    arm(timeout_s, unit=unit)
    try:
        yield
    finally:
        disarm(unit=unit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prime a forced reboot before a risky PCIe op; cancel it on success")
    actions = parser.add_subparsers(dest="action", required=True)
    arm_parser = actions.add_parser("arm", help="schedule the reset")
    arm_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds before the reset fires")
    arm_parser.add_argument("--unit", default=DEFAULT_UNIT, type=check_unit, help="transient systemd unit, dau-deadman or dau-deadman-<suffix>")
    arm_parser.add_argument("--dry-run", action="store_true", help="print the command without running it")
    disarm_parser = actions.add_parser("disarm", help="cancel the pending reset")
    disarm_parser.add_argument("--unit", default=DEFAULT_UNIT, type=check_unit, help="transient systemd unit, dau-deadman or dau-deadman-<suffix>")
    disarm_parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    status_parser = actions.add_parser("status", help="list the pending timer")
    status_parser.add_argument("--unit", default=DEFAULT_UNIT, type=check_unit, help="transient systemd unit, dau-deadman or dau-deadman-<suffix>")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.action == "arm":
        if args.dry_run:
            print(shlex.join(arm_command(args.timeout, unit=args.unit)))
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
                print(shlex.join(command))
            return 0
        try:
            disarm(unit=args.unit)
        except DeadmanError as error:
            print(f"deadman DISARM FAILED: {error}", file=sys.stderr)
            return 1
        print(f"deadman disarmed (unit {args.unit})")
        return 0

    try:
        return status(unit=args.unit)
    except DeadmanError as error:
        print(f"deadman status unavailable: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
