"""Application configuration via pydantic-settings.

All settings are loaded from environment variables or a .env file.
Never use os.environ directly — always import and use get_settings().

Group:
    App        — runtime environment, debug mode
    Postgres   — operational database + pipeline schema connection
    Kafka      — event streaming (local Docker or AWS MSK)
    Redis      — cache and Celery broker
    SEC EDGAR  — rate limits and user-agent header
    News API   — API key for newsapi.org
    Auth0      — JWT validation (Phase 3)
    OpenAI     — entity resolution LLM fallback (Stage 3)
"""

from typing import Literal

import structlog
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = structlog.get_logger()


class Settings(BaseSettings):
    """Application settings — all values from environment variables or .env file.

    Fields with defaults: safe to omit in local dev.
    Fields without defaults: must be set before the app will start.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignore unknown env vars (e.g. PATH, HOME)
    )

    # ── App ───────────────────────────────────────────────────────────────────
    environment: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = False

    # ── Postgres ──────────────────────────────────────────────────────────────
    # Async SQLAlchemy URL for the FastAPI backend (asyncpg driver).
    database_url: str = (
        "postgresql+asyncpg://supplier_risk:supplier_risk@localhost:5432/supplier_risk"
    )
    # Raw connection params used by Airflow, dbt, and direct psycopg2 connections.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "supplier_risk"
    postgres_password: str = "supplier_risk"
    postgres_db: str = "supplier_risk"

    # ── Kafka ─────────────────────────────────────────────────────────────────
    # Comma-separated broker list. PLAINTEXT locally, SASL_SSL on AWS MSK.
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_security_protocol: str = "PLAINTEXT"

    # ── Redis ─────────────────────────────────────────────────────────────────
    # DB 0 = production / Celery broker, DB 1 = test isolation.
    redis_url: str = "redis://localhost:6379/0"

    # ── SEC EDGAR ─────────────────────────────────────────────────────────────
    # Required by SEC terms of service. Use a real email — fake emails risk IP ban.
    # Format: "AppName your-real-email@domain.com"
    sec_edgar_user_agent: str = "SupplierRiskPlatform dev@example.com"
    sec_edgar_base_url: str = "https://data.sec.gov"
    # Max concurrent requests enforced by asyncio.Semaphore. SEC limit = 10.
    sec_edgar_rate_limit: int = 10

    # ── News API ──────────────────────────────────────────────────────────────
    # From newsapi.org. Leave empty in Phase 1 if not yet provisioned.
    news_api_key: str = ""

    # ── Auth0 ─────────────────────────────────────────────────────────────────
    # Required in Phase 3. Empty string disables JWT validation in dev.
    auth0_domain: str = ""
    auth0_audience: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins. Use * only in local dev.
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # ── SendGrid (alert email dispatch) ───────────────────────────────────────
    # Leave empty to disable sending (dev mode logs the email instead).
    sendgrid_api_key: str = ""
    email_from: str = "alerts@supplierrisk.io"
    email_enabled: bool = False  # set True in staging/prod

    # ── OpenAI (entity resolution Stage 3) ────────────────────────────────────
    openai_api_key: str = ""
    llm_resolution_daily_limit: int = 200

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("sec_edgar_rate_limit")
    @classmethod
    def rate_limit_must_be_positive(cls, v: int) -> int:
        """SEC rate limit must be between 1 and 10."""
        if not (1 <= v <= 10):
            raise ValueError(f"sec_edgar_rate_limit must be 1–10, got {v}")
        return v

    @field_validator("sec_edgar_user_agent")
    @classmethod
    def user_agent_must_include_email(cls, v: str) -> str:
        """SEC terms of service require a real email in the User-Agent header."""
        if "@" not in v:
            raise ValueError(
                "sec_edgar_user_agent must contain an email address. "
                "Format: 'AppName your-email@domain.com'"
            )
        return v

    @field_validator("llm_resolution_daily_limit")
    @classmethod
    def daily_limit_must_be_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("llm_resolution_daily_limit must be >= 0")
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the application Settings singleton.

    Cached after first call — settings are immutable at runtime.
    In tests, override by patching this function or passing Settings directly.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
        log.info(
            "settings.loaded",
            environment=_settings.environment,
            debug=_settings.debug,
        )
    return _settings
