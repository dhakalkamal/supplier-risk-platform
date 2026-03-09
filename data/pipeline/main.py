"""
Pipeline consumer entrypoint.

Starts all five consumers concurrently.
Each consumer runs as an asyncio task; a signal handler shuts them all down cleanly.

Usage:
    python -m pipeline.main

Environment variables:
    DATABASE_URL    postgres://user:pass@localhost:5432/srip
    KAFKA_BOOTSTRAP kafka:9092
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import structlog

from pipeline.consumers.sources import ALL_CONSUMERS
from pipeline.db.repository import create_pool, PipelineRepository

# ─────────────────────────────────────────────────────────────────────────────
# Logging — structlog for machine-readable output (matches Datadog ingest)
# ─────────────────────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    kafka_bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")

    pool = await create_pool(database_url)
    repo = PipelineRepository(pool)

    tasks: list[asyncio.Task] = []
    for consumer_cls in ALL_CONSUMERS:
        consumer = consumer_cls(bootstrap_servers=kafka_bootstrap, repo=repo)
        task = asyncio.create_task(consumer.run(), name=consumer_cls.source)
        tasks.append(task)

    logger.info("All consumers started", count=len(tasks))

    # Graceful shutdown on SIGTERM / SIGINT
    loop = asyncio.get_running_loop()

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("Shutdown signal received", signal=signum)
        for t in tasks:
            t.cancel()

    loop.add_signal_handler(signal.SIGTERM, _shutdown, signal.SIGTERM, None)
    loop.add_signal_handler(signal.SIGINT,  _shutdown, signal.SIGINT,  None)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for consumer_cls, result in zip(ALL_CONSUMERS, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            logger.error(
                "Consumer exited with error",
                source=consumer_cls.source,
                error=str(result),
            )

    await pool.close()
    logger.info("Pipeline shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
