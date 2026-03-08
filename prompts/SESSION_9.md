# SESSION_9.md — End-to-End Testing, Auth0, Deployment

## HOW TO START THIS SESSION

Say this to Claude Code:
```
Read CLAUDE.md first. Then read prompts/SESSION_9.md.
Do not read any other file yet.
Tell me what you're going to build before writing any code.
```

Only start after Session 8 checklist is fully green.

---

## CONTEXT CHECK

Read:
1. `CLAUDE.md`
2. `docs/ARCHITECTURE.md` — Section 2 (environment architecture)

Confirm:
> "I am wiring up Auth0 for real authentication, running the full stack end-to-end
> with real data, and deploying to a server. After this session the product is
> accessible at a real URL."

---

## RULES FOR THIS SESSION

- Never commit real Auth0 credentials, API keys, or secrets — `.env` only.
- Never commit a populated `.env` file — only `.env.example`.
- Every external service (SEC EDGAR, NewsAPI) gets a real API key from `.env` — no mocks.
- If an end-to-end test fails, fix the root cause — do not work around it.
- Deployment target: Railway (simplest, no AWS account needed for MVP).
  If Railway is not available, use Render as fallback.
  Do not set up Kubernetes — that is Phase 4.

---

## STEP 1: Auth0 Setup

This step requires manual action in the Auth0 dashboard before any code.

### Manual steps (do these first, outside Claude Code):
1. Create a free Auth0 account at https://auth0.com
2. Create a new Application → Single Page Application → name it "Supplier Risk Platform"
3. Set Allowed Callback URLs: `http://localhost:5173, https://your-railway-domain.up.railway.app`
4. Set Allowed Logout URLs: same
5. Set Allowed Web Origins: same
6. Create an API → name "Supplier Risk API" → identifier `https://api.supplierrisk.com`
7. Note down: Domain, Client ID, Client Secret, API Audience

### Update `.env` with real Auth0 values:
```
AUTH0_DOMAIN=your-tenant.us.auth0.com
AUTH0_AUDIENCE=https://api.supplierrisk.com
VITE_AUTH0_DOMAIN=your-tenant.us.auth0.com
VITE_AUTH0_CLIENT_ID=your-client-id
VITE_AUTH0_AUDIENCE=https://api.supplierrisk.com
```

### Update `backend/app/dependencies.py`
Remove the dev bypass (the `if not settings.auth0_domain` shortcut).
Real JWT validation must be the only path in production.

### Test Auth0 works:
```bash
# Start full stack
make dev           # Docker services
conda run -n genai uvicorn backend.app.main:app --reload --port 8000
cd frontend && npm run dev

# Open http://localhost:5173
# Should redirect to Auth0 login page
# Login → should land on dashboard
# GET /api/v1/portfolio/summary with real JWT → should return data
```

**Say: "✅ Step 1 complete — Auth0 login working end-to-end."**

---

## STEP 2: Real Data End-to-End Test

This is the most important step. Run the full pipeline with real data and verify
everything flows from ingestion to the dashboard.

### Prerequisite: get real API keys
Add to `.env`:
```
SEC_EDGAR_USER_AGENT=YourName your@email.com   # required by SEC, free
NEWS_API_KEY=your-newsapi-key                   # free tier at newsapi.org
OPENAI_API_KEY=your-key                         # for entity resolution LLM stage
```

### Run the pipeline end-to-end:

**Step 2a — Apply DB schema:**
```bash
# Start Docker services
make dev

# Apply schema to local Postgres
conda run -n genai python -c "
import asyncio, asyncpg, pathlib
from backend.app.config import get_settings

async def apply():
    s = get_settings()
    conn = await asyncpg.connect(s.database_url)
    sql = pathlib.Path('backend/app/db/migrations/001_initial_schema.sql').read_text()
    await conn.execute(sql)
    await conn.close()
    print('Schema applied')

asyncio.run(apply())
"
```

