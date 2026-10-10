from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, sqrt
from statistics import pstdev

from src.data.universe import UniverseLoader
from .availability import AvailabilityPolicy


@dataclass(frozen=True)
class FeatureSnapshot:
    data_cutoff: str
    mode: str
    factor_version: str
    values: dict[str, dict[str, float | None]]
    provenance: dict[str, list[dict]]
    quality_issues: tuple[dict, ...]
    excluded: tuple[dict, ...]

    def to_dict(self) -> dict:
        return asdict(self)


class FactorCalculator:
    VERSION = "baseline-factors-1.0"
    HORIZONS = (5, 10, 20)

    @staticmethod
    def momentum(closes: list[float], horizon: int) -> float | None:
        if len(closes) <= horizon:
            return None
        return closes[-1] / closes[-horizon - 1] - 1

    @staticmethod
    def risk(closes: list[float], horizon: int = 20) -> tuple[float | None, float | None]:
        if len(closes) <= horizon:
            return None, None
        subset = closes[-horizon - 1 :]
        returns = [current / previous - 1 for previous, current in zip(subset, subset[1:])]
        peak, maximum_drawdown = subset[0], 0.0
        for close in subset:
            peak = max(peak, close)
            maximum_drawdown = min(maximum_drawdown, close / peak - 1)
        return pstdev(returns) * sqrt(252), maximum_drawdown


class FeatureQualityValidator:
    def validate(self, values: dict[str, dict[str, float | None]]) -> list[dict]:
        issues = []
        universe = UniverseLoader()
        for ticker, factors in values.items():
            if not universe.is_valid_ticker(ticker):
                issues.append({"severity": "ERROR", "ticker": ticker, "code": "invalid_ticker"})
            for name, value in factors.items():
                if value is None:
                    issues.append(
                        {
                            "severity": "WARNING",
                            "ticker": ticker,
                            "factor": name,
                            "code": "missing_input",
                        }
                    )
                elif not isfinite(value):
                    issues.append(
                        {
                            "severity": "ERROR",
                            "ticker": ticker,
                            "factor": name,
                            "code": "nonfinite_factor",
                        }
                    )
        return issues


