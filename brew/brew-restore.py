#!/usr/bin/env python3
"""Restore Homebrew state from a Brewfile."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path


BAR_WIDTH = 50


class ProgressBar:
    def __init__(self, label: str, total_steps: int) -> None:
        self.label = label
        self.total_steps = total_steps
        self.completed_steps = 0
        self.interactive = sys.stdout.isatty()
        self.active = False

    def draw(self, label: str | None = None) -> None:
        if label is not None:
            self.label = label

        percent = round((self.completed_steps / self.total_steps) * 100)
        filled = BAR_WIDTH if self.completed_steps >= self.total_steps else percent // 2
        bar = "█" * filled + "░" * (BAR_WIDTH - filled)
        line = f"{self.label:<28} [{bar}] {percent:3d}%"
        if self.interactive:
            print(f"\r{line}\033[K", end="", flush=True)
        else:
            print(line, flush=True)
        self.active = True

    def advance(self, label: str | None = None) -> None:
        self.completed_steps = min(self.completed_steps + 1, self.total_steps)
        self.draw(label)
        if self.completed_steps >= self.total_steps and self.interactive:
            print(flush=True)
        if self.completed_steps >= self.total_steps:
            self.active = False


def run(args: list[str], *, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    process_stdout = subprocess.PIPE if quiet else None
    process_stderr = subprocess.PIPE if quiet else None
    completed = subprocess.run(
        args,
        stdout=process_stdout,
        stderr=process_stderr,
        check=False,
        text=True,
    )

    if check and completed.returncode != 0:
        if quiet:
            print()
            if completed.stdout:
                sys.stdout.write(completed.stdout)
            if completed.stderr:
                sys.stderr.write(completed.stderr)
        raise subprocess.CalledProcessError(
            completed.returncode,
            args,
            output=completed.stdout,
            stderr=completed.stderr,
        )

    return completed


def short_hostname() -> str:
    try:
        hostname = subprocess.run(
            ["hostname", "-s"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        if hostname:
            return hostname
    except (OSError, subprocess.CalledProcessError):
        pass

    hostname = socket.gethostname()
    return hostname.split(".", 1)[0]


def default_brewfile() -> Path:
    return Path.home() / "git" / "toolbox" / "brew" / f"backups.{short_hostname()}" / "Brewfile"


def main() -> int:
    brewfile = Path(sys.argv[1]) if len(sys.argv) > 1 else default_brewfile()

    if shutil.which("brew") is None:
        print("Homebrew is not installed.")
        print("Install it from https://brew.sh, then rerun this script.")
        return 1

    if not brewfile.is_file():
        print(f"Missing Brewfile: {brewfile}")
        return 1

    print(f"Restoring Homebrew state from {brewfile}")

    progress = ProgressBar("Updating Homebrew", 3)
    progress.draw()
    run(["brew", "update"], quiet=True)
    progress.advance("Updated Homebrew")

    progress.draw("Checking Brewfile")
    check = run(
        ["brew", "bundle", "check", f"--file={brewfile}", "--verbose"],
        check=False,
        quiet=True,
    )
    progress.advance("Checked Brewfile")
    if check.returncode == 0:
        progress.advance("Already installed")
        print(f"Everything in {brewfile} is already installed.")
    else:
        progress.draw("Installing packages")
        run(["brew", "bundle", "install", f"--file={brewfile}", "--no-upgrade"], quiet=True)
        progress.advance("Installed packages")

    print("Restore complete.")
    print("Optional cleanup preview:")
    print(f'  brew bundle cleanup --file="{brewfile}"')
    print("Optional cleanup execute:")
    print(f'  brew bundle cleanup --file="{brewfile}" --force')

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
