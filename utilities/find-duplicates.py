#!/usr/bin/env python3
"""Find duplicate files by MD5 and optionally clean up extra copies.

The directory is scanned recursively. Files with matching sizes and MD5
digests are also compared byte-for-byte before they are reported as duplicates.
By default, the script is a dry run. The oldest copy is kept. ``--delete``
removes the other copies, while ``--stash`` renames them in place with a
``duplicate_`` prefix.
"""

# Author: Evan Musial <evan@evan.engineer>

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CHUNK_SIZE = 1024 * 1024
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


@dataclass(frozen=True)
class FileRecord:
    """A file and the metadata captured while its MD5 was calculated."""

    path: Path
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    md5: str


@dataclass(frozen=True)
class DuplicateGroup:
    """One set of byte-for-byte identical files."""

    md5: str
    keep: FileRecord
    duplicates: tuple[FileRecord, ...]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Recursively find duplicate files using MD5. The oldest file in "
            "each group is kept (based on modification time)."
        )
    )
    parser.add_argument("directory", type=Path, help="directory to scan recursively")

    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--delete",
        action="store_true",
        help=(
            "delete copies other than the oldest after an exact uppercase Y "
            "confirmation"
        ),
    )
    action.add_argument(
        "--stash",
        action="store_true",
        help=(
            "rename copies other than the oldest with a duplicate_ prefix "
            "after an exact uppercase Y confirmation"
        ),
    )
    return parser.parse_args()


def colorize(text: str, color: str, *, use_color: bool) -> str:
    """Color a label when writing to an interactive terminal."""
    if not use_color:
        return text
    return f"{color}{text}{RESET}"


