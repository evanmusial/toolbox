#!/usr/bin/env python3
"""Interactively review subscriptions from the authenticated YouTube account.

The default mode is read-only and can persistently stage channels for later
removal. Passing ``--allow-unsubscribe`` enables a separately confirmed batch
apply command and requests the broader OAuth scope YouTube requires for it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
FULL_SCOPE = "https://www.googleapis.com/auth/youtube"
MAX_API_PAGE_SIZE = 50
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# xterm-256 color indexes. Color is decoration only; all layout is calculated
# before escape sequences are applied.
COLOR_CYAN = 45
COLOR_BLUE = 75
COLOR_GREEN = 114
COLOR_YELLOW = 221
COLOR_RED = 203
COLOR_MAGENTA = 213
COLOR_WHITE = 255
COLOR_MUTED = 244
COLOR_RULE = 238
KEY_LEFT = "__left_arrow__"
KEY_RIGHT = "__right_arrow__"


@dataclass
class LatestUpload:
    """The most recent public upload returned for a channel."""

    video_id: str
    title: str
    published_at: str | None


@dataclass
class UnsubscribeBatchResult:
    """Outcome of a checkpointed unsubscribe batch."""

    removed: list[Subscription]
    quota_exhausted: bool = False


@dataclass
class Subscription:
    """A subscription plus public metadata for its target channel."""

    subscription_id: str
    channel_id: str
    name: str
    description: str
    handle: str | None = None
    subscribed_at: str | None = None
    channel_created_at: str | None = None
    country: str | None = None
    subscriber_count: int | None = None
    subscribers_hidden: bool = False
    video_count: int | None = None
    view_count: int | None = None
    uploads_playlist_id: str | None = None
    latest_upload: LatestUpload | None = None
    latest_upload_checked: bool = False
    latest_upload_error: str | None = None

    @property
    def channel_url(self) -> str:
        return f"https://www.youtube.com/channel/{self.channel_id}"


def default_token_path() -> Path:
    """Return a token path outside the repository."""
    base = Path(
        os.environ.get(
            "XDG_CONFIG_HOME", str(Path.home() / ".config")
        )
    )
    return base / "youtube-subscription-review" / "token.json"


def default_backup_dir() -> Path:
    """Return the durable subscription-backup directory."""
    base = Path(
        os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
        )
    )
    return base / "youtube-subscription-review" / "backups"


def default_marks_path() -> Path:
    """Return the persistent staged-unsubscribe queue path."""
    return default_backup_dir().parent / "marked-for-unsubscribe.json"


def default_kept_path() -> Path:
    """Return the persistent reviewed-and-kept decision path."""
    return default_backup_dir().parent / "kept-subscriptions.json"


def positive_integer(value: str) -> int:
    """Parse a strictly positive command-line integer."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Review the authenticated YouTube account's channel subscriptions "
            "one at a time. The default mode cannot change subscriptions."
        )
    )
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=Path(os.environ.get("YOUTUBE_CLIENT_SECRETS", "client_secret.json")),
        help=(
            "Google OAuth Desktop client JSON (default: client_secret.json, or "
            "YOUTUBE_CLIENT_SECRETS)"
        ),
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=default_token_path(),
        help="OAuth token cache (default: %(default)s)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=default_backup_dir(),
        help="directory for timestamped inventory backups (default: %(default)s)",
    )
    parser.add_argument(
        "--marks-file",
        type=Path,
        default=default_marks_path(),
        help="persistent staged-unsubscribe queue (default: %(default)s)",
    )
    parser.add_argument(
        "--kept-file",
        type=Path,
        default=default_kept_path(),
        help="persistent reviewed-and-kept decisions (default: %(default)s)",
    )
    parser.add_argument(
        "--order",
        choices=("alphabetical", "relevance", "unread"),
        default="alphabetical",
        help="subscription ordering (default: %(default)s)",
    )
    parser.add_argument(
        "--no-last-upload",
        action="store_true",
        help="skip latest-upload requests (faster and uses less API quota)",
    )
    parser.add_argument(
        "--allow-unsubscribe",
        action="store_true",
        help=(
            "enable applying the confirmed unsubscribe queue and request "
            "YouTube's broader management OAuth scope"
        ),
    )
    parser.add_argument(
        "--max-unsubscribes",
        type=positive_integer,
        default=150,
        help=(
            "maximum channels processed by one apply command "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable xterm-256 colors",
    )
    parser.add_argument(
        "--reset-decisions",
        action="store_true",
        help=(
            "archive and clear local keep/unsubscribe decisions without "
            "calling YouTube"
        ),
    )
    args = parser.parse_args(argv)
    return args


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size chunks from values."""
    for start in range(0, len(values), size):
        yield values[start : start + size]


def integer_or_none(value: Any) -> int | None:
    """Convert a YouTube integer string to int when present."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_handle(value: Any) -> str | None:
    """Normalize YouTube's channel custom URL into an @handle for display."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().rstrip("/").rsplit("/", 1)[-1]
    return candidate if candidate.startswith("@") else f"@{candidate}"


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a YouTube ISO-8601 timestamp."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def human_age(value: str | None, *, now: datetime | None = None) -> str:
    """Return a compact human-readable age for an ISO timestamp."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return "unknown"
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - parsed).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 60:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if days < 730:
        months = max(1, int(days / 30.4375))
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = max(1, int(days / 365.25))
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_count(value: int | None) -> str:
    """Format a large integer compactly without hiding small exact counts."""
    if value is None:
        return "n/a"
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= divisor:
            compact = f"{value / divisor:.1f}".rstrip("0").rstrip(".")
            return f"{compact}{suffix}"
    return f"{value:,}"


