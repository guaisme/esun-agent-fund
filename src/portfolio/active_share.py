from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from src.competition.models import AccountingError, decimal


@dataclass(frozen=True)
class BenchmarkHoldings:
    ticker: str
    weights: dict[str, Decimal]
    content_as_of: str
    available_at: str
    source_url: str
    verified: bool
    synthetic: bool = False

    def validate(self, cutoff: datetime, max_age_days: int, fixture: bool = False) -> None:
        as_of, available = datetime.fromisoformat(self.content_as_of), datetime.fromisoformat(
            self.available_at
        )
        if as_of.tzinfo is None or available.tzinfo is None or cutoff.tzinfo is None:
            raise AccountingError("Benchmark timestamps require timezone")
        if (
            as_of > cutoff
            or available > cutoff
            or (cutoff - as_of).total_seconds() > max_age_days * 86400
        ):
            raise AccountingError("Future or stale benchmark holdings")
        if (
            not self.source_url
            or (not self.verified and not fixture)
            or (self.synthetic and not fixture)
        ):
            raise AccountingError("Unverified benchmark holdings")
        checked = {ticker: decimal(weight) for ticker, weight in self.weights.items()}
        if (
            not checked
            or any(weight < 0 for weight in checked.values())
            or sum(checked.values()) > 1
        ):
            raise AccountingError("Invalid benchmark weights")


def top_ten(
    weights: dict[str, Decimal], method: Literal["raw", "renormalized"]
) -> dict[str, Decimal]:
    if method not in {"raw", "renormalized"}:
        raise AccountingError("Unknown top-10 Active Share methodology")
    weights = {ticker: decimal(weight) for ticker, weight in weights.items()}
    if any(weight < 0 for weight in weights.values()) or sum(weights.values()) > 1:
        raise AccountingError("Invalid portfolio weights")
    result = dict(sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:10])
    total = sum(result.values(), Decimal(0))
    if not total:
        raise AccountingError("Active Share undefined for an empty equity portfolio")
    return (
        {ticker: value / total for ticker, value in result.items()}
        if method == "renormalized"
        else result
    )


def calculate_active_share(
    portfolio: dict[str, Decimal],
    benchmark: dict[str, Decimal],
    method: Literal["raw", "renormalized"],
) -> Decimal:
    left, right = top_ten(portfolio, method), top_ten(benchmark, method)
    return (
        sum(
            (
                abs(left.get(ticker, Decimal(0)) - right.get(ticker, Decimal(0)))
                for ticker in set(left) | set(right)
            ),
            Decimal(0),
        )
        / 2
    )


class BenchmarkHoldingsProvider:
    """Validate supplied official snapshots; never invent an endpoint or holdings.

    All officially listed ETFs must be covered. Acquisition adapters can later
    populate these snapshots from each fund's published portfolio with evidence.
    """

    def compare(
        self,
        portfolio: dict[str, Decimal],
        snapshots: list[BenchmarkHoldings],
        required: set[str],
        cutoff: datetime,
        method: Literal["raw", "renormalized"],
        max_age_days: int = 7,
        fixture: bool = False,
    ) -> dict[str, Decimal]:
        if {row.ticker for row in snapshots} != required or len(snapshots) != len(required):
            raise AccountingError("Missing or duplicate official active-ETF benchmark snapshots")
        result = {}
        for row in snapshots:
            row.validate(cutoff, max_age_days, fixture)
            result[row.ticker] = calculate_active_share(portfolio, row.weights, method)
        return result
