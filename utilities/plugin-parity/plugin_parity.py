#!/usr/bin/env python3
"""Inventory macOS audio plug-ins and compare parity between two machines."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import sys
import tempfile
import unicodedata


SCRIPT_VERSION = "2026.11.19"
TOOL_DIR = Path(__file__).resolve().parent
BUNDLE_FORMATS = {
    ".component": "AU",
    ".appex": "AUv3",
    ".vst": "VST",
    ".vst3": "VST3",
    ".aaxplugin": "AAX",
}
FORMAT_ALIASES = {
    "au": "AU", "auv2": "AU", "audio unit": "AU",
    "au3": "AUv3", "auv3": "AUv3",
    "vst": "VST", "vst2": "VST", "vst3": "VST3", "aax": "AAX",
}
NAME_FIELDS = ("name", "plugin", "plug-in", "title", "display_name")
VENDOR_FIELDS = ("manufacturer", "vendor", "creator", "maker")


class ParityError(Exception):
    """Report a user-facing inventory, comparison, or input error."""


class Palette:
    """Apply a restrained 256-color palette when terminal color is enabled."""

    def __init__(self, enabled):
        """Select colored or plain rendering."""
        self.enabled = enabled

    def paint(self, text, code):
        """Wrap text in one ANSI style when enabled."""
        return f"\033[{code}m{text}\033[0m" if self.enabled else str(text)

    def title(self, text):
        """Render a high-contrast report title."""
        return self.paint(text, "1;38;5;255")

    def manufacturer(self, text):
        """Render a manufacturer group heading."""
        return self.paint(text, "1;38;5;141")

    def missing(self, text):
        """Render missing-item markers and counts."""
        return self.paint(text, "38;5;203")

    def good(self, text):
        """Render parity and matched values."""
        return self.paint(text, "38;5;114")

    def format(self, text):
        """Render format labels without overpowering names."""
        return self.paint(text, "38;5;81")

    def warning(self, text):
        """Render version-difference values."""
        return self.paint(text, "38;5;214")

    def dim(self, text):
        """Render secondary counts and guidance."""
        return self.paint(text, "38;5;245")


def host_name():
    """Return a stable short host name for snapshot filenames."""
    return socket.gethostname().split(".", 1)[0]


def normalize_text(value):
    """Return a conservative comparison token for a human-readable value."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[\u2122\u00ae\u00a9]", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_format(value):
    """Normalize a plug-in format label while retaining unknown labels."""
    if not value:
        return None
    text = str(value).strip()
    return FORMAT_ALIASES.get(text.casefold(), text.upper())


def clean_manufacturer(value):
    """Reject version/build strings and recover vendors from copyright metadata."""
    if not value:
        return None
    text = str(value).strip()
    if "©" in text:
        copyright_vendor = text.rsplit("©", 1)[1].strip(" .,;-")
        if copyright_vendor:
            return copyright_vendor
    if re.fullmatch(r"v?\d[\w.() -]*(?:build[\w .-]*)?", text, re.I):
        return None
    if "::" in text and re.search(r"\d", text):
        return None
    return text or None


def default_roots(home=None):
    """Return standard system, user, and disabled plug-in search roots."""
    home = Path.home() if home is None else Path(home)
    return [
        {"format": "AU", "path": Path("/Library/Audio/Plug-Ins/Components"),
         "scope": "system", "enabled": True},
        {"format": "AU", "path": home / "Library/Audio/Plug-Ins/Components",
         "scope": "user", "enabled": True},
        {"format": "AU", "path": Path("/System/Library/Components"),
         "scope": "macOS", "enabled": True},
        {"format": "VST", "path": Path("/Library/Audio/Plug-Ins/VST"),
         "scope": "system", "enabled": True},
        {"format": "VST", "path": home / "Library/Audio/Plug-Ins/VST",
         "scope": "user", "enabled": True},
        {"format": "VST3", "path": Path("/Library/Audio/Plug-Ins/VST3"),
         "scope": "system", "enabled": True},
        {"format": "VST3", "path": home / "Library/Audio/Plug-Ins/VST3",
         "scope": "user", "enabled": True},
        {"format": "AAX", "path": Path("/Library/Application Support/Avid/Audio/Plug-Ins"),
         "scope": "system", "enabled": True},
        {"format": "AAX", "path": home / "Library/Application Support/Avid/Audio/Plug-Ins",
         "scope": "user", "enabled": True},
        {"format": "AAX", "path": Path("/Library/Application Support/Avid/Audio/Plug-Ins (Unused)"),
         "scope": "system-disabled", "enabled": False},
        {"format": "AAX", "path": home / "Library/Application Support/Avid/Audio/Plug-Ins (Unused)",
         "scope": "user-disabled", "enabled": False},
    ]


def parse_root(value):
    """Parse a FORMAT=PATH command-line root specification."""
    if "=" not in value:
        raise argparse.ArgumentTypeError("root must be FORMAT=PATH")
    raw_format, raw_path = value.split("=", 1)
    plugin_format = normalize_format(raw_format)
    if plugin_format not in set(BUNDLE_FORMATS.values()):
        raise argparse.ArgumentTypeError("format must be AU, AUv3, VST, VST3, or AAX")
    if not raw_path.strip():
        raise argparse.ArgumentTypeError("root path cannot be empty")
    return {"format": plugin_format, "path": Path(raw_path).expanduser(),
            "scope": "custom", "enabled": True}


