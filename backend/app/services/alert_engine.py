"""Alert engine — evaluates alert rules and fires alerts when thresholds are crossed.

Called after every scoring run (via Kafka consumer) and after manual score triggers.
Never called directly from route handlers.

Alert types:
    score_spike     — score rose >= threshold points in 7 days
    high_threshold  — score crossed above high_risk_threshold
    event_detected  — specific news topic flag triggered (bankruptcy, sanctions, etc.)
    sanctions_hit   — on_sanctions_list or parent_on_sanctions_list became True
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog

from backend.app.models.responses import AlertRulesResponse
from backend.app.repositories.alert_repository import AlertRecord, AlertRepository
from ml.scoring.models import RiskScoreOutput

log = structlog.get_logger()

AlertType = Literal["score_spike", "high_threshold", "event_detected", "sanctions_hit"]
Severity = Literal["low", "medium", "high", "critical"]

# Event flags evaluated by _check_event_flags, mapped to alert metadata label
_EVENT_FLAGS: list[tuple[str, str, Severity]] = [
    ("topic_bankruptcy_30d", "Bankruptcy news detected", "critical"),
    ("topic_disaster_30d", "Disaster / disruption news detected", "high"),
]

_SANCTIONS_FLAGS = ["on_sanctions_list", "parent_on_sanctions_list"]


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_flag(score: RiskScoreOutput, signal_name: str) -> bool:
    """Extract a boolean flag value from RiskScoreOutput.all_signals."""
    for sig in score.all_signals:
        if sig.signal_name == signal_name:
            return sig.raw_value == 1.0
    return False


def _make_alert_id() -> str:
    return "alr_" + str(uuid.uuid4()).replace("-", "")


class AlertEngine:
    """Evaluates alert rules and fires alerts when thresholds are crossed.

    Inject the alert repository so the engine can check deduplication and
    persist alerts without directly touching the database.
    """

    def __init__(self, alert_repo: AlertRepository) -> None:
        self._repo = alert_repo

    async def evaluate(
        self,
        supplier_id: str,
        tenant_id: str,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
        rules: AlertRulesResponse,
    ) -> list[str]:
        """Evaluate all alert rules for this supplier score update.

        Returns list of alert_ids that were created (may be empty).
        Persists each alert via the repository — caller does not need to write.
        """
        candidates: list[AlertRecord] = []

        spike = self._check_score_spike(new_score, previous_score, rules)
        if spike is not None:
            candidates.append(spike)

        threshold = self._check_high_threshold(new_score, previous_score, rules)
        if threshold is not None:
            candidates.append(threshold)

        candidates.extend(self._check_event_flags(new_score, previous_score))
        candidates.extend(self._check_sanctions(new_score, previous_score))

        created_ids: list[str] = []
        for record in candidates:
            record.supplier_id = supplier_id
            record.tenant_id = tenant_id
            dedup = await self._repo.has_recent_alert(
                supplier_id, record.alert_type, tenant_id
            )
            if dedup:
                log.info(
                    "alert.deduplicated",
                    supplier_id=supplier_id,
                    tenant_id=tenant_id,
                    alert_type=record.alert_type,
                )
                continue
            alert_id = await self._repo.insert_alert(record)
            created_ids.append(alert_id)
            log.info(
                "alert.created",
                alert_id=alert_id,
                supplier_id=supplier_id,
                tenant_id=tenant_id,
                alert_type=record.alert_type,
            )
        return created_ids

    def _check_score_spike(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
        rules: AlertRulesResponse,
    ) -> AlertRecord | None:
        """Fire if score rose >= score_spike_threshold points since last score."""
        if previous_score is None:
            return None
        delta = new_score.score - previous_score.score
        if delta < rules.score_spike_threshold:
            return None
        severity: Severity = "high" if new_score.score >= 80 else "medium"
        return AlertRecord(
            supplier_id="",  # filled by evaluate()
            tenant_id="",
            alert_type="score_spike",
            severity=severity,
            title=f"Risk score spiked +{delta} points",
            message=(
                f"Score rose from {previous_score.score} to {new_score.score} "
                f"(+{delta} pts), exceeding the spike threshold of "
                f"{rules.score_spike_threshold}."
            ),
            metadata={
                "previous_score": previous_score.score,
                "new_score": new_score.score,
                "delta": delta,
                "threshold": rules.score_spike_threshold,
            },
        )

    def _check_high_threshold(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
        rules: AlertRulesResponse,
    ) -> AlertRecord | None:
        """Fire if score crossed high_risk_threshold from below.

        Only fires once — not on every score above threshold.
        """
        crossed = new_score.score >= rules.high_risk_threshold
        if not crossed:
            return None
        # If there was a previous score already above threshold → no crossing
        if previous_score is not None and previous_score.score >= rules.high_risk_threshold:
            return None
        return AlertRecord(
            supplier_id="",
            tenant_id="",
            alert_type="high_threshold",
            severity="high",
            title=f"Supplier entered high-risk zone (score {new_score.score})",
            message=(
                f"Risk score {new_score.score} crossed the high-risk threshold of "
                f"{rules.high_risk_threshold}."
            ),
            metadata={
                "score": new_score.score,
                "threshold": rules.high_risk_threshold,
                "previous_score": previous_score.score if previous_score else None,
            },
        )

    def _check_event_flags(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
    ) -> list[AlertRecord]:
        """Fire for bankruptcy / disaster news — only on False → True transition."""
        alerts: list[AlertRecord] = []
        for signal_name, label, severity in _EVENT_FLAGS:
            now_set = _get_flag(new_score, signal_name)
            prev_set = _get_flag(previous_score, signal_name) if previous_score else False
            if now_set and not prev_set:
                alerts.append(
                    AlertRecord(
                        supplier_id="",
                        tenant_id="",
                        alert_type="event_detected",
                        severity=severity,
                        title=label,
                        message=(
                            f"{label} for this supplier in the last 30 days "
                            f"(signal: {signal_name})."
                        ),
                        metadata={"signal": signal_name},
                    )
                )
        return alerts

    def _check_sanctions(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
    ) -> list[AlertRecord]:
        """Fire when on_sanctions_list or parent_on_sanctions_list transitions False → True."""
        def _prev_flag(f: str) -> bool:
            return _get_flag(previous_score, f) if previous_score else False

        if not any(
            _get_flag(new_score, f) and not _prev_flag(f) for f in _SANCTIONS_FLAGS
        ):
            return []
        hit_flags = [f for f in _SANCTIONS_FLAGS if _get_flag(new_score, f)]
        return [
            AlertRecord(
                supplier_id="",
                tenant_id="",
                alert_type="sanctions_hit",
                severity="critical",
                title="Sanctions list match detected",
                message=(
                    f"Supplier or related entity appears on a sanctions list. "
                    f"Flags: {', '.join(hit_flags)}."
                ),
                metadata={"flags": hit_flags},
            )
        ]
