# dau utils

Host-side operational utilities for DAU bench work

[![Build Status](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-utils/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-utils)
[![License](https://img.shields.io/github/license/dau-dev/dau-utils)](https://github.com/dau-dev/dau-utils)
[![PyPI](https://img.shields.io/pypi/v/dau-utils.svg)](https://pypi.python.org/pypi/dau-utils)

## Overview

Small, package-neutral utilities for operating FPGA bench hosts. Each owns
one mechanism; policy (which devices, when to arm) belongs to the caller —
higher layers such as `dau-build` compose these into their hardware
command plans.

## Deadman: survive a wedged PCIe device

A driver/PCIe hang can leave the kernel and systemd alive — the hardware
watchdog keeps getting petted, yet a clean `systemctl reboot` blocks on
the stuck device, and the box needs a walk to the power switch. The
deadman arms a forced reboot *before* the risky operation and cancels it
on success:

```bash
dau-utils-deadman arm --timeout 300     # transient systemd timer; survives SSH loss
# ... rescan / flash / probe ...
dau-utils-deadman disarm                # clean return: cancel the reset
dau-utils-deadman status
```

If the host wedges before `disarm`, the timer fires `sysrq-b`
(`emergency_restart()`), which resets past the wedged driver without
touching it. The same operations are available as a Python API
(`dau_utils.deadman.arm/disarm/is_armed` and an `armed()` context
manager); failures raise `DeadmanError` — treat the host as unsafe.

Arm it before every PCIe rescan, flash, or register probe against a device
that has previously wedged. Synthesis and other non-PCIe work does not
need it.

## Runtime PM: hold and release PCIe power management

`dau-utils-pci-runtime-pm` holds or releases Linux PCI runtime PM for
devices matched from `lspci -Dnn`. The caller decides which patterns
matter; this utility owns the sysfs writes:

```bash
dau-utils-pci-runtime-pm hold    --pattern Thunderbolt --pattern 10ee:7011
dau-utils-pci-runtime-pm release --pattern Thunderbolt --pattern 10ee:7011
```

Use `--dry-run` to print the planned writes without touching sysfs.

## Development

```bash
pip install -e .[develop]
python -m pytest dau_utils/tests
python -m ruff check dau_utils
```

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
