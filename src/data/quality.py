from __future__ import annotations
from datetime import date, datetime
from math import isfinite
from .time_safety import aware, TAIPEI, now_iso
from .universe import UniverseLoader


class DataQualityValidator:
    def __init__(self, universe: UniverseLoader) -> None:
        self.universe = universe

    def validate(self, rows: list[dict], dataset: str = "prices") -> list[dict]:
        issues = []
        seen = set()

        def add(level, code, r):
            issues.append(
                dict(level=level, code=code, ticker=r.get("ticker"), date=r.get("observation_date"))
            )

        required = {
            "ticker",
            "market",
            "observation_date",
            "source",
            "fetched_at",
            "available_at",
        } | (
            {"trade_date", "open", "high", "low", "close", "volume", "turnover"}
            if dataset == "prices"
            else (
                {"trade_date", "foreign_net_buy", "investment_trust_net_buy", "dealer_net_buy"}
                if dataset == "flows"
                else {"revenue_year", "revenue_month", "monthly_revenue", "published_at"}
            )
        )
        for r in rows:
            if not self.universe.is_valid_ticker(r.get("ticker", "")):
                add("ERROR", "invalid_ticker", r)
            elif r.get("market") != self.universe.get_stock_info(r["ticker"])["market"]:
                add("ERROR", "market_mismatch", r)
            if not r.get("source"):
                add("ERROR", "missing_source", r)
            if required - set(r):
                add("ERROR", "missing_fields", r)
            try:
                d = date.fromisoformat(r["observation_date"])
                if d.isoformat() != r["observation_date"]:
                    raise ValueError()
                if dataset in {"prices", "flows"} and r.get("trade_date") != r["observation_date"]:
                    add("ERROR", "trade_date_mismatch", r)
                if d > datetime.now(TAIPEI).date():
                    add("ERROR", "future_observation", r)
                fetched = aware(r["fetched_at"])
                if fetched > datetime.now(TAIPEI):
                    add("ERROR", "future_fetch", r)
                if r.get("available_at") and aware(r["available_at"]) > fetched:
                    add("ERROR", "availability_after_fetch", r)
            except (ValueError, TypeError, KeyError):
                add("ERROR", "invalid_date_or_timestamp", r)
            key = (r.get("ticker"), r.get("observation_date"))
            if key in seen:
                add("ERROR", "duplicate", r)
            seen.add(key)
            if dataset == "prices":
                if (
                    r.get("volume_unit") != "shares"
                    or r.get("turnover_unit") != "TWD"
                    or r.get("price_unit") != "TWD/share"
                ):
                    add("ERROR", "unit_mismatch", r)
                for k in ["open", "high", "low", "close", "volume", "turnover"]:
                    v = r.get(k)
                    if v is None:
                        add("WARNING", "missing_" + k, r)
                    elif not isinstance(v, (int, float)) or not isfinite(v) or v < 0:
                        add("ERROR", "invalid_" + k, r)
                    elif k in {"volume", "turnover"} and not isinstance(v, int):
                        add("ERROR", "non_integer_" + k, r)
                hi, lo, cl = r.get("high"), r.get("low"), r.get("close")
                if isinstance(hi, (int, float)) and isinstance(lo, (int, float)):
                    if hi < lo:
                        add("ERROR", "high_below_low", r)
                    if isinstance(cl, (int, float)) and not lo <= cl <= hi:
                        add("ERROR", "close_outside_range", r)
                if r.get("volume") == 0:
                    add("INFO", "no_trades_or_suspension", r)
                if any(r.get(k) == 0 for k in ["open", "high", "low", "close"]) and r.get(
                    "volume", 0
                ):
                    add("ERROR", "zero_traded_price", r)
            elif dataset == "flows":
                if r.get("flow_unit") != "shares":
                    add("ERROR", "unit_mismatch", r)
                for k in ["foreign_net_buy", "investment_trust_net_buy", "dealer_net_buy"]:
                    if not isinstance(r.get(k), int):
                        add("ERROR", "invalid_" + k, r)
            else:
                if r.get("revenue_unit") != "TWD":
                    add("ERROR", "unit_mismatch", r)
                if r.get("published_at") is None:
                    add("WARNING", "publication_unknown_strict_history_excluded", r)
                if not isinstance(r.get("monthly_revenue"), int) or r["monthly_revenue"] < 0:
                    add("ERROR", "invalid_revenue", r)
                if not 1 <= r.get("revenue_month", 0) <= 12:
                    add("ERROR", "invalid_revenue_month", r)
            if r.get("available_at_basis") == "first_observed_snapshot":
                add("INFO", "historical_publication_not_verified", r)
        return issues

    def report(
        self,
        store,
        dataset: str = "prices",
        start: str | None = None,
        end: str | None = None,
        min_days: int = 120,
    ) -> dict:
        rows = store.query(dataset, start=start, end=end)
        issues = self.validate(rows, dataset)
        universe = self.universe.load_universe()
        counts = {s["ticker"]: 0 for s in universe}
        dates = {t: set() for t in counts}
        for r in rows:
            counts[r["ticker"]] += 1
            dates[r["ticker"]].add(r["observation_date"])
        calendar = {
            m: {
                d
                for (d,) in store.db.execute("SELECT day FROM calendar WHERE market=?", (m,))
                if (not start or d >= start) and (not end or d <= end)
            }
            for m in ["TWSE", "TPEx"]
        }
        missing = {
            s["ticker"]: sorted(calendar[s["market"]] - dates[s["ticker"]])
            for s in universe
            if calendar[s["market"]] - dates[s["ticker"]]
        }
        for r in rows:
            if (
                dataset in {"prices", "flows"}
                and r["observation_date"] not in calendar[r["market"]]
            ):
                issues.append(
                    dict(
                        level="WARNING",
                        code="unverified_trading_day",
                        ticker=r["ticker"],
                        date=r["observation_date"],
                    )
                )
        updates = [
            dict(market=m, date=d, status=s)
            for m, d, s in store.db.execute(
                "SELECT market,day,status FROM updates WHERE dataset=?", (dataset,)
            )
            if (not start or d >= start) and (not end or d <= end)
        ]
        return dict(
            generated_at=now_iso(),
            dataset=dataset,
            total_stocks=len(universe),
            stocks_with_data=sum(v > 0 for v in counts.values()),
            rows=len(rows),
            expected_observations=(
                sum(len(calendar[s["market"]]) for s in universe) if dataset != "revenue" else None
            ),
            observed_day_coverage=(
                (len(rows) / sum(len(calendar[s["market"]]) for s in universe))
                if dataset != "revenue" and any(calendar.values())
                else None
            ),
            date_range=[
                min((r["observation_date"] for r in rows), default=None),
                max((r["observation_date"] for r in rows), default=None),
            ],
            sources=sorted({r["source"] for r in rows}),
            last_fetched_at=max((r["fetched_at"] for r in rows), default=None),
            counts=counts,
            missing_stocks=[t for t, n in counts.items() if not n],
            below_target=[t for t, n in counts.items() if n < min_days],
            minimum_trading_days=min_days,
            missing_observation_dates=missing,
            unverified_or_failed_days=[u for u in updates if u["status"] != "complete"],
            quality_counts={
                level: sum(i["level"] == level for i in issues)
                for level in ["ERROR", "WARNING", "INFO"]
            },
            issues=issues,
            availability_note="Historical publication times unknown; available only since first observed snapshot. Missing dates may indicate suspension or listing changes; never filled.",
            completeness_denominator="Only observed market trading days; empty API days remain unverified, never assumed holidays",
        )
