# SESSION_7.md — Alert Engine, Celery, WebSocket

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md first. Then read prompts/SESSION_7.md.
Do not read any other file yet.
Tell me what you're going to build before writing any code.
```

Only start after Session 6 checklist is fully green and `make test` passes clean.

---

## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `specs/API_SPEC.md` — Section 7.4 (alerts), Section 8 (WebSocket spec)
3. `docs/ARCHITECTURE.md` — Section 3.3 (scoring → alerts data flow), Section 11 (Celery config)

Confirm:
> "I am building the alert engine: it reads scores.updated Kafka events, evaluates alert rules,
> writes alerts to Postgres, dispatches email/Slack via Celery, and pushes real-time updates
> to connected clients via WebSocket + Redis pub/sub."

---

## RULES FOR THIS SESSION

- Alert evaluation logic lives in `backend/app/services/alert_engine.py` — not in Kafka consumers or routes.
- Celery tasks are thin wrappers — all business logic in services, not in tasks.
- WebSocket connections are authenticated — reject unauthenticated connections at handshake.
- Run `make lint` after Steps 2 and 4. Run `make test` after Step 5.
- Do not build the React frontend — that is Session 8.
- Do not change any existing route handlers from Session 6.

---

## STEP 1: Celery Setup

### `backend/app/worker/celery_app.py`
```python
"""Celery application factory.

Broker: Redis (DB 0)
Result backend: Redis (DB 0)
Three queues: scoring, notifications, websocket

Never use DB 1 in tests — that is reserved for test isolation.
"""
from celery import Celery

def create_celery_app() -> Celery: ...

celery_app = create_celery_app()
```

Configuration:
- Broker: `settings.redis_url` (Redis DB 0)
- Result backend: same Redis
- Task serializer: JSON
- Three queues: `scoring`, `notifications`, `websocket`
- Task routing:
  - `backend.app.worker.tasks.dispatch_email_alert` → `notifications` queue
  - `backend.app.worker.tasks.dispatch_slack_alert` → `notifications` queue
  - `backend.app.worker.tasks.push_websocket_event` → `websocket` queue
- `task_acks_late = True` — task not acknowledged until complete (prevents silent drops)
- `task_reject_on_worker_lost = True`
- `worker_prefetch_multiplier = 1` — one task at a time per worker

Add to `Makefile`:
```makefile
worker:   # conda run -n genai celery -A backend.app.worker.celery_app worker --loglevel=info -Q notifications,websocket
```

**Say: "✅ Step 1 complete."**

---

## STEP 2: Alert Engine Service

### `backend/app/services/alert_engine.py`

This is the core of Session 7. It takes a scored supplier and determines whether to fire alerts.

```python
class AlertEngine:
    """Evaluates alert rules and fires alerts when thresholds are crossed.

    Called after every scoring run (via Kafka consumer) and after manual
    score triggers. Never called directly from route handlers.

    Alert types:
        score_spike     — score rose >= threshold points in 7 days
        high_threshold  — score crossed above high_risk_threshold
        event_detected  — specific news topic flag triggered (bankruptcy, sanctions, etc.)
        sanctions_hit   — on_sanctions_list or parent_on_sanctions_list became True
    """

    async def evaluate(
        self,
        supplier_id: str,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
        tenant_alert_rules: list[AlertRules],
    ) -> list[Alert]:
        """Evaluate all alert rules for this supplier score update.

        Returns list of Alert objects that were created (may be empty).
        Fires Celery tasks for each alert created — caller does not dispatch.
        """
        ...

    def _check_score_spike(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
        rules: AlertRules,
    ) -> Alert | None:
        """Fire if score rose >= score_spike_threshold points since last score."""
        ...

    def _check_high_threshold(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
        rules: AlertRules,
    ) -> Alert | None:
        """Fire if score crossed high_risk_threshold from below.
        Only fires once — not on every score above threshold.
        """
        ...

    def _check_event_flags(
        self,
        new_score: RiskScoreOutput,
        previous_score: RiskScoreOutput | None,
    ) -> list[Alert]:
        """Fire for: bankruptcy news, sanctions hit, disaster news.
        Only fires when flag transitions False → True (not on every score).
        """
        ...
```

### Alert deduplication
Never fire the same alert type for the same supplier twice within 24 hours.
Check `alerts` table before inserting: if an alert of the same `(supplier_id, alert_type)` 
exists with `fired_at > NOW() - INTERVAL '24 hours'` and `status != 'dismissed'` — skip.

### `backend/app/services/alert_rules_service.py`
```python
async def get_tenants_monitoring_supplier(
    supplier_id: str, pool: asyncpg.Pool
) -> list[tuple[str, AlertRules]]:
    """Return (tenant_id, alert_rules) for all tenants that have this supplier
    in their portfolio. Used by the Kafka consumer to fan out alert evaluation.
    """
    ...
