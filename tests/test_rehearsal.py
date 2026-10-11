"""MOCK engineering tests, temporary directories only; no API quota or finance claims."""

import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.competition.serialization import write_json
from src.data.time_safety import TAIPEI, now_iso
from src.pipeline.cli import parser
from src.pipeline.rehearsal import rehearsal_run, run_rehearsal_worker
from src.pipeline.research_validation import has_source_attribution
from src.pipeline.run_store import RunStore, sha256

PAYLOAD = {
    "source": "臺灣證券交易所、證券櫃檯買賣中心 三大法人買賣超",
    "link": "https://toalpha.tw/stock/2454/institutional",
}


@pytest.mark.parametrize(
    "answer",
    [
        "source：臺灣證券交易所、證券櫃檯買賣中心 三大法人買賣超",
        "來源：台灣證券交易所、證券櫃檯買賣中心 三大法人買賣超",
        "link：https://toalpha.tw/stock/2454/institutional",
        "來源：TWSE",
        "來源：TPEx",
        "資料來自證交所",
    ],
)
def test_actual_source_or_matching_link_accepted(answer):
    assert has_source_attribution(answer, [PAYLOAD])


@pytest.mark.parametrize(
    "answer",
    [
        "source：",
        "來源未說明",
        "ToAlpha",
        "https://other.test/2454",
        "資料日期2026-10-08",
    ],
)
def test_label_without_actual_attribution_rejected(answer):
    assert not has_source_attribution(answer, [PAYLOAD])


def args(tmp_path, live=False, rerun=False):
    return SimpleNamespace(
        output=tmp_path / "rehearsals",
        data_root=tmp_path / "data",
        live=live,
        initial=True,
        state=None,
        calendar=tmp_path / "calendar.json",
        benchmarks=None,
        rerun=rerun,
    )


def test_no_live_never_launches_child_and_duplicate_across_outputs_blocked(
    tmp_path, monkeypatch
):
    import src.pipeline.rehearsal as module

    monkeypatch.setattr(module, "code_fingerprint", lambda: {"MOCK": True})
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected API worker")
    )
    first = rehearsal_run(args(tmp_path))
    assert first["submission_status"] == "BLOCKED" and first["gemini"] == "NOT_ENABLED"
    other = args(tmp_path)
    other.output = tmp_path / "other-output"
    assert "ExistingRehearsalUseRerun" in rehearsal_run(other)["errors"]
    second = rehearsal_run(args(tmp_path, rerun=True))
    assert second["run_id"] != first["run_id"]


def test_cli_does_not_accept_historical_or_future_trade_date():
    with pytest.raises(SystemExit):
        parser().parse_args(
            [
                "rehearsal-run",
                "--calendar",
                "x",
                "--initial",
                "--trade-date",
                "2026-10-27",
            ]
        )


def test_rehearsal_cannot_write_financial_database(tmp_path):
    value = args(tmp_path)
    value.output = value.data_root / "processed"
    with pytest.raises(ValueError, match="Separate"):
        rehearsal_run(value)


def prepare_worker(tmp_path):
    on = datetime.now(TAIPEI).date().isoformat()
    store = RunStore(tmp_path / "outputs")
    directory, report = store.prepare(
        on, rerun=False, resume=None, inputs={"MOCK": True}
    )
    return (
        directory,
        report,
        {
            "run_directory": str(directory),
            "output_root": str(store.root),
            "inputs": {"live": True},
        },
    )


def test_failure_report_does_not_expose_raw_exception(tmp_path, monkeypatch):
    import src.pipeline.rehearsal as module

    directory, _, request = prepare_worker(tmp_path)

    async def failing(*a):
        raise RuntimeError("MOCK_SECRET_VALUE")

    monkeypatch.setattr(module, "_execute", failing)
    report = run_rehearsal_worker(request)
    assert (
        report["execution_status"] == "FAILED"
        and report["submission_status"] == "BLOCKED"
    )
    assert "MOCK_SECRET_VALUE" not in (directory / "status.json").read_text()


