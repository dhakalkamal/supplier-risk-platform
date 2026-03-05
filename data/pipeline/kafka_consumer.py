"""Kafka consumer base for the Supplier Risk Intelligence Platform.

Consumers read from raw.* topics, apply transformations (NLP, enrichment),
and write results to Postgres pipeline schema via repository interfaces.
Failed records are routed to the dead-letter queue (raw.dlq.*).

Implemented in Session 2.
"""
