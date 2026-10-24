from __future__ import annotations

import hashlib
import html
import json
import re
import warnings
from dataclasses import dataclass
from urllib.parse import urlsplit

import soupsieve
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from lxml import etree

from .config import Field, Station


class SourceError(Exception):
    def __init__(self, reason, retryable=True, retry_after=None):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


def identity_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_key(value):
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", "", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def slack_escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def json_pointer(document, pointer):
    current = document
    if not pointer:
        return current
    for component in pointer[1:].split("/"):
        component = component.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", component):
                    return None
                current = current[int(component)]
            elif isinstance(current, dict):
                current = current.get(component)
            else:
                return None
        except (KeyError, IndexError, TypeError):
            return None
    return current


def label_key(value):
    return " ".join(value.split()).rstrip(":").strip().casefold()


def html_value(document, spec: Field):
    if spec.after_label:
        matches = []
        for label in document.select(spec.label_selector):
            if label_key(label.get_text(" ", strip=True)) != label_key(spec.after_label):
                continue
            for sibling in label.find_next_siblings():
                if soupsieve.match(spec.selector, sibling):
                    matches.append(sibling)
                    if not spec.multiple:
                        break
            if matches and not spec.multiple:
                break
    else:
        matches = document.select(spec.selector)
    if not spec.multiple:
        matches = matches[:1]
    values = [node.get_text(" ", strip=True) if spec.attribute == "text" else node.get(spec.attribute) for node in matches]
    return values if spec.multiple else (values[0] if values else None)


def normalize(value, spec: Field):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise SourceError("invalid_field_type")
    result = str(value)
    if spec.html_unescape:
        result = html.unescape(result)
    if spec.collapse_whitespace:
        result = " ".join(result.split())
    result = result.strip()
    if spec.regex:
        match = re.search(spec.regex, result)
        result = match.group(spec.regex_group).strip() if match else ""
    return result


@dataclass(frozen=True)
class Play:
    fields: dict[str, str]
    identity: str
    identity_key: str

    @property
    def display_text(self):
        return self.fields["display_text"]


def parse_body(body: bytes, station: Station) -> Play:
    try:
        if station.method == "json":
            document = json.loads(body.decode("utf-8-sig"))
        elif station.method == "html":
            # Old Icecast emits XHTML/prologs but still requires tolerant HTML parsing.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
                document = BeautifulSoup(body, "lxml")
        else:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
            document = etree.fromstring(body, parser)
            if document.getroottree().docinfo.doctype:
                raise SourceError("xml_doctype_not_allowed", retryable=False)
    except SourceError:
        raise
    except Exception:
        raise SourceError("invalid_document") from None
    values = {"station": station.id, "title": "", "artists": "", "play_id": "", "image_url": "", "display_text": ""}
    artist_values = []
    for spec in station.fields:
        try:
            if station.method == "json":
                extracted = json_pointer(document, spec.path)
            elif station.method == "html":
                extracted = html_value(document, spec)
            else:
                extracted = document.xpath(spec.path, namespaces=station.namespaces)
                if isinstance(extracted, list):
                    extracted = [node.get(spec.attribute) if spec.attribute != "text" and isinstance(node, etree._Element)
                                 else " ".join(node.itertext()) if isinstance(node, etree._Element)
                                 else node for node in extracted]
                    if not spec.multiple:
                        extracted = extracted[0] if extracted else None
            items = extracted if isinstance(extracted, list) else [extracted]
            cleaned = [normalize(item, spec) for item in items]
            cleaned = [item for item in cleaned if item]
            if len(cleaned) > 1 and not spec.multiple:
                raise SourceError("unexpected_multiple_values")
            value = spec.join.join(cleaned)
            if spec.required and not value:
                raise SourceError("missing_required_field")
            if spec.name == "artists":
                artist_values = cleaned
            values[spec.name] = value
        except SourceError:
            raise
        except Exception:
            raise SourceError("extraction_failed") from None
    if not values["display_text"]:
        values["display_text"] = " - ".join(v for v in (values["artists"], values["title"]) if v)
    display = values["display_text"]
    if not display or display.lower() in ("none", "null", "n/a") or re.match(
        r"^(?:error\s*:|error fetching\b|current song information not found\b)", display, re.I
    ):
        raise SourceError("invalid_song_text")
    if len(display) > 2000 or any(ord(c) < 32 and c not in "\n\t" for c in display):
        raise SourceError("invalid_song_text")
    values["match_key"] = f"{clean_key(values['title'])}::{'|'.join(sorted(clean_key(v) for v in artist_values))}"
    try:
        identity = station.identity.format_map(values).strip()
    except (ValueError, KeyError):
        raise SourceError("identity_format_failed", retryable=False) from None
    if not identity:
        raise SourceError("empty_identity")
    return Play(values, identity, identity_hash(identity))


def build_payload(station: Station, play: Play):
    message = station.message
    escaped = {key: slack_escape(value) for key, value in play.fields.items()}
    color_text = message.color_key.format_map(play.fields)
    attachment = {
        "fallback": message.fallback.format_map(play.fields),
        "color": "#" + hashlib.sha1(color_text.encode("utf-8")).hexdigest()[:6],
    }
    text = message.text.format_map(escaped)
    if message.layout == "field":
        attachment["fields"] = [{"title": message.title.format_map(escaped), "value": text, "short": False}]
    else:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        image = play.fields.get("image_url", "")
        try:
            parsed_image = urlsplit(image)
            valid_image = bool(parsed_image.hostname) and parsed_image.scheme in ("http", "https")
        except ValueError:
            valid_image = False
        if image and valid_image:
            block["accessory"] = {"type": "image", "image_url": image, "alt_text": f"Album art for {play.fields['title']}"}
        attachment["blocks"] = [block]
    return {"attachments": [attachment]}
