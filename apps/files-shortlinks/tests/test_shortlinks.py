from __future__ import annotations

import unittest

from shortlinks import LinkRecord, create_app, encoded_object_path, parse_token


TARGET = (
    "evan/meteorology/papers/Evan Musial - Assessing Vertical Coherence of "
    "Doppler Radar Rotation Signatures (2026).pdf"
)


class FakeStore:
    def __init__(self, links=None, error: Exception | None = None):
        self.links = links or {}
        self.error = error

    def get(self, slug: str):
        if self.error:
            raise self.error
        return self.links.get(slug)


class ParsingTests(unittest.TestCase):
    def test_parse_extensionless(self):
        self.assertEqual(parse_token("assessing-vertical-coherence"), ("assessing-vertical-coherence", None))

    def test_parse_extension(self):
        self.assertEqual(parse_token("assessing-vertical-coherence.pdf"), ("assessing-vertical-coherence", "pdf"))

    def test_rejects_uppercase_and_nested_paths(self):
        for value in ("Uppercase", "nested/path", "bad_slug"):
            with self.assertRaises(ValueError):
                parse_token(value)

    def test_encodes_each_object_segment(self):
        self.assertEqual(
            encoded_object_path("folder/a file (2026).pdf"),
            "folder/a%20file%20%282026%29.pdf",
        )


class HandlerTests(unittest.TestCase):
    def setUp(self):
        record = LinkRecord(target_object=TARGET, extension="pdf", status="active")
        self.store = FakeStore({"assessing-vertical-coherence": record})
        self.client = create_app(self.store).test_client()

    def assert_redirect(self, path: str):
        response = self.client.head(path)
        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response.headers["Location"],
            "https://files.musial.io/evan/meteorology/papers/"
            "Evan%20Musial%20-%20Assessing%20Vertical%20Coherence%20of%20Doppler%20"
            "Radar%20Rotation%20Signatures%20%282026%29.pdf",
        )
        self.assertEqual(response.headers["CDN-Cache-Control"], "public, max-age=86400")

    def test_extensionless_redirect(self):
        self.assert_redirect("/s/assessing-vertical-coherence")

    def test_extension_redirect(self):
        self.assert_redirect("/s/assessing-vertical-coherence.pdf")

    def test_wrong_extension_is_not_found(self):
        response = self.client.get("/s/assessing-vertical-coherence.docx")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_unknown_is_not_cached(self):
        response = self.client.get("/s/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_disabled_is_gone(self):
        disabled = LinkRecord(target_object=TARGET, extension="pdf", status="disabled")
        client = create_app(FakeStore({"retired": disabled})).test_client()
        response = client.get("/s/retired")
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_store_failure_is_not_cached(self):
        client = create_app(FakeStore(error=RuntimeError("down"))).test_client()
        response = client.get("/s/assessing-vertical-coherence")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_post_is_rejected(self):
        response = self.client.post("/s/assessing-vertical-coherence")
        self.assertEqual(response.status_code, 405)


if __name__ == "__main__":
    unittest.main()
