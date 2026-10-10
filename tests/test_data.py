"""Offline unit tests. Every generated row/HTTP response here is MOCK data."""

from __future__ import annotations
import asyncio
import csv
import json
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from types import SimpleNamespace
import pytest
import requests
from src.data.universe import UniverseLoader, UniverseError, ROOT
from src.data.providers.base import number, price_row, ProviderError
from src.data.providers.twse import TWSEProvider
from src.data.providers.tpex import TPExProvider
from src.data.providers.finmind import FinMindProvider, MonthlyRevenueProvider
from src.data.http import HttpClient
from src.data.storage import DataStore
from src.data.quality import DataQualityValidator
from src.data.time_safety import filter_available_data
from src.data.download import Downloader
from research_agent.reliability import (
    RetryPolicy,
    bounded_call,
    DEADLINE,
    ReliableGemini,
    ReliableTool,
)


@pytest.fixture
def universe():
    return UniverseLoader()


@pytest.fixture
def row():
    return price_row(
        "2330",
        "TWSE",
        "2025-03-10",
        "MOCK",
        [10, 12, 9, 11],
        1000,
        11000,
        "2025-03-11T08:00:00+08:00",
    )


@pytest.fixture
def store(tmp_path, universe):
    s = DataStore(tmp_path / "mock-data")
    s.set_universe(universe.load_universe())
    yield s
    s.close()


def test_universe_counts_and_lookup(universe):
    assert len(universe.load_universe()) == 150
    assert len(universe.get_twse_tickers()) == 100
    assert len(universe.get_tpex_tickers()) == 50
    assert universe.get_stock_info("2330")["name"] == "台積電"
    assert not universe.is_valid_ticker("2330.TW")
    with pytest.raises(UniverseError):
        universe.get_stock_info("9999")


