"""Pydantic v2 models for SEC EDGAR ingestion.

Defines the schema for data flowing through the SEC EDGAR pipeline:
    CompanySearchResult  — result of a company name search
    CompanySubmissions   — raw submissions API response for a single company
    CompanyFacts         — XBRL company facts API response
    Filing               — metadata for a single SEC filing
    FinancialSnapshot    — extracted financial data from one filing period
    SECRawEvent          — Kafka message schema for the raw.sec topic
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanySearchResult(BaseModel):
    """Result of a company name search against SEC EDGAR."""

    cik: str  # zero-padded to 10 digits
    canonical_name: str
    tickers: list[str]
    exchanges: list[str]


class CompanySubmissions(BaseModel):
    """Company submission data from /submissions/CIK{cik}.json.

    The filings field contains the raw 'filings' dict from the API response,
    which includes a 'recent' key with parallel arrays of filing metadata.
    """

    model_config = ConfigDict(populate_by_name=True)

    cik: str
    name: str
    tickers: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    filings: dict[str, Any] = Field(default_factory=dict)


class CompanyFacts(BaseModel):
    """XBRL company facts from /api/xbrl/companyfacts/CIK{cik}.json.

    The facts field contains the raw XBRL taxonomy dicts (e.g. 'us-gaap', 'dei'),
    which map concept names to their unit/value arrays.
    """

    model_config = ConfigDict(populate_by_name=True)

    cik: int
    entity_name: str = Field(alias="entityName")
    facts: dict[str, Any]


class Filing(BaseModel):
    """Metadata for a single SEC filing."""

    cik: str
    filing_type: str  # "10-K", "10-Q", "8-K"
    filed_date: date
    period_of_report: date
    accession_number: str
    primary_document: str


class FinancialSnapshot(BaseModel):
    """Extracted financial data from one SEC filing period.

    All financial fields are nullable — missing data is always None, never 0.
    Consumers must treat None as 'data unavailable', not as a zero balance.
    """

    cik: str
    period_end: date
    filing_type: str

    # Balance sheet
    total_assets: float | None = None
    current_assets: float | None = None
    total_liabilities: float | None = None
    current_liabilities: float | None = None
    retained_earnings: float | None = None
    shareholders_equity: float | None = None

    # Income statement
    revenue: float | None = None
    ebit: float | None = None
    net_income: float | None = None
    interest_expense: float | None = None

    # Other
    cash: float | None = None
    long_term_debt: float | None = None
    inventory: float | None = None

    # Derived fields (computed by SECFinancialsParser)
    altman_z_score: float | None = None
    going_concern_flag: bool = False
    financial_data_staleness_days: int = 0

    # Provenance
    source_url: str
    ingested_at: datetime


class SECRawEvent(BaseModel):
    """Schema for the raw.sec Kafka topic.

    Published by the SEC EDGAR scraper after parsing each 10-K/10-Q/8-K.
    Consumed by the entity resolution pipeline and the risk scoring engine.
    """

    source: Literal["sec_edgar"] = "sec_edgar"
    cik: str
    company_name: str
    filing_type: str
    filed_date: date
    period_of_report: date
    financials: FinancialSnapshot
    going_concern: bool
    ingested_at: datetime