def load_plist(bundle):
    """Read a bundle Info.plist without executing the bundle."""
    for candidate in (bundle / "Contents/Info.plist", bundle / "Info.plist"):
        try:
            with candidate.open("rb") as stream:
                value = plistlib.load(stream)
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, plistlib.InvalidFileException):
            continue
    return {}


def bundle_display_name(bundle, info):
    """Choose a stable display name from metadata and the bundle filename."""
    for key in ("CFBundleDisplayName", "CFBundleName", "CFBundleExecutable"):
        if info.get(key):
            return str(info[key]).strip()
    return bundle.stem


def bundle_vendor(info):
    """Return vendor metadata when the bundle publishes an explicit value."""
    for key in ("VST3Vendor", "AAXManufacturer", "Manufacturer", "CFBundleGetInfoString"):
        value = info.get(key)
        if not value:
            continue
        text = str(value).strip()
        if key == "CFBundleGetInfoString" and "," in text:
            text = text.split(",", 1)[0].strip()
        manufacturer = clean_manufacturer(text)
        if manufacturer:
            return manufacturer
    return None


def base_record(bundle, plugin_format, info, root):
    """Build metadata shared by all identities exposed by one bundle."""
    version = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
    return {
        "name": bundle_display_name(bundle, info),
        "manufacturer": bundle_vendor(info),
        "format": plugin_format,
        "version": str(version) if version is not None else None,
        "build": str(info.get("CFBundleVersion")) if info.get("CFBundleVersion") is not None else None,
        "bundle_id": info.get("CFBundleIdentifier"),
        "path": str(bundle.resolve()),
        "scope": root["scope"],
        "enabled": root["enabled"],
        "identity": None,
        "source": "filesystem",
        "enumeration_level": "bundle",
    }


def au_records(bundle, plugin_format, info, root):
    """Expand an AU bundle's declared AudioComponents into identities."""
    base = base_record(bundle, plugin_format, info, root)
    components = info.get("AudioComponents")
    if not isinstance(components, list) or not components:
        return [base]
    records = []
    for component in components:
        if not isinstance(component, dict):
            continue
        record = dict(base)
        raw_name = component.get("name") or base["name"]
        if isinstance(raw_name, str) and ":" in raw_name:
            vendor, name = raw_name.split(":", 1)
            record["manufacturer"] = vendor.strip() or record["manufacturer"]
            record["name"] = name.strip() or base["name"]
        else:
            record["name"] = str(raw_name).strip()
        identity = {
            "type": component.get("type"),
            "subtype": component.get("subtype"),
            "manufacturer": component.get("manufacturer"),
        }
        record["identity"] = identity if any(identity.values()) else None
        record["enumeration_level"] = "component"
        records.append(record)
    return records or [base]


