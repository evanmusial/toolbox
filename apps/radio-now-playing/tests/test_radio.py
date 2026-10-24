from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web

from radio_now_playing.__main__ import main
from radio_now_playing.config import ConfigError, Field, Message, Policy, Station, load_configuration, summary
from radio_now_playing.extract import SourceError, build_payload, identity_hash, json_pointer, parse_body
from radio_now_playing.http import DeliveryResult, fetch_play, post_payload
from radio_now_playing.service import poll_station, station_loop
from radio_now_playing.state import InstanceLock, State, readonly_report


HOOK = "https://hooks.slack.com/services/EXAMPLE/TEST/NOT_A_CREDENTIAL"
HTML = b'<html><table><tr><td> Current <b>Song:</b> </td>\n<td class="streamdata">A &amp; B - &quot;Song&quot;</table>'


def station(**overrides):
    values = dict(id="test", enabled=True, method="json", url="http://127.0.0.1/metadata", webhook_url=HOOK,
                  fields=(Field("display_text", path="/title"),), initial_post=False)
    values.update(overrides)
    return Station(**values)


def play(text="Artist - Song", config=None):
    return parse_body(json.dumps({"title": text}).encode(), config or station())


class TemporaryConfiguration:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "conf.d").mkdir()
        self.main = self.root / "daemon.conf"
        self.main.write_text(f"[daemon]\nconf_dir={self.root / 'conf.d'}\nstate_db={self.root / 'state.sqlite3'}\n")
        self.main.chmod(0o600)
        self.station_file = self.root / "conf.d" / "test.conf"
        self.set_station()

    def set_station(self, extra="", url="http://127.0.0.1/metadata"):
        self.station_file.write_text(f"[station]\nid=test\nmethod=json\nurl={url}\nwebhook_url={HOOK}\npoll_interval=1\n[field.display_text]\npath=/title\n" + extra)
        self.station_file.chmod(0o600)

    def close(self):
        self.temp.cleanup()


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.files = TemporaryConfiguration()
        self.addCleanup(self.files.close)

    def test_configuration_and_summary_never_expose_webhook(self):
        config = load_configuration(self.files.main)
        self.assertEqual(config.enabled[0].method, "json")
        self.assertNotIn(HOOK, json.dumps(summary(config)))
        self.assertNotIn(HOOK, repr(config))

    def test_world_readable_configuration_rejected(self):
        self.files.station_file.chmod(0o644)
        with self.assertRaises(ConfigError):
            load_configuration(self.files.main)

    def test_unknown_option_rejected(self):
        self.files.set_station(extra="[http]\nread_timout=8\n")
        with self.assertRaises(ConfigError):
            load_configuration(self.files.main)

    def test_templates_cannot_traverse_objects(self):
        self.files.set_station(extra="[message]\ntext={display_text.__class__}\n")
        with self.assertRaises(ConfigError):
            load_configuration(self.files.main)

    def test_invalid_selector_rejected_before_activation(self):
        text = self.files.station_file.read_text().replace("method=json", "method=html").replace("path=/title", "selector=td[")
        self.files.station_file.write_text(text)
        with self.assertRaises(ConfigError):
            load_configuration(self.files.main)

    def test_duplicate_ids_rejected(self):
        other = self.files.station_file.parent / "duplicate.conf"
        other.write_text(self.files.station_file.read_text())
        other.chmod(0o600)
        with self.assertRaises(ConfigError):
            load_configuration(self.files.main)

    def test_bpm_and_legacy_migration_are_non_posting_and_idempotent(self):
        config_text = self.files.station_file.read_text().replace("id=test", "id=bpm").replace("[field.display_text]", "identity={play_id}\n[field.play_id]")
        config_text += "\n[field.display_text]\npath=/title\n"
        self.files.station_file.write_text(config_text)
        old_path = self.files.root / "old.sqlite3"
        old = sqlite3.connect(old_path)
        old.execute("CREATE TABLE station_state(source,station,last_play_id,last_checked_at,last_posted_at)")
        old.execute("INSERT INTO station_state VALUES('xmplaylist','bpm','old-play','old-check','old-post')")
        old.commit(); old.close()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.files.main), "import-bpm", "--from-db", str(old_path)]), 0)
            self.assertEqual(main(["--config", str(self.files.main), "import-bpm", "--from-db", str(old_path)]), 0)
        report = readonly_report(self.files.root / "state.sqlite3")
        self.assertEqual(report["stations"][0]["last_seen_key"], identity_hash("old-play"))
        self.assertEqual(report["delivery_counts"], [])


