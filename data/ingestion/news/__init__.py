"""News ingestion pipeline — NewsAPI (primary) and GDELT (fallback).

Fetches articles mentioning monitored suppliers, runs FinBERT sentiment scoring,
classifies topics (layoff, bankruptcy, strike, disaster, regulatory), and
publishes enriched articles to the raw.news Kafka topic.

Implemented in Session 2.
"""
