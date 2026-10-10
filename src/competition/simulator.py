from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, localcontext

from src.data.universe import UniverseLoader
from src.portfolio.compliance import ComplianceEngine
from .models import (
    AccountingError,
    ComplianceViolation,
    CorporateAction,
    DailyNAV,
    PortfolioState,
    Position,
    PriceBar,
    SimulationResult,
    TradeExecution,
    TradeOrder,
    TransactionCosts,
    day,
)
from .policy import CompetitionPolicy
from .corporate_actions import CompetitionCorporateActionPolicy


def mark_to_market(state: PortfolioState, bars: dict[str, PriceBar], trade_date: str) -> DailyNAV:
    values: dict[str, Decimal] = {}
    for ticker, position in state.positions.items():
        bar = bars.get(ticker)
        if bar is None or bar.trade_date != trade_date or bar.ticker != ticker or bar.close is None:
            raise AccountingError(f"Missing same-session valuation close: {ticker}")
        values[ticker] = bar.close * position.shares
    market_value = sum(values.values(), Decimal(0))
    nav = market_value + state.cash
    if nav <= 0:
        raise AccountingError("NAV must remain positive")
    return DailyNAV(
        trade_date,
        state.cash,
        market_value,
        nav,
        state.deferred_dividends,
        {ticker: value / nav for ticker, value in values.items()},
        state.cash / nav,
    )


