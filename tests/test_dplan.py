"""Official immutable examples + explicitly synthetic evidence-chain fixtures."""

import json
from copy import deepcopy
from decimal import Decimal as D

import pytest

from src.competition.models import PortfolioState, Position
from src.data.universe import ROOT, UniverseLoader
from src.dplan.generator import DPlanGenerator, exact_json, write_dplan
from src.dplan.validation import DPlanValidator

TICKERS = sorted(UniverseLoader().get_all_tickers())[:20]


def fixture_plan(state=None, targets=None, nav=D("1000000000")):
    state = state or PortfolioState()
    targets = targets or {t: D("0.04") for t in TICKERS}
    return DPlanGenerator().generate(
        team_id="FIXTURE_ONLY",
        trade_date="2026-10-27",
        state=state,
        previous_nav=nav,
        previous_closes={t: D(100) for t in TICKERS},
        targets=targets,
        sources=[
            {
                "source_id": "S1",
                "authority": "other",
                "url": "https://example.test/SYNTHETIC_FIXTURE",
                "content_as_of": "2026-10-23T13:30:00+08:00",
            }
        ],
        observations=[
            {
                "obs_id": "O1",
                "source_ref": ["S1"],
                "statement": "合成測試資料：所有股票前一交易日收盤價為一百元。",
                "values": {"fixture_previous_close": 100},
            }
        ],
        market_view={
            "basis_refs": ["O1"],
            "logic": "合成測試情境使用固定預算建立二十檔組合，以驗證帳務而非主張市場走勢。",
            "regime": "neutral",
            "stance": "neutral",
            "posture": {"net_exposure_intent": "increase", "target_cash_pct_range": [0.18, 0.22]},
            "counter_evidence": None,
        },
        rationale={
            t: {
                "premise_refs": ["O1"],
                "logic": "依據合成收盤價格及固定測試預算配置此股票，用於檢驗官方股數推導與證據鏈。",
                "counter_evidence": None,
            }
            for t in targets
        },
        metadata={
            "model_provider": "other",
            "model_version": "SYNTHETIC_FIXTURE",
            "run_started_at": "2026-10-27T08:00:00+08:00",
            "run_completed_at": "2026-10-27T08:10:00+08:00",
            "code_version": "fixture-v1",
        },
    )


def validate(plan, state=None, nav=D("1000000000"), **kwargs):
    return DPlanValidator().validate(
        plan,
        state=state or PortfolioState(),
        previous_nav=nav,
        previous_closes={t: D(100) for t in TICKERS},
        cutoff="2026-10-27T08:30:00+08:00",
        strict=False,
        **kwargs
    )


def test_full_initial_fixture_schema_and_semantics():
    plan = fixture_plan()
    report = validate(plan)
    assert report.status == "VALID", report.issues
    assert report.estimated_cash == D("198860000")
    assert len(plan["orders"]) == 20
    assert [d["action"] for d in plan["decisions"]] == ["BUY"] * 20


def test_official_positive_schema_is_preserved():
    plan = json.loads(
        (ROOT / "docs/official/D-Plan_TEAM_042_2026-10-27.json").read_text(encoding="utf-8")
    )
    report = DPlanValidator().validate(plan, strict=False)
    assert report.schema_valid
    assert report.status == "BLOCKED"  # no actual settled ledger; not competition-validated
    assert "合理推估非真實行情" in plan["_NOTE"]