def snapshot_key(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return metadata used to notice a file changing during the scan."""
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def md5_file(path: Path, expected_stat: os.stat_result) -> FileRecord:
    """Hash one file and fail if it changes while being read."""
    before = path.stat(follow_symlinks=False)
    if snapshot_key(before) != snapshot_key(expected_stat):
        raise OSError("file changed before it could be hashed")

    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)

    after = path.stat(follow_symlinks=False)
    if snapshot_key(before) != snapshot_key(after):
        raise OSError("file changed while it was being hashed")

    return FileRecord(
        path=path,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
        device=after.st_dev,
        inode=after.st_ino,
        md5=digest.hexdigest(),
    )


def files_equal(first: FileRecord, second: FileRecord) -> bool:
    """Confirm that two MD5 candidates are byte-for-byte identical."""
    if first.size != second.size:
        return False

    with first.path.open("rb") as first_handle, second.path.open("rb") as second_handle:
        first_before = os.fstat(first_handle.fileno())
        second_before = os.fstat(second_handle.fileno())
        first_identity = (
            first_before.st_dev,
            first_before.st_ino,
            first_before.st_size,
            first_before.st_mtime_ns,
        )
        second_identity = (
            second_before.st_dev,
            second_before.st_ino,
            second_before.st_size,
            second_before.st_mtime_ns,
        )
        if first_identity != (first.device, first.inode, first.size, first.mtime_ns):
            raise OSError(f"{first.path} changed before comparison")
        if second_identity != (
            second.device,
            second.inode,
            second.size,
            second.mtime_ns,
        ):
            raise OSError(f"{second.path} changed before comparison")

        equal = True
        while True:
            first_chunk = first_handle.read(CHUNK_SIZE)
            second_chunk = second_handle.read(CHUNK_SIZE)
            if first_chunk != second_chunk:
                equal = False
                break
            if not first_chunk:
                break

        first_after = os.fstat(first_handle.fileno())
        second_after = os.fstat(second_handle.fileno())
        if snapshot_key(first_before) != snapshot_key(first_after):
            raise OSError(f"{first.path} changed during comparison")
        if snapshot_key(second_before) != snapshot_key(second_after):
            raise OSError(f"{second.path} changed during comparison")
        return equal


def find_duplicate_groups(root: Path) -> tuple[list[DuplicateGroup], list[str]]:
    """Scan root recursively and return exact duplicate groups and errors."""
    errors: list[str] = []
    files_by_size: dict[int, list[tuple[Path, os.stat_result]]] = defaultdict(list)

    def record_walk_error(error: OSError) -> None:
        location = error.filename or root
        errors.append(f"{location}: {error.strerror or error}")

    for directory, directory_names, file_names in os.walk(
        root, followlinks=False, onerror=record_walk_error
    ):
        directory_names.sort()
        file_names.sort()
        parent = Path(directory)

        for file_name in file_names:
            path = parent / file_name
            try:
                file_stat = path.stat(follow_symlinks=False)
            except OSError as error:
                errors.append(f"{path}: {error.strerror or error}")
                continue

            # Do not follow or report symlinks, sockets, devices, or FIFOs.
            if stat.S_ISREG(file_stat.st_mode):
                files_by_size[file_stat.st_size].append((path, file_stat))

    files_by_digest: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    for size in sorted(files_by_size):
        candidates = files_by_size[size]
        if len(candidates) < 2:
            continue

        for path, file_stat in candidates:
            try:
                record = md5_file(path, file_stat)
            except OSError as error:
                errors.append(f"{path}: {error.strerror or error}")
                continue
            files_by_digest[(record.size, record.md5)].append(record)

    exact_groups: list[tuple[str, list[FileRecord]]] = []
    for (_, digest), candidates in sorted(files_by_digest.items()):
        if len(candidates) < 2:
            continue

        content_groups: list[list[FileRecord]] = []
        for candidate in sorted(candidates, key=lambda item: str(item.path)):
            comparison_failed = False
            for content_group in content_groups:
                try:
                    if files_equal(candidate, content_group[0]):
                        content_group.append(candidate)
                        break
                except OSError as error:
                    errors.append(
                        f"Could not compare {candidate.path} with "
                        f"{content_group[0].path}: {error.strerror or error}"
                    )
                    comparison_failed = True
                    break
            else:
                content_groups.append([candidate])

            if comparison_failed:
                continue

        exact_groups.extend(
            (digest, content_group)
            for content_group in content_groups
            if len(content_group) > 1
        )

    groups: list[DuplicateGroup] = []
    for digest, files in exact_groups:
        # Keep the oldest file. The lexicographically first path wins a
        # modification-time tie.
        ordered = sorted(files, key=lambda item: (item.mtime_ns, str(item.path)))
        groups.append(
            DuplicateGroup(md5=digest, keep=ordered[0], duplicates=tuple(ordered[1:]))
        )

    groups.sort(key=lambda group: str(group.keep.path))
    return groups, errors


def relative_display(path: Path, root: Path) -> str:
    """Display a path relative to the explicitly selected scan root."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def formatted_mtime(mtime_ns: int) -> str:
    """Format a nanosecond modification timestamp in the local timezone."""
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000).astimezone().isoformat(
        timespec="seconds"
    )


def next_stash_path(path: Path, reserved: set[Path]) -> Path:
    """Choose a duplicate_-prefixed path without planning an overwrite."""
    destination = path.with_name(f"duplicate_{path.name}")
    counter = 2
    while os.path.lexists(destination) or destination in reserved:
        destination = path.with_name(f"duplicate_{counter}_{path.name}")
        counter += 1
    reserved.add(destination)
    return destination


def build_stash_plan(groups: list[DuplicateGroup]) -> dict[Path, Path]:
    """Plan deterministic, non-overwriting stash names."""
    reserved: set[Path] = set()
    return {
        duplicate.path: next_stash_path(duplicate.path, reserved)
        for group in groups
        for duplicate in group.duplicates
    }


