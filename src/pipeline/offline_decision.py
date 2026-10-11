"""Complete deterministic MOCK decision pipeline, separate from golden accounting replay."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from src.agent.decision import InvestmentDecision
from src.agent.tools import readonly_tools
from src.competition.configuration import load_settings
from src.competition.models import PortfolioState, PriceBar, TradeOrder
from src.competition.serialization import write_json
from src.competition.simulator import PortfolioSimulator
from src.data.universe import UniverseLoader
from src.portfolio.optimizer import PortfolioOptimizer
from src.strategy.availability import AvailabilityPolicy
from .context import build_context
from .workflow import competition_dry_run


async def complete_offline_dry_run(output: Path) -> dict:
    settings = load_settings()
    tickers = sorted(UniverseLoader().get_all_tickers())[:30]
    days = []
    on = date(2026, 9, 21)
    while on <= date(2026, 10, 30):
        if on.weekday() < 5:
            days.append(on.isoformat())
        on += timedelta(days=1)
    rows = []
    for index, day in enumerate(days):
        for number, ticker in enumerate(tickers):
            close = round(100 + index * (0.01 + number * 0.003), 3)
            rows.append(
                dict(
                    ticker=ticker,
                    market=UniverseLoader().get_stock_info(ticker)["market"],
                    trade_date=day,
                    observation_date=day,
                    close=close,
                    open=close,
                    high=close,
                    low=close,
                    volume=1000000,
                    turnover=int(Decimal(str(close)) * 1000000),
                    source="SYNTHETIC_FIXTURE",
                    fetched_at=f"{day}T16:00:00+08:00",
                    available_at=f"{day}T16:00:00+08:00",
                    availability_verified=True,
                    synthetic=True,
                    price_unit="TWD/share",
                    volume_unit="shares",
                    turnover_unit="TWD",
                )
            )
    state = PortfolioState()
    history = []
    for trade_date in ("2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"):
        context = build_context(
            data={"prices": rows, "flows": [], "revenue": []},
            state=state,
            trade_date=trade_date,
            cutoff=f"{trade_date}T08:30:00+08:00",
            availability=AvailabilityPolicy("synthetic_fixture"),
            alpha=settings.alpha,
        )
        # Real exchange source names must not leak into synthetic evidence.
        for observation in context.observations:
            observation["statement"] = "SYNTHETIC_FIXTURE: " + observation["statement"].replace(
                "官方", "合成"
            )
        for source in context.sources:
            source.update(
                authority="other",
                url="https://example.test/SYNTHETIC_FIXTURE",
                name="SYNTHETIC fixture evidence",
            )

        async def mock_runner(ctx, *, feedback=None):
            functions = {f.__name__: f for f in readonly_tools(ctx)}
            calls = []
            for name in (
                "get_portfolio",
                "get_market_snapshot",
                "get_rankings",
                "get_data_availability",
            ):
                functions[name]()
                calls.append({"name": name, "synthetic": True})
            targets = None
            if feedback:
                targets = feedback[0].get("suggested_targets")
            optimizer = PortfolioOptimizer(settings.optimizer, settings.policy, settings.costs)
            proposal = optimizer.optimize(
                rankings=ctx.rankings["rankings"],
                state=ctx.state,
                previous_nav=ctx.previous_nav,
                previous_closes=ctx.previous_closes,
                regime="neutral",
                proposed_targets=targets,
            )
            if proposal.status != "PROPOSED":
                return None, {
                    "status": "FAILED",
                    "error_type": "SyntheticOptimizerBlocked",
                    "run_started_at": f"{trade_date}T08:00:00+08:00",
                    "run_completed_at": f"{trade_date}T08:01:00+08:00",
                }
            obs = {
                str(r.get("values", {}).get("ticker_number")).zfill(4): r["obs_id"]
                for r in ctx.observations
            }
            refs = [ctx.observations[0]["obs_id"]]
            candidate = [r["ticker"] for r in ctx.rankings["rankings"]]
            net = sum(
                (
                    ctx.previous_closes[o.ticker] * o.shares * (1 if o.side == "BUY" else -1)
                    for o in proposal.orders
                ),
                Decimal(0),
            )
            decision = InvestmentDecision(
                trade_date=trade_date,
                data_cutoff=ctx.data_cutoff,
                regime="neutral",
                stance="neutral",
                investment_posture=(
                    "hold"
                    if abs(net) <= ctx.previous_nav * Decimal(".02")
                    else ("increase" if net > 0 else "reduce")
                ),
                candidates=candidate,
                target_weights=proposal.target_weights,
                rationale={
                    t: dict(
                        premise_refs=[obs.get(t, refs[0])],
                        logic="合成資料以固定版本因子與成本規則形成部位，僅驗證完整流程，不代表真實Gemini研究。",
                        counter_evidence=None,
                    )
                    for t in proposal.target_weights
                },
                observation_refs=refs,
                market_logic="合成市場資料以固定中性風險預算重播，缺漏法人與營收不自行補值，所有資料及模型決策明確標示為MOCK。",
                counter_evidence=None,
                cash_range=tuple(Decimal(v) for v in proposal.diagnostics["cash_range"]),
                additional_research_needed=["SYNTHETIC_FIXTURE; actual Gemini not invoked"],
                model_version="MOCK-DETERMINISTIC",
                strategy_version=ctx.strategy_version,
            )
            functions["validate_proposal"](
                json.dumps({t: str(w) for t, w in decision.target_weights.items()}), "neutral"
            )
            calls.append({"name": "validate_proposal", "synthetic": True})
            return decision, {
                "status": "DECISION_PARSED",
                "synthetic": True,
                "tool_calls": calls,
                "model_events": [],
                "run_started_at": f"{trade_date}T08:00:00+08:00",
                "run_completed_at": f"{trade_date}T08:01:00+08:00",
                "model_version": "MOCK-DETERMINISTIC",
                "decision": decision.model_dump(mode="json"),
            }

        daily = output / trade_date
        write_json(daily / "features.json", context.snapshot)
        write_json(daily / "rankings.json", context.rankings)
        report = await competition_dry_run(
            context, settings, daily, decision_runner=mock_runner, trading_days=set(days)
        )
        if not report.get("dplan_generated"):
            write_json(
                output / "summary.json",
                {
                    "status": "FAILED",
                    "synthetic": True,
                    "reason": report.get("reason", report.get("validation")),
                },
            )
            return {"status": "FAILED", "synthetic": True, "trade_date": trade_date}
        plan = __import__("json").loads(Path(report["plan_path"]).read_text(encoding="utf-8"))
        orders = [
            TradeOrder(o["ticker"], o["side"], o["shares"], o["decision_ref"])
            for o in plan["orders"]
        ]
        bars = {
            r["ticker"]: PriceBar.from_data_row(r) for r in rows if r["trade_date"] == trade_date
        }
        executed = PortfolioSimulator().execute_day(
            state,
            trade_date,
            orders,
            bars,
            trading_days=set(days),
            calendar_verified=False,
            strict=False,
            fixture_mode=True,
        )
        if executed.status != "EXECUTED":
            return {"status": "FAILED", "synthetic": True, "reason": "Fixture simulation blocked"}
        state = executed.state
        write_json(daily / "simulation.json", executed)
        write_json(daily / "portfolio.json", state)
        history.append(
            {
                "trade_date": trade_date,
                "features": len(context.snapshot.values),
                "rankings": len(context.rankings["rankings"]),
                "order_count": len(orders),
                "nav": executed.daily_nav.nav,
                "holdings": len(state.positions),
                "formal_readiness": report["status"],
            }
        )
    result = {
        "status": "SYNTHETIC_PIPELINE_PASSED",
        "synthetic": True,
        "live_model_called": False,
        "history": history,
        "terminal_nav": history[-1]["nav"],
        "formal_readiness": "BLOCKED",
        "note": "MOCK decision runner; full features/ranking/optimizer/typed decision/D-Plan/validation/execution/NAV/state carry, never live Gemini evidence",
    }
    write_json(output / "summary.json", result)
    return result
