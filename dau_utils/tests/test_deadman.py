from __future__ import annotations

import runpy
import shlex
import subprocess
import sys

import pytest

from dau_utils import deadman
from dau_utils.deadman import (
    COMMAND_TIMEOUT_S,
    DEFAULT_TIMEOUT_S,
    DEFAULT_UNIT,
    DeadmanError,
    arm,
    arm_command,
    check_unit,
    disarm,
    disarm_commands,
    is_armed,
    main,
    status_command,
)


def test_arm_command_schedules_a_transient_sysrq_reset_timer() -> None:
    command = arm_command(120, unit="dau-deadman")

    assert command[:7] == (
        "sudo",
        "-n",
        "systemd-run",
        "--unit=dau-deadman",
        "--on-active=120",
        "--timer-property=AccuracySec=1s",
        "--collect",
    )
    assert command[7:9] == ("/bin/sh", "-c")
    assert "/proc/sysrq-trigger" in command[9]
    assert command[9].strip().endswith("echo b > /proc/sysrq-trigger")


def test_arm_command_rejects_a_nonpositive_timeout() -> None:
    for bad in (0, -5):
        with pytest.raises(ValueError, match="at least 1 second"):
            arm_command(bad)


def test_units_outside_the_deadman_namespace_are_refused() -> None:
    """`disarm --unit sshd` would stop sshd; the tool may only touch its own units."""
    assert check_unit("dau-deadman") == "dau-deadman"
    assert check_unit("dau-deadman-bench2") == "dau-deadman-bench2"
    for bad in ("sshd", "dau-deadman.service", "dau-deadman-*", "", "dau_deadman"):
        with pytest.raises(ValueError, match="dau-deadman"):
            check_unit(bad)
    with pytest.raises(ValueError):
        disarm_commands(unit="sshd")
    with pytest.raises(SystemExit):
        main(["disarm", "--unit", "sshd", "--dry-run"])


def test_disarm_stops_the_timer_and_service_then_clears_failed_state() -> None:
    stop, reset = disarm_commands(unit="dau-deadman")

    assert stop == ("sudo", "-n", "systemctl", "stop", "dau-deadman.timer", "dau-deadman.service")
    assert reset == ("sudo", "-n", "systemctl", "reset-failed", "dau-deadman.timer", "dau-deadman.service")


def test_status_command_lists_the_named_timer() -> None:
    assert status_command(unit="dau-deadman") == ("systemctl", "list-timers", "--all", "dau-deadman.timer")


def test_cli_arm_dry_run_prints_the_scheduled_reset_command_shell_quoted(capsys) -> None:
    exit_code = main(["arm", "--timeout", "90", "--dry-run"])

    assert exit_code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == shlex.join(arm_command(90, unit=DEFAULT_UNIT))
    assert shlex.split(printed) == list(arm_command(90, unit=DEFAULT_UNIT))  # the reset script survives the quoting


def test_cli_disarm_dry_run_prints_both_teardown_commands(capsys) -> None:
    exit_code = main(["disarm", "--dry-run"])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [shlex.join(command) for command in disarm_commands(unit=DEFAULT_UNIT)]


def test_cli_arm_dry_run_defaults_to_the_module_timeout(capsys) -> None:
    main(["arm", "--dry-run"])

    assert f"--on-active={DEFAULT_TIMEOUT_S}" in capsys.readouterr().out


def test_cli_refuses_flags_that_do_not_belong_to_the_action() -> None:
    with pytest.raises(SystemExit):
        main(["disarm", "--timeout", "999"])
    with pytest.raises(SystemExit):
        main(["status", "--dry-run"])