def moduleinfo_path(bundle):
    """Return the first VST3 moduleinfo.json path present in a bundle."""
    candidates = (
        bundle / "Contents/Resources/moduleinfo.json",
        bundle / "Contents/Resources/ModuleInfo.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def vst3_records(bundle, info, root, warnings):
    """Expand public VST3 module metadata when the bundle supplies it."""
    base = base_record(bundle, "VST3", info, root)
    path = moduleinfo_path(bundle)
    if not path:
        return [base]
    try:
        module = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        warnings.append(f"Could not read {path}: {error}")
        return [base]
    factory = module.get("Factory Info") if isinstance(module, dict) else None
    if isinstance(factory, dict):
        base["manufacturer"] = factory.get("Vendor") or base["manufacturer"]
    classes = module.get("Classes") if isinstance(module, dict) else None
    if not isinstance(classes, list) or not classes:
        return [base]
    records = []
    for plugin_class in classes:
        if not isinstance(plugin_class, dict):
            continue
        record = dict(base)
        record["name"] = str(plugin_class.get("Name") or base["name"]).strip()
        cid = plugin_class.get("CID")
        record["identity"] = {"cid": str(cid)} if cid else None
        record["enumeration_level"] = "class"
        records.append(record)
    return records or [base]


def is_audio_unit_extension(info):
    """Recognize an AUv3 application extension from its property list."""
    if isinstance(info.get("AudioComponents"), list):
        return True
    extension = info.get("NSExtension")
    if not isinstance(extension, dict):
        return False
    point = str(extension.get("NSExtensionPointIdentifier") or "")
    return "AudioUnit" in point or point == "com.apple.AudioUnit"


def iter_bundles(root, expected_format, warnings=None):
    """Yield matching plug-in bundles recursively without descending into them."""
    path = Path(root)
    if path.is_file() or path.suffix.casefold() in BUNDLE_FORMATS:
        if BUNDLE_FORMATS.get(path.suffix.casefold()) == expected_format:
            yield path
        return
    if not path.is_dir():
        return
    def on_error(error):
        """Retain inaccessible-path evidence instead of silently omitting it."""
        if warnings is not None:
            warnings.append(f"Could not scan {error.filename}: {error.strerror}")

    for current, directories, _files in os.walk(path, onerror=on_error):
        current_path = Path(current)
        keep = []
        for directory in directories:
            candidate = current_path / directory
            candidate_format = BUNDLE_FORMATS.get(candidate.suffix.casefold())
            if candidate_format:
                if candidate_format == expected_format:
                    yield candidate
            else:
                keep.append(directory)
        directories[:] = keep


def iter_auv3_bundles(application_roots, warnings=None):
    """Yield AUv3 extensions found inside installed application bundles."""
    seen = set()
    for root in application_roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        def on_error(error):
            """Retain inaccessible application paths as scan warnings."""
            if warnings is not None:
                warnings.append(f"Could not scan {error.filename}: {error.strerror}")

        for current, directories, _files in os.walk(root, onerror=on_error):
            current_path = Path(current)
            apps = [current_path / directory for directory in directories
                    if directory.casefold().endswith(".app")]
            directories[:] = [directory for directory in directories
                               if not directory.casefold().endswith(".app")]
            for app in apps:
                plugins = app / "Contents/PlugIns"
                if not plugins.is_dir():
                    continue
                for extension in plugins.rglob("*.appex"):
                    resolved = str(extension.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        yield extension


def scan_installed(roots, application_roots=(), include_auv3=True):
    """Scan bundle metadata in selected roots and return records and warnings."""
    records = []
    warnings = []
    searched = []
    seen = set()
    for root in roots:
        path = Path(root["path"]).expanduser()
        searched.append({**root, "path": str(path), "exists": path.exists()})
        for bundle in iter_bundles(path, root["format"], warnings):
            key = (root["format"], str(bundle.resolve()))
            if key in seen:
                continue
            seen.add(key)
            info = load_plist(bundle)
            if root["format"] in ("AU", "AUv3"):
                records.extend(au_records(bundle, root["format"], info, root))
            elif root["format"] == "VST3":
                records.extend(vst3_records(bundle, info, root, warnings))
            else:
                records.append(base_record(bundle, root["format"], info, root))
    if include_auv3:
        auv3_root = {"format": "AUv3", "scope": "application", "enabled": True}
        for bundle in iter_auv3_bundles(application_roots, warnings):
            key = ("AUv3", str(bundle.resolve()))
            if key in seen:
                continue
            info = load_plist(bundle)
            if not is_audio_unit_extension(info):
                continue
            seen.add(key)
            records.extend(au_records(bundle, "AUv3", info, auv3_root))
    records.sort(key=lambda item: (
        item["format"], normalize_text(item.get("manufacturer")),
        normalize_text(item["name"]), item["path"], json.dumps(item.get("identity"), sort_keys=True),
    ))
    return records, searched, warnings


def first_value(mapping, fields):
    """Return the first non-empty value from alternate field names."""
    for field in fields:
        value = mapping.get(field)
        if value not in (None, ""):
            return value
    return None


def coerce_record(item, source, default_format=None):
    """Convert a foreign inventory row to the common comparison shape."""
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict):
        return None
    name = first_value(item, NAME_FIELDS)
    if not name:
        return None
    plugin_format = normalize_format(item.get("format") or item.get("plugin_format") or default_format)
    return {
        "name": str(name).strip(),
        "manufacturer": first_value(item, VENDOR_FIELDS),
        "format": plugin_format,
        "version": item.get("version"),
        "build": item.get("build"),
        "bundle_id": item.get("bundle_id") or item.get("bundle_identifier"),
        "path": item.get("path"),
        "scope": item.get("scope"),
        "enabled": item.get("enabled"),
        "identity": item.get("identity") or item.get("dev_identifier") or item.get("class_id"),
        "source": source,
        "enumeration_level": item.get("enumeration_level") or "reported",
    }


def records_from_json(value, source):
    """Extract plug-in rows from this tool, Live exports, or a JSON list."""
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, dict):
        candidates = None
        for key in ("master_plugins", "installed_plugins", "plugins", "records", "inventory"):
            if isinstance(value.get(key), list):
                candidates = value[key]
                break
        if candidates is None:
            raise ParityError("JSON inventory has no supported plug-in record list.")
    else:
        raise ParityError("JSON inventory must contain an object or list.")
    return [record for item in candidates if (record := coerce_record(item, source))]


def records_from_delimited(path, source, delimiter):
    """Read a CSV or TSV inventory with common field names."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        return [record for row in reader if (record := coerce_record(row, source))]


def records_from_text(text, source):
    """Read Live's grouped text export or a one-name-per-line checklist."""
    records = []
    current_vendor = None
    current = None
    live_variant = re.compile(r"^\s{4}(.+?) \[(AU|AUv3|VST|VST3|AAX)\]\s*$", re.I)
    vendor_heading = re.compile(r"^(.+?) \(\d+ named groups?\)\s*$")
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        vendor_match = vendor_heading.match(line)
        if vendor_match:
            current_vendor = vendor_match.group(1).strip()
            continue
        variant_match = live_variant.match(line)
        if variant_match:
            current = coerce_record(
                {"name": variant_match.group(1), "manufacturer": current_vendor,
                 "format": variant_match.group(2)}, source,
            )
            records.append(current)
            continue
        if current and line.startswith("      Version:"):
            value = line.split(":", 1)[1].strip()
            current["version"] = value.split(" (", 1)[0] if value != "unknown" else None
            continue
        if current and line.startswith("      Path:"):
            value = line.split(":", 1)[1].strip()
            current["path"] = value if value != "unknown" else None
    if records:
        return records
    simple = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(.+?)\s*\[(AU|AUv3|VST|VST3|AAX)\]\s*$", line, re.I)
        item = {"name": match.group(1), "format": match.group(2)} if match else {"name": line}
        record = coerce_record(item, source)
        if record:
            simple.append(record)
    return simple


