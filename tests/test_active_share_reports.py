"""MOCK benchmark/sector fixtures only; no live ETF holdings are used."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D

import pytest

from src.competition.models import PortfolioState
from src.portfolio.active_share import (
    ActiveETFLoader,
    ActiveShareCalculator,
    ActiveShareValidator,
    BenchmarkHoldings,
)
from src.portfolio.diagnostics import sector_concentration_diagnostics
from src.portfolio.optimizer import PortfolioOptimizer
from src.data.universe import UniverseLoader

CUTOFF = datetime.fromisoformat("2026-10-28T08:30:00+08:00")
DAYS = {"2026-10-27", "2026-10-28", "2026-10-29"}


def snapshots(weights=None):
    return [
        BenchmarkHoldings(
            t,
            weights or {"2454": D("0.10")},
            "2026-10-27T16:00:00+08:00",
            "2026-10-27T17:00:00+08:00",
            "https://example.test/MOCK-benchmark",
            False,
            True,
        )
        for t in sorted(ActiveETFLoader().tickers())
    ]


def calculate(rows=None, confirmed=True, cutoff=CUTOFF):
    return ActiveShareCalculator().evaluate(
        {"2330": D("0.10")},
        snapshots() if rows is None else rows,
        cutoff,
        "raw",
        method_confirmed=confirmed,
        fixture=True,
    )


def test_per_benchmark_report_covers_all_thirty_and_minimum():
    report = calculate()
    assert len(report["records"]) == 30 and report["minimum_active_share"] == D("0.10")
    assert report["status"] == "EVALUATED" and report["synthetic"]
    assert all(
        r["benchmark_as_of"] and r["available_at"] and r["source_url"]
        for r in report["records"]
    )


@pytest.mark.parametrize(
    "condition",
    [
        "missing",
        "duplicate",
        "stale",
        "future_available",
        "future_content",
        "unknown_extra",
    ],
)
def test_incomplete_snapshots_never_turn_into_empty_etf_success(condition):
    rows = snapshots()
    if condition == "missing":
        rows.pop()
    if condition == "duplicate":
        rows.append(rows[0])
    if condition == "stale":
        rows[0] = replace(rows[0], content_as_of="2026-10-01T16:00:00+08:00")
    if condition == "future_available":
        rows[0] = replace(rows[0], available_at="2026-10-29T17:00:00+08:00")
    if condition == "future_content":
        rows[0] = replace(rows[0], content_as_of="2026-10-29T16:00:00+08:00")
    if condition == "unknown_extra":
        rows.append(replace(rows[0], ticker="UNKNOWN_MOCK_ETF"))
    report = calculate(rows)
    assert report["status"] == "BLOCKED"
    result = ActiveShareValidator().validate(report, "2026-10-28", DAYS)
    assert result["status"] == "BLOCKED" and not result["streaks_advanced"]


def test_unconfirmed_method_does_not_advance_or_disqualify():
    report = calculate(confirmed=False)
    result = ActiveShareValidator().validate(
        report,
        "2026-10-28",
        DAYS,
        previous_date="2026-10-27",
        previous_streaks={snapshots()[0].ticker: 1},
    )
    assert result["status"] == "BLOCKED" and list(result["streaks"].values()) == [1]


def test_two_actual_consecutive_sessions_and_replay_guard():
    first = ActiveShareValidator().validate(calculate(), "2026-10-27", DAYS)
    day_two = [
        replace(
            r,
            content_as_of="2026-10-28T16:00:00+08:00",
            available_at="2026-10-28T17:00:00+08:00",
        )
        for r in snapshots()
    ]
    second_calculation = calculate(
        day_two, cutoff=datetime.fromisoformat("2026-10-29T08:30:00+08:00")
    )
    second = ActiveShareValidator().validate(
        second_calculation,
        "2026-10-28",
        DAYS,
        previous_date="2026-10-27",
        previous_streaks=first["streaks"],
    )
    assert first["status"] == "WARNING" and second["status"] == "DISQUALIFIED"
    with pytest.raises(ValueError, match="duplicate"):
        ActiveShareValidator().validate(
            calculate(),
            "2026-10-28",
            DAYS,
            previous_date="2026-10-28",
            previous_streaks=second["streaks"],
        )


def test_gap_in_evidence_cannot_prove_consecutive_breaches():
    result = ActiveShareValidator().validate(
        calculate(),
        "2026-10-29",
        DAYS,
        previous_date="2026-10-27",
        previous_streaks={snapshots()[0].ticker: 1},
    )
    assert result["status"] == "BLOCKED" and not result["streaks_advanced"]


def test_exact_legal_twenty_percent_only_triggers_internal_buffer():
    report = ActiveShareCalculator().evaluate(
        {"2330": D("0.20")},
        snapshots({"2454": D("0.20")}),
        CUTOFF,
        "raw",
        method_confirmed=True,
        fixture=True,
    )
    result = ActiveShareValidator().validate(report, "2026-10-27", DAYS)
    assert result["status"] == "WARNING" and all(
        v == 0 for v in result["streaks"].values()
    )


def test_forged_empty_evaluated_report_is_blocked():
    report = {
        "status": "EVALUATED",
        "method_confirmed": True,
        "data_completeness": "COMPLETE",
        "records": [],
        "synthetic": True,
    }
    assert (
        ActiveShareValidator().validate(report, "2026-10-28", DAYS)["status"]
        == "BLOCKED"
    )


def test_sector_mapping_must_be_verified_and_available_at_cutoff():
    row = {
        "sector": "MOCK-sector",
        "verified": True,
        "available_at": "2026-10-27T17:00:00+08:00",
        "observation_date": "2026-10-27",
        "source": "SYNTHETIC_FIXTURE",
    }
    report = sector_concentration_diagnostics(
        {"2330": D("0.4"), "2454": D("0.3")},
        {"2330": row, "2454": {**row, "available_at": "2026-10-29T17:00:00+08:00"}},
        CUTOFF,
    )
    assert report["sector_weights"] == {"MOCK-sector": D("0.4")}
    assert report["unclassified_weight"] == D("0.3") and report["warnings"]
    assert not report["official_constraint"]


def test_optimizer_blocks_supplied_illegal_as_instead_of_silently_ignoring():
    tickers = sorted(UniverseLoader().get_all_tickers())[:35]
    args = dict(
        rankings=[{"ticker": t, "score": float(35 - i)} for i, t in enumerate(tickers)],
        state=PortfolioState(),
        previous_nav=D("1000000000"),
        previous_closes={t: D(100) for t in tickers},
        regime="neutral",
    )
    baseline = PortfolioOptimizer().optimize(**args)
    assert (
        baseline.status == "PROPOSED"
        and baseline.diagnostics["active_share"]["status"] == "BLOCKED"
    )
    aligned = snapshots({t: D("0.044") for t in tickers[:20]})
    result = PortfolioOptimizer().optimize(
        **args,
        benchmark_snapshots=aligned,
        benchmark_cutoff=CUTOFF,
        active_share_method_confirmed=True,
        fixture=True
    )
    assert (
        result.status == "BLOCKED"
        and not result.orders
        and "Active Share" in result.diagnostics["reason"]
    )


def test_agent_tools_do_not_expose_future_or_unstructured_holdings():
    from test_investment_agent import context
    from src.agent.tools import readonly_tools

    ctx = replace(
        context(),
        benchmark_evidence={"status": "PASS", "untrusted_future_prices": [999999]},
        benchmark_snapshots=tuple(
            replace(r, available_at="2027-01-01T00:00:00+08:00") for r in snapshots()
        ),
    )
    tools = {f.__name__: f for f in readonly_tools(ctx)}
    result = tools["get_active_etf_holdings"]()
    assert result["status"] == "UNVERIFIED" and not result["snapshots"]
    assert "untrusted_future_prices" not in result


def test_previous_session_holdings_cannot_disqualify_a_new_session():
    result = ActiveShareValidator().validate(
        calculate(),
        "2026-10-28",
        DAYS,
        previous_date="2026-10-27",
        previous_streaks={snapshots()[0].ticker: 1},
    )
    assert result["status"] == "BLOCKED" and not result["streaks_advanced"]
    assert "Same-session" in result["reason"]


def test_second_session_missing_history_cannot_be_assumed_clean():
    rows = [
        replace(
            r,
            content_as_of="2026-10-28T16:00:00+08:00",
            available_at="2026-10-28T17:00:00+08:00",
        )
        for r in snapshots()
    ]
    report = calculate(rows, cutoff=datetime.fromisoformat("2026-10-29T08:30:00+08:00"))
    validator = ActiveShareValidator()
    assert validator.validate(report, "2026-10-28", DAYS)["status"] == "BLOCKED"
    known_clean = validator.validate(
        report, "2026-10-28", DAYS, previous_date="2026-10-27", previous_streaks={}
    )
    assert known_clean["status"] == "WARNING" and all(
        v == 1 for v in known_clean["streaks"].values()
    )


def test_publication_before_observation_and_string_verification_rejected():
    row = replace(snapshots()[0], available_at="2026-10-27T08:00:00+08:00")
    with pytest.raises(ValueError, match="precedes"):
        row.validate(CUTOFF, 7, True)
    row = replace(snapshots()[0], synthetic=False, verified="true")
    with pytest.raises(ValueError, match="Unverified"):
        row.validate(CUTOFF, 7, False)


@pytest.mark.parametrize(
    "changes",
    [
        {"active_share_safety_threshold": "0.19"},
        {"sector_warning_threshold": 0},
        {"benchmark_max_age_days": -1},
    ],
)
def test_optimizer_diagnostic_settings_reject_invalid_limits(changes):
    from src.portfolio.optimizer import OptimizerConfig

    with pytest.raises(ValueError, match="Invalid optimizer"):
        OptimizerConfig(**changes)


def test_code_fingerprint_git_timeout_preserves_content_hashes(monkeypatch):
    import subprocess
    from src.competition.configuration import code_fingerprint

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 3
        raise subprocess.TimeoutExpired(args[0], 3)

    monkeypatch.setattr(subprocess, "check_output", timeout)
    result = code_fingerprint()
    assert result["git_commit"] == "unknown"
    assert "src/portfolio/active_share.py" in result["file_hashes"]


def test_numeric_as_dictionary_cannot_certify_production_even_with_flags():
    from src.portfolio.compliance import ComplianceEngine
    from src.competition.policy import CompetitionPolicy
    from src.competition.simulator import mark_to_market
    from test_simulator import initial, bars

    state = initial().state
    nav = mark_to_market(state, bars("2026-10-28"), "2026-10-28")
    required = ActiveETFLoader().tickers()
    report = ComplianceEngine(
        CompetitionPolicy(active_share_method_confirmed=True)
    ).post_trade(state, nav, set(), {t: D("0.5") for t in required}, required)
    assert not report.verified and not report.active_share_streaks
    assert any(v.code == "active_share_evidence" for v in report.violations)


def test_rollback_as_counters_follow_retained_holdings_not_rejected_targets():
    from src.competition.policy import CompetitionPolicy
    from src.competition.simulator import PortfolioSimulator, mark_to_market
    from src.competition.models import TradeOrder
    from test_simulator import initial, bars, DAYS as FIXTURE_DAYS

    state = initial().state
    state.active_share_streaks = {t: 0 for t in ActiveETFLoader().tickers()}
    state.active_share_last_date = "2026-10-27"
    nav = mark_to_market(state, bars("2026-10-28"), "2026-10-28")
    top = dict(sorted(nav.weights.items(), key=lambda r: (-r[1], r[0]))[:10])
    sold = next(iter(top))
    top[sold] += D("0.38")  # MOCK: retained AS=.19; sold candidate AS>.20.
    benchmark_rows = [
        replace(
            r,
            weights=top,
            content_as_of="2026-10-28T16:00:00+08:00",
            available_at="2026-10-28T17:00:00+08:00",
        )
        for r in snapshots()
    ]
    result = PortfolioSimulator(
        CompetitionPolicy(active_share_method_confirmed=True)
    ).execute_day(
        state,
        "2026-10-28",
        [TradeOrder(sold, "SELL", state.positions[sold].shares)],
        bars("2026-10-28"),
        trading_days=FIXTURE_DAYS,
        calendar_verified=False,
        strict=False,
        fixture_mode=True,
        benchmark_snapshots=benchmark_rows,
        benchmark_cutoff=datetime.fromisoformat("2026-10-29T08:30:00+08:00"),
    )
    assert (
        result.status == "REJECTED"
        and len(result.state.positions) == 20
        and not result.executions
    )
    assert all(v == 1 for v in result.state.active_share_streaks.values())
    assert all(v == 0 for v in state.active_share_streaks.values())
    assert result.state.active_share_last_date == "2026-10-28"


def test_settled_trade_date_does_not_prove_previous_as_evidence():
    from src.portfolio.compliance import ComplianceEngine
    from src.competition.policy import CompetitionPolicy
    from src.competition.simulator import mark_to_market
    from test_simulator import initial, bars, DAYS as FIXTURE_DAYS

    state = initial().state
    state.last_trade_date = "2026-10-28"  # MOCK: ledger advanced without AS evidence.
    state.active_share_last_date = "2026-10-27"
    state.active_share_streaks = {t: 1 for t in ActiveETFLoader().tickers()}
    rows = [
        replace(
            r,
            content_as_of="2026-10-29T16:00:00+08:00",
            available_at="2026-10-29T17:00:00+08:00",
        )
        for r in snapshots()
    ]
    nav = mark_to_market(state, bars("2026-10-29"), "2026-10-29")
    report = ComplianceEngine(
        CompetitionPolicy(active_share_method_confirmed=True)
    ).post_trade(
        state,
        nav,
        set(),
        None,
        ActiveETFLoader().tickers(),
        strict=False,
        fixture_mode=True,
        benchmark_snapshots=rows,
        benchmark_cutoff=datetime.fromisoformat("2026-10-30T08:30:00+08:00"),
        trading_days=FIXTURE_DAYS,
    )
    assert not report.verified and not report.failed
    assert all(v == 1 for v in report.active_share_streaks.values())
    assert report.active_share_date == "2026-10-27"


def test_as_evidence_date_is_serialized_and_old_ledgers_remain_loadable():
    from src.competition.serialization import json_value, load_state

    state = PortfolioState(
        last_trade_date="2026-10-28", active_share_last_date="2026-10-27"
    )
    assert load_state(json_value(state)).active_share_last_date == "2026-10-27"
    assert (
        load_state({"cash": "1000000000", "positions": {}}).active_share_last_date
        is None
    )


@pytest.mark.parametrize("minimum", ["0.19", "0.21"])
def test_official_as_minimum_cannot_be_reconfigured(minimum):
    from src.competition.policy import CompetitionPolicy

    with pytest.raises(ValueError):
        CompetitionPolicy(active_share_min=D(minimum))
