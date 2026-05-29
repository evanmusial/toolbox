#!/usr/bin/env python3
"""Restore Homebrew state from a Brewfile."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


BAR_WIDTH = 50
STATUS_WIDTH = 36
ANIMATION_SECONDS = 0.35
FILLED_SEGMENT = "▰"
EMPTY_SEGMENT = "▱"
FILLED_COLOR = "\033[38;5;153m"
EMPTY_COLOR = "\033[38;5;24m"
RESET_COLOR = "\033[0m"


class ProgressBar:
    def __init__(self, label: str, total_steps: int) -> None:
        self.label = label
        self.total_steps = total_steps
        self.completed_steps = 0
        self.current_percent = 0
        self.interactive = sys.stdout.isatty()
        self.use_color = self.interactive and "NO_COLOR" not in os.environ
        self.active = False

    def colorize(self, value: str, color: str) -> str:
        if not self.use_color:
            return value
        return f"{color}{value}{RESET_COLOR}"

    def format_status(self, step: int | None = None) -> str:
        display_step = step if step is not None else self.completed_steps
        display_step = min(max(display_step, 1), self.total_steps)
        step_text = f"{display_step}/{self.total_steps}"
        available_label_width = STATUS_WIDTH - len(step_text) - 5
        label = self.label
        if len(label) > available_label_width:
            label = label[: max(available_label_width - 3, 0)] + "..."

        raw_status = f"  [{step_text}] {label}"
        padding = " " * max(STATUS_WIDTH - len(raw_status), 0)
        if not self.use_color:
            return f"{raw_status}{padding}"

        return (
            "  "
            f"{self.colorize('[', EMPTY_COLOR)}"
            f"{self.colorize(step_text, FILLED_COLOR)}"
            f"{self.colorize(']', EMPTY_COLOR)} "
            f"{self.colorize(label, FILLED_COLOR)}"
            f"{padding}"
        )

    def render(self, percent: int, *, step: int | None = None) -> None:
        percent = min(max(percent, 0), 100)
        filled = BAR_WIDTH if percent >= 100 else percent // 2
        filled_bar = self.colorize(FILLED_SEGMENT * filled, FILLED_COLOR)
        empty_bar = self.colorize(EMPTY_SEGMENT * (BAR_WIDTH - filled), EMPTY_COLOR)
        percent_text = self.colorize(f"{percent:3d}%", FILLED_COLOR)
        line = f"{self.format_status(step)} {filled_bar}{empty_bar} {percent_text}"
        if self.interactive:
            print(f"\r{line}\033[K", end="", flush=True)
        else:
            print(line, flush=True)
        self.active = True

    def target_percent(self) -> int:
        return round((self.completed_steps / self.total_steps) * 100)

    def draw(self, label: str | None = None, *, step: int | None = None) -> None:
        if label is not None:
            self.label = label

        self.current_percent = self.target_percent()
        self.render(self.current_percent, step=step)

    def animate_to(self, target_percent: int) -> None:
        if not self.interactive:
            self.current_percent = target_percent
            self.render(target_percent)
            return

        distance = abs(target_percent - self.current_percent)
        if distance == 0:
            self.render(target_percent)
            return

        direction = 1 if target_percent > self.current_percent else -1
        delay = min(0.02, ANIMATION_SECONDS / distance)
        for percent in range(self.current_percent + direction, target_percent + direction, direction):
            self.current_percent = percent
            self.render(percent)
            time.sleep(delay)

    def advance(self, label: str | None = None) -> None:
        self.completed_steps = min(self.completed_steps + 1, self.total_steps)
        if label is not None:
            self.label = label
        self.animate_to(self.target_percent())
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
    progress.draw(step=1)
    run(["brew", "update"], quiet=True)
    progress.advance("Updated Homebrew")

    progress.draw("Checking Brewfile", step=2)
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
        progress.draw("Installing packages", step=3)
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