def load_inventory(path, source):
    """Load JSON, CSV, TSV, or text inventory records from disk."""
    path = Path(path).expanduser()
    try:
        suffix = path.suffix.casefold()
        if suffix == ".json":
            return records_from_json(json.loads(path.read_text(encoding="utf-8-sig")), source)
        if suffix == ".csv":
            return records_from_delimited(path, source, ",")
        if suffix == ".tsv":
            return records_from_delimited(path, source, "\t")
        return records_from_text(path.read_text(encoding="utf-8-sig"), source)
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as error:
        raise ParityError(f"Could not read {path}: {error}") from error


def identity_token(value):
    """Serialize a format-specific identity for exact comparisons."""
    if value in (None, "", {}):
        return ""
    if isinstance(value, dict):
        au_fields = (value.get("type"), value.get("subtype"), value.get("manufacturer"))
        if all(au_fields):
            return "au:" + ":".join(str(field) for field in au_fields)
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    text = str(value)
    au_match = re.match(
        r"^device:au(?:v2|v3)?:[^:]+:(\d+):(\d+):(\d+)$", text, re.I,
    )
    if au_match:
        manufacturer, subtype, component_type = (
            int(part).to_bytes(4, "big").decode("latin-1") for part in au_match.groups()
        )
        return f"au:{component_type}:{subtype}:{manufacturer}"
    return text.casefold()


def enrich_manufacturers(records):
    """Use authoritative Live vendors across same-name format variants."""
    authoritative = {}
    fallback = {}
    for record in records:
        name = normalize_text(record.get("name"))
        live_record = record.get("live_record")
        live_manufacturer = clean_manufacturer(
            live_record.get("manufacturer") if isinstance(live_record, dict) else None
        )
        manufacturer = clean_manufacturer(record.get("manufacturer"))
        if not name:
            continue
        if live_manufacturer:
            authoritative.setdefault(name, set()).add(live_manufacturer)
        elif record.get("source") == "live" and manufacturer:
            authoritative.setdefault(name, set()).add(manufacturer)
        elif manufacturer:
            fallback.setdefault(name, set()).add(manufacturer)
    enriched = []
    for record in records:
        entry = dict(record)
        name = normalize_text(record.get("name"))
        candidates = authoritative.get(name, set()) or fallback.get(name, set())
        if len(candidates) == 1:
            entry["manufacturer"] = next(iter(candidates))
            entry["manufacturer_source"] = "same_name_cross_format"
        else:
            entry["manufacturer"] = clean_manufacturer(entry.get("manufacturer"))
        enriched.append(entry)
    return enriched


def shell_family_token(record):
    """Identify versioned WaveShell container bundles without folding products."""
    if record.get("shell_family") == "waveshell":
        return ("shell-family", normalize_format(record.get("format")) or "*", "waveshell")
    name = str(record.get("name") or "").strip().casefold()
    if re.match(r"^waveshell\d*-(?:au|vst3?|aax)(?:\s|$)", name):
        return ("shell-family", normalize_format(record.get("format")) or "*", "waveshell")
    return None


def collapse_shell_containers(records):
    """Represent many historical WaveShell versions once per plug-in format."""
    collapsed = []
    shell_formats = {}
    collapsed_count = 0
    for record in records:
        token = shell_family_token(record)
        if not token:
            collapsed.append(record)
            continue
        plugin_format = token[1]
        if plugin_format in shell_formats:
            collapsed_count += 1
            continue
        entry = dict(record)
        entry.update({
            "name": "WaveShell container",
            "manufacturer": "Waves",
            "version": None,
            "identity": None,
            "bundle_id": None,
            "shell_family": "waveshell",
        })
        shell_formats[plugin_format] = entry
        collapsed.append(entry)
    return collapsed, collapsed_count


