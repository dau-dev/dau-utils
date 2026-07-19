from __future__ import annotations

import runpy
import sys
from pathlib import Path

from dau_utils.pci_runtime_pm import discover_pci_devices, main, plan_runtime_pm_writes

LSPCI_OUTPUT = """
0000:00:07.0 PCI bridge [0604]: Intel Corporation Raptor Lake-P Thunderbolt 4 PCI Express Root Port #0 [8086:a76e]
0000:00:0d.2 USB controller [0c03]: Intel Corporation Raptor Lake-P Thunderbolt 4 NHI #0 [8086:a73e]
0000:02:00.0 PCI bridge [0604]: Intel Corporation JHL6240 Thunderbolt 3 Bridge (Low Power) [8086:15c0] (rev 01)
0000:04:00.0 Processing accelerators [1200]: Xilinx Corporation 7-Series FPGA Hard PCIe block (AXI/debug) [10ee:7011]
"""


def test_discovers_devices_matching_explicit_patterns() -> None:
    assert discover_pci_devices(LSPCI_OUTPUT, patterns=("Thunderbolt", "JHL", "10ee:7011")) == (
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


def test_cli_dry_run_prints_hold_writes_for_explicit_device(capsys) -> None:
    exit_code = main(["hold", "--device", "0000:04:00.0", "--dry-run"])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "write /sys/bus/pci/devices/0000:04:00.0/power/control on",
        "write /sys/bus/pci/devices/0000:04:00.0/d3cold_allowed 0",
    ]


def test_cli_dry_run_can_discover_devices_from_lspci_fixture(capsys) -> None:
    exit_code = main(
        [
            "release",
            "--dry-run",
            "--pattern",
            "Thunderbolt",
            "--pattern",
            "JHL",
            "--pattern",
            "10ee:7011",
            "--lspci-output",
            LSPCI_OUTPUT,
        ]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "write /sys/bus/pci/devices/0000:00:07.0/power/control auto"
    assert lines[-1] == "write /sys/bus/pci/devices/0000:04:00.0/d3cold_allowed 1"


def test_cli_without_patterns_matches_no_devices(capsys) -> None:
    exit_code = main(["hold"])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_cli_missing_sysfs_paths_surface_skips_and_fail(tmp_path, capsys) -> None:
    exit_code = main(["hold", "--device", "0000:04:00.0", "--sysfs-root", str(tmp_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    err_lines = captured.err.splitlines()
    assert err_lines == [
        f"skipped {tmp_path}/0000:04:00.0/power/control (missing)",
        f"skipped {tmp_path}/0000:04:00.0/d3cold_allowed (missing)",
        "applied 0 of 2 runtime PM writes",
    ]


def test_cli_partial_apply_reports_skip_and_fails(tmp_path, capsys) -> None:
    device_root = tmp_path / "0000:04:00.0"
    (device_root / "power").mkdir(parents=True)
    control = device_root / "power" / "control"
    control.write_text("auto\n")
    # d3cold_allowed intentionally absent

    exit_code = main(["hold", "--device", "0000:04:00.0", "--sysfs-root", str(tmp_path)])

    assert exit_code == 1
    assert control.read_text() == "on\n"
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f"wrote {control} on"]
    assert captured.err.splitlines() == [
        f"skipped {device_root}/d3cold_allowed (missing)",
        "applied 1 of 2 runtime PM writes",
    ]


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
