#!/usr/bin/env python3
"""Back up the current Homebrew state for this host.

This script writes Homebrew inventory files into a host-specific backup
directory, commits any changes, pushes them, and leaves the local checkout
synchronized with the remote.
"""

# Author: Evan Musial <evan@evan.engineer>

from __future__ import annotations

import atexit
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
ANIMATION_SECONDS = 0.08
MAX_ANIMATION_FRAMES = 6

# The progress bar uses 256-color ANSI codes in interactive terminals. It
# falls back to plain text automatically for logs, pipes, or NO_COLOR.
FILLED_SEGMENT = "▰"
EMPTY_SEGMENT = "▱"
FILLED_COLOR = "\033[38;5;153m"
EMPTY_COLOR = "\033[38;5;24m"
RESET_COLOR = "\033[0m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

# These are the files that make up one complete Homebrew backup snapshot.
# They are added to Git after each run from inside the host backup directory.
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
    """Render a fixed-width, host-script-friendly terminal progress line."""

    def __init__(self, label: str, total_steps: int) -> None:
        self.label = label
        self.total_steps = total_steps
        self.completed_steps = 0
        self.current_percent = 0
        self.interactive = sys.stdout.isatty()
        self.use_color = self.interactive and "NO_COLOR" not in os.environ
        self.cursor_hidden = False
        self.active = False
        if self.interactive:
            atexit.register(self.show_cursor)

    def colorize(self, value: str, color: str) -> str:
        """Apply color only where ANSI output is appropriate."""
        if not self.use_color:
            return value
        return f"{color}{value}{RESET_COLOR}"

    def hide_cursor(self) -> None:
        """Hide the terminal cursor while the progress bar is repainting."""
        if self.interactive and not self.cursor_hidden:
            print(HIDE_CURSOR, end="", flush=True)
            self.cursor_hidden = True

    def show_cursor(self) -> None:
        """Restore the terminal cursor after progress output completes."""
        if self.cursor_hidden:
            print(SHOW_CURSOR, end="", flush=True)
            self.cursor_hidden = False

    def format_status(self, step: int | None = None) -> str:
        """Build the fixed-width status field to keep the bar from shifting."""
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
        """Draw one progress frame at the requested percentage."""
        percent = min(max(percent, 0), 100)
        filled = BAR_WIDTH if percent >= 100 else percent // 2
        filled_bar = self.colorize(FILLED_SEGMENT * filled, FILLED_COLOR)
        empty_bar = self.colorize(EMPTY_SEGMENT * (BAR_WIDTH - filled), EMPTY_COLOR)
        percent_text = self.colorize(f"{percent:3d}%", FILLED_COLOR)
        line = f"{self.format_status(step)} {filled_bar}{empty_bar} {percent_text}"
        if self.interactive:
            self.hide_cursor()
            print(f"\r{line}\033[K", end="", flush=True)
        else:
            print(line, flush=True)
        self.active = True

    def target_percent(self) -> int:
        """Convert completed task count into an integer percentage."""
        return round((self.completed_steps / self.total_steps) * 100)

    def draw(self, label: str | None = None, *, step: int | None = None) -> None:
        """Draw the current state without advancing the task count."""
        if label is not None:
            self.label = label

        self.current_percent = self.target_percent()
        self.render(self.current_percent, step=step)

    def animate_to(self, target_percent: int) -> None:
        """Animate between step boundaries without overworking slow terminals."""
        if not self.interactive:
            self.current_percent = target_percent
            self.render(target_percent)
            return

        distance = abs(target_percent - self.current_percent)
        if distance == 0:
            self.render(target_percent)
            return

        # Cap the number of redraws so older Intel Macs still get a smooth
        # transition without spending noticeable time repainting the terminal.
        start_percent = self.current_percent
        frame_count = min(distance, MAX_ANIMATION_FRAMES)
        delay = ANIMATION_SECONDS / frame_count
        previous_percent = start_percent
        for frame in range(1, frame_count + 1):
            percent = round(start_percent + ((target_percent - start_percent) * frame / frame_count))
            if percent == previous_percent and frame != frame_count:
                continue
            self.current_percent = percent
            self.render(percent)
            previous_percent = percent
            if frame != frame_count:
                time.sleep(delay)

    def advance(self, label: str | None = None) -> None:
        """Mark one logical step complete and animate to the new percentage."""
        self.completed_steps = min(self.completed_steps + 1, self.total_steps)
        if label is not None:
            self.label = label
        self.animate_to(self.target_percent())
        if self.completed_steps >= self.total_steps and self.interactive:
            print(flush=True)
            self.show_cursor()
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
    """Run a subprocess with consistent error handling and optional capture.

    Most Homebrew and Git commands are intentionally quiet on success so the
    progress bar stays readable. If a quiet command fails, captured output is
    replayed before raising the same style of exception subprocess.run(check)
    would have raised.
    """
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
    """Return the short host name used to choose the backup directory."""
    try:
        # `hostname -s` is the source of truth because the backup directory is
        # named backups.<short-hostname>. The socket fallback keeps the script
        # usable if the command is unavailable for some reason.
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


