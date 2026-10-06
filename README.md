# dau utils

Host utilities for FPGA bench machines

[![Build Status](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-utils/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-utils)
[![License](https://img.shields.io/github/license/dau-dev/dau-utils)](https://github.com/dau-dev/dau-utils)
[![PyPI](https://img.shields.io/pypi/v/dau-utils.svg)](https://pypi.python.org/pypi/dau-utils)

## Overview

Two small command-line tools for the Linux machine an FPGA card is plugged
into. Each one does a single thing and leaves the decisions (which devices,
when to arm) to the caller. Higher layers such as `dau-build` run them
from their hardware command plans; you can also run them by hand.

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

If the host hangs before `disarm`, the timer fires `sysrq-b`
(`emergency_restart()`), which resets the machine without going through the
stuck driver. The same operations are available from Python:
`dau_utils.deadman.arm`, `disarm`, `is_armed`, and an `armed()` context
manager. Failures raise `DeadmanError`; if you see one, assume the host is
not protected.

Arm it before every PCIe rescan, flash or register probe against a device
that has hung before. Synthesis and other work that does not touch PCIe does
not need it.

## Runtime PM: hold and release PCIe power management

`dau-utils-pci-runtime-pm` holds or releases Linux PCI runtime power
management for devices matched against `lspci -Dnn`. You choose the patterns;
the tool does the sysfs writes:

```bash
dau-utils-pci-runtime-pm hold    --pattern Thunderbolt --pattern 10ee:7011
dau-utils-pci-runtime-pm release --pattern Thunderbolt --pattern 10ee:7011
```

`--dry-run` prints the writes it would make without touching sysfs.

## Development

```bash
pip install -e .[develop]
python -m pytest dau_utils/tests
python -m ruff check dau_utils
```

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
