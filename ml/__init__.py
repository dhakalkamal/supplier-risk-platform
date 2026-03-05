"""ML pipeline package for the Supplier Risk Intelligence Platform.

Phase 1: Heuristic scorer (rules-based, no training data needed) — Session 5
Phase 2: XGBoost classifier with SHAP explainability

Subpackages:
    features/   — SupplierFeatureVector definition and feature computation
    training/   — Model training pipelines and MLflow experiment tracking
    serving/    — Score computation, SHAP attribution, and ensemble scoring
    evaluation/ — Statistical metrics, calibration curves, drift detection
"""
