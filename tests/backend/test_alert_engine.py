"""Tests for AlertEngine — all 4 alert types, dedup, edge cases."""

from __future__ import annotations

from datetime import date, datetime, timezone

from backend.app.models.responses import (
    AlertRulesResponse,
    ChannelsResponse,
    EmailChannelResponse,
    SlackChannelResponse,
    WebhookChannelResponse,
)
from backend.app.repositories.alert_repository import InMemoryAlertRepository
from backend.app.services.alert_engine import AlertEngine
from ml.scoring.models import RiskScoreOutput, SignalContribution

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_RULES = AlertRulesResponse(
    score_spike_threshold=15,
    high_risk_threshold=70,
    channels=ChannelsResponse(
        email=EmailChannelResponse(enabled=True, recipients=["admin@test.example"]),
        slack=SlackChannelResponse(enabled=False, webhook_url=None, webhook_verified=False),
        webhook=WebhookChannelResponse(enabled=False, url=None, secret=None),
    ),
    updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
)

_TENANT_ID = "10000000-0000-0000-0000-000000000001"
_SUPPLIER_ID = "sup_abc123"


def _make_signal(name: str, value: float | None, contribution: float = 0.0) -> SignalContribution:
    return SignalContribution(
        signal_name=name,
        display_name=name,
        category="news",
        raw_value=value,
        contribution=contribution,
        direction="neutral",
        explanation="test",
    )


def _make_score(
    score: int = 50,
    *,
    flags: dict[str, bool] | None = None,
) -> RiskScoreOutput:
    """Build a minimal RiskScoreOutput with optional boolean flags in all_signals."""
    signals: list[SignalContribution] = []
    for flag_name, flag_val in (flags or {}).items():
        signals.append(_make_signal(flag_name, 1.0 if flag_val else 0.0))

    risk_level = "low" if score < 40 else ("medium" if score < 70 else "high")
    return RiskScoreOutput(
        supplier_id=_SUPPLIER_ID,
        score=score,
        risk_level=risk_level,  # type: ignore[arg-type]
        financial_score=0.0,
        news_score=0.0,
        shipping_score=0.0,
        geo_score=0.0,
        macro_score=0.0,
        top_drivers=[],
        all_signals=signals,
        model_version="heuristic_v0",
        feature_date=date.today(),
        scored_at=datetime.now(tz=timezone.utc),
        data_completeness=1.0,
    )


async def _evaluate(
    new_score: RiskScoreOutput,
    previous_score: RiskScoreOutput | None = None,
    rules: AlertRulesResponse = _DEFAULT_RULES,
) -> tuple[list[str], InMemoryAlertRepository]:
    repo = InMemoryAlertRepository()
    engine = AlertEngine(repo)
    ids = await engine.evaluate(
        supplier_id=_SUPPLIER_ID,
        tenant_id=_TENANT_ID,
        new_score=new_score,
        previous_score=previous_score,
        rules=rules,
    )
    return ids, repo


# ---------------------------------------------------------------------------
# score_spike
# ---------------------------------------------------------------------------


class TestScoreSpike:
    async def test_fires_when_delta_meets_threshold(self) -> None:
        prev = _make_score(50)
        new = _make_score(65)  # delta = 15 == threshold
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "score_spike"]
        assert len(alerts) == 1

    async def test_does_not_fire_when_delta_below_threshold(self) -> None:
        prev = _make_score(50)
        new = _make_score(64)  # delta = 14 < 15
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "score_spike"]
        assert len(alerts) == 0

    async def test_skipped_when_no_previous_score(self) -> None:
        new = _make_score(90)
        ids, repo = await _evaluate(new, previous_score=None)
        spike_alerts = [a for a in repo._alerts.values() if a["alert_type"] == "score_spike"]
        assert len(spike_alerts) == 0


# ---------------------------------------------------------------------------
# high_threshold
# ---------------------------------------------------------------------------


class TestHighThreshold:
    async def test_fires_when_crossing_from_below(self) -> None:
        prev = _make_score(59)
        new = _make_score(71)  # crossed 70
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "high_threshold"]
        assert len(alerts) == 1

    async def test_does_not_fire_when_already_above_threshold(self) -> None:
        prev = _make_score(72)
        new = _make_score(75)  # still above, no crossing
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "high_threshold"]
        assert len(alerts) == 0

    async def test_fires_with_no_previous_score(self) -> None:
        """Without a previous score, any score above threshold triggers crossing."""
        new = _make_score(75)
        ids, repo = await _evaluate(new, previous_score=None)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "high_threshold"]
        assert len(alerts) == 1

    async def test_does_not_fire_below_threshold(self) -> None:
        prev = _make_score(50)
        new = _make_score(69)
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "high_threshold"]
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# event_detected
# ---------------------------------------------------------------------------


class TestEventDetected:
    async def test_fires_when_bankruptcy_flag_transitions_false_to_true(self) -> None:
        prev = _make_score(40, flags={"topic_bankruptcy_30d": False})
        new = _make_score(55, flags={"topic_bankruptcy_30d": True})
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "event_detected"]
        assert len(alerts) == 1
        assert alerts[0]["metadata"]["signal"] == "topic_bankruptcy_30d"

    async def test_does_not_fire_when_flag_was_already_true(self) -> None:
        prev = _make_score(40, flags={"topic_bankruptcy_30d": True})
        new = _make_score(55, flags={"topic_bankruptcy_30d": True})
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "event_detected"]
        assert len(alerts) == 0

    async def test_does_not_fire_when_flag_is_false(self) -> None:
        prev = _make_score(40, flags={"topic_bankruptcy_30d": False})
        new = _make_score(55, flags={"topic_bankruptcy_30d": False})
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "event_detected"]
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# sanctions_hit
# ---------------------------------------------------------------------------


class TestSanctionsHit:
    async def test_fires_when_sanctions_flag_transitions_false_to_true(self) -> None:
        prev = _make_score(50, flags={"on_sanctions_list": False})
        new = _make_score(80, flags={"on_sanctions_list": True})
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "sanctions_hit"]
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"

    async def test_does_not_fire_when_already_on_sanctions_list(self) -> None:
        prev = _make_score(80, flags={"on_sanctions_list": True})
        new = _make_score(82, flags={"on_sanctions_list": True})
        ids, repo = await _evaluate(new, prev)
        alerts = [a for a in repo._alerts.values() if a["alert_type"] == "sanctions_hit"]
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    async def test_second_identical_alert_within_24h_is_skipped(self) -> None:
        prev = _make_score(50)
        new = _make_score(65)  # delta = 15 → score_spike

        # First evaluation creates the alert
        repo = InMemoryAlertRepository()
        engine = AlertEngine(repo)
        ids1 = await engine.evaluate(_SUPPLIER_ID, _TENANT_ID, new, prev, _DEFAULT_RULES)
        assert len(ids1) == 1

        # Second evaluation within 24h → deduped
        ids2 = await engine.evaluate(_SUPPLIER_ID, _TENANT_ID, new, prev, _DEFAULT_RULES)
        assert len(ids2) == 0
        # Only the original alert exists
        spike_alerts = [a for a in repo._alerts.values() if a["alert_type"] == "score_spike"]
        assert len(spike_alerts) == 1