@pytest.mark.parametrize(
    "mutation", ["duplicate", "ticker", "name", "market", "count", "outside", "wrong_name"]
)
def test_universe_rejects(tmp_path, universe, mutation):
    rows = universe.load_universe()
    if mutation == "duplicate":
        rows[1] = rows[0].copy()
    elif mutation == "ticker":
        rows[0]["ticker"] = "abc1"
    elif mutation == "name":
        rows[0]["name"] = ""
    elif mutation == "market":
        rows[0]["market"] = "NYSE"
    elif mutation == "count":
        rows.pop()
    elif mutation == "outside":
        rows[0]["ticker"] = "9999"
    else:
        rows[0]["name"] = "錯誤名稱"
    path = tmp_path / "mock-universe.csv"
    with path.open("w", encoding="utf8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "market"])
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(UniverseError):
        UniverseLoader(path).load_universe()


def test_numbers_and_units(row):
    assert number("1,234", True) == 1234
    assert number("--") is None
    assert row["vwap"] == 11 and row["volume_unit"] == "shares" and row["turnover_unit"] == "TWD"
    with pytest.raises(ProviderError):
        number("nan")
    with pytest.raises(ProviderError):
        number("1.1", True)


def client_payload(payload):
    return SimpleNamespace(
        get=Mock(return_value=(payload, "2025-03-11T08:00:00+08:00", "MOCK.json"))
    )


def test_twse_field_mapping():
    fields = ["證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"]
    p = {
        "stat": "OK",
        "date": "20250310",
        "tables": [
            {"fields": fields, "data": [["2330", "10", "12", "9", "11", "1,000", "11,000"]]}
        ],
    }
    r = TWSEProvider(client_payload(p)).snapshot("2025-03-10")[0]
    assert (r["close"], r["volume"], r["turnover"]) == (11, 1000, 11000)
    p["date"] = "20250311"
    with pytest.raises(ProviderError):
        TWSEProvider(client_payload(p)).snapshot("2025-03-10")


def test_tpex_exact_shares_not_lots():
    fields = ["代號", "名稱", "開盤", "最高", "最低", "收盤", "成交股數", "成交金額(元)", "均價"]
    p = {
        "tables": [
            {
                "date": "114/03/10",
                "fields": fields,
                "data": [["5274", "MOCK", "10", "12", "9", "11", "1001", "11011", "11"]],
            }
        ]
    }
    r = TPExProvider(client_payload(p)).snapshot("2025-03-10")[0]
    assert r["volume"] == 1001 and r["turnover"] == 11011
    assert r["vwap"] == 11


def test_tpex_flow_group_mapping():
    fields = (
        ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
    )
    values = ["5274", "MOCK"] + [str(n) for n in range(2, 24)]
    p = {"tables": [{"date": "114/03/10", "fields": fields, "data": [values]}]}
    r = TPExProvider(client_payload(p)).snapshot("2025-03-10", True)[0]
    assert (r["foreign_net_buy"], r["investment_trust_net_buy"], r["dealer_net_buy"]) == (4, 13, 22)
    assert r["flow_unit"] == "shares"


def test_nontrading_response():
    assert (
        TWSEProvider(client_payload({"stat": "很抱歉，沒有符合條件的資料!"})).snapshot("2025-03-09")
        == []
    )
    assert TPExProvider(client_payload({"tables": [{"data": []}]})).snapshot("2025-03-09") == []
    with pytest.raises(ProviderError):
        TPExProvider(client_payload({})).snapshot("2025-03-09")


def test_finmind_optin_and_mapping(monkeypatch, universe):
    monkeypatch.delenv("FINMIND_ENABLED", raising=False)
    with pytest.raises(ProviderError):
        FinMindProvider(client_payload({}), universe)
    monkeypatch.setenv("FINMIND_ENABLED", "1")
    p = {
        "status": 200,
        "data": [
            dict(
                stock_id="2330",
                date="2025-03-10",
                open=10,
                max=12,
                min=9,
                close=11,
                Trading_Volume=1001,
                Trading_money=11011,
                Trading_turnover=42,
            )
        ],
    }
    r = FinMindProvider(client_payload(p), universe).get_daily_prices(
        "2330", "2025-03-10", "2025-03-10"
    )[0]
    assert r["turnover"] == 11011  # Trading_turnover is trade count, NOT turnover money.


def test_revenue_publication_unknown(monkeypatch, universe):
    monkeypatch.setenv("FINMIND_ENABLED", "1")
    p = {
        "status": 200,
        "data": [
            dict(
                stock_id="2330",
                date="2025-03-01",
                revenue_year=2025,
                revenue_month=2,
                revenue=123,
                create_time="2025-03-01",
            )
        ],
    }
    r = MonthlyRevenueProvider(FinMindProvider(client_payload(p), universe)).get_monthly_revenue(
        "2330", "2025-03-01", "2025-03-11"
    )[0]
    assert r["published_at"] is None and not r["strict_historical_backtest_eligible"]
    assert r["observation_date"] == "2025-02-28"
    assert not filter_available_data([r], "2025-03-10T08:30:00+08:00")


@pytest.mark.parametrize("status", [429, 503])
def test_http_retry_status(tmp_path, monkeypatch, status):
    monkeypatch.setattr("src.data.http.time.sleep", lambda _: None)
    c = HttpClient(tmp_path, max_retries=2, interval=0)
    failed = requests.Response()
    failed.status_code = status
    good = Mock()
    good.raise_for_status.return_value = None
    good.json.return_value = {"data": []}
    c.session.get = Mock(side_effect=[requests.HTTPError(response=failed), good])
    assert c.get("https://MOCK.invalid", {})[0] == {"data": []}
    assert c.session.get.call_count == 2


def test_http_timeout_bounded_and_no_secrets(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("src.data.http.time.sleep", lambda _: None)
    c = HttpClient(tmp_path, max_retries=2, interval=0)
    c.session.get = Mock(side_effect=requests.Timeout("token=MOCK_SECRET"))
    with pytest.raises(ProviderError):
        c.get("https://MOCK.invalid", {}, {"Authorization": "Bearer MOCK_SECRET"})
    assert c.session.get.call_count == 3
    assert "MOCK_SECRET" not in caplog.text


def test_raw_archive_and_resume(tmp_path):
    c = HttpClient(tmp_path)
    good = Mock()
    good.json.return_value = {"data": []}
    c.session.get = Mock(return_value=good)
    first = c.get("https://MOCK.invalid", {})
    second = HttpClient(tmp_path)
    second.session.get = Mock(side_effect=AssertionError("no duplicate HTTP"))
    assert second.get("https://MOCK.invalid", {}) == first
    assert Path(first[2]).exists()


def test_sqlite_parquet_idempotent_and_revision(store, row):
    assert store.save("prices", [row, row]) == 1
    assert store.save("prices", [row]) == 0
    assert store.query("prices", "2330")[0]["close"] == 11
    revised = dict(
        row,
        close=12,
        fetched_at="2025-03-12T08:00:00+08:00",
        available_at="2025-03-12T08:00:00+08:00",
    )
    assert store.save("prices", [revised]) == 1
    assert store.query("prices", "2330")[0]["close"] == 12
    assert store.query("prices", "2330", as_of="2025-03-11T12:00:00+08:00")[0]["close"] == 11
    assert store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    assert len(list((store.root / "processed").rglob("*.parquet"))) == 2


def test_pit_leakage(row):
    assert not filter_available_data([row], "2025-03-10T08:30:00+08:00")
    assert filter_available_data([row], "2025-03-11T08:00:00+08:00")
    unknown = dict(row, available_at=None, published_at=None)
    assert not filter_available_data([unknown], "2025-03-11T08:00:00+08:00")
    assert filter_available_data([unknown], "2025-03-11T08:00:00+08:00", strict=False)[0][
        "availability_uncertain"
    ]
    with pytest.raises(ValueError):
        filter_available_data([row], "2025-03-11T08:00:00")


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"volume": -1}, "invalid_volume"),
        ({"high": 8}, "high_below_low"),
        ({"close": 20}, "close_outside_range"),
        ({"volume_unit": "lots"}, "unit_mismatch"),
        ({"trade_date": None}, "invalid_date_or_timestamp"),
        ({"fetched_at": "2999-01-01T00:00:00+08:00"}, "future_fetch"),
    ],
)
def test_quality_errors(universe, row, changes, code):
    if code == "invalid_date_or_timestamp":
        changes = {"observation_date": "bad"}
    issues = DataQualityValidator(universe).validate([dict(row, **changes)])
    assert any(i["level"] == "ERROR" and i["code"] == code for i in issues)


