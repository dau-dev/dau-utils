"""Hold or release Linux PCI runtime power management for named devices.

A device is named by its PCI address (``dddd:bb:dd.f``) or discovered from
``lspci -Dnn`` by text patterns, never both at once. The writes go to the
device's ``power/control`` and ``d3cold_allowed`` attributes under the sysfs
device root; because every device name is a PCI address, no name can leave
that root, and the whole plan is checked before the first write so a missing
attribute leaves nothing half applied.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SYSFS_ROOT = Path("/sys/bus/pci/devices")
# lspci answers at once; a longer wait is a wedged bus, which is what the
# caller is about to protect against, not something to hang on here
LSPCI_TIMEOUT_S = 15

# the sysfs names are PCI addresses; the attribute values are the two states
# of each knob. Nothing else is ever written.
_BDF = re.compile(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]")
_VALUES = frozenset({"on", "auto", "0", "1"})


class RuntimePmError(RuntimeError):
    """The plan could not be made or applied as asked."""


def check_device(device: str) -> str:
    """The device, if it is a PCI address; the only form that stays inside the sysfs root."""
    if not _BDF.fullmatch(device):
        raise RuntimePmError(f"device must be a PCI address (dddd:bb:dd.f), got {device!r}")
    return device


@dataclass(frozen=True)
class RuntimePmWrite:
    path: Path
    value: str

    def __post_init__(self) -> None:
        if self.value not in _VALUES:
            raise RuntimePmError(f"runtime PM write value must be one of {sorted(_VALUES)}, got {self.value!r}")


def discover_pci_devices(lspci_output: str, *, patterns: Sequence[str] = ()) -> tuple[str, ...]:
    matches: list[str] = []
    for line in lspci_output.splitlines():
        if any(pattern in line for pattern in patterns):
            parts = line.split(maxsplit=1)
            if parts:
                matches.append(check_device(parts[0]))
    return tuple(dict.fromkeys(matches))


def plan_runtime_pm_writes(mode: str, devices: Sequence[str], *, sysfs_root: Path = DEFAULT_SYSFS_ROOT) -> tuple[RuntimePmWrite, ...]:
    control, d3cold_allowed = _mode_values(mode)
    writes: list[RuntimePmWrite] = []
    for device in devices:
        device_root = sysfs_root / check_device(device)
        writes.extend(
            (
                RuntimePmWrite(device_root / "power" / "control", control),
                RuntimePmWrite(device_root / "d3cold_allowed", d3cold_allowed),
            )
        )
    return tuple(writes)


def missing_targets(writes: Sequence[RuntimePmWrite]) -> tuple[RuntimePmWrite, ...]:
    """The writes whose attribute does not exist; checked before anything is written."""
    return tuple(write for write in writes if not write.path.exists())


def apply_runtime_pm_writes(writes: Sequence[RuntimePmWrite]) -> tuple[RuntimePmWrite, ...]:
    """Apply every write, or none: a missing attribute refuses the whole plan
    so a device is never left half held or half released."""
    missing = missing_targets(writes)
    if missing:
        raise RuntimePmError("missing sysfs attributes: " + ", ".join(str(write.path) for write in missing))
    for write in writes:
        write.path.write_text(f"{write.value}\n")
    return tuple(writes)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hold or release Linux PCI runtime PM for matching devices")
    parser.add_argument("mode", choices=("hold", "release"), help="runtime PM mode to apply")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--device", action="append", default=[], help="PCI address to update (dddd:bb:dd.f); may be repeated")
    selector.add_argument("--pattern", action="append", default=[], help="lspci text pattern to discover devices by; may be repeated")
    parser.add_argument("--sysfs-root", type=Path, default=DEFAULT_SYSFS_ROOT, help="PCI sysfs device root")
    parser.add_argument("--lspci-output", help="lspci -Dnn text to discover from instead of running lspci (needs --pattern)")
    parser.add_argument("--dry-run", action="store_true", help="print the writes without applying them")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.lspci_output is not None and not args.pattern:
        parser.error("--lspci-output only makes sense with --pattern")

    try:
        if args.device:
            devices: tuple[str, ...] = tuple(check_device(device) for device in args.device)
        else:
            lspci_output = args.lspci_output if args.lspci_output is not None else _lspci_output()
            devices = discover_pci_devices(lspci_output, patterns=tuple(args.pattern))
            if not devices:
                # tolerated on purpose: a device that no longer enumerates is the
                # one being recovered, and there is nothing to hold for it
                print(f"no device matched {shlex.join(args.pattern)}; nothing to write", file=sys.stderr)
        writes = plan_runtime_pm_writes(args.mode, devices, sysfs_root=args.sysfs_root)
    except RuntimePmError as error:
        print(f"runtime PM: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        for write in writes:
            print(f"write {write.path} {write.value}")
        return 0

    missing = missing_targets(writes)
    if missing:
        for write in missing:
            print(f"missing {write.path}", file=sys.stderr)
        print(f"applied 0 of {len(writes)} runtime PM writes: the plan is refused whole", file=sys.stderr)
        return 1
    for write in apply_runtime_pm_writes(writes):
        print(f"wrote {write.path} {write.value}")
    return 0


def _mode_values(mode: str) -> tuple[str, str]:
    if mode == "hold":
        return "on", "0"
    if mode == "release":
        return "auto", "1"
    raise RuntimePmError(f"unknown runtime PM mode: {mode}")


def _lspci_output() -> str:
    try:
        return subprocess.run(("lspci", "-Dnn"), check=True, text=True, stdout=subprocess.PIPE, timeout=LSPCI_TIMEOUT_S).stdout
    except FileNotFoundError as error:
        raise RuntimePmError("lspci is not installed (pciutils)") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimePmError(f"lspci did not answer within {LSPCI_TIMEOUT_S}s") from error
    except subprocess.CalledProcessError as error:
        raise RuntimePmError(f"lspci failed with exit {error.returncode}") from error


if __name__ == "__main__":
    raise SystemExit(main())
