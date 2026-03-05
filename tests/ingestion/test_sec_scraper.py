"""Tests for data.ingestion.sec_edgar.scraper.SECEdgarClient.

All HTTP calls are mocked via respx — no live SEC EDGAR requests.
Tenacity retry wait is overridden with wait_none() to avoid real delays.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from tenacity import wait_none

from data.ingestion.sec_edgar.models import CompanySearchResult, CompanySubmissions
from data.ingestion.sec_edgar.scraper import RetryableHTTPError, SECEdgarClient

# ── Shared fixture data ───────────────────────────────────────────────────────

SUBMISSIONS_DATA = {
    "cik": "0000789019",
    "name": "MICROSOFT CORP",
    "tickers": ["MSFT"],
    "exchanges": ["Nasdaq"],
    "filings": {
        "recent": {
            "accessionNumber": ["0001193125-23-201719"],
            "filingDate": ["2023-07-27"],
            "reportDate": ["2023-06-30"],
            "form": ["10-K"],
            "primaryDocument": ["msft-20230630.htm"],
        }
    },
}

SEARCH_DATA = {
    "hits": {
        "hits": [
            {
                "_source": {
                    "entity_id": "789019",
                    "entity_name": "MICROSOFT CORP",
                    "display_names": ["MICROSOFT CORP"],
                    "tickers": ["MSFT"],
                    "exchanges": ["Nasdaq"],
                }
            }
        ]
    }
}

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000789019.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index?q=&entity=Microsoft"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(mock_settings) -> SECEdgarClient:
    """SECEdgarClient with zero-wait retry and isolated test settings."""
    return SECEdgarClient(settings=mock_settings, retry_wait=wait_none())


# ── Happy-path tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_company_submissions_happy_path(client, mock_httpx_client):
    """Returns a correctly parsed CompanySubmissions on a 200 response."""
    mock_httpx_client.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=SUBMISSIONS_DATA)
    )
    result = await client.get_company_submissions("789019")

    assert isinstance(result, CompanySubmissions)
    assert result.name == "MICROSOFT CORP"
    assert result.tickers == ["MSFT"]
    assert result.exchanges == ["Nasdaq"]


@pytest.mark.asyncio
async def test_get_company_submissions_cik_is_padded(client, mock_httpx_client):
    """Input CIK '789019' must produce a request to CIK0000789019."""
    route = mock_httpx_client.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=SUBMISSIONS_DATA)
    )
    await client.get_company_submissions("789019")
    assert route.called


# ── Retry tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_company_submissions_retries_on_429(client, mock_httpx_client):
    """A single 429 triggers one retry; the second attempt succeeds."""
    mock_httpx_client.get(SUBMISSIONS_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=SUBMISSIONS_DATA),
        ]
    )
    result = await client.get_company_submissions("789019")
    assert result.name == "MICROSOFT CORP"


@pytest.mark.asyncio
async def test_get_company_submissions_retries_on_503(client, mock_httpx_client):
    """A single 503 triggers one retry; the second attempt succeeds."""
    mock_httpx_client.get(SUBMISSIONS_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=SUBMISSIONS_DATA),
        ]
    )
    result = await client.get_company_submissions("789019")
    assert result.name == "MICROSOFT CORP"


@pytest.mark.asyncio
async def test_get_company_submissions_three_failures_raises(client, mock_httpx_client):
    """Three consecutive retryable failures exhaust attempts and raise."""
    mock_httpx_client.get(SUBMISSIONS_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(429),
        ]
    )
    with pytest.raises(RetryableHTTPError):
        await client.get_company_submissions("789019")


# ── Search tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_company_returns_results(client, mock_httpx_client):
    """search_company parses EFTS hits into CompanySearchResult objects."""
    mock_httpx_client.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=SEARCH_DATA)
    )
    results = await client.search_company("Microsoft")

    assert len(results) == 1
    assert isinstance(results[0], CompanySearchResult)
    assert results[0].canonical_name == "MICROSOFT CORP"
    assert results[0].cik == "0000789019"
    assert results[0].tickers == ["MSFT"]


# ── get_company_facts ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_company_facts_returns_parsed_data(client, mock_httpx_client):
    """get_company_facts returns a CompanyFacts with entity name and facts."""
    from data.ingestion.sec_edgar.models import CompanyFacts

    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"
    facts_data = {
        "cik": 789019,
        "entityName": "MICROSOFT CORP",
        "facts": {"us-gaap": {"Assets": {}}},
    }
    mock_httpx_client.get(url).mock(return_value=httpx.Response(200, json=facts_data))
    result = await client.get_company_facts("789019")

    assert isinstance(result, CompanyFacts)
    assert result.entity_name == "MICROSOFT CORP"
    assert "us-gaap" in result.facts


# ── get_recent_filings ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_recent_filings_returns_list(client, mock_httpx_client):
    """get_recent_filings parses EFTS hits into Filing objects."""
    from datetime import date

    from data.ingestion.sec_edgar.models import Filing

    url = (
        "https://efts.sec.gov/LATEST/search-index"
        "?q=&forms=10-K,10-Q,8-K&dateRange=custom&startdt=2023-01-01"
    )
    filings_data = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "entity_id": "789019",
                        "file_type": "10-K",
                        "period_of_report": "2023-06-30",
                        "file_date": "2023-07-27",
                        "file_num": "0001193125-23-201719",
                    }
                }
            ]
        }
    }
    mock_httpx_client.get(url).mock(
        return_value=httpx.Response(200, json=filings_data)
    )
    results = await client.get_recent_filings(date(2023, 1, 1))

    assert len(results) == 1
    assert isinstance(results[0], Filing)
    assert results[0].filing_type == "10-K"


@pytest.mark.asyncio
async def test_get_recent_filings_skips_invalid_hits(client, mock_httpx_client):
    """Hits with missing/invalid date fields are skipped without raising."""
    from datetime import date

    url = (
        "https://efts.sec.gov/LATEST/search-index"
        "?q=&forms=10-K,10-Q,8-K&dateRange=custom&startdt=2023-01-01"
    )
    bad_hit = {"_source": {"entity_id": "789019", "period_of_report": "not-a-date"}}
    mock_httpx_client.get(url).mock(
        return_value=httpx.Response(200, json={"hits": {"hits": [bad_hit]}})
    )
    results = await client.get_recent_filings(date(2023, 1, 1))
    assert results == []


# ── Context manager ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_manager_closes_client(mock_settings, mock_httpx_client):
    """async with SECEdgarClient() creates and closes the httpx client."""
    mock_httpx_client.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=SUBMISSIONS_DATA)
    )
    async with SECEdgarClient(
        settings=mock_settings, retry_wait=wait_none()
    ) as sec_client:
        await sec_client.get_company_submissions("789019")
        assert sec_client._client is not None
    assert sec_client._client is None


# ── Rate-limiting test ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiting_15_concurrent_calls(client, mock_httpx_client):
    """15 concurrent requests all complete — semaphore does not deadlock."""
    mock_httpx_client.get(SUBMISSIONS_URL).mock(
        return_value=httpx.Response(200, json=SUBMISSIONS_DATA)
    )
    results = await asyncio.gather(
        *[client.get_company_submissions("789019") for _ in range(15)]
    )
    assert len(results) == 15
    assert all(r.name == "MICROSOFT CORP" for r in results)
