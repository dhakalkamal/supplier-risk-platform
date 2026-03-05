"""Tests for MockKafkaProducer (conftest) and SupplierRiskKafkaProducer interface.

Uses the mock_kafka_producer fixture — no real Kafka broker required.
Verifies correct topic routing, DLQ behaviour, and context manager lifecycle.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from data.ingestion.sec_edgar.models import FinancialSnapshot, SECRawEvent
from data.pipeline.kafka_producer import SupplierRiskKafkaProducer

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_event(**overrides) -> SECRawEvent:
    """Build a valid SECRawEvent with sensible defaults."""
    now = datetime.now(tz=timezone.utc)
    snapshot = FinancialSnapshot(
        cik="0000789019",
        period_end=date(2023, 6, 30),
        filing_type="10-K",
        source_url="https://data.sec.gov/test",
        ingested_at=now,
    )
    defaults: dict = dict(
        cik="0000789019",
        company_name="MICROSOFT CORP",
        filing_type="10-K",
        filed_date=date(2023, 7, 27),
        period_of_report=date(2023, 6, 30),
        financials=snapshot,
        going_concern=False,
        ingested_at=now,
    )
    defaults.update(overrides)
    return SECRawEvent(**defaults)


# ── publish_sec_event ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_sec_event_goes_to_raw_sec_topic(mock_kafka_producer):
    """A valid event is published to the raw.sec topic."""
    event = _make_event()
    result = await mock_kafka_producer.publish_sec_event(event)

    assert result is True
    assert len(mock_kafka_producer.published) == 1
    assert mock_kafka_producer.published[0].topic == "raw.sec"


@pytest.mark.asyncio
async def test_publish_sec_event_payload_is_json_serialisable(mock_kafka_producer):
    """The captured payload must survive a round-trip through json.dumps."""
    await mock_kafka_producer.publish_sec_event(_make_event())
    payload = mock_kafka_producer.published[0].payload
    serialised = json.dumps(payload)
    assert "cik" in json.loads(serialised)


@pytest.mark.asyncio
async def test_publish_sec_event_payload_contains_cik(mock_kafka_producer):
    """The CIK from the event appears in the captured payload."""
    await mock_kafka_producer.publish_sec_event(_make_event())
    assert mock_kafka_producer.published[0].payload["cik"] == "0000789019"


@pytest.mark.asyncio
async def test_publish_sec_event_payload_contains_source(mock_kafka_producer):
    """The source field is always 'sec_edgar'."""
    await mock_kafka_producer.publish_sec_event(_make_event())
    assert mock_kafka_producer.published[0].payload["source"] == "sec_edgar"


# ── DLQ behaviour ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dlq_message_contains_original_payload_and_error(mock_kafka_producer):
    """_publish_to_dlq captures a message with original_payload and error."""
    original = {"cik": "0000789019", "note": "test"}
    await mock_kafka_producer._publish_to_dlq("raw.sec", original, "validation failed")

    assert len(mock_kafka_producer.dlq_messages) == 1
    dlq = mock_kafka_producer.dlq_messages[0]
    assert "raw.dlq" in dlq.topic
    assert dlq.payload["original_payload"] == original
    assert dlq.payload["error"] == "validation failed"


@pytest.mark.asyncio
async def test_dlq_topic_is_derived_from_source_topic(mock_kafka_producer):
    """DLQ topic is raw.dlq.{last segment of source topic}."""
    await mock_kafka_producer._publish_to_dlq("raw.sec", {}, "err")
    assert mock_kafka_producer.dlq_messages[0].topic == "raw.dlq.sec"


# ── Context manager lifecycle ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_manager_starts_and_stops(mock_kafka_producer):
    """Async context manager starts the producer on enter, stops on exit."""
    assert not mock_kafka_producer._started
    async with mock_kafka_producer:
        assert mock_kafka_producer._started
    assert not mock_kafka_producer._started


# ── reset ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_clears_published_and_dlq(mock_kafka_producer):
    """reset() empties both published and dlq_messages lists."""
    await mock_kafka_producer.publish_sec_event(_make_event())
    await mock_kafka_producer._publish_to_dlq("raw.sec", {}, "err")
    mock_kafka_producer.reset()

    assert mock_kafka_producer.published == []
    assert mock_kafka_producer.dlq_messages == []


# ── SupplierRiskKafkaProducer (real class, mocked aiokafka) ──────────────────


@pytest.fixture
def aiokafka_mock():
    """Patch AIOKafkaProducer with an AsyncMock so no real broker is needed."""
    with patch("data.pipeline.kafka_producer.AIOKafkaProducer") as mock_cls:
        instance = AsyncMock()
        mock_cls.return_value = instance
        yield instance


@pytest.mark.asyncio
async def test_real_producer_start_and_stop(mock_settings, aiokafka_mock):
    """start() creates and starts the aiokafka producer; stop() stops it."""
    producer = SupplierRiskKafkaProducer(settings=mock_settings)
    await producer.start()
    aiokafka_mock.start.assert_called_once()

    await producer.stop()
    aiokafka_mock.stop.assert_called_once()


@pytest.mark.asyncio
async def test_real_producer_context_manager(mock_settings, aiokafka_mock):
    """Async context manager calls start on enter and stop on exit."""
    async with SupplierRiskKafkaProducer(settings=mock_settings):
        aiokafka_mock.start.assert_called_once()
    aiokafka_mock.stop.assert_called_once()


@pytest.mark.asyncio
async def test_real_producer_publish_sec_event(mock_settings, aiokafka_mock):
    """publish_sec_event serialises the event and sends to raw.sec."""
    async with SupplierRiskKafkaProducer(settings=mock_settings) as producer:
        result = await producer.publish_sec_event(_make_event())

    assert result is True
    aiokafka_mock.send_and_wait.assert_called_once()
    call_kwargs = aiokafka_mock.send_and_wait.call_args
    assert call_kwargs[0][0] == "raw.sec"  # first positional arg = topic


@pytest.mark.asyncio
async def test_real_producer_dlq_on_send_failure(mock_settings, aiokafka_mock):
    """When send raises, the event is routed to the DLQ topic."""
    aiokafka_mock.send_and_wait.side_effect = [
        Exception("broker unavailable"),  # first call (raw.sec) fails
        None,  # second call (DLQ) succeeds
    ]
    async with SupplierRiskKafkaProducer(settings=mock_settings) as producer:
        result = await producer.publish_sec_event(_make_event())

    assert result is False
    assert aiokafka_mock.send_and_wait.call_count == 2
    dlq_call = aiokafka_mock.send_and_wait.call_args_list[1]
    assert "raw.dlq" in dlq_call[0][0]


@pytest.mark.asyncio
async def test_real_producer_publish_to_dlq_directly(mock_settings, aiokafka_mock):
    """_publish_to_dlq sends to raw.dlq.{source_suffix}."""
    async with SupplierRiskKafkaProducer(settings=mock_settings) as producer:
        await producer._publish_to_dlq("raw.sec", {"cik": "123"}, "parse error")

    call = aiokafka_mock.send_and_wait.call_args
    assert call[0][0] == "raw.dlq.sec"
    payload = call[1]["value"]
    assert payload["error"] == "parse error"
    assert payload["original_payload"] == {"cik": "123"}
