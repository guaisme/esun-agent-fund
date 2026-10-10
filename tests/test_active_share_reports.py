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
