from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from dau_utils import pci_runtime_pm
from dau_utils.pci_runtime_pm import RuntimePmError, RuntimePmWrite, apply_runtime_pm_writes, discover_pci_devices, main, plan_runtime_pm_writes

# synthetic lspci -Dnn text: a root port, a bridge behind it, and an accelerator behind that
LSPCI_OUTPUT = """
0000:00:07.0 PCI bridge [0604]: Example Silicon Root Port [1234:0001]
0000:00:0d.2 USB controller [0c03]: Example Silicon Host Controller [1234:0002]
0000:02:00.0 PCI bridge [0604]: Example Silicon Downstream Bridge [1234:0003] (rev 01)
0000:04:00.0 Processing accelerators [1200]: Example Accelerator Co. FPGA card [abcd:ef01]
"""


def test_discovers_devices_matching_explicit_patterns() -> None:
    assert discover_pci_devices(LSPCI_OUTPUT, patterns=("Root Port", "Host Controller", "Bridge", "abcd:ef01")) == (
        "0000:00:07.0",
        "0000:00:0d.2",
        "0000:02:00.0",
        "0000:04:00.0",
    )


def test_no_patterns_discovers_no_devices() -> None:
    assert discover_pci_devices(LSPCI_OUTPUT) == ()
    assert discover_pci_devices(LSPCI_OUTPUT, patterns=()) == ()


def test_runtime_pm_write_plan_maps_hold_and_release_to_sysfs_knobs() -> None:
    root = Path("/sys/bus/pci/devices")

    hold_writes = plan_runtime_pm_writes("hold", ("0000:04:00.0",), sysfs_root=root)
    release_writes = plan_runtime_pm_writes("release", ("0000:04:00.0",), sysfs_root=root)

    assert [(write.path, write.value) for write in hold_writes] == [
        (root / "0000:04:00.0" / "power" / "control", "on"),
        (root / "0000:04:00.0" / "d3cold_allowed", "0"),
    ]
    assert [(write.path, write.value) for write in release_writes] == [
        (root / "0000:04:00.0" / "power" / "control", "auto"),
        (root / "0000:04:00.0" / "d3cold_allowed", "1"),
    ]


def test_a_device_that_is_not_a_pci_address_cannot_leave_the_sysfs_root() -> None:
    """The only confinement sysfs allows (its device entries are symlinks, so a
    resolved-path check would fail on a real host) is the name itself."""
    for bad in ("../../../etc", "/etc/passwd", "0000:04:00", "0000:04:00.9", "xdma0", ""):
        with pytest.raises(RuntimePmError, match="PCI address"):
            plan_runtime_pm_writes("hold", (bad,))
    with pytest.raises(RuntimePmError, match="PCI address"):
        discover_pci_devices("../escape PCI bridge [0604]: thing", patterns=("thing",))
    with pytest.raises(RuntimePmError, match="value must be one of"):
        RuntimePmWrite(Path("/sys/x"), "rm -rf")