```

**Run `make lint` — fix all errors before proceeding.**
**Say: "✅ Step 2 complete — lint passing."**

---

## STEP 3: Celery Tasks

### `backend/app/worker/tasks.py`

```python
"""Celery tasks for alert dispatch.

Tasks are thin — all logic lives in services.
Every task logs with structlog. Every task handles its own exceptions
and never raises — a failed notification should not crash the worker.
"""

@celery_app.task(queue="notifications", bind=True, max_retries=3)
def dispatch_email_alert(self, alert_id: str, tenant_id: str) -> None:
    """Send alert email via SendGrid.

    Retries 3 times with exponential backoff on failure.
    On final failure: logs error, marks alert notification_failed=True in DB.
    Never raises — caller is Celery, not application code.
    """
    ...

@celery_app.task(queue="notifications", bind=True, max_retries=3)
def dispatch_slack_alert(self, alert_id: str, tenant_id: str) -> None:
    """POST alert to Slack webhook.

    Only runs if tenant has slack.enabled = True in alert_rules.channels.
    Retries 3 times. On failure: logs and moves on.
    """
    ...

@celery_app.task(queue="websocket", bind=True, max_retries=2)
def push_websocket_event(self, event_type: str, payload: dict, tenant_id: str) -> None:
    """Publish event to Redis pub/sub channel for WebSocket broadcast.

    Channel: ws:{tenant_id}
    WebSocket server subscribes to this channel and pushes to connected clients.
    """
    ...
```

### Email template
Create `backend/app/services/email_service.py`:
- Uses SendGrid API (`sendgrid` package)
- `send_alert_email(alert: Alert, recipients: list[str]) -> bool`
- Subject format: `"🔴 [HIGH] {supplier_name} — {alert_title} | Supplier Risk Platform"`
- Body: plain text + HTML (both). Use the template from PRODUCT_SPEC.md Section 4 F03.
- In dev/test: `EMAIL_ENABLED=false` in settings → log the email content, don't send

### Slack notification
Create `backend/app/services/slack_service.py`:
- `send_slack_alert(alert: Alert, webhook_url: str) -> bool`
- Simple `httpx.post` to the webhook URL
- Message format: supplier name + alert type + score delta + link to app
- Verify webhook works by sending test message in settings (already implemented in Session 6)

**Say: "✅ Step 3 complete."**

---

## STEP 4: Kafka Consumer + WebSocket

### `backend/app/consumers/scores_consumer.py`

Kafka consumer that listens to `scores.updated` topic and triggers alert evaluation:

```python
class ScoresConsumer:
    """Consumes scores.updated events and fans out to alert engine.

    For each score event:
    1. Load previous score from DB
    2. Get all tenants monitoring this supplier
    3. For each tenant: evaluate alert rules, create alerts, fire Celery tasks
    4. Publish scores.updated WebSocket event to Redis pub/sub

    One failure for one tenant must not stop processing for other tenants.
    """

    async def run(self) -> None:
        """Main consumer loop. Never raises — logs errors and continues."""
        ...

    async def _process_score_event(self, event: ScoreUpdatedEvent) -> None:
        ...
