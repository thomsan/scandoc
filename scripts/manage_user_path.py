"""Safely add or remove one directory from the Windows user PATH."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


parser = argparse.ArgumentParser()
parser.add_argument("action", choices=("add", "remove"))
parser.add_argument("directory")
args = parser.parse_args()

directory = os.path.normpath(str(Path(args.directory).resolve()))
target = powershell_literal(directory)

common = (
    f"$target = {target}; "
    "$current = [Environment]::GetEnvironmentVariable('Path', 'User'); "
    "$parts = @($current -split ';' | Where-Object { $_ }); "
)

if args.action == "add":
    command = (
        common
        + "$present = @($parts | Where-Object { "
        "[Environment]::ExpandEnvironmentVariables($_).TrimEnd('\\') "
        "-ieq $target.TrimEnd('\\') }); "
        "if ($present.Count -eq 0) { $parts += $target }; "
        "[Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User'); "
        "Write-Host ('User PATH includes ' + $target + "
        "'; open a new shell to use scandoc.')"
    )
else:
    command = (
        common
        + "$parts = @($parts | Where-Object { "
        "[Environment]::ExpandEnvironmentVariables($_).TrimEnd('\\') "
        "-ine $target.TrimEnd('\\') }); "
        "[Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User'); "
        "Write-Host ('Removed ' + $target + ' from the user PATH.')"
    )

subprocess.check_call(["powershell.exe", "-NoProfile", "-Command", command])
