from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DEVICE_PATTERNS = ("Thunderbolt", "JHL", "10ee:7011", "Xilinx")
DEFAULT_SYSFS_ROOT = Path("/sys/bus/pci/devices")


@dataclass(frozen=True)
class RuntimePmWrite:
    path: Path
    value: str


def discover_pci_devices(lspci_output: str, *, patterns: Sequence[str] = DEFAULT_DEVICE_PATTERNS) -> tuple[str, ...]:
    matches: list[str] = []
    for line in lspci_output.splitlines():
        if any(pattern in line for pattern in patterns):
            parts = line.split(maxsplit=1)
            if parts:
                matches.append(parts[0])
    return tuple(dict.fromkeys(matches))


def plan_runtime_pm_writes(mode: str, devices: Sequence[str], *, sysfs_root: Path = DEFAULT_SYSFS_ROOT) -> tuple[RuntimePmWrite, ...]:
    control, d3cold_allowed = _mode_values(mode)
    writes: list[RuntimePmWrite] = []
    for device in devices:
        device_root = sysfs_root / device
        writes.extend(
            (
                RuntimePmWrite(device_root / "power" / "control", control),
                RuntimePmWrite(device_root / "d3cold_allowed", d3cold_allowed),
            )
        )
    return tuple(writes)


def apply_runtime_pm_writes(writes: Sequence[RuntimePmWrite]) -> tuple[RuntimePmWrite, ...]:
    applied: list[RuntimePmWrite] = []
    for write in writes:
        if write.path.exists() and write.path.parent.exists():
            write.path.write_text(f"{write.value}\n")
            applied.append(write)
    return tuple(applied)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hold or release Linux PCI runtime PM for matching devices")
    parser.add_argument("mode", choices=("hold", "release"), help="Runtime PM mode to apply")
    parser.add_argument("--device", action="append", default=[], help="PCI BDF to update; may be provided more than once")
    parser.add_argument("--pattern", action="append", default=[], help="lspci text pattern to discover; may be provided more than once")
    parser.add_argument("--sysfs-root", type=Path, default=DEFAULT_SYSFS_ROOT, help="PCI sysfs device root")
    parser.add_argument("--lspci-output", help="Use provided lspci output instead of running lspci -Dnn")
    parser.add_argument("--dry-run", action="store_true", help="Print writes without applying them")
    args = parser.parse_args(argv)

    patterns = tuple(args.pattern) if args.pattern else DEFAULT_DEVICE_PATTERNS
    devices = tuple(args.device) or discover_pci_devices(args.lspci_output if args.lspci_output is not None else _lspci_output(), patterns=patterns)
    writes = plan_runtime_pm_writes(args.mode, devices, sysfs_root=args.sysfs_root)

    if args.dry_run:
        for write in writes:
            print(f"write {write.path} {write.value}")
        return 0

    for write in apply_runtime_pm_writes(writes):
        print(f"wrote {write.path} {write.value}")
    return 0


def _mode_values(mode: str) -> tuple[str, str]:
    if mode == "hold":
        return "on", "0"
    if mode == "release":
        return "auto", "1"
    raise ValueError(f"unknown runtime PM mode: {mode}")


def _lspci_output() -> str:
    return subprocess.run(("lspci", "-Dnn"), check=True, text=True, stdout=subprocess.PIPE).stdout


if __name__ == "__main__":
    raise SystemExit(main())