def test_official_negative_is_detected_without_editing_example():
    plan = json.loads(
        (ROOT / "docs/official/D-Plan_TEAM_043_2026-10-27.json").read_text(encoding="utf-8")
    )
    report = DPlanValidator().validate(plan, strict=False)
    assert report.status == "INVALID"


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda p: p["sources"][0].update(source_id="S01"), "C1_ID"),
        (lambda p: p["observations"][0].update(source_ref=["S99"]), "C1_REF"),
        (lambda p: p["inferences"][0].update(premise_refs=["O99"]), "C1_REF"),
        (lambda p: p["orders"][0].update(decision_ref="D99"), "C1_ORDER"),
        (lambda p: p["orders"][0].update(shares=401000), "C2_ORDERS"),
        (lambda p: p["decisions"][0].update(action="TRIM"), "C2_ACTION"),
        (lambda p: p["market_view"]["posture"].update(net_exposure_intent="hold"), "C12_POSTURE"),
        (
            lambda p: p["market_view"]["posture"].update(target_cash_pct_range=[0.24, 0.20]),
            "C12_RANGE",
        ),
        (lambda p: p["decisions"][0].update(target_weight=0.11), "C9_WEIGHT"),
        (lambda p: p["decisions"][0].update(ticker="0000"), "C9_UNIVERSE"),
        (lambda p: p["sources"][0].update(content_as_of="2026-10-27T13:30:00+08:00"), "PIT_SOURCE"),
        (
            lambda p: p["agent_metadata"].update(run_started_at="2026-10-27T09:30:00+08:00"),
            "C6_RUN",
        ),
        (lambda p: p["agent_metadata"].update(run_completed_at="2026-10-27T08:10:00Z"), "SCHEMA"),
        (lambda p: p["decisions"][0].update(funding_for=["D2"]), "C14_REF"),
    ],
)
def test_extreme_invalid_dplans(mutation, code):
    plan = fixture_plan()
    mutation(plan)
    report = validate(plan)
    assert report.status == "INVALID"
    assert code in {issue.code for issue in report.issues}


def test_no_trade_all_holdings_covered_and_no_fabricated_orders():
    state = PortfolioState(cash=D("200000000"), positions={t: Position(t, 400000) for t in TICKERS})
    plan = fixture_plan(state)
    plan["market_view"]["posture"]["net_exposure_intent"] = "hold"
    assert not plan["orders"] and not plan["decisions"]
    assert len(plan["no_trade_decisions"]) == 20
    assert validate(plan, state).status == "VALID"
    del plan["no_trade_decisions"][0]
    assert "COVERAGE" in {i.code for i in validate(plan, state).issues}


def test_filename_authentication_calendar_and_readiness_blocks():
    plan = fixture_plan()
    report = DPlanValidator().validate(
        plan,
        state=PortfolioState(),
        previous_nav=D("1000000000"),
        previous_closes={t: D(100) for t in TICKERS},
        cutoff="2026-10-27T08:30:00+08:00",
        authenticated_team="TEAM_11586",
        filename="wrong.json",
        trading_days={"2026-10-28"},
    )
    assert {i.code for i in report.issues} >= {
        "TEAM",
        "FILENAME",
        "CALENDAR",
        "INVENTORY",
        "EVIDENCE",
    }
    assert report.status == "INVALID"


def test_exact_json_decimal_orders_survive_roundtrip(tmp_path):
    plan = fixture_plan()
    plan["decisions"][0]["target_weight"] = D("0.040000000000000000000001")
    path = write_dplan(tmp_path, plan)
    assert path.name == "D-Plan_FIXTURE_ONLY_2026-10-27.json"
    restored = json.loads(path.read_text(encoding="utf-8"), parse_float=D)
    assert restored["decisions"][0]["target_weight"] == plan["decisions"][0]["target_weight"]
    assert validate(restored).status == "VALID"


def test_funding_for_false_shortfall_rejected():
    state = PortfolioState(cash=D("200000000"), positions={t: Position(t, 400000) for t in TICKERS})
    targets = {t: D("0.04") for t in TICKERS}
    targets[TICKERS[0]], targets[TICKERS[1]] = D("0.039"), D("0.041")
    plan = fixture_plan(state, targets)
    plan["market_view"]["posture"]["net_exposure_intent"] = "hold"
    plan["decisions"][0]["funding_for"] = [plan["decisions"][1]["decision_id"]]
    report = validate(plan, state)
    assert "C14_SHORTFALL" in {i.code for i in report.issues}
