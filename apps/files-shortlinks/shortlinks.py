"""HTTP redirect service backed by a durable Firestore registry."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re
from typing import Any, Mapping, Protocol
from urllib.parse import quote

from flask import Flask, Response, request


SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
EXTENSION_RE = re.compile(r"^[a-z0-9]{1,12}$")
ACTIVE = "active"
DISABLED = "disabled"


@dataclass(frozen=True)
class LinkRecord:
    target_object: str
    extension: str
    status: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LinkRecord":
        record = cls(
            target_object=str(value.get("target_object", "")),
            extension=str(value.get("extension", "")).lower(),
            status=str(value.get("status", "")),
        )
        record.validate()
        return record

    def validate(self) -> None:
        if not is_safe_object_name(self.target_object):
            raise ValueError("invalid target_object")
        if not EXTENSION_RE.fullmatch(self.extension):
            raise ValueError("invalid extension")
        if self.status not in {ACTIVE, DISABLED}:
            raise ValueError("invalid status")


class LinkStore(Protocol):
    def get(self, slug: str) -> LinkRecord | None:
        """Return one link by slug, or None if it does not exist."""


class FirestoreLinkStore:
    def __init__(self, project: str, database: str, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project, database=database)
        self._collection = self._client.collection(collection)

    def get(self, slug: str) -> LinkRecord | None:
        snapshot = self._collection.document(slug).get()
        if not snapshot.exists:
            return None
        return LinkRecord.from_mapping(snapshot.to_dict() or {})


def is_safe_object_name(value: str) -> bool:
    if not value or value.startswith("/") or value.endswith("/"):
        return False
    if "://" in value or "\\" in value or any(ord(char) < 32 for char in value):
        return False
    segments = value.split("/")
    return all(segment not in {"", ".", ".."} for segment in segments)


def encoded_object_path(value: str) -> str:
    if not is_safe_object_name(value):
        raise ValueError("unsafe object name")
    return "/".join(quote(segment, safe="~") for segment in value.split("/"))


def parse_token(token: str) -> tuple[str, str | None]:
    if "/" in token or token != token.lower():
        raise ValueError("invalid token")
    if "." in token:
        slug, extension = token.rsplit(".", 1)
        if not EXTENSION_RE.fullmatch(extension):
            raise ValueError("invalid extension")
    else:
        slug, extension = token, None
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("invalid slug")
    return slug, extension


def no_store_response(message: str, status: int) -> Response:
    response = Response(message, status=status, content_type="text/plain; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


def create_app(store: LinkStore | None = None) -> Flask:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "musial-family-public")
    database = os.environ.get("FIRESTORE_DATABASE", "files-shortlinks")
    collection = os.environ.get("SHORTLINK_COLLECTION", "short_links")
    canonical_host = os.environ.get("CANONICAL_HOST", "files.musial.io")
    resolved_store = store or FirestoreLinkStore(project, database, collection)

    app = Flask(__name__)

    @app.get("/healthz")
    def healthz() -> Response:
        return no_store_response("ok\n", 200)

    @app.route("/s/<token>", methods=["GET", "HEAD"])
    def resolve(token: str) -> Response:
        try:
            slug, requested_extension = parse_token(token)
        except ValueError:
            return no_store_response("not found\n", 404)

        try:
            record = resolved_store.get(slug)
        except Exception:
            app.logger.exception("short-link lookup failed", extra={"slug": slug})
            return no_store_response("temporarily unavailable\n", 503)

        if record is None:
            return no_store_response("not found\n", 404)
        if record.status == DISABLED:
            return no_store_response("gone\n", 410)
        if requested_extension is not None and requested_extension != record.extension:
            return no_store_response("not found\n", 404)

        try:
            target_path = encoded_object_path(record.target_object)
        except ValueError:
            app.logger.error("short-link target is unsafe", extra={"slug": slug})
            return no_store_response("temporarily unavailable\n", 503)

        location = f"https://{canonical_host}/{target_path}"
        body = "" if request.method == "HEAD" else f"Permanent redirect: {location}\n"
        response = Response(body, status=308, content_type="text/plain; charset=utf-8")
        response.headers["Location"] = location
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["CDN-Cache-Control"] = "public, max-age=86400"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

    @app.errorhandler(404)
    def not_found(_: Exception) -> Response:
        return no_store_response("not found\n", 404)

    @app.errorhandler(405)
    def method_not_allowed(_: Exception) -> Response:
        return no_store_response("method not allowed\n", 405)

    app.logger.setLevel(logging.INFO)
    return app
