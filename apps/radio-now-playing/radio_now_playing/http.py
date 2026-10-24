from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .config import Station
from .extract import Play, SourceError, parse_body


def retry_after(value):
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            timestamp = parsedate_to_datetime(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return max(0.0, timestamp.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return None


@dataclass(frozen=True)
class Fetched:
    play: Play
    attempts: int


async def read_source(session, station: Station, remaining):
    policy = station.policy
    timeout = aiohttp.ClientTimeout(
        total=min(policy.request_timeout, remaining),
        connect=min(policy.connect_timeout, remaining),
        sock_connect=min(policy.connect_timeout, remaining),
        sock_read=min(policy.read_timeout, remaining),
        ceil_threshold=float("inf"),
    )
    async with session.get(station.url, headers=station.headers, timeout=timeout, max_redirects=5) as response:
        if response.status < 200 or response.status >= 300:
            raise SourceError(f"http_{response.status}", retryable=response.status in (408, 425, 429) or response.status >= 500,
                              retry_after=retry_after(response.headers.get("Retry-After")))
        chunks = []
        size = 0
        async for chunk in response.content.iter_chunked(65536):
            size += len(chunk)
            if size > policy.max_response_bytes:
                raise SourceError("response_too_large", retryable=False)
            chunks.append(chunk)
        return await asyncio.to_thread(parse_body, b"".join(chunks), station)


async def fetch_play(session, station: Station, log=None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + station.policy.retry_budget
    last = SourceError("retry_budget_exhausted")
    attempts = 0
    for attempt in range(1, station.policy.retry_attempts + 1):
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        attempts = attempt
        try:
            play = await asyncio.wait_for(read_source(session, station, remaining), timeout=remaining)
            return Fetched(play, attempt)
        except SourceError as exc:
            last = exc
        except asyncio.TimeoutError:
            last = SourceError("timeout")
        except aiohttp.ClientConnectionError:
            last = SourceError("connection_failed")
        except aiohttp.ClientError:
            last = SourceError("http_client_error")
        if not last.retryable or attempt == station.policy.retry_attempts:
            break
        delay = station.policy.retry_backoff * (2 ** (attempt - 1)) + random.uniform(0, station.policy.retry_jitter)
        if last.retry_after is not None:
            delay = max(delay, last.retry_after)
        if delay >= deadline - loop.time():
            break
        if log:
            log("fetch_retry", station=station.id, attempt=attempt, reason=last.reason, delay=round(delay, 3))
        await asyncio.sleep(delay)
    if log:
        log("fetch_exhausted", station=station.id, attempts=attempts, reason=last.reason)
    raise last


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    reason: str
    http_status: int | None = None
    retry_after: float | None = None


async def post_payload(session, station, payload):
    timeout = aiohttp.ClientTimeout(total=12, connect=5, sock_connect=5, sock_read=8, ceil_threshold=float("inf"))
    try:
        async with session.post(station.webhook_url, json=payload, timeout=timeout, allow_redirects=False) as response:
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(1024):
                size += len(chunk)
                if size > 1024:
                    return DeliveryResult("uncertain", "oversized_webhook_response", response.status)
                chunks.append(chunk)
            body = b"".join(chunks)
            if response.status == 200 and body.strip() == b"ok":
                return DeliveryResult("sent", "confirmed", 200)
            if response.status == 429:
                return DeliveryResult("retryable", "rate_limited", 429, retry_after(response.headers.get("Retry-After")) or 30)
            if 400 <= response.status < 500:
                return DeliveryResult("rejected", "webhook_rejected", response.status)
            return DeliveryResult("uncertain", "unconfirmed_response", response.status)
    except aiohttp.ClientConnectorError:
        return DeliveryResult("retryable", "connection_not_established", retry_after=30)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        # The server may have accepted the POST. Never resend it automatically.
        return DeliveryResult("uncertain", "delivery_outcome_unknown")