def test_supervisor_kills_timeout_and_keeps_blocked(tmp_path, monkeypatch):
    import subprocess
    import src.pipeline.rehearsal as module

    killed = []
    monkeypatch.setattr(module, "code_fingerprint", lambda: {"MOCK": True})

    class Process:
        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired("MOCK", timeout)

        def kill(self):
            killed.append(True)

    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: Process())
    report = rehearsal_run(args(tmp_path, live=True))
    assert (
        killed
        and report["execution_status"] == "FAILED"
        and report["submission_status"] == "BLOCKED"
    )


def test_missing_key_fails_before_mcp(tmp_path, monkeypatch):
    from dotenv import main
    import src.pipeline.market_research as market

    monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(__import__("dotenv"), "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        market, "get_live_market_snapshot", lambda: pytest.fail("Unexpected MCP")
    )
    _, _, request = prepare_worker(tmp_path)
    report = run_rehearsal_worker(request)
    assert "MissingGeminiAPIKey" in report["errors"]


def test_worker_never_promotes_ready_even_if_stage_claims_it(tmp_path, monkeypatch):
    import src.pipeline.rehearsal as module

    _, _, request = prepare_worker(tmp_path)

    async def fake_execute(request, report):
        report.update(submission_status="READY", rehearsal_checks_passed=True)

    monkeypatch.setattr(module, "_execute", fake_execute)
    result = run_rehearsal_worker(request)
    assert (
        result["execution_status"] == "COMPLETED"
        and result["submission_status"] == "BLOCKED"
    )


def mock_data_environment(tmp_path, monkeypatch, rows):
    """MOCK calendar/database interfaces; never official API or formal data files."""
    from datetime import timedelta
    import src.pipeline.daily as daily
    import src.data.storage as storage
    import src.pipeline.rehearsal as module
    import dotenv
    from src.competition.calendar import TradingCalendar

    directory, report, request = prepare_worker(tmp_path)
    root = tmp_path / "data"
    (root / "metadata").mkdir(parents=True)
    (root / "metadata/catalog.sqlite").touch()
    calendar = tmp_path / "calendar.json"
    calendar.write_text("MOCK CALENDAR")
    previous = (datetime.now(TAIPEI).date() - timedelta(days=1)).isoformat()

    class Calendar:
        def sessions(self, *a):
            return {previous}

    class Database:
        def __init__(self, *a):
            pass

        def query(self, dataset):
            return rows if dataset == "prices" else []

        def close(self):
            pass

    monkeypatch.setattr(daily, "read_calendar", lambda *a: Calendar())
    monkeypatch.setattr(storage, "DataStore", Database)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "MOCK_KEY_NEVER_USED")
    request["inputs"].update(
        data_root=str(root),
        initial=True,
        state=None,
        benchmarks=None,
        calendar=str(calendar),
        calendar_sha256=sha256(calendar),
        code_fingerprint=module.code_fingerprint(),
    )
    return directory, report, request, previous


def test_missing_previous_prices_blocks_before_any_api(tmp_path, monkeypatch):
    import src.pipeline.market_research as market

    _, _, request, _ = mock_data_environment(tmp_path, monkeypatch, [])
    monkeypatch.setattr(
        market, "get_live_market_snapshot", lambda: pytest.fail("Unexpected MCP")
    )
    result = run_rehearsal_worker(request)
    assert "PreviousSessionCoverageIncomplete" in result["errors"]
    assert len(result["previous_session_missing_tickers"]) == 150


def test_synthetic_prices_rejected_before_any_api(tmp_path, monkeypatch):
    import src.pipeline.market_research as market

    _, _, request, previous = mock_data_environment(tmp_path, monkeypatch, [])
    import src.data.storage as storage

    monkeypatch.setattr(
        storage.DataStore,
        "query",
        lambda self, dataset: (
            [
                {
                    "ticker": "2330",
                    "observation_date": previous,
                    "source": "SYNTHETIC_FIXTURE",
                    "synthetic": True,
                }
            ]
            if dataset == "prices"
            else []
        ),
    )
    monkeypatch.setattr(
        market, "get_live_market_snapshot", lambda: pytest.fail("Unexpected MCP")
    )
    result = run_rehearsal_worker(request)
    assert result["execution_status"] == "FAILED"


