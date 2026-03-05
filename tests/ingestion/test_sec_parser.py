"""Tests for data.ingestion.sec_edgar.parser.SECFinancialsParser.

Uses real XBRL fixture data from tests/fixtures/sec_company_facts.json.
No external HTTP calls.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from data.ingestion.sec_edgar.models import FinancialSnapshot
from data.ingestion.sec_edgar.parser import GOING_CONCERN_PHRASES, SECFinancialsParser

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def parser() -> SECFinancialsParser:
    return SECFinancialsParser()


@pytest.fixture
def company_facts() -> dict:
    with open(FIXTURES_DIR / "sec_company_facts.json") as fh:
        return json.load(fh)


@pytest.fixture
def snapshot(parser, company_facts) -> FinancialSnapshot:
    return parser.extract_financials("0000789019", company_facts)


def _make_full_snapshot(**overrides) -> FinancialSnapshot:
    """Helper: build a FinancialSnapshot with all required fields for Z' scoring."""
    defaults = dict(
        cik="0000789019",
        period_end=date(2023, 6, 30),
        filing_type="10-K",
        total_assets=10_000_000.0,
        current_assets=3_000_000.0,
        current_liabilities=1_000_000.0,
        retained_earnings=2_000_000.0,
        ebit=1_500_000.0,
        shareholders_equity=5_000_000.0,
        total_liabilities=5_000_000.0,
        revenue=8_000_000.0,
        source_url="https://data.sec.gov/test",
        ingested_at=datetime.now(tz=timezone.utc),
    )
    defaults.update(overrides)
    return FinancialSnapshot(**defaults)


# ── extract_financials ────────────────────────────────────────────────────────


def test_extract_financials_total_assets(snapshot):
    assert snapshot.total_assets == 10_000_000.0


def test_extract_financials_current_assets(snapshot):
    assert snapshot.current_assets == 3_000_000.0


def test_extract_financials_total_liabilities(snapshot):
    assert snapshot.total_liabilities == 5_000_000.0


def test_extract_financials_revenue(snapshot):
    assert snapshot.revenue == 8_000_000.0


def test_extract_financials_ebit(snapshot):
    assert snapshot.ebit == 1_500_000.0


def test_extract_financials_period_end(snapshot):
    assert snapshot.period_end == date(2023, 6, 30)


def test_extract_financials_cik(snapshot):
    assert snapshot.cik == "0000789019"


def test_extract_financials_z_score_is_populated(snapshot):
    """extract_financials computes altman_z_score (not left as None)."""
    assert snapshot.altman_z_score is not None


# ── compute_altman_z_score ────────────────────────────────────────────────────


def test_altman_z_score_known_values(parser):
    """Z' score must match manual calculation for the fixture values.

    With the fixture values:
        X1 = (3M - 1M) / 10M = 0.2
        X2 = 2M / 10M       = 0.2
        X3 = 1.5M / 10M     = 0.15
        X4 = 5M / 5M        = 1.0
        X5 = 8M / 10M       = 0.8
        Z' = 0.717*0.2 + 0.847*0.2 + 3.107*0.15 + 0.420*1.0 + 0.998*0.8
    """
    snap = _make_full_snapshot()
    expected = (
        0.717 * 0.2
        + 0.847 * 0.2
        + 3.107 * 0.15
        + 0.420 * 1.0
        + 0.998 * 0.8
    )
    result = parser.compute_altman_z_score(snap)
    assert result is not None
    assert abs(result - expected) < 1e-9


def test_altman_z_score_grey_zone(parser):
    """Fixture values should land in the grey zone (1.23 < Z' < 2.90)."""
    snap = _make_full_snapshot()
    z = parser.compute_altman_z_score(snap)
    assert z is not None
    assert 1.23 < z < 2.90


def test_altman_z_score_none_when_total_assets_missing(parser):
    snap = _make_full_snapshot(total_assets=None)
    assert parser.compute_altman_z_score(snap) is None


def test_altman_z_score_none_when_revenue_missing(parser):
    snap = _make_full_snapshot(revenue=None)
    assert parser.compute_altman_z_score(snap) is None


def test_altman_z_score_none_when_ebit_missing(parser):
    snap = _make_full_snapshot(ebit=None)
    assert parser.compute_altman_z_score(snap) is None


def test_altman_z_score_none_when_total_assets_zero(parser):
    """Zero total_assets avoids division-by-zero and returns None."""
    snap = _make_full_snapshot(total_assets=0.0)
    assert parser.compute_altman_z_score(snap) is None


# ── detect_going_concern ──────────────────────────────────────────────────────


@pytest.mark.parametrize("phrase", GOING_CONCERN_PHRASES)
def test_detect_going_concern_true_for_each_phrase(parser, phrase):
    """Returns True for every phrase in GOING_CONCERN_PHRASES."""
    text = f"The auditors noted that {phrase} exists for the entity."
    assert parser.detect_going_concern(text) is True


def test_detect_going_concern_false_for_normal_text(parser):
    text = (
        "The company delivered record revenue and operating income in fiscal 2023. "
        "Management is confident in continued growth."
    )
    assert parser.detect_going_concern(text) is False


def test_detect_going_concern_is_case_insensitive(parser):
    text = "SUBSTANTIAL DOUBT ABOUT ITS ABILITY TO CONTINUE AS A GOING CONCERN."
    assert parser.detect_going_concern(text) is True


# ── get_latest_value ──────────────────────────────────────────────────────────


def test_get_latest_value_returns_most_recent(parser):
    """Selects the entry with the latest end date."""
    facts = {
        "Assets": {
            "units": {
                "USD": [
                    {"end": "2022-06-30", "val": 900_000.0, "form": "10-K"},
                    {"end": "2023-06-30", "val": 1_000_000.0, "form": "10-K"},
                ]
            }
        }
    }
    assert parser.get_latest_value(facts, ["Assets"]) == 1_000_000.0


def test_get_latest_value_tries_fallback_concept(parser):
    """Falls through to the second concept when the primary is missing."""
    facts = {
        "SalesRevenueNet": {
            "units": {
                "USD": [{"end": "2023-06-30", "val": 5_000_000.0, "form": "10-K"}]
            }
        }
    }
    result = parser.get_latest_value(facts, ["Revenues", "SalesRevenueNet"])
    assert result == 5_000_000.0


def test_get_latest_value_returns_none_when_no_concept_matches(parser):
    assert parser.get_latest_value({}, ["Revenues", "SalesRevenueNet"]) is None


def test_get_latest_value_ignores_non_annual_forms(parser):
    """Only 10-K and 10-Q forms are considered; 8-K entries are ignored."""
    facts = {
        "Assets": {
            "units": {
                "USD": [
                    {"end": "2023-09-30", "val": 999.0, "form": "8-K"},
                    {"end": "2023-06-30", "val": 1_000_000.0, "form": "10-K"},
                ]
            }
        }
    }
    assert parser.get_latest_value(facts, ["Assets"]) == 1_000_000.0