def installed_shell_format(record):
    """Return the format when a record proves that a WaveShell bundle exists."""
    path_name = str(record.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = str(record.get("name") or "")
    if re.match(r"^waveshell\d*-(?:au|vst3?|aax)(?:[ .]|$)", path_name, re.I):
        return normalize_format(record.get("format"))
    if shell_family_token(record):
        return normalize_format(record.get("format"))
    return None


def record_keys(record):
    """Return strongest-to-weakest keys suitable for migration comparison."""
    plugin_format = normalize_format(record.get("format")) or "*"
    name = normalize_text(record.get("name"))
    vendor = normalize_text(record.get("manufacturer"))
    keys = []
    identity = identity_token(record.get("identity"))
    if identity:
        keys.append(("identity", plugin_format, identity))
    shell_family = shell_family_token(record)
    if shell_family:
        keys.append(shell_family)
    bundle_id = normalize_text(record.get("bundle_id"))
    if bundle_id:
        keys.append(("bundle", plugin_format, bundle_id, name))
    if name and vendor:
        keys.append(("vendor-name", plugin_format, vendor, name))
    if name:
        keys.append(("name", plugin_format, name))
        keys.append(("any-format-name", name))
    return keys


def match_records(expected, actual):
    """Match expected records to installed records with format-aware keys."""
    index = {}
    installed_shells = {}
    for position, record in enumerate(actual):
        for key in record_keys(record):
            index.setdefault(key, set()).add(position)
        shell_format = installed_shell_format(record)
        if shell_format:
            installed_shells.setdefault(shell_format, set()).add(position)
    matched = []
    gaps = []
    for record in expected:
        shell_family = shell_family_token(record)
        candidates = set(installed_shells.get(shell_family[1], ())) if shell_family else set()
        keys = record_keys(record)
        if not record.get("format"):
            keys = [key for key in keys if key[0] == "any-format-name"]
        if not candidates:
            for key in keys:
                candidates.update(index.get(key, ()))
                if candidates:
                    break
        entry = dict(record)
        if candidates:
            entry["matches"] = [actual[position] for position in sorted(candidates)]
            matched.append(entry)
        else:
            gaps.append(entry)
    return matched, gaps


def merge_current(installed, live):
    """Merge filesystem and Live records while preserving source evidence."""
    installed_matches, live_only = match_records(live, installed)
    matched_installed = set()
    for live_record in installed_matches:
        for record in live_record["matches"]:
            matched_installed.add((record.get("format"), record.get("path"), record.get("name")))
    filesystem_only = [record for record in installed if (
        record.get("format"), record.get("path"), record.get("name")) not in matched_installed]
    master = []
    for record in installed:
        entry = dict(record)
        entry["sources"] = ["filesystem"]
        master.append(entry)
    for live_record in live:
        candidates, _gaps = match_records([live_record], installed)
        if candidates:
            for installed_record in candidates[0]["matches"]:
                target = next(item for item in master if item.get("path") == installed_record.get("path")
                              and item.get("name") == installed_record.get("name")
                              and item.get("format") == installed_record.get("format"))
                if "live" not in target["sources"]:
                    target["sources"].append("live")
                target["manufacturer"] = target.get("manufacturer") or live_record.get("manufacturer")
                target["version"] = target.get("version") or live_record.get("version")
                target["live_record"] = live_record
        else:
            entry = dict(live_record)
            entry["sources"] = ["live"]
            master.append(entry)
    master.sort(key=lambda item: (
        normalize_text(item.get("manufacturer")), normalize_text(item.get("name")),
        item.get("format") or "", item.get("path") or "",
    ))
    return master, filesystem_only, live_only


def build_snapshot(installed, searched_roots, live, warnings, computer):
    """Build a durable host snapshot from filesystem and Live evidence."""
    master, filesystem_only, live_only = merge_current(installed, live)
    formats = Counter(record.get("format") or "unknown" for record in installed)
    return {
        "schema_version": 1,
        "tool": "plugin_parity.py",
        "tool_version": SCRIPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "computer": computer,
        "scope": "Bundle-metadata inventory; plug-in binaries are not loaded or executed.",
        "summary": {
            "installed_records": len(installed),
            "installed_by_format": dict(sorted(formats.items())),
            "live_records": len(live),
            "master_records": len(master),
            "filesystem_only_records": len(filesystem_only),
            "live_only_records": len(live_only),
        },
        "searched_roots": searched_roots,
        "warnings": warnings,
        "installed_plugins": installed,
        "live_plugins": live,
        "master_plugins": master,
        "filesystem_only": filesystem_only,
        "live_only": live_only,
    }


def load_snapshot(path):
    """Read a plugin-parity snapshot and validate its master record list."""
    path = Path(path).expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ParityError(f"Could not read snapshot {path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("master_plugins"), list):
        raise ParityError(f"{path} is not a plugin-parity snapshot.")
    return value


def compare_snapshots(baseline, candidate):
    """Compare a baseline host snapshot against a candidate host snapshot."""
    enriched_expected = enrich_manufacturers(baseline["master_plugins"])
    all_expected, collapsed_shell_records = collapse_shell_containers(enriched_expected)
    ignored = [record for record in all_expected
               if record.get("sources") == ["live"] and not record.get("path")]
    expected = [record for record in all_expected if record not in ignored]
    actual = enrich_manufacturers(candidate["master_plugins"])
    matched, gaps = match_records(expected, actual)
    additional_matched, additional = match_records(actual, expected)
    del additional_matched
    version_differences = []
    for entry in matched:
        if shell_family_token(entry):
            continue
        baseline_version = entry.get("version")
        candidate_versions = sorted({str(match["version"]) for match in entry["matches"]
                                     if match.get("version")})
        if baseline_version and candidate_versions and str(baseline_version) not in candidate_versions:
            version_differences.append({
                "name": entry["name"],
                "manufacturer": entry.get("manufacturer"),
                "format": entry.get("format"),
                "baseline_version": baseline_version,
                "candidate_versions": candidate_versions,
            })
    gap_formats = Counter(record.get("format") or "unspecified" for record in gaps)
    return {
        "schema_version": 1,
        "tool": "plugin_parity.py",
        "tool_version": SCRIPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {
            "computer": baseline.get("computer"),
            "generated_at": baseline.get("generated_at"),
            "records": len(expected),
        },
        "candidate": {
            "computer": candidate.get("computer"),
            "generated_at": candidate.get("generated_at"),
            "records": len(actual),
        },
        "summary": {
            "baseline_records": len(expected),
            "baseline_raw_records": len(all_expected),
            "ignored_unresolved_live_records": len(ignored),
            "collapsed_shell_records": collapsed_shell_records,
            "candidate_records": len(actual),
            "matched": len(matched),
            "gaps": len(gaps),
            "gaps_by_format": dict(sorted(gap_formats.items())),
            "additional_candidate_records": len(additional),
            "version_differences": len(version_differences),
            "parity_percent": round(100.0 * len(matched) / len(expected), 2) if expected else 100.0,
        },
        "gaps": gaps,
        "ignored_unresolved_live_records": ignored,
        "matched": matched,
        "additional_candidate_records": additional,
        "version_differences": version_differences,
    }


def record_manufacturer(record):
    """Return the best available manufacturer label for display grouping."""
    live_record = record.get("live_record")
    live_manufacturer = clean_manufacturer(
        live_record.get("manufacturer") if isinstance(live_record, dict) else None
    )
    manufacturer = live_manufacturer or clean_manufacturer(record.get("manufacturer"))
    return manufacturer or "Unknown manufacturer"


def grouped_products(records):
    """Fold repeated records into manufacturer/name rows with combined formats."""
    groups = {}
    for record in records:
        manufacturer = record_manufacturer(record)
        manufacturer_key = normalize_text(manufacturer)
        name = str(record.get("name") or "Unnamed plug-in").strip()
        product_key = normalize_text(name) or name.casefold()
        group = groups.setdefault(manufacturer_key, {
            "manufacturer": manufacturer, "records": 0, "products": {},
        })
        group["records"] += 1
        product = group["products"].setdefault(product_key, {
            "name": name, "formats": set(), "records": 0,
        })
        product["records"] += 1
        if record.get("format"):
            product["formats"].add(str(record["format"]))
    ordered = []
    for group in groups.values():
        group["products"] = sorted(
            group["products"].values(), key=lambda product: normalize_text(product["name"]),
        )
        ordered.append(group)
    return sorted(
        ordered,
        key=lambda group: (group["manufacturer"] == "Unknown manufacturer",
                           normalize_text(group["manufacturer"])),
    )


def should_color(mode, stream):
    """Resolve ANSI color policy from CLI mode, environment, and TTY state."""
    if mode == "always":
        return True
    if mode == "never" or os.environ.get("NO_COLOR") is not None:
        return False
    return bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("TERM") != "dumb"


def format_order(value):
    """Sort familiar plug-in formats in a migration-friendly order."""
    order = ("AU", "AUv3", "VST", "VST3", "AAX")
    return (order.index(value) if value in order else len(order), value)


def write_product_groups(records, stream, palette, marker="−"):
    """Write compact manufacturer groups with formats folded per product."""
    marker_style = palette.good if marker == "✓" else palette.format if marker == "+" else palette.missing
    for group in grouped_products(records):
        product_count = len(group["products"])
        count_text = f"{product_count} product{'s' if product_count != 1 else ''}"
        if group["records"] != product_count:
            count_text += f" / {group['records']} records"
        stream.write(
            f"\n{palette.manufacturer(group['manufacturer'])}  "
            f"{palette.dim(count_text)}\n"
        )
        for product in group["products"]:
            formats = ", ".join(sorted(product["formats"], key=format_order)) or "unspecified"
            repeated = (palette.dim(f" ×{product['records']}")
                        if product["records"] > 1 else "")
            stream.write(
                f"  {marker_style(marker)} {product['name']}  "
                f"{palette.format('[' + formats + ']')}{repeated}\n"
            )


def write_terminal_report(report, stream, color_mode="auto", show_matches=False,
                          show_additional=False, show_versions=False):
    """Write a concise color-aware diff intended for interactive terminal use."""
    palette = Palette(should_color(color_mode, stream))
    summary = report["summary"]
    baseline = report["baseline"].get("computer") or "baseline"
    candidate = report["candidate"].get("computer") or "candidate"
    width = 28
    filled = round(width * summary["parity_percent"] / 100.0)
    bar = palette.good("━" * filled) + palette.dim("━" * (width - filled))
    parity = palette.good(f"{summary['parity_percent']:.2f}%")
    matched = palette.dim(f"{summary['matched']}/{summary['baseline_records']} matched")
    stream.write(f"{palette.title('Plug-in parity')}  {baseline} → {candidate}\n")
    stream.write(f"{bar}  {parity}  {matched}\n")
    stream.write(
        f"{palette.missing(summary['gaps'])} missing  "
        f"{palette.warning(summary['version_differences'])} version differences  "
        f"{palette.dim(str(summary['additional_candidate_records']) + ' candidate-only')}\n"
    )
    if summary.get("ignored_unresolved_live_records"):
        stream.write(
            f"{palette.dim(str(summary['ignored_unresolved_live_records']) +
                           ' unresolved Live-only baseline records ignored')}\n"
        )
    if summary.get("collapsed_shell_records"):
        stream.write(
            f"{palette.dim(str(summary['collapsed_shell_records']) +
                           ' historical shell-container records consolidated')}\n"
        )
    stream.write(f"\n{palette.title('Missing from ' + candidate)}")
    if report["gaps"]:
        write_product_groups(report["gaps"], stream, palette)
    else:
        stream.write(f"\n  {palette.good('✓ No missing plug-ins')}\n")
    if show_versions:
        stream.write(f"\n{palette.title('Version differences')}\n")
        if report["version_differences"]:
            differences = sorted(report["version_differences"], key=lambda item: (
                normalize_text(item.get("manufacturer")), normalize_text(item["name"]),
                item.get("format") or "",
            ))
            for difference in differences:
                manufacturer = difference.get("manufacturer") or "Unknown manufacturer"
                candidate_versions = ", ".join(difference["candidate_versions"])
                plugin_format = difference.get("format") or "unspecified"
                stream.write(
                    f"  {palette.warning('~')} {manufacturer} · {difference['name']} "
                    f"{palette.format('[' + plugin_format + ']')}  "
                    f"{palette.dim(str(difference['baseline_version']))} → "
                    f"{palette.warning(candidate_versions)}\n"
                )
        else:
            stream.write(f"  {palette.good('✓ None')}\n")
    elif summary["version_differences"]:
        stream.write(
            f"\n{palette.dim(str(summary['version_differences']) + ' version differences hidden; '
                           'use --show-versions.')}\n"
        )
    if show_additional:
        stream.write(f"\n{palette.title('Candidate-only plug-ins')}")
        if report["additional_candidate_records"]:
            write_product_groups(report["additional_candidate_records"], stream, palette, marker="+")
        else:
            stream.write(f"\n  {palette.dim('None')}\n")
    elif summary["additional_candidate_records"]:
        stream.write(
            f"{palette.dim(str(summary['additional_candidate_records']) + ' candidate-only records hidden; '
                           'use --show-additional.')}\n"
        )
    if show_matches:
        stream.write(f"\n{palette.title('Matches')}")
        if report["matched"]:
            write_product_groups(report["matched"], stream, palette, marker="✓")
        else:
            stream.write(f"\n  {palette.dim('None')}\n")
    else:
        stream.write(
            f"{palette.dim(str(summary['matched']) + ' matching records hidden; use --show-matches.')}\n"
        )