```

Add to `Makefile`:
```makefile
consume-scores:  # conda run -n genai python -m backend.app.consumers.scores_consumer
```

### `backend/app/api/v1/routes/websocket.py`

WebSocket endpoint per API_SPEC.md Section 8:

```python
@router.websocket("/ws/alerts")
async def websocket_alerts(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    """Real-time alert and score update stream.

    Auth: JWT in query param ?token=...
    On auth failure: HTTP 401, connection rejected before upgrade.

    Message types sent to client:
        alert.fired     — new alert created
        score.updated   — supplier score changed
        ping            — heartbeat every 30 seconds

    Client must respond to ping with pong.
    Connection closed after 5 minutes of no pong response.
    Max 5 concurrent connections per tenant.
    """
    ...
```

WebSocket connection manager:

```python
# backend/app/services/websocket_manager.py

class WebSocketManager:
    """Manages active WebSocket connections per tenant.

    Uses Redis pub/sub to receive events published by Celery tasks.
    Broadcasts to all active connections for a tenant.

    Connection limits: max 5 per tenant. Reject with close code 1008 if exceeded.
    """

    async def connect(self, websocket: WebSocket, tenant_id: str) -> bool:
        """Returns False if connection limit exceeded."""
        ...

    async def disconnect(self, websocket: WebSocket, tenant_id: str) -> None: ...

    async def broadcast_to_tenant(self, tenant_id: str, message: dict) -> None: ...

    async def listen_for_events(self, websocket: WebSocket, tenant_id: str) -> None:
        """Subscribe to Redis pub/sub channel ws:{tenant_id} and forward to client."""
        ...
```

**Run `make lint` — fix all errors before proceeding.**
**Say: "✅ Step 4 complete — lint passing."**

---

## STEP 5: Tests

### `tests/backend/test_alert_engine.py`
- `score_spike` fires when delta >= threshold (default 15 points)
- `score_spike` does not fire when delta < threshold
- `high_threshold` fires only when score crosses from below (59 → 71), not when already above (72 → 75)
- `event_detected` fires when `topic_bankruptcy_30d` transitions False → True
- `event_detected` does not fire when flag was already True in previous score
- `sanctions_hit` fires when `on_sanctions_list` transitions False → True
- Deduplication: second identical alert within 24h is not created
- No previous score: spike check skipped, threshold check still runs

### `tests/backend/test_celery_tasks.py`
- `dispatch_email_alert` calls email service with correct alert data
- `dispatch_email_alert` retries on SendGrid failure (mock failure → verify retry)
- `dispatch_slack_alert` skips when slack not enabled for tenant
- `dispatch_slack_alert` posts to correct webhook URL
- `push_websocket_event` publishes to correct Redis channel `ws:{tenant_id}`

### `tests/backend/test_websocket.py`
- Connection rejected with 401 when JWT invalid
- Connection accepted with valid JWT
- `ping` message sent every 30 seconds (mock time)
- Connection limit: 6th connection rejected with close code 1008
- `alert.fired` message received after alert created (mock Redis pub/sub)
- `score.updated` message received after score event (mock Redis pub/sub)

### `tests/backend/test_scores_consumer.py`
- Happy path: score event → alert evaluated → Celery task fired
- One tenant failure does not stop other tenants processing
- Invalid Kafka message → routed to DLQ, consumer continues

**Run `make test` — must pass with ≥ 80% coverage on `backend/`.**
**Say: "✅ Step 5 complete — X tests passing, Y% coverage."**

---

## STEP 6: Wire Up to main.py

Update `backend/app/main.py` to include the WebSocket router:
```python
from backend.app.api.v1.routes.websocket import router as websocket_router
app.include_router(websocket_router, prefix="/api/v1")
```

Add startup task to start Redis pub/sub listener:
```python
@app.on_event("startup")
async def startup():
    # existing pool setup...
    asyncio.create_task(websocket_manager.start_redis_listener())
```

Smoke test:
```bash
# Start server
conda run -n genai uvicorn backend.app.main:app --reload --port 8000

# In another terminal — connect to WebSocket (needs valid JWT or dev bypass)
# GET /health should still return 200
```

**Say: "✅ Step 6 complete — server starts, WebSocket endpoint registered."**

---

## SESSION 7 DONE — CHECKLIST

```
□ make lint passes clean — zero ruff and mypy errors
□ make test passes — ≥ 80% coverage on backend/
□ Celery app created with 3 queues: scoring, notifications, websocket
□ AlertEngine evaluates 4 alert types: score_spike, high_threshold, event_detected, sanctions_hit
□ Alert deduplication: same type for same supplier not fired twice within 24h
□ high_threshold fires only on crossing (not on every score above threshold)
□ event_detected fires only on flag transition (False → True, not repeated)
□ Email dispatch via SendGrid (dev mode: logs only, does not send)
□ Slack dispatch via webhook (only when tenant has Slack enabled)
□ Celery tasks retry 3 times with backoff, never raise
□ WebSocket endpoint at /api/v1/ws/alerts
□ WebSocket auth: JWT in query param, rejected at handshake if invalid
□ WebSocket connection limit: max 5 per tenant
□ WebSocket heartbeat: ping every 30s, disconnect after 5 min no pong
□ Redis pub/sub: Celery tasks publish to ws:{tenant_id}, WebSocket manager subscribes
□ Scores consumer: fans out to all tenants monitoring a supplier
```

**Say: "Session 7 complete. Checklist: X/16 items green."**

If any item is red — fix it before declaring done.

---

## WHAT COMES NEXT

Session 8: React frontend — portfolio dashboard, supplier profile, alert centre.

Commit before starting Session 8:
```
git add .
git commit -m "feat(session-7): alert engine, Celery dispatch, WebSocket real-time alerts"
git push origin main
```