class PortfolioSimulator:
    """Pure day transaction: return a new ledger; never mutate caller state.

    Historical execution inputs are separate from any decision feature snapshot.
    Dividend receivables are excluded from daily NAV until competition-end settlement.
    Commission/tax rounding is explicit and not yet organizer-verified by default.
    """

    def __init__(
        self, policy: CompetitionPolicy | None = None, costs: TransactionCosts | None = None
    ) -> None:
        self.policy = policy or CompetitionPolicy()
        self.costs = costs or TransactionCosts()
        self.universe = UniverseLoader()
        self.compliance = ComplianceEngine(self.policy)
        self.corporate_action_policy = CompetitionCorporateActionPolicy()

    def execute_day(
        self,
        state: PortfolioState,
        trade_date: str,
        orders: list[TradeOrder],
        bars: dict[str, PriceBar],
        *,
        trading_days: set[str],
        calendar_verified: bool,
        active_shares: dict[str, Decimal] | None = None,
        required_benchmarks: set[str] | None = None,
        corporate_actions: tuple[CorporateAction, ...] = (),
        decision_cutoff: str | None = None,
        strict: bool = True,
        fixture_mode: bool = False,
        settle_dividends: bool = False,
        competition_end: str = "2026-11-27",
    ) -> SimulationResult:
        day(trade_date)
        if any(not self.universe.is_valid_ticker(ticker) for ticker in state.positions):
            raise AccountingError("Settled state contains stock outside official universe")
        if state.last_trade_date and trade_date <= state.last_trade_date:
            raise AccountingError("Duplicate or out-of-order session; ledger replay prohibited")
        if state.disqualified:
            return SimulationResult(
                "DISQUALIFIED", deepcopy(state), None, (), (), trade_date, fixture_mode
            )
        if trade_date not in trading_days:
            raise AccountingError("Not a calendar trading session; weekday alone is insufficient")
        if any(
            ticker != bar.ticker or bar.trade_date != trade_date for ticker, bar in bars.items()
        ):
            raise AccountingError("Execution bars must match ticker keys and the trade session")
        if any(bar.synthetic for bar in bars.values()) and not fixture_mode:
            raise AccountingError("Synthetic prices require explicit fixture mode")
        if fixture_mode and strict:
            raise AccountingError("Synthetic fixtures cannot certify production compliance")
        blocked = []
        if strict and not calendar_verified:
            blocked.append(
                ComplianceViolation("calendar", "UNVERIFIED", "Exchange calendar not verified")
            )
        if strict and not self.policy.cash_rounding_confirmed:
            blocked.append(
                ComplianceViolation(
                    "fee_rounding",
                    "UNVERIFIED",
                    "Commission/tax cash rounding awaits official confirmation",
                )
            )
        if blocked:
            return SimulationResult(
                "BLOCKED", deepcopy(state), None, (), tuple(blocked), trade_date, fixture_mode
            )
        seen: set[str] = set()
        order_errors = []
        for order in orders:
            if order.ticker in seen:
                order_errors.append(
                    ComplianceViolation(
                        "duplicate_order",
                        "HARD_FAIL",
                        "Repeated ticker or same-session round trip",
                        order.ticker,
                    )
                )
            seen.add(order.ticker)
            if not self.universe.is_valid_ticker(order.ticker):
                order_errors.append(
                    ComplianceViolation(
                        "universe", "HARD_FAIL", "Outside official 150-stock universe", order.ticker
                    )
                )
            held = state.positions.get(order.ticker)
            if order.side == "SELL" and (held is None or held.shares < order.shares):
                order_errors.append(
                    ComplianceViolation(
                        "oversell",
                        "HARD_FAIL",
                        "Cannot short or oversell settled holdings",
                        order.ticker,
                    )
                )
        baseline = deepcopy(state)
        self._apply_actions(baseline, trade_date, corporate_actions, decision_cutoff)
        if settle_dividends:
            if trade_date != competition_end or baseline.dividends_settled:
                raise AccountingError("Deferred dividend settlement only once on competition end")
            baseline.cash += baseline.deferred_dividends
            baseline.deferred_dividends = Decimal(0)
            baseline.dividends_settled = True
        try:
            baseline_nav = mark_to_market(baseline, bars, trade_date)
        except AccountingError as exc:
            return SimulationResult(
                "BLOCKED",
                deepcopy(state),
                None,
                (),
                (ComplianceViolation("valuation", "UNVERIFIED", str(exc)),),
                trade_date,
                fixture_mode,
            )
        if order_errors:
            return self._reject_day(baseline, baseline_nav, trade_date, order_errors, fixture_mode)
        candidate = deepcopy(baseline)
        executions = []
        skipped: list[ComplianceViolation] = []
        # Sells precede buys for deterministic cash settlement, with fees charged on every order.
        # Both sides of the same ticker were already rejected; no intraday funding round trip.
        with localcontext() as context:
            context.prec = 50
            for order in sorted(orders, key=lambda o: (o.side != "SELL", o.ticker)):
                bar = bars.get(order.ticker)
                try:
                    if bar is None:
                        raise AccountingError(f"Missing execution bar: {order.ticker}")
                    price = bar.execution_price()
                except AccountingError as exc:
                    issue = ComplianceViolation(
                        "execution_price", "UNVERIFIED", str(exc), order.ticker
                    )
                    if self.policy.missing_execution == "block" or (
                        strict and not self.policy.missing_execution_confirmed
                    ):
                        return SimulationResult(
                            "BLOCKED", deepcopy(state), None, (), (issue,), trade_date, fixture_mode
                        )
                    if self.policy.missing_execution == "reject_day":
                        return self._reject_day(
                            baseline, baseline_nav, trade_date, [issue], fixture_mode
                        )
                    skipped.append(issue)
                    continue
                gross = price * order.shares
                commission, tax = self.costs.fees(gross, order.side)
                old = candidate.positions.get(order.ticker)
                old_shares = old.shares if old else 0
                if order.side == "BUY":
                    candidate.cash -= gross + commission
                    new_shares = old_shares + order.shares
                else:
                    candidate.cash += gross - commission - tax
                    new_shares = old_shares - order.shares
                if new_shares:
                    candidate.positions[order.ticker] = Position(order.ticker, new_shares)
                else:
                    candidate.positions.pop(order.ticker, None)
                executions.append(TradeExecution(order, price, gross, commission, tax, bar.source))
            try:
                nav = mark_to_market(candidate, bars, trade_date)
            except AccountingError as exc:
                return SimulationResult(
                    "BLOCKED",
                    deepcopy(state),
                    None,
                    (),
                    (ComplianceViolation("valuation", "UNVERIFIED", str(exc)),),
                    trade_date,
                    fixture_mode,
                )
            report = self.compliance.post_trade(
                candidate,
                nav,
                {e.order.ticker for e in executions if e.order.side == "BUY"},
                active_shares,
                required_benchmarks or set(),
                strict,
            )
            issues = list(report.violations) + skipped
            if strict and not report.verified:
                return SimulationResult(
                    "BLOCKED", deepcopy(state), None, (), tuple(issues), trade_date, fixture_mode
                )
            if any(v.code == "active_share_disqualification" for v in issues):
                # Active Share is evaluated on end-of-day holdings even if another rule breaches.
                baseline.active_share_streaks = report.active_share_streaks
                baseline.disqualified = True
                baseline.last_trade_date = trade_date
                return SimulationResult(
                    "DISQUALIFIED",
                    baseline,
                    baseline_nav,
                    (),
                    tuple(issues),
                    trade_date,
                    fixture_mode,
                )
            if report.failed:
                return self._reject_day(baseline, baseline_nav, trade_date, issues, fixture_mode)
            candidate.initialized = True
            candidate.last_trade_date = trade_date
            candidate.overweight_streaks = report.overweight_streaks
            candidate.active_share_streaks = report.active_share_streaks
            return SimulationResult(
                "EXECUTED",
                candidate,
                nav,
                tuple(executions),
                tuple(issues),
                trade_date,
                fixture_mode,
            )

    def _reject_day(
        self,
        baseline: PortfolioState,
        nav: DailyNAV,
        trade_date: str,
        issues: list[ComplianceViolation],
        fixture: bool,
    ) -> SimulationResult:
        # One warning per failed day, regardless of the number of violated rules.
        baseline.warning_count += 1
        baseline.disqualified = baseline.warning_count >= self.policy.warning_limit
        baseline.last_trade_date = trade_date
        # Invalid trades do not modify inventory. Still advance price-drift streaks for old holdings.
        baseline.overweight_streaks = {
            ticker: baseline.overweight_streaks.get(ticker, 0) + 1
            for ticker, weight in nav.weights.items()
            if weight > self.policy.cap(ticker)
        }
        return SimulationResult(
            "DISQUALIFIED" if baseline.disqualified else "REJECTED",
            baseline,
            nav,
            (),
            tuple(issues),
            trade_date,
            fixture,
        )

    def _apply_actions(
        self,
        state: PortfolioState,
        trade_date: str,
        actions: tuple[CorporateAction, ...],
        cutoff: str | None,
    ) -> None:
        if actions and cutoff is None:
            raise AccountingError("Corporate actions require an explicit availability cutoff")
        for action in actions:
            self.corporate_action_policy.validate(action)
            if action.effective_date != trade_date or action.action_id in state.applied_actions:
                raise AccountingError("Wrong-date or already applied corporate action")
            if datetime.fromisoformat(action.available_at) > datetime.fromisoformat(cutoff):
                raise AccountingError("Future corporate-action information")
            if not self.universe.is_valid_ticker(action.ticker):
                raise AccountingError("Corporate action outside universe")
            position = state.positions.get(action.ticker)
            if position:
                if action.kind == "cash_dividend":
                    state.deferred_dividends += position.shares * action.value
                else:
                    ratio = action.value if action.kind == "split" else Decimal(1) + action.value
                    shares = Decimal(position.shares) * ratio
                    if shares != shares.to_integral_value():
                        raise AccountingError(
                            "Fractional corporate-action shares require verified settlement policy"
                        )
                    state.positions[action.ticker] = Position(action.ticker, int(shares))
            state.applied_actions.add(action.action_id)
