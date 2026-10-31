from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import youtube_subscription_review as review  # noqa: E402


class Request:
    def __init__(self, response):
        self.response = response

    def execute(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeHttpError(Exception):
    def __init__(self, status, reason, message):
        super().__init__(message)
        self.resp = type("Response", (), {"status": status})()
        self.content = json.dumps(
            {
                "error": {
                    "message": message,
                    "errors": [{"reason": reason}],
                }
            }
        ).encode("utf-8")


class SubscriptionsEndpoint:
    def __init__(self, pages, delete_responses=None):
        self.pages = pages
        self.delete_responses = delete_responses or {}
        self.calls = []
        self.deleted = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return Request(self.pages[kwargs.get("pageToken")])

    def delete(self, **kwargs):
        subscription_id = kwargs["id"]
        self.deleted.append(subscription_id)
        return Request(self.delete_responses.get(subscription_id, {}))


class ChannelsEndpoint:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        requested = set(kwargs["id"].split(","))
        return Request({"items": [item for item in self.items if item["id"] in requested]})


class PlaylistItemsEndpoint:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return Request(self.responses[kwargs["playlistId"]])


class FakeYouTube:
    def __init__(
        self,
        subscription_pages,
        channels,
        playlist_responses=None,
        delete_responses=None,
    ):
        self.subscription_endpoint = SubscriptionsEndpoint(
            subscription_pages,
            delete_responses=delete_responses,
        )
        self.channel_endpoint = ChannelsEndpoint(channels)
        self.playlist_endpoint = PlaylistItemsEndpoint(playlist_responses or {})

    def subscriptions(self):
        return self.subscription_endpoint

    def channels(self):
        return self.channel_endpoint

    def playlistItems(self):
        return self.playlist_endpoint


def subscription_item(subscription_id, channel_id, title):
    return {
        "id": subscription_id,
        "snippet": {
            "title": title,
            "description": f"About {title}",
            "publishedAt": "2020-01-02T00:00:00Z",
            "resourceId": {"channelId": channel_id},
        },
    }


class FormattingTests(unittest.TestCase):
    def test_format_count(self):
        self.assertEqual(review.format_count(999), "999")
        self.assertEqual(review.format_count(1_200), "1.2K")
        self.assertEqual(review.format_count(2_000_000), "2M")
        self.assertEqual(review.format_count(None), "n/a")

    def test_human_age(self):
        now = datetime(2026, 10, 29, tzinfo=timezone.utc)
        self.assertEqual(review.human_age("2026-10-27T00:00:00Z", now=now), "2 days ago")
        self.assertEqual(review.human_age("2026-08-29T00:00:00Z", now=now), "2 months ago")
        self.assertEqual(review.human_age(None, now=now), "unknown")

    def test_xterm_single_channel_card(self):
        record = review.Subscription(
            subscription_id="sub-a",
            channel_id="chan-a",
            name="Weather 🌩 Lab",
            description="A useful channel description that remains readable.",
            handle="@WeatherLab",
            subscriber_count=12_300,
            video_count=456,
            view_count=7_890_000,
            latest_upload=review.LatestUpload(
                video_id="video-a",
                title="Latest weather analysis",
                published_at="2026-10-28T00:00:00Z",
            ),
            latest_upload_checked=True,
        )
        width = 110
        rendered = review.render_single_record(
            record,
            position=4,
            total=20,
            reviewed=3,
            remaining=17,
            marked_count=1,
            decision="unsubscribe",
            width=width,
            use_color=True,
        )
        self.assertEqual(review.display_width(rendered.splitlines()[0]), width)
        self.assertIn("\x1b[", rendered)
        self.assertIn("Decision: UNSUBSCRIBE", review.strip_ansi(rendered))
        self.assertIn("@WeatherLab", review.strip_ansi(rendered))
        self.assertIn("Subscribers     12.3K", review.strip_ansi(rendered))

    def test_normalize_handle(self):
        self.assertEqual(review.normalize_handle("@ForExample"), "@ForExample")
        self.assertEqual(
            review.normalize_handle("https://www.youtube.com/@ForExample/"),
            "@ForExample",
        )
        self.assertIsNone(review.normalize_handle(None))

    def test_next_undecided_wraps_and_stops_when_complete(self):
        records = [
            review.Subscription("s-a", "c-a", "Alpha", ""),
            review.Subscription("s-b", "c-b", "Beta", ""),
            review.Subscription("s-c", "c-c", "Gamma", ""),
        ]
        self.assertEqual(
            review.next_undecided_index(records, {"c-a"}, after=0),
            1,
        )
        self.assertEqual(
            review.next_undecided_index(records, {"c-b", "c-c"}, after=2),
            0,
        )
        self.assertIsNone(
            review.next_undecided_index(records, {"c-a", "c-b", "c-c"})
        )

    def test_inventory_backup_and_unsubscribe_receipt(self):
        record = review.Subscription(
            subscription_id="sub-a",
            channel_id="chan-a",
            name="Alpha",
            description="Alpha description",
        )
        with tempfile.TemporaryDirectory() as directory:
            backup_path = review.write_inventory_backup([record], Path(directory))
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
            self.assertEqual(backup["subscription_count"], 1)
            self.assertEqual(
                backup["subscriptions"][0]["channel_id"], "chan-a"
            )

            receipt_path = review.append_unsubscribe_receipt(
                record, backup_path=backup_path
            )
            receipts = receipt_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(
                json.loads(receipts[0])["backed_up_in"], backup_path.name
            )

    def test_marks_persist_and_unsubscribe_as_a_batch(self):
        first = review.Subscription(
            subscription_id="sub-a",
            channel_id="chan-a",
            name="Alpha",
            description="",
        )
        second = review.Subscription(
            subscription_id="sub-b",
            channel_id="chan-b",
            name="Beta",
            description="",
        )
        youtube = FakeYouTube({None: {"items": []}}, [])
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            marks_path = directory_path / "marks.json"
            kept_path = directory_path / "kept.json"
            backup_path = directory_path / "backup.json"
            marked = {"chan-a"}
            kept = {"chan-b"}
            records = [first, second]

            review.save_marked_channels(marks_path, marked, records)
            review.save_kept_channels(kept_path, kept, records)
            self.assertEqual(review.load_marked_channel_ids(marks_path), marked)
            self.assertEqual(review.load_kept_channel_ids(kept_path), kept)

            result = review.unsubscribe_marked(
                youtube,
                records,
                marked,
                backup_path=backup_path,
                marks_path=marks_path,
                limit=150,
            )

            self.assertEqual([record.name for record in result.removed], ["Alpha"])
            self.assertFalse(result.quota_exhausted)
            self.assertEqual(youtube.subscription_endpoint.deleted, ["sub-a"])
            self.assertEqual([record.name for record in records], ["Beta"])
            self.assertEqual(marked, set())
            self.assertEqual(review.load_marked_channel_ids(marks_path), set())

    def test_quota_exhaustion_preserves_unprocessed_marks(self):
        first = review.Subscription("sub-a", "chan-a", "Alpha", "")
        second = review.Subscription("sub-b", "chan-b", "Beta", "")
        quota_error = FakeHttpError(
            403,
            "quotaExceeded",
            "The request cannot be completed because quota was exceeded.",
        )
        youtube = FakeYouTube(
            {None: {"items": []}},
            [],
            delete_responses={"sub-b": quota_error},
        )
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            marks_path = directory_path / "marks.json"
            marked = {"chan-a", "chan-b"}
            records = [first, second]
            review.save_marked_channels(marks_path, marked, records)

            result = review.unsubscribe_marked(
                youtube,
                records,
                marked,
                backup_path=directory_path / "backup.json",
                marks_path=marks_path,
                limit=150,
            )

            self.assertTrue(result.quota_exhausted)
            self.assertEqual([record.name for record in result.removed], ["Alpha"])
            self.assertEqual([record.name for record in records], ["Beta"])
            self.assertEqual(marked, {"chan-b"})
            self.assertEqual(
                review.load_marked_channel_ids(marks_path),
                {"chan-b"},
            )

    def test_reset_decisions_archives_then_clears_both_sets(self):
        record = review.Subscription("sub-a", "chan-a", "Alpha", "")
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            marks_path = state_dir / "marked.json"
            kept_path = state_dir / "kept.json"
            review.save_marked_channels(marks_path, {"chan-a"}, [record])
            review.save_kept_channels(kept_path, {"chan-a"}, [record])

            archive_dir = review.reset_review_decisions(marks_path, kept_path)

            self.assertTrue(
                (archive_dir / "marked-for-unsubscribe.json").is_file()
            )
            self.assertTrue((archive_dir / "kept-subscriptions.json").is_file())
            self.assertEqual(review.load_marked_channel_ids(marks_path), set())
            self.assertEqual(review.load_kept_channel_ids(kept_path), set())


class ApiMappingTests(unittest.TestCase):
    def test_loads_all_pages_and_enriches_channels(self):
        pages = {
            None: {
                "items": [subscription_item("sub-b", "chan-b", "Old B")],
                "nextPageToken": "next",
            },
            "next": {"items": [subscription_item("sub-a", "chan-a", "Old A")]},
        }
        channels = [
            {
                "id": "chan-a",
                "snippet": {
                    "title": "Alpha",
                    "description": "Alpha description",
                    "customUrl": "@AlphaChannel",
                    "publishedAt": "2010-01-01T00:00:00Z",
                    "country": "US",
                },
                "statistics": {
                    "subscriberCount": "1234",
                    "videoCount": "56",
                    "viewCount": "7890",
                    "hiddenSubscriberCount": False,
                },
                "contentDetails": {"relatedPlaylists": {"uploads": "uploads-a"}},
            },
            {
                "id": "chan-b",
                "snippet": {"title": "Beta", "description": "Beta description"},
                "statistics": {"hiddenSubscriberCount": True, "videoCount": "2"},
                "contentDetails": {"relatedPlaylists": {"uploads": "uploads-b"}},
            },
        ]
        youtube = FakeYouTube(pages, channels)

        records = review.load_subscriptions(youtube, order="alphabetical")

        self.assertEqual([record.name for record in records], ["Alpha", "Beta"])
        self.assertEqual(records[0].subscriber_count, 1234)
        self.assertEqual(records[0].handle, "@AlphaChannel")
        self.assertEqual(records[0].uploads_playlist_id, "uploads-a")
        self.assertTrue(records[1].subscribers_hidden)
        self.assertEqual(len(youtube.subscription_endpoint.calls), 2)
        self.assertEqual(
            youtube.channel_endpoint.calls[0]["part"],
            "snippet,statistics,contentDetails",
        )

    def test_latest_upload_is_loaded_only_once(self):
        youtube = FakeYouTube(
            {None: {"items": []}},
            [],
            {
                "uploads-a": {
                    "items": [
                        {
                            "snippet": {"title": "Newest"},
                            "contentDetails": {
                                "videoId": "video-1",
                                "videoPublishedAt": "2026-10-28T12:00:00Z",
                            },
                        }
                    ]
                }
            },
        )
        record = review.Subscription(
            subscription_id="sub-a",
            channel_id="chan-a",
            name="Alpha",
            description="",
            uploads_playlist_id="uploads-a",
        )

        review.ensure_latest_upload(youtube, record)
        review.ensure_latest_upload(youtube, record)

        self.assertEqual(record.latest_upload.title, "Newest")
        self.assertEqual(record.latest_upload.video_id, "video-1")
        self.assertEqual(len(youtube.playlist_endpoint.calls), 1)

    def test_missing_uploads_playlist_is_nonfatal(self):
        youtube = FakeYouTube(
            {None: {"items": []}},
            [],
            {
                "missing-uploads": FakeHttpError(
                    404,
                    "playlistNotFound",
                    "The playlist cannot be found.",
                )
            },
        )
        record = review.Subscription(
            subscription_id="sub-a",
            channel_id="chan-a",
            name="Unavailable uploads",
            description="",
            uploads_playlist_id="missing-uploads",
        )

        review.ensure_latest_upload(youtube, record)

        self.assertTrue(record.latest_upload_checked)
        self.assertEqual(record.latest_upload_error, "unavailable")
        self.assertEqual(review.latest_age(record), "unavailable")


if __name__ == "__main__":
    unittest.main()
