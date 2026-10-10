"""Explicitly opted-in billable test; never counts offline fixtures as Gemini."""

import asyncio
import os
from pathlib import Path

import pytest


@pytest.mark.live_e2e
def test_live_investment_decision():
    if os.getenv("RUN_LIVE_INVESTMENT") != "1":
        pytest.skip("Explicit RUN_LIVE_INVESTMENT=1 required; consumes Gemini quota")
    if not os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE":
        pytest.skip("Gemini key missing")
    if not os.getenv("LIVE_TRADE_DATE") or not os.getenv("LIVE_AS_OF"):
        pytest.skip("Explicit date/cutoff required; no fabricated competition date")
    from src.competition.configuration import load_settings
    from src.competition.models import PortfolioState
    from src.competition.serialization import write_json
    from src.data.universe import ROOT
    from src.pipeline.cli import load_local
    from src.pipeline.context import build_context
    from src.agent.investment import run_investment_decision

    settings = load_settings()
    context = build_context(
        data=load_local(ROOT / "data"),
        state=PortfolioState(),
        trade_date=os.environ["LIVE_TRADE_DATE"],
        cutoff=os.environ["LIVE_AS_OF"],
        availability=settings.availability,
        alpha=settings.alpha,
    )
    decision, trace = asyncio.run(run_investment_decision(context))
    write_json(Path("outputs/live-investment-test/trace.json"), trace)
    assert decision is not None and trace["status"] == "DECISION_PARSED"
    assert not trace["synthetic"] and trace["tool_calls"] and trace["model_events"]
    # This validates autonomous evidence/typed proposal, not official readiness.