def write_comparison_text(report, stream):
    """Write a complete, uncolored cross-machine parity artifact."""
    summary = report["summary"]
    baseline = report["baseline"].get("computer") or "baseline"
    candidate = report["candidate"].get("computer") or "candidate"
    stream.write("MAC AUDIO PLUG-IN PARITY REPORT\n")
    stream.write("================================\n\n")
    stream.write(f"Baseline: {baseline} ({summary['baseline_records']} records)\n")
    stream.write(f"Candidate: {candidate} ({summary['candidate_records']} records)\n")
    stream.write(f"Matched: {summary['matched']}\n")
    stream.write(f"Gaps: {summary['gaps']}\n")
    stream.write(f"Parity: {summary['parity_percent']:.2f}%\n")
    stream.write(f"Candidate-only records: {summary['additional_candidate_records']}\n")
    stream.write(f"Version differences: {summary['version_differences']}\n")
    stream.write(
        f"Unresolved Live-only baseline records ignored: "
        f"{summary.get('ignored_unresolved_live_records', 0)}\n"
    )
    stream.write(
        f"Historical shell-container records consolidated: "
        f"{summary.get('collapsed_shell_records', 0)}\n"
    )
    if summary["gaps_by_format"]:
        stream.write("Gaps by format: " + ", ".join(
            f"{key}: {value}" for key, value in summary["gaps_by_format"].items()) + "\n")
    stream.write("\nMISSING FROM CANDIDATE\n----------------------")
    if not report["gaps"]:
        stream.write("\nNone.\n")
    else:
        write_product_groups(report["gaps"], stream, Palette(False))
    stream.write("\nVERSION DIFFERENCES\n-------------------\n")
    if not report["version_differences"]:
        stream.write("None.\n")
    for record in report["version_differences"]:
        candidates = ", ".join(record["candidate_versions"])
        stream.write(
            f"- {record['name']} [{record.get('format') or 'unspecified'}]: "
            f"{record['baseline_version']} -> {candidates}\n"
        )
    stream.write("\nCANDIDATE-ONLY RECORDS\n----------------------")
    if not report["additional_candidate_records"]:
        stream.write("\nNone.\n")
    else:
        write_product_groups(report["additional_candidate_records"], stream, Palette(False), marker="+")


