from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext

from src.competition.models import (
    AccountingError,
    PortfolioState,
    TradeOrder,
    TransactionCosts,
    decimal,
)
from src.competition.orders import derive_orders
from src.competition.policy import CompetitionPolicy
from src.data.universe import UniverseLoader


@dataclass(frozen=True)
class OptimizerConfig:
    version: str = "cost-aware-lot-search-1.0"
    holdings: int = 20
    turnover_cap: Decimal = Decimal("0.15")
    no_trade_band: Decimal = Decimal("0.005")
    minimum_alpha_advantage: Decimal = Decimal("0.25")
    cost_penalty: Decimal = Decimal("1")
    buy_stress: Decimal = Decimal("0.05")
    sell_stress: Decimal = Decimal("0.05")
    max_repairs: int = 12

    def __post_init__(self) -> None:
        for name in (
            "turnover_cap",
            "no_trade_band",
            "minimum_alpha_advantage",
            "cost_penalty",
            "buy_stress",
            "sell_stress",
        ):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if (
            not 20 <= self.holdings <= 30
            or not 0 < self.turnover_cap <= 2
            or not 0 <= self.no_trade_band < 1
            or not 0 <= self.minimum_alpha_advantage
            or self.cost_penalty < 0
            or not 0 <= self.buy_stress < 1
            or not 0 <= self.sell_stress < 1
            or not 1 <= self.max_repairs <= 50
        ):
            raise AccountingError("Invalid optimizer configuration")


@dataclass(frozen=True)
class OptimizationResult:
    status: str
    target_weights: dict[str, Decimal]
    orders: tuple[TradeOrder, ...]
    diagnostics: dict
    modifications: tuple[dict, ...]


