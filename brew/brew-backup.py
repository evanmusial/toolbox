#!/usr/bin/env python3
"""Back up the current Homebrew state for this host."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


BAR_WIDTH = 50
STATUS_WIDTH = 36
ANIMATION_SECONDS = 0.35
FILLED_SEGMENT = "▰"
EMPTY_SEGMENT = "▱"
FILLED_COLOR = "\033[38;5;153m"
EMPTY_COLOR = "\033[38;5;24m"
RESET_COLOR = "\033[0m"

BACKUP_FILES = [
    "Brewfile",
    "brew-formulae.requested.txt",
    "brew-formulae.dependency-leaves.txt",
    "brew-formulae.versions.txt",
    "brew-casks.versions.txt",
    "brew-deps.declared.tree.txt",
    "brew-deps.installed.txt",
    "brew-installed.json",
]


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


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout=None,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    process_stdout = stdout
    process_stderr = subprocess.PIPE if quiet else None
    if quiet and stdout is None:
        process_stdout = subprocess.PIPE

    completed = subprocess.run(
        args,
        cwd=cwd,
        env=merged_env,
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


def write_command_output(args: list[str], destination: Path, *, env: dict[str, str] | None = None) -> None:
    with destination.open("w", encoding="utf-8") as output:
        run(args, cwd=destination.parent, env=env, stdout=output, quiet=True)


def main() -> int:
    this_hostname = short_hostname()
    backup_dir = Path.home() / "git" / "toolbox" / "brew" / f"backups.{this_hostname}"
    this_date = datetime.now(timezone.utc).strftime("%Y%m%d @ %H%M (UTC)")

    print("Backup of Homebrew state...")
    progress = ProgressBar("Preparing backup", 14)
    progress.draw(step=1)

    backup_dir.mkdir(parents=True, exist_ok=True)
    progress.advance("Prepared backup dir")

    run(["git", "pull", "--ff-only"], cwd=backup_dir, quiet=True)
    progress.advance("Synced backup repo")

    tmp_brewfile = tempfile.NamedTemporaryFile(delete=False)
    tmp_brewfile_path = Path(tmp_brewfile.name)
    tmp_brewfile.close()

    try:
        run(
            [
                "brew",
                "bundle",
                "dump",
                "--force",
                "--describe",
                f"--file={tmp_brewfile_path}",
            ],
            cwd=backup_dir,
            quiet=True,
        )
        progress.advance("Dumped Brewfile")

        brewfile_contents = tmp_brewfile_path.read_text(encoding="utf-8")
        (backup_dir / "Brewfile").write_text(
            f"# backup: {this_date}\n# host: {this_hostname}\n\n{brewfile_contents}",
            encoding="utf-8",
        )
        progress.advance("Wrote Brewfile")

        write_command_output(
            ["brew", "leaves", "--installed-on-request"],
            backup_dir / "brew-formulae.requested.txt",
        )
        progress.advance("Saved requested formulae")
        write_command_output(
            ["brew", "leaves", "--installed-as-dependency"],
            backup_dir / "brew-formulae.dependency-leaves.txt",
        )
        progress.advance("Saved dependency leaves")
        write_command_output(
            ["brew", "list", "--formula", "--versions"],
            backup_dir / "brew-formulae.versions.txt",
        )
        progress.advance("Saved formula versions")
        write_command_output(
            ["brew", "list", "--cask", "--versions"],
            backup_dir / "brew-casks.versions.txt",
        )
        progress.advance("Saved cask versions")
        write_command_output(
            ["brew", "deps", "--installed", "--tree", "--annotate"],
            backup_dir / "brew-deps.declared.tree.txt",
            env={"HOMEBREW_NO_ENV_HINTS": "1"},
        )
        progress.advance("Saved declared deps")
        write_command_output(
            ["brew", "deps", "--installed"],
            backup_dir / "brew-deps.installed.txt",
        )
        progress.advance("Saved installed deps")
        write_command_output(
            ["brew", "info", "--json=v2", "--installed"],
            backup_dir / "brew-installed.json",
        )
        progress.advance("Saved installed JSON")

        run(["git", "add", *BACKUP_FILES], cwd=backup_dir, quiet=True)
        progress.advance("Staged backup files")
        diff = run(["git", "diff", "--cached", "--quiet"], cwd=backup_dir, check=False, quiet=True)
        progress.advance("Checked for changes")
        if diff.returncode == 0:
            progress.advance("No changes found")
            print("No Homebrew changes to commit.")
        else:
            run(["git", "commit", "-m", f"Homebrew backup: {this_date}"], cwd=backup_dir, quiet=True)
            run(["git", "push"], cwd=backup_dir, quiet=True)
            progress.advance("Published backup")
    finally:
        tmp_brewfile_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
