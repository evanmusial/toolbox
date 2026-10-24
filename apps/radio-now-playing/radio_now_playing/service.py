from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal

import aiohttp

from .config import ConfigError, load_configuration, summary
from .extract import SourceError, build_payload
from .http import DeliveryResult, fetch_play, post_payload
from .state import InstanceLock, State, now_iso


LOGGER = logging.getLogger("radio-now-playing")


def event(name, **values):
    level = logging.WARNING if name in ("fetch_exhausted", "station_fetch_failed", "delivery_uncertain", "delivery_rejected", "reload_rejected") else logging.INFO
    LOGGER.log(level, json.dumps({"at": now_iso(), "event": name, **values}, ensure_ascii=False, sort_keys=True))


async def poll_station(session, station, state):
    try:
        fetched = await fetch_play(session, station, log=event)
    except SourceError as exc:
        state.failed_fetch(station.id, exc.reason)
        event("station_fetch_failed", station=station.id, reason=exc.reason)
        return
    play = fetched.play
    delivery, disposition = state.observe(station, play)
    if delivery is None:
        event(disposition, station=station.id, identity=play.identity_key[:12], text=play.display_text, fetch_attempts=fetched.attempts)
        return
    payload = build_payload(station, play)
    payload_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    sequence = delivery["sequence"]
    if not state.begin_delivery(station.id, sequence, payload_hash):
        event("delivery_suppressed", station=station.id, sequence=sequence)
        return
    try:
        result = await post_payload(session, station, payload)
    except asyncio.CancelledError:
        state.finish_delivery(station.id, sequence, DeliveryResult("uncertain", "shutdown_during_delivery"))
        event("delivery_uncertain", station=station.id, sequence=sequence, reason="shutdown_during_delivery")
        raise
    status = state.finish_delivery(station.id, sequence, result)
    event("posted" if status == "sent" else f"delivery_{status}", station=station.id,
          sequence=sequence, identity=play.identity_key[:12], text=play.display_text,
          fetch_attempts=fetched.attempts, http_status=result.http_status, reason=result.reason)


async def station_loop(session, station, state, stop):
    loop = asyncio.get_running_loop()
    due = loop.time()
    while not stop.is_set():
        await poll_station(session, station, state)
        due += station.poll_interval
        if due <= loop.time():
            due = loop.time() + station.poll_interval
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0, due - loop.time()))
        except asyncio.TimeoutError:
            pass


async def drain(tasks, stop):
    stop.set()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=40)
    except asyncio.TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_daemon(config):
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    reload_requested = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, shutdown.set)
    loop.add_signal_handler(signal.SIGINT, shutdown.set)
    loop.add_signal_handler(signal.SIGHUP, reload_requested.set)
    with InstanceLock(config.state_db):
        state = State(config.state_db)
        try:
            while not shutdown.is_set():
                generation_stop = asyncio.Event()
                async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, trust_env=False) as session:
                    tasks = [asyncio.create_task(station_loop(session, station, state, generation_stop), name=station.id)
                             for station in config.enabled]
                    event("daemon_ready", **summary(config))
                    shutdown_wait = asyncio.create_task(shutdown.wait())
                    reload_wait = asyncio.create_task(reload_requested.wait())
                    try:
                        while True:
                            done, _ = await asyncio.wait([shutdown_wait, reload_wait, *tasks], return_when=asyncio.FIRST_COMPLETED)
                            failed = [t for t in tasks if t in done]
                            if failed:
                                # A programming/state failure must restart the daemon, not silently lose a station.
                                for task in failed:
                                    task.result()
                                raise RuntimeError("Station task exited unexpectedly")
                            if shutdown_wait in done:
                                await drain(tasks, generation_stop)
                                break
                            reload_requested.clear()
                            try:
                                replacement = load_configuration(config.path)
                                if replacement.state_db != config.state_db:
                                    raise ConfigError("Changing state_db requires a controlled migration")
                            except ConfigError as exc:
                                event("reload_rejected", reason=str(exc))
                                reload_wait = asyncio.create_task(reload_requested.wait())
                                continue
                            await drain(tasks, generation_stop)
                            config = replacement
                            LOGGER.setLevel(config.log_level)
                            event("configuration_reloaded", stations=[s.id for s in config.enabled])
                            break
                    finally:
                        for waiter in (shutdown_wait, reload_wait):
                            waiter.cancel()
                        await asyncio.gather(shutdown_wait, reload_wait, return_exceptions=True)
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            event("daemon_stopped")
        finally:
            state.close()


async def fetch_preview(config, station_id=None):
    selected = [station for station in config.enabled if station_id is None or station.id == station_id]
    if not selected:
        raise ConfigError("Requested station is not enabled")
    async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, trust_env=False) as session:
        async def one(station):
            try:
                fetched = await fetch_play(session, station)
                return {"station": station.id, "ok": True, "attempts": fetched.attempts,
                        "identity": fetched.play.identity, "fields": fetched.play.fields,
                        "payload": build_payload(station, fetched.play)}
            except SourceError as exc:
                return {"station": station.id, "ok": False, "reason": exc.reason}
        return await asyncio.gather(*(one(station) for station in selected))
