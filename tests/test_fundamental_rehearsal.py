"""MOCK fixtures only; no provider or Gemini requests."""

import json
from types import SimpleNamespace

from src.data.providers.finmind import MonthlyRevenueProvider
from src.data.revenue_download import download_revenue
from src.data.providers.base import ProviderError
from src.pipeline.fundamentals import fundamental_coverage, REQUIRED_FACTORS
from src.pipeline.decision_report import export_decision_report
from src.pipeline.cli import parser


def test_growth_ratios_from_observed_months_preserve_unknown_publication():
    rows = [
        dict(stock_id="2330", revenue_year=y, revenue_month=m, revenue=n)
        for y, m, n in [(2025, 1, 100), (2025, 12, 150), (2026, 1, 200)]
    ]
    provider = SimpleNamespace(
        fetch=lambda *a: (rows, "2026-02-15T12:00:00+08:00", "MOCK.json"),
        universe=SimpleNamespace(get_stock_info=lambda t: {"market": "TWSE"}),
    )
    result = MonthlyRevenueProvider(provider).get_monthly_revenue(
        "2330", "2025-01-01", "2026-02-15"
    )
    assert result[-1]["revenue_yoy"] == 1
    assert abs(result[-1]["revenue_mom"] - 1 / 3) < 1e-10
    assert result[-1]["published_at"] is None
    assert result[0]["revenue_yoy"] is None


def test_coverage_requires_every_stock_and_zero_is_valid():
    complete = dict.fromkeys(REQUIRED_FACTORS, 0)
    result = fundamental_coverage({"2330": complete}, ["2330", "2454"])
    assert result["complete_stocks"] == 1 and result["status"] == "INCOMPLETE"
    assert result["missing"]["2454"] == list(REQUIRED_FACTORS)
    assert fundamental_coverage({"2330": complete}, ["2330"])["status"] == "COMPLETE"
    assert result["financial_statements"] == "NOT_IMPLEMENTED"


def test_default_requires_fundamentals_explicit_price_only_opt_out():
    assert (
        not parser()
        .parse_args(["rehearsal-run", "--calendar", "x", "--initial"])
        .allow_price_only
    )
    assert (
        parser()
        .parse_args(
            ["rehearsal-run", "--calendar", "x", "--initial", "--allow-price-only"]
        )
        .allow_price_only
    )


def test_revenue_stops_repeated_provider_failure_without_logging_secret():
    attempts = []

    def fail(t, *a):
        attempts.append(t)
        raise ProviderError("MOCK_SECRET")

    result = download_revenue(
        SimpleNamespace(get_monthly_revenue=fail),
        SimpleNamespace(get_all_tickers=lambda: ["1", "2", "3", "4"]),
        SimpleNamespace(failure=lambda *a: None),
        "2025-01-01",
        "2026-01-01",
    )
    assert len(attempts) == 3 and result["unattempted"] == ["4"]
    assert "MOCK_SECRET" not in json.dumps(result)


def test_report_distinguishes_cash_orders_and_no_fills(tmp_path):
    source, destination = tmp_path / "MOCK-run", tmp_path / "report"
    source.mkdir()
    data = {
        "workflow.json": {
            "agent_decision": {
                "model_version": "MOCK",
                "target_weights": {"2330": "0.2"},
                "rationale": {"2330": {"logic": "MOCK reason", "premise_refs": ["O1"]}},
            },
            "optimizer": {
                "orders": [{"ticker": "2330", "side": "BUY", "shares": 1000}],
                "diagnostics": {"cash": "810", "cash_ratio": "0.81"},
            },
        },
        "context.json": {
            "state": {"cash": "1000"},
            "observations": [{"obs_id": "O1", "statement": "MOCK only"}],
        },
        "status.json": {"run_id": "MOCK"},
    }
    for name, payload in data.items():
        (source / name).write_text(json.dumps(payload), encoding="utf-8")
    text = export_decision_report(source, destination).read_text(encoding="utf-8")
    assert "80.00%" in text and "81.00%" in text and "沒有實際成交" in text
    assert "MOCK reason" in text and "台積電" in text
    assert "NOT_EXECUTED" in (destination / "proposed-orders.csv").read_text(
        encoding="utf-8-sig"
    )


def test_missing_fundamentals_blocks_before_any_mcp_or_gemini(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.test_rehearsal import mock_data_environment
    from tests.test_workflow import fixture
    from src.pipeline.rehearsal import run_rehearsal_worker
    import src.pipeline.context as context_module
    import src.pipeline.market_research as market

    directory, _, request, previous = mock_data_environment(tmp_path, monkeypatch, [])
    import src.data.storage as storage
    from src.data.universe import UniverseLoader
    from src.strategy.availability import AvailabilityPolicy
    from src.data.quality import DataQualityValidator

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
    monkeypatch.setattr(AvailabilityPolicy, "select", lambda self, rows, *a: (rows, []))
    monkeypatch.setattr(DataQualityValidator, "validate", lambda *a: [])
    request["inputs"]["require_fundamentals"] = True
    context, _ = fixture()
    monkeypatch.setattr(
        context_module,
        "build_context",
        lambda **k: replace(
            context,
            snapshot=replace(context.snapshot, values={}, data_cutoff=k["cutoff"]),
            rankings={**context.rankings, "data_cutoff": k["cutoff"]},
            trade_date=k["trade_date"],
            data_cutoff=k["cutoff"],
        ),
    )

    async def unexpected():
        raise AssertionError("Unexpected billable API")

    monkeypatch.setattr(market, "get_live_market_snapshot", unexpected)
    report = run_rehearsal_worker(request)
    assert report["execution_status"] == "FAILED"
    assert "FundamentalCoverageIncomplete" in report["errors"]
    assert report["fundamental_coverage"]["complete_stocks"] == 0
    assert (directory / "fundamental-coverage.json").exists()
