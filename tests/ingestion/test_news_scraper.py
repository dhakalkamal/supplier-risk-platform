"""Tests for data.ingestion.news.scraper.

All HTTP calls are mocked via respx — no live API requests.
Tenacity retry wait is overridden with wait_none() to avoid real delays.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from tenacity import wait_none

from data.ingestion.news.models import RawArticle
from data.ingestion.news.scraper import (
    SOURCE_CREDIBILITY,
    GDELTClient,
    NewsAPIClient,
    RetryableHTTPError,
    _article_id_from_url,
    _credibility_for_url,
)

# ── Shared fixture data ────────────────────────────────────────────────────────

_ARTICLE_URL = "https://reuters.com/business/acme-corp-files-bankruptcy-2024-01-15/"

_NEWSAPI_ARTICLE = {
    "source": {"name": "Reuters"},
    "title": "Acme Corp files for bankruptcy",
    "content": "Acme Corp announced Chapter 11 filing on Monday.",
    "url": _ARTICLE_URL,
    "publishedAt": "2024-01-15T10:00:00Z",
}

_NEWSAPI_RESPONSE = {
    "status": "ok",
    "totalResults": 1,
    "articles": [_NEWSAPI_ARTICLE],
}

_GDELT_RESPONSE = {
    "articles": [
        {
            "url": "https://apnews.com/article/factory-fire-supply-chain",
            "title": "Factory fire disrupts supply chain",
            "domain": "apnews.com",
            "seendate": "20240115T120000Z",
        }
    ]
}

_NEWSAPI_BASE = "https://newsapi.org/v2/everything"
_GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def newsapi_client(mock_settings) -> NewsAPIClient:
    return NewsAPIClient(settings=mock_settings, retry_wait=wait_none())


@pytest.fixture
def gdelt_client() -> GDELTClient:
    return GDELTClient(retry_wait=wait_none())


# ── article_id determinism ─────────────────────────────────────────────────────


def test_article_id_is_sha256_of_url():
    """article_id must be the sha256 hex digest of the URL."""
    url = "https://reuters.com/some-article"
    expected = hashlib.sha256(url.encode()).hexdigest()
    assert _article_id_from_url(url) == expected


def test_article_id_is_deterministic():
    """Calling _article_id_from_url twice with the same URL returns same value."""
    url = "https://bloomberg.com/news/article/123"
    assert _article_id_from_url(url) == _article_id_from_url(url)


def test_duplicate_urls_produce_same_article_id():
    """Two RawArticle objects from the same URL must have identical article_ids."""
    url = "https://ft.com/content/duplicate-story"
    id1 = _article_id_from_url(url)
    id2 = _article_id_from_url(url)
    assert id1 == id2


def test_different_urls_produce_different_article_ids():
    """Different URLs must not collide."""
    url_a = "https://reuters.com/article-a"
    url_b = "https://reuters.com/article-b"
    assert _article_id_from_url(url_a) != _article_id_from_url(url_b)


# ── Source credibility ─────────────────────────────────────────────────────────


def test_credibility_reuters():
    assert _credibility_for_url("https://reuters.com/article") == SOURCE_CREDIBILITY["reuters.com"]


def test_credibility_bloomberg():
    assert _credibility_for_url("https://bloomberg.com/news/123") == 1.0


def test_credibility_ft():
    assert _credibility_for_url("https://ft.com/content/abc") == 0.95


def test_credibility_wsj():
    assert _credibility_for_url("https://wsj.com/articles/xyz") == 0.95


def test_credibility_unknown_domain_defaults_to_50():
    assert _credibility_for_url("https://somerandomsite.io/news") == 0.50


def test_credibility_www_prefix_stripped():
    """www.reuters.com should resolve to the same credibility as reuters.com."""
    assert _credibility_for_url("https://www.reuters.com/article") == 1.0


# ── NewsAPIClient happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_recent_articles_returns_raw_articles(
    newsapi_client, mock_httpx_client
):
    """fetch_recent_articles returns a list of RawArticle on a 200 response."""
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        return_value=httpx.Response(200, json=_NEWSAPI_RESPONSE)
    )
    articles = await newsapi_client.fetch_recent_articles("Acme Corp", hours_back=24)

    assert len(articles) == 1
    assert isinstance(articles[0], RawArticle)
    assert articles[0].title == "Acme Corp files for bankruptcy"
    assert articles[0].ingestion_source == "newsapi"


@pytest.mark.asyncio
async def test_fetch_recent_articles_article_id_is_sha256_of_url(
    newsapi_client, mock_httpx_client
):
    """article_id on a fetched article must equal sha256(url)."""
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        return_value=httpx.Response(200, json=_NEWSAPI_RESPONSE)
    )
    articles = await newsapi_client.fetch_recent_articles("Acme Corp")
    expected_id = hashlib.sha256(_ARTICLE_URL.encode()).hexdigest()
    assert articles[0].article_id == expected_id


@pytest.mark.asyncio
async def test_fetch_recent_articles_assigns_credibility_by_domain(
    newsapi_client, mock_httpx_client
):
    """source_credibility is assigned from SOURCE_CREDIBILITY, not from the API."""
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        return_value=httpx.Response(200, json=_NEWSAPI_RESPONSE)
    )
    articles = await newsapi_client.fetch_recent_articles("Acme Corp")
    # URL is reuters.com → credibility 1.0
    assert articles[0].source_credibility == 1.0


@pytest.mark.asyncio
async def test_fetch_recent_articles_deduplicates_across_pages(
    newsapi_client, mock_httpx_client
):
    """Articles with the same URL appearing on multiple pages are deduplicated."""
    full_page = {"status": "ok", "articles": [_NEWSAPI_ARTICLE] * 100}
    single_article = {"status": "ok", "articles": [_NEWSAPI_ARTICLE]}
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        side_effect=[
            httpx.Response(200, json=full_page),
            httpx.Response(200, json=single_article),
        ]
    )
    articles = await newsapi_client.fetch_recent_articles("Acme Corp")
    # First page: 100 identical URLs → 1 unique. Second page: same URL → still 1.
    assert len(articles) == 1


@pytest.mark.asyncio
async def test_fetch_articles_for_supplier_returns_raw_articles(
    newsapi_client, mock_httpx_client
):
    """fetch_articles_for_supplier returns RawArticle list on 200."""
    from datetime import date

    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        return_value=httpx.Response(200, json=_NEWSAPI_RESPONSE)
    )
    articles = await newsapi_client.fetch_articles_for_supplier(
        company_name="Acme Corp",
        from_date=date(2024, 1, 1),
        to_date=date(2024, 1, 31),
    )
    assert len(articles) == 1
    assert articles[0].ingestion_source == "newsapi"


# ── NewsAPIClient retry / error handling ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_recent_articles_retries_on_429(newsapi_client, mock_httpx_client):
    """A single 429 triggers a retry; second attempt succeeds."""
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=_NEWSAPI_RESPONSE),
        ]
    )
    articles = await newsapi_client.fetch_recent_articles("Acme Corp")
    assert len(articles) == 1


@pytest.mark.asyncio
async def test_fetch_recent_articles_raises_after_three_429s(
    newsapi_client, mock_httpx_client
):
    """Three consecutive 429s exhaust retries and raise RetryableHTTPError."""
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(429),
        ]
    )
    with pytest.raises(RetryableHTTPError):
        await newsapi_client.fetch_recent_articles("Acme Corp")


# ── GDELTClient ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gdelt_fetch_articles_returns_raw_articles(
    gdelt_client, mock_httpx_client
):
    """GDELTClient.fetch_articles returns RawArticle list on 200."""
    mock_httpx_client.get(_GDELT_BASE).mock(
        return_value=httpx.Response(200, json=_GDELT_RESPONSE)
    )
    articles = await gdelt_client.fetch_articles("factory fire")

    assert len(articles) == 1
    assert isinstance(articles[0], RawArticle)
    assert articles[0].ingestion_source == "gdelt"
    assert articles[0].content is None  # GDELT artlist mode has no body


@pytest.mark.asyncio
async def test_gdelt_article_id_is_sha256_of_url(gdelt_client, mock_httpx_client):
    """GDELT article_id is sha256(url) — same as NewsAPI articles."""
    mock_httpx_client.get(_GDELT_BASE).mock(
        return_value=httpx.Response(200, json=_GDELT_RESPONSE)
    )
    articles = await gdelt_client.fetch_articles("factory fire")
    url = "https://apnews.com/article/factory-fire-supply-chain"
    expected = hashlib.sha256(url.encode()).hexdigest()
    assert articles[0].article_id == expected


@pytest.mark.asyncio
async def test_gdelt_assigns_credibility_by_domain(gdelt_client, mock_httpx_client):
    """GDELT articles get credibility from SOURCE_CREDIBILITY dict."""
    mock_httpx_client.get(_GDELT_BASE).mock(
        return_value=httpx.Response(200, json=_GDELT_RESPONSE)
    )
    articles = await gdelt_client.fetch_articles("factory fire")
    # URL is apnews.com → credibility 1.0
    assert articles[0].source_credibility == 1.0


@pytest.mark.asyncio
async def test_gdelt_deduplicates_by_article_id(gdelt_client, mock_httpx_client):
    """Duplicate URLs in GDELT response are deduplicated by article_id."""
    duplicate_response = {
        "articles": [
            _GDELT_RESPONSE["articles"][0],
            _GDELT_RESPONSE["articles"][0],
        ]
    }
    mock_httpx_client.get(_GDELT_BASE).mock(
        return_value=httpx.Response(200, json=duplicate_response)
    )
    articles = await gdelt_client.fetch_articles("factory fire")
    assert len(articles) == 1


# ── GDELT fallback scenario ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gdelt_called_as_fallback_when_newsapi_exhausted(
    newsapi_client, gdelt_client, mock_httpx_client
):
    """When NewsAPI raises RetryableHTTPError (429), GDELTClient provides results."""
    mock_httpx_client.get(_NEWSAPI_BASE).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(429),
        ]
    )
    mock_httpx_client.get(_GDELT_BASE).mock(
        return_value=httpx.Response(200, json=_GDELT_RESPONSE)
    )

    # Simulate the DAG fallback logic
    try:
        articles = await newsapi_client.fetch_recent_articles("factory fire")
    except RetryableHTTPError:
        articles = await gdelt_client.fetch_articles("factory fire")

    assert len(articles) == 1
    assert articles[0].ingestion_source == "gdelt"
