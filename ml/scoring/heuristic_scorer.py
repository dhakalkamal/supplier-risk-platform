"""Heuristic risk scorer — v0 model, pre-ML.

Rules-based scorer used until we have enough labelled disruption events to
train XGBoost (target: 100+ events, ~3 months of data).

The output schema is identical to the XGBoost v1 model — it is a drop-in
replacement. Neither the API nor the alert engine should branch on model_version.

See ML_SPEC.md Section 6.1 and ADR-007.
"""

import typing
from datetime import datetime, timezone
from typing import Literal

import structlog

from ml.features.feature_vector import FEATURE_COLUMNS, SupplierFeatureVector
from ml.scoring.models import RiskScoreOutput, SignalContribution

logger = structlog.get_logger(__name__)

# Altman Z' thresholds — private company model (Altman 1983).
# Never use 1.81 — that is the public company threshold (uses market cap).
_Z_DISTRESS: float = 1.23
_Z_GREY: float = 2.90

# Computed once at import: which FEATURE_COLUMNS allow None. Used for data_completeness.
_NULLABLE_FEATURE_COLUMNS: frozenset[str] = frozenset(
    col for col in FEATURE_COLUMNS
    if type(None) in typing.get_args(
        typing.get_type_hints(SupplierFeatureVector).get(col, object)
    )
)


def _contrib(
    signal_name: str,
    display_name: str,
    category: Literal["financial", "news", "shipping", "geopolitical", "macro"],
    raw_value: float | None,
    contribution: float,
    explanation: str,
) -> SignalContribution:
    """Build a SignalContribution, inferring direction from contribution sign."""
    direction: Literal["increases_risk", "decreases_risk", "neutral"] = (
        "increases_risk" if contribution > 0
        else "decreases_risk" if contribution < 0
        else "neutral"
    )
    return SignalContribution(
        signal_name=signal_name,
        display_name=display_name,
        category=category,
        raw_value=raw_value,
        contribution=round(contribution, 1),
        direction=direction,
        explanation=explanation,
    )


