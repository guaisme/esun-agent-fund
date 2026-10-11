"""MOCK operations tests. No real API key, schedule registration or finance data."""

import asyncio
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from src.competition.calendar import TradingCalendar
from src.competition.serialization import write_json
from src.data.session_quality import session_quality
from src.data.time_safety import TAIPEI
from src.data.universe import UniverseLoader, ROOT
from src.pipeline.daily import daily_run, operations_config, window_check, read_calendar
from src.pipeline.run_store import RunStore, exclusive_lock, sha256, compare_runs
from src.data.providers.benchmark import ReviewedFileBenchmarkProvider
from src.portfolio.active_share import ActiveETFLoader


def test_os_lock_released_and_duplicate_blocked(tmp_path):
    with exclusive_lock(tmp_path / "lock"):
        with pytest.raises(RuntimeError, match="DuplicateExecution"):
            with exclusive_lock(tmp_path / "lock"):
                pass
    with exclusive_lock(tmp_path / "lock"):
        pass


def test_completed_run_never_overwritten_and_explicit_rerun_comparable(tmp_path):
    store = RunStore(tmp_path)
    first, report = store.prepare(
        "2026-10-27", rerun=False, resume=None, inputs={"MOCK": 1}
    )
    report["execution_status"] = "COMPLETED"
    store.save(first, report)
    original = (first / "status.json").read_bytes()
    with pytest.raises(ValueError, match="ExistingRun"):
        store.prepare("2026-10-27", rerun=False, resume=None, inputs={"MOCK": 1})
    with pytest.raises(ValueError, match="CompletedRunImmutable"):
        store.prepare(
            "2026-10-27", rerun=False, resume=report["run_id"], inputs={"MOCK": 1}
        )
    second, _ = store.prepare("2026-10-27", rerun=True, resume=None, inputs={"MOCK": 1})
    assert first != second and (first / "status.json").read_bytes() == original
    assert compare_runs(first, second)["same_inputs"]


def test_restart_recovers_idempotent_stage_but_rejects_changed_artifact(tmp_path):
    store = RunStore(tmp_path)
    path, report = store.prepare(
        "2026-10-27", rerun=False, resume=None, inputs={"MOCK": 1}
    )
    write_json(path / "data.json", {"synthetic": True})
    report["artifact_hashes"]["data.json"] = sha256(path / "data.json")
    report["active_stage"] = "data"
    store.save(path, report)
    _, recovered = store.prepare(
        "2026-10-27", rerun=False, resume=report["run_id"], inputs={"MOCK": 1}
    )
    assert recovered["recoveries"] == 1
    write_json(path / "data.json", {"changed": True})
    with pytest.raises(ValueError, match="ResumeArtifactChanged"):
        store.prepare(
            "2026-10-27", rerun=False, resume=report["run_id"], inputs={"MOCK": 1}
        )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("inputs", "ResumeInputsChanged"),
        ("decision", "InterruptedDecision"),
        ("path", "InvalidRunId"),
    ],
)
def test_resume_never_silently_reissues_agent(tmp_path, change, reason):
    store = RunStore(tmp_path)
    path, report = store.prepare(
        "2026-10-27", rerun=False, resume=None, inputs={"MOCK": 1}
    )
    if change == "decision":
        report["active_stage"] = "decision"
        store.save(path, report)
    with pytest.raises(ValueError, match=reason):
        store.prepare(
            "2026-10-27",
            rerun=False,
            resume="../escape" if change == "path" else report["run_id"],
            inputs={"MOCK": 2 if change == "inputs" else 1},
        )