def print_plan(
    groups: list[DuplicateGroup],
    root: Path,
    *,
    action: str,
    stash_plan: dict[Path, Path],
    use_color: bool,
) -> int:
    """Print every keeper and planned duplicate action."""
    duplicate_count = sum(len(group.duplicates) for group in groups)
    group_word = "group" if len(groups) == 1 else "groups"
    file_word = "file" if duplicate_count == 1 else "files"
    print(
        f"Found {len(groups)} duplicate {group_word} "
        f"({duplicate_count} extra {file_word})."
    )

    for index, group in enumerate(groups, start=1):
        print(f"\nGroup {index} — MD5 {group.md5}")
        keep_label = colorize(f"{'[KEEP]':<10}", GREEN, use_color=use_color)
        print(
            f"  {keep_label} {relative_display(group.keep.path, root)} "
            f"(oldest; modified {formatted_mtime(group.keep.mtime_ns)})"
        )

        for duplicate in group.duplicates:
            if action == "stash":
                label = colorize(f"{'[STASH]':<10}", YELLOW, use_color=use_color)
                destination = relative_display(stash_plan[duplicate.path], root)
                suffix = f" -> {destination}"
            else:
                label = colorize(f"{'[DELETE]':<10}", RED, use_color=use_color)
                suffix = ""
            print(
                f"  {label} {relative_display(duplicate.path, root)}"
                f"{suffix} (modified {formatted_mtime(duplicate.mtime_ns)})"
            )

    return duplicate_count


def confirm(action: str, duplicate_count: int) -> bool:
    """Require an exact uppercase Y before any requested mutation pass."""
    verb = "delete" if action == "delete" else "stash"
    try:
        answer = input(
            f"\nType Y to {verb} {duplicate_count} duplicate "
            f"{'file' if duplicate_count == 1 else 'files'}: "
        )
    except EOFError:
        answer = ""
    return answer.strip() == "Y"


def record_snapshot(record: FileRecord) -> tuple[int, int, int, int, int]:
    """Return the scan-time snapshot for a file record."""
    return (
        record.device,
        record.inode,
        record.size,
        record.mtime_ns,
        record.ctime_ns,
    )


def validate_record(record: FileRecord, *, strict: bool) -> str | None:
    """Return an error if a planned file no longer matches the scan."""
    try:
        current = record.path.stat(follow_symlinks=False)
    except OSError as error:
        return f"{record.path}: {error.strerror or error}"

    if not stat.S_ISREG(current.st_mode):
        return f"{record.path}: no longer a regular file"

    if strict:
        unchanged = snapshot_key(current) == record_snapshot(record)
    else:
        # Earlier actions can alter ctime for hard-linked paths without
        # changing their identity or contents, so the immediate check leaves
        # ctime to the byte-for-byte comparison below.
        unchanged = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) == (record.device, record.inode, record.size, record.mtime_ns)

    if not unchanged:
        return f"{record.path}: changed since it was scanned"
    return None


def validate_plan(
    groups: list[DuplicateGroup], action: str, stash_plan: dict[Path, Path]
) -> list[str]:
    """Revalidate the complete confirmed plan before changing any files."""
    errors: list[str] = []
    for group in groups:
        keeper_error = validate_record(group.keep, strict=True)
        if keeper_error:
            errors.append(keeper_error)

        for duplicate in group.duplicates:
            duplicate_error = validate_record(duplicate, strict=True)
            if duplicate_error:
                errors.append(duplicate_error)

            if not keeper_error and not duplicate_error:
                try:
                    if not files_equal(group.keep, duplicate):
                        errors.append(
                            f"{duplicate.path}: contents no longer match {group.keep.path}"
                        )
                except OSError as error:
                    errors.append(
                        f"Could not compare {duplicate.path} with {group.keep.path}: "
                        f"{error.strerror or error}"
                    )

            if action == "stash":
                destination = stash_plan[duplicate.path]
                if os.path.lexists(destination):
                    errors.append(f"{destination}: stash destination now exists")

    return errors


