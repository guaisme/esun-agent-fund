"""Real network/API tests: default SKIPPED; no mock success is counted as E2E."""

from __future__ import annotations
import asyncio
import json
import os
import re
from pathlib import Path
from uuid import uuid4
import pytest

CASES = [
    (
        "revenue",
        "請使用工具查詢台積電 2330 最新月營收。請標示實際資料日期、數值、單位與來源。",
        "monthly_revenue",
    ),
    (
        "market",
        "請使用工具查詢目前台股市場概況。請標示資料日期、數值、單位與來源。",
        "market_overview",
    ),
    (
        "flows",
        "請使用工具查詢聯發科 2454 最近五個交易日的三大法人買賣超。請標示各日日期、數值、單位與來源。",
        "institutional_flows",
    ),
]


@pytest.mark.live_e2e
@pytest.mark.parametrize("case,prompt,required", CASES)
def test_live_research(case, prompt, required):
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("Enable RUN_LIVE_E2E=1; consumes Gemini quota")
    if not os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE":
        pytest.skip("Missing Gemini API key")
    asyncio.run(run_case(case, prompt, required))


async def run_case(case: str, prompt: str, required: str):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from research_agent.agent import create_agent, response_payloads, has_future_date

    agent = create_agent()
    sessions = InMemorySessionService()
    session_id = uuid4().hex
    await sessions.create_session(
        app_name="research_agent", user_id="live_test", session_id=session_id
    )
    runner = Runner(agent=agent, app_name="research_agent", session_service=sessions)
    calls = []
    responses = []
    final = ""
    error = None
    try:
        async for event in runner.run_async(
            user_id="live_test",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        ):
            if event.content:
                for part in event.content.parts or []:
                    if part.function_call:
                        calls.append(part.function_call.name)
                    if part.function_response:
                        responses.append(
                            (part.function_response.name, part.function_response.response)
                        )
                if event.is_final_response():
                    final = "".join(
                        p.text or "" for p in event.content.parts or [] if not p.thought
                    )
        validate_live_evidence(calls, responses, final, required)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        out = Path(os.getenv("LIVE_REPORT_DIR", "data/metadata/live-e2e"))
        out.mkdir(parents=True, exist_ok=True)
        report = dict(
            case=case,
            model=str(agent.model),
            calls=calls,
            required_tool=required,
            final=final,
            status="FAILED" if error else "AUTOMATED_CHECKS_PASSED",
            error_type=error,
            human_review_required="Compare every numerical claim/units/dates with MCP payload; check Traditional Chinese and five trading days.",
            responses=responses,
        )
        # These public research payloads are local and gitignored; never API keys.
        from research_agent.agent import redact

        (out / (case + ".json")).write_text(
            redact(json.dumps(report, ensure_ascii=False, indent=2, default=str)), encoding="utf-8"
        )
        await runner.close()


def validate_live_evidence(calls: list, responses: list, final: str, required: str) -> None:
    """Validate real or synthetic protocol shape; not a full semantic fact checker."""
    from research_agent.agent import response_payloads, has_future_date

    finance = [
        p
        for name, response in responses
        if name == required
        for p in response_payloads(response)
        if isinstance(p, dict)
        and (p.get("rows") or p.get("as_of") or p.get("source"))
        and not p.get("error")
    ]
    dates = sorted(
        set(re.findall(r"\b\d{4}-\d{2}(?:-\d{2})?\b", json.dumps(finance, ensure_ascii=False)))
    )
    assert required in calls, "Gemini did not call required MCP tool"
    assert finance, "MCP did not return inspectable real dated financial data"
    assert dates and not any(
        has_future_date(p) for p in finance
    ), "Missing/current-future date error"
    assert final and re.search("[\u4e00-\u9fff]", final), "No Chinese final answer"
    assert not any(
        x in final
        for x in [
            "Gemini API 呼叫失敗",
            "研究已達整體時間上限",
            "本輪未成功取得",
            "本次研究未完成",
        ]
    ), final
    assert any(d in final for d in dates), "Final answer does not cite returned data date"
    assert re.search(r"來源|ToAlpha|TWSE|TPEx|證交|櫃買", final), "Missing source attribution"
    # Semantic correctness, Traditional Chinese usage, and every quoted number
    # still require human comparison with returned payloads. Do not claim these
    # automated checks prove full absence of hallucination.


@pytest.mark.integration
def test_official_provider_live(tmp_path):
    if os.getenv("RUN_DATA_INTEGRATION") != "1":
        pytest.skip("Enable RUN_DATA_INTEGRATION=1; real official API requests")
    from src.data.http import HttpClient
    from src.data.providers.twse import TWSEProvider
    from src.data.providers.tpex import TPExProvider
    from src.data.universe import UniverseLoader
    from src.data.quality import DataQualityValidator

    client = HttpClient(tmp_path / "real-raw", interval=2)
    universe = UniverseLoader()
    for provider, ticker in [(TWSEProvider(client), "2330"), (TPExProvider(client), "5274")]:
        for dataset in ["prices", "flows"]:
            rows = [
                r
                for r in provider.snapshot("2026-09-30", flows=dataset == "flows")
                if r["ticker"] == ticker
            ]
            assert rows and not any(
                i["level"] == "ERROR"
                for i in DataQualityValidator(universe).validate(rows, dataset)
            )
            assert all(
                r["source"] == provider.name and r["trade_date"] == "2026-09-30" for r in rows
            )


def test_nested_market_dates_mock_schema():
    # MOCK protocol fixture: not real finance and never saved in formal data.
    validate_live_evidence(
        ["market_overview"],
        [
            (
                "market_overview",
                {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "index": {"MOCK": {"as_of": "2025-03-10", "close": 123}},
                                    "source": "MOCK",
                                }
                            )
                        }
                    ]
                },
            )
        ],
        "來源 MOCK，資料日期 2025-03-10",
        "market_overview",
    )
