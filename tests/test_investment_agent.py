"""Unit and MOCK Gemini tests: not live model/financial evidence."""

import asyncio
import json
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src.agent.decision import InvestmentDecision, parse_decision
from src.agent.investment import create_investment_agent, run_investment_decision
from src.agent.tools import DecisionContext, readonly_tools
from src.competition.models import PortfolioState
from src.data.universe import UniverseLoader
from src.strategy.features import FeatureSnapshot

TICKERS = sorted(UniverseLoader().get_all_tickers())[:20]


def context():
    cutoff = "2026-10-27T08:30:00+08:00"
    snapshot = FeatureSnapshot(
        cutoff,
        "synthetic_fixture",
        "fixture",
        {t: {"momentum_20d": 0.1} for t in TICKERS},
        {},
        (),
        (),
    )
    return DecisionContext(
        "2026-10-27",
        cutoff,
        PortfolioState(),
        D("1000000000"),
        {t: D(100) for t in TICKERS},
        snapshot,
        {"rankings": [{"ticker": t, "score": 1.0} for t in TICKERS], "data_cutoff": cutoff},
        [{"source_id": "S1"}],
        [{"obs_id": "O1"}],
        {"status": "SYNTHETIC"},
        {"status": "UNVERIFIED"},
        "fixture-v1",
        True,
    )


def proposal():
    return dict(
        trade_date="2026-10-27",
        data_cutoff="2026-10-27T08:30:00+08:00",
        regime="neutral",
        stance="neutral",
        investment_posture="increase",
        candidates=TICKERS,
        target_weights={t: "0.04" for t in TICKERS},
        rationale={
            t: {
                "premise_refs": ["O1"],
                "logic": "合成測試中根據指定證據與風險預算建立此股票部位，以驗證完整自主決策輸出。",
                "counter_evidence": None,
            }
            for t in TICKERS
        },
        observation_refs=["O1"],
        market_logic="合成測試資料顯示固定的正向動能，按中性預算建立部位，並保持現金風險緩衝。",
        counter_evidence=None,
        cash_range=["0.18", "0.22"],
        additional_research_needed=["官方ETF持股尚未取得"],
        model_version="MOCK-GEMINI",
        strategy_version="fixture-v1",
    )


def test_import_agent_and_readonly_tool_boundary():
    agent = create_investment_agent(context(), "MOCK-GEMINI")
    assert agent.name == "esun_investment_agent"
    functions = {t.__name__: t for t in readonly_tools(context())}
    assert len(functions) == 12
    first = functions["get_portfolio"]()
    first["state"]["cash"] = "0"
    assert functions["get_portfolio"]()["state"]["cash"] == "1000000000"
    assert "execution" not in functions["get_market_snapshot"]()
    assert functions["get_features"]("0000")["status"] == "UNAVAILABLE"


def test_missing_key_skips_without_any_model_call(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    decision, trace = asyncio.run(run_investment_decision(context()))
    assert decision is None and trace["status"] == "SKIPPED"
    assert trace["tool_calls"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(orders=[{"ticker": "2330", "shares": 1000}]),
        lambda p: p["target_weights"].update({TICKERS[0]: "0.11"}),
        lambda p: p.update(candidates=TICKERS[:-1] + ["0000"]),
        lambda p: p.update(cash_range=["0.25", "0.10"]),
        lambda p: p["target_weights"].update({TICKERS[0]: "NaN"}),
        lambda p: p["rationale"].pop(TICKERS[0]),
    ],
)
def test_invalid_decision_never_generates_fallback(mutate):
    p = proposal()
    mutate(p)
    with pytest.raises(ValueError):
        parse_decision(json.dumps(p, ensure_ascii=False))


def test_tool_budget_is_finite():
    agent = create_investment_agent(context(), "MOCK-GEMINI")
    tool_context = SimpleNamespace(state={"investment_tool_calls": 20})
    with pytest.raises(RuntimeError, match="limit"):
        agent.before_tool_callback(None, {}, tool_context)


@pytest.mark.parametrize(
    "regime,intent", [("neutral", "increase"), ("risk_off", "reduce"), ("risk_on", "hold")]
)
def test_schema_accepts_explicit_regime_and_posture(regime, intent):
    p = proposal()
    p.update(regime=regime, investment_posture=intent)
    decision = InvestmentDecision.model_validate(p)
    assert decision.regime == regime and decision.investment_posture == intent
