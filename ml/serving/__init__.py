"""Model serving — score computation and SHAP attribution.

HeuristicRiskScorer (v0) and XGBoostRiskScorer (v1) implement the same
RiskScorer Protocol — the ML model is a drop-in replacement (ADR-014).

Every score must have SHAP attribution via shap.TreeExplainer.
No black-box scores in production — explainability is a product requirement.

See ML_SPEC.md Section 8 for SHAP implementation spec.
"""