@pytest.mark.parametrize(
    "clock,expected",
    [
        ("06:59", "BeforeSubmissionWindow"),
        ("07:00", "IN_WINDOW"),
        ("08:29", "IN_WINDOW"),
        ("08:30", "SubmissionDeadlinePassed"),
    ],
)
def test_submission_window_boundaries(clock, expected):
    config = {
        "submission_open": "07:00",
        "submission_close": "08:30",
        "submission_window_source": "MOCK rule",
    }
    result = window_check(
        datetime.fromisoformat(f"2026-10-27T{clock}:00+08:00"), "2026-10-27", config
    )
    assert result.get("reason", result["status"]) == expected


def test_unknown_window_and_wrong_date_never_ready():
    now = datetime(2026, 10, 27, 7, tzinfo=TAIPEI)
    assert (
        window_check(now, "2026-10-27", operations_config())["reason"]
        == "OfficialSubmissionWindowUnknown"
    )
    assert (
        window_check(now, "2026-10-28", operations_config())["reason"]
        == "RunDateDiffersFromToday"
    )


def test_calendar_gap_report_distinguishes_verified_close_and_missing_stock():
    calendar = TradingCalendar(
        2026, frozenset({"2026-10-26"}), frozenset(), "MOCK", "MOCK", "MOCK"
    )
    row = {
        "ticker": UniverseLoader().get_twse_tickers()[0],
        "market": "TWSE",
        "trade_date": "2026-10-27",
    }
    updates = [
        dict(market="TWSE", date="2026-10-26", status="empty_unverified"),
        dict(market="TWSE", date="2026-10-27", status="partial"),
        dict(market="TPEx", date="2026-10-26", status="empty_unverified"),
    ]
    result = session_quality([row], updates, {"TWSE": calendar})
    assert [r["classification"] for r in result["sessions"]] == [
        "VERIFIED_CLOSED",
        "MISSING_OBSERVATIONS",
        "CALENDAR_UNVERIFIED",
    ]
    assert len(result["sessions"][1]["missing_tickers"]) == 99
    assert result["historical_publication_verified"] is False


def test_observation_on_closed_day_is_error_not_holiday():
    calendar = TradingCalendar(2026, frozenset(), frozenset(), "MOCK", "MOCK", "MOCK")
    result = session_quality(
        [dict(ticker="2330", market="TWSE", trade_date="2026-10-31")],
        [dict(market="TWSE", date="2026-10-31", status="partial")],
        {"TWSE": calendar},
    )
    assert result["sessions"][0]["classification"] == "CALENDAR_CONFLICT"


def test_benchmark_missing_all_30_explicit_fields_and_dates(tmp_path):
    rows, report = ReviewedFileBenchmarkProvider(tmp_path).load(
        "2026-10-27T07:00:00+08:00"
    )
    assert rows == [] and report["status"] == "BLOCKED"
    assert {
        r["etf"] for r in report["missing_or_invalid"]
    } == ActiveETFLoader().tickers()
    assert all(
        r["required_as_of"] and "weights" in r["fields"]
        for r in report["missing_or_invalid"]
    )


@pytest.mark.parametrize(
    "fault", ["hash", "future", "historical", "duplicate", "unit", "review"]
)
def test_benchmark_rejects_unverifiable_import(tmp_path, fault):
    etf = sorted(ActiveETFLoader().tickers())[0]
    original = tmp_path / "original.txt"
    original.write_text("MOCK export", encoding="utf-8")
    digest = sha256(original)
    value = dict(
        ticker=etf,
        original_file="original.txt",
        original_sha256=digest,
        reviewed_original_sha256=digest,
        source_url="https://www.twse.com.tw/MOCK-test-only",
        reviewed_source_domain="www.twse.com.tw",
        weight_unit="fraction",
        complete_equity_holdings=True,
        holdings=[dict(ticker="2330", weight="0.1")],
        content_as_of="2026-10-23T16:00:00+08:00",
        first_observed_at="2026-10-26T16:00:00+08:00",
        available_at="2026-10-26T16:00:00+08:00",
    )
    if fault == "hash":
        value["original_sha256"] = "bad"
    if fault == "future":
        value["available_at"] = "2026-10-28T16:00:00+08:00"
    if fault == "historical":
        value["available_at"] = "2026-10-23T16:00:00+08:00"
    if fault == "duplicate":
        value["holdings"] *= 2
    if fault == "unit":
        value["weight_unit"] = "percent"
    if fault == "review":
        value["reviewed_source_domain"] = None
    write_json(tmp_path / (etf + ".json"), value)
    rows, report = ReviewedFileBenchmarkProvider(tmp_path).load(
        "2026-10-27T07:00:00+08:00"
    )
    assert not rows and report["status"] == "BLOCKED"


