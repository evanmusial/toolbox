#!/usr/bin/env python3
"""Admin CLI for the files.musial.io Firestore short-link registry."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


PROJECT = os.environ.get("FILES_LINK_PROJECT", "musial-family-public")
DATABASE = os.environ.get("FILES_LINK_DATABASE", "files-shortlinks")
COLLECTION = os.environ.get("FILES_LINK_COLLECTION", "short_links")
EVENT_COLLECTION = os.environ.get("FILES_LINK_EVENT_COLLECTION", "short_link_events")
BUCKET = os.environ.get("FILES_LINK_BUCKET", "musial-family-public")
HOST = os.environ.get("FILES_LINK_HOST", "files.musial.io")
URL_MAP = os.environ.get("FILES_LINK_URL_MAP", "files-musial-io-map")
GCLOUD = os.environ.get("GCLOUD") or shutil.which("gcloud") or (
    "/Users/evan/Downloads/google-cloud-sdk/bin/gcloud"
)
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
EXTENSION_RE = re.compile(r"^[a-z0-9]{1,12}$")


def run_gcloud(*args: str) -> str:
    completed = subprocess.run(
        [GCLOUD, *args], check=True, text=True, stdout=subprocess.PIPE
    )
    return completed.stdout.strip()


def actor() -> str:
    return run_gcloud("config", "get-value", "account")


def access_token() -> str:
    return run_gcloud("auth", "print-access-token")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def value(data: object) -> dict[str, object]:
    if isinstance(data, bool):
        return {"booleanValue": data}
    if isinstance(data, int):
        return {"integerValue": str(data)}
    return {"stringValue": str(data)}


def fields(data: dict[str, object]) -> dict[str, dict[str, object]]:
    return {key: value(item) for key, item in data.items()}


def document_name(collection: str, document_id: str) -> str:
    return f"projects/{PROJECT}/databases/{DATABASE}/documents/{collection}/{document_id}"


def request_json(method: str, url: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {access_token()}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Firestore returned HTTP {exc.code}: {detail}") from exc


def commit(writes: list[dict]) -> dict:
    endpoint = (
        f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/"
        f"{DATABASE}/documents:commit"
    )
    return request_json("POST", endpoint, {"writes": writes})


def decode_value(item: dict) -> object:
    if "stringValue" in item:
        return item["stringValue"]
    if "integerValue" in item:
        return int(item["integerValue"])
    if "booleanValue" in item:
        return bool(item["booleanValue"])
    if "timestampValue" in item:
        return item["timestampValue"]
    return item


def decode_document(document: dict) -> dict[str, object]:
    decoded = {key: decode_value(item) for key, item in document.get("fields", {}).items()}
    decoded["slug"] = document.get("name", "").rsplit("/", 1)[-1]
    decoded["update_time"] = document.get("updateTime", "")
    return decoded


def get_document(slug: str) -> dict[str, object]:
    endpoint = (
        f"https://firestore.googleapis.com/v1/{document_name(COLLECTION, slug)}"
    )
    return decode_document(request_json("GET", endpoint))


def normalize_object(value_: str) -> tuple[str, str]:
    if value_.startswith("gs://"):
        without_scheme = value_[5:]
        bucket, separator, object_name = without_scheme.partition("/")
        if not separator or bucket != BUCKET:
            raise ValueError(f"object must be in gs://{BUCKET}")
    else:
        object_name = value_.lstrip("/")
    if not object_name or any(part in {"", ".", ".."} for part in object_name.split("/")):
        raise ValueError("invalid object name")
    return object_name, f"gs://{BUCKET}/{object_name}"


def describe_object(uri: str) -> dict:
    output = run_gcloud(
        "storage", "objects", "describe", uri, "--project", PROJECT, "--format=json"
    )
    return json.loads(output)


def validate_slug(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("slug must be 1-64 lowercase letters, digits, or hyphens")


def event_write(action: str, slug: str, actor_: str, details: dict[str, object]) -> dict:
    event_id = uuid.uuid4().hex
    payload = {"action": action, "slug": slug, "actor": actor_, "at": now(), **details}
    return {
        "update": {
            "name": document_name(EVENT_COLLECTION, event_id),
            "fields": fields(payload),
        },
        "currentDocument": {"exists": False},
    }


def add_link(args: argparse.Namespace) -> None:
    validate_slug(args.slug)
    object_name, uri = normalize_object(args.object)
    metadata = describe_object(uri)
    extension = PurePosixPath(object_name).suffix.lstrip(".").lower()
    if not EXTENSION_RE.fullmatch(extension):
        raise ValueError("target must have a simple 1-12 character extension")
    actor_ = actor()
    timestamp = now()
    data: dict[str, object] = {
        "target_object": object_name,
        "extension": extension,
        "status": "active",
        "target_generation": str(metadata.get("generation", "")),
        "content_type": str(
            metadata.get("content_type", metadata.get("contentType", "application/octet-stream"))
        ),
        "size_bytes": int(metadata.get("size", 0)),
        "created_at": timestamp,
        "created_by": actor_,
        "updated_at": timestamp,
        "updated_by": actor_,
    }
    writes = [
        {
            "update": {
                "name": document_name(COLLECTION, args.slug),
                "fields": fields(data),
            },
            "currentDocument": {"exists": False},
        },
        event_write("created", args.slug, actor_, {"target_object": object_name}),
    ]
    commit(writes)
    print(f"created {args.slug} -> {object_name}")
    print(f"https://{HOST}/s/{args.slug}")
    print(f"https://{HOST}/s/{args.slug}.{extension}")
    if not args.no_smoke:
        verify_link(args.slug, data)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def head_without_redirect(url: str) -> tuple[int, str]:
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, method="HEAD")
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def encoded_target(object_name: str) -> str:
    return "/".join(
        urllib.parse.quote(segment, safe="~") for segment in object_name.split("/")
    )


def verify_link(slug: str, data: dict[str, object] | None = None) -> None:
    record = data or get_document(slug)
    target = f"https://{HOST}/{encoded_target(str(record['target_object']))}"
    extension = str(record["extension"])
    for suffix in ("", f".{extension}"):
        url = f"https://{HOST}/s/{slug}{suffix}"
        status, location = head_without_redirect(url)
        if status != 308 or location != target:
            raise RuntimeError(
                f"smoke check failed for {url}: status={status} location={location!r}"
            )
        print(f"verified 308 {url} -> {location}")


def get_link(args: argparse.Namespace) -> None:
    validate_slug(args.slug)
    print(json.dumps(get_document(args.slug), indent=2, sort_keys=True))


def list_links(_: argparse.Namespace) -> None:
    base = (
        f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/"
        f"{DATABASE}/documents/{COLLECTION}?pageSize=1000"
    )
    documents: list[dict[str, object]] = []
    page_token = ""
    while True:
        endpoint = base
        if page_token:
            endpoint += "&pageToken=" + urllib.parse.quote(page_token)
        response = request_json("GET", endpoint)
        documents.extend(decode_document(item) for item in response.get("documents", []))
        page_token = str(response.get("nextPageToken", ""))
        if not page_token:
            break
    for item in sorted(documents, key=lambda entry: str(entry["slug"])):
        print(
            f"{item['slug']}\t{item.get('status', '')}\t"
            f"{item.get('target_object', '')}"
        )


def invalidate(slug: str, extension: str) -> None:
    for path in (f"/s/{slug}", f"/s/{slug}.{extension}"):
        run_gcloud(
            "compute",
            "url-maps",
            "invalidate-cdn-cache",
            URL_MAP,
            f"--path={path}",
            "--project",
            PROJECT,
            "--quiet",
        )


def disable_link(args: argparse.Namespace) -> None:
    validate_slug(args.slug)
    record = get_document(args.slug)
    actor_ = actor()
    timestamp = now()
    writes = [
        {
            "update": {
                "name": document_name(COLLECTION, args.slug),
                "fields": fields(
                    {
                        "status": "disabled",
                        "updated_at": timestamp,
                        "updated_by": actor_,
                    }
                ),
            },
            "updateMask": {
                "fieldPaths": ["status", "updated_at", "updated_by"]
            },
            "currentDocument": {"updateTime": record["update_time"]},
        },
        event_write(
            "disabled",
            args.slug,
            actor_,
            {"target_object": str(record.get("target_object", ""))},
        ),
    ]
    commit(writes)
    invalidate(args.slug, str(record["extension"]))
    print(f"disabled {args.slug}; slug remains reserved")


def verify_command(args: argparse.Namespace) -> None:
    validate_slug(args.slug)
    verify_link(args.slug)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="create an immutable short link")
    add.add_argument("slug")
    add.add_argument("object", help="GCS object name or gs:// URI")
    add.add_argument("--no-smoke", action="store_true")
    add.set_defaults(func=add_link)

    get = subparsers.add_parser("get", help="show one registry record")
    get.add_argument("slug")
    get.set_defaults(func=get_link)

    listing = subparsers.add_parser("list", help="list registry records")
    listing.set_defaults(func=list_links)

    disable = subparsers.add_parser("disable", help="tombstone a short link")
    disable.add_argument("slug")
    disable.set_defaults(func=disable_link)

    verify = subparsers.add_parser("verify", help="verify both public aliases")
    verify.add_argument("slug")
    verify.set_defaults(func=verify_command)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