def normalized_description(value: str) -> str:
    """Collapse whitespace for terminal display."""
    return " ".join(value.split())


def supports_color(*, disabled: bool = False) -> bool:
    """Return whether ANSI color should be emitted for this terminal."""
    return (
        not disabled
        and "NO_COLOR" not in os.environ
        and sys.stdout.isatty()
        and os.environ.get("TERM", "") != "dumb"
    )


def paint(
    text: str,
    *,
    fg: int | None = None,
    bg: int | None = None,
    bold: bool = False,
    dim: bool = False,
    use_color: bool = True,
) -> str:
    """Apply xterm-256 styling without changing visible layout width."""
    if not use_color:
        return text
    codes: list[str] = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if fg is not None:
        codes.extend(("38", "5", str(fg)))
    if bg is not None:
        codes.extend(("48", "5", str(bg)))
    if not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def strip_ansi(text: str) -> str:
    """Remove SGR sequences for layout tests and non-terminal consumers."""
    return ANSI_ESCAPE_RE.sub("", text)


def read_interactive_command(prompt: str) -> str:
    """Read a command while recognizing left/right arrows on a POSIX terminal."""
    if not sys.stdin.isatty():
        return input(prompt).strip()

    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    buffer: list[str] = []
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        tty.setraw(descriptor)
        while True:
            character = sys.stdin.read(1)
            if character in ("\r", "\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "".join(buffer).strip()
            if character == "\x03":
                sys.stdout.write("^C\r\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            if character == "\x04":
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                raise EOFError
            if character == "\x1b":
                second = sys.stdin.read(1)
                third = sys.stdin.read(1)
                if not buffer and second == "[" and third in ("C", "D"):
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return KEY_RIGHT if third == "C" else KEY_LEFT
                continue
            if character in ("\x7f", "\x08"):
                if buffer:
                    buffer.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if character.isprintable():
                buffer.append(character)
                sys.stdout.write(character)
                sys.stdout.flush()
                if len(buffer) == 1 and character.lower() in {
                    "?",
                    "k",
                    "p",
                    "q",
                    "u",
                    "x",
                }:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return character.lower()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def display_width(text: str) -> int:
    """Return approximate xterm cell width, ignoring ANSI styling."""
    width = 0
    for character in strip_ansi(text):
        if unicodedata.combining(character) or unicodedata.category(character) in (
            "Cf",
            "Me",
            "Mn",
        ):
            continue
        width += 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
    return width


def truncate(value: str, width: int, *, placeholder: str = "…") -> str:
    """Truncate a single-line value to an exact maximum display width."""
    normalized = normalized_description(value)
    if display_width(normalized) <= width:
        return normalized
    placeholder_width = display_width(placeholder)
    if width <= placeholder_width:
        return placeholder if width == placeholder_width else ""
    available = width - placeholder_width
    result: list[str] = []
    used = 0
    for character in normalized:
        character_width = display_width(character)
        if used + character_width > available:
            break
        result.append(character)
        used += character_width
    return "".join(result).rstrip() + placeholder


def retrieve_subscriptions(youtube: Any, *, order: str) -> list[Subscription]:
    """Retrieve every subscription for the authenticated account."""
    records: list[Subscription] = []
    page_token: str | None = None
    while True:
        response = (
            youtube.subscriptions()
            .list(
                part="snippet",
                mine=True,
                maxResults=MAX_API_PAGE_SIZE,
                order=order,
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})
            channel_id = resource.get("channelId")
            subscription_id = item.get("id")
            if not channel_id or not subscription_id:
                continue
            records.append(
                Subscription(
                    subscription_id=subscription_id,
                    channel_id=channel_id,
                    name=snippet.get("title") or channel_id,
                    description=snippet.get("description") or "",
                    subscribed_at=snippet.get("publishedAt"),
                )
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return records


def enrich_channels(youtube: Any, records: list[Subscription]) -> None:
    """Attach public channel statistics and uploads-playlist IDs in batches."""
    by_id = {record.channel_id: record for record in records}
    for channel_ids in chunks(list(by_id), MAX_API_PAGE_SIZE):
        response = (
            youtube.channels()
            .list(
                part="snippet,statistics,contentDetails",
                id=",".join(channel_ids),
                maxResults=MAX_API_PAGE_SIZE,
            )
            .execute()
        )
        for item in response.get("items", []):
            record = by_id.get(item.get("id"))
            if record is None:
                continue
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            related = item.get("contentDetails", {}).get("relatedPlaylists", {})
            record.name = snippet.get("title") or record.name
            record.description = snippet.get("description") or record.description
            record.handle = normalize_handle(snippet.get("customUrl"))
            record.channel_created_at = snippet.get("publishedAt")
            record.country = snippet.get("country")
            record.subscribers_hidden = bool(statistics.get("hiddenSubscriberCount"))
            record.subscriber_count = integer_or_none(statistics.get("subscriberCount"))
            record.video_count = integer_or_none(statistics.get("videoCount"))
            record.view_count = integer_or_none(statistics.get("viewCount"))
            record.uploads_playlist_id = related.get("uploads")


def load_subscriptions(youtube: Any, *, order: str) -> list[Subscription]:
    """Retrieve all subscriptions and enrich them with channel metadata."""
    records = retrieve_subscriptions(youtube, order=order)
    enrich_channels(youtube, records)
    if order == "alphabetical":
        records.sort(key=lambda record: record.name.casefold())
    return records


def google_api_error_details(
    error: Exception,
) -> tuple[int | None, str | None, str | None]:
    """Return HTTP status, machine reason, and message from a Google API error."""
    status = getattr(getattr(error, "resp", None), "status", None)
    content = getattr(error, "content", b"")
    try:
        decoded = json.loads(
            content.decode("utf-8") if isinstance(content, bytes) else content
        )
        error_body = decoded.get("error", {})
        errors = error_body.get("errors") or []
        reason = errors[0].get("reason") if errors else None
        return status, reason, error_body.get("message")
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
        return status, None, None


def ensure_latest_upload(youtube: Any, record: Subscription) -> None:
    """Fetch a channel's latest public upload at most once per run."""
    if record.latest_upload_checked:
        return
    record.latest_upload_checked = True
    if not record.uploads_playlist_id:
        record.latest_upload_error = "unavailable"
        return
    try:
        response = (
            youtube.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=record.uploads_playlist_id,
                maxResults=1,
            )
            .execute()
        )
    except Exception as error:
        status, reason, _ = google_api_error_details(error)
        if status == 404 or (
            status == 403
            and reason in {"channelForbidden", "playlistForbidden", "playlistNotFound"}
        ):
            record.latest_upload_error = "unavailable"
            return
        raise
    items = response.get("items", [])
    if not items:
        return
    item = items[0]
    snippet = item.get("snippet", {})
    details = item.get("contentDetails", {})
    video_id = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
    if not video_id:
        return
    record.latest_upload = LatestUpload(
        video_id=video_id,
        title=snippet.get("title") or "Untitled video",
        published_at=details.get("videoPublishedAt") or snippet.get("publishedAt"),
    )


def latest_age(record: Subscription) -> str:
    """Return the latest-upload age shown in the table."""
    if not record.latest_upload_checked:
        return "not checked"
    if record.latest_upload_error:
        return record.latest_upload_error
    if record.latest_upload:
        return human_age(record.latest_upload.published_at)
    return "none found"


def render_single_record(
    record: Subscription,
    *,
    position: int,
    total: int,
    reviewed: int,
    remaining: int,
    marked_count: int,
    decision: str | None,
    width: int,
    use_color: bool,
) -> str:
    """Render one complete review card for a keep/unsubscribe decision."""
    subscriber_text = (
        "hidden" if record.subscribers_hidden else format_count(record.subscriber_count)
    )
    decision_text = decision.upper() if decision else "UNDECIDED"
    decision_color = (
        COLOR_GREEN
        if decision == "keep"
        else COLOR_RED if decision == "unsubscribe" else COLOR_YELLOW
    )
    lines = [
        paint("━" * width, fg=COLOR_RULE, use_color=use_color),
        paint(
            f"CHANNEL {position} OF {total}",
            fg=COLOR_BLUE,
            bold=True,
            use_color=use_color,
        )
        + paint(
            f"   reviewed {reviewed}/{total}   remaining {remaining}   "
            f"staged {marked_count}",
            fg=COLOR_MUTED,
            use_color=use_color,
        ),
        paint("━" * width, fg=COLOR_RULE, use_color=use_color),
        paint(record.name, fg=COLOR_CYAN, bold=True, use_color=use_color)
        + "  "
        + paint(
            record.handle or "@handle unavailable",
            fg=COLOR_BLUE if record.handle else COLOR_MUTED,
            bold=bool(record.handle),
            use_color=use_color,
        ),
        paint(
            f"Decision: {decision_text}",
            fg=decision_color,
            bold=True,
            use_color=use_color,
        ),
        "",
    ]

    def field(label: str, value: str, *, color: int = COLOR_WHITE) -> None:
        lines.append(
            paint(label.ljust(16), fg=COLOR_MUTED, use_color=use_color)
            + paint(value, fg=color, use_color=use_color)
        )

    field("Subscribers", subscriber_text, color=COLOR_YELLOW)
    field("Public videos", format_count(record.video_count), color=COLOR_GREEN)
    field("Total views", format_count(record.view_count), color=COLOR_MAGENTA)
    field(
        "Latest upload",
        latest_age(record),
        color=COLOR_GREEN if record.latest_upload else COLOR_MUTED,
    )
    if record.latest_upload:
        field("Latest video", record.latest_upload.title)
        field(
            "Video URL",
            f"https://www.youtube.com/watch?v={record.latest_upload.video_id}",
            color=COLOR_BLUE,
        )
    field(
        "Subscribed",
        human_age(record.subscribed_at) if record.subscribed_at else "unknown",
    )
    field(
        "Channel created",
        human_age(record.channel_created_at)
        if record.channel_created_at
        else "unknown",
    )
    field("Country", record.country or "not supplied")
    field("Channel URL", record.channel_url, color=COLOR_BLUE)

    lines.extend(["", paint("Description", fg=COLOR_MUTED, bold=True, use_color=use_color)])
    description = normalized_description(record.description) or "(none)"
    wrapped = textwrap.wrap(description, width=max(30, width - 4)) or ["(none)"]
    if len(wrapped) > 4:
        wrapped = wrapped[:4]
        wrapped[-1] = truncate(wrapped[-1] + "…", max(30, width - 4))
    lines.extend("  " + line for line in wrapped)
    lines.append(paint("━" * width, fg=COLOR_RULE, use_color=use_color))
    return "\n".join(lines)


def next_undecided_index(
    records: list[Subscription],
    decided_channel_ids: set[str],
    *,
    after: int = -1,
) -> int | None:
    """Find the next undecided record, wrapping once at the end."""
    for index in list(range(after + 1, len(records))) + list(range(0, after + 1)):
        if records[index].channel_id not in decided_channel_ids:
            return index
    return None


def write_inventory_backup(
    records: list[Subscription], backup_dir: Path
) -> Path:
    """Write a durable, timestamped pre-change snapshot of all subscriptions."""
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    created_at = datetime.now(timezone.utc)
    filename = created_at.strftime("subscriptions-%Y%m%dT%H%M%S.%fZ.json")
    destination = backup_dir / filename
    temporary = backup_dir / f".{filename}.tmp-{os.getpid()}"
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "subscription_count": len(records),
        "subscriptions": [asdict(record) for record in records],
    }
    try:
        file_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def append_unsubscribe_receipt(
    record: Subscription, *, backup_path: Path
) -> Path:
    """Append a durable receipt after a confirmed API deletion succeeds."""
    receipt_path = backup_path.parent / "unsubscribe-receipts.jsonl"
    receipt = {
        "unsubscribed_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "backed_up_in": backup_path.name,
        "subscription": asdict(record),
    }
    file_descriptor = os.open(
        receipt_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    with os.fdopen(file_descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(receipt_path, 0o600)
    return receipt_path


def save_channel_decisions(
    path: Path,
    channel_ids: set[str],
    records: list[Subscription],
    *,
    field: str,
) -> None:
    """Atomically persist one set of review decisions with channel names."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    by_id = {record.channel_id: record for record in records}
    decision_records = [
        {
            "channel_id": channel_id,
            "name": by_id[channel_id].name if channel_id in by_id else "unknown",
        }
        for channel_id in sorted(
            channel_ids,
            key=lambda value: by_id[value].name.casefold() if value in by_id else value,
        )
    ]
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        field: decision_records,
    }
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        file_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_channel_decisions(path: Path, *, field: str, description: str) -> set[str]:
    """Load one decision set, returning an empty set when it does not exist."""
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or not isinstance(
            payload.get(field), list
        ):
            raise ValueError("unsupported decision format")
        channel_ids = {
            item["channel_id"]
            for item in payload[field]
            if isinstance(item, dict) and isinstance(item.get("channel_id"), str)
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {description}: {path}") from error
    return channel_ids


def save_marked_channels(
    marks_path: Path,
    marked_channel_ids: set[str],
    records: list[Subscription],
) -> None:
    """Persist the staged unsubscribe queue."""
    save_channel_decisions(
        marks_path,
        marked_channel_ids,
        records,
        field="marked",
    )


def load_marked_channel_ids(marks_path: Path) -> set[str]:
    """Load the staged unsubscribe queue."""
    return load_channel_decisions(
        marks_path,
        field="marked",
        description="staged unsubscribe queue",
    )


def save_kept_channels(
    kept_path: Path,
    kept_channel_ids: set[str],
    records: list[Subscription],
) -> None:
    """Persist channels the user decided to keep."""
    save_channel_decisions(
        kept_path,
        kept_channel_ids,
        records,
        field="kept",
    )


def load_kept_channel_ids(kept_path: Path) -> set[str]:
    """Load channels the user decided to keep."""
    return load_channel_decisions(
        kept_path,
        field="kept",
        description="kept-channel decisions",
    )


def reset_review_decisions(marks_path: Path, kept_path: Path) -> Path:
    """Archive and clear both local decision sets without touching YouTube."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive_dir = marks_path.parent / "decision-history" / timestamp
    archive_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(archive_dir, 0o700)
    for source, destination_name in (
        (marks_path, "marked-for-unsubscribe.json"),
        (kept_path, "kept-subscriptions.json"),
    ):
        if source.exists():
            destination = archive_dir / destination_name
            shutil.copy2(source, destination)
            os.chmod(destination, 0o600)
    save_marked_channels(marks_path, set(), [])
    save_kept_channels(kept_path, set(), [])
    return archive_dir


def render_marked_queue(
    records: list[Subscription],
    marked_channel_ids: set[str],
    *,
    width: int,
    use_color: bool,
) -> str:
    """Render the entire staged queue in current subscription order."""
    marked_records = [
        (index, record)
        for index, record in enumerate(records, 1)
        if record.channel_id in marked_channel_ids
    ]
    if not marked_records:
        return paint("No channels are marked.", fg=COLOR_MUTED, use_color=use_color)
    lines = [
        paint(
            f"Marked for unsubscribe ({len(marked_records)})",
            fg=COLOR_RED,
            bold=True,
            use_color=use_color,
        ),
        paint("─" * min(width, 80), fg=COLOR_RULE, use_color=use_color),
    ]
    for global_index, record in marked_records:
        identity = record.name + (f"  {record.handle}" if record.handle else "")
        lines.append(
            f"{paint(f'{global_index:>4}', fg=COLOR_BLUE, use_color=use_color)}  "
            f"{paint(identity, fg=COLOR_RED, bold=True, use_color=use_color)}"
        )
    return "\n".join(lines)


def unsubscribe_marked(
    youtube: Any,
    records: list[Subscription],
    marked_channel_ids: set[str],
    *,
    backup_path: Path,
    marks_path: Path,
    limit: int,
) -> UnsubscribeBatchResult:
    """Delete a bounded marked batch, checkpointing each success and quota stop."""
    removed: list[Subscription] = []
    targets = [
        record for record in records if record.channel_id in marked_channel_ids
    ][:limit]
    for record in targets:
        try:
            youtube.subscriptions().delete(id=record.subscription_id).execute()
        except Exception as error:
            status, reason, message = google_api_error_details(error)
            quota_error = reason in {"dailyLimitExceeded", "quotaExceeded"} or (
                status == 403
                and isinstance(message, str)
                and "quota" in message.lower()
            )
            if quota_error:
                return UnsubscribeBatchResult(
                    removed=removed,
                    quota_exhausted=True,
                )
            raise
        append_unsubscribe_receipt(record, backup_path=backup_path)
        marked_channel_ids.discard(record.channel_id)
        records.remove(record)
        removed.append(record)
        save_marked_channels(marks_path, marked_channel_ids, records)
    return UnsubscribeBatchResult(removed=removed)


def interactive_review(
    youtube: Any,
    records: list[Subscription],
    *,
    include_latest: bool,
    allow_unsubscribe: bool,
    backup_path: Path,
    marks_path: Path,
    kept_path: Path,
    marked_channel_ids: set[str],
    kept_channel_ids: set[str],
    use_color: bool,
    max_unsubscribes: int,
) -> None:
    """Present one channel at a time for a persistent keep/unsubscribe decision."""
    if not records:
        print("No subscriptions were returned for this YouTube account.")
        return
    terminal_width = max(
        68,
        min(160, shutil.get_terminal_size(fallback=(110, 24)).columns),
    )
    decided_channel_ids = marked_channel_ids | kept_channel_ids
    cursor = next_undecided_index(records, decided_channel_ids)
    if cursor is None:
        cursor = 0

    def command_key(value: str) -> str:
        return paint(value, fg=COLOR_BLUE, bold=True, use_color=use_color)

    def print_help() -> None:
        print(
            f"{command_key('Enter/k')} keep   {command_key('u')} unsubscribe   "
            f"{command_key('←/p')} previous   {command_key('→')} next   "
            f"{command_key('x')} undecide"
        )
        print(
            f"{command_key('marked')} show unsubscribe list   "
            f"{command_key('apply')} apply list"
            f"{' (needs --allow-unsubscribe)' if not allow_unsubscribe else ''}   "
            f"{command_key('q')} quit"
        )

    while records:
        cursor = min(cursor, len(records) - 1)
        record = records[cursor]
        if include_latest and not record.latest_upload_checked:
            print(
                paint(
                    "Loading latest upload…",
                    fg=COLOR_MUTED,
                    dim=True,
                    use_color=use_color,
                )
            )
            ensure_latest_upload(youtube, record)

        current_channel_ids = {item.channel_id for item in records}
        decided_channel_ids = (
            marked_channel_ids | kept_channel_ids
        ) & current_channel_ids
        reviewed = len(decided_channel_ids)
        remaining = len(records) - reviewed
        decision = (
            "unsubscribe"
            if record.channel_id in marked_channel_ids
            else "keep" if record.channel_id in kept_channel_ids else None
        )
        print(
            "\n"
            + render_single_record(
                record,
                position=cursor + 1,
                total=len(records),
                reviewed=reviewed,
                remaining=remaining,
                marked_count=len(marked_channel_ids & current_channel_ids),
                decision=decision,
                width=terminal_width,
                use_color=use_color,
            )
        )
        print_help()
        try:
            prompt = paint(
                "decision > ",
                fg=COLOR_BLUE,
                bold=True,
                use_color=use_color,
            )
            command = read_interactive_command(prompt)
        except EOFError:
            print()
            return

        lowered = command.lower()
        if lowered in ("", "k", "keep"):
            marked_channel_ids.discard(record.channel_id)
            kept_channel_ids.add(record.channel_id)
            save_marked_channels(marks_path, marked_channel_ids, records)
            save_kept_channels(kept_path, kept_channel_ids, records)
            print(
                paint(
                    f'KEEP — "{record.name}"',
                    fg=COLOR_GREEN,
                    bold=True,
                    use_color=use_color,
                )
            )
        elif lowered in ("u", "unsubscribe"):
            kept_channel_ids.discard(record.channel_id)
            marked_channel_ids.add(record.channel_id)
            save_kept_channels(kept_path, kept_channel_ids, records)
            save_marked_channels(marks_path, marked_channel_ids, records)
            print(
                paint(
                    f'UNSUBSCRIBE LATER — "{record.name}"',
                    fg=COLOR_RED,
                    bold=True,
                    use_color=use_color,
                )
            )
        elif lowered in (KEY_LEFT, "p", "prev", "previous"):
            cursor = (cursor - 1) % len(records)
            continue
        elif lowered == KEY_RIGHT:
            cursor = (cursor + 1) % len(records)
            continue
        elif lowered in ("x", "undecide", "clear"):
            marked_channel_ids.discard(record.channel_id)
            kept_channel_ids.discard(record.channel_id)
            save_marked_channels(marks_path, marked_channel_ids, records)
            save_kept_channels(kept_path, kept_channel_ids, records)
            print(
                paint(
                    f'Decision cleared for "{record.name}".',
                    fg=COLOR_YELLOW,
                    use_color=use_color,
                )
            )
            continue
        elif lowered in ("q", "quit", "exit"):
            return
        elif lowered in ("?", "h", "help"):
            print_help()
            print(
                paint(
                    "The u decision only stages a channel. Only apply changes "
                    "your YouTube account.",
                    fg=COLOR_MUTED,
                    use_color=use_color,
                )
            )
            continue
        elif lowered in ("marked", "marks", "queue"):
            print(
                "\n"
                + render_marked_queue(
                    records,
                    marked_channel_ids,
                    width=terminal_width,
                    use_color=use_color,
                )
            )
            continue
        elif lowered == "apply":
            if not marked_channel_ids:
                print(paint("No channels are marked.", fg=COLOR_MUTED, use_color=use_color))
                continue
            if not allow_unsubscribe:
                print(
                    paint(
                        "Queue saved. Quit and rerun with --allow-unsubscribe, "
                        "then use apply.",
                        fg=COLOR_YELLOW,
                        use_color=use_color,
                    )
                )
                continue
            targets = [
                record
                for record in records
                if record.channel_id in marked_channel_ids
            ]
            batch_size = min(len(targets), max_unsubscribes)
            print(
                "\n"
                + render_marked_queue(
                    records,
                    marked_channel_ids,
                    width=terminal_width,
                    use_color=use_color,
                )
            )
            phrase = f"UNSUBSCRIBE {batch_size}"
            print(
                paint(
                    f"This apply will unsubscribe from {batch_size} of "
                    f"{len(targets)} staged channel(s). The pre-change inventory "
                    "backup is already saved.",
                    fg=COLOR_RED,
                    bold=True,
                    use_color=use_color,
                )
            )
            confirmation = input(
                f'Type {phrase} to confirm; anything else cancels: '
            ).strip()
            if confirmation != phrase:
                print(paint("Cancelled; nothing changed.", fg=COLOR_MUTED, use_color=use_color))
                continue
            result = unsubscribe_marked(
                youtube,
                records,
                marked_channel_ids,
                backup_path=backup_path,
                marks_path=marks_path,
                limit=max_unsubscribes,
            )
            remaining_staged = len(
                {
                    record.channel_id
                    for record in records
                    if record.channel_id in marked_channel_ids
                }
            )
            print(
                paint(
                    f"Unsubscribed from {len(result.removed)} channel(s). "
                    f"{remaining_staged} remain staged. Receipts appended to "
                    f"{backup_path.parent / 'unsubscribe-receipts.jsonl'}",
                    fg=COLOR_GREEN,
                    bold=True,
                    use_color=use_color,
                )
            )
            if result.quota_exhausted:
                print(
                    paint(
                        "YouTube's daily API quota is exhausted. Every successful "
                        "unsubscribe was saved; rerun after the quota resets at "
                        "midnight Pacific Time.",
                        fg=COLOR_YELLOW,
                        bold=True,
                        use_color=use_color,
                    )
                )
                return
            if not records:
                return
            cursor = min(cursor, len(records) - 1)
            continue
        else:
            print(
                paint(
                    "Unknown command. Use Enter, u, ←, →, marked, apply, or q.",
                    fg=COLOR_RED,
                    use_color=use_color,
                )
            )
            continue

        next_index = next_undecided_index(
            records,
            marked_channel_ids | kept_channel_ids,
            after=cursor,
        )
        if next_index is None:
            print(
                paint(
                    f"Review complete: {len(kept_channel_ids)} kept, "
                    f"{len(marked_channel_ids)} staged for unsubscribe.",
                    fg=COLOR_GREEN,
                    bold=True,
                    use_color=use_color,
                )
            )
            print(
                render_marked_queue(
                    records,
                    marked_channel_ids,
                    width=terminal_width,
                    use_color=use_color,
                )
            )
            if not allow_unsubscribe and marked_channel_ids:
                print(
                    "Rerun with --allow-unsubscribe when you are ready to apply "
                    "the staged list."
                )
                return
            if not marked_channel_ids:
                return
        else:
            cursor = next_index


def credentials_have_scope(credentials: Any, *, allow_unsubscribe: bool) -> bool:
    """Check whether cached credentials are sufficient for this mode."""
    granted = set(credentials.scopes or ())
    if allow_unsubscribe:
        return MANAGE_SCOPE in granted or FULL_SCOPE in granted
    return bool({READONLY_SCOPE, MANAGE_SCOPE, FULL_SCOPE} & granted)


def save_token(credentials: Any, token_path: Path) -> None:
    """Write the credential cache atomically with owner-only permissions."""
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(token_path.parent, 0o700)
    temporary = token_path.with_name(f".{token_path.name}.tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, token_path)
    os.chmod(token_path, 0o600)


def authenticate(
    client_secrets: Path, token_path: Path, *, allow_unsubscribe: bool
) -> Any:
    """Load, refresh, or obtain installed-app OAuth credentials."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as error:
        raise RuntimeError(
            "Missing Google API dependencies. Install requirements.txt in a virtual environment."
        ) from error

    scopes = [MANAGE_SCOPE if allow_unsubscribe else READONLY_SCOPE]
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path))
        if not credentials_have_scope(
            credentials, allow_unsubscribe=allow_unsubscribe
        ):
            credentials = None
            print("Cached authorization lacks the requested scope; reauthorization is required.")

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        if not client_secrets.is_file():
            raise RuntimeError(
                f"OAuth client file not found: {client_secrets}. See README.md for setup."
            )
        if allow_unsubscribe:
            print(
                "Management mode requests YouTube's broad force-ssl scope because the API "
                "does not offer a subscription-only delete scope."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes)
        credentials = flow.run_local_server(port=0, open_browser=True)

    save_token(credentials, token_path)
    return credentials


def build_youtube(credentials: Any) -> Any:
    """Construct the YouTube Data API client without discovery caching."""
    try:
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError(
            "Missing google-api-python-client. Install requirements.txt first."
        ) from error
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def safe_error_message(error: Exception) -> str:
    """Return a useful API error without dumping credential-bearing request data."""
    status, reason, message = google_api_error_details(error)
    if status and (message or reason):
        return f"YouTube API error {status}: {message or reason}"
    if status:
        return f"YouTube API error {status}"
    return f"{type(error).__name__}: {error}"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    try:
        if args.reset_decisions:
            archive_dir = reset_review_decisions(
                args.marks_file,
                args.kept_file,
            )
            print("Review decisions reset.")
            print(f"Previous decisions archived at: {archive_dir}")
            return 0
        credentials = authenticate(
            args.client_secrets,
            args.token,
            allow_unsubscribe=args.allow_unsubscribe,
        )
        youtube = build_youtube(credentials)
        print("Fetching subscriptions and channel statistics…")
        records = load_subscriptions(youtube, order=args.order)
        print(f"Loaded {len(records)} subscription(s).")
        backup_path = write_inventory_backup(records, args.backup_dir)
        print(f"Inventory backup saved: {backup_path}")
        marked_channel_ids = load_marked_channel_ids(args.marks_file)
        kept_channel_ids = load_kept_channel_ids(args.kept_file)
        current_channel_ids = {record.channel_id for record in records}
        active_marked_channel_ids = marked_channel_ids & current_channel_ids
        active_kept_channel_ids = (
            kept_channel_ids & current_channel_ids
        ) - active_marked_channel_ids
        if active_marked_channel_ids != marked_channel_ids or not args.marks_file.exists():
            save_marked_channels(
                args.marks_file,
                active_marked_channel_ids,
                records,
            )
        if active_kept_channel_ids != kept_channel_ids or not args.kept_file.exists():
            save_kept_channels(
                args.kept_file,
                active_kept_channel_ids,
                records,
            )
        if active_marked_channel_ids or active_kept_channel_ids:
            print(
                f"Restored decisions: {len(active_kept_channel_ids)} keep, "
                f"{len(active_marked_channel_ids)} unsubscribe."
            )
        interactive_review(
            youtube,
            records,
            include_latest=not args.no_last_upload,
            allow_unsubscribe=args.allow_unsubscribe,
            backup_path=backup_path,
            marks_path=args.marks_file,
            kept_path=args.kept_file,
            marked_channel_ids=active_marked_channel_ids,
            kept_channel_ids=active_kept_channel_ids,
            use_color=supports_color(disabled=args.no_color),
            max_unsubscribes=args.max_unsubscribes,
        )
        return 0
    except KeyboardInterrupt:
        print("\nCancelled; no pending action was applied.", file=sys.stderr)
        return 130
    except Exception as error:
        print(safe_error_message(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