def arguments(tmp_path):
    return SimpleNamespace(
        operations=None,
        trade_date=None,
        live=False,
        update_data=False,
        output=tmp_path / "runs",
        data_root=tmp_path / "data",
        state=None,
        calendar=None,
        benchmarks=None,
        initial=False,
        rerun=False,
        resume=None,
    )


def test_supervisor_deadline_kills_worker_and_notifies_without_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "src.pipeline.daily.code_fingerprint", lambda: {"code_version": "MOCK"}
    )
    calls = []

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout):
            if not calls:
                raise subprocess.TimeoutExpired("MOCK", timeout)
            return 0

        def kill(self):
            calls.append("killed")

    monkeypatch.setattr(subprocess, "Popen", Process)
    report = daily_run(arguments(tmp_path))
    assert calls == ["killed"] and report["execution_status"] == "FAILED"
    assert report["submission_status"] == "BLOCKED"
    notification = json.loads(
        (Path(report["report_directory"]) / "notification.json").read_text(
            encoding="utf-8"
        )
    )
    assert "不可提交" in notification["message"]
    assert "GOOGLE_API_KEY" not in json.dumps(report)


def test_live_historical_date_rejected_before_child_or_key(tmp_path):
    args = arguments(tmp_path)
    args.live, args.trade_date = True, "2000-01-01"
    assert daily_run(args)["errors"] == ["HistoricalLiveExecutionForbidden"]


def test_duplicate_across_output_folders_uses_shared_data_registry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "src.pipeline.daily.code_fingerprint", lambda: {"code_version": "MOCK"}
    )

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout):
            return 1

    monkeypatch.setattr(subprocess, "Popen", Process)
    first = arguments(tmp_path)
    assert daily_run(first)["execution_status"] == "FAILED"
    second = arguments(tmp_path)
    second.output = tmp_path / "different-output"
    assert daily_run(second)["errors"] == ["ExistingRunUseResumeOrRerun"]


def test_window_requires_timezone_and_converts_utc_to_taipei():
    config = {
        "submission_open": "07:00",
        "submission_close": "08:30",
        "submission_window_source": "MOCK",
    }
    assert (
        window_check(
            datetime.fromisoformat("2026-10-26T23:00:00+00:00"), "2026-10-27", config
        )["status"]
        == "IN_WINDOW"
    )
    with pytest.raises(ValueError, match="Timezone"):
        window_check(datetime(2026, 10, 27, 7), "2026-10-27", config)


def test_scheduler_scripts_have_safe_defaults_and_no_registration_in_tests():
    install = (ROOT / "scripts/Install-DailyTask.ps1").read_text(encoding="utf-8")
    run = (ROOT / "scripts/Run-Daily.ps1").read_text(encoding="utf-8")
    assert (
        "-MultipleInstances IgnoreNew" in install and "-StartWhenAvailable" in install
    )
    assert (
        "-LogonType Interactive" in install and "-DryRun" not in install
    )  # parameter is switch DryRun
    assert "[switch]$DryRun" in install and "if ($Live)" in run
    assert "conda activate" not in run and "--live" not in run.split("if ($Live)")[0]


