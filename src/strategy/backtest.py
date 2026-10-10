from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev

from src.competition.models import PortfolioState, PriceBar, SimulationResult
from src.competition.simulator import PortfolioSimulator
from src.portfolio.optimizer import OptimizerConfig, PortfolioOptimizer
from .availability import AvailabilityPolicy
from .features import FeatureEngine
from .ranking import AlphaModel


@dataclass(frozen=True)
class WalkForwardSplit:
    training: tuple[str, ...]
    validation: tuple[str, ...]
    test: tuple[str, ...]


def spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i + 1
            while j < len(order) and values[order[j]] == values[order[i]]:
                j += 1
            for k in range(i, j):
                result[order[k]] = (i + j - 1) / 2
            i = j
        return result

    a, b = ranks(left), ranks(right)
    center_a, center_b = mean(a), mean(b)
    numerator = sum((x - center_a) * (y - center_b) for x, y in zip(a, b))
    denominator = sqrt(sum((x - center_a) ** 2 for x in a) * sum((y - center_b) ** 2 for y in b))
    return numerator / denominator if denominator else None


def walk_forward_splits(
    sessions: list[str],
    training_days: int = 40,
    validation_days: int = 24,
    test_days: int = 24,
    expanding: bool = True,
) -> list[WalkForwardSplit]:
    if sessions != sorted(set(sessions)) or min(training_days, validation_days, test_days) <= 0:
        raise ValueError("Unique ordered sessions and positive windows required")
    result = []
    test_start = training_days + validation_days
    while test_start + test_days <= len(sessions):
        training_end = test_start - validation_days
        training_start = 0 if expanding else training_end - training_days
        result.append(
            WalkForwardSplit(
                tuple(sessions[training_start:training_end]),
                tuple(sessions[training_end:test_start]),
                tuple(sessions[test_start : test_start + test_days]),
            )
        )
        test_start += test_days
    return result


def performance_metrics(
    results: list[SimulationResult], initial_nav: Decimal = Decimal("1000000000")
) -> dict:
    navs = [float(initial_nav)] + [float(r.daily_nav.nav) for r in results if r.daily_nav]
    returns = [current / previous - 1 for previous, current in zip(navs, navs[1:])]
    peak, drawdown = navs[0], 0.0
    for value in navs:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    dispersion = pstdev(returns) if len(returns) > 1 else None
    commissions = sum((e.commission for r in results for e in r.executions), Decimal(0))
    taxes = sum((e.tax for r in results for e in r.executions), Decimal(0))
    previous_nav = initial_nav
    turnovers = []
    basis: dict[str, tuple[int, Decimal]] = {}
    realized = []
    for result in results:
        for execution in result.executions:
            ticker = execution.order.ticker
            old_shares, cost = basis.get(ticker, (0, Decimal(0)))
            if execution.order.side == "BUY":
                basis[ticker] = (
                    old_shares + execution.order.shares,
                    cost + execution.gross + execution.commission,
                )
            elif old_shares >= execution.order.shares and old_shares:
                allocated = cost * execution.order.shares / old_shares
                realized.append(execution.gross - execution.commission - execution.tax - allocated)
                basis[ticker] = (old_shares - execution.order.shares, cost - allocated)
        if result.daily_nav:
            turnovers.append(
                float(sum((e.gross for e in result.executions), Decimal(0)) / previous_nav)
            )
            previous_nav = result.daily_nav.nav
    return {
        "terminal_nav_ntd": (
            str(results[-1].daily_nav.nav) if results and results[-1].daily_nav else None
        ),
        "total_return": navs[-1] / navs[0] - 1 if returns else None,
        "max_drawdown": drawdown if returns else None,
        "annualized_volatility": dispersion * sqrt(252) if dispersion is not None else None,
        "sharpe_zero_risk_free": mean(returns) / dispersion * sqrt(252) if dispersion else None,
        "gross_turnover_sum": sum(turnovers),
        "commission_ntd": str(commissions),
        "tax_ntd": str(taxes),
        "positive_day_fraction": (
            sum(value > 0 for value in returns) / len(returns) if returns else None
        ),
        "trade_hit_rate": (
            sum(value > 0 for value in realized) / len(realized) if realized else None
        ),
        "realized_sell_orders": len(realized),
        "rank_ic": None,
        "active_share_verified": False,
        "warning_days": sum(r.status == "REJECTED" for r in results),
        "disqualified": any(r.status == "DISQUALIFIED" for r in results),
        "metric_notes": [
            "Turnover = total buy plus sell gross / previous NAV; first establishment included",
            "Sharpe uses 252 sessions and zero risk-free rate; short-window annualization unstable",
            "Trade hit rate = positive realized sell-order PnL / matched sell orders, using weighted-average buy cost including commission",
            "Rank IC is Spearman correlation of prior-cutoff score and next-session close-to-close return, before costs",
            "Active ETF holdings and corporate actions are not yet independently complete",
        ],
    }


