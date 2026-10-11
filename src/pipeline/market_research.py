"""Explicit live, read-only MCP market snapshot; no historical bulk export."""

from __future__ import annotations
import json
import os
from src.data.time_safety import now_iso, aware, TAIPEI


def extract_market_response(value: dict, cutoff: str) -> dict:
    if value.get("isError"):
        raise ValueError("MCPBusinessError")
    blocks = value.get("content", [])
    payloads = [json.loads(b["text"]) for b in blocks if b.get("type") == "text"]
    if len(payloads) != 1 or not isinstance(payloads[0], dict):
        raise ValueError("MCPMarketSchemaChanged")
    payload = payloads[0]
    on = aware(cutoff).astimezone(TAIPEI).date().isoformat()
    dated = {"index": {}, "sections": {}}
    for name, row in payload.get("index", {}).items():
        if not row.get("as_of") or row["as_of"] >= on:
            raise ValueError("MCPMarketDateNotPreviousSession")
        dated["index"][name] = row
    if not dated["index"]:
        raise ValueError("MCPMarketIndexMissing")
    for name in ("institutional", "breadth", "margin"):
        row = payload.get(name)
        if row and row.get("as_of") and row["as_of"] < on:
            dated["sections"][name] = row
    dated.update(
        source=payload.get("source"),
        link=payload.get("link"),
        excluded_undated_sections=["valuation"] if "valuation" in payload else [],
    )
    return dated


async def get_live_market_snapshot() -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import (
        streamable_http_client,
        create_mcp_http_client,
    )
    import httpx2
    from research_agent.reliability import bounded_call, RetryPolicy
    from research_agent.agent import redact

    async def request():
        async with create_mcp_http_client(timeout=httpx2.Timeout(30)) as client:
            async with streamable_http_client(
                os.getenv("TOALPHA_MCP_URL", "https://toalpha.tw/mcp"),
                http_client=client,
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if "market_overview" not in {tool.name for tool in tools.tools}:
                        raise ValueError("RequiredMCPToolUnavailable")
                    response = await session.call_tool(
                        "market_overview", {}, read_timeout_seconds=30
                    )
                    return json.loads(redact(response.model_dump_json()))

    response = await bounded_call(request, RetryPolicy.from_env(), 45, "DailyMCPMarket")
    fetched = now_iso()
    payload = extract_market_response(response, fetched)
    return {
        "status": "RECEIVED",
        "tool_calls": [
            {"name": "market_overview", "arguments": {}, "response": response}
        ],
        "first_observed_at": fetched,
        "synthetic": False,
        "payload": payload,
        "note": "Runner fetched MCP snapshot; subsequent Gemini reads this captured evidence through get_market_snapshot.",
    }