def validate_immediately_before_action(
    group: DuplicateGroup, duplicate: FileRecord
) -> str | None:
    """Check a duplicate and its keeper again immediately before mutation."""
    for record in (group.keep, duplicate):
        error = validate_record(record, strict=False)
        if error:
            return error

    try:
        if not files_equal(group.keep, duplicate):
            return f"{duplicate.path}: contents no longer match {group.keep.path}"
    except OSError as error:
        return (
            f"Could not compare {duplicate.path} with {group.keep.path}: "
            f"{error.strerror or error}"
        )
    return None


def stash_without_overwrite(source: Path, destination: Path) -> None:
    """Move a regular file without ever replacing an existing destination."""
    # Path.rename() can overwrite on POSIX. A same-directory hard link gives
    # us an atomic no-clobber destination; removing the old name completes the
    # move while preserving the file's inode and metadata.
    os.link(source, destination, follow_symlinks=False)
    try:
        source.unlink()
    except OSError as source_error:
        try:
            destination.unlink()
        except OSError as rollback_error:
            raise OSError(
                f"could not remove the original after creating {destination}; "
                f"rollback also failed: {rollback_error}"
            ) from source_error
        raise


def apply_action(
    groups: list[DuplicateGroup], action: str, stash_plan: dict[Path, Path]
) -> tuple[int, list[str]]:
    """Delete or stash each confirmed duplicate, returning successes and errors."""
    completed = 0
    errors: list[str] = []
    print()

    for group in groups:
        for duplicate in group.duplicates:
            validation_error = validate_immediately_before_action(group, duplicate)
            if validation_error:
                errors.append(validation_error)
                continue

            try:
                if action == "delete":
                    duplicate.path.unlink()
                    print(f"Deleted: {duplicate.path}")
                else:
                    destination = stash_plan[duplicate.path]
                    stash_without_overwrite(duplicate.path, destination)
                    print(f"Stashed: {duplicate.path} -> {destination}")
            except OSError as error:
                errors.append(f"{duplicate.path}: {error.strerror or error}")
                continue

            completed += 1

    return completed, errors


def main() -> int:
    args = parse_args()
    try:
        root = args.directory.expanduser().resolve(strict=True)
    except OSError as error:
        print(f"Cannot scan {args.directory}: {error}", file=sys.stderr)
        return 2

    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    action = "stash" if args.stash else "delete"
    is_mutation_pass = args.delete or args.stash
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    print(f"Scanning recursively: {root}")
    groups, errors = find_duplicate_groups(root)

    if errors:
        print("\nScan warnings:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)

    if not groups:
        print("No duplicate files found.")
        return 1 if errors else 0

    stash_plan = build_stash_plan(groups) if action == "stash" else {}
    duplicate_count = print_plan(
        groups,
        root,
        action=action,
        stash_plan=stash_plan,
        use_color=use_color,
    )

    if errors:
        if is_mutation_pass:
            print(
                "\nThe scan was incomplete, so the requested action will not continue.",
                file=sys.stderr,
            )
        return 1

    if not is_mutation_pass:
        print(
            "\nDry run only. Re-run with --delete to delete the red entries or "
            "--stash to rename them with a duplicate_ prefix."
        )
        return 0

    if not confirm(action, duplicate_count):
        print("Cancelled. No files were changed.")
        return 0

    validation_errors = validate_plan(groups, action, stash_plan)
    if validation_errors:
        print("\nThe plan is no longer safe to apply:", file=sys.stderr)
        for error in validation_errors:
            print(f"  - {error}", file=sys.stderr)
        print("No files were changed.", file=sys.stderr)
        return 1

    try:
        completed, action_errors = apply_action(groups, action, stash_plan)
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Review the printed successes; the remaining actions "
            "were not completed.",
            file=sys.stderr,
        )
        return 130
    if action_errors:
        print("\nSome actions failed:", file=sys.stderr)
        for error in action_errors:
            print(f"  - {error}", file=sys.stderr)

    past_tense = "Deleted" if action == "delete" else "Stashed"
    print(f"\n{past_tense} {completed} duplicate {'file' if completed == 1 else 'files'}.")
    return 1 if action_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
