"""Offline unit fixtures: no financial data or model call is real in this module."""

import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src.agent.investment import create_investment_agent
from src.competition.models import PortfolioState, Position
from src.competition.reconciliation import OfficialSettlement, SettlementReconciler
from src.dplan.strategy_document import StrategyDeclaration, freeze_declaration
from src.portfolio.active_share import ActiveETFLoader, BenchmarkHoldingsProvider
from src.strategy.evaluation import evaluate_walk_forward
from test_investment_agent import context, proposal


def test_submit_tool_rejects_then_accepts_actual_typed_fixture():
    holder = {}
    agent = create_investment_agent(context(), "MOCK-GEMINI", holder)
    submit = next(tool for tool in agent.tools if tool.__name__ == "submit_investment_decision")
    assert submit("{}")["status"] == "INVALID" and not holder
    assert submit(json.dumps(proposal()))["status"] == "VALID"
    assert holder["decision"].model_version == "MOCK-GEMINI"


def test_all_official_etfs_required_in_production():
    from datetime import datetime

    assert len(ActiveETFLoader().tickers()) == 30
    with pytest.raises(ValueError, match="all 30"):
        BenchmarkHoldingsProvider().compare(
            {}, [], set(), datetime.fromisoformat("2026-10-27T08:30:00+08:00"), "raw"
        )


def test_settlement_export_never_silently_repairs_ledger(tmp_path):
    original = tmp_path / "MOCK-organizer-export.txt"
    original.write_text("SYNTHETIC organizer export fixture", encoding="utf-8")
    state = PortfolioState(
        cash=D(100), positions={"2330": Position("2330", 1000)}, last_trade_date="2026-10-27"
    )
    row = OfficialSettlement(
        "TEAM_11586",
        "2026-10-27",
        D(100),
        {"2330": 1000},
        D(1100),
        original,
        hashlib.sha256(original.read_bytes()).hexdigest(),
        "2026-10-27T16:00:00+08:00",
        False,
    )
    assert (
        SettlementReconciler().reconcile(state, D(1100), row, "TEAM_11586")["status"] == "BLOCKED"
    )
    assert state.cash == 100 and state.positions["2330"].shares == 1000


def declaration():
    return StrategyDeclaration(
        etf_name="主動測試",
        theme="明確標示的離線測試",
        philosophy="本段僅為合成測試資料，不代表真實模型決策或金融證據。" * 5,
        strategy_version="fixture",
        model_version="MOCK",
        agent_generated=True,
    )


def test_declaration_freeze_requires_matching_actual_trace(tmp_path):
    trace = tmp_path / "trace.json"
    trace.write_text(json.dumps({"status": "FAILED"}), encoding="utf-8")
    with pytest.raises(ValueError, match="actual validated"):
        freeze_declaration(declaration(), tmp_path / "frozen.json", trace, visual_qa_passed=True)
    assert not (tmp_path / "frozen.json").exists()


@pytest.mark.parametrize("name", ["測試ETF", "主動123", "主動測試-"])
def test_declaration_invalid_names(name):
    with pytest.raises(ValueError):
        declaration().model_copy(update={"etf_name": name}).model_validate(
            {**declaration().model_dump(), "etf_name": name}
        )


def test_walk_forward_executes_training_validation_test_without_tuning():
    from datetime import date, timedelta

    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(88)]
    calls = []

    def fake(start, end):
        calls.append((start, end))
        return {"status": "SYNTHETIC_FIXTURE", "metrics": None}

    result = evaluate_walk_forward({"prices": [{"trade_date": d} for d in dates]}, runner=fake)
    assert len(calls) == 3 and len(result["folds"]) == 1
    assert calls[0][1] < calls[1][0] <= calls[1][1] < calls[2][0]
    assert result["rolling_24_session_evaluations"][0]["status"] == "SYNTHETIC_FIXTURE"


def test_official_strategy_original_bytes_match_manifest():
    manifest = json.loads(Path("docs/official/manifest.json").read_text(encoding="utf-8"))
    for row in manifest:
        file = Path("docs/official") / row["filename"]
        if file.exists():
            assert hashlib.sha256(file.read_bytes()).hexdigest() == row["sha256"]


def test_research_status_future_event_is_filtered_without_using_future_finance():
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from research_agent.agent import record_tool, TAIPEI

    future = (datetime.now(TAIPEI) + timedelta(days=30)).date().isoformat()
    payload = {
        "sources": [{"source": "prices", "last_period": "2025-03-07"}],
        "others": {"conference_latest": future},
    }
    ctx = SimpleNamespace(state={})
    response = record_tool(SimpleNamespace(name="data_status"), {}, ctx, payload)
    assert len(response["sources"]) == 1 and "others" not in response
    assert not ctx.state.get("temp:research_data_ok")
    response = record_tool(
        SimpleNamespace(name="monthly_revenue"),
        {},
        ctx,
        {"as_of": future, "rows": [{"value": 100}]},
    )
    assert response["isError"] and not ctx.state.get("temp:research_data_ok")


def test_complete_mock_decision_pipeline_carries_state(tmp_path):
    import asyncio
    from src.pipeline.offline_decision import complete_offline_dry_run

    result = asyncio.run(complete_offline_dry_run(tmp_path))
    assert result["status"] == "SYNTHETIC_PIPELINE_PASSED"
    assert result["synthetic"] and not result["live_model_called"]
    assert len(result["history"]) == 4
    assert all(r["holdings"] == 20 and r["features"] == 30 for r in result["history"])
    assert (tmp_path / "2026-10-30" / "portfolio.json").exists()
    assert result["formal_readiness"] == "BLOCKED"