**Step 2b — Seed a test tenant + supplier:**
```bash
conda run -n genai python -c "
import asyncio, asyncpg, uuid
from backend.app.config import get_settings

async def seed():
    s = get_settings()
    conn = await asyncpg.connect(s.database_url)

    # Insert test tenant
    tenant_id = str(uuid.uuid4())
    await conn.execute('''
        INSERT INTO tenants (id, name, plan, max_suppliers)
        VALUES (\$1, \$2, \$3, \$4)
        ON CONFLICT DO NOTHING
    ''', tenant_id, 'Test Company', 'growth', 100)

    # Insert a real supplier (TSMC)
    await conn.execute('''
        INSERT INTO suppliers (id, canonical_name, country, industry_code, cik, is_public_company)
        VALUES (\$1, \$2, \$3, \$4, \$5, \$6)
        ON CONFLICT DO NOTHING
    ''', 'sup_tsmc_001', 'Taiwan Semiconductor Manufacturing Co', 'TW', '33441', '0001341439', True)

    # Add to portfolio
    await conn.execute('''
        INSERT INTO portfolio_suppliers (id, tenant_id, supplier_id)
        VALUES (\$1, \$2, \$3)
        ON CONFLICT DO NOTHING
    ''', 'pf_tsmc_001', tenant_id, 'sup_tsmc_001')

    await conn.close()
    print(f'Seeded tenant_id={tenant_id}')

asyncio.run(seed())
"
```

**Step 2c — Run SEC ingestion for TSMC:**
```bash
conda run -n genai python -c "
import asyncio
from data.ingestion.sec_edgar.scraper import SECEdgarClient
from data.ingestion.sec_edgar.parser import SECFinancialsParser

async def test():
    async with SECEdgarClient() as client:
        facts = await client.get_company_facts('0001341439')
        parser = SECFinancialsParser()
        snapshot = parser.extract_financials('0001341439', facts)
        print(f'Z-Score: {snapshot.altman_z_score}')
        print(f'Going concern: {snapshot.going_concern_flag}')
        print(f'Revenue: {snapshot.revenue}')

asyncio.run(test())
"
```

**Step 2d — Run scoring for TSMC:**
```bash
conda run -n genai python -m ml.scoring.run_scoring --date $(date +%Y-%m-%d)
```

**Step 2e — Verify score in dashboard:**
```bash
# Check score in Postgres
conda run -n genai python -c "
import asyncio, asyncpg
from backend.app.config import get_settings

async def check():
    s = get_settings()
    conn = await asyncpg.connect(s.database_url)
    rows = await conn.fetch('SELECT * FROM supplier_scores ORDER BY scored_at DESC LIMIT 5')
    for r in rows:
        print(dict(r))
    await conn.close()

asyncio.run(check())
"
```

**Step 2f — Open dashboard and verify TSMC appears with a score.**

If any step fails, fix the root cause before continuing. Common issues:
- SEC EDGAR rate limit: add `time.sleep(0.5)` between requests
- Missing financial fields: check XBRL concept fallbacks in parser.py
- Scoring fails with None feature vector: check dbt models ran, or seed feature vector directly

**Say: "✅ Step 2 complete — real supplier scored, visible on dashboard."**

---

## STEP 3: Containerise with Docker

### `backend/Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg + ML libraries
RUN apt-get update && apt-get install -y gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `frontend/Dockerfile`
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

### `frontend/nginx.conf`
```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    # SPA routing — all paths serve index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy API calls to backend
    location /api {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket proxy
    location /ws {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### `docker-compose.prod.yml`
Production compose — no Airflow, no MailHog:
```yaml
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: supplier_risk
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  backend:
    build: .
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/supplier_risk
      REDIS_URL: redis://redis:6379/0
      AUTH0_DOMAIN: ${AUTH0_DOMAIN}
      AUTH0_AUDIENCE: ${AUTH0_AUDIENCE}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    command: celery -A backend.app.worker.celery_app worker --loglevel=info -Q notifications,websocket
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/supplier_risk
      REDIS_URL: redis://redis:6379/0
    depends_on:
      - redis
      - postgres

  frontend:
    build: frontend/
    ports:
      - "80:80"
    depends_on:
      - backend

volumes:
  postgres_data:
```

### Verify Docker build locally:
```bash
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up
# Open http://localhost — should show login page
```

**Say: "✅ Step 3 complete — Docker build succeeds, app runs in containers."**

---

## STEP 4: Deploy to Railway

Railway deploys directly from GitHub. No CLI required for the initial setup.

### Manual steps in Railway dashboard:
1. Go to https://railway.app → New Project → Deploy from GitHub
2. Select your `supplier-risk-platform` repo
3. Railway auto-detects the Dockerfile in root → backend service
4. Add a second service → select `frontend/` directory → Dockerfile
5. Add Postgres plugin (Railway managed)
6. Add Redis plugin (Railway managed)

### Environment variables to set in Railway (backend service):
```
AUTH0_DOMAIN=
AUTH0_AUDIENCE=
SEC_EDGAR_USER_AGENT=
NEWS_API_KEY=
OPENAI_API_KEY=
SENDGRID_API_KEY=
EMAIL_FROM=alerts@yourdomain.com
EMAIL_ENABLED=true
CORS_ORIGINS=https://your-frontend.up.railway.app
```

### Apply schema on Railway:
```bash
# Railway CLI (install: npm install -g @railway/cli)
railway login
railway run python -c "
import asyncio, asyncpg, pathlib
async def apply():
    import os
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    sql = pathlib.Path('backend/app/db/migrations/001_initial_schema.sql').read_text()
    await conn.execute(sql)
    print('Schema applied on Railway')