class HeuristicRiskScorer:
    """Rules-based supplier risk scorer (v0, pre-ML).

    Scores suppliers 0–100 based on weighted signal rules derived from
    financial stress literature (Altman, 1968) and supply chain risk research.

    The output schema is identical to the ML model — it is a drop-in replacement.
    See ML_SPEC.md Section 6.1 and ADR-007.
    """

    MODEL_VERSION: str = "heuristic_v0"

    # Baseline score for a supplier with no adverse signals.
    # 30 = "probably fine, watching" — more honest prior than 50.
    # Rationale: 50 implies "medium risk" for all suppliers, creating alert fatigue.
    # See ML_SPEC.md Section 6.1.
    BASELINE_SCORE: int = 30

    # Category weights — must sum to 1.0
    WEIGHTS: dict[str, float] = {
        "financial":    0.30,
        "news":         0.25,
        "shipping":     0.20,
        "geopolitical": 0.15,
        "macro":        0.10,
    }

    def score(self, features: SupplierFeatureVector) -> RiskScoreOutput:
        """Score a supplier and return a fully attributed RiskScoreOutput."""
        fin_score, fin_sigs   = self._score_financial(features)
        news_score, news_sigs = self._score_news(features)
        ship_score, ship_sigs = self._score_shipping(features)
        geo_score, geo_sigs   = self._score_geo(features)
        macro_score, macro_sigs = self._score_macro(features)
        category_scores = {
            "financial": fin_score, "news": news_score, "shipping": ship_score,
            "geopolitical": geo_score, "macro": macro_score,
        }
        final_score = self._compute_final_score(category_scores)
        risk_level = self._risk_level(final_score)
        completeness = self._data_completeness(features)
        all_signals = sorted(
            fin_sigs + news_sigs + ship_sigs + geo_sigs + macro_sigs,
            key=lambda s: abs(s.contribution),
            reverse=True,
        )
        logger.info("supplier_scored", supplier_id=features.supplier_id,
                    score=final_score, risk_level=risk_level,
                    model_version=self.MODEL_VERSION, data_completeness=completeness)
        return RiskScoreOutput(
            supplier_id=features.supplier_id,
            score=final_score,
            risk_level=risk_level,
            financial_score=fin_score,
            news_score=news_score,
            shipping_score=ship_score,
            geo_score=geo_score,
            macro_score=macro_score,
            top_drivers=all_signals[:5],
            all_signals=all_signals,
            model_version=self.MODEL_VERSION,
            feature_date=features.feature_date,
            scored_at=datetime.now(tz=timezone.utc),
            data_completeness=completeness,
        )

    # ── Financial ─────────────────────────────────────────────────────────────

    def _score_financial(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for the financial category.

        Uses private company Altman Z' thresholds — never the public 1.81 threshold.
        """
        contribs: list[SignalContribution] = []
        points = 0.0
        z = f.altman_z_score
        if z is None:
            z_pts, z_expl = 10.0, "No financial data available — uncertainty penalty"
        elif z < _Z_DISTRESS:
            z_pts = 30.0
            z_expl = f"Altman Z'-Score {z:.2f} is in distress zone (< {_Z_DISTRESS})"
        elif z < _Z_GREY:
            z_pts = 15.0
            z_expl = f"Altman Z'-Score {z:.2f} is in grey zone ({_Z_DISTRESS}–{_Z_GREY})"
        else:
            z_pts, z_expl = 0.0, f"Altman Z'-Score {z:.2f} is in safe zone (> {_Z_GREY})"
        contribs.append(_contrib(
            "altman_z_score", "Financial Stress Index (Altman Z')",
            "financial", z, z_pts, z_expl,
        ))
        points += z_pts
        gc = f.going_concern_flag
        gc_pts = 25.0 if gc is True else 0.0
        if gc is True:
            gc_expl = "Auditor issued going concern opinion — elevated bankruptcy risk"
        elif gc is False:
            gc_expl = "No going concern opinion issued"
        else:
            gc_expl = "Going concern status unavailable"
        contribs.append(_contrib(
            "going_concern_flag", "Going Concern Opinion",
            "financial", float(gc) if gc is not None else None, gc_pts, gc_expl,
        ))
        points += gc_pts
        ratio_pts, ratio_sigs = self._financial_ratio_contribs(f)
        contribs.extend(ratio_sigs)
        points += ratio_pts
        return min(100.0, points), contribs

    def _financial_ratio_contribs(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score current ratio, debt-to-equity, interest coverage, and data freshness."""
        contribs: list[SignalContribution] = []
        points = 0.0
        cr = f.current_ratio
        cr_pts = 10.0 if (cr is not None and cr < 1.0) else 0.0
        if cr is not None and cr < 1.0:
            cr_expl = f"Current ratio {cr:.2f} < 1.0 — liabilities exceed current assets"
        elif cr is not None:
            cr_expl = f"Current ratio {cr:.2f} is adequate"
        else:
            cr_expl = "Current ratio unavailable"
        contribs.append(_contrib(
            "current_ratio", "Liquidity (Current Ratio)", "financial", cr, cr_pts, cr_expl,
        ))
        points += cr_pts
        de = f.debt_to_equity
        de_pts = 10.0 if (de is not None and de > 2.0) else 0.0
        if de is not None and de > 2.0:
            de_expl = f"Debt-to-equity {de:.2f} > 2.0 — high leverage"
        elif de is not None:
            de_expl = f"Debt-to-equity {de:.2f} is within normal range"
        else:
            de_expl = "Debt-to-equity unavailable"
        contribs.append(_contrib(
            "debt_to_equity", "Leverage (Debt-to-Equity)", "financial", de, de_pts, de_expl,
        ))
        points += de_pts
        ic = f.interest_coverage
        ic_pts = 10.0 if (ic is not None and ic < 1.5) else 0.0
        if ic is not None and ic < 1.5:
            ic_expl = f"Interest coverage {ic:.2f} < 1.5 — debt service at risk"
        elif ic is not None:
            ic_expl = f"Interest coverage {ic:.2f} is adequate"
        else:
            ic_expl = "Interest coverage unavailable"
        contribs.append(_contrib(
            "interest_coverage", "Interest Coverage Ratio", "financial", ic, ic_pts, ic_expl,
        ))
        points += ic_pts
        stale_pts = 10.0 if f.financial_data_is_stale else 0.0
        days = f.financial_data_staleness_days
        if f.financial_data_is_stale:
            stale_expl = f"Financial data is {days} days old (> 180) — signal may be stale"
        else:
            stale_expl = "Financial data is current"
        contribs.append(_contrib(
            "financial_data_is_stale", "Financial Data Freshness",
            "financial", float(days) if days is not None else None, stale_pts, stale_expl,
        ))
        points += stale_pts
        return points, contribs

    # ── News ──────────────────────────────────────────────────────────────────

    def _score_news(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for the news sentiment category."""
        contribs: list[SignalContribution] = []
        points = 0.0
        nc = f.news_negative_count_30d
        if nc is None:
            nc_pts, nc_expl = 0.0, "No news coverage data available"
        elif nc >= 5:
            nc_pts, nc_expl = 25.0, f"{nc} negative articles in last 30 days — high volume"
        elif nc >= 2:
            nc_pts, nc_expl = 15.0, f"{nc} negative articles in last 30 days — elevated"
        else:
            nc_pts, nc_expl = 0.0, f"{nc} negative articles in last 30 days — normal range"
        contribs.append(_contrib(
            "news_negative_count_30d", "Negative News Volume (30d)",
            "news", float(nc) if nc is not None else None, nc_pts, nc_expl,
        ))
        points += nc_pts
        s30 = f.news_sentiment_30d
        s30_pts = 20.0 if (s30 is not None and s30 < -0.5) else 0.0
        if s30 is not None and s30 < -0.5:
            s30_expl = f"30-day sentiment {s30:.2f} is highly negative (< -0.5)"
        elif s30 is not None:
            s30_expl = f"30-day sentiment {s30:.2f} is neutral or positive"
        else:
            s30_expl = "No news sentiment data available"
        contribs.append(_contrib(
            "news_sentiment_30d", "News Sentiment (30d)", "news", s30, s30_pts, s30_expl,
        ))
        points += s30_pts
        nv = f.news_negative_velocity
        nv_pts = 15.0 if (nv is not None and nv > 2.0) else 0.0
        if nv is not None and nv > 2.0:
            nv_expl = f"Negative news velocity {nv:.2f}x — accelerating"
        elif nv is not None:
            nv_expl = f"Negative news velocity {nv:.2f}x — normal pace"
        else:
            nv_expl = "Negative news velocity unavailable"
        contribs.append(_contrib(
            "news_negative_velocity", "Negative News Velocity", "news", nv, nv_pts, nv_expl,
        ))
        points += nv_pts
        topic_pts, topic_sigs = self._news_topic_contribs(f)
        contribs.extend(topic_sigs)
        points += topic_pts
        return min(100.0, points), contribs

    def _news_topic_contribs(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score topic flags: bankruptcy, layoff, strike, disaster, regulatory."""
        rules: list[tuple[bool, str, str, float, str]] = [
            (f.topic_bankruptcy_30d, "topic_bankruptcy_30d", "Bankruptcy-Related News",
             30.0, "Bankruptcy-related news detected in last 30 days"),
            (f.topic_layoff_30d, "topic_layoff_30d", "Layoff-Related News",
             15.0, "Layoff-related news detected in last 30 days"),
            (f.topic_strike_30d, "topic_strike_30d", "Strike / Industrial Action",
             15.0, "Strike or industrial action news detected in last 30 days"),
            (f.topic_disaster_30d, "topic_disaster_30d", "Disaster / Incident News",
             20.0, "Disaster or major incident news detected in last 30 days"),
            (f.topic_regulatory_30d, "topic_regulatory_30d", "Regulatory Action News",
             15.0, "Regulatory action or fine news detected in last 30 days"),
        ]
        contribs: list[SignalContribution] = []
        points = 0.0
        for flag, name, display, flag_pts, pos_expl in rules:
            pts = flag_pts if flag else 0.0
            expl = pos_expl if flag else pos_expl.replace("detected", "not detected")
            contribs.append(_contrib(name, display, "news", 1.0 if flag else 0.0, pts, expl))
            points += pts
        return points, contribs

    # ── Shipping ──────────────────────────────────────────────────────────────

    def _score_shipping(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for the shipping volume category."""
        no_data = f.port_call_count_30d is None and f.shipping_volume_delta_30d is None
        if no_data:
            sig = _contrib(
                "shipping_volume_delta_30d", "Shipping Volume Change (30d)",
                "shipping", None, 10.0, "No shipping data available — uncertainty penalty",
            )
            return 10.0, [sig]
        signal_pts, signal_sigs = self._shipping_signal_contribs(f)
        return min(100.0, signal_pts), signal_sigs

    def _shipping_signal_contribs(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score shipping volume delta, z-score, anomaly flag, and dwell time."""
        contribs: list[SignalContribution] = []
        points = 0.0
        delta = f.shipping_volume_delta_30d
        if delta is None:
            d_pts, d_expl = 0.0, "Shipping volume delta unavailable"
        elif delta < -0.5:
            d_pts = 30.0
            d_expl = f"Shipping volume down {abs(delta)*100:.0f}% vs prior 30 days (> 50% drop)"
        elif delta < -0.3:
            d_pts = 20.0
            d_expl = f"Shipping volume down {abs(delta)*100:.0f}% vs prior 30 days (> 30% drop)"
        else:
            d_pts, d_expl = 0.0, f"Shipping volume change {delta*100:+.0f}% is within normal range"
        contribs.append(_contrib(
            "shipping_volume_delta_30d", "Shipping Volume Change (30d)",
            "shipping", delta, d_pts, d_expl,
        ))
        points += d_pts
        z = f.shipping_volume_z_score
        z_pts = 25.0 if (z is not None and z < -2.0) else 0.0
        if z is not None and z < -2.0:
            z_expl = f"Shipping z-score {z:.2f} is a statistical anomaly (< -2)"
        elif z is not None:
            z_expl = f"Shipping z-score {z:.2f} is within normal range"
        else:
            z_expl = "Shipping volume z-score unavailable"
        contribs.append(_contrib(
            "shipping_volume_z_score", "Shipping Volume Z-Score", "shipping", z, z_pts, z_expl,
        ))
        points += z_pts
        anom_pts = 20.0 if f.shipping_anomaly_flag else 0.0
        anom_expl = (
            "Shipping anomaly flag set — statistical outlier detected"
            if f.shipping_anomaly_flag else "No shipping anomaly detected"
        )
        contribs.append(_contrib(
            "shipping_anomaly_flag", "Shipping Anomaly Detected",
            "shipping", 1.0 if f.shipping_anomaly_flag else 0.0, anom_pts, anom_expl,
        ))
        points += anom_pts
        dwell = f.dwell_time_delta
        dwell_pts = 15.0 if (dwell is not None and dwell > 48.0) else 0.0
        if dwell is not None and dwell > 48.0:
            dwell_expl = f"Port dwell time up {dwell:.0f}h vs baseline — vessels waiting longer"
        elif dwell is not None:
            dwell_expl = f"Port dwell time delta {dwell:.0f}h is within normal range"
        else:
            dwell_expl = "Port dwell time data unavailable"
        contribs.append(_contrib(
            "dwell_time_delta", "Port Dwell Time Change", "shipping", dwell, dwell_pts, dwell_expl,
        ))
        points += dwell_pts
        return points, contribs

    # ── Geopolitical ──────────────────────────────────────────────────────────

    def _score_geo(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for the geopolitical risk category."""
        contribs: list[SignalContribution] = []
        points = 0.0
        sanctions_pts = 50.0 if f.on_sanctions_list else 0.0
        sanctions_expl = (
            "Supplier is on OFAC SDN sanctions list — immediate high risk"
            if f.on_sanctions_list else "Supplier is not on OFAC SDN sanctions list"
        )
        contribs.append(_contrib(
            "on_sanctions_list", "OFAC Sanctions Hit",
            "geopolitical", 1.0 if f.on_sanctions_list else 0.0, sanctions_pts, sanctions_expl,
        ))
        points += sanctions_pts
        cs_pts = 30.0 if f.country_under_sanctions else 0.0
        cs_expl = (
            "Supplier country is under broad international sanctions"
            if f.country_under_sanctions else "Supplier country is not under sanctions"
        )
        contribs.append(_contrib(
            "country_under_sanctions", "Country Sanctions Exposure",
            "geopolitical", 1.0 if f.country_under_sanctions else 0.0, cs_pts, cs_expl,
        ))
        points += cs_pts
        crs = f.country_risk_score
        if crs is None:
            crs_pts, crs_expl = 0.0, "Country risk score unavailable"
        elif crs > 75:
            crs_pts, crs_expl = 25.0, f"Country risk score {crs:.0f} is very high (> 75)"
        elif crs > 50:
            crs_pts, crs_expl = 15.0, f"Country risk score {crs:.0f} is elevated (50–75)"
        else:
            crs_pts, crs_expl = 0.0, f"Country risk score {crs:.0f} is within normal range"
        contribs.append(_contrib(
            "country_risk_score", "Country Political Risk",
            "geopolitical", crs, crs_pts, crs_expl,
        ))
        points += crs_pts
        trend = f.country_risk_trend_90d
        trend_pts = 10.0 if (trend is not None and trend > 10.0) else 0.0
        if trend is not None and trend > 10.0:
            trend_expl = f"Country risk increased {trend:.0f} pts over 90 days — worsening trend"
        elif trend is not None:
            trend_expl = f"Country risk trend {trend:+.0f} is stable"
        else:
            trend_expl = "Country risk trend data unavailable"
        contribs.append(_contrib(
            "country_risk_trend_90d", "Country Risk Trend (90d)",
            "geopolitical", trend, trend_pts, trend_expl,
        ))
        points += trend_pts
        return min(100.0, points), contribs

    # ── Macro ─────────────────────────────────────────────────────────────────

    def _score_macro(
        self, f: SupplierFeatureVector
    ) -> tuple[float, list[SignalContribution]]:
        """Score 0–100 for the macro / input cost category."""
        contribs: list[SignalContribution] = []
        points = 0.0
        pmi = f.industry_pmi
        if pmi is None:
            pmi_pts, pmi_expl = 0.0, "Industry PMI data unavailable"
        elif pmi < 45:
            pmi_pts, pmi_expl = 25.0, f"Industry PMI {pmi:.1f} is in contraction (< 45)"
        elif pmi < 50:
            pmi_pts, pmi_expl = 10.0, f"Industry PMI {pmi:.1f} is near contraction (45–50)"
        else:
            pmi_pts, pmi_expl = 0.0, f"Industry PMI {pmi:.1f} is in expansion (≥ 50)"
        contribs.append(_contrib(
            "industry_pmi", "Industry PMI", "macro", pmi, pmi_pts, pmi_expl,
        ))
        points += pmi_pts
        cmdty = f.commodity_price_delta_30d
        cmdty_pts = 15.0 if (cmdty is not None and cmdty > 0.20) else 0.0
        if cmdty is not None and cmdty > 0.20:
            cmdty_expl = f"Commodity price up {cmdty*100:.0f}% in 30 days — input cost pressure"
        elif cmdty is not None:
            cmdty_expl = f"Commodity price change {cmdty*100:+.0f}% is within normal range"
        else:
            cmdty_expl = "Commodity price data unavailable"
        contribs.append(_contrib(
            "commodity_price_delta_30d", "Commodity Price Change (30d)",
            "macro", cmdty, cmdty_pts, cmdty_expl,
        ))
        points += cmdty_pts
        hy = f.high_yield_spread_delta_30d
        hy_pts = 15.0 if (hy is not None and hy > 0.5) else 0.0
        if hy is not None and hy > 0.5:
            hy_expl = f"High-yield spread widened {hy:.2f}pp in 30 days — credit market stress"
        elif hy is not None:
            hy_expl = f"High-yield spread change {hy:+.2f}pp is benign"
        else:
            hy_expl = "High-yield spread data unavailable"
        contribs.append(_contrib(
            "high_yield_spread_delta_30d", "Credit Market Stress (HY Spread)",
            "macro", hy, hy_pts, hy_expl,
        ))
        points += hy_pts
        return min(100.0, points), contribs

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _compute_final_score(self, category_scores: dict[str, float]) -> int:
        """Weighted average of category scores added to baseline, clamped 0–100."""
        weighted_sum = sum(
            score * self.WEIGHTS[category]
            for category, score in category_scores.items()
        )
        return int(min(100, max(0, round(self.BASELINE_SCORE + weighted_sum))))

    def _risk_level(self, score: int) -> Literal["low", "medium", "high"]:
        if score >= 70:
            return "high"
        if score >= 40:
            return "medium"
        return "low"

    def _data_completeness(self, features: SupplierFeatureVector) -> float:
        """Fraction of nullable feature columns that have a non-None value."""
        if not _NULLABLE_FEATURE_COLUMNS:
            return 1.0
        present = sum(
            1 for col in _NULLABLE_FEATURE_COLUMNS
            if getattr(features, col) is not None
        )
        return round(present / len(_NULLABLE_FEATURE_COLUMNS), 4)
