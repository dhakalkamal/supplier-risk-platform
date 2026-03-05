"""Repository pattern implementations for all data access.

Every repository has three implementations:
    - Protocol (interface): defines the contract
    - InMemory*Repository: for fast unit tests (no DB required)
    - Postgres*Repository: production implementation

Inject via FastAPI Depends() — never instantiate repositories directly in routes.
See DECISIONS.md ADR-010 for rationale.
"""
