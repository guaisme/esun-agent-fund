"""Sequential revenue acquisition. HTTP archive cache supplies resumability."""

from __future__ import annotations

import logging
from .providers.base import ProviderError

LOG = logging.getLogger(__name__)


def download_revenue(
    provider, universe, store, start: str, end: str, ticker: str | None = None
) -> dict:
    tickers = [ticker] if ticker else universe.get_all_tickers()
    written, failures, empty, consecutive = 0, [], [], 0
    unattempted = []
    for index, stock in enumerate(tickers):
        LOG.info(
            "revenue_progress stock=%s index=%d total=%d",
            stock,
            index + 1,
            len(tickers),
        )
        try:
            rows = provider.get_monthly_revenue(stock, start, end)
            written += store.save("revenue", rows)
            if not rows:
                empty.append(stock)
            consecutive = 0
        except (ProviderError, ValueError) as error:
            store.failure("revenue", stock, start, error)
            failures.append({"ticker": stock, "error_type": type(error).__name__})
            consecutive += 1
            if consecutive >= 3:
                unattempted = tickers[index + 1 :]
                break
    return dict(
        written=written,
        failures=failures,
        empty_stocks=empty,
        unattempted=unattempted,
        status="INCOMPLETE" if failures or empty or unattempted else "DOWNLOADED",
        note="Download success does not certify freshness or historical publication time",
    )