class PortfolioOptimizer:
    """Transparent deterministic heuristic, not a claimed global optimum.

    Candidate selection starts from the Agent's approved stock list/rankings.
    Cost-adjusted replacement benefits, no-trade bands, turnover caps and cash
    stress checks limit trading. Every bounded repair is recorded for Agent
    approval; production must not silently replace its investment decision.
    """

    def __init__(
        self,
        config: OptimizerConfig | None = None,
        policy: CompetitionPolicy | None = None,
        costs: TransactionCosts | None = None,
    ) -> None:
        self.config, self.policy, self.costs = (
            config or OptimizerConfig(),
            policy or CompetitionPolicy(),
            costs or TransactionCosts(),
        )
        self.universe = UniverseLoader()

    def optimize(
        self,
        *,
        rankings: list[dict],
        state: PortfolioState,
        previous_nav: Decimal,
        previous_closes: dict[str, Decimal],
        regime: str,
        proposed_targets: dict[str, Decimal] | None = None,
        cash_range: tuple[Decimal, Decimal] | None = None,
    ) -> OptimizationResult:
        previous_nav = decimal(previous_nav)
        if previous_nav <= 0 or regime not in {"risk_on", "neutral", "risk_off"}:
            raise AccountingError("Invalid NAV or regime")
        cash_target = {
            "risk_on": Decimal("0.08"),
            "neutral": Decimal("0.12"),
            "risk_off": Decimal("0.20"),
        }[regime]
        low, high = cash_range or (
            max(Decimal("0.02"), cash_target - Decimal("0.03")),
            min(Decimal("0.24"), cash_target + Decimal("0.03")),
        )
        low, high = decimal(low), decimal(high)
        if not 0 <= low <= high < self.policy.cash_cap:
            raise AccountingError("Cash range requires buffer below official 25% cap")
        modifications: list[dict] = []
        scores: dict[str, Decimal] = {}
        for row in rankings:
            ticker = row["ticker"]
            if ticker in scores or not self.universe.is_valid_ticker(ticker):
                raise AccountingError("Duplicate or unsupported ranking ticker")
            scores[ticker] = decimal(str(row["score"]))
        if len(scores) < self.config.holdings or set(state.positions) - set(previous_closes):
            return OptimizationResult(
                "BLOCKED",
                {},
                (),
                {"reason": "Insufficient candidates or missing settled prices"},
                (),
            )
        if any(decimal(value) <= 0 for value in previous_closes.values()):
            raise AccountingError("Positive previous prices required")
        selected = sorted(scores, key=lambda t: (-scores[t], t))[: self.config.holdings]
        # Keep an existing eligible holding unless the new name clears both
        # a configured alpha hurdle and a round-trip transaction-cost penalty.
        if state.initialized and proposed_targets is None:
            outsiders = [t for t in state.positions if t in scores and t not in selected]
            entrants = [t for t in reversed(selected) if t not in state.positions]
            for old, new in zip(sorted(outsiders, key=lambda t: (-scores[t], t)), entrants):
                cost_hurdle = self.config.cost_penalty * (
                    self.costs.commission_rate * 2 + self.costs.sell_tax_rate
                )
                if scores[new] - scores[old] < self.config.minimum_alpha_advantage + cost_hurdle:
                    selected.remove(new)
                    selected.append(old)
                    modifications.append(
                        {"type": "cost_hysteresis", "retained": old, "rejected_replacement": new}
                    )
        if state.initialized and proposed_targets is None:
            # Budget new-name replacements before equal-weight allocation.
            # Actual lot/cost turnover is still checked below; this is a
            # conservative selection heuristic, not a relaxation of the cap.
            per_replacement = Decimal(2) * (1 - cash_target) / self.config.holdings
            allowed_replacements = int(self.config.turnover_cap / per_replacement)
            entrants = sorted(
                (t for t in selected if t not in state.positions), key=lambda t: (scores[t], t)
            )
            retainable = sorted(
                (t for t in state.positions if t in scores and t not in selected),
                key=lambda t: (-scores[t], t),
            )
            excess = max(0, len(entrants) - allowed_replacements)
            for old, new in zip(retainable, entrants[:excess]):
                selected.remove(new)
                selected.append(old)
                modifications.append(
                    {"type": "turnover_hysteresis", "retained": old, "rejected_replacement": new}
                )
        with localcontext() as context:
            context.prec = 50
            if proposed_targets is not None:
                targets = {t: decimal(w) for t, w in proposed_targets.items()}
                if not set(state.positions) <= set(targets):
                    return OptimizationResult(
                        "BLOCKED", {}, (), {"reason": "Agent omitted existing holdings"}, ()
                    )
                if set(targets) - (set(scores) | set(state.positions)):
                    return OptimizationResult(
                        "BLOCKED",
                        {},
                        (),
                        {"reason": "Agent selected a stock outside its approved candidates"},
                        (),
                    )
            else:
                allocation = (1 - cash_target) / self.config.holdings
                targets = {t: allocation for t in selected}
                targets.update({t: Decimal(0) for t in state.positions if t not in selected})
            for ticker in list(targets):
                if not self.universe.is_valid_ticker(ticker) or ticker not in previous_closes:
                    return OptimizationResult(
                        "BLOCKED",
                        {},
                        (),
                        {"reason": f"Unsupported or missing-price target {ticker}"},
                        tuple(modifications),
                    )
                if not 0 <= targets[ticker] <= self.policy.cap(ticker):
                    return OptimizationResult(
                        "BLOCKED",
                        targets,
                        (),
                        {"reason": f"Agent weight violates cap {ticker}"},
                        tuple(modifications),
                    )
                if ticker in state.positions:
                    old_weight = (
                        Decimal(state.positions[ticker].shares)
                        * decimal(previous_closes[ticker])
                        / previous_nav
                    )
                    if (
                        abs(targets[ticker] - old_weight) <= self.config.no_trade_band
                        and targets[ticker] > 0
                        and old_weight <= self.policy.cap(ticker)
                    ):
                        # A midpoint inside the lot cell preserves existing shares
                        # under exact official floor derivation without underflow.
                        held_target = (
                            (Decimal(state.positions[ticker].shares) + Decimal("0.1"))
                            * decimal(previous_closes[ticker])
                            / previous_nav
                        )
                        if (
                            held_target <= self.policy.cap(ticker)
                            and held_target != targets[ticker]
                        ):
                            modifications.append(
                                {
                                    "type": "no_trade_band",
                                    "ticker": ticker,
                                    "before": str(targets[ticker]),
                                    "after": str(held_target),
                                }
                            )
                            targets[ticker] = held_target
            for attempt in range(self.config.max_repairs + 1):
                try:
                    orders = derive_orders(
                        targets, state, previous_nav, previous_closes, policy=self.policy
                    )
                except AccountingError as exc:
                    return OptimizationResult(
                        "BLOCKED", targets, (), {"reason": str(exc)}, tuple(modifications)
                    )
                diagnostics = self.estimate(orders, state, previous_nav, previous_closes)
                count = diagnostics["holdings"]
                if count < self.policy.min_holdings or count > self.policy.max_holdings:
                    return OptimizationResult(
                        "BLOCKED",
                        targets,
                        tuple(orders),
                        {**diagnostics, "reason": "Lot rounding violates holding count"},
                        tuple(modifications),
                    )
                if state.initialized and diagnostics["gross_turnover"] > self.config.turnover_cap:
                    return OptimizationResult(
                        "BLOCKED",
                        targets,
                        tuple(orders),
                        {
                            **diagnostics,
                            "reason": "Turnover cap exceeded; Agent must revise targets",
                        },
                        tuple(modifications),
                    )
                if (
                    diagnostics["cash_ratio"] >= self.policy.cash_cap
                    or diagnostics["cash_ratio"] > high
                ):
                    return OptimizationResult(
                        "BLOCKED",
                        targets,
                        tuple(orders),
                        {**diagnostics, "reason": "Too much cash after lot/cost estimation"},
                        tuple(modifications),
                    )
                overweight = [
                    t
                    for t, weight in diagnostics["estimated_weights"].items()
                    if weight > self.policy.cap(t)
                ]
                if overweight:
                    return OptimizationResult(
                        "BLOCKED",
                        targets,
                        tuple(orders),
                        {
                            **diagnostics,
                            "reason": "Estimated post-cost concentration exceeds cap",
                            "tickers": overweight,
                        },
                        tuple(modifications),
                    )
                if diagnostics["cash_ratio"] >= low and diagnostics["stress_cash"] >= 0:
                    return OptimizationResult(
                        "PROPOSED",
                        targets,
                        tuple(orders),
                        {
                            **diagnostics,
                            "cash_range": [str(low), str(high)],
                            "regime": regime,
                            "method": "deterministic bounded heuristic",
                            "strategy_version": self.config.version,
                            "agent_approval_required": bool(modifications),
                        },
                        tuple(modifications),
                    )
                if attempt == self.config.max_repairs:
                    break
                buys = [o for o in orders if o.side == "BUY" and o.shares > 1000]
                if not buys:
                    break
                trim = min(buys, key=lambda o: (scores.get(o.ticker, Decimal("-1E9")), o.ticker))
                current = state.positions.get(trim.ticker)
                target_shares = (current.shares if current else 0) + trim.shares - 1000
                revised = (
                    (Decimal(target_shares) + Decimal("0.1"))
                    * decimal(previous_closes[trim.ticker])
                    / previous_nav
                )
                modifications.append(
                    {
                        "type": "cash_stress_repair",
                        "iteration": attempt + 1,
                        "ticker": trim.ticker,
                        "before": str(targets[trim.ticker]),
                        "after": str(revised),
                    }
                )
                targets[trim.ticker] = revised
            return OptimizationResult(
                "BLOCKED",
                targets,
                tuple(orders),
                {**diagnostics, "reason": "Bounded cash stress repair exhausted"},
                tuple(modifications),
            )

    def estimate(
        self,
        orders: list[TradeOrder],
        state: PortfolioState,
        nav: Decimal,
        closes: dict[str, Decimal],
    ) -> dict:
        cash, stress_cash, gross_turnover, commissions, taxes = (
            state.cash,
            state.cash,
            Decimal(0),
            Decimal(0),
            Decimal(0),
        )
        shares = {t: p.shares for t, p in state.positions.items()}
        for order in orders:
            price = decimal(closes[order.ticker])
            gross = price * order.shares
            commission, tax = self.costs.fees(gross, order.side)
            stress_gross = gross * (
                1 + self.config.buy_stress if order.side == "BUY" else 1 - self.config.sell_stress
            )
            stress_commission, stress_tax = self.costs.fees(stress_gross, order.side)
            sign = 1 if order.side == "BUY" else -1
            cash -= sign * gross + commission + tax
            stress_cash -= sign * stress_gross + stress_commission + stress_tax
            shares[order.ticker] = shares.get(order.ticker, 0) + sign * order.shares
            gross_turnover += gross
            commissions += commission
            taxes += tax
        if any(value < 0 for value in shares.values()):
            raise AccountingError("Estimated oversell")
        market_values = {t: value * decimal(closes[t]) for t, value in shares.items() if value}
        estimated_nav = cash + sum(market_values.values(), Decimal(0))
        if estimated_nav <= 0:
            raise AccountingError("Non-positive estimated NAV")
        return {
            "cash": cash,
            "cash_ratio": cash / nav,
            "stress_cash": stress_cash,
            "gross_turnover": gross_turnover / nav,
            "commission": commissions,
            "tax": taxes,
            "holdings": len(market_values),
            "estimated_nav": estimated_nav,
            "estimated_weights": {t: value / estimated_nav for t, value in market_values.items()},
        }
