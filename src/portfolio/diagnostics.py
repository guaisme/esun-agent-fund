"""Sector exposure diagnostics use supplied PIT evidence, never inferred industries."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.competition.models import AccountingError, decimal, day
from src.data.time_safety import aware


def sector_concentration_diagnostics(
    weights: dict[str, Decimal],
    memberships: dict[str, dict],
    cutoff: datetime,
    threshold: Decimal = Decimal("0.35"),
) -> dict:
    threshold = decimal(threshold)
    if not 0 < threshold <= 1 or cutoff.tzinfo is None:
        raise AccountingError("Sector diagnostic cutoff and threshold invalid")
    sectors: dict[str, Decimal] = {}
    unknown = Decimal(0)
    excluded = []
    for ticker, original in weights.items():
        weight = decimal(original)
        if not 0 <= weight <= 1:
            raise AccountingError("Invalid sector diagnostic portfolio weight")
        row = memberships.get(ticker, {})
        try:
            if (
                row.get("verified") is not True
                or not row.get("source")
                or not row.get("sector", "").strip()
            ):
                raise AccountingError("Verified sector classification unavailable")
            day(row["observation_date"])
            if (
                aware(row["available_at"]) > cutoff
                or row["observation_date"] > cutoff.date().isoformat()
            ):
                raise AccountingError("Future sector classification")
            sectors[row["sector"]] = sectors.get(row["sector"], Decimal(0)) + weight
        except (KeyError, ValueError, TypeError) as error:
            unknown += weight
            excluded.append({"ticker": ticker, "reason": str(error)})
    if sum(sectors.values(), unknown) > 1:
        raise AccountingError("Portfolio weights exceed NAV")
    return {
        "sector_weights": sectors,
        "unclassified_weight": unknown,
        "data_completeness": "COMPLETE" if not excluded else "UNVERIFIED",
        "threshold": threshold,
        "warnings": [
            {"sector": name, "weight": value}
            for name, value in sectors.items()
            if value > threshold
        ],
        "excluded": excluded,
        "official_constraint": False,
    }