def test_quality_null_no_trades_and_duplicates(universe, row):
    v = DataQualityValidator(universe)
    r = dict(row, open=None, volume=0)
    issues = v.validate([r])
    assert not any(i["level"] == "ERROR" for i in issues)
    assert any(i["code"] == "no_trades_or_suspension" for i in issues)
    assert any(i["code"] == "duplicate" for i in v.validate([row, row]))


def test_download_resume_failure_and_report(store, universe, row):
    p = SimpleNamespace(snapshot=Mock(return_value=[row]))
    d = Downloader(universe, store, {"TWSE": p})
    d.download("2025-03-10", "2025-03-10")
    assert store.query("prices", "2330")
    # Partial full-market snapshot is never silently called complete.
    assert not store.completed("prices", "TWSE", "2025-03-10")
    p.snapshot.side_effect = ProviderError("MOCK offline")
    result = d.download("2025-03-11", "2025-03-11")
    assert (
        result["failures"]
        and store.db.execute("SELECT COUNT(*) FROM failures WHERE day='2025-03-11'").fetchone()[0]
        == 1
    )
    report = DataQualityValidator(universe).report(store)
    assert report["total_stocks"] == 150 and report["stocks_with_data"] == 1
    assert "2454" in report["missing_stocks"]


@pytest.mark.parametrize("code", [429, 503])
def test_agent_bounded_retries(code):
    class Error(Exception):
        pass

    e = Error()
    e.code = code
    operation = AsyncMock(side_effect=[e, "MOCK OK"])
    assert (
        asyncio.run(
            bounded_call(
                operation, RetryPolicy(initial_backoff=0.001, max_backoff=0.001), 1, "MOCK"
            )
        )
        == "MOCK OK"
    )
    assert operation.call_count == 2


def test_agent_timeout_and_nonretryable():
    op = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        asyncio.run(
            bounded_call(
                op,
                RetryPolicy(max_retries=2, initial_backoff=0.001, max_backoff=0.001),
                0.001,
                "MOCK",
            )
        )
    assert op.call_count == 3
    op = AsyncMock(side_effect=ValueError())
    with pytest.raises(ValueError):
        asyncio.run(bounded_call(op, RetryPolicy(), 1, "MOCK"))
    assert op.call_count == 1


