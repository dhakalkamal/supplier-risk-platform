"""Model evaluation — metrics, calibration curves, and drift detection.

Key metrics: AUC-ROC (>= 0.70), PR-AUC (>= 0.25), Brier Score (< 0.15),
Precision@top10%, Recall@score>70.

Drift detection: KS-test on weekly score distributions vs. 4-week baseline.
Retraining triggered automatically on p < 0.05.

See ML_SPEC.md Section 10 for metric thresholds and target values.
"""
