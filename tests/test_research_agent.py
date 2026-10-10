"""Offline tests use mocks; the explicitly opted-in test calls real ToAlpha."""

import asyncio
import importlib
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.mcp_tool import McpToolset
from google.genai import types

agent = importlib.import_module("research_agent.agent")


def context():
    return SimpleNamespace(state={})


def test_import_and_configuration(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    root = agent.create_agent()
    assert isinstance(root, LlmAgent)
    assert root.name == "esun_research_agent"
    assert root.model == "gemini-2.5-flash"
    monkeypatch.setenv("GEMINI_MODEL", "custom-model")
    assert agent.create_agent().model == "custom-model"


def test_mcp_parameters():
    toolset = agent.root_agent.tools[0]
    assert isinstance(toolset, McpToolset)
    params = toolset.connection_params
    assert params.url == os.getenv("TOALPHA_MCP_URL", "https://toalpha.tw/mcp")
    assert params.timeout == 15
    assert params.sse_read_timeout == 30
    assert set(toolset.tool_filter) == set(agent.TOOLS)


def test_api_key_never_logged(monkeypatch, caplog):
    key = "test-secret-do-not-log-123"
    monkeypatch.setenv("GOOGLE_API_KEY", key)
    with caplog.at_level(logging.INFO):
        logging.getLogger("sdk_test").info("request key=%s", key)
        try:
            raise RuntimeError("API rejected " + key)
        except RuntimeError:
            logging.getLogger("sdk_test").exception("failure %s", key)
        agent.log_tool(SimpleNamespace(name="monthly_revenue"), {"key": key}, context())
    assert key not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_discovery_unavailable(monkeypatch):
    monkeypatch.setattr(McpToolset, "get_tools", AsyncMock(side_effect=ConnectionError("offline")))
    assert asyncio.run(agent.root_agent.tools[0].get_tools()) == []


def test_missing_tools_fail_closed(monkeypatch):
    monkeypatch.setattr(McpToolset, "get_tools", AsyncMock(return_value=[SimpleNamespace(name="resolve_stock")]))
    assert asyncio.run(agent.root_agent.tools[0].get_tools()) == []


def test_tool_and_api_errors():
    tool = SimpleNamespace(name="monthly_revenue")
    result = agent.tool_error(tool, {}, context(), TimeoutError())
    assert result["isError"] is True
    assert "失敗" in result["error"]
    response = agent.model_error(context(), None, RuntimeError())
    assert "Gemini API 呼叫失敗" in response.content.parts[0].text


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert "尚未設定" in agent.check_api_key(context(), None).content.parts[0].text


def test_evidence_required_per_turn():
    ctx = context()
    final = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="invented finance")]))
    assert "未成功取得" in agent.require_evidence(ctx, final).content.parts[0].text
    agent.record_tool(SimpleNamespace(name="monthly_revenue"), {}, ctx, {"as_of": "2020-01", "rows": [{"revenue": 1}], "source": "mock"})
    assert agent.require_evidence(ctx, final) is None
    agent.begin_research(ctx)
    assert agent.require_evidence(ctx, final) is not None
    agent.record_tool(SimpleNamespace(name="monthly_revenue"), {}, ctx, {"isError": True})
    assert not ctx.state["temp:research_data_ok"]


def test_business_error_and_future_data():
    ctx = context()
    tool = SimpleNamespace(name="monthly_revenue")
    agent.record_tool(tool, {}, ctx, {"content": [{"text": '{"error":"quota","as_of":"2020-01"}'}]})
    assert not ctx.state.get("temp:research_data_ok", False)
    rejected = agent.record_tool(tool, {}, ctx, {"content": [{"text": '{"as_of":"2999-01"}'}]})
    assert rejected["isError"]
    assert not ctx.state.get("temp:research_data_ok", False)


def test_no_codex_runtime_dependency():
    root = Path(__file__).parents[1]
    text = (root / "requirements.txt").read_text() + (root / "research_agent/agent.py").read_text(encoding="utf-8")
    assert "codex" not in text.lower()
    assert "subprocess" not in text


def test_adk_web_discovery():
    from fastapi.testclient import TestClient
    from google.adk.cli.fast_api import get_fast_api_app
    from google.adk.cli.utils.agent_loader import AgentLoader

    root = str(Path(__file__).parents[1])
    assert AgentLoader(root).load_agent("research_agent").name == "esun_research_agent"
    app = get_fast_api_app(agents_dir=root, web=True, use_local_storage=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        response = client.get("/list-apps")
        assert response.status_code == 200
        assert "research_agent" in response.json()


@pytest.mark.skipif(os.getenv("RUN_TOALPHA_INTEGRATION") != "1", reason="Real network test: opt in with RUN_TOALPHA_INTEGRATION=1")
def test_real_toalpha():
    asyncio.run(real_toalpha())


async def real_toalpha():
    """No Gemini key needed. Real tools/list and calls, never mock data."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with asyncio.timeout(120):
        async with streamable_http_client(os.getenv("TOALPHA_MCP_URL", "https://toalpha.tw/mcp")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                schemas = {t.name: t.input_schema for t in listed.tools}
                assert set(agent.TOOLS) <= schemas.keys()
                for name in agent.TOOLS:
                    print("LIVE SCHEMA", name, schemas[name])
                calls = [("data_status", {}), ("resolve_stock", {"query": "2330"}),
                         ("monthly_revenue", {"stock_id": "2330", "months": 3}),
                         ("market_overview", {}),
                         ("institutional_flows", {"stock_id": "2454", "days": 5})]
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    assert not result.is_error, name
                    assert result.content, name
                    print("LIVE RESULT", name, result.model_dump_json())
    # Also verify our actual ADK toolset can discover and expose this allowlist.
    toolset = agent.create_agent().tools[0]
    try:
        tools = await toolset.get_tools()
        assert {tool.name for tool in tools} == set(agent.TOOLS)
        print("LIVE ADK TOOLSET", sorted(tool.name for tool in tools))
    finally:
        await toolset.close()