def test_deadline_stops_before_request():
    import time

    async def run():
        token = DEADLINE.set(time.monotonic() - 1)
        op = AsyncMock(return_value="MOCK")
        try:
            with pytest.raises(TimeoutError):
                await bounded_call(op, RetryPolicy(max_retries=0), 10, "MOCK")
            assert op.call_count == 0
        finally:
            DEADLINE.reset(token)

    asyncio.run(run())


def test_real_gemini_wrapper_retries_503(monkeypatch):
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    monkeypatch.setenv("AGENT_INITIAL_BACKOFF", "0.001")
    monkeypatch.setenv("AGENT_MAX_BACKOFF", "0.001")
    count = []

    class Error(Exception):
        code = 503

    async def transport(self, request, stream=False):
        count.append(1)
        if len(count) < 3:
            raise Error("MOCK")
        yield LlmResponse(content=types.Content(parts=[types.Part(text="MOCK")]))

    monkeypatch.setattr(Gemini, "generate_content_async", transport)
    request = SimpleNamespace(config=types.GenerateContentConfig())

    async def run():
        return [
            r async for r in ReliableGemini(model="gemini-MOCK").generate_content_async(request)
        ]

    assert len(asyncio.run(run())) == 1 and len(count) == 3
    assert request.config.http_options.retry_options.attempts == 1


def test_streaming_response_not_replayed(monkeypatch):
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    count = []

    async def transport(self, request, stream=False):
        count.append(1)
        yield LlmResponse(partial=True)
        raise TimeoutError("MOCK")

    monkeypatch.setattr(Gemini, "generate_content_async", transport)

    async def run():
        return [
            r
            async for r in ReliableGemini(model="gemini-MOCK").generate_content_async(
                SimpleNamespace(config=types.GenerateContentConfig())
            )
        ]

    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert len(count) == 1


def test_actual_mcp_timeout_wrapper(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_RETRIES", "1")
    monkeypatch.setenv("AGENT_INITIAL_BACKOFF", "0.001")
    monkeypatch.setenv("AGENT_MAX_BACKOFF", "0.001")
    monkeypatch.setenv("MCP_TOOL_TIMEOUT", "0.001")
    count = []

    async def operation(**kwargs):
        count.append(1)
        await asyncio.sleep(1)

    delegate = SimpleNamespace(
        name="monthly_revenue",
        description="MOCK readonly",
        is_long_running=False,
        run_async=operation,
    )
    with pytest.raises(TimeoutError):
        asyncio.run(ReliableTool(delegate).run_async(args={}, tool_context=None))
    assert len(count) == 2


def test_overall_agent_deadline(monkeypatch):
    from google.adk.agents import LlmAgent
    from research_agent.agent import create_agent

    monkeypatch.setenv("AGENT_TASK_DEADLINE", "0.01")

    async def slow(self, ctx):
        await asyncio.sleep(1)
        if False:
            yield None

    monkeypatch.setattr(LlmAgent, "_run_async_impl", slow)

    async def run():
        return [e async for e in create_agent()._run_async_impl(None)]

    events = asyncio.run(run())
    assert len(events) == 1 and "整體時間上限" in events[0].content.parts[0].text


def test_official_failure_uses_configured_fallback(store, universe, row):
    official = SimpleNamespace(snapshot=Mock(side_effect=ProviderError("MOCK offline")))
    fallback = SimpleNamespace(
        get_daily_prices=Mock(return_value=[dict(row, source="MOCK fallback")])
    )
    result = Downloader(universe, store, {"TWSE": official}, fallback).download(
        "2025-03-10", "2025-03-10", "2330"
    )
    assert result["written"] == 1 and store.query("prices", "2330")[0]["source"] == "MOCK fallback"


def test_invalid_availability_and_trade_date(universe, row):
    issues = DataQualityValidator(universe).validate(
        [dict(row, trade_date="2025-03-11", available_at="2025-03-12T00:00:00+08:00")]
    )
    assert {"trade_date_mismatch", "availability_after_fetch"} <= {i["code"] for i in issues}


def test_store_rejects_unofficial_ticker(store, row):
    with pytest.raises(ValueError):
        store.save("prices", [dict(row, ticker="9999")])
    assert store.query("prices") == []
