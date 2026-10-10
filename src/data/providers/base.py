from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..time_safety import now_iso, TAIPEI

Row = dict[str, Any]


class ProviderError(RuntimeError):
    pass


def days(start: str, end: str):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or last > datetime.now(TAIPEI).date():
        raise ValueError("Date range must be ordered and must not include future dates")
    while first <= last:
        yield first.isoformat()
        first += timedelta(days=1)


def number(value: Any, integral: bool = False) -> int | float | None:
    if value is None or str(value).strip() in {"", "--", "---", "----", "N/A", "除權", "除息"}:
        return None
    try:
        result = Decimal(str(value).replace(",", "").replace(" ", ""))
    except InvalidOperation as error:
        raise ProviderError("Unexpected numeric field") from error
    if not result.is_finite():
        raise ProviderError("Non-finite number")
    if integral:
        if result != result.to_integral_value():
            raise ProviderError("Non-integral share/NTD field")
        return int(result)
    return float(result)


def provenance(
    ticker: str,
    market: str,
    observation: str,
    source: str,
    fetched: str | None = None,
    raw_ref: str | None = None,
) -> Row:
    fetched = fetched or now_iso()
    return dict(
        ticker=ticker,
        market=market,
        observation_date=observation,
        source=source,
        fetched_at=fetched,
        published_at=None,
        available_at=fetched,
        availability_verified=True,
        available_at_basis="first_observed_snapshot",
        raw_ref=raw_ref,
    )


def price_row(
    ticker: str,
    market: str,
    trade_date: str,
    source: str,
    prices: list[Any],
    volume: Any,
    turnover: Any,
    fetched: str,
    raw_ref: str | None = None,
) -> Row:
    row = provenance(ticker, market, trade_date, source, fetched, raw_ref)
    row.update(zip(["open", "high", "low", "close"], [number(v) for v in prices]))
    row.update(
        trade_date=trade_date,
        volume=number(volume, True),
        turnover=number(turnover, True),
        volume_unit="shares",
        turnover_unit="TWD",
        price_unit="TWD/share",
    )
    row["vwap"] = (
        row["turnover"] / row["volume"] if row["volume"] and row["turnover"] is not None else None
    )
    row["vwap_method"] = "turnover_TWD/volume_shares; not verified competition execution price"
    return row


class BaseProvider(ABC):
    name: str
    market: str

    @abstractmethod
    def get_daily_prices(self, ticker: str, start_date: str, end_date: str) -> list[Row]:
        """Return unadjusted OHLC in TWD/share, volume in shares, turnover in TWD."""
