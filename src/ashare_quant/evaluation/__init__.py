"""Comprehensive strategy evaluation framework.

Implements the industry-standard evaluation checklist from
docs/EVALUATION_FRAMEWORK_INDUSTRY_zh-CN.md (dimensions A-K).

Modules:
  metrics      — Sortino, Calmar, Omega, Ulcer, VaR, CVaR, tail ratio, etc.
  significance — DSR, PSR, PBO (CSCV), Haircut Sharpe, MinTRL
  risk         — Drawdown analysis, stress tests, regime detection
  attribution  — OLS factor regression, capture ratios, yearly/monthly decomposition
  costs        — Turnover, cost drag, holding period, capacity
  report       — Markdown report + matplotlib charts
  pipeline     — Main orchestrator
"""
