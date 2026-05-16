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


def test_discovers_thunderbolt_and_xilinx_devices_from_lspci_output() -> None:
    assert discover_pci_devices(LSPCI_OUTPUT) == (
        "0000:00:07.0",
        "0000:00:0d.2",
        "0000:02:00.0",
        "0000:04:00.0",
    )


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
    exit_code = main(["release", "--dry-run", "--lspci-output", LSPCI_OUTPUT])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "write /sys/bus/pci/devices/0000:00:07.0/power/control auto"
    assert lines[-1] == "write /sys/bus/pci/devices/0000:04:00.0/d3cold_allowed 1"


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
