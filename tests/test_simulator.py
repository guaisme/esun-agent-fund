"""SYNTHETIC accounting fixtures only; never market data or empirical performance."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal as D

import pytest

from src.competition.models import (
    AccountingError,
    CorporateAction,
    PortfolioState,
    Position,
    PriceBar,
    TradeOrder,
    TransactionCosts,
    decimal,
)
from src.competition.orders import derive_orders
from src.competition.policy import CompetitionPolicy
from src.competition.simulator import PortfolioSimulator, mark_to_market
from src.data.universe import UniverseLoader
from src.portfolio.active_share import (
    BenchmarkHoldings,
    BenchmarkHoldingsProvider,
    calculate_active_share,
)

DAYS = {
    "2026-10-27",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
    "2026-11-02",
    "2026-11-03",
    "2026-11-04",
    "2026-11-27",
}
TICKERS = sorted(UniverseLoader().get_all_tickers())[:21]


def bars(on: str, close: str = "100", average: str = "100") -> dict[str, PriceBar]:
    return {
        ticker: PriceBar(
            ticker, on, D(close), 1000000, D(average) * 1000000, "SYNTHETIC_FIXTURE", synthetic=True
        )
        for ticker in TICKERS
    }


def run(state: PortfolioState, on: str, orders: list[TradeOrder], prices=None, **kwargs):
    return PortfolioSimulator().execute_day(
        state,
        on,
        orders,
        prices or bars(on),
        trading_days=DAYS,
        calendar_verified=False,
        strict=False,
        fixture_mode=True,
        **kwargs
    )


def initial():
    # 20 stocks at 4%, 80% invested, 20% cash before explicit costs.
    return run(PortfolioState(), "2026-10-27", [TradeOrder(t, "BUY", 400000) for t in TICKERS[:20]])


def test_initial_golden_accounting_and_input_immutability():
    state = PortfolioState()
    result = run(state, "2026-10-27", [TradeOrder(t, "BUY", 400000) for t in TICKERS[:20]])
    assert result.status == "EXECUTED"
    assert state == PortfolioState()
    assert len(result.state.positions) == 20
    assert result.state.cash == D("198860000")
    assert result.daily_nav.market_value == D("800000000")
    assert result.daily_nav.nav == D("998860000")
    assert sum(e.commission for e in result.executions) == D("1140000")
    assert not sum(e.tax for e in result.executions)
    assert result.synthetic


def test_multi_day_hold_add_reduce_sell_and_next_state():
    first = initial()
    held = run(first.state, "2026-10-28", [], bars("2026-10-28", "101"))
    assert held.status == "EXECUTED" and held.executions == ()
    assert held.daily_nav.nav == D("1006860000")
    orders = [TradeOrder(TICKERS[0], "BUY", 10000), TradeOrder(TICKERS[1], "SELL", 10000)]
    changed = run(held.state, "2026-10-29", orders)
    assert changed.status == "EXECUTED"
    assert changed.state.cash == D("198854150")  # two commissions 1425 each; sell tax 3000
    assert changed.state.positions[TICKERS[0]].shares == 410000
    assert changed.state.positions[TICKERS[1]].shares == 390000
    replacement = run(
        changed.state,
        "2026-10-30",
        [TradeOrder(TICKERS[2], "SELL", 400000), TradeOrder(TICKERS[20], "BUY", 400000)],
    )
    assert replacement.status == "EXECUTED"
    assert TICKERS[2] not in replacement.state.positions
    assert len(replacement.state.positions) == 20
    assert replacement.state.cash == D("198620150")
    assert replacement.daily_nav.nav == D("998620150")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.1, True, "NaN", "Infinity"])
def test_no_binary_float_or_nonfinite_accounting(value):
    with pytest.raises(AccountingError):
        decimal(value)


@pytest.mark.parametrize("shares", [0, -1000, 100, 1000.0, True])
def test_invalid_lots(shares):
    with pytest.raises(AccountingError):
        TradeOrder(TICKERS[0], "BUY", shares)


def test_exact_official_order_derivation_and_hold_no_zero_orders():
    state = PortfolioState(positions={TICKERS[0]: Position(TICKERS[0], 400000)})
    targets = {TICKERS[0]: D("0.04001"), TICKERS[1]: D("0.035999")}
    orders = derive_orders(targets, state, D("1000000000"), {t: D("100") for t in targets})
    assert orders == [TradeOrder(TICKERS[1], "BUY", 359000)]
    with pytest.raises(AccountingError, match="explicit target"):
        derive_orders({}, state, D(100), {})


def test_cash_boundary_25_percent_rejected_single_warning():
    orders = [TradeOrder(t, "BUY", 1000) for t in TICKERS[:19]]
    result = run(PortfolioState(), "2026-10-27", orders)
    assert result.status == "REJECTED" and result.state.warning_count == 1
    assert result.state.cash == D("1000000000") and not result.state.positions
    assert result.executions == ()
    assert {v.code for v in result.violations} >= {"holding_count", "cash_cap"}


def test_oversell_outside_universe_and_roundtrip_atomic_reject():
    first = initial()
    original = deepcopy(first.state)
    for orders in (
        [TradeOrder(TICKERS[0], "SELL", 401000)],
        [TradeOrder("0000", "BUY", 1000)],
        [TradeOrder(TICKERS[0], "BUY", 1000), TradeOrder(TICKERS[0], "SELL", 1000)],
    ):
        result = run(first.state, "2026-10-28", orders)
        assert result.status == "REJECTED" and result.executions == ()
        assert result.state.positions == original.positions
        assert first.state == original


def test_no_borrowing_to_fund_orders():
    first = initial()
    result = run(first.state, "2026-10-28", [TradeOrder(t, "BUY", 200000) for t in TICKERS[:20]])
    assert result.status == "REJECTED"
    assert any(v.code == "negative_cash" for v in result.violations)
    assert result.state.cash == first.state.cash


def test_three_invalid_days_disqualify_and_no_replay():
    state = PortfolioState()
    for i, on in enumerate(sorted(DAYS)[:3], 1):
        result = run(state, on, [])
        assert result.state.warning_count == i
        state = result.state
    assert result.status == "DISQUALIFIED"
    with pytest.raises(AccountingError, match="replay"):
        run(state, on, [])


def test_price_drift_has_five_session_grace_and_sixth_warning():
    state = initial().state
    for i, on in enumerate(sorted(DAYS - {"2026-10-27", "2026-11-27"}), 1):
        prices = bars(on)
        prices[TICKERS[0]] = PriceBar(
            TICKERS[0], on, D(300), 1000000, D(300000000), "SYNTHETIC_FIXTURE", synthetic=True
        )
        result = run(state, on, [], prices)
        assert result.state.overweight_streaks[TICKERS[0]] == i
        assert result.status == ("EXECUTED" if i <= 5 else "REJECTED")
        state = result.state


def test_added_overweight_warns_immediately():
    first = initial()
    result = run(first.state, "2026-10-28", [TradeOrder(TICKERS[0], "BUY", 700000)])
    assert result.status == "REJECTED"
    assert any(v.code == "overweight" and v.severity == "WARNING" for v in result.violations)


def test_missing_execution_never_fallback_to_close():
    first = initial()
    prices = bars("2026-10-28")
    prices[TICKERS[0]] = PriceBar(
        TICKERS[0], "2026-10-28", D(100), 0, D(0), "SYNTHETIC_FIXTURE", synthetic=True
    )
    result = run(first.state, "2026-10-28", [TradeOrder(TICKERS[0], "BUY", 1000)], prices)
    assert result.status == "BLOCKED" and result.state == first.state
    assert result.executions == ()


def test_missing_valuation_blocks_even_hold_day():
    first = initial()
    prices = bars("2026-10-28")
    del prices[TICKERS[0]]
    result = run(first.state, "2026-10-28", [], prices)
    assert result.status == "BLOCKED" and result.daily_nav is None


def test_daily_execution_uses_turnover_over_shares_not_close():
    result = run(
        PortfolioState(),
        "2026-10-27",
        [TradeOrder(t, "BUY", 400000) for t in TICKERS[:20]],
        bars("2026-10-27", "105", "100.25"),
    )
    assert result.status == "EXECUTED"
    assert result.executions[0].price == D("100.25")
    assert result.daily_nav.market_value == D("840000000")
    assert result.state.cash == D("196857150")


def test_units_are_never_coerced_or_assumed():
    with pytest.raises(AccountingError, match="shares and NTD"):
        PriceBar(TICKERS[0], "2026-10-27", D(100), 1000, D(100), "official", volume_unit="lots")


def test_fee_rounding_explicit():
    commission, tax = TransactionCosts(quantum=D("1")).fees(D("1001"), "SELL")
    assert (commission, tax) == (D(1), D(3))


def test_cash_dividend_excluded_until_final_settlement():
    first = initial()
    action = CorporateAction(
        "fixture-dividend",
        TICKERS[0],
        "2026-10-28",
        "cash_dividend",
        D(2),
        "SYNTHETIC_FIXTURE",
        True,
        "2026-10-27T18:00:00+08:00",
    )
    result = run(
        first.state,
        "2026-10-28",
        [],
        corporate_actions=(action,),
        decision_cutoff="2026-10-28T08:30:00+08:00",
    )
    assert result.state.deferred_dividends == D("800000")
    assert result.daily_nav.nav == first.daily_nav.nav
    final = run(result.state, "2026-11-27", [], settle_dividends=True)
    assert final.state.deferred_dividends == 0
    assert final.daily_nav.nav == D("999660000")


def test_corporate_action_future_and_fractional_block():
    action = CorporateAction(
        "fixture-future",
        TICKERS[0],
        "2026-10-28",
        "split",
        D("1.000001"),
        "SYNTHETIC_FIXTURE",
        True,
        "2026-10-29T18:00:00+08:00",
    )
    with pytest.raises(AccountingError, match="Future"):
        run(
            initial().state,
            "2026-10-28",
            [],
            corporate_actions=(action,),
            decision_cutoff="2026-10-28T08:30:00+08:00",
        )
    action = CorporateAction(
        "fixture-fractional",
        TICKERS[0],
        "2026-10-28",
        "split",
        D("1.000001"),
        "SYNTHETIC_FIXTURE",
        True,
        "2026-10-27T18:00:00+08:00",
    )
    with pytest.raises(AccountingError, match="Fractional"):
        run(
            initial().state,
            "2026-10-28",
            [],
            corporate_actions=(action,),
            decision_cutoff="2026-10-28T08:30:00+08:00",
        )


def test_active_share_raw_and_renormalized_are_distinct():
    left = {t: D("0.04") for t in TICKERS[:20]}
    right = {t: D("0.06") for t in TICKERS[:10]}
    assert calculate_active_share(left, right, "raw") == D("0.10")
    assert calculate_active_share(left, right, "renormalized") == D(0)


def test_active_share_two_consecutive_sessions_disqualify():
    first = initial()
    second = run(
        first.state,
        "2026-10-28",
        [],
        active_shares={"FIXTURE_ETF": D("0.19")},
        required_benchmarks={"FIXTURE_ETF"},
    )
    assert second.status == "EXECUTED"
    third = run(
        second.state,
        "2026-10-29",
        [],
        active_shares={"FIXTURE_ETF": D("0.19")},
        required_benchmarks={"FIXTURE_ETF"},
    )
    assert third.status == "DISQUALIFIED"


def test_verified_benchmark_coverage_and_staleness_required():
    row = BenchmarkHoldings(
        "FIXTURE_ETF",
        {TICKERS[0]: D("0.05")},
        "2026-10-01T18:00:00+08:00",
        "2026-10-01T19:00:00+08:00",
        "https://example.test/fixture",
        True,
        True,
    )
    cutoff = datetime.fromisoformat("2026-10-28T08:30:00+08:00")
    with pytest.raises(AccountingError, match="stale"):
        BenchmarkHoldingsProvider().compare(
            {TICKERS[0]: D("0.05")}, [row], {"FIXTURE_ETF"}, cutoff, "raw", fixture=True
        )
    with pytest.raises(AccountingError, match="Missing"):
        BenchmarkHoldingsProvider().compare({}, [], {"FIXTURE_ETF"}, cutoff, "raw")


def test_strict_unconfirmed_policy_cannot_claim_ready():
    result = PortfolioSimulator().execute_day(
        PortfolioState(), "2026-10-27", [], {}, trading_days=DAYS, calendar_verified=True
    )
    assert result.status == "BLOCKED"
    assert any(v.code == "fee_rounding" for v in result.violations)


def test_non_session_and_synthetic_production_rejected():
    with pytest.raises(AccountingError, match="calendar"):
        run(PortfolioState(), "2026-10-26", [])
    with pytest.raises(AccountingError, match="Synthetic"):
        PortfolioSimulator().execute_day(
            PortfolioState(),
            "2026-10-27",
            [],
            bars("2026-10-27"),
            trading_days=DAYS,
            calendar_verified=True,
        )