class ExtractionTests(unittest.TestCase):
    def test_json_pointer_arrays_and_escaped_keys(self):
        self.assertEqual(json_pointer({"a/b": [{"~key": "value"}]}, "/a~1b/0/~0key"), "value")
        self.assertIsNone(json_pointer(["no-negative-index"], "/-1"))

    def test_html_tolerates_nested_labels_and_unclosed_markup(self):
        config = station(method="html", fields=(Field("display_text", selector="td.streamdata", after_label="Current Song:"),))
        self.assertEqual(parse_body(HTML, config).display_text, 'A & B - "Song"')

    def test_scraper_error_text_is_not_a_song(self):
        config = station(method="html", fields=(Field("display_text", selector="td.streamdata", after_label="Current Song:"),))
        with self.assertRaises(SourceError):
            parse_body(b"Error: HTTPConnectionPool(host='knhc-ice.streamguys1.com', port=80): Read timed out. (read timeout=None)", config)
        with self.assertRaises(SourceError):
            play("Error: Read timed out")

    def test_xml_xpath_namespace_attributes_and_multiple_values(self):
        config = station(method="xml", fields=(Field("title", path="/n:now/n:title"),
                            Field("artists", path="/n:now/n:artist", multiple=True, join=" & "),
                            Field("play_id", path="/n:now/@id")), namespaces={"n": "urn:radio"}, identity="{play_id}")
        result = parse_body(b'<now xmlns="urn:radio" id="42"><title>Song</title><artist>A</artist><artist>B</artist></now>', config)
        self.assertEqual(result.display_text, "A & B - Song")
        self.assertEqual(result.identity, "42")

    def test_xml_doctype_cannot_read_local_files(self):
        config = station(method="xml", fields=(Field("title", path="/now"),))
        with self.assertRaises(SourceError):
            parse_body(b'<!DOCTYPE now [<!ENTITY x SYSTEM "file:///etc/passwd">]><now>&x;</now>', config)

    def test_json_serialization_and_color_match_legacy_behavior(self):
        text = 'Beyonce & Friends - "Quoted" \\ Mix <Live>'
        config = station()
        result = play(text, config)
        payload = build_payload(config, result)
        roundtrip = json.loads(json.dumps(payload, ensure_ascii=False))
        attachment = roundtrip["attachments"][0]
        self.assertEqual(attachment["color"], "#" + hashlib.sha1(text.encode()).hexdigest()[:6])
        self.assertIn('&amp;', attachment["fields"][0]["value"])
        self.assertIn('"Quoted"', attachment["fields"][0]["value"])

    def test_bpm_format_artwork_identity_and_color_preserved(self):
        config = station(id="bpm", identity="{play_id}", fields=(Field("title", path="/track/title"),
                         Field("artists", path="/track/artists", multiple=True), Field("play_id", path="/id"),
                         Field("image_url", path="/image", required=False)),
                         message=Message(layout="section", text="*{title}*\n{artists}", fallback="{title} - {artists}", color_key="{match_key}"))
        result = parse_body(json.dumps({"id": "play-1", "track": {"title": "Song (Remix)", "artists": ["B", "A"]}, "image": "https://example.com/cover.jpg"}).encode(), config)
        payload = build_payload(config, result)["attachments"][0]
        self.assertEqual(result.fields["match_key"], "song::a|b")
        self.assertEqual(result.identity, "play-1")
        self.assertEqual(payload["blocks"][0]["text"]["text"], "*Song (Remix)*\nB, A")
        self.assertEqual(payload["color"], "#" + hashlib.sha1(b"song::a|b").hexdigest()[:6])
        self.assertIn("accessory", payload["blocks"][0])

    def test_bad_optional_artwork_cannot_crash_the_daemon(self):
        config = station(fields=(Field("title", path="/title"), Field("image_url", path="/image", required=False)),
                         message=Message(layout="section", text="{title}"))
        result = parse_body(b'{"title":"Song","image":"https://[invalid"}', config)
        self.assertNotIn("accessory", build_payload(config, result)["attachments"][0]["blocks"][0])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.state = State(self.path)
        self.addCleanup(lambda: self.state.close() if self.state else None)

    def test_baseline_and_repeated_polls_are_suppressed(self):
        config = station()
        for _ in range(5):
            delivery, _ = self.state.observe(config, play())
            self.assertIsNone(delivery)
        report = self.state.report()
        self.assertEqual(report["stations"][0]["checks"], 5)
        self.assertEqual(report["stations"][0]["suppressed"], 5)
        self.assertEqual(report["delivery_counts"], [])

    def test_change_delivered_once_but_later_repeat_is_allowed(self):
        config = station()
        self.state.observe(config, play("A"))
        for text in ("B", "A"):
            record, _ = self.state.observe(config, play(text))
            self.assertIsNotNone(record)
            self.assertTrue(self.state.begin_delivery(config.id, record["sequence"], "hash"))
            self.state.finish_delivery(config.id, record["sequence"], DeliveryResult("sent", "confirmed", 200))
            self.assertIsNone(self.state.observe(config, play(text))[0])
        self.assertEqual(self.state.report()["delivery_counts"], [{"station": "test", "status": "sent", "count": 2}])

    def test_crash_after_post_started_is_not_replayed(self):
        config = station(initial_post=True)
        record, _ = self.state.observe(config, play())
        self.state.begin_delivery(config.id, record["sequence"], "hash")
        self.state.close()
        self.state = State(self.path)
        self.assertIsNone(self.state.observe(config, play())[0])
        self.assertEqual(self.state.report()["recent_deliveries"][0]["status"], "uncertain")

    def test_failed_fetch_preserves_last_valid_identity(self):
        self.state.observe(station(), play())
        before = self.state.report()["stations"][0]["last_seen_key"]
        self.state.failed_fetch("test", "timeout")
        after = self.state.report()["stations"][0]
        self.assertEqual(before, after["last_seen_key"])
        self.assertEqual(after["consecutive_errors"], 1)

    def test_restart_keeps_confirmed_delivery_suppressed(self):
        config = station(initial_post=True)
        record, _ = self.state.observe(config, play())
        self.state.begin_delivery(config.id, record["sequence"], "hash")
        self.state.finish_delivery(config.id, record["sequence"], DeliveryResult("sent", "confirmed", 200))
        self.state.close(); self.state = State(self.path)
        self.assertIsNone(self.state.observe(config, play())[0])

    def test_instance_lock_excludes_a_second_process(self):
        with InstanceLock(self.path):
            with self.assertRaises(RuntimeError):
                with InstanceLock(self.path):
                    pass

    def test_delivery_retry_limit_is_three_and_state_not_falsely_sent(self):
        config = station(initial_post=True)
        record, _ = self.state.observe(config, play())
        for _ in range(3):
            self.assertTrue(self.state.begin_delivery("test", record["sequence"], "hash"))
            self.state.finish_delivery("test", record["sequence"], DeliveryResult("retryable", "rate_limited", 429))
        self.assertFalse(self.state.begin_delivery("test", record["sequence"], "hash"))
        self.assertEqual(self.state.report()["recent_deliveries"][0]["status"], "rejected")
        self.assertIsNone(self.state.report()["stations"][0]["last_delivered_at"])


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handlers = {}
        app = web.Application()
        async def route(request):
            return await self.handlers[request.path](request)
        app.router.add_route("*", "/{tail:.*}", route)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.base = "http://127.0.0.1:" + str(self.site._server.sockets[0].getsockname()[1])
        self.session = aiohttp.ClientSession()
        self.temp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.temp.name) / "state.sqlite3")

    async def asyncTearDown(self):
        self.state.close(); self.temp.cleanup()
        await self.session.close()
        await self.runner.cleanup()

    def network_station(self, **overrides):
        values = dict(url=self.base + "/feed", policy=Policy(connect_timeout=.1, read_timeout=.025, request_timeout=.07,
                      retry_budget=.3, retry_backoff=.001, retry_jitter=0))
        values.update(overrides)
        return station(**values)

    async def test_real_read_timeouts_retry_then_recover_icecast(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            if calls < 3:
                await asyncio.sleep(.06)
            return web.Response(body=HTML)
        self.handlers["/feed"] = handler
        config = self.network_station(method="html", fields=(Field("display_text", selector="td.streamdata", after_label="Current Song:"),))
        result = await fetch_play(self.session, config)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(calls, 3)
        self.assertEqual(result.play.display_text, 'A & B - "Song"')

    async def test_missing_dom_retried_then_recovers(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return web.Response(body=b"temporarily unavailable" if calls < 3 else HTML)
        self.handlers["/feed"] = handler
        config = self.network_station(method="html", fields=(Field("display_text", selector="td.streamdata", after_label="Current Song:"),))
        self.assertEqual((await fetch_play(self.session, config)).attempts, 3)

    async def test_retry_budget_bounds_slow_source(self):
        async def handler(request):
            await asyncio.sleep(.2)
            return web.json_response({"title": "Late"})
        self.handlers["/feed"] = handler
        config = self.network_station(policy=Policy(connect_timeout=.02, read_timeout=.02, request_timeout=.03,
                                      retry_budget=.055, retry_backoff=.001, retry_jitter=0))
        start = asyncio.get_running_loop().time()
        with self.assertRaises(SourceError):
            await fetch_play(self.session, config)
        self.assertLess(asyncio.get_running_loop().time() - start, .13)

    async def test_permanent_http_error_not_retried(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return web.Response(status=404)
        self.handlers["/feed"] = handler
        with self.assertRaises(SourceError):
            await fetch_play(self.session, self.network_station())
        self.assertEqual(calls, 1)

    async def test_retry_after_exceeding_budget_does_not_hammer(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return web.Response(status=429, headers={"Retry-After": "60"})
        self.handlers["/feed"] = handler
        with self.assertRaises(SourceError):
            await fetch_play(self.session, self.network_station())
        self.assertEqual(calls, 1)

    async def test_failed_fetch_never_posts_error_text(self):
        async def handler(request):
            return web.Response(text="Error: Read timed out")
        self.handlers["/feed"] = handler
        config = self.network_station(method="html", fields=(Field("display_text", selector="td.streamdata", after_label="Current Song:"),))
        with patch("radio_now_playing.service.post_payload", new_callable=AsyncMock) as sender:
            await poll_station(self.session, config, self.state)
            sender.assert_not_awaited()
        self.assertEqual(self.state.report()["delivery_counts"], [])

    async def test_slow_station_does_not_block_fast_station(self):
        slow_entered = asyncio.Event()
        fast_served = asyncio.Event()
        release_slow = asyncio.Event()
        async def slow(request):
            slow_entered.set()
            await release_slow.wait()
            return web.json_response({"title": "Slow"})
        async def fast(request):
            fast_served.set()
            return web.json_response({"title": "Fast"})
        self.handlers["/slow"] = slow; self.handlers["/fast"] = fast
        policy = Policy(connect_timeout=1, read_timeout=5, request_timeout=5, retry_budget=5)
        slow_config = self.network_station(id="slow", url=self.base + "/slow", policy=policy)
        fast_config = self.network_station(id="fast", url=self.base + "/fast", policy=policy)
        stop = asyncio.Event()
        tasks = [asyncio.create_task(station_loop(self.session, cfg, self.state, stop)) for cfg in (slow_config, fast_config)]
        try:
            await asyncio.wait_for(slow_entered.wait(), 3)
            await asyncio.wait_for(fast_served.wait(), 3)
            self.assertFalse(release_slow.is_set(), "Fast station must be served while the slow request remains blocked")
        finally:
            stop.set()
            release_slow.set()
            await asyncio.gather(*tasks)

    async def test_multiple_polls_and_restart_produce_one_post_per_change(self):
        current = "Song A"
        async def handler(request):
            return web.json_response({"title": current})
        self.handlers["/feed"] = handler
        config = self.network_station(initial_post=True)
        with patch("radio_now_playing.service.post_payload", new_callable=AsyncMock, return_value=DeliveryResult("sent", "confirmed", 200)) as sender:
            for _ in range(3): await poll_station(self.session, config, self.state)
            path = self.state.path
            self.state.close(); self.state = State(path)
            await poll_station(self.session, config, self.state)
            self.assertEqual(sender.await_count, 1)
            current = "Song B"
            for _ in range(3): await poll_station(self.session, config, self.state)
            self.assertEqual(sender.await_count, 2)

    async def test_ambiguous_post_is_not_retried(self):
        async def handler(request):
            return web.json_response({"title": "Song"})
        self.handlers["/feed"] = handler
        config = self.network_station(initial_post=True)
        with patch("radio_now_playing.service.post_payload", new_callable=AsyncMock, return_value=DeliveryResult("uncertain", "delivery_outcome_unknown")) as sender:
            for _ in range(3): await poll_station(self.session, config, self.state)
            self.assertEqual(sender.await_count, 1)
        self.assertEqual(self.state.report()["recent_deliveries"][0]["status"], "uncertain")

    async def test_webhook_timeout_is_uncertain_and_does_not_log_url(self):
        fake = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError(HOOK))
        context.__aexit__ = AsyncMock(return_value=None)
        fake.post.return_value = context
        result = await post_payload(fake, station(), {"text": "Song"})
        self.assertEqual(result.status, "uncertain")
        self.assertNotIn(HOOK, repr(result))

    async def test_split_slack_confirmation_is_read_to_completion(self):
        async def handler(request):
            response = web.StreamResponse(status=200)
            await response.prepare(request)
            await response.write(b"o")
            await asyncio.sleep(.01)
            await response.write(b"k")
            await response.write_eof()
            return response
        self.handlers["/hook"] = handler
        config = self.network_station(webhook_url=self.base + "/hook")
        result = await post_payload(self.session, config, {"text": "Test"})
        self.assertEqual(result.status, "sent")

    async def test_daemon_reload_rejects_invalid_config_and_survives_valid_reload(self):
        async def handler(request):
            return web.json_response({"title": "Stable"})
        self.handlers["/feed"] = handler
        files = TemporaryConfiguration()
        self.addCleanup(files.close)
        files.set_station(url=self.base + "/feed")
        log_path = files.root / "daemon.log"
        with log_path.open("w") as handle:
            process = subprocess.Popen([sys.executable, "-m", "radio_now_playing", "--config", str(files.main), "daemon"], stdout=handle, stderr=subprocess.STDOUT)
            try:
                async def wait_for_text(text):
                    for _ in range(150):
                        if text in log_path.read_text(): return
                        if process.poll() is not None: self.fail(log_path.read_text())
                        await asyncio.sleep(.02)
                    self.fail("Missing log event: " + text + "\n" + log_path.read_text())
                await wait_for_text('"event": "baseline"')
                files.station_file.write_text(files.station_file.read_text() + "[http]\nread_timout=8\n")
                os.kill(process.pid, signal.SIGHUP)
                await wait_for_text('"event": "reload_rejected"')
                self.assertIsNone(process.poll())
                files.set_station(url=self.base + "/feed")
                os.kill(process.pid, signal.SIGHUP)
                await wait_for_text('"event": "configuration_reloaded"')
                await wait_for_text('"event": "unchanged"')
                report = readonly_report(files.root / "state.sqlite3")
                self.assertEqual(report["delivery_counts"], [])
            finally:
                if process.poll() is None:
                    process.terminate()
                    for _ in range(100):
                        if process.poll() is not None: break
                        await asyncio.sleep(.02)
                    if process.poll() is None: process.kill()
                process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
