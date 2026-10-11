"""Replay captured decisions without credentials, network calls or LLM execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, TYPE_CHECKING
from zipfile import ZipFile, BadZipFile

from src.agent.decision import InvestmentDecision
from src.agent.tools import DecisionContext
from src.competition.configuration import Settings, code_fingerprint
from src.competition.models import AccountingError, TransactionCosts, decimal
from src.competition.policy import CompetitionPolicy
from src.competition.serialization import json_value, load_state, write_json
from src.data.universe import ROOT
from src.dplan.generator import DPlanGenerator, exact_json
from src.dplan.validation import DPlanValidator, DPlanValidationReport
from src.portfolio.active_share import BenchmarkHoldings
from src.portfolio.optimizer import (
    OptimizerConfig,
    PortfolioOptimizer,
    OptimizationResult,
)
from src.strategy.availability import AvailabilityPolicy
from src.strategy.features import FeatureSnapshot
from src.strategy.ranking import AlphaConfig

if TYPE_CHECKING:
    from .workflow import ReadinessEvidence


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise AccountingError(f"Replay {name} must be a JSON object")
    return value


def _typed_numbers(value: Any) -> Any:
    """Preserve Decimal evidence as a number, distinct from a numeric string."""
    if isinstance(value, Decimal):
        return {"__replay_decimal__": str(value)}
    if isinstance(value, dict):
        if set(value) == {"__replay_decimal__"}:
            raise AccountingError("Context uses a reserved replay numeric tag")
        return {k: _typed_numbers(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_typed_numbers(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def _restore_numbers(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__replay_decimal__"}:
            return decimal(value["__replay_decimal__"])
        return {k: _restore_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore_numbers(v) for v in value]
    return value


def _inside(directory: Path, relative: str) -> Path:
    path = directory / relative
    if (
        not relative
        or Path(relative).is_absolute()
        or not path.resolve().is_relative_to(directory.resolve())
    ):
        raise AccountingError("Replay evidence path escapes its saved run")
    return path


def save_decision_replay(
    directory: Path,
    *,
    context: DecisionContext,
    settings: Settings,
    decision: InvestmentDecision,
    trace: dict,
    plan_path: Path,
    proposal: OptimizationResult,
    validation: DPlanValidationReport,
    readiness: "ReadinessEvidence",
    trading_days: set[str] | None,
    original_status: str,
) -> dict:
    """Capture immutable inputs plus file hashes; never a compliance certificate.

    The original raw trace remains unchanged. Fixture runners may omit a model
    response; their separately saved typed decisions retain the synthetic label.
    """
    trace_name = f"agent-trace-{trace['revision']}.json"
    plan_name = plan_path.resolve().relative_to(directory.resolve()).as_posix()
    inputs = directory / "decision-inputs.json"
    write_json(
        inputs,
        {
            "context": _typed_numbers(asdict(context)),
            "settings": settings,
            "decision": decision.model_dump(mode="json"),
            "proposal": proposal,
            "validation": validation,
            "readiness": readiness,
            "trading_days": sorted(trading_days) if trading_days is not None else None,
        },
    )
    files = ("decision-inputs.json", trace_name, plan_name, "decision-source.zip")
    manifest = {
        "format_version": 1,
        "original_status": original_status,
        "synthetic": context.fixture,
        "trace_file": trace_name,
        "plan_file": plan_name,
        "code_fingerprint": trace["code_fingerprint"],
        "file_hashes": {name: _hash(_inside(directory, name)) for name in files},
        "limitation": "Local integrity hashes do not authenticate an external model or certify official compliance",
    }
    path = directory / "decision-replay.json"
    write_json(path, manifest)
    return {"path": str(path), "sha256": _hash(path), "format_version": 1}


def _context(raw: dict) -> DecisionContext:
    row = _mapping(_restore_numbers(raw), "context")
    row["state"] = load_state(row["state"])
    row["previous_nav"] = decimal(row["previous_nav"])
    row["previous_closes"] = {
        t: decimal(v)
        for t, v in _mapping(row["previous_closes"], "previous_closes").items()
    }
    snapshot = dict(row["snapshot"])
    for name in ("quality_issues", "excluded"):
        snapshot[name] = tuple(snapshot[name])
    row["snapshot"] = FeatureSnapshot(**snapshot)
    row["benchmark_snapshots"] = tuple(
        BenchmarkHoldings(
            **{**b, "weights": {t: decimal(v) for t, v in b["weights"].items()}}
        )
        for b in row.get("benchmark_snapshots", [])
    )
    return DecisionContext(**row)


def _settings(raw: dict) -> Settings:
    return Settings(
        competition=raw["competition"],
        policy=CompetitionPolicy(**raw["policy"]),
        costs=TransactionCosts(**raw["costs"]),
        alpha=AlphaConfig(**raw["alpha"]),
        optimizer=OptimizerConfig(**raw["optimizer"]),
        availability=AvailabilityPolicy(**raw["availability"]),
        agent=raw["agent"],
    )


def _replay(directory: Path, expected_manifest_sha256: str | None, root: Path) -> dict:
    manifest_path = directory / "decision-replay.json"
    if not manifest_path.is_file():
        return {
            "status": "BLOCKED",
            "reason": "Saved run has no replay inputs; legacy traces cannot reconstruct missing context",
            "live_model_called": False,
        }
    manifest_hash = _hash(manifest_path)
    if expected_manifest_sha256 and manifest_hash != expected_manifest_sha256:
        raise AccountingError(
            "Replay manifest differs from independently retained SHA256"
        )
    manifest = _mapping(
        json.loads(manifest_path.read_text(encoding="utf-8")), "manifest"
    )
    if manifest["format_version"] != 1:
        raise AccountingError("Unsupported replay bundle version")
    expected_files = {
        "decision-inputs.json",
        manifest["trace_file"],
        manifest["plan_file"],
        "decision-source.zip",
    }
    hashes = _mapping(manifest["file_hashes"], "file_hashes")
    if set(hashes) != expected_files or len(expected_files) != 4:
        raise AccountingError("Incomplete or conflicting replay evidence set")
    for name, expected in manifest["file_hashes"].items():
        if _hash(_inside(directory, name)) != expected:
            raise AccountingError(f"Saved replay evidence changed: {name}")
    version = _mapping(manifest["code_fingerprint"], "code_fingerprint")
    _mapping(version["file_hashes"], "source file_hashes")
    current = code_fingerprint(root)
    if current["file_hashes"] != version["file_hashes"]:
        changed = sorted(
            k
            for k in set(current["file_hashes"]) | set(version["file_hashes"])
            if current["file_hashes"].get(k) != version["file_hashes"].get(k)
        )
        return {
            "status": "BLOCKED",
            "reason": "Decision-affecting source/config differs; restore archived source before replay",
            "changed_files": changed,
            "live_model_called": False,
        }
    with ZipFile(directory / "decision-source.zip") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(
            version["file_hashes"]
        ) | {"fingerprint.json"}:
            raise AccountingError(
                "Source archive evidence set differs from fingerprint"
            )
        if json.loads(archive.read("fingerprint.json")) != version:
            raise AccountingError(
                "Source archive fingerprint differs from captured trace"
            )
        for name, expected in version["file_hashes"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise AccountingError(f"Source archive content changed: {name}")
    inputs = _mapping(
        json.loads((directory / "decision-inputs.json").read_text(encoding="utf-8")),
        "inputs",
    )
    trace = _mapping(
        json.loads(
            _inside(directory, manifest["trace_file"]).read_text(encoding="utf-8")
        ),
        "trace",
    )
    context, settings = _context(inputs["context"]), _settings(inputs["settings"])
    decision = InvestmentDecision.model_validate(inputs["decision"])
    if (
        context.fixture != manifest["synthetic"]
        or trace.get("code_fingerprint") != version
    ):
        raise AccountingError(
            "Replay mode or trace version differs from captured inputs"
        )
    if (
        trace.get("source_archive_sha256")
        != manifest["file_hashes"]["decision-source.zip"]
    ):
        raise AccountingError("Trace source archive hash differs")
    if trace.get("status") != "DECISION_PARSED":
        raise AccountingError("Saved model run did not produce a parsed decision")
    if trace.get("decision") is not None:
        if InvestmentDecision.model_validate(trace["decision"]) != decision:
            raise AccountingError("Captured Agent decision differs from replay targets")
    elif not context.fixture:
        raise AccountingError("Actual trace lacks captured typed model decision")
    proposal = PortfolioOptimizer(
        settings.optimizer, settings.policy, settings.costs
    ).optimize(
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
        or json_value(proposal) != inputs["proposal"]
    ):
        raise AccountingError(
            "Recomputed optimizer differs from approved captured proposal"
        )
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
    plan = DPlanGenerator(settings.policy).from_decision(
        context, decision, team_id=settings.competition["team_id"], metadata=metadata
    )
    plan_bytes = (exact_json(plan) + "\n").encode("utf-8")
    if (
        hashlib.sha256(plan_bytes).hexdigest()
        != manifest["file_hashes"][manifest["plan_file"]]
    ):
        raise AccountingError(
            "Regenerated D-Plan differs from saved Agent-generated orders/evidence"
        )
    readiness = inputs["readiness"]
    validation = DPlanValidator(policy=settings.policy, costs=settings.costs).validate(
        plan,
        state=context.state,
        previous_nav=context.previous_nav,
        previous_closes=context.previous_closes,
        cutoff=context.data_cutoff,
        authenticated_team=settings.competition["team_id"],
        trading_days=(
            set(inputs["trading_days"]) if inputs["trading_days"] is not None else None
        ),
        inventory_reconciled=readiness["inventory_reconciled"],
        evidence_verified=readiness["observations_verified"],
        strict=True,
    )
    if json_value(validation) != inputs["validation"]:
        raise AccountingError("Recomputed validation differs from original run")
    return {
        "status": "REPRODUCED",
        "live_model_called": False,
        "synthetic": context.fixture,
        "trade_date": context.trade_date,
        "original_readiness": manifest["original_status"],
        "manifest_sha256": manifest_hash,
        "independent_hash_checked": expected_manifest_sha256 is not None,
        "same_source_content": True,
        "same_optimizer": True,
        "same_dplan_bytes": True,
        "same_validation": True,
        "orders": len(plan["orders"]),
        "note": "Replays captured deterministic processing, not stochastic Gemini generation; local integrity is not external authenticity or competition certification",
    }


def replay_decision(
    directory: Path, *, expected_manifest_sha256: str | None = None, root: Path = ROOT
) -> dict:
    """Return actionable failure; do not execute archived code or modify a plan."""
    try:
        return _replay(directory, expected_manifest_sha256, root)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ArithmeticError,
        BadZipFile,
    ) as error:
        from research_agent.agent import redact

        return {
            "status": "FAILED",
            "reason": type(error).__name__,
            "details": redact(str(error))[:1000],
            "live_model_called": False,
        }