def test_24_day_synthetic_full_pipeline(tmp_path):
    from src.pipeline.offline_decision import complete_offline_dry_run

    report = asyncio.run(complete_offline_dry_run(tmp_path, 24))
    assert report["status"] == "SYNTHETIC_PIPELINE_PASSED"
    assert report["synthetic"] is True and report["live_model_called"] is False
    assert len(report["history"]) == 24
    assert report["history"][0]["trade_date"] == "2026-10-27"
    assert report["history"][-1]["trade_date"] == "2026-11-27"
    assert all(
        r["features"] == 150
        and 20 <= r["holdings"] <= 30
        and r["formal_readiness"] == "BLOCKED"
        for r in report["history"]
    )
    assert (tmp_path / "2026-11-27/portfolio.json").exists()
    assert any(r["buy_order_count"] > 0 for r in report["history"][1:])
    assert any(r["sell_order_count"] > 0 for r in report["history"][1:])


@pytest.mark.parametrize("fault", ["future", "undated", "business"])
def test_mcp_market_rejects_bad_dates_and_errors(fault):
    from src.pipeline.market_research import extract_market_response

    payload = {
        "index": {"MOCK index": {"as_of": "2026-10-08", "close": 100}},
        "valuation": {"pe_ttm": 10},
    }
    if fault == "future":
        payload["index"]["MOCK index"]["as_of"] = "2026-10-12"
    if fault == "undated":
        payload["index"]["MOCK index"].pop("as_of")
    response = {
        "isError": fault == "business",
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }
    with pytest.raises(ValueError):
        extract_market_response(response, "2026-10-11T07:00:00+08:00")


def test_mcp_market_undated_valuation_not_forwarded_to_agent():
    from src.pipeline.market_research import extract_market_response

    payload = {
        "index": {"MOCK": {"as_of": "2026-10-08", "close": 100}},
        "valuation": {"pe_ttm": 10},
    }
    result = extract_market_response(
        {"content": [{"type": "text", "text": json.dumps(payload)}]},
        "2026-10-11T07:00:00+08:00",
    )
    assert "valuation" not in result and result["excluded_undated_sections"] == [
        "valuation"
    ]


def test_worker_outside_competition_makes_no_model_calls(tmp_path):
    from src.pipeline.daily import run_worker

    store = RunStore(tmp_path)
    inputs = {
        "trade_date": "2000-01-01",
        "operations": operations_config(),
        "live": False,
    }
    directory, _ = store.prepare("2000-01-01", rerun=False, resume=None, inputs=inputs)
    result = run_worker(
        {
            "run_directory": str(directory),
            "output_root": str(tmp_path),
            "inputs": inputs,
        }
    )
    assert (
        result["execution_status"] == "COMPLETED"
        and result["submission_status"] == "BLOCKED"
    )
    assert result["errors"] == ["OutsideCompetitionPeriod"]
    assert "decision" not in result["stages"]


