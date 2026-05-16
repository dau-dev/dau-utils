# dau utils

Utilities for dau

[![Build Status](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-utils/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-utils/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-utils)
[![License](https://img.shields.io/github/license/dau-dev/dau-utils)](https://github.com/dau-dev/dau-utils)
[![PyPI](https://img.shields.io/pypi/v/dau-utils.svg)](https://pypi.python.org/pypi/dau-utils)

## Overview

A collection of miscellaneous utilities for dau development.

### Runtime PM Helper

`dau-utils-pci-runtime-pm` holds or releases Linux PCI runtime PM for devices matched from `lspci -Dnn`. It is intentionally package-neutral: callers such as `dau-build` decide which device patterns matter, while this utility owns the sysfs writes.

```bash
dau-utils-pci-runtime-pm hold --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx
dau-utils-pci-runtime-pm release --pattern Thunderbolt --pattern JHL --pattern 10ee:7011 --pattern Xilinx
```

Use `--dry-run` to print planned writes without touching sysfs.

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
