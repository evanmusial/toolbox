from __future__ import annotations

import configparser
import json
import math
import os
import re
import stat
import string
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import soupsieve
from lxml import etree


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    connect_timeout: float = 5
    read_timeout: float = 8
    request_timeout: float = 12
    retry_attempts: int = 3
    retry_budget: float = 25
    retry_backoff: float = 1
    retry_jitter: float = 0.25
    max_response_bytes: int = 1048576


@dataclass(frozen=True)
class Field:
    name: str
    path: str = ""
    selector: str = ""
    after_label: str = ""
    label_selector: str = "td"
    attribute: str = "text"
    multiple: bool = False
    required: bool = True
    join: str = ", "
    collapse_whitespace: bool = True
    html_unescape: bool = False
    regex: str = ""
    regex_group: int = 1


@dataclass(frozen=True)
class Message:
    layout: str = "field"
    title: str = "Now Playing"
    text: str = "{display_text}"
    fallback: str = "Song Change Detected"
    color_key: str = "{display_text}"


@dataclass(frozen=True)
class Station:
    id: str
    enabled: bool
    method: str
    url: str
    webhook_url: str = field(repr=False)
    poll_interval: float = 30
    identity: str = "{display_text}"
    initial_post: bool = False
    policy: Policy = field(default_factory=Policy)
    fields: tuple[Field, ...] = ()
    message: Message = field(default_factory=Message)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    namespaces: dict[str, str] = field(default_factory=dict)
    filename: str = ""


@dataclass(frozen=True)
class Configuration:
    path: Path
    conf_dir: Path
    state_db: Path
    log_level: str
    user_agent: str
    stations: tuple[Station, ...]

    @property
    def enabled(self):
        return tuple(s for s in self.stations if s.enabled)


POLICY_KEYS = set(Policy.__dataclass_fields__)
FIELD_KEYS = set(Field.__dataclass_fields__) - {"name"}
MESSAGE_KEYS = set(Message.__dataclass_fields__)
BUILTIN_FIELDS = {"station", "display_text", "title", "artists", "play_id", "image_url", "match_key"}


def quoted(value: str) -> str:
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            raise ConfigError("Invalid quoted configuration string") from None
        if not isinstance(parsed, str):
            raise ConfigError("Expected a quoted string")
        return parsed
    return value


def boolean(value: str, key: str) -> bool:
    lookup = {"true": True, "yes": True, "1": True, "false": False, "no": False, "0": False}
    try:
        return lookup[value.lower()]
    except KeyError:
        raise ConfigError(f"Invalid boolean for {key}") from None


def number(value: str, key: str, minimum=0, integer=False):
    try:
        result = int(value) if integer else float(value)
    except (ValueError, TypeError):
        raise ConfigError(f"Invalid number for {key}") from None
    if not math.isfinite(result) or result < minimum:
        raise ConfigError(f"Number out of range for {key}")
    return result


def reject_keys(values, allowed, context):
    extra = set(values) - set(allowed)
    if extra:
        raise ConfigError(f"Unknown options in {context}: {', '.join(sorted(extra))}")


def read_ini(path: Path) -> configparser.ConfigParser:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError(f"Configuration is not a regular file: {path.name}")
        if info.st_uid not in (0, os.geteuid()) or stat.S_IMODE(info.st_mode) & 0o027:
            raise ConfigError(f"Unsafe configuration permissions: {path.name}; use root:evan 0640")
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error, UnicodeError):
        raise ConfigError(f"Cannot read configuration: {path.name}") from None
    if parser.defaults():
        raise ConfigError(f"Use explicit sections, not DEFAULT: {path.name}")
    return parser


def policy(values, base=None):
    reject_keys(values, POLICY_KEYS, "HTTP policy")
    settings = {k: getattr(base or Policy(), k) for k in POLICY_KEYS}
    for key, value in values.items():
        integer = key in ("retry_attempts", "max_response_bytes")
        minimum = 1 if integer else (0 if key in ("retry_backoff", "retry_jitter") else 0.001)
        settings[key] = number(value, key, minimum, integer)
    if settings["retry_attempts"] > 10:
        raise ConfigError("retry_attempts must be between 1 and 10")
    if not 1024 <= settings["max_response_bytes"] <= 10 * 1024 * 1024:
        raise ConfigError("max_response_bytes must be between 1024 and 10485760")
    return Policy(**settings)


def validate_template(template, names):
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError:
        raise ConfigError("Malformed message or identity template") from None
    for _, key, spec, conversion in parts:
        if key is not None and (key not in names or spec or conversion):
            raise ConfigError("Templates support named fields only, without indexing or format conversions")


def valid_http_url(value, context, slack=False):
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password
        if slack:
            valid = valid and parsed.scheme == "https" and parsed.hostname in ("hooks.slack.com", "hooks.slack-gov.com") and parsed.path.startswith("/services/")
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError(f"Invalid {context}; value omitted")


