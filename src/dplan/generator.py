from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from decimal import Decimal
from pathlib import Path

from src.competition.models import AccountingError, PortfolioState, decimal
from src.competition.orders import derive_orders
from src.competition.policy import CompetitionPolicy

if TYPE_CHECKING:
    from src.agent.decision import InvestmentDecision
    from src.agent.tools import DecisionContext


def exact_json(value) -> str:
    """Emit Decimal as JSON numbers, never convert weights through binary float."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AccountingError("Non-finite JSON number")
        return str(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + exact_json(item)
                for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(exact_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def write_dplan(directory: Path, plan: dict) -> Path:
    # It remains a local file only. Publishing requires the separate readiness gate.
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"D-Plan_{plan['team_id']}_{plan['trade_date']}.json"
    if Path(path.name).name != path.name or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for char in plan["team_id"]
    ):
        raise AccountingError("Unsafe team identifier")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(exact_json(plan) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path


class DPlanGenerator:
    def __init__(self, policy: CompetitionPolicy | None = None) -> None:
        self.policy = policy or CompetitionPolicy()

    def from_decision(
        self,
        context: "DecisionContext",
        decision: "InvestmentDecision",
        *,
        team_id: str,
        metadata: dict,
    ) -> dict:
        """Use the same captured Agent decision in generation and offline replay."""
        if (
            decision.trade_date != context.trade_date
            or decision.data_cutoff != context.data_cutoff
        ):
            raise AccountingError(
                "Agent decision date/cutoff differs from its captured context"
            )
        return self.generate(
            team_id=team_id,
            trade_date=context.trade_date,
            state=context.state,
            previous_nav=context.previous_nav,
            previous_closes=context.previous_closes,
            targets=decision.target_weights,
            sources=context.sources,
            observations=context.observations,
            market_view={
                "basis_refs": decision.observation_refs,
                "logic": decision.market_logic,
                "regime": decision.regime,
                "stance": decision.stance,
                "posture": {
                    "net_exposure_intent": decision.investment_posture,
                    "target_cash_pct_range": list(decision.cash_range),
                },
                "counter_evidence": decision.counter_evidence,
            },
            rationale={t: r.model_dump() for t, r in decision.rationale.items()},
            metadata=metadata,
        )

    def generate(
        self,
        *,
        team_id: str,
        trade_date: str,
        state: PortfolioState,
        previous_nav: Decimal,
        previous_closes: dict[str, Decimal],
        targets: dict[str, Decimal],
        sources: list[dict],
        observations: list[dict],
        market_view: dict,
        rationale: dict[str, dict],
        metadata: dict,
        funding_for: dict[str, list[str]] | None = None,
    ) -> dict:
        """All rationale/market choices must come from the captured Agent output.

        Deterministically classify actions, assign IDs and derive quantities.
        The LLM has no interface for writing or editing an order quantity.
        """
        if not team_id:
            raise AccountingError("Actual organizer-assigned team ID required")
        targets = {ticker: decimal(weight) for ticker, weight in targets.items()}
        if not set(state.positions) <= set(targets):
            raise AccountingError("Agent must address every settled holding")
        tickers = sorted(targets)
        if set(tickers) != set(rationale):
            raise AccountingError(
                "Every target, including unchanged holdings, needs Agent rationale"
            )
        raw_orders = derive_orders(
            targets, state, previous_nav, previous_closes, policy=self.policy
        )
        order_lookup = {o.ticker: o for o in raw_orders}
        inferences, decisions, holds, orders = [], [], [], []
        ids = {}
        for ticker in tickers:
            text = rationale[ticker]
            inf_id = f"I{len(inferences) + 1}"
            inferences.append(
                {
                    "inf_id": inf_id,
                    "premise_refs": text["premise_refs"],
                    "logic": text["logic"],
                    "counter_evidence": text["counter_evidence"],
                }
            )
            order = order_lookup.get(ticker)
            if order is None:
                if ticker not in state.positions:
                    raise AccountingError(
                        "Zero-weight non-holding cannot be a no-trade decision"
                    )
                holds.append(
                    {
                        "ticker": ticker,
                        "reason_refs": [inf_id],
                        "reason": text.get("summary", "維持持股，未達調整門檻。"),
                    }
                )
                continue
            old = state.positions[ticker].shares if ticker in state.positions else 0
            action = (
                "BUY"
                if order.side == "BUY" and old == 0
                else (
                    "ADD"
                    if order.side == "BUY"
                    else "SELL_ALL" if order.shares == old else "TRIM"
                )
            )
            decision_id = f"D{len(decisions) + 1}"
            ids[ticker] = decision_id
            decisions.append(
                {
                    "decision_id": decision_id,
                    "ticker": ticker,
                    "action": action,
                    "target_weight": targets[ticker],
                    "inference_refs": [inf_id],
                }
            )
            orders.append(
                {
                    "ticker": ticker,
                    "side": order.side,
                    "shares": order.shares,
                    "decision_ref": decision_id,
                }
            )
        for decision in decisions:
            funding = (funding_for or {}).get(decision["ticker"])
            if funding:
                decision["funding_for"] = [ids[ticker] for ticker in funding]
        return {
            "schema_version": "4.2",
            "doc_type": "D-Plan",
            "team_id": team_id,
            "trade_date": trade_date,
            "sources": sources,
            "observations": observations,
            "market_view": market_view,
            "inferences": inferences,
            "decisions": decisions,
            "no_trade_decisions": holds,
            "orders": orders,
            "agent_metadata": metadata,
        }