class FeatureEngine:
    def __init__(self, availability: AvailabilityPolicy | None = None) -> None:
        self.availability = availability or AvailabilityPolicy()
        self.calculator = FactorCalculator()

    def build(
        self,
        *,
        prices: list[dict],
        flows: list[dict],
        revenue: list[dict],
        cutoff: str,
        market: list[dict] | None = None,
        trading_days: set[str] | None = None,
    ) -> FeatureSnapshot:
        selected = {}
        excluded = []
        for dataset, rows in (
            ("prices", prices),
            ("flows", flows),
            ("revenue", revenue),
            ("market", market or []),
        ):
            selected[dataset], removed = self.availability.select(rows, dataset, cutoff)
            excluded.extend({"dataset": dataset, **r} for r in removed)
        values: dict[str, dict[str, float | None]] = {}
        provenance: dict[str, list[dict]] = {}
        input_issues = []
        index_rows = selected["market"]
        # A supplied verified market series must be singular. Never silently use
        # equal-weight competition stocks as an actual TWSE index substitute.
        if len({r.get("ticker") for r in index_rows}) > 1:
            raise ValueError("Supply one identified market benchmark series")
        for ticker in sorted({r["ticker"] for r in selected["prices"]}):
            rows = [r for r in selected["prices"] if r["ticker"] == ticker]
            if any(
                r.get("price_unit") != "TWD/share" or r.get("volume_unit") != "shares" for r in rows
            ):
                raise ValueError("Feature inputs require TWD/share and volume in shares")
            factors: dict[str, float | None] = {}
            # Preserve null closes: a missing session invalidates a lookback;
            # dropping it would turn a 20-session window into an arbitrary span.
            for horizon in self.calculator.HORIZONS:
                window = rows[-horizon - 1 :]
                valid = len(window) == horizon + 1 and all(
                    isinstance(r.get("close"), (int, float))
                    and isfinite(r["close"])
                    and r["close"] > 0
                    for r in window
                )
                if trading_days and window:
                    expected = sorted(
                        d
                        for d in trading_days
                        if window[0]["observation_date"] <= d <= window[-1]["observation_date"]
                    )
                    valid = valid and [r["observation_date"] for r in window] == expected
                factors[f"momentum_{horizon}d"] = (
                    self.calculator.momentum([r["close"] for r in window], horizon)
                    if valid
                    else None
                )
                # Match exactly the same endpoint dates for the market return.
                index_by_day = {r["observation_date"]: r for r in index_rows}
                benchmark = [index_by_day.get(r["observation_date"]) for r in window]
                market_ok = valid and all(
                    r
                    and isinstance(r.get("close"), (int, float))
                    and isfinite(r["close"])
                    and r["close"] > 0
                    for r in benchmark
                )
                factors[f"relative_strength_{horizon}d"] = (
                    factors[f"momentum_{horizon}d"]
                    - self.calculator.momentum([r["close"] for r in benchmark], horizon)
                    if market_ok
                    else None
                )
                by_day = {
                    r["observation_date"]: r for r in selected["flows"] if r["ticker"] == ticker
                }
                flow_window = [by_day.get(r["observation_date"]) for r in rows[-horizon:]]
                denominators = rows[-horizon:]
                volume_ok = len(denominators) == horizon and all(
                    type(r.get("volume")) is int and r["volume"] > 0 for r in denominators
                )
                if trading_days and denominators:
                    expected_flow_days = sorted(
                        d
                        for d in trading_days
                        if denominators[0]["observation_date"]
                        <= d
                        <= denominators[-1]["observation_date"]
                    )
                    volume_ok = (
                        volume_ok
                        and [r["observation_date"] for r in denominators] == expected_flow_days
                    )
                for prefix, field in (
                    ("foreign", "foreign_net_buy"),
                    ("trust", "investment_trust_net_buy"),
                    ("dealer", "dealer_net_buy"),
                ):
                    flow_ok = (
                        volume_ok
                        and len(flow_window) == horizon
                        and all(
                            r and r.get("flow_unit") == "shares" and type(r.get(field)) is int
                            for r in flow_window
                        )
                    )
                    # Sum of net shares / sum of trading shares; consistent units.
                    factors[f"{prefix}_flow_{horizon}d"] = (
                        sum(r[field] for r in flow_window) / sum(r["volume"] for r in denominators)
                        if flow_ok
                        else None
                    )
            recent = rows[-21:]
            risk_ok = len(recent) == 21 and factors["momentum_20d"] is not None
            volatility, drawdown = (
                self.calculator.risk([r["close"] for r in recent]) if risk_ok else (None, None)
            )
            factors.update(volatility_20d=volatility, drawdown_20d=drawdown)
            revenues = sorted(
                [r for r in selected["revenue"] if r["ticker"] == ticker],
                key=lambda r: (r["revenue_year"], r["revenue_month"]),
            )
            latest = revenues[-1] if revenues else {}
            factors["revenue_yoy"] = latest.get("revenue_yoy")
            factors["revenue_mom"] = latest.get("revenue_mom")
            acceleration = None
            if len(revenues) >= 2:
                before = revenues[-2]
                month_delta = (
                    (latest["revenue_year"] - before["revenue_year"]) * 12
                    + latest["revenue_month"]
                    - before["revenue_month"]
                )
                if (
                    month_delta == 1
                    and latest.get("revenue_yoy") is not None
                    and before.get("revenue_yoy") is not None
                ):
                    acceleration = latest["revenue_yoy"] - before["revenue_yoy"]
            factors["revenue_acceleration"] = acceleration
            values[ticker] = factors
            provenance[ticker] = [
                {
                    "dataset": dataset,
                    **{
                        k: r.get(k)
                        for k in (
                            "observation_date",
                            "available_at",
                            "published_at",
                            "fetched_at",
                            "source",
                            "raw_ref",
                            "availability_verified",
                            "availability_assumption",
                        )
                    },
                }
                for dataset in ("prices", "flows", "revenue")
                for r in selected[dataset]
                if r["ticker"] == ticker
            ]
        if not trading_days:
            input_issues.append(
                {
                    "severity": "WARNING",
                    "code": "unverified_calendar",
                    "message": "Lookbacks use observed sessions; suspension/missing-session completeness unverified",
                }
            )
        return FeatureSnapshot(
            cutoff,
            self.availability.mode,
            self.calculator.VERSION,
            values,
            provenance,
            tuple(input_issues + FeatureQualityValidator().validate(values)),
            tuple(excluded),
        )
