-- =============================================================================
-- Migration 001: Initial schema for the Supplier Risk Intelligence Platform
-- =============================================================================
-- Run once against a fresh Postgres 15 database.
-- Idempotent: all CREATE statements use IF NOT EXISTS where supported.
-- Schemas: public (operational API tables) + pipeline (ML / dbt tables, separate)
-- =============================================================================

-- Required extension for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- TENANTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS tenants (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(255)    NOT NULL,
    plan                VARCHAR(50)     NOT NULL
                            CHECK (plan IN ('starter', 'growth', 'pro', 'enterprise')),
    stripe_customer_id  VARCHAR(255)    UNIQUE,
    max_suppliers       INTEGER         NOT NULL DEFAULT 25,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- USERS
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID            NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email       VARCHAR(255)    NOT NULL,
    role        VARCHAR(50)     NOT NULL CHECK (role IN ('admin', 'viewer')),
    auth0_id    VARCHAR(255)    NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_email ON users(tenant_id, email);
CREATE INDEX        IF NOT EXISTS idx_users_auth0_id     ON users(auth0_id);

-- =============================================================================
-- CANONICAL SUPPLIER REGISTRY
-- Application generates IDs with 'sup_' prefix (e.g. sup_01HX...).
-- =============================================================================
CREATE TABLE IF NOT EXISTS suppliers (
    id                  VARCHAR(30)     PRIMARY KEY,  -- 'sup_' prefix, app-generated
    canonical_name      VARCHAR(500)    NOT NULL,
    aliases             TEXT[]          NOT NULL DEFAULT '{}',
    country             CHAR(2)         NOT NULL,     -- ISO 3166-1 alpha-2
    industry_code       VARCHAR(10),                  -- NAICS code
    industry_name       VARCHAR(255),
    duns_number         VARCHAR(9),
    cik                 VARCHAR(10),                  -- SEC CIK if public company
    website             VARCHAR(500),
    is_public_company   BOOLEAN         NOT NULL DEFAULT FALSE,
    primary_location    JSONB,          -- {lat, lng, city, country}
    primary_port_id     VARCHAR(50),    -- MarineTraffic port ID
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_suppliers_country        ON suppliers(country);
CREATE INDEX IF NOT EXISTS idx_suppliers_cik            ON suppliers(cik) WHERE cik IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_suppliers_canonical_name ON suppliers
    USING gin(to_tsvector('english', canonical_name));

-- =============================================================================
-- TENANT PORTFOLIOS
-- id is a raw UUID; application wraps it as 'pf_<uuid_no_dashes>' in API responses.
-- =============================================================================
CREATE TABLE IF NOT EXISTS portfolio_suppliers (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    supplier_id     VARCHAR(30)     NOT NULL REFERENCES suppliers(id),
    internal_id     VARCHAR(255),           -- customer's own vendor ID
    custom_name     VARCHAR(255),           -- customer's display name for this supplier
    tags            TEXT[]          NOT NULL DEFAULT '{}',
    added_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_tenant_supplier
    ON portfolio_suppliers(tenant_id, supplier_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_tenant_id
    ON portfolio_suppliers(tenant_id);

-- =============================================================================
-- RISK SCORES
-- Written by the ML scoring pipeline; read by the backend API.
-- id is a raw UUID; score records are shared across tenants (not tenant-scoped).
-- =============================================================================
CREATE TABLE IF NOT EXISTS supplier_scores (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id         VARCHAR(30)     NOT NULL REFERENCES suppliers(id),
    score               SMALLINT        NOT NULL CHECK (score BETWEEN 0 AND 100),
    risk_level          VARCHAR(10)     NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    score_date          DATE            NOT NULL,
    signal_breakdown    JSONB           NOT NULL,   -- full RiskScoreOutput as JSON
    model_version       VARCHAR(50)     NOT NULL,
    data_completeness   NUMERIC(3, 2),              -- 0.00 – 1.00
    scored_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Enforce one score per supplier per day
CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_supplier_date
    ON supplier_scores(supplier_id, score_date);

-- Covering indexes for common API read patterns
CREATE INDEX IF NOT EXISTS idx_scores_supplier_id
    ON supplier_scores(supplier_id);
CREATE INDEX IF NOT EXISTS idx_scores_score_date
    ON supplier_scores(score_date DESC);

-- Partial index for high-risk suppliers (frequent dashboard access pattern)
CREATE INDEX IF NOT EXISTS idx_scores_high_risk
    ON supplier_scores(supplier_id, score_date DESC)
    WHERE score >= 70;

-- =============================================================================
-- ALERTS
-- id is a raw UUID; application wraps it as 'alr_<uuid_no_dashes>' in API responses.
-- =============================================================================
CREATE TABLE IF NOT EXISTS alerts (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID            NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    supplier_id     VARCHAR(30)     NOT NULL REFERENCES suppliers(id),
    alert_type      VARCHAR(50)     NOT NULL
                        CHECK (alert_type IN (
                            'score_spike', 'high_threshold',
                            'event_detected', 'sanctions_hit'
                        )),
    severity        VARCHAR(10)     NOT NULL
                        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    title           VARCHAR(500)    NOT NULL,
    message         TEXT            NOT NULL,
    metadata        JSONB           NOT NULL DEFAULT '{}',
    status          VARCHAR(20)     NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'investigating', 'resolved', 'dismissed')),
    note            TEXT,
    fired_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alerts_tenant_status
    ON alerts(tenant_id, status, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_supplier_id  ON alerts(supplier_id);
CREATE INDEX IF NOT EXISTS idx_alerts_fired_at     ON alerts(fired_at DESC);

-- =============================================================================
-- ALERT RULES (per-tenant configuration)
-- One row per tenant — enforced by the unique index below.
-- =============================================================================
CREATE TABLE IF NOT EXISTS alert_rules (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rule_name               VARCHAR(255) NOT NULL DEFAULT 'Default Rule',
    score_spike_threshold   SMALLINT    NOT NULL DEFAULT 15
                                CHECK (score_spike_threshold BETWEEN 5 AND 50),
    high_risk_threshold     SMALLINT    NOT NULL DEFAULT 70
                                CHECK (high_risk_threshold BETWEEN 50 AND 95),
    channels                JSONB       NOT NULL DEFAULT
                                '{"email": {"enabled": true}, "slack": {"enabled": false}}',
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- At most one rule set per tenant
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_rules_tenant ON alert_rules(tenant_id);

-- =============================================================================
-- DISRUPTION REPORTS
-- Analyst-curated records of confirmed supply disruptions.
-- =============================================================================
CREATE TABLE IF NOT EXISTS disruption_reports (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id         VARCHAR(30)     NOT NULL REFERENCES suppliers(id),
    tenant_id           UUID            NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    disruption_type     VARCHAR(100)    NOT NULL,
    disruption_date     DATE            NOT NULL,
    severity            VARCHAR(10)     NOT NULL
                            CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    source              VARCHAR(255),
    confidence          NUMERIC(3, 2),  -- 0.00 – 1.00
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_disruption_supplier  ON disruption_reports(supplier_id);
CREATE INDEX IF NOT EXISTS idx_disruption_tenant    ON disruption_reports(tenant_id);

-- =============================================================================
-- INGESTION LOG (pipeline health monitoring)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ingestion_log (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source           VARCHAR(50) NOT NULL,
    dag_run_id       VARCHAR(255),
    run_date         DATE        NOT NULL,
    records_fetched  INTEGER     NOT NULL DEFAULT 0,
    records_written  INTEGER     NOT NULL DEFAULT 0,
    records_failed   INTEGER     NOT NULL DEFAULT 0,
    duration_seconds NUMERIC(10, 2),
    status           VARCHAR(20) NOT NULL
                         CHECK (status IN ('running', 'success', 'partial', 'failed')),
    error_message    TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_log_source_date
    ON ingestion_log(source, run_date DESC);

-- =============================================================================
-- ROW LEVEL SECURITY
-- Application sets app.current_tenant_id at the start of each request.
-- FastAPI middleware calls: SET LOCAL app.current_tenant_id = '<uuid>'
-- =============================================================================

ALTER TABLE users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_suppliers ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts             ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_rules        ENABLE ROW LEVEL SECURITY;

-- Drop policies before (re-)creating so this script is re-runnable
DO $$ BEGIN
    DROP POLICY IF EXISTS tenant_isolation ON users;
    DROP POLICY IF EXISTS tenant_isolation ON portfolio_suppliers;
    DROP POLICY IF EXISTS tenant_isolation ON alerts;
    DROP POLICY IF EXISTS tenant_isolation ON alert_rules;
END $$;

CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

CREATE POLICY tenant_isolation ON portfolio_suppliers
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

CREATE POLICY tenant_isolation ON alerts
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

CREATE POLICY tenant_isolation ON alert_rules
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid);

-- Allow the application role to bypass RLS when needed (e.g. scoring pipeline)
-- In production, grant this only to the backend service role, not the pipeline role.
-- ALTER TABLE supplier_scores FORCE ROW LEVEL SECURITY;  -- scores are NOT tenant-scoped
