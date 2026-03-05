"""Shared pytest fixtures for the Supplier Risk Intelligence Platform test suite.

Fixtures:
    mock_settings       — Settings with safe test values, reads no real .env
    mock_kafka_producer — In-memory Kafka mock that captures published messages
    mock_httpx_client   — respx router for mocking external HTTP calls

Rules (ADR-013):
    - No live external API calls in any test
    - No real Kafka broker required for unit tests
    - No real database connections in unit tests
    - respx used for all httpx mocking (never unittest.mock.patch for HTTP)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
import respx

from backend.app.config import Settings
from data.ingestion.sec_edgar.models import SECRawEvent

# ── Settings fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings() -> Settings:
    """Return a Settings instance with safe test values.

    Does NOT read from .env — all values are hardcoded for determinism.
    Inject into code under test via dependency injection or monkeypatching.
    """
    return Settings(
        environment="dev",
        debug=True,
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        kafka_bootstrap_servers="localhost:9092",
        kafka_security_protocol="PLAINTEXT",
        redis_url="redis://localhost:6379/1",  # DB 1 = test isolation
        sec_edgar_user_agent="SupplierRiskPlatformTest test@example.com",
        sec_edgar_base_url="https://data.sec.gov",
        sec_edgar_rate_limit=10,
        news_api_key="test-news-api-key",
        auth0_domain="test.auth0.com",
        auth0_audience="https://api.test.supplierrisk.com",
        openai_api_key="sk-test-key",
        llm_resolution_daily_limit=10,
    )


# ── Kafka mock fixture ────────────────────────────────────────────────────────


@dataclass
class PublishedMessage:
    """A single message captured by the mock Kafka producer."""

    topic: str
    payload: dict[str, Any]
    key: str | None = None


@dataclass
class MockKafkaProducer:
    """In-memory Kafka producer that captures published messages.

    Used in tests that call the producer. Provides the same public interface
    as SupplierRiskKafkaProducer so callers don't know they're talking to a mock.

    Usage:
        producer = mock_kafka_producer
        await producer.publish_sec_event(event)
        assert producer.published[0].topic == "raw.sec"
    """

    published: list[PublishedMessage] = field(default_factory=list)
    dlq_messages: list[PublishedMessage] = field(default_factory=list)
    _started: bool = False

    async def start(self) -> None:
        """Simulate producer startup."""
        self._started = True

    async def stop(self) -> None:
        """Simulate producer shutdown."""
        self._started = False

    async def __aenter__(self) -> "MockKafkaProducer":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def _publish(self, topic: str, payload: dict[str, Any]) -> bool:
        """Capture a message instead of sending to real Kafka."""
        self.published.append(PublishedMessage(topic=topic, payload=payload))
        return True

    async def _publish_to_dlq(
        self, topic: str, payload: dict[str, Any], error: str
    ) -> None:
        """Capture a DLQ message instead of sending to real Kafka."""
        dlq_topic = f"raw.dlq.{topic.split('.')[-1]}"
        self.dlq_messages.append(
            PublishedMessage(
                topic=dlq_topic,
                payload={"original_payload": payload, "error": error},
            )
        )

    async def publish_sec_event(self, event: SECRawEvent) -> bool:
        """Publish a SEC filing event — captures to self.published instead of Kafka.

        Mirrors SupplierRiskKafkaProducer.publish_sec_event so tests that inject
        this mock can verify what was published without a real broker.
        """
        try:
            payload = json.loads(event.model_dump_json())
            return await self._publish("raw.sec", payload)
        except Exception as exc:  # noqa: BLE001
            raw: dict[str, Any] = {}
            try:
                raw = event.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                pass
            await self._publish_to_dlq("raw.sec", raw, str(exc))
            return False

    def reset(self) -> None:
        """Clear all captured messages. Call between test cases."""
        self.published.clear()
        self.dlq_messages.clear()


@pytest.fixture
def mock_kafka_producer() -> MockKafkaProducer:
    """Return a fresh MockKafkaProducer for each test.

    The producer starts in stopped state — tests that need it started
    should either use it as an async context manager or call await .start().
    """
    return MockKafkaProducer()


# ── HTTP mock fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_httpx_client() -> respx.MockRouter:
    """Return a respx MockRouter for mocking external HTTP calls.

    Usage in tests:
        def test_something(mock_httpx_client):
            mock_httpx_client.get("https://data.sec.gov/...").mock(
                return_value=httpx.Response(200, json={...})
            )
            # call code that makes httpx requests — they'll hit the mock

    The router raises httpx.ConnectError for any unregistered URL,
    preventing accidental live calls during tests.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router
