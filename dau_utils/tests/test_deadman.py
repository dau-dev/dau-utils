from __future__ import annotations

import runpy
import subprocess
import sys

import pytest

from dau_utils import deadman
from dau_utils.deadman import (
    DEFAULT_TIMEOUT_S,
    DEFAULT_UNIT,
    DeadmanError,
    arm,
    arm_command,
    disarm,
    disarm_commands,
    is_armed,
    main,
    status_command,
)


def test_arm_command_schedules_a_transient_sysrq_reset_timer() -> None:
    command = arm_command(120, unit="dau-deadman")

    assert command[:6] == (
        "sudo",
        "systemd-run",
        "--unit=dau-deadman",
        "--on-active=120",
        "--timer-property=AccuracySec=1s",
        "--collect",
    )
    assert command[6:8] == ("/bin/sh", "-c")
    assert "/proc/sysrq-trigger" in command[8]
    assert command[8].strip().endswith("echo b > /proc/sysrq-trigger")


def test_arm_command_rejects_a_nonpositive_timeout() -> None:
    for bad in (0, -5):
        try:
            arm_command(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for timeout {bad}")


def test_disarm_stops_the_timer_and_service_then_clears_failed_state() -> None:
    stop, reset = disarm_commands(unit="dau-deadman")

    assert stop == ("sudo", "systemctl", "stop", "dau-deadman.timer", "dau-deadman.service")
    assert reset == ("sudo", "systemctl", "reset-failed", "dau-deadman.timer", "dau-deadman.service")


def test_status_command_lists_the_named_timer() -> None:
    assert status_command(unit="dau-deadman") == ("systemctl", "list-timers", "--all", "dau-deadman.timer")


def test_cli_arm_dry_run_prints_the_scheduled_reset_command(capsys) -> None:
    exit_code = main(["arm", "--timeout", "90", "--dry-run"])

    assert exit_code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == " ".join(arm_command(90, unit=DEFAULT_UNIT))


def test_cli_disarm_dry_run_prints_both_teardown_commands(capsys) -> None:
    exit_code = main(["disarm", "--dry-run"])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [" ".join(command) for command in disarm_commands(unit=DEFAULT_UNIT)]


def test_cli_arm_dry_run_defaults_to_the_module_timeout(capsys) -> None:
    main(["arm", "--dry-run"])

    assert f"--on-active={DEFAULT_TIMEOUT_S}" in capsys.readouterr().out


class _FakeSystemctl:
    """Stands in for subprocess.run: is-active returns a scripted state, and
    every mutating call (sudo/systemd-run/stop) is recorded."""

    def __init__(self, active_states: dict[str, str]) -> None:
        self.active_states = active_states
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command, check=False, capture_output=False, text=False):  # noqa: ANN001
        command = tuple(command)
        self.calls.append(command)
        if command[:2] == ("systemctl", "is-active"):
            state = self.active_states.get(command[2], "inactive")
            return subprocess.CompletedProcess(command, 0, stdout=f"{state}\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_arm_refuses_to_replace_an_already_armed_timer(monkeypatch) -> None:
    fake = _FakeSystemctl({f"{DEFAULT_UNIT}.timer": "active"})
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    with pytest.raises(DeadmanError, match="already armed"):
        arm(120)

    assert not any(call[:2] == ("sudo", "systemd-run") for call in fake.calls)


def test_arm_schedules_when_no_timer_is_live(monkeypatch) -> None:
    fake = _FakeSystemctl({})  # everything inactive
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    arm(120)

    assert any(call[:2] == ("sudo", "systemd-run") for call in fake.calls)


def test_disarm_raises_when_the_timer_survives_the_stop(monkeypatch) -> None:
    fake = _FakeSystemctl({f"{DEFAULT_UNIT}.timer": "active"})  # stop is a no-op here
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    with pytest.raises(DeadmanError, match="still armed after stop"):
        disarm()


def test_disarm_succeeds_only_once_the_timer_is_confirmed_inactive(monkeypatch) -> None:
    fake = _FakeSystemctl({})  # is-active reports inactive
    monkeypatch.setattr(deadman.subprocess, "run", fake)

    disarm()  # no raise

    assert ("sudo", "systemctl", "stop", f"{DEFAULT_UNIT}.timer", f"{DEFAULT_UNIT}.service") in fake.calls


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


def test_module_entrypoint_runs_cli_for_uninstalled_checkout(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["deadman", "arm", "--timeout", "42", "--dry-run"])
    monkeypatch.delitem(sys.modules, "dau_utils.deadman", raising=False)

    try:
        runpy.run_module("dau_utils.deadman", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0

    assert "--on-active=42" in capsys.readouterr().out
