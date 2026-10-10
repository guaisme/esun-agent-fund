from __future__ import annotations

import json
import os
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types

from research_agent.agent import redact
from research_agent.reliability import ReliableResearchAgent
from src.data.time_safety import now_iso
from .decision import InvestmentDecision, parse_decision
from .tools import DecisionContext, readonly_tools

PROMPT_VERSION = "investment-prompt-1.0"
INSTRUCTION = """你是 TEAM_11586 玉山競賽的投資決策 Agent，以繁體中文分析。
先實際呼叫 get_portfolio、get_market_snapshot、get_rankings、get_data_availability，
根據工具證據自主決定 regime、候選股票、目標權重、交易或持有意圖及理由。
禁止捏造金融事實、資料日期或來源；缺資料須在 additional_research_needed 列明。
工具提供的 observation ID 是唯一可引用事實；推論和反證寫在 rationale／market_logic。
不可從當日成交／收盤資料作盤前決策。不可寫委託股數，股數由官方公式機械推導。
不得預設偏好半導體，應以實際訊號及風險證據判斷。
首日全現金須建立20至30檔，不能用全現金 hold 當合規備援。2330上限25%，其餘10%。
現金不得負且低於NAV25%，留緩衝；每個現有持股必須在 target_weights 與 rationale 中，
全部賣出填0。無交易時也須逐一提出續抱理由，不能忽略既有持股。
提交前用 validate_proposal 檢查；修正需自主確認並在 rationale 說明，不得隱藏更動。
最後只輸出符合提供之 InvestmentDecision JSON schema 的單一JSON，不加其他文字。
model_version 必須填實際模型，strategy_version 必須填 context 提供之版本。
"""


def create_investment_agent(
    context: DecisionContext, model: str | None = None
) -> ReliableResearchAgent:
    model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    contract = InvestmentDecision.model_json_schema()

    def limit_tools(tool, args, tool_context):
        count = tool_context.state.get("investment_tool_calls", 0) + 1
        tool_context.state["investment_tool_calls"] = count
        if count > 20:
            raise RuntimeError("Investment tool-call limit exceeded")
        return None

    return ReliableResearchAgent(
        name="esun_investment_agent",
        model=model,
        description="PIT-safe competition investment decisions with read-only evidence tools",
        instruction=INSTRUCTION
        + "\n輸出結構："
        + json.dumps(contract, ensure_ascii=False)
        + f"\ntrade_date={context.trade_date}; data_cutoff={context.data_cutoff}; model_version={model}; strategy_version={context.strategy_version}",
        tools=readonly_tools(context),
        before_tool_callback=limit_tools,
    )


async def run_investment_decision(
    context: DecisionContext, *, feedback: list[dict] | None = None, model: str | None = None
) -> tuple[InvestmentDecision | None, dict]:
    trace = {
        "status": "STARTED",
        "run_started_at": now_iso(),
        "prompt_version": PROMPT_VERSION,
        "data_cutoff": context.data_cutoff,
        "mode": context.snapshot.mode,
        "tool_calls": [],
        "tool_results": [],
        "model_events": [],
        "feedback": feedback or [],
        "synthetic": context.fixture,
    }
    if not os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE":
        trace.update(status="SKIPPED", error_type="MissingAPIKey", run_completed_at=now_iso())
        return None, trace
    agent = create_investment_agent(context, model)
    trace["model_version"] = str(agent.model)
    sessions = InMemorySessionService()
    session_id = uuid4().hex
    await sessions.create_session(
        app_name="investment_agent", user_id="local", session_id=session_id
    )
    runner = Runner(agent=agent, app_name="investment_agent", session_service=sessions)
    text = "請使用工具取得資料，自主產生完整投資決策JSON。"
    if feedback:
        text += "\n上輪檢查回饋，請重新評估並確認修正：" + json.dumps(
            feedback, ensure_ascii=False, default=str
        )
    final = ""
    try:
        async for event in runner.run_async(
            user_id="local",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
            run_config=RunConfig(max_llm_calls=8),
        ):
            usage = getattr(event, "usage_metadata", None)
            if usage:
                trace["model_events"].append(usage.model_dump(exclude_none=True))
            for part in event.content.parts or [] if event.content else []:
                if part.function_call:
                    trace["tool_calls"].append(
                        {
                            "name": part.function_call.name,
                            "args": part.function_call.args,
                            "timestamp": now_iso(),
                        }
                    )
                if part.function_response:
                    trace["tool_results"].append(
                        {
                            "name": part.function_response.name,
                            "response": part.function_response.response,
                            "timestamp": now_iso(),
                        }
                    )
            if event.is_final_response() and event.content:
                final = "".join(
                    part.text or "" for part in event.content.parts or [] if not part.thought
                )
        trace["raw_response"] = redact(final)
        decision = parse_decision(final)
        called = {item["name"] for item in trace["tool_calls"]}
        if (
            not {
                "get_portfolio",
                "get_market_snapshot",
                "get_rankings",
                "get_data_availability",
                "validate_proposal",
            }
            <= called
        ):
            raise ValueError("Required evidence and validation tools not actually called")
        if (
            decision.trade_date != context.trade_date
            or decision.data_cutoff != context.data_cutoff
            or decision.model_version != str(agent.model)
            or decision.strategy_version != context.strategy_version
        ):
            raise ValueError("Agent date/cutoff/model/strategy mismatch")
        valid_obs = {r["obs_id"] for r in context.observations}
        if not set(decision.observation_refs) <= valid_obs or any(
            not set(r.premise_refs) <= valid_obs for r in decision.rationale.values()
        ):
            raise ValueError("Agent invented observation references")
        trace.update(status="DECISION_PARSED", decision=decision.model_dump(mode="json"))
        return decision, trace
    except Exception as error:
        trace.update(
            status="FAILED",
            error_type=type(error).__name__,
            error_message=redact(str(error))[:1000],
        )
        return None, trace
    finally:
        trace["run_completed_at"] = now_iso()
        await runner.close()
