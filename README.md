# dau utils

Host utilities for FPGA bench machines

[![Build Status](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-utils/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-utils)
[![License](https://img.shields.io/github/license/dau-dev/dau-utils)](https://github.com/dau-dev/dau-utils)

## Overview

Small command-line tools for the Linux machine an FPGA card is plugged into.
Each one does a single thing and leaves the decisions (which devices, when to
arm) to the caller. Higher layers such as `dau-build` run them from their
hardware command plans; you can also run them by hand. Both tools print what
they would do with `--dry-run`, which needs no privilege, and refuse an
ambiguous request rather than guessing.

## Requirements

- Linux with systemd (the deadman schedules a transient timer) and the
  `sysrq` trigger enabled for `b` (`kernel.sysrq` must allow it).
- `sudo` without a password prompt for the invoking user, for `systemd-run`
  and `systemctl`: the deadman runs them with `sudo -n` and treats a prompt
  as a failure. The runtime-PM tool writes sysfs directly and is run under
  `sudo` itself.
- `pciutils` (`lspci`) for pattern-based device discovery.
- Python 3.11 or later.

## Installation

There is no package index release yet. Install from a clone, or straight from
GitHub:

```bash
pip install git+https://github.com/dau-dev/dau-utils.git
```

For development, clone and run `make develop`.

## Deadman: recover from a hung PCIe device

A driver or PCIe hang can leave the kernel and systemd running while the
device itself is stuck. The hardware watchdog keeps getting fed, but a
`systemctl reboot` blocks on the hung device, and someone has to walk over
and pull the power. The deadman schedules a forced reboot before you do the
risky thing, and you cancel it once the operation comes back cleanly:

```bash
dau-utils-deadman arm --timeout 300     # transient systemd timer; outlasts a lost SSH session
# ... rescan / flash / probe ...
dau-utils-deadman disarm                # operation returned: cancel the reboot
dau-utils-deadman status
```

The timeout is a policy choice: long enough for the operation to finish,
short enough that a wedged host comes back without anyone present. The
default is 180 seconds.

If the host hangs before `disarm`, the timer fires `sysrq-b`
(`emergency_restart()`), which resets the machine without going through the
stuck driver. `arm` confirms the timer is active before it reports success,
and `disarm` confirms it is gone; a missing command, a failed `systemd-run`,
or a call that does not answer within fifteen seconds raises `DeadmanError`.
If you see one, assume the host is not protected, or not yet safe.

The unit name is the tool's own (`dau-deadman`, or `dau-deadman-<suffix>` to
run more than one); it will not stop or reset any other unit.

The same operations are available from Python: `dau_utils.deadman.arm`,
`disarm`, `is_armed`, and an `armed()` context manager. The context manager
disarms on the way out whatever the block did, including on an exception,
which is right for Python work. A hardware plan that fails a step must leave
the timer armed, so that the host reboots if the step wedged it; the plans in
`dau-build` call `arm` and `disarm` directly for that reason.

Arm it before every PCIe rescan, flash or register probe against a device
that has hung before. Synthesis and other work that does not touch PCIe does
not need it.

## Runtime PM: hold and release PCIe power management

`dau-utils-pci-runtime-pm` holds or releases Linux PCI runtime power
management for devices named by PCI address or matched against `lspci -Dnn`
by text pattern. You choose the devices; the tool writes `power/control` and
`d3cold_allowed` under the sysfs device root:

```bash
sudo dau-utils-pci-runtime-pm hold    --device 0000:04:00.0
sudo dau-utils-pci-runtime-pm hold    --pattern "Downstream Bridge" --pattern abcd:ef01
sudo dau-utils-pci-runtime-pm release --pattern "Downstream Bridge" --pattern abcd:ef01
```

A device must be a PCI address (`dddd:bb:dd.f`), which is the only form that
stays inside the sysfs root. `--device` and `--pattern` are alternatives, not
a combination. Every attribute in the plan is checked before the first write,
so a device is never left half held. A pattern that matches nothing is
tolerated, and said on stderr, because the device being recovered may not
enumerate.

`--dry-run` prints the writes without touching sysfs and needs no privilege.

## Development

```bash
make develop
python -m pytest dau_utils/tests
python -m ruff check dau_utils
```