def atomic_json(value, destination):
    """Replace a JSON file atomically."""
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=destination.parent,
            prefix=f".{destination.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def save_comparison(report, output, output_format):
    """Write a JSON or text comparison atomically, or to standard output."""
    if output == "-":
        if output_format == "json":
            json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            write_comparison_text(report, sys.stdout)
        return
    destination = Path(output).expanduser().resolve()
    if output_format == "json":
        atomic_json(report, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=destination.parent,
            prefix=f".{destination.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            write_comparison_text(report, stream)
        temporary.replace(destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def add_scan_arguments(parser):
    """Add filesystem and Live inventory options to a scan parser."""
    parser.add_argument("--host", default=host_name(),
                        help="Snapshot host label; defaults to the short host name")
    parser.add_argument("--root", action="append", type=parse_root, default=[],
                        help="Additional FORMAT=PATH root; repeatable")
    parser.add_argument("--no-default-roots", action="store_true",
                        help="Scan only roots supplied with --root")
    parser.add_argument("--application-root", action="append", type=Path, default=[],
                        help="Application folder to search for AUv3 extensions; repeatable")
    parser.add_argument("--skip-auv3", action="store_true",
                        help="Do not inspect application bundles for AUv3 extensions")
    parser.add_argument("--live-inventory", action="append", type=Path, default=[],
                        help="Live inventory JSON/text/CSV/TSV to merge; repeatable")
    parser.add_argument("--output", type=Path,
                        help="Snapshot path; defaults to inventories/<host>.json")


def run_scan(args):
    """Execute a host scan and save its durable snapshot."""
    roots = ([] if args.no_default_roots else default_roots()) + args.root
    applications = args.application_root or [Path("/Applications"), Path.home() / "Applications"]
    installed, searched, warnings = scan_installed(
        roots, application_roots=applications, include_auv3=not args.skip_auv3,
    )
    live = []
    for path in args.live_inventory:
        live.extend(load_inventory(path, "live"))
    if any(record["format"] == "AAX" and record["enumeration_level"] == "bundle"
           for record in installed):
        warnings.append(
            "AAX bundles are inventoried without loading them; shell bundles may contain "
            "multiple products that are not individually exposed in bundle metadata."
        )
    snapshot = build_snapshot(installed, searched, live, warnings, args.host)
    output = args.output or TOOL_DIR / "inventories" / f"{args.host}.json"
    atomic_json(snapshot, output)
    summary = snapshot["summary"]
    print(
        f"Saved {args.host}: {summary['installed_records']} filesystem records, "
        f"{summary['live_records']} Live records, {summary['master_records']} master records -> {output}",
        file=sys.stderr,
    )
    return 0


def run_compare(args):
    """Compare saved baseline and candidate host snapshots."""
    baseline = load_snapshot(args.baseline)
    candidate_path = args.candidate or TOOL_DIR / "inventories" / f"{host_name()}.json"
    candidate = load_snapshot(candidate_path)
    report = compare_snapshots(baseline, candidate)
    output_format = args.format or (Path(args.output).suffix.lstrip(".").casefold()
                                    if args.output else "txt")
    if output_format not in ("json", "txt"):
        output_format = "txt"
    baseline_name = baseline.get("computer") or Path(args.baseline).stem
    candidate_name = candidate.get("computer") or Path(candidate_path).stem
    output = args.output or TOOL_DIR / "reports" / f"{baseline_name}-vs-{candidate_name}.{output_format}"
    if str(output) == "-" and output_format == "txt":
        write_terminal_report(
            report, sys.stdout, args.color, args.show_matches,
            args.show_additional, args.show_versions,
        )
    else:
        save_comparison(report, str(output), output_format)
        if not args.quiet:
            write_terminal_report(
                report, sys.stdout, args.color, args.show_matches,
                args.show_additional, args.show_versions,
            )
    summary = report["summary"]
    if str(output) != "-":
        print(f"Full report: {output}", file=sys.stderr)
    return 3 if args.fail_on_gaps and summary["gaps"] else 0


def main(argv=None):
    """Run the snapshot or parity-comparison command."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="Inventory this Mac")
    add_scan_arguments(scan_parser)
    compare_parser = subparsers.add_parser("compare", help="Compare two saved snapshots")
    compare_parser.add_argument("--baseline", type=Path, required=True,
                                help="Source-machine snapshot, normally inventories/drift.json")
    compare_parser.add_argument("--candidate", type=Path,
                                help="Destination snapshot; defaults to this host's inventory")
    compare_parser.add_argument("--output", help="Comparison path, or - for stdout")
    compare_parser.add_argument("--format", choices=("json", "txt"),
                                help="Defaults to the output extension, otherwise text")
    compare_parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                                help="ANSI color policy for the terminal diff")
    compare_parser.add_argument("--show-matches", action="store_true",
                                help="Show matching records; hidden by default")
    compare_parser.add_argument("--show-additional", action="store_true",
                                help="Show candidate-only records; hidden by default")
    compare_parser.add_argument("--show-versions", action="store_true",
                                help="Show detailed version differences")
    compare_parser.add_argument("--quiet", action="store_true",
                                help="Write the report without printing the terminal diff")
    compare_parser.add_argument("--fail-on-gaps", action="store_true",
                                help="Return exit code 3 while baseline plug-ins remain missing")
    args = parser.parse_args(argv)
    try:
        return run_scan(args) if args.command == "scan" else run_compare(args)
    except (ParityError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
