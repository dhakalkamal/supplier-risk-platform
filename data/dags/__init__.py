"""Airflow DAGs for the Supplier Risk Intelligence Platform.

DAGs:
    ingest_sec_edgar      — daily SEC EDGAR ingestion (02:00 UTC)
    ingest_news           — every 2h news ingestion (Session 2)
    ingest_ais            — every 4h AIS shipping ingestion (Session 2)
    ingest_macro          — daily FRED macro ingestion (Session 2)
    dbt_transform         — triggered after each ingestion run (Session 4)
    ml_score_suppliers    — every 6h scoring run (Session 5)
"""