class FactorBacktester:
    def __init__(
        self,
        holdings: int = 20,
        availability: AvailabilityPolicy | None = None,
        alpha_model: AlphaModel | None = None,
    ) -> None:
        self.availability = availability or AvailabilityPolicy()
        self.features = FeatureEngine(self.availability)
        self.model = alpha_model or AlphaModel()
        self.optimizer = PortfolioOptimizer(OptimizerConfig(holdings=holdings))
        self.simulator = PortfolioSimulator()

    def run(
        self,
        *,
        prices: list[dict],
        flows: list[dict],
        revenue: list[dict],
        start: str,
        end: str,
        baseline: str = "multifactor",
        market: list[dict] | None = None,
        calendar: set[str] | None = None,
    ) -> dict:
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError("Backtest start after end")
        fixture = self.availability.mode == "synthetic_fixture"
        if self.availability.mode == "strict_verified":
            # A strict backtest also requires corporate actions and benchmark
            # availability; prices alone cannot certify a historical replay.
            return {
                "status": "BLOCKED",
                "mode": "strict_verified",
                "reason": "Verified historical corporate actions, official calendar and all ETF holdings required",
                "metrics": None,
            }
        sessions = sorted({r["trade_date"] for r in prices})
        trading_days = calendar or set(sessions)
        state = PortfolioState()
        results: list[SimulationResult] = []
        audit = []
        rank_ics = []
        for on in (d for d in sessions if start <= d <= end):
            previous_sessions = [d for d in sessions if d < on]
            if not previous_sessions:
                audit.append({"trade_date": on, "status": "BLOCKED", "reason": "No prior session"})
                continue
            previous_day = previous_sessions[-1]
            previous_closes = {
                r["ticker"]: Decimal(str(r["close"]))
                for r in prices
                if r["trade_date"] == previous_day and r.get("close") is not None and r["close"] > 0
            }
            if set(state.positions) - set(previous_closes):
                audit.append(
                    {
                        "trade_date": on,
                        "status": "BLOCKED",
                        "reason": "Missing previous close for held stock",
                    }
                )
                break
            previous_nav = state.cash + sum(
                (p.shares * previous_closes[t] for t, p in state.positions.items()), Decimal(0)
            )
            cutoff = f"{on}T08:30:00+08:00"
            snapshot = self.features.build(
                prices=prices,
                flows=flows,
                revenue=revenue,
                market=market,
                cutoff=cutoff,
                trading_days=trading_days,
            )
            ranking = self.model.rank(snapshot, baseline)
            # Only stocks with a usable previous official price may enter.
            candidates = [r for r in ranking["rankings"] if r["ticker"] in previous_closes]
            proposal = self.optimizer.optimize(
                rankings=candidates,
                state=state,
                previous_nav=previous_nav,
                previous_closes=previous_closes,
                regime="neutral",
            )
            if proposal.status != "PROPOSED":
                audit.append(
                    {
                        "trade_date": on,
                        "status": "BLOCKED",
                        "reason": proposal.diagnostics.get("reason"),
                        "excluded_candidates": len(ranking["excluded"]),
                    }
                )
                break
            execution_bars = {
                r["ticker"]: PriceBar.from_data_row(r) for r in prices if r["trade_date"] == on
            }
            aligned = [
                r
                for r in candidates
                if r["ticker"] in execution_bars and execution_bars[r["ticker"]].close is not None
            ]
            ic = spearman(
                [r["score"] for r in aligned],
                [
                    float(execution_bars[r["ticker"]].close / previous_closes[r["ticker"]] - 1)
                    for r in aligned
                ],
            )
            if ic is not None:
                rank_ics.append(ic)
            result = self.simulator.execute_day(
                state,
                on,
                list(proposal.orders),
                execution_bars,
                trading_days=trading_days,
                calendar_verified=calendar is not None,
                strict=False,
                fixture_mode=fixture,
            )
            results.append(result)
            audit.append(
                {
                    "trade_date": on,
                    "decision_cutoff": cutoff,
                    "decision_feature_latest_date": max(
                        (
                            r["observation_date"]
                            for rows in snapshot.provenance.values()
                            for r in rows
                            if r["dataset"] == "prices"
                        ),
                        default=None,
                    ),
                    "status": result.status,
                    "optimizer_modifications": list(proposal.modifications),
                    "violations": [v.code for v in result.violations],
                    "orders": len(result.executions),
                }
            )
            if result.status in {"BLOCKED", "DISQUALIFIED"}:
                break
            state = result.state
        metrics = performance_metrics(results)
        metrics["rank_ic"] = mean(rank_ics) if rank_ics else None
        metrics["rank_ic_sessions"] = len(rank_ics)
        return {
            "status": (
                "COMPLETED_CONDITIONAL"
                if results
                and results[-1].status == "EXECUTED"
                and len(results) == sum(start <= d <= end for d in sessions)
                else "PARTIALLY_COMPLETED"
            ),
            "mode": self.availability.mode,
            "synthetic": fixture,
            "baseline": baseline,
            "holding_count": self.optimizer.config.holdings,
            "factor_version": self.features.calculator.VERSION,
            "strategy_version": self.model.config.version,
            "requested_range": [start, end],
            "actual_range": [results[0].trade_date, results[-1].trade_date] if results else None,
            "sessions_simulated": len(results),
            "metrics": metrics if results else None,
            "audit": audit,
            "daily_results": results,
            "limitations": [
                "Fixed 2026 competition universe creates survivorship bias",
                "Historical price availability and revisions are assumption-dependent",
                "No independently complete corporate-action history; returns use raw closes and may be biased",
                "All official active-ETF historical holdings unavailable; AS compliance unverified",
                "Calendar inferred from observed exchange sessions unless supplied verified calendar",
                "Fixed baseline parameters; no final-test tuning or winning-strategy claim",
            ],
        }
