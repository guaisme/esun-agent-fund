from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from src.data.universe import UniverseLoader
from .models import AccountingError, PortfolioState, TradeOrder, decimal
from .policy import CompetitionPolicy


def derive_orders(
    targets: dict[str, Decimal],
    state: PortfolioState,
    previous_nav: Decimal,
    previous_closes: dict[str, Decimal],
    decision_refs: dict[str, str] | None = None,
    policy: CompetitionPolicy | None = None,
    unchanged_tickers: set[str] | None = None,
) -> list[TradeOrder]:
    """Official v4.2: floor(weight * previous NAV / previous close / 1000).

    Every existing holding must be explicitly targeted; omission never means sell.
    Prices belong to the previous settled session; execution-day bars are not inputs.
    """
    policy = policy or CompetitionPolicy()
    universe = UniverseLoader()
    previous_nav = decimal(previous_nav)
    if previous_nav <= 0:
        raise AccountingError("Previous NAV must be positive")
    unchanged = unchanged_tickers or set()
    if unchanged - set(state.positions) or unchanged & set(targets):
        raise AccountingError(
            "Unchanged tickers must be existing positions without changed targets"
        )
    if not set(state.positions) <= set(targets) | unchanged:
        raise AccountingError("Every existing holding requires an explicit target")
    orders = []
    weights = {ticker: decimal(value) for ticker, value in targets.items()}
    if sum(weights.values(), Decimal(0)) > 1:
        raise AccountingError("Target weights exceed 100%")
    for ticker in sorted(weights):
        if not universe.is_valid_ticker(ticker):
            raise AccountingError(f"Outside official universe: {ticker}")
        weight = weights[ticker]
        if not 0 <= weight <= policy.cap(ticker):
            raise AccountingError(f"Target weight violates cap: {ticker}")
        close = decimal(previous_closes.get(ticker, "NaN"))
        if close <= 0:
            raise AccountingError(f"Missing positive previous official close: {ticker}")
        lots = int(
            (weight * previous_nav / close / policy.lot_size).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        shares = lots * policy.lot_size
        old = state.positions[ticker].shares if ticker in state.positions else 0
        difference = shares - old
        if difference:
            orders.append(
                TradeOrder(
                    ticker,
                    "BUY" if difference > 0 else "SELL",
                    abs(difference),
                    (decision_refs or {}).get(ticker),
                )
            )
    return orders
