from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.competition.models import ComplianceViolation, DailyNAV, PortfolioState
from src.competition.policy import CompetitionPolicy


@dataclass(frozen=True)
class ComplianceReport:
    violations: tuple[ComplianceViolation, ...]
    overweight_streaks: dict[str, int]
    active_share_streaks: dict[str, int]

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
                ComplianceViolation("negative_cash", "HARD_FAIL", "Cash must not be negative")
            )
        if nav.cash_ratio >= self.policy.cash_cap:
            issues.append(
                ComplianceViolation("cash_cap", "WARNING", "Cash / NAV must be strictly below 25%")
            )
        overweight = {}
        for ticker, weight in nav.weights.items():
            if weight > self.policy.cap(ticker):
                streak = state.overweight_streaks.get(ticker, 0) + 1
                overweight[ticker] = streak
                severity = (
                    "WARNING"
                    if ticker in added_tickers or streak > self.policy.overweight_grace_days
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
        active_streaks: dict[str, int] = {}
        values = active_shares or {}
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
            if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1:
                issues.append(
                    ComplianceViolation(
                        "active_share_value", "HARD_FAIL", "Invalid Active Share value"
                    )
                )
                continue
            streak = (
                state.active_share_streaks.get(benchmark, 0) + 1
                if value < self.policy.active_share_min
                else 0
            )
            active_streaks[benchmark] = streak
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
        return ComplianceReport(tuple(issues), overweight, active_streaks)
