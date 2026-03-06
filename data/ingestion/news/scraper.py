"""Async news API clients for NewsAPI.org and GDELT.

NewsAPIClient is the primary source — requires NEWS_API_KEY in settings.
GDELTClient is the free fallback — no API key, used when NewsAPI returns 429.

Both clients:
  - Return RawArticle with article_id = sha256(url) for deterministic deduplication
  - Assign source_credibility by domain from SOURCE_CREDIBILITY dict
  - Log all requests and responses with structlog
  - Retry on 429 / 503 with exponential backoff (tenacity)
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from backend.app.config import Settings, get_settings
from data.ingestion.news.models import RawArticle

log = structlog.get_logger()

# ── Constants ─────────────────────────────────────────────────────────────────

_NEWSAPI_BASE_URL = "https://newsapi.org/v2"
_GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_NEWSAPI_PAGE_SIZE = 100
_RETRYABLE_STATUSES = frozenset({429, 503})

SOURCE_CREDIBILITY: dict[str, float] = {
    "reuters.com": 1.0,
    "bloomberg.com": 1.0,
    "apnews.com": 1.0,
    "ft.com": 0.95,
    "wsj.com": 0.95,
    "cnbc.com": 0.85,
    "businessinsider.com": 0.70,
    "default": 0.50,
}


# ── Shared utilities ──────────────────────────────────────────────────────────


class RetryableHTTPError(Exception):
    """Raised for 429 and 503 HTTP responses to trigger tenacity retry."""


def _article_id_from_url(url: str) -> str:
    """Return sha256 hex digest of URL — deterministic article deduplication key."""
    return hashlib.sha256(url.encode()).hexdigest()


def _credibility_for_url(url: str) -> float:
    """Look up source credibility score by domain. Defaults to 0.50."""
    try:
        netloc = urlparse(url).netloc
        domain = netloc.removeprefix("www.")
    except Exception:
        return SOURCE_CREDIBILITY["default"]
    return SOURCE_CREDIBILITY.get(domain, SOURCE_CREDIBILITY["default"])


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


# ── NewsAPI client ────────────────────────────────────────────────────────────


class NewsAPIClient:
    """Client for NewsAPI.org.

    Fetches articles mentioning supplier names or arbitrary query strings.
    Handles pagination (page size 100), rate limiting, and deduplication
    by article_id (sha256 of URL).

    Raises RetryableHTTPError on 429/503 — tenacity retries up to 3 attempts
    with exponential backoff. On exhaustion, the error propagates to the caller.

    Args:
        settings: Application settings. Defaults to get_settings() singleton.
        retry_wait: Tenacity wait strategy. Pass wait_none() in tests.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(5)  # conservative default for free tier
        self._retry_wait: wait_base = retry_wait or wait_exponential(
            multiplier=1, min=2, max=10
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "NewsAPIClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        """Rate-limited GET with retry on 429/503."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(RetryableHTTPError),
            stop=stop_after_attempt(3),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:
                    log.info("newsapi.request", url=url, params=params)
                    response = await self._http.get(
                        url,
                        params={**params, "apiKey": self._settings.news_api_key},
                    )
                    log.info(
                        "newsapi.response",
                        status=response.status_code,
                        url=url,
                    )
                    if response.status_code in _RETRYABLE_STATUSES:
                        raise RetryableHTTPError(
                            f"HTTP {response.status_code} from {url} — will retry"
                        )
                    response.raise_for_status()
                    return response
        raise AssertionError("unreachable: tenacity reraises on exhaustion")  # pragma: no cover

    async def fetch_articles_for_supplier(
        self,
        company_name: str,
        from_date: date,
        to_date: date,
    ) -> list[RawArticle]:
        """Fetch all articles mentioning company_name between from_date and to_date.

        Paginates automatically until no more results are returned.
        Deduplicates by article_id across pages.

        Args:
            company_name: Supplier name to search for.
            from_date: Start of date range (inclusive).
            to_date: End of date range (inclusive).

        Returns:
            Deduplicated list of RawArticle sorted by published_at descending.
        """
        seen_ids: set[str] = set()
        articles: list[RawArticle] = []
        page = 1

        while True:
            params = {
                "q": company_name,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": _NEWSAPI_PAGE_SIZE,
                "page": page,
            }
            data = (await self._get(_NEWSAPI_BASE_URL + "/everything", params)).json()
            raw_articles = data.get("articles", [])

            if not raw_articles:
                break

            new_articles: list[RawArticle] = []
            for raw in raw_articles:
                if not raw.get("url"):
                    continue
                article = self._parse_article(raw)
                if article.article_id not in seen_ids:
                    seen_ids.add(article.article_id)
                    new_articles.append(article)

            articles.extend(new_articles)

            log.info(
                "newsapi.page_fetched",
                company=company_name,
                page=page,
                count=len(new_articles),
            )

            if len(raw_articles) < _NEWSAPI_PAGE_SIZE:
                break
            page += 1

        return articles

    async def fetch_recent_articles(
        self,
        query: str,
        hours_back: int = 24,
    ) -> list[RawArticle]:
        """Fetch articles matching query published within the last hours_back hours.

        Uses the NewsAPI /everything endpoint with a from timestamp.
        Paginates until all results are retrieved.

        Args:
            query: Search query string.
            hours_back: How many hours back to search. Default 24.

        Returns:
            Deduplicated list of RawArticle.
        """
        from datetime import timedelta

        from_dt = _utcnow() - timedelta(hours=hours_back)
        seen_ids: set[str] = set()
        articles: list[RawArticle] = []
        page = 1

        while True:
            params = {
                "q": query,
                "from": from_dt.isoformat(),
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": _NEWSAPI_PAGE_SIZE,
                "page": page,
            }
            data = (await self._get(_NEWSAPI_BASE_URL + "/everything", params)).json()
            raw_articles = data.get("articles", [])

            if not raw_articles:
                break

            new_articles = []
            for raw in raw_articles:
                if not raw.get("url"):
                    continue
                article = self._parse_article(raw)
                if article.article_id not in seen_ids:
                    seen_ids.add(article.article_id)
                    new_articles.append(article)

            articles.extend(new_articles)

            log.info(
                "newsapi.recent_page_fetched",
                query=query,
                page=page,
                count=len(new_articles),
            )

            if len(raw_articles) < _NEWSAPI_PAGE_SIZE:
                break
            page += 1

        return articles

    def _parse_article(self, raw: dict[str, Any]) -> RawArticle:
        """Parse a single NewsAPI article dict into a RawArticle."""
        url: str = raw.get("url", "")
        published_raw: str = raw.get("publishedAt", "")
        try:
            published_at = datetime.fromisoformat(
                published_raw.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            published_at = _utcnow()
            log.warning("newsapi.invalid_published_at", raw=published_raw, url=url)

        source_name: str = (raw.get("source") or {}).get("name", "unknown")
        content: str | None = raw.get("content") or raw.get("description") or None

        return RawArticle(
            article_id=_article_id_from_url(url),
            url=url,
            title=raw.get("title", ""),
            content=content,
            published_at=published_at,
            source_name=source_name,
            source_credibility=_credibility_for_url(url),
            ingested_at=_utcnow(),
            ingestion_source="newsapi",
        )


# ── GDELT client ──────────────────────────────────────────────────────────────


class GDELTClient:
    """Fallback client for GDELT (free, no API key required).

    Lower quality than NewsAPI but free and has global historical coverage.
    Used as fallback when NewsAPI quota is exhausted (429).

    Rate limit: GDELT requests should stay under 1 req/sec to be a polite consumer.
    """

    def __init__(
        self,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(1)  # polite: 1 concurrent request
        self._retry_wait: wait_base = retry_wait or wait_exponential(
            multiplier=1, min=2, max=10
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "GDELTClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _get(self, params: dict[str, Any]) -> httpx.Response:
        """Rate-limited GET with retry on 429/503."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(RetryableHTTPError),
            stop=stop_after_attempt(3),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:
                    log.info("gdelt.request", params=params)
                    response = await self._http.get(_GDELT_BASE_URL, params=params)
                    log.info("gdelt.response", status=response.status_code)
                    if response.status_code in _RETRYABLE_STATUSES:
                        raise RetryableHTTPError(
                            f"HTTP {response.status_code} from GDELT — will retry"
                        )
                    response.raise_for_status()
                    return response
        raise AssertionError("unreachable: tenacity reraises on exhaustion")  # pragma: no cover

    async def fetch_articles(
        self,
        query: str,
        max_records: int = 250,
    ) -> list[RawArticle]:
        """Fetch articles from GDELT matching query.

        GDELT does not paginate — max_records caps the result set.
        Deduplicates by article_id.

        Args:
            query: Search query string.
            max_records: Maximum articles to return. GDELT cap is 250.

        Returns:
            Deduplicated list of RawArticle.
        """
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": min(max_records, 250),
        }
        data = (await self._get(params)).json()
        raw_articles: list[dict[str, Any]] = data.get("articles", [])

        seen_ids: set[str] = set()
        articles: list[RawArticle] = []
        for raw in raw_articles:
            url: str = raw.get("url", "")
            if not url:
                continue
            article = self._parse_article(raw)
            if article.article_id not in seen_ids:
                seen_ids.add(article.article_id)
                articles.append(article)

        log.info("gdelt.fetch_complete", query=query, count=len(articles))
        return articles

    def _parse_article(self, raw: dict[str, Any]) -> RawArticle:
        """Parse a single GDELT article dict into a RawArticle."""
        url: str = raw.get("url", "")
        published_raw: str = raw.get("seendate", "")
        try:
            # GDELT format: "20240115T120000Z"
            published_at = datetime.strptime(published_raw, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            published_at = _utcnow()
            log.warning("gdelt.invalid_seendate", raw=published_raw, url=url)

        return RawArticle(
            article_id=_article_id_from_url(url),
            url=url,
            title=raw.get("title", ""),
            content=None,  # GDELT artlist mode does not return body text
            published_at=published_at,
            source_name=raw.get("domain", "unknown"),
            source_credibility=_credibility_for_url(url),
            ingested_at=_utcnow(),
            ingestion_source="gdelt",
        )