asyncio.run(apply())
"
```

### Update Auth0 callback URLs:
Add Railway frontend URL to Auth0 Allowed Callback URLs, Logout URLs, Web Origins.

### Update frontend `.env` for production build:
Railway injects `VITE_*` env vars at build time — set them in Railway frontend service settings.

### Verify deployment:
```bash
# Hit the live URL
curl https://your-backend.up.railway.app/health
# → {"status": "ok"}

curl https://your-backend.up.railway.app/ready
# → {"status": "ready", "dependencies": {"postgres": "ok", "redis": "ok"}}
```

**Say: "✅ Step 4 complete — app live at Railway URL."**

---

## STEP 5: Smoke Test Production

Run these checks against the live Railway deployment:

```bash
PROD_URL=https://your-backend.up.railway.app

# Health
curl $PROD_URL/health

# Ready
curl $PROD_URL/ready

# Auth required (should return 401)
curl $PROD_URL/api/v1/portfolio/summary
# → {"error": {"code": "UNAUTHORIZED", ...}}

# Frontend loads
curl -I https://your-frontend.up.railway.app
# → HTTP/1.1 200 OK
```

Manual checks in browser:
- [ ] Login with Auth0 works
- [ ] Dashboard loads (empty portfolio — no suppliers yet)
- [ ] Add a supplier via typeahead search
- [ ] Supplier appears in portfolio table
- [ ] Navigate to supplier profile — score shows "Monitoring" (no data yet, < 7 days)
- [ ] Settings page loads, alert rules visible
- [ ] `/health` returns 200

**Say: "✅ Step 5 complete — production smoke tests passing."**

---

## STEP 6: Makefile + README

### Add to root `Makefile`:
```makefile
deploy-schema:   # railway run python -c "..." (applies migration on Railway)
docker-build:    # docker-compose -f docker-compose.prod.yml build
docker-prod:     # docker-compose -f docker-compose.prod.yml up
```

### `README.md`
Write a concise README covering:
```markdown
# Supplier Risk Intelligence Platform

Real-time supplier risk monitoring for procurement teams.
Scores suppliers 0–100 using public signals (SEC filings, news, shipping, geopolitical data).

## Quick Start (local)
1. cp .env.example .env  # fill in API keys
2. make dev              # start Docker services
3. uvicorn backend.app.main:app --reload  # start API
4. cd frontend && npm run dev              # start UI

## Stack
- Backend: FastAPI + Python 3.11
- Frontend: React + TypeScript + Tailwind
- Data: Kafka + Postgres + dbt + Airflow
- ML: XGBoost (Phase 2) / Heuristic scorer (current)
- Auth: Auth0
- Deploy: Railway

## Architecture
See docs/ARCHITECTURE.md

## Session Prompts
Development is structured in sessions. See prompts/ for each session's scope.
```

**Say: "✅ Step 6 complete."**

---

## SESSION 9 DONE — CHECKLIST

```
□ Auth0 login works end-to-end (not dev bypass)
□ Real SEC EDGAR data fetched for at least one supplier
□ Scoring pipeline ran and produced a real score in Postgres
□ Score visible on dashboard in browser
□ Docker build succeeds for both backend and frontend
□ docker-compose.prod.yml runs the full stack in containers
□ App deployed to Railway (or Render)
□ /health returns 200 on production URL
□ /ready returns 200 on production URL (all deps healthy)
□ Auth0 login works on production URL
□ Add supplier flow works end-to-end on production
□ No secrets committed to git — .env only
□ README.md written and accurate
```

**Say: "Session 9 complete. Checklist: X/13 items green."**

---

## WHAT COMES NEXT

After Session 9 the product is live. The next priorities in order:

1. **Get a real customer** — show the live URL to a procurement manager. Real feedback beats more code.
2. **Collect disruption labels** — every customer-reported disruption is training data for XGBoost.
3. **Session 10 (Phase 2)** — XGBoost training pipeline once 100+ labelled events are collected.
4. **Stripe billing** — wire up payment before charging customers.
5. **Risk Map (F06)** — Mapbox GL world map, Phase 4 feature.