def test_mock_orchestration_runs_real_optimizer_generator_and_both_validations(
    tmp_path, monkeypatch
):
    """MOCK protocol reply/context (including real-like flags) only in tmp_path.

    No genuine Gemini call is implied. Feature construction and database/calendar
    adapters are mocked; actual Optimizer/Generator/Validator execute unchanged.
    """
    from tests.test_workflow import fixture
    from src.agent.decision import InvestmentDecision
    from src.data.universe import UniverseLoader
    import src.data.storage as storage
    import src.pipeline.context as context_module
    import src.pipeline.market_research as market
    import src.pipeline.workflow as workflow

    directory, _, request, previous = mock_data_environment(tmp_path, monkeypatch, [])
    context, template = fixture()
    monkeypatch.setattr(
        storage.DataStore,
        "query",
        lambda self, dataset: (
            [
                {
                    "ticker": t,
                    "trade_date": previous,
                    "observation_date": previous,
                    "close": 100,
                    "market": UniverseLoader().get_stock_info(t)["market"],
                }
                for t in UniverseLoader().get_all_tickers()
            ]
            if dataset == "prices"
            else []
        ),
    )
    from src.strategy.availability import AvailabilityPolicy
    from src.data.quality import DataQualityValidator

    monkeypatch.setattr(AvailabilityPolicy, "select", lambda self, rows, *a: (rows, []))
    monkeypatch.setattr(DataQualityValidator, "validate", lambda *a: [])

    def build(**values):
        return replace(
            context,
            trade_date=values["trade_date"],
            data_cutoff=values["cutoff"],
            fixture=False,
            snapshot=replace(
                context.snapshot, data_cutoff=values["cutoff"], mode="strict_verified"
            ),
            rankings={**context.rankings, "data_cutoff": values["cutoff"]},
            sources=[
                {**context.sources[0], "content_as_of": previous + "T00:00:00+08:00"}
            ],
        )

    monkeypatch.setattr(context_module, "build_context", build)

    async def snapshot():
        return {
            "first_observed_at": now_iso(),
            "synthetic": False,
            "tool_calls": [{"name": "market_overview", "MOCK": True}],
            "payload": {
                "index": {
                    "MOCK": {
                        "as_of": previous,
                        "close": 100,
                        "change": 1,
                        "change_pct": 0.01,
                    }
                },
                "link": "https://example.test/MOCK",
                "source": "MOCK",
            },
        }

    monkeypatch.setattr(market, "get_live_market_snapshot", snapshot)

    async def model(ctx, feedback=None):
        decision = InvestmentDecision.model_validate(
            {
                **template.model_dump(),
                "trade_date": ctx.trade_date,
                "data_cutoff": ctx.data_cutoff,
                "model_version": "gemini-protocol-stub",
            }
        )
        return decision, {
            "status": "DECISION_PARSED",
            "synthetic": False,
            "MOCK": True,
            "run_started_at": now_iso(),
            "run_completed_at": now_iso(),
            "tool_calls": [{"name": "MOCK_READ_ONLY"}],
            "model_events": [{"MOCK": True}],
        }

    original_workflow = workflow.competition_dry_run

    async def invoke(*a, **k):
        return await original_workflow(*a, **k, decision_runner=model)

    monkeypatch.setattr(workflow, "competition_dry_run", invoke)
    result = run_rehearsal_worker(request)
    assert result["execution_status"] == "COMPLETED", result["errors"]
    assert (
        result["submission_status"] == "BLOCKED" and result["rehearsal_checks_passed"]
    )
    assert result["schema"] is True
    assert any(i.code == "C6_CUTOFF" for i in result["formal_validation"].issues)
    assert (directory / "rehearsal-dplan-audit.json").exists()
    assert not (directory / "ready").exists()
