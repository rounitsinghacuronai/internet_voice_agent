"""Retry with exponential backoff + jitter. Provider-agnostic."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


class RetryExhausted(Exception):
    def __init__(self, attempts: int, last: Exception):
        super().__init__(f"all {attempts} attempts failed: {last}")
        self.attempts = attempts
        self.last = last


async def retry_async(
    fn: Callable[[], Awaitable],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 2.0,
    max_delay_s: float = 30.0,
    on_retry: Callable[[int, Exception], None] | None = None,
):
    """Run `fn` until it succeeds. Backoff: base·2^n + jitter, capped.

    on_retry(attempt_no, error) fires before each sleep — the manager uses it
    to flip delivery status to RETRYING and audit-log the error.
    """
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:            # noqa: BLE001 — provider errors vary
            last = e
            if attempt == max_attempts:
                break
            if on_retry:
                try:
                    on_retry(attempt, e)
                except Exception:          # audit must never kill the retry loop
                    log.exception("on_retry callback failed")
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay *= 0.8 + random.random() * 0.4         # ±20% jitter
            log.warning("notify retry %d/%d in %.1fs (%s)",
                        attempt, max_attempts, delay, e)
            await asyncio.sleep(delay)
    raise RetryExhausted(max_attempts, last or Exception("unknown"))
