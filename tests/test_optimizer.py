"""SYNTHETIC optimizer fixtures only."""

from decimal import Decimal as D

from src.competition.models import PortfolioState, Position
from src.data.universe import UniverseLoader
from src.portfolio.optimizer import OptimizerConfig, PortfolioOptimizer

TICKERS = sorted(UniverseLoader().get_all_tickers())[:35]
CLOSES = {t: D(100) for t in TICKERS}
RANKINGS = [{"ticker": t, "score": 1.0 - i / 100} for i, t in enumerate(TICKERS)]


def test_initial_candidate_selection_and_riskoff_cash():
    result = PortfolioOptimizer().optimize(
        rankings=RANKINGS,
        state=PortfolioState(),
        previous_nav=D("1000000000"),
        previous_closes=CLOSES,
        regime="risk_off",
    )
    assert result.status == "PROPOSED"
    assert result.diagnostics["holdings"] == 20
    assert result.diagnostics["cash"] == D("198860000")
    assert result.diagnostics["stress_cash"] == D("158803000")
    assert result.diagnostics["gross_turnover"] == D("0.8")
    assert all(weight < D("0.1") for weight in result.diagnostics["estimated_weights"].values())


def test_20_22_25_30_holdings_initial_feasible():
    for count in (20, 22, 25, 30):
        result = PortfolioOptimizer(OptimizerConfig(holdings=count)).optimize(
            rankings=RANKINGS,
            state=PortfolioState(),
            previous_nav=D("1000000000"),
            previous_closes=CLOSES,
            regime="neutral",
        )
        assert result.status == "PROPOSED", result.diagnostics
        assert result.diagnostics["holdings"] == count


def test_no_trade_band_keeps_all_existing_shares():
    state = PortfolioState(
        cash=D("120000000"),
        positions={t: Position(t, 440000) for t in TICKERS[:20]},
        initialized=True,
    )
    result = PortfolioOptimizer().optimize(
        rankings=RANKINGS,
        state=state,
        previous_nav=D("1000000000"),
        previous_closes=CLOSES,
        regime="neutral",
    )
    assert result.status == "PROPOSED" and not result.orders
    assert len(result.modifications) == 20
    assert result.diagnostics["agent_approval_required"]


def test_initial_cash_hold_not_legal_fallback_and_missing_candidates_block():
    result = PortfolioOptimizer().optimize(
        rankings=RANKINGS[:19],
        state=PortfolioState(),
        previous_nav=D("1000000000"),
        previous_closes=CLOSES,
        regime="risk_off",
    )
    assert result.status == "BLOCKED" and not result.orders


def test_agent_unsupported_or_overweight_targets_block_without_silent_fix():
    targets = {t: D("0.04") for t in TICKERS[:20]}
    targets[TICKERS[0]] = D("0.11")
    result = PortfolioOptimizer().optimize(
        rankings=RANKINGS,
        state=PortfolioState(),
        previous_nav=D("1000000000"),
        previous_closes=CLOSES,
        regime="neutral",
        proposed_targets=targets,
    )
    assert result.status == "BLOCKED" and not result.modifications


def test_turnover_cap_blocks_mass_replacement():
    state = PortfolioState(
        cash=D("120000000"),
        positions={t: Position(t, 440000) for t in TICKERS[:20]},
        initialized=True,
    )
    targets = {t: D(0) for t in TICKERS[:20]}
    targets.update({t: D("0.044") for t in TICKERS[15:35]})
    result = PortfolioOptimizer().optimize(
        rankings=RANKINGS,
        state=state,
        previous_nav=D("1000000000"),
        previous_closes=CLOSES,
        regime="neutral",
        proposed_targets=targets,
    )
    assert result.status == "BLOCKED" and "Turnover" in result.diagnostics["reason"]


def test_cost_hysteresis_prevents_small_alpha_replacement():
    state = PortfolioState(
        cash=D("120000000"),
        positions={t: Position(t, 440000) for t in TICKERS[1:21]},
        initialized=True,
    )
    result = PortfolioOptimizer().optimize(
        rankings=RANKINGS,
        state=state,
        previous_nav=D("1000000000"),
        previous_closes=CLOSES,
        regime="neutral",
    )
    assert result.status == "PROPOSED" and not result.orders
    assert any(m["type"] == "cost_hysteresis" for m in result.modifications)
