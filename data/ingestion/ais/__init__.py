"""AIS (Automatic Identification System) shipping data ingestion.

Fetches port call data from MarineTraffic (primary) or PortWatch (fallback).
Computes shipping volume deltas and z-scores vs. historical baseline.
Publishes to the raw.ais Kafka topic.

Implemented in Session 2.
"""
