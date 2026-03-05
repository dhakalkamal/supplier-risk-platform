"""Test suite for the Supplier Risk Intelligence Platform.

Structure mirrors the source tree:
    ingestion/  — tests for data/ingestion/
    pipeline/   — tests for data/pipeline/
    ml/         — tests for ml/ (Phase 2)

All tests use InMemory repositories and respx HTTP mocks.
No live database, Kafka, or external API calls in unit tests (ADR-013).
"""
