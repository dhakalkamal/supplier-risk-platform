"""Supplier entity resolution pipeline.

Maps raw company name strings to canonical supplier IDs.

Three-stage pipeline (see DATA_SOURCES.md Section 8):
    Stage 1: Exact match after normalisation (~60% of cases)
    Stage 2: Fuzzy match via rapidfuzz token_sort_ratio >= 85 (~25% of cases)
    Stage 3: LLM-assisted via GPT-4o-mini for hard cases (~10% of cases)

Unresolved entities written to pipeline.unresolved_entities for manual review.
Never raises — unresolved is a valid outcome.

Implemented in Session 3.
"""
