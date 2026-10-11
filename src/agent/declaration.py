from __future__ import annotations

import json
import os
from uuid import uuid4

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from research_agent.agent import redact
from research_agent.reliability import ReliableResearchAgent
from src.competition.configuration import Settings, code_fingerprint
from src.data.time_safety import now_iso
from src.dplan.strategy_document import StrategyDeclaration


async def generate_strategy_declaration(
    settings: Settings,
) -> tuple[StrategyDeclaration | None, dict]:
    """One operation of the Investment Agent, not a cooperating second agent."""
    model = os.getenv("GEMINI_MODEL", settings.agent["default_model"])
    trace = {
        "status": "STARTED",
        "run_started_at": now_iso(),
        "model_version": model,
        "prompt_version": "strategy-declaration-1.0",
        "code_fingerprint": code_fingerprint(),
        "tool_calls": [],
    }
    if not os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE":
        trace.update(status="SKIPPED", error_type="MissingAPIKey", run_completed_at=now_iso())
        return None, trace
    instruction = """你是玉山競賽 TEAM_11586 的 Investment Agent，現在為已實作策略撰寫官方ETF說明。
以繁體中文自主產生名稱、主題、理念，不要聲稱有已證實的超額報酬或捏造研究成效。
名稱20字內，只能中文或英文字母並以「主動」開頭。主題50字內。
理念150至250字：策略運用5日及20日動能，對20日波動風險做扣分、横斷面標準化及縮尾，
由Gemini在可得資料及風險預算下自主判斷，而非固定買某產業。
僅交易官方150檔、20至30檔持股、整張，2330上限25%其他10%，現金留緩衝且低於25%。
以成本、周轉率門檻與不交易區間限制無意義調整。資料時點不明不作歷史可得假設。
法人與營收目前為補充研究資料，未通過公布時間驗證前不當作必要選股因子。
Active Share及其他官方資料未經驗證時，正式就緒輸出須阻擋。
以上是目前實作的行為與限制，不能宣称未實作的預測模型或新增特定股票推薦。
agent_generated=true；model_version及strategy_version務必使用訊息提供的實際值。
"""
    trace["instruction"] = instruction
    agent = ReliableResearchAgent(
        name="esun_investment_agent",
        model=model,
        instruction=instruction,
        generate_content_config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=StrategyDeclaration.model_json_schema(),
        ),
    )
    sessions = InMemorySessionService()
    identifier = uuid4().hex
    await sessions.create_session(
        app_name="investment_agent", user_id="declaration", session_id=identifier
    )
    runner = Runner(agent=agent, app_name="investment_agent", session_service=sessions)
    try:
        text = f"請產生策略文件JSON，model_version={model}；strategy_version={settings.alpha.version}。"
        final = ""
        usage = []
        trace["validation_attempts"] = []
        for revision in range(3):
            async for event in runner.run_async(
                user_id="declaration",
                session_id=identifier,
                new_message=types.Content(role="user", parts=[types.Part(text=text)]),
                run_config=RunConfig(max_llm_calls=1),
            ):
                if getattr(event, "usage_metadata", None):
                    usage.append(event.usage_metadata.model_dump(exclude_none=True))
                if event.is_final_response() and event.content:
                    final = "".join(
                        p.text or "" for p in event.content.parts or [] if not p.thought
                    )
            trace["validation_attempts"].append({"raw_response": redact(final)})
            try:
                declaration = StrategyDeclaration.model_validate_json(final)
                break
            except ValueError as error:
                trace["validation_attempts"][-1]["error"] = redact(str(error))[:1000]
                if revision == 2:
                    raise
                text = (
                    "請自行修正上輪JSON："
                    + str(error)[:1000]
                    + "。理念僅150至200個中文字，務必少於300字。不要使用數字作ETF名稱。"
                )
        if (
            declaration.model_version != model
            or declaration.strategy_version != settings.alpha.version
            or not declaration.agent_generated
        ):
            raise ValueError("Model/strategy provenance mismatch")
        trace.update(
            status="DECLARATION_PARSED",
            raw_response=redact(final),
            usage_metadata=usage,
            declaration=declaration.model_dump(),
        )
        return declaration, trace
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
