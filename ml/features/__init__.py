"""Feature engineering for supplier risk scoring.

Defines SupplierFeatureVector — the single contract between dbt and the ML pipeline.
The dbt model pipeline.supplier_feature_vector must produce columns matching
these field names exactly. If a column name changes in dbt, update here first.

See ML_SPEC.md Section 2 for the complete 30+ feature specification.
"""