class _FakeSystemctl:
    """Stands in for subprocess.run: is-active returns a scripted state, a
    systemd-run marks the timer active, and every call is recorded with the
    timeout it was given."""

    def __init__(self, active_states: dict[str, str], *, arm_takes: bool = True) -> None:
        self.active_states = dict(active_states)
        self.arm_takes = arm_takes
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float | None] = []

    def __call__(self, command, check=False, capture_output=False, text=False, timeout=None):
        command = tuple(command)
        self.calls.append(command)
        self.timeouts.append(timeout)
        if command[:2] == ("systemctl", "is-active"):
            state = self.active_states.get(command[2], "inactive")
            return subprocess.CompletedProcess(command, 0, stdout=f"{state}\n", stderr="")
        if command[:3] == ("sudo", "-n", "systemd-run") and self.arm_takes:
            unit = command[3].removeprefix("--unit=")
            self.active_states[f"{unit}.timer"] = "active"
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_arm_refuses_to_replace_an_already_armed_timer(monkeypatch) -> None:
    fake = _FakeSystemctl({f"{DEFAULT_UNIT}.timer": "active"})
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    with pytest.raises(DeadmanError, match="already armed"):
        arm(120)

    assert not any(call[:3] == ("sudo", "-n", "systemd-run") for call in fake.calls)


def test_arm_schedules_when_no_timer_is_live_and_confirms_it(monkeypatch) -> None:
    fake = _FakeSystemctl({})  # everything inactive until systemd-run
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    arm(120)

    assert any(call[:3] == ("sudo", "-n", "systemd-run") for call in fake.calls)
    assert fake.calls[-1][:2] == ("systemctl", "is-active")  # confirmed after scheduling
    assert all(timeout == COMMAND_TIMEOUT_S for timeout in fake.timeouts)  # every call is bounded


def test_arm_reports_an_unprotected_host_when_the_timer_does_not_come_up(monkeypatch) -> None:
    fake = _FakeSystemctl({}, arm_takes=False)
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    with pytest.raises(DeadmanError, match="NOT protected"):
        arm(120)


def test_a_missing_or_hanging_systemd_is_a_deadman_error(monkeypatch) -> None:
    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(deadman.subprocess, "run", missing)
    with pytest.raises(DeadmanError, match="not installed"):
        deadman._run(("systemd-run",), check=True)

    def hangs(command, **kwargs):
        raise subprocess.TimeoutExpired(command, float(kwargs.get("timeout") or 0))

    monkeypatch.setattr(deadman.subprocess, "run", hangs)
    with pytest.raises(DeadmanError, match="did not answer"):
        deadman._run(("systemctl", "is-active", "x"), check=False)
    # and a query that cannot be answered counts as armed
    assert is_armed() is True


def test_disarm_raises_when_the_timer_survives_the_stop(monkeypatch) -> None:
    fake = _FakeSystemctl({f"{DEFAULT_UNIT}.timer": "active"})  # stop is a no-op here
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    with pytest.raises(DeadmanError, match="still armed after stop"):
        disarm()


def test_disarm_succeeds_only_once_the_timer_is_confirmed_inactive(monkeypatch) -> None:
    fake = _FakeSystemctl({})  # is-active reports inactive
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    disarm()  # no raise

    stop, reset = disarm_commands(unit=DEFAULT_UNIT)
    assert stop in fake.calls and reset in fake.calls  # execution runs the commands the dry run prints


def test_is_armed_treats_a_failed_query_as_still_armed(monkeypatch) -> None:
    fake = _FakeSystemctl({f"{DEFAULT_UNIT}.timer": "activating"})
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    assert is_armed() is True


def test_cli_disarm_reports_failure_when_timer_cannot_be_confirmed_gone(monkeypatch, capsys) -> None:
    fake = _FakeSystemctl({f"{DEFAULT_UNIT}.timer": "active"})
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    exit_code = main(["disarm"])

    assert exit_code == 1
    assert "DISARM FAILED" in capsys.readouterr().err


def test_cli_status_returns_systemctls_exit_code(monkeypatch, capsys) -> None:
    def failing(command, **kwargs):
        return subprocess.CompletedProcess(tuple(command), 3, stdout="", stderr="Failed to connect to bus\n")

    monkeypatch.setattr(deadman.subprocess, "run", failing)
    assert main(["status"]) == 3
    assert "Failed to connect" in capsys.readouterr().err


def test_module_entrypoint_runs_cli_for_uninstalled_checkout(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["deadman", "arm", "--timeout", "42", "--dry-run"])
    monkeypatch.delitem(sys.modules, "dau_utils.deadman", raising=False)

    try:
        runpy.run_module("dau_utils.deadman", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0

    assert "--on-active=42" in capsys.readouterr().out