@pytest.mark.parametrize("fault", [None, "missing_price", "mcp_timeout"])
def test_daily_worker_mock_orchestration_and_failure_reports(
    tmp_path, monkeypatch, fault
):
    """Every provider/model here is MOCK; exercises runner integration, not Live."""
    from dataclasses import replace
    from test_workflow import fixture
    from src.pipeline.daily import run_worker
    from src.competition.calendar import TWSECalendarProvider

    monkeypatch.setenv("GOOGLE_API_KEY", "MOCK-not-a-live-key")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 27, 7, tzinfo=TAIPEI)

    monkeypatch.setattr("src.pipeline.daily.datetime", Clock)
    rows = [
        dict(
            ticker=s["ticker"],
            market=s["market"],
            trade_date="2026-10-23",
            observation_date="2026-10-23",
            close=100,
            volume=1000,
            available_at="2026-10-23T16:00:00+08:00",
            MOCK=True,
        )
        for s in UniverseLoader().load_universe()
    ]
    if fault == "missing_price":
        rows.pop()

    class Database:
        def __init__(self, *args):
            pass

        def query(self, kind):
            return rows if kind == "prices" else []

        def close(self):
            pass

    monkeypatch.setattr("src.data.storage.DataStore", Database)
    quality = dict(
        stocks_with_data=len(rows),
        rows=len(rows),
        quality_counts={"ERROR": 0},
        below_target=[],
        missing_stocks=[],
        unverified_or_failed_days=[],
    )
    monkeypatch.setattr(
        "src.data.quality.DataQualityValidator.report", lambda *a, **k: quality
    )
    context, decision = fixture()

    def build(**kwargs):
        cutoff = kwargs["cutoff"]
        return replace(
            context,
            data_cutoff=cutoff,
            snapshot=replace(context.snapshot, data_cutoff=cutoff),
            rankings={**context.rankings, "data_cutoff": cutoff},
        )

    monkeypatch.setattr("src.pipeline.context.build_context", build)

    async def market():
        if fault == "mcp_timeout":
            raise TimeoutError("MOCK")
        return {
            "first_observed_at": "2026-10-27T07:00:00+08:00",
            "payload": {
                "index": {
                    "MOCK index": {
                        "as_of": "2026-10-23",
                        "close": 100,
                        "change": 1,
                        "change_pct": 0.01,
                    }
                },
                "link": "https://example.test/MOCK",
            },
        }

    monkeypatch.setattr("src.pipeline.market_research.get_live_market_snapshot", market)

    async def workflow(*args, **kwargs):
        output = args[2]
        write_json(output / "MOCK.json", {"synthetic": True})
        return {
            "status": "BLOCKED",
            "agent_decision": decision.model_dump(mode="json"),
            "validation": {"schema_valid": True, "status": "BLOCKED"},
            "gates": {"active_share": False, "settled_inventory": False},
        }

    monkeypatch.setattr("src.pipeline.workflow.competition_dry_run", workflow)
    raw = tmp_path / "MOCK-calendar-raw.json"
    payload = dict(
        stat="ok",
        queryYear=2026,
        fields=["日期", "名稱", "說明"],
        total=1,
        data=[["2026-10-26", "MOCK補假", "補假"]],
    )
    write_json(
        raw,
        {
            "url": TWSECalendarProvider.URL,
            "payload": payload,
            "fetched_at": "2026-10-11T07:00:00+08:00",
        },
    )
    calendar = tmp_path / "MOCK-calendar.json"
    write_json(
        calendar,
        {
            "url": TWSECalendarProvider.URL,
            "payload": payload,
            "fetched_at": "2026-10-11T07:00:00+08:00",
            "raw_ref": str(raw),
            "raw_sha256": sha256(raw),
        },
    )
    inputs = dict(
        trade_date="2026-10-27",
        operations=operations_config(),
        live=True,
        state=None,
        settled_directory=str(tmp_path / "settled"),
        initial=True,
        update_data=False,
        calendar=str(calendar),
        benchmarks=None,
        data_root=str(tmp_path / "data"),
    )
    store = RunStore(tmp_path / "runs")
    directory, _ = store.prepare(
        inputs["trade_date"], rerun=False, resume=None, inputs=inputs
    )
    report = run_worker(
        {
            "run_directory": str(directory),
            "output_root": str(tmp_path / "runs"),
            "inputs": inputs,
        }
    )
    assert report["submission_status"] == "BLOCKED"
    if fault == "missing_price":
        assert (
            "PreviousSessionCoverageIncomplete" in report["errors"]
            and "decision" not in report["stages"]
        )
    elif fault == "mcp_timeout":
        assert (
            report["execution_status"] == "FAILED"
            and "TimeoutError" in report["errors"]
        )
    else:
        assert report["gemini"] == "DECISION_PARSED" and report["schema"] is True
        assert (
            report["execution_status"] == "COMPLETED" and "decision" in report["stages"]
        )
        assert "active_share" in report["errors"]
    assert (directory / "notification.json").exists() and (
        directory / "report.md"
    ).exists()
