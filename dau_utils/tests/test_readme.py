"""Every command the README shows runs as written, in its unprivileged dry-run
form. The README is the first thing a bench operator reads; a command that
does not run there is a claim the repository cannot back."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from dau_utils import deadman, pci_runtime_pm

README = Path(__file__).resolve().parents[2] / "README.md"
BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
TOOLS = {"dau-utils-deadman": deadman.main, "dau-utils-pci-runtime-pm": pci_runtime_pm.main}


def _documented_commands() -> list[str]:
    found = []
    for block in BLOCK.findall(README.read_text(encoding="utf-8")):
        for line in block.splitlines():
            argv = shlex.split(line.split("#", 1)[0])
            if argv and argv[0] == "sudo":
                argv = argv[1:]
            if argv and argv[0] in TOOLS:
                found.append(shlex.join(argv))
    return found


@pytest.mark.parametrize("command", _documented_commands())
def test_a_documented_command_runs_as_a_dry_run(command: str, monkeypatch, capsys) -> None:
    argv = shlex.split(command)
    tool, args = TOOLS[argv[0]], argv[1:]
    if args[0] == "status":
        # status queries systemd; prove it composes and exits with systemctl's code
        import subprocess

        monkeypatch.setattr(deadman.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(tuple(c), 0, stdout="", stderr=""))
    elif "--pattern" in args:
        monkeypatch.setattr(
            pci_runtime_pm, "_lspci_output", lambda: "0000:04:00.0 Processing accelerators [1200]: Example Downstream Bridge FPGA card [abcd:ef01]\n"
        )
        args = [*args, "--dry-run"]
    else:
        args = [*args, "--dry-run"]
    assert tool(args) == 0, command
