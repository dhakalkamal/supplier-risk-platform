"""SEC EDGAR XBRL financial data parser.

Handles XBRL messiness: multiple concept names per financial field,
missing data (returns None — never 0), stale filings, and unit variations.

Computes Altman Z' score using the private company formula (book equity,
not market cap). Returns None if any required input is missing.

Detects going concern language in 10-K filing text.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import structlog

from data.ingestion.sec_edgar.models import FinancialSnapshot

log = structlog.get_logger()

GOING_CONCERN_PHRASES: list[str] = [
    "substantial doubt about its ability to continue as a going concern",
    "going concern doubt",
    "ability to continue as a going concern",
    "raise substantial doubt",
]


class SECFinancialsParser:
    """Extracts structured financial data from SEC EDGAR XBRL company facts.

    Handles the messiness of XBRL: multiple concept names for the same field,
    different units, missing data, and stale filings.

    Altman Z' uses the private company formula (book equity, not market cap).
    Returns None for any metric when required inputs are missing — never 0.
    """

    # XBRL concept mappings — tried in order; first match wins.
    REVENUE_CONCEPTS: list[str] = [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ]
    ASSET_CONCEPTS: list[str] = ["Assets"]
    CURRENT_ASSET_CONCEPTS: list[str] = ["AssetsCurrent"]
    LIABILITY_CONCEPTS: list[str] = ["Liabilities"]
    CURRENT_LIABILITY_CONCEPTS: list[str] = ["LiabilitiesCurrent"]
    RETAINED_EARNINGS_CONCEPTS: list[str] = [
        "RetainedEarningsAccumulatedDeficit",
        "RetainedEarnings",
    ]
    EQUITY_CONCEPTS: list[str] = [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ]
    EBIT_CONCEPTS: list[str] = [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ]
    NET_INCOME_CONCEPTS: list[str] = ["NetIncomeLoss", "ProfitLoss"]
    CASH_CONCEPTS: list[str] = [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ]
    LONG_TERM_DEBT_CONCEPTS: list[str] = ["LongTermDebt", "LongTermDebtNoncurrent"]
    INTEREST_EXPENSE_CONCEPTS: list[str] = ["InterestExpense"]
    INVENTORY_CONCEPTS: list[str] = ["InventoryNet", "Inventories"]

    def get_latest_value(
        self, facts: dict[str, Any], concepts: list[str]
    ) -> float | None:
        """Get the most recent USD value for the first matching XBRL concept.

        Tries each concept in order. For each concept, filters to entries from
        10-K or 10-Q forms and selects the one with the most recent end date.

        Args:
            facts: The us-gaap sub-dict from a CompanyFacts response.
            concepts: Ordered list of XBRL concept names to try.

        Returns:
            The most recent float value, or None if no concept matches.
        """
        for concept in concepts:
            if concept not in facts:
                continue
            usd_entries: list[dict[str, Any]] = (
                facts[concept].get("units", {}).get("USD", [])
            )
            candidates = [
                e
                for e in usd_entries
                if e.get("form") in ("10-K", "10-Q") and e.get("end") is not None
            ]
            if not candidates:
                continue
            latest = max(candidates, key=lambda e: e["end"])
            return float(latest["val"])
        return None

    def extract_financials(
        self, cik: str, company_facts: Any
    ) -> FinancialSnapshot:
        """Extract a FinancialSnapshot from raw XBRL company facts.

        Args:
            cik: Zero-padded CIK string.
            company_facts: CompanyFacts Pydantic model or raw dict from
                /api/xbrl/companyfacts/CIK{cik}.json.

        Returns:
            FinancialSnapshot with all available fields populated and
            altman_z_score computed. going_concern_flag defaults to False
            (caller must set it after analysing filing text).
        """
        # Support both CompanyFacts Pydantic model and raw dict (tests / legacy callers)
        if hasattr(company_facts, "facts"):
            facts_dict: dict[str, Any] = company_facts.facts
            entity_name: str = getattr(company_facts, "entity_name", "")
        else:
            facts_dict = company_facts.get("facts", {})
            entity_name = company_facts.get("entityName", "")

        gaap: dict[str, Any] = facts_dict.get("us-gaap", {})
        snapshot = self._build_snapshot(cik, gaap)
        snapshot.altman_z_score = self.compute_altman_z_score(snapshot)
        log.info(
            "sec_edgar.financials_extracted",
            cik=cik,
            entity=entity_name,
            period_end=str(snapshot.period_end),
            z_score=snapshot.altman_z_score,
        )
        return snapshot

    def _build_snapshot(self, cik: str, gaap: dict[str, Any]) -> FinancialSnapshot:
        """Build a FinancialSnapshot from the us-gaap facts sub-dict."""
        now = datetime.now(tz=timezone.utc)
        period_end = self._get_period_end(gaap) or date.today()
        staleness = (date.today() - period_end).days

        return FinancialSnapshot(
            cik=cik,
            period_end=period_end,
            filing_type=self._get_filing_type(gaap),
            total_assets=self.get_latest_value(gaap, self.ASSET_CONCEPTS),
            current_assets=self.get_latest_value(gaap, self.CURRENT_ASSET_CONCEPTS),
            total_liabilities=self.get_latest_value(gaap, self.LIABILITY_CONCEPTS),
            current_liabilities=self.get_latest_value(
                gaap, self.CURRENT_LIABILITY_CONCEPTS
            ),
            retained_earnings=self.get_latest_value(
                gaap, self.RETAINED_EARNINGS_CONCEPTS
            ),
            shareholders_equity=self.get_latest_value(gaap, self.EQUITY_CONCEPTS),
            revenue=self.get_latest_value(gaap, self.REVENUE_CONCEPTS),
            ebit=self.get_latest_value(gaap, self.EBIT_CONCEPTS),
            net_income=self.get_latest_value(gaap, self.NET_INCOME_CONCEPTS),
            cash=self.get_latest_value(gaap, self.CASH_CONCEPTS),
            long_term_debt=self.get_latest_value(gaap, self.LONG_TERM_DEBT_CONCEPTS),
            interest_expense=self.get_latest_value(
                gaap, self.INTEREST_EXPENSE_CONCEPTS
            ),
            inventory=self.get_latest_value(gaap, self.INVENTORY_CONCEPTS),
            altman_z_score=None,  # computed by caller after _build_snapshot
            going_concern_flag=False,  # caller sets after analysing filing text
            financial_data_staleness_days=staleness,
            source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            ingested_at=now,
        )

    def _get_period_end(self, gaap: dict[str, Any]) -> date | None:
        """Infer the most recent annual filing period-end date."""
        for concept_group in (
            self.ASSET_CONCEPTS,
            self.REVENUE_CONCEPTS,
            self.NET_INCOME_CONCEPTS,
        ):
            for concept in concept_group:
                if concept not in gaap:
                    continue
                entries: list[dict[str, Any]] = (
                    gaap[concept].get("units", {}).get("USD", [])
                )
                annual = [e for e in entries if e.get("form") == "10-K"]
                if not annual:
                    continue
                latest = max(annual, key=lambda e: e.get("end", ""))
                try:
                    return date.fromisoformat(latest["end"])
                except (ValueError, KeyError):
                    continue
        return None

    def _get_filing_type(self, gaap: dict[str, Any]) -> str:
        """Determine the most recent form type from available GAAP facts."""
        for concept_group in (self.ASSET_CONCEPTS, self.REVENUE_CONCEPTS):
            for concept in concept_group:
                if concept not in gaap:
                    continue
                entries: list[dict[str, Any]] = (
                    gaap[concept].get("units", {}).get("USD", [])
                )
                if entries:
                    latest = max(entries, key=lambda e: e.get("end", ""))
                    return str(latest.get("form", "10-K"))
        return "10-K"

    def compute_altman_z_score(self, f: FinancialSnapshot) -> float | None:
        """Compute Altman Z' score using the private company formula.

        Z' = 0.717*X1 + 0.847*X2 + 3.107*X3 + 0.420*X4 + 0.998*X5
            X1 = working_capital / total_assets
            X2 = retained_earnings / total_assets
            X3 = ebit / total_assets
            X4 = book_equity / total_liabilities
            X5 = revenue / total_assets

        Zones: Z' < 1.23 distress | 1.23–2.90 grey | Z' > 2.90 safe

        Returns:
            Z' score float, or None if any required input field is missing.
        """
        if (
            f.total_assets is None
            or f.total_assets == 0
            or f.current_assets is None
            or f.current_liabilities is None
            or f.retained_earnings is None
            or f.ebit is None
            or f.shareholders_equity is None
            or f.total_liabilities is None
            or f.revenue is None
        ):
            return None

        assets: float = f.total_assets
        working_capital = f.current_assets - f.current_liabilities
        x1 = working_capital / assets
        x2 = f.retained_earnings / assets
        x3 = f.ebit / assets
        x4 = f.shareholders_equity / f.total_liabilities if f.total_liabilities != 0 else 0.0
        x5 = f.revenue / assets

        return 0.717 * x1 + 0.847 * x2 + 3.107 * x3 + 0.420 * x4 + 0.998 * x5

    def detect_going_concern(self, filing_text: str) -> bool:
        """Detect going concern language in 10-K filing text.

        Args:
            filing_text: Full text content of a 10-K filing.

        Returns:
            True if any going concern phrase is found (case-insensitive).
        """
        lower = filing_text.lower()
        return any(phrase in lower for phrase in GOING_CONCERN_PHRASES)
