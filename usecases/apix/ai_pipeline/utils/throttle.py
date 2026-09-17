"""Async throttle / rate-limiting helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiolimiter import AsyncLimiter
from tqdm.asyncio import tqdm_asyncio

from ai_pipeline.programs_config.base import ThrottleConfig
from ai_pipeline.logging_config import get_logger

logger = get_logger("utils.throttle")


class Throttle:
    """Combines RPM limiter + concurrency semaphore + per-request timeout."""

    def __init__(self, config: ThrottleConfig) -> None:
        self.rpm_limiter = AsyncLimiter(config.requests_per_minute, time_period=60)
        self.semaphore = asyncio.Semaphore(config.max_concurrent)
        self.timeout = config.request_timeout
        logger.info(
            "Throttle init | rpm=%d concurrent=%d timeout=%ds",
            config.requests_per_minute, config.max_concurrent, config.request_timeout,
        )

    async def run(self, coro, label: str = ""):
        """Execute *coro* with throttling."""
        async with self.rpm_limiter:
            async with self.semaphore:
                try:
                    return await asyncio.wait_for(coro, timeout=self.timeout)
                except asyncio.TimeoutError:
                    logger.warning("Request timed out | %s", label)
                    return None


async def run_throttled(tasks_with_labels: list[tuple], throttle: Throttle):
    """Run a list of (coroutine, label) pairs through *throttle* with a progress bar."""
    coros = [throttle.run(coro, label) for coro, label in tasks_with_labels]
    return [await t for t in tqdm_asyncio.as_completed(coros)]
