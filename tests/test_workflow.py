"""MOCK Agent + synthetic D-Plan dry run; no live success is implied."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D

from src.agent.decision import InvestmentDecision
from src.agent.tools import DecisionContext
from src.competition.configuration import load_settings
from src.competition.models import PortfolioState
from src.data.universe import UniverseLoader
from src.pipeline.workflow import competition_dry_run
from src.strategy.features import FeatureSnapshot


def fixture():
    tickers = sorted(UniverseLoader().get_all_tickers())[:20]
    cutoff = "2026-10-27T08:30:00+08:00"
    source = {
        "source_id": "S1",
        "authority": "other",
        "url": "https://example.test/SYNTHETIC",
        "content_as_of": "2026-10-23T13:30:00+08:00",
    }
    obs = {
        "obs_id": "O1",
        "source_ref": ["S1"],
        "statement": "合成測試股票價格均為一百元，不是真實市場資料。",
        "values": {"fixture_close": 100},
    }
    snapshot = FeatureSnapshot(
        cutoff, "synthetic_fixture", "fixture", {t: {} for t in tickers}, {}, (), ()
    )
    context = DecisionContext(
        "2026-10-27",
        cutoff,
        PortfolioState(),
        D("1000000000"),
        {t: D(100) for t in tickers},
        snapshot,
        {"data_cutoff": cutoff, "rankings": [{"ticker": t, "score": 1.0} for t in tickers]},
        [source],
        [obs],
        {"status": "SYNTHETIC"},
        {"status": "UNVERIFIED"},
        "fixture-v1",
        True,
    )
    decision = InvestmentDecision.model_validate(
        dict(
            trade_date=context.trade_date,
            data_cutoff=cutoff,
            regime="neutral",
            stance="neutral",
            investment_posture="increase",
            candidates=tickers,
            target_weights={t: "0.04" for t in tickers},
            rationale={
                t: {
                    "premise_refs": ["O1"],
                    "logic": "依合成測試證據及指定預算建立此股票部位，專門驗證乾跑的決策及機械委託推導流程。",
                    "counter_evidence": None,
                }
                for t in tickers
            },
            observation_refs=["O1"],
            market_logic="合成情境採用固定風險預算建立二十檔部位，僅驗證流程而不主張任何市場或策略成效。",
            counter_evidence=None,
            cash_range=["0.18", "0.22"],
            additional_research_needed=["官方ETF資料待確認"],
            model_version="MOCK-GEMINI",
            strategy_version="fixture-v1",
        )
    )
    return context, decision


def trace():
    return {
        "status": "DECISION_PARSED",
        "run_started_at": "2026-10-27T08:00:00+08:00",
        "run_completed_at": "2026-10-27T08:10:00+08:00",
        "tool_calls": [{"name": "MOCK_TOOL"}],
        "synthetic": True,
    }


def test_mock_dryrun_creates_exact_draft_but_never_ready(tmp_path):
    context, decision = fixture()

    async def fake_runner(context, feedback=None):
        return decision, trace()

    result = asyncio.run(
        competition_dry_run(context, load_settings(), tmp_path, decision_runner=fake_runner)
    )
    assert result["status"] == "BLOCKED"
    assert result["dplan_generated"]
    assert "draft" in result["plan_path"]
    assert not result["gates"]["live_model"] and not result["gates"]["strict_data_mode"]
    assert result["validation"].schema_valid
    assert not any(i.severity == "HARD_FAIL" for i in result["validation"].issues)


def test_bounded_feedback_never_silently_changes_agent_targets(tmp_path):
    context, decision = fixture()
    decision = decision.model_copy(
        update={"target_weights": {t: D("0.047") for t in decision.target_weights}}
    )
    calls = []

    async def fake_runner(context, feedback=None):
        calls.append(feedback)
        return decision, trace()

    result = asyncio.run(
        competition_dry_run(context, load_settings(), tmp_path, decision_runner=fake_runner)
    )
    assert result["status"] == "FAILED" and not result["dplan_generated"]
    assert len(calls) == 3 and calls[1] and calls[2]


def test_service_failure_no_fabricated_fallback(tmp_path):
    context, _ = fixture()

    async def fake_runner(context, feedback=None):
        return None, {**trace(), "status": "FAILED", "error_type": "MOCK_HTTP_503"}

    result = asyncio.run(
        competition_dry_run(context, load_settings(), tmp_path, decision_runner=fake_runner)
    )
    assert result["status"] == "FAILED" and not result["dplan_generated"]
    assert len(result["attempts"]) == 1
