"""Explicit coverage contract for the currently supported fundamental inputs."""

from __future__ import annotations

from math import isfinite

REQUIRED_FACTORS = (
    "foreign_flow_5d",
    "trust_flow_5d",
    "dealer_flow_5d",
    "foreign_flow_20d",
    "trust_flow_20d",
    "dealer_flow_20d",
    "revenue_yoy",
    "revenue_mom",
    "revenue_acceleration",
)


def fundamental_coverage(values: dict, tickers: list[str]) -> dict:
    missing = {}
    for ticker in tickers:
        factors = values.get(ticker, {})
        absent = [
            name
            for name in REQUIRED_FACTORS
            if not isinstance(factors.get(name), (int, float))
            or isinstance(factors.get(name), bool)
            or not isfinite(factors[name])
        ]
        if absent:
            missing[ticker] = absent
    return {
        "status": "COMPLETE" if not missing else "INCOMPLETE",
        "scope": "monthly_revenue_and_institutional_flows",
        "total_stocks": len(tickers),
        "complete_stocks": len(tickers) - len(missing),
        "required_factors": list(REQUIRED_FACTORS),
        "missing": missing,
        "financial_statements": "NOT_IMPLEMENTED",
        "note": "完整度僅涵蓋月營收與法人；不代表EPS、資產負債或現金流財報完整。",
    }