def colorize_output(value: str, color: str) -> str:
    """Color one-off status text using the same terminal rules as the bar."""
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return value
    return f"{color}{value}{RESET_COLOR}"


def write_command_output(args: list[str], destination: Path, *, env: dict[str, str] | None = None) -> None:
    """Write a command's stdout directly to one backup artifact."""
    with destination.open("w", encoding="utf-8") as output:
        run(args, cwd=destination.parent, env=env, stdout=output, quiet=True)


def main() -> int:
    this_hostname = short_hostname()

    # Backups are host-scoped so the same repository can carry separate
    # Homebrew inventories for multiple Macs without overwriting each other.
    backup_dir = Path.home() / "git" / "toolbox" / "brew" / f"backups.{this_hostname}"
    this_date = datetime.now(timezone.utc).strftime("%Y%m%d @ %H%M (UTC)")
    display_hostname = colorize_output(this_hostname, FILLED_COLOR)

    print()
    print(f"Backing up Homebrew state for host {display_hostname}")
    progress = ProgressBar("Preparing backup", 15)
    progress.draw(step=1)

    backup_dir.mkdir(parents=True, exist_ok=True)
    progress.advance("Prepared backup dir")

    # Pull before collecting data so this host starts from the latest remote
    # backup history and can fast-forward safely.
    run(["git", "pull", "--ff-only"], cwd=backup_dir, quiet=True)
    progress.advance("Synced backup repo")

    # `brew bundle dump` writes a Brewfile, but we wrap it with our own header
    # so each snapshot records when and where it was created.
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

        # The remaining files are audit/detail views of the same Homebrew
        # state. Restore only needs Brewfile, but these files make diffs and
        # reviews much easier to understand.
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

        # Stage only the expected backup artifacts. This avoids accidentally
        # committing local scratch files from the backup directory.
        run(["git", "add", *BACKUP_FILES], cwd=backup_dir, quiet=True)
        progress.advance("Staged backup files")
        diff = run(["git", "diff", "--cached", "--quiet"], cwd=backup_dir, check=False, quiet=True)
        progress.advance("Checked for changes")
        no_changes = False
        if diff.returncode == 0:
            progress.advance("No changes found")
            no_changes = True
        else:
            run(["git", "commit", "-m", f"Homebrew backup: {this_date}"], cwd=backup_dir, quiet=True)
            run(["git", "push"], cwd=backup_dir, quiet=True)
            progress.advance("Published backup")

        # Pull again after the run so the checkout is clean and synchronized
        # for the next daily alias invocation.
        run(["git", "pull", "--ff-only"], cwd=backup_dir, quiet=True)
        progress.advance("Synced final state")
        if no_changes:
            print("No Homebrew changes to commit.")
        print(f"Homebrew backup complete for host  {display_hostname}")
        print()
    finally:
        tmp_brewfile_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
