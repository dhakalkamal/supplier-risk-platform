"""SEC EDGAR ingestion pipeline.

Fetches 10-K/10-Q filings, extracts financial data via XBRL,
computes Altman Z' score (private company formula), detects going concern
language, and publishes structured events to the raw.sec Kafka topic.

Entry point: SECEdgarClient in scraper.py
"""
