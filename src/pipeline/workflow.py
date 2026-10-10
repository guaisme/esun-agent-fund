from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable

from src.agent.decision import InvestmentDecision
from src.agent.investment import run_investment_decision
from src.agent.tools import DecisionContext
from src.competition.configuration import (
    Settings,
    code_fingerprint,
    archive_source,
    configure_agent_environment,
)
from src.competition.serialization import write_json
from src.data.time_safety import aware
from src.dplan.generator import DPlanGenerator, write_dplan
from src.dplan.validation import DPlanValidator
from src.portfolio.optimizer import PortfolioOptimizer


@dataclass(frozen=True)
class ReadinessEvidence:
    calendar_verified: bool = False
    calendar_conflicts_absent: bool = False
    corporate_actions_complete: bool = False
    inventory_reconciled: bool = False
    active_share_passed: bool = False
    observations_verified: bool = False
    official_strategy_frozen: bool = False
    live_model_verified: bool = False


async def _competition_dry_run(
    context: DecisionContext,
    settings: Settings,
    output: Path,
    *,
    readiness: ReadinessEvidence | None = None,
    trading_days: set[str] | None = None,
    decision_runner: Callable[
        ..., Awaitable[tuple[InvestmentDecision | None, dict]]
    ] = run_investment_decision,
) -> dict:
    """Research can run with missing evidence, but no successful-looking READY.

    Agent target repairs are returned as explicit feedback and require a new
    bounded model decision. Saved D-Plan orders are never manually patched.
    """
    output.mkdir(parents=True, exist_ok=True)
    evidence = readiness or ReadinessEvidence()
    configure_agent_environment(settings)
    version = code_fingerprint()
    archive_hash = archive_source(output / "decision-source.zip", version)
    attempts = []
    feedback = None
    decision = None
    optimizer = PortfolioOptimizer(settings.optimizer, settings.policy, settings.costs)
    max_revisions = min(int(settings.agent["max_decision_revisions"]), 3)
    for revision in range(max_revisions + 1):
        decision, trace = await decision_runner(context, feedback=feedback)
        trace["code_fingerprint"] = version
        trace["source_archive_sha256"] = archive_hash
        trace["revision"] = revision
        attempts.append(trace)
        write_json(output / f"agent-trace-{revision}.json", trace)
        if decision is None:
            if (
                trace.get("error_type")
                in {"ValidationError", "JSONDecodeError", "ValueError"}
                and revision < max_revisions
            ):
                feedback = [
                    {
                        "type": "invalid_agent_output",
                        "error_type": trace["error_type"],
                        "details": trace.get(
                            "error_message",
                            "Invalid typed output; use exact schema and one rationale per ticker",
                        ),
                    }
                ]
                continue
            status = "SKIPPED" if trace["status"] == "SKIPPED" else "FAILED"
            result = {
                "status": status,
                "reason": trace.get("error_type", "NoValidDecision"),
                "attempts": attempts,
                "dplan_generated": False,
            }
            write_json(output / "readiness.json", result)
            return result
        if not set(context.state.positions) <= set(decision.target_weights):
            feedback = [
                {
                    "type": "missing_holdings",
                    "tickers": sorted(
                        set(context.state.positions) - set(decision.target_weights)
                    ),
                }
            ]
            continue
        proposal = optimizer.optimize(
            rankings=context.rankings["rankings"],
            state=context.state,
            previous_nav=context.previous_nav,
            previous_closes=context.previous_closes,
            regime=decision.regime,
            proposed_targets=decision.target_weights,
            cash_range=decision.cash_range,
            **context.optimizer_evidence_inputs(),
        )
        if (
            proposal.status != "PROPOSED"
            or proposal.target_weights != decision.target_weights
        ):
            feedback = [
                {
                    "type": "optimizer_feedback",
                    "status": proposal.status,
                    "diagnostics": proposal.diagnostics,
                    "suggested_targets": proposal.target_weights,
                    "modifications": proposal.modifications,
                }
            ]
            continue
        break
    else:
        result = {
            "status": "FAILED",
            "reason": "Bounded decision revision exhausted",
            "attempts": attempts,
            "feedback": feedback,
            "dplan_generated": False,
        }
        write_json(output / "readiness.json", result)
        return result
    trace = attempts[-1]
    metadata = {
        "model_provider": (
            "other"
            if context.fixture or decision.model_version.startswith("MOCK")
            else "google"
        ),
        "model_version": decision.model_version,
        "run_started_at": trace["run_started_at"],
        "run_completed_at": trace["run_completed_at"],
        "code_version": version["code_version"],
        "tool_use_count": len(trace.get("tool_calls", [])),
    }
    plan = DPlanGenerator(settings.policy).generate(
        team_id=settings.competition["team_id"],
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
    validator = DPlanValidator(policy=settings.policy, costs=settings.costs)
    validation = validator.validate(
        plan,
        state=context.state,
        previous_nav=context.previous_nav,
        previous_closes=context.previous_closes,
        cutoff=context.data_cutoff,
        authenticated_team=settings.competition["team_id"],
        trading_days=trading_days,
        inventory_reconciled=evidence.inventory_reconciled,
        evidence_verified=evidence.observations_verified,
        strict=True,
    )
    gates = {
        "competition_period": settings.competition["start_date"]
        <= context.trade_date
        <= settings.competition["end_date"],
        "schema_semantics": validation.status == "VALID",
        "strict_data_mode": context.snapshot.mode == "strict_verified"
        and not context.fixture,
        "live_model": trace["status"] == "DECISION_PARSED"
        and metadata["model_provider"] == "google"
        and bool(trace.get("model_events")),
        "calendar": evidence.calendar_verified and evidence.calendar_conflicts_absent,
        "corporate_actions": evidence.corporate_actions_complete,
        "settled_inventory": evidence.inventory_reconciled,
        "active_share": evidence.active_share_passed
        and settings.policy.active_share_method_confirmed
        and proposal.diagnostics.get("active_share", {}).get("status") == "EVALUATED"
        and not proposal.diagnostics["active_share"]["synthetic"],
        "observations": evidence.observations_verified,
        "strategy_declaration": evidence.official_strategy_frozen,
        "fee_rounding": settings.policy.cash_rounding_confirmed,
        "first_session": settings.competition["competition_first_session_confirmed"],
    }
    ready = all(gates.values())
    # Invalid schema/semantics are audit artifacts, never competition filenames.
    plan_path = None
    if validation.schema_valid and not any(
        i.severity == "HARD_FAIL" for i in validation.issues
    ):
        plan_path = write_dplan(output / ("ready" if ready else "draft"), plan)
    else:
        write_json(output / "invalid-dplan-audit.json", plan)
    result = {
        "status": "READY" if ready else "BLOCKED",
        "gates": gates,
        "validation": validation,
        "plan_path": str(plan_path) if plan_path else None,
        "dplan_generated": plan_path is not None,
        "agent_decision": decision.model_dump(mode="json"),
        "attempts": attempts,
        "optimizer": proposal,
        "code_fingerprint": version,
    }
    write_json(output / "readiness.json", result)
    return result


async def competition_dry_run(
    context: DecisionContext, settings: Settings, output: Path, **kwargs
) -> dict:
    """A single wall-clock deadline spans all bounded decision revisions."""
    try:
        async with asyncio.timeout(float(settings.agent["task_deadline"])):
            return await _competition_dry_run(context, settings, output, **kwargs)
    except TimeoutError:
        result = {
            "status": "FAILED",
            "reason": "Overall dry-run deadline exceeded",
            "dplan_generated": False,
        }
        write_json(output / "readiness.json", result)
        return result
    except (ValueError, KeyError, ArithmeticError) as error:
        from research_agent.agent import redact

        result = {
            "status": "FAILED",
            "reason": type(error).__name__,
            "details": redact(str(error))[:1000],
            "dplan_generated": False,
        }
        write_json(output / "readiness.json", result)
        return result