def test_cli_dry_run_prints_hold_writes_for_explicit_device(capsys) -> None:
    exit_code = main(["hold", "--device", "0000:04:00.0", "--dry-run"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "write /sys/bus/pci/devices/0000:04:00.0/power/control on",
        "write /sys/bus/pci/devices/0000:04:00.0/d3cold_allowed 0",
    ]


def test_cli_dry_run_can_discover_devices_from_lspci_fixture(capsys) -> None:
    exit_code = main(
        ["release", "--dry-run", "--pattern", "Root Port", "--pattern", "Bridge", "--pattern", "abcd:ef01", "--lspci-output", LSPCI_OUTPUT]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "write /sys/bus/pci/devices/0000:00:07.0/power/control auto"
    assert lines[-1] == "write /sys/bus/pci/devices/0000:04:00.0/d3cold_allowed 1"


def test_cli_refuses_an_ambiguous_or_empty_selection() -> None:
    with pytest.raises(SystemExit):
        main(["hold"])  # nothing selected
    with pytest.raises(SystemExit):
        main(["hold", "--device", "0000:04:00.0", "--pattern", "Bridge", "--dry-run"])  # both selectors
    with pytest.raises(SystemExit):
        main(["hold", "--device", "0000:04:00.0", "--lspci-output", LSPCI_OUTPUT, "--dry-run"])  # lspci text without a pattern


def test_cli_rejects_a_device_that_is_not_a_pci_address(capsys) -> None:
    assert main(["hold", "--device", "../../etc", "--dry-run"]) == 1
    assert "PCI address" in capsys.readouterr().err


def test_cli_says_so_when_no_device_matches(capsys) -> None:
    """Zero matches are tolerated (the device being recovered may not
    enumerate), and said out loud rather than passed silently."""
    exit_code = main(["hold", "--pattern", "Nonesuch", "--lspci-output", LSPCI_OUTPUT])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no device matched Nonesuch; nothing to write" in captured.err


def test_cli_missing_sysfs_paths_refuse_the_whole_plan(tmp_path, capsys) -> None:
    exit_code = main(["hold", "--device", "0000:04:00.0", "--sysfs-root", str(tmp_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        f"missing {tmp_path}/0000:04:00.0/power/control",
        f"missing {tmp_path}/0000:04:00.0/d3cold_allowed",
        "applied 0 of 2 runtime PM writes: the plan is refused whole",
    ]


def test_cli_writes_nothing_when_one_attribute_is_missing(tmp_path, capsys) -> None:
    """A device half held is the state the tool exists to avoid; the plan is
    checked before the first write."""
    device_root = tmp_path / "0000:04:00.0"
    (device_root / "power").mkdir(parents=True)
    control = device_root / "power" / "control"
    control.write_text("auto\n")
    # d3cold_allowed intentionally absent

    exit_code = main(["hold", "--device", "0000:04:00.0", "--sysfs-root", str(tmp_path)])

    assert exit_code == 1
    assert control.read_text() == "auto\n"  # untouched
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        f"missing {device_root}/d3cold_allowed",
        "applied 0 of 2 runtime PM writes: the plan is refused whole",
    ]
    with pytest.raises(RuntimePmError, match="missing sysfs attributes"):
        apply_runtime_pm_writes(plan_runtime_pm_writes("hold", ("0000:04:00.0",), sysfs_root=tmp_path))


def test_cli_applies_present_sysfs_paths(tmp_path, capsys) -> None:
    device_root = tmp_path / "0000:04:00.0"
    (device_root / "power").mkdir(parents=True)
    control = device_root / "power" / "control"
    d3cold = device_root / "d3cold_allowed"
    control.write_text("auto\n")
    d3cold.write_text("1\n")

    exit_code = main(["hold", "--device", "0000:04:00.0", "--sysfs-root", str(tmp_path)])

    assert exit_code == 0
    assert control.read_text() == "on\n"
    assert d3cold.read_text() == "0\n"
    assert capsys.readouterr().err == ""


def test_lspci_is_bounded_and_its_absence_is_reported(monkeypatch, capsys) -> None:
    seen = {}

    def fake_run(command, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(tuple(command), 0, stdout=LSPCI_OUTPUT)

    monkeypatch.setattr(pci_runtime_pm.subprocess, "run", fake_run)
    assert main(["hold", "--pattern", "abcd:ef01", "--dry-run"]) == 0
    assert seen["timeout"] == pci_runtime_pm.LSPCI_TIMEOUT_S

    def missing(command, **kwargs):
        raise FileNotFoundError("lspci")

    monkeypatch.setattr(pci_runtime_pm.subprocess, "run", missing)
    assert main(["hold", "--pattern", "abcd:ef01", "--dry-run"]) == 1
    assert "pciutils" in capsys.readouterr().err


def test_module_entrypoint_runs_cli_for_uninstalled_checkout(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pci_runtime_pm", "hold", "--device", "0000:04:00.0", "--dry-run"])
    monkeypatch.delitem(sys.modules, "dau_utils.pci_runtime_pm", raising=False)

    try:
        runpy.run_module("dau_utils.pci_runtime_pm", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.splitlines() == [
        "write /sys/bus/pci/devices/0000:04:00.0/power/control on",
        "write /sys/bus/pci/devices/0000:04:00.0/d3cold_allowed 0",
    ]
