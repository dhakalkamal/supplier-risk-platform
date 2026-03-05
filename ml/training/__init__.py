"""Model training pipelines.

All training runs must be tracked with MLflow (params, metrics, artifacts).
Temporal train/val/test splits only — never random splits on time-series data.
SMOTE applied after splitting, on training set only.

See ML_SPEC.md Section 6 for model architecture and Section 5 for leakage rules.
"""
