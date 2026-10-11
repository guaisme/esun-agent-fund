from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

from .active_share import (
    ActiveETFLoader,
    ActiveShareCalculator,
    ActiveShareValidator,
    BenchmarkHoldings,
)

from src.competition.models import ComplianceViolation, DailyNAV, PortfolioState
from src.competition.policy import CompetitionPolicy


@dataclass(frozen=True)
class ComplianceReport:
    violations: tuple[ComplianceViolation, ...]
    overweight_streaks: dict[str, int]
    active_share_streaks: dict[str, int]
    active_share_date: str | None = None

    @property
    def failed(self) -> bool:
        return any(v.severity in {"HARD_FAIL", "WARNING"} for v in self.violations)

    @property
    def verified(self) -> bool:
        return not any(v.severity == "UNVERIFIED" for v in self.violations)


class ComplianceEngine:
    def __init__(self, policy: CompetitionPolicy | None = None) -> None:
        self.policy = policy or CompetitionPolicy()

    def post_trade(
        self,
        state: PortfolioState,
        nav: DailyNAV,
        added_tickers: set[str],
        active_shares: dict[str, Decimal] | None,
        required_benchmarks: set[str],
        strict: bool = True,
        *,
        fixture_mode: bool = False,
        benchmark_snapshots: list[BenchmarkHoldings] | None = None,
        benchmark_cutoff: datetime | None = None,
        trading_days: set[str] | None = None,
        active_share_method: str = "raw",
    ) -> ComplianceReport:
        """One invocation per official session, with no mutation of input state."""
        issues: list[ComplianceViolation] = []
        count = len(state.positions)
        if not self.policy.min_holdings <= count <= self.policy.max_holdings:
            issues.append(
                ComplianceViolation(
                    "holding_count",
                    "WARNING",
                    f"Holdings={count}; required {self.policy.min_holdings}–{self.policy.max_holdings}",
                )
            )
        if nav.cash < 0:
            issues.append(
                ComplianceViolation(
                    "negative_cash", "HARD_FAIL", "Cash must not be negative"
                )
            )
        if nav.cash_ratio >= self.policy.cash_cap:
            issues.append(
                ComplianceViolation(
                    "cash_cap", "WARNING", "Cash / NAV must be strictly below 25%"
                )
            )
        overweight = {}
        for ticker, weight in nav.weights.items():
            if weight > self.policy.cap(ticker):
                streak = state.overweight_streaks.get(ticker, 0) + 1
                overweight[ticker] = streak
                severity = (
                    "WARNING"
                    if ticker in added_tickers
                    or streak > self.policy.overweight_grace_days
                    else "INFO"
                )
                issues.append(
                    ComplianceViolation(
                        "overweight",
                        severity,
                        f"Weight={weight}; price-drift grace session {streak}/5",
                        ticker,
                    )
                )
        active_streaks: dict[str, int] = dict(state.active_share_streaks)
        values = active_shares or {}
        active_date = state.active_share_last_date
        if (
            benchmark_snapshots is not None
            and benchmark_cutoff is not None
            and trading_days
        ):
            calculation = ActiveShareCalculator().evaluate(
                nav.weights,
                benchmark_snapshots,
                benchmark_cutoff,
                active_share_method,
                method_confirmed=self.policy.active_share_method_confirmed,
                fixture=fixture_mode,
            )
            previous_verified = (
                set(state.active_share_streaks) == ActiveETFLoader().tickers()
            )
            proof = ActiveShareValidator().validate(
                calculation,
                nav.trade_date,
                trading_days,
                previous_date=(
                    state.active_share_last_date if previous_verified else None
                ),
                previous_streaks=state.active_share_streaks,
            )
            if proof["streaks_advanced"]:
                values = {
                    row["benchmark_id"]: row["active_share"]
                    for row in calculation["records"]
                }
                active_streaks = proof["streaks"]
                active_date = nav.trade_date
                required_benchmarks = ActiveETFLoader().tickers()
            else:
                values = {}
                issues.append(
                    ComplianceViolation(
                        "active_share_evidence", "UNVERIFIED", proof["reason"]
                    )
                )
        elif benchmark_snapshots is not None or not fixture_mode:
            # A numeric dictionary is a research estimate, never source/time proof.
            values = {}
            issues.append(
                ComplianceViolation(
                    "active_share_evidence",
                    "UNVERIFIED",
                    "Structured same-session benchmark snapshots and consecutive-session evidence required",
                )
            )

        if strict and required_benchmarks != ActiveETFLoader().tickers():
            issues.append(
                ComplianceViolation(
                    "active_share_official_benchmarks",
                    "UNVERIFIED",
                    "Production compliance requires all 30 official active ETFs",
                )
            )
        if not required_benchmarks or set(values) != required_benchmarks:
            issues.append(
                ComplianceViolation(
                    "active_share_coverage",
                    "UNVERIFIED",
                    "Verified same-session comparisons with every official benchmark are required",
                )
            )
        for benchmark in sorted(required_benchmarks & set(values)):
            value = values[benchmark]
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or not 0 <= value <= 1
            ):
                issues.append(
                    ComplianceViolation(
                        "active_share_value", "HARD_FAIL", "Invalid Active Share value"
                    )
                )
                continue
            streak = (
                active_streaks.get(benchmark, 0)
                if benchmark_snapshots is not None
                else (
                    state.active_share_streaks.get(benchmark, 0) + 1
                    if value < self.policy.active_share_min
                    else 0
                )
            )
            active_streaks[benchmark] = streak
            if fixture_mode and benchmark_snapshots is None:
                active_date = nav.trade_date
            if streak >= self.policy.active_share_breach_days:
                issues.append(
                    ComplianceViolation(
                        "active_share_disqualification",
                        "HARD_FAIL",
                        f"{benchmark} below 20% for {streak} consecutive sessions",
                    )
                )
            elif streak:
                issues.append(
                    ComplianceViolation(
                        "active_share_first_breach",
                        "INFO",
                        f"{benchmark} below 20% for first session",
                    )
                )
        if strict and not self.policy.active_share_method_confirmed:
            issues.append(
                ComplianceViolation(
                    "active_share_method",
                    "UNVERIFIED",
                    "Top-10 raw/renormalized methodology awaits organizer confirmation",
                )
            )
        return ComplianceReport(tuple(issues), overweight, active_streaks, active_date)
