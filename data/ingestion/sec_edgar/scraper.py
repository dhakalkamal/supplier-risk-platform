"""Async SEC EDGAR API client.

Rate-limited to 10 req/sec per SEC terms of service (asyncio.Semaphore).
Retries on 429 and 503 responses using exponential backoff (tenacity).
All requests and responses logged with structlog.
CIK values always zero-padded to 10 digits.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

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
from data.ingestion.sec_edgar.models import (
    CompanyFacts,
    CompanySearchResult,
    CompanySubmissions,
    Filing,
)

log = structlog.get_logger()

_EFTS_BASE_URL = "https://efts.sec.gov"
_CIK_PAD_LENGTH = 10
_RETRYABLE_STATUSES = frozenset({429, 503})
_DEFAULT_TIMEOUT_SECONDS = 30.0


class RetryableHTTPError(Exception):
    """Raised for 429 and 503 HTTP responses to trigger tenacity retry."""


class SECEdgarClient:
    """Async client for the SEC EDGAR API.

    Respects the SEC's rate limit of 10 requests/second via asyncio.Semaphore.
    Uses exponential backoff on 429 and 503 responses (tenacity AsyncRetrying).
    All requests and responses are logged with structlog.

    Args:
        settings: Application settings. Defaults to get_settings() singleton.
        retry_wait: Tenacity wait strategy. Defaults to exponential backoff
            starting at 2 s. Pass wait_none() in tests to skip delays.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        """Initialise with settings and an injectable retry wait strategy."""
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(self._settings.sec_edgar_rate_limit)
        self._retry_wait: wait_base = retry_wait or wait_exponential(
            multiplier=1, min=2, max=10
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        """Lazily create and return the shared httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self._settings.sec_edgar_user_agent},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "SECEdgarClient":
        """Support async context manager — returns self."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Close the HTTP client on context manager exit."""
        await self.close()

    async def _get(self, url: str, cik: str = "") -> httpx.Response:
        """Make a rate-limited GET request with retry on 429/503.

        Args:
            url: Full URL to request.
            cik: CIK for structured log fields (empty string if not applicable).

        Returns:
            Successful httpx.Response.

        Raises:
            RetryableHTTPError: Propagated after max_attempts exhausted.
            httpx.HTTPStatusError: On non-retryable 4xx/5xx responses.
        """
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(RetryableHTTPError),
            stop=stop_after_attempt(3),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:
                    log.info("sec_edgar.request", url=url, cik=cik)
                    response = await self._http.get(url)
                    log.info(
                        "sec_edgar.response",
                        status=response.status_code,
                        cik=cik,
                        url=url,
                    )
                    if response.status_code in _RETRYABLE_STATUSES:
                        raise RetryableHTTPError(
                            f"HTTP {response.status_code} from {url} — will retry"
                        )
                    response.raise_for_status()
                    return response
        raise AssertionError("unreachable: tenacity reraises on exhaustion")  # pragma: no cover

    def _pad_cik(self, cik: str) -> str:
        """Zero-pad a CIK string to 10 digits as required by SEC EDGAR."""
        return cik.zfill(_CIK_PAD_LENGTH)

    async def get_company_submissions(self, cik: str) -> CompanySubmissions:
        """Fetch company submission metadata from the SEC EDGAR submissions API.

        Args:
            cik: Company CIK (will be zero-padded to 10 digits automatically).

        Returns:
            CompanySubmissions with name, tickers, exchanges, and recent filings.
        """
        padded = self._pad_cik(cik)
        url = f"{self._settings.sec_edgar_base_url}/submissions/CIK{padded}.json"
        data = (await self._get(url, cik=padded)).json()
        return CompanySubmissions(
            cik=padded,
            name=data.get("name", ""),
            tickers=data.get("tickers", []),
            exchanges=data.get("exchanges", []),
            filings=data.get("filings", {}),
        )

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        """Fetch XBRL company facts from the SEC EDGAR API.

        Args:
            cik: Company CIK (will be zero-padded to 10 digits automatically).

        Returns:
            CompanyFacts with the full XBRL us-gaap fact tree.
        """
        padded = self._pad_cik(cik)
        url = (
            f"{self._settings.sec_edgar_base_url}"
            f"/api/xbrl/companyfacts/CIK{padded}.json"
        )
        data = (await self._get(url, cik=padded)).json()
        return CompanyFacts(
            cik=data.get("cik", 0),
            entityName=data.get("entityName", ""),
            facts=data.get("facts", {}),
        )

    async def search_company(self, company_name: str) -> list[CompanySearchResult]:
        """Search for companies by name via SEC EDGAR entity search.

        Args:
            company_name: Company name string to search.

        Returns:
            List of CompanySearchResult ordered by relevance.
        """
        url = f"{_EFTS_BASE_URL}/LATEST/search-index?q=&entity={company_name}"
        data = (await self._get(url)).json()
        hits = data.get("hits", {}).get("hits", [])
        return [self._parse_search_hit(hit) for hit in hits]

    def _parse_search_hit(self, hit: dict[str, Any]) -> CompanySearchResult:
        """Parse a single EFTS search hit into a CompanySearchResult."""
        source = hit.get("_source", {})
        display_names: list[str] = source.get("display_names", [])
        canonical = display_names[0] if display_names else source.get("entity_name", "")
        return CompanySearchResult(
            cik=self._pad_cik(str(source.get("entity_id", "0"))),
            canonical_name=canonical,
            tickers=source.get("tickers", []),
            exchanges=source.get("exchanges", []),
        )

    async def get_recent_filings(self, since_date: date) -> list[Filing]:
        """Fetch recent 10-K, 10-Q, and 8-K filings since a given date.

        Uses the SEC EDGAR full-text search system (EFTS) to find filings
        filed on or after since_date across all companies.

        Args:
            since_date: Return only filings filed on or after this date.

        Returns:
            List of Filing objects. Empty list if no filings found.
        """
        url = (
            f"{_EFTS_BASE_URL}/LATEST/search-index"
            f"?q=&forms=10-K,10-Q,8-K&dateRange=custom"
            f"&startdt={since_date.isoformat()}"
        )
        data = (await self._get(url)).json()
        hits = data.get("hits", {}).get("hits", [])
        filings: list[Filing] = []
        for hit in hits:
            filing = self._parse_filing_hit(hit)
            if filing is not None:
                filings.append(filing)
        return filings

    def _parse_filing_hit(self, hit: dict[str, Any]) -> Filing | None:
        """Parse a single EFTS search hit into a Filing, or None if invalid."""
        try:
            source = hit.get("_source", {})
            period_str: str = source.get("period_of_report", "")
            filed_str: str = source.get("file_date", period_str)
            return Filing(
                cik=self._pad_cik(str(source.get("entity_id", "0"))),
                filing_type=source.get("file_type", ""),
                filed_date=date.fromisoformat(filed_str),
                period_of_report=date.fromisoformat(period_str),
                accession_number=source.get("file_num", ""),
                primary_document=source.get("period_of_report", ""),
            )
        except (ValueError, KeyError) as exc:
            log.warning("sec_edgar.parse_filing_failed", error=str(exc))
            return None
