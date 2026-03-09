"""
Unit tests for raw event Pydantic schemas.

No database, no Kafka — pure schema validation only.
Run with: pytest tests/test_schemas.py -v
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from pipeline.schemas.raw_events import (
    SECRawEvent,
    NewsRawEvent,
    AISRawEvent,
    MacroRawEvent,
    GeoRawEvent,
    DeadLetterEvent,
    TOPIC_SCHEMA_MAP,
)

NOW = datetime.now(tz=timezone.utc)
TODAY = date.today()


# ─────────────────────────────────────────────────────────────────────────────
# SECRawEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestSECRawEvent:
    def _valid(self, **overrides) -> dict:
        base = dict(
            cik="0001234567",
            company_name="Acme Corp",
            filing_type="10-K",
            filed_date=TODAY,
            period_of_report=TODAY,
            financials={"revenue": 1_000_000},
            going_concern=False,
            ingested_at=NOW,
        )
        return {**base, **overrides}

    def test_valid(self):
        event = SECRawEvent.model_validate(self._valid())
        assert event.cik == "1234567"          # leading zeros stripped

    def test_cik_leading_zeros_stripped(self):
        event = SECRawEvent.model_validate(self._valid(cik="0000000001"))
        assert event.cik == "1"

    def test_invalid_filing_type(self):
        with pytest.raises(ValidationError, match="Unknown filing_type"):
            SECRawEvent.model_validate(self._valid(filing_type="FAKE"))

    def test_invalid_cik_letters(self):
        with pytest.raises(ValidationError, match="Invalid CIK"):
            SECRawEvent.model_validate(self._valid(cik="ABCDE"))

    def test_period_of_report_optional(self):
        data = self._valid()
        data["period_of_report"] = None
        event = SECRawEvent.model_validate(data)
        assert event.period_of_report is None


# ─────────────────────────────────────────────────────────────────────────────
# NewsRawEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestNewsRawEvent:
    def _valid(self, **overrides) -> dict:
        base = dict(
            article_id="abc-123",
            url="https://reuters.com/article/123",
            title="Supply chain disruption hits chip makers",
            content="Detailed article content here...",
            published_at=NOW,
            source_name="Reuters",
            ingestion_source="newsapi",
            ingested_at=NOW,
        )
        return {**base, **overrides}

    def test_valid(self):
        event = NewsRawEvent.model_validate(self._valid())
        assert event.ingestion_source == "newsapi"

    def test_invalid_ingestion_source(self):
        with pytest.raises(ValidationError, match="ingestion_source must be one of"):
            NewsRawEvent.model_validate(self._valid(ingestion_source="twitter"))

    def test_blank_content_rejected(self):
        with pytest.raises(ValidationError, match="content must not be blank"):
            NewsRawEvent.model_validate(self._valid(content="   "))

    def test_blank_article_id_rejected(self):
        with pytest.raises(ValidationError, match="article_id must not be blank"):
            NewsRawEvent.model_validate(self._valid(article_id="  "))

    def test_gdelt_source_accepted(self):
        event = NewsRawEvent.model_validate(self._valid(ingestion_source="gdelt"))
        assert event.ingestion_source == "gdelt"


# ─────────────────────────────────────────────────────────────────────────────
# AISRawEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestAISRawEvent:
    def _valid(self, **overrides) -> dict:
        base = dict(
            port_id="PORT-SIN-001",
            port_name="Port of Singapore",
            vessel_mmsi="123456789",
            vessel_name="MV Ocean Star",
            arrival_time=datetime(2024, 3, 1, 8, 0, tzinfo=timezone.utc),
            departure_time=datetime(2024, 3, 2, 12, 0, tzinfo=timezone.utc),
            cargo_type="CONTAINERS",
            ingested_at=NOW,
        )
        return {**base, **overrides}

    def test_valid(self):
        event = AISRawEvent.model_validate(self._valid())
        assert event.vessel_mmsi == "123456789"

    def test_mmsi_must_be_9_digits(self):
        with pytest.raises(ValidationError, match="MMSI must be exactly 9 digits"):
            AISRawEvent.model_validate(self._valid(vessel_mmsi="12345"))

    def test_mmsi_no_letters(self):
        with pytest.raises(ValidationError, match="MMSI must be exactly 9 digits"):
            AISRawEvent.model_validate(self._valid(vessel_mmsi="12345678A"))

    def test_arrival_after_departure_rejected(self):
        with pytest.raises(ValidationError, match="arrival_time.*after departure_time"):
            AISRawEvent.model_validate(self._valid(
                arrival_time=datetime(2024, 3, 5, tzinfo=timezone.utc),
                departure_time=datetime(2024, 3, 1, tzinfo=timezone.utc),
            ))

    def test_null_times_accepted(self):
        event = AISRawEvent.model_validate(
            self._valid(arrival_time=None, departure_time=None)
        )
        assert event.arrival_time is None


# ─────────────────────────────────────────────────────────────────────────────
# MacroRawEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestMacroRawEvent:
    def _valid(self, **overrides) -> dict:
        base = dict(
            series_id="gdp",
            series_name="Gross Domestic Product",
            observation_date=TODAY,
            value=25_000.5,
            unit="Billions of Dollars",
            ingested_at=NOW,
        )
        return {**base, **overrides}

    def test_valid(self):
        event = MacroRawEvent.model_validate(self._valid())
        assert event.series_id == "GDP"          # uppercased

    def test_series_id_uppercased(self):
        event = MacroRawEvent.model_validate(self._valid(series_id="unrate"))
        assert event.series_id == "UNRATE"

    def test_nan_value_rejected(self):
        import math
        with pytest.raises(ValidationError, match="value must be finite"):
            MacroRawEvent.model_validate(self._valid(value=math.nan))

    def test_inf_value_rejected(self):
        import math
        with pytest.raises(ValidationError, match="value must be finite"):
            MacroRawEvent.model_validate(self._valid(value=math.inf))

    def test_negative_value_allowed(self):
        event = MacroRawEvent.model_validate(self._valid(value=-3.5))
        assert event.value == -3.5


# ─────────────────────────────────────────────────────────────────────────────
# GeoRawEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestGeoRawEvent:
    def _valid(self, **overrides) -> dict:
        base = dict(
            event_id="geo-001",
            event_type="conflict",
            country="UA",
            region="Donetsk",
            event_date=TODAY,
            severity="high",
            source="acled",
            ingested_at=NOW,
        )
        return {**base, **overrides}

    def test_valid(self):
        event = GeoRawEvent.model_validate(self._valid())
        assert event.country == "UA"

    def test_country_lowercased_normalised(self):
        event = GeoRawEvent.model_validate(self._valid(country="ua"))
        assert event.country == "UA"

    def test_country_3_chars_rejected(self):
        with pytest.raises(ValidationError, match="ISO 3166-1 alpha-2"):
            GeoRawEvent.model_validate(self._valid(country="UKR"))

    def test_invalid_severity(self):
        with pytest.raises(ValidationError, match="severity must be one of"):
            GeoRawEvent.model_validate(self._valid(severity="catastrophic"))

    def test_invalid_source(self):
        with pytest.raises(ValidationError, match="source must be one of"):
            GeoRawEvent.model_validate(self._valid(source="twitter"))

    def test_region_optional(self):
        event = GeoRawEvent.model_validate(self._valid(region=None))
        assert event.region is None


# ─────────────────────────────────────────────────────────────────────────────
# TOPIC_SCHEMA_MAP coverage
# ─────────────────────────────────────────────────────────────────────────────

def test_topic_schema_map_covers_all_topics():
    expected = {"raw.sec", "raw.news", "raw.ais", "raw.macro", "raw.geo"}
    assert set(TOPIC_SCHEMA_MAP.keys()) == expected