def read_station(path: Path, defaults: Policy) -> Station:
    parser = read_ini(path)
    allowed = {"station", "http", "headers", "namespaces", "message"}
    for section in parser.sections():
        if section not in allowed and not section.startswith("field."):
            raise ConfigError(f"Unknown section in {path.name}")
    if "station" not in parser:
        raise ConfigError(f"Missing station section in {path.name}")
    values = dict(parser["station"])
    reject_keys(values, {"id", "enabled", "method", "url", "webhook_url", "poll_interval", "identity", "initial_post"}, path.name)
    station_id = values.get("id", "")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", station_id):
        raise ConfigError(f"Invalid station id in {path.name}")
    enabled = boolean(values.get("enabled", "true"), "enabled")
    method = values.get("method", "")
    if method not in ("json", "html", "xml"):
        raise ConfigError(f"Invalid extraction method in {path.name}")
    url = values.get("url", "")
    webhook = values.get("webhook_url", "")
    valid_http_url(url, "source URL")
    if enabled:
        valid_http_url(webhook, "Slack webhook URL", slack=True)
    fields = []
    namespaces = dict(parser["namespaces"]) if "namespaces" in parser else {}
    for section in parser.sections():
        if not section.startswith("field."):
            continue
        name = section[6:]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in ("station", "match_key"):
            raise ConfigError(f"Invalid field name in {path.name}")
        options = dict(parser[section])
        reject_keys(options, FIELD_KEYS, section)
        converted = {k: quoted(v) for k, v in options.items()}
        for key in ("multiple", "required", "collapse_whitespace", "html_unescape"):
            if key in options:
                converted[key] = boolean(options[key], key)
        if "regex_group" in options:
            converted["regex_group"] = number(options["regex_group"], "regex_group", 0, True)
        spec = Field(name=name, **converted)
        try:
            if method == "json":
                if spec.path and not spec.path.startswith("/"):
                    raise ConfigError("JSON paths use JSON Pointer syntax beginning with /")
                if re.search(r"~(?![01])", spec.path):
                    raise ConfigError("Invalid JSON Pointer escape")
            elif method == "html":
                if not spec.selector:
                    raise ConfigError("HTML extraction requires a selector")
                soupsieve.compile(spec.selector)
                soupsieve.compile(spec.label_selector)
            else:
                if not spec.path:
                    raise ConfigError("XML extraction requires an XPath")
                etree.XPath(spec.path, namespaces=namespaces)
            if spec.regex:
                expression = re.compile(spec.regex)
                if spec.regex_group > expression.groups:
                    raise ConfigError("regex_group exceeds the expression's capture groups")
        except ConfigError:
            raise
        except Exception:
            raise ConfigError(f"Invalid extraction expression in {path.name}:{section}") from None
        fields.append(spec)
    if not fields:
        raise ConfigError(f"No extraction fields in {path.name}")
    names = BUILTIN_FIELDS | {f.name for f in fields}
    if not {f.name for f in fields} & {"title", "display_text"}:
        raise ConfigError("Map title or display_text in every station")
    message_values = dict(parser["message"]) if "message" in parser else {}
    reject_keys(message_values, MESSAGE_KEYS, "message")
    message = Message(**{k: quoted(v) for k, v in message_values.items()})
    if message.layout not in ("field", "section"):
        raise ConfigError("Message layout must be field or section")
    identity = quoted(values.get("identity", "{display_text}"))
    for template in (identity, message.title, message.text, message.fallback, message.color_key):
        validate_template(template, names)
    return Station(
        id=station_id, enabled=enabled, method=method, url=url,
        webhook_url=webhook,
        poll_interval=number(values.get("poll_interval", "30"), "poll_interval", 1),
        identity=identity,
        initial_post=boolean(values.get("initial_post", "false"), "initial_post"),
        policy=policy(dict(parser["http"]) if "http" in parser else {}, defaults),
        fields=tuple(fields), message=message,
        headers=dict(parser["headers"]) if "headers" in parser else {},
        namespaces=namespaces, filename=str(path),
    )


def load_configuration(path: Path | str) -> Configuration:
    path = Path(path).resolve()
    parser = read_ini(path)
    if set(parser.sections()) - {"daemon", "defaults"} or "daemon" not in parser:
        raise ConfigError("Main configuration requires daemon and optional defaults sections")
    values = dict(parser["daemon"])
    reject_keys(values, {"conf_dir", "state_db", "log_level", "user_agent"}, "daemon")
    conf_dir = Path(values.get("conf_dir", str(path.parent / "conf.d")))
    state_db = Path(values.get("state_db", "/var/lib/radio-now-playing/state.sqlite3"))
    if not conf_dir.is_absolute() or not state_db.is_absolute():
        raise ConfigError("conf_dir and state_db must be absolute paths")
    level = values.get("log_level", "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("Invalid log_level")
    defaults = policy(dict(parser["defaults"]) if "defaults" in parser else {})
    if not conf_dir.is_dir():
        raise ConfigError("Station configuration directory is missing")
    stations = tuple(read_station(p, defaults) for p in sorted(conf_dir.glob("*.conf")))
    if len({s.id for s in stations}) != len(stations):
        raise ConfigError("Duplicate station id")
    if not any(s.enabled for s in stations):
        raise ConfigError("At least one station must be enabled")
    return Configuration(path, conf_dir, state_db, level, values.get("user_agent", "evan-radio-now-playing/1.0"), stations)


def summary(config: Configuration):
    return {
        "configuration": str(config.path), "state_db": str(config.state_db),
        "stations": [{"id": s.id, "enabled": s.enabled, "method": s.method,
                      "poll_interval": s.poll_interval, "retry_attempts": s.policy.retry_attempts,
                      "retry_budget": s.policy.retry_budget, "webhook_configured": bool(s.webhook_url)}
                     for s in config.stations],
    }
