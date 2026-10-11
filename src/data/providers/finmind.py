from __future__ import annotations
import os
from .base import BaseProvider, ProviderError, days, price_row, provenance, number
from ..http import HttpClient
from ..universe import UniverseLoader


class FinMindProvider(BaseProvider):
    name, market = "FinMind", "mixed"

    def __init__(self, client: HttpClient, universe: UniverseLoader) -> None:
        if os.getenv("FINMIND_ENABLED") != "1":
            raise ProviderError(
                "FinMind requires explicit FINMIND_ENABLED=1 and terms/quota review"
            )
        self.client, self.universe = client, universe

    def fetch(
        self, dataset: str, ticker: str, start: str, end: str
    ) -> tuple[list[dict], str, str]:
        list(days(start, end))
        self.universe.get_stock_info(ticker)
        token = os.getenv("FINMIND_TOKEN", "")
        p, fetched, raw = self.client.get(
            "https://api.finmindtrade.com/api/v4/data",
            dict(dataset=dataset, data_id=ticker, start_date=start, end_date=end),
            {"Authorization": "Bearer " + token} if token else None,
        )
        if p.get("status") != 200 or not isinstance(p.get("data"), list):
            raise ProviderError("FinMind data/schema error")
        return p["data"], fetched, raw

    def get_daily_prices(
        self, ticker: str, start_date: str, end_date: str
    ) -> list[dict]:
        data, fetched, raw = self.fetch(
            "TaiwanStockPrice", ticker, start_date, end_date
        )
        result = []
        market = self.universe.get_stock_info(ticker)["market"]
        for r in data:
            if (
                str(r.get("stock_id")) != ticker
                or not start_date <= r["date"] <= end_date
            ):
                raise ProviderError("FinMind identity/date mismatch")
            result.append(
                price_row(
                    ticker,
                    market,
                    r["date"],
                    self.name,
                    [r[k] for k in ["open", "max", "min", "close"]],
                    r["Trading_Volume"],
                    r["Trading_money"],
                    fetched,
                    raw,
                )
            )
        return result


class MonthlyRevenueProvider:
    def __init__(self, provider: FinMindProvider) -> None:
        self.provider = provider

    def get_monthly_revenue(
        self, ticker: str, start_date: str, end_date: str
    ) -> list[dict]:
        data, fetched, raw = self.provider.fetch(
            "TaiwanStockMonthRevenue", ticker, start_date, end_date
        )
        result = []
        for r in data:
            if str(r.get("stock_id")) != ticker:
                raise ProviderError("Revenue ticker mismatch")
            year, month = int(r["revenue_year"]), int(r["revenue_month"])
            from calendar import monthrange

            observation = f"{year:04}-{month:02}-{monthrange(year,month)[1]:02}"
            row = provenance(
                ticker,
                self.provider.universe.get_stock_info(ticker)["market"],
                observation,
                "FinMind",
                fetched,
                raw,
            )
            row.update(
                revenue_year=year,
                revenue_month=month,
                monthly_revenue=number(r["revenue"], True),
                revenue_unit="TWD",
                revenue_yoy=None,
                revenue_mom=None,
                published_at=None,
                publication_verified=False,
                strict_historical_backtest_eligible=False,
                source_period_marker=r.get("date"),
                source_created_date=r.get("create_time"),
            )
            result.append(row)
        # Compute only from rows in this observed provider snapshot. Missing or
        # zero denominators remain null; no fabricated publication timestamps.
        periods = {(r["revenue_year"], r["revenue_month"]): r for r in result}
        for row in result:
            year, month = row["revenue_year"], row["revenue_month"]
            previous = periods.get(
                (year if month > 1 else year - 1, month - 1 if month > 1 else 12)
            )
            annual = periods.get((year - 1, month))
            for field, baseline in [("revenue_mom", previous), ("revenue_yoy", annual)]:
                if (
                    baseline
                    and baseline["monthly_revenue"]
                    and row["monthly_revenue"] is not None
                ):
                    row[field] = (
                        row["monthly_revenue"] / baseline["monthly_revenue"] - 1
                    )
            row["growth_unit"] = "ratio"
            row["growth_method"] = "observed_monthly_revenue / observed_baseline - 1"
        return sorted(result, key=lambda r: (r["revenue_year"], r["revenue_month"]))
