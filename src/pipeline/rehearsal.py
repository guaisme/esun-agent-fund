"""Supervised live engineering rehearsal; never certifies official submission."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from src.competition.configuration import code_fingerprint, load_settings
from src.competition.models import PortfolioState
from src.competition.serialization import load_state, write_json, json_value
from src.data.time_safety import TAIPEI, aware, now_iso
from src.data.universe import ROOT, UniverseLoader
from .run_store import RunStore, exclusive_lock, sha256


def rehearsal_run(args) -> dict:
    """Separate registry/output and bounded child process, with explicit billable opt-in."""
    output = args.output.resolve()
    if output.is_relative_to(
        (ROOT / "outputs/daily").resolve()
    ) or output.is_relative_to(args.data_root.resolve()):
        raise ValueError("RehearsalOutputMustBeSeparate")
    store = RunStore(output)
    try:
        with exclusive_lock(args.data_root / "metadata/rehearsal.lock"):
            on = datetime.now(TAIPEI).date().isoformat()
            registry_path = args.data_root / "metadata/rehearsal-registry.json"
            registry = (
                json.loads(registry_path.read_text(encoding="utf-8"))
                if registry_path.exists()
                else {}
            )
            if registry.get(on) and not args.rerun:
                raise ValueError("ExistingRehearsalUseRerun")
            inputs = dict(
                live=args.live,
                initial=args.initial,
                data_root=str(args.data_root.resolve()),
                code_fingerprint=code_fingerprint(),
                require_fundamentals=not getattr(args, "allow_price_only", False),
            )
            for key in ("calendar", "state", "benchmarks"):
                path = getattr(args, key, None)
                inputs[key] = str(path.resolve()) if path else None
                if path and path.is_file():
                    inputs[key + "_sha256"] = sha256(path)
                if path and path.is_dir():
                    inputs[key + "_hashes"] = {
                        p.relative_to(path).as_posix(): sha256(p)
                        for p in path.rglob("*")
                        if p.is_file()
                    }
            directory, report = store.prepare(
                on, rerun=args.rerun, resume=None, inputs=inputs
            )
            report.update(
                execution_mode="LIVE_ENGINEERING_REHEARSAL",
                submission_status="BLOCKED",
                code_fingerprint=inputs["code_fingerprint"],
                run_directory=str(directory),
            )
            registry.setdefault(on, []).append(str(directory))
            write_json(registry_path, registry)
            if not args.live:
                report.update(
                    execution_status="COMPLETED",
                    gemini="NOT_ENABLED",
                    completed_at=now_iso(),
                )
                report["errors"].append("LiveModeNotExplicitlyEnabled")
                store.save(directory, report)
                return report
            store.save(directory, report)
            request = {
                "run_directory": str(directory),
                "output_root": str(output),
                "inputs": inputs,
            }
            write_json(directory / "request.json", request)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "src.pipeline.rehearsal",
                    str(directory / "request.json"),
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                process.wait(timeout=900)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                process.kill()
                process.wait()
                report = json.loads(
                    (directory / "status.json").read_text(encoding="utf-8")
                )
                report.update(
                    execution_status="FAILED",
                    submission_status="BLOCKED",
                    completed_at=now_iso(),
                )
                report["errors"].append(type(error).__name__)
                store.save(directory, report)
            report = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if report["execution_status"] == "RUNNING":
                report.update(
                    execution_status="FAILED",
                    submission_status="BLOCKED",
                    completed_at=now_iso(),
                )
                report["errors"].append("WorkerExitedWithoutReport")
                store.save(directory, report)
            return report
    except (ValueError, RuntimeError, OSError) as error:
        return {
            "execution_status": "FAILED",
            "submission_status": "BLOCKED",
            "errors": [
                (
                    str(error)
                    if isinstance(error, (ValueError, RuntimeError))
                    else type(error).__name__
                )
            ],
        }


def _attach_market(context, market: dict):
    """Attach the actual receipt; undated sections were excluded by the MCP parser."""
    payload = market["payload"]
    source_id = f"S{len(context.sources) + 1}"
    date = max(row["as_of"] for row in payload["index"].values())
    sources = context.sources + [
        {
            "source_id": source_id,
            "authority": "other",
            "url": payload["link"],
            "name": "ToAlpha MCP market_overview",
            "content_as_of": date + "T00:00:00+08:00",
            "fetched_at": market["first_observed_at"],
        }
    ]
    observations = list(context.observations)
    for name, row in payload["index"].items():
        observations.append(
            {
                "obs_id": f"O{len(observations)+1}",
                "source_ref": [source_id],
                "topic": "market_index",
                "statement": f"{row['as_of']} {name}：收盤 {row['close']} 點。",
                "values": {k: row[k] for k in ("close", "change", "change_pct")},
            }
        )
    return replace(
        context,
        sources=sources,
        observations=observations,
        market_evidence={
            **payload,
            "execution_mode": "ENGINEERING_REHEARSAL",
            "inventory_reconciled": False,
            "submission_status": "BLOCKED",
        },
    )


async def _execute(request: dict, report: dict) -> None:
    from dotenv import load_dotenv
    from src.data.storage import DataStore
    from src.data.quality import DataQualityValidator
    from src.data.providers.benchmark import ReviewedFileBenchmarkProvider
    from src.strategy.availability import AvailabilityPolicy
    from src.dplan.validation import DPlanValidator
    from src.dplan.generator import DPlanGenerator, exact_json
    from src.agent.decision import InvestmentDecision
    from .daily import read_calendar
    from .context import build_context
    from .market_research import get_live_market_snapshot
    from .workflow import competition_dry_run

    inputs = request["inputs"]
    if not inputs["live"]:
        raise ValueError("LiveModeNotExplicitlyEnabled")
    load_dotenv(ROOT / "research_agent/.env", override=False)
    if (
        not os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE"
    ):
        raise ValueError("MissingGeminiAPIKey")
    directory = Path(request["run_directory"])
    if code_fingerprint() != inputs["code_fingerprint"]:
        raise ValueError("SourceChangedBeforeRehearsal")
    for key in ("calendar", "state"):
        if inputs.get(key) and sha256(Path(inputs[key])) != inputs[key + "_sha256"]:
            raise ValueError("RehearsalInputChanged")
    if inputs.get("benchmarks"):
        benchmark_root = Path(inputs["benchmarks"])
        actual = {
            p.relative_to(benchmark_root).as_posix(): sha256(p)
            for p in benchmark_root.rglob("*")
            if p.is_file()
        }
        if actual != inputs.get("benchmarks_hashes", {}):
            raise ValueError("RehearsalInputChanged")
    clock = datetime.now(TAIPEI)
    on = clock.date().isoformat()
    if on != report["trade_date"]:
        raise ValueError("RehearsalDateChanged")
    calendar = read_calendar(Path(inputs["calendar"]), clock.year)
    sessions = calendar.sessions(f"{clock.year}-01-01", f"{clock.year}-12-31")
    previous = max(d for d in sessions if d < on)
    state = (
        load_state(json.loads(Path(inputs["state"]).read_text(encoding="utf-8")))
        if inputs.get("state")
        else PortfolioState()
    )
    if state.last_trade_date and state.last_trade_date != previous:
        raise ValueError("ResearchLedgerNotPreviousSession")
    report["warnings"] += [
        "ResearchOnlyInventoryNotOrganizerReconciled",
        "EngineeringRehearsalNeverSubmittable",
        "TPExCalendarUnverified",
    ]
    if not inputs.get("state") and not inputs.get("initial"):
        raise ValueError("ExplicitResearchInventoryRequired")
    if on not in sessions:
        report["warnings"].append("RehearsalOnNonTradingDay")
    root = Path(inputs["data_root"])
    if not (root / "metadata/catalog.sqlite").exists():
        raise ValueError("LocalMarketDatabaseMissing")
    database = DataStore(root)
    try:
        original = {
            dataset: database.query(dataset)
            for dataset in ("prices", "flows", "revenue")
        }
    finally:
        database.close()
    # Validate/PIT-filter before spending any model or MCP quota.
    preflight_cutoff = now_iso()
    policy = AvailabilityPolicy("strict_verified")
    data, exclusions = {}, {}
    validator = DataQualityValidator(UniverseLoader())
    issues = []
    for dataset, rows in original.items():
        data[dataset], excluded = policy.select(rows, dataset, preflight_cutoff)
        exclusions[dataset] = {
            "selected": len(data[dataset]),
            "excluded": len(excluded),
        }
        issues.extend(validator.validate(data[dataset], dataset))
    write_json(
        directory / "data-quality.json", {"issues": issues, "selection": exclusions}
    )
    report["data_quality"] = {
        "selection": exclusions,
        "errors": sum(i["level"] == "ERROR" for i in issues),
    }
    if report["data_quality"]["errors"]:
        raise ValueError("DataQualityErrors")
    coverage = {
        r["ticker"]
        for r in data["prices"]
        if r["trade_date"] == previous and r.get("close") and r["close"] > 0
    }
    missing = sorted(set(UniverseLoader().get_all_tickers()) - coverage)
    report["previous_session"] = previous
    report["previous_session_missing_tickers"] = missing
    if missing:
        raise ValueError("PreviousSessionCoverageIncomplete")
    if any(
        r["trade_date"] not in sessions for r in data["prices"] if r["market"] == "TWSE"
    ):
        raise ValueError("OfficialCalendarPriceConflict")
    settings = load_settings()
    if inputs.get("require_fundamentals", False):
        settings = replace(
            settings,
            alpha=replace(
                settings.alpha,
                version="rehearsal-revenue-flow-1.0",
                missing_policy="exclude",
                weights={
                    "momentum_20d": 0.25,
                    "volatility_20d": -0.15,
                    "foreign_flow_20d": 0.15,
                    "trust_flow_5d": 0.15,
                    "revenue_yoy": 0.20,
                    "revenue_acceleration": 0.10,
                },
            ),
        )
    # Context feasibility is checked before any external call.
    context = build_context(
        data=data,
        state=state,
        trade_date=on,
        cutoff=preflight_cutoff,
        availability=policy,
        alpha=settings.alpha,
        calendar=sessions,
    )
    from .fundamentals import fundamental_coverage

    fundamental_report = fundamental_coverage(
        context.snapshot.values, UniverseLoader().get_all_tickers()
    )
    previous_month = (
        clock.year if clock.month > 1 else clock.year - 1,
        clock.month - 1 if clock.month > 1 else 12,
    )
    current_revenue = {
        r["ticker"]
        for r in data["revenue"]
        if (r["revenue_year"], r["revenue_month"]) == previous_month
    }
    fundamental_report["required_revenue_period"] = "%04d-%02d" % previous_month
    fundamental_report["missing_latest_revenue"] = sorted(
        set(UniverseLoader().get_all_tickers()) - current_revenue
    )
    if fundamental_report["missing_latest_revenue"]:
        fundamental_report["status"] = "INCOMPLETE"
    write_json(directory / "fundamental-coverage.json", fundamental_report)
    report["fundamental_coverage"] = {
        k: v for k, v in fundamental_report.items() if k != "missing"
    }
    if (
        inputs.get("require_fundamentals", False)
        and fundamental_report["status"] != "COMPLETE"
    ):
        report["errors"].append("FundamentalCoverageIncomplete")
        raise ValueError("FundamentalCoverageIncomplete")
    if fundamental_report["status"] != "COMPLETE":
        report["warnings"].append("ExplicitIncompleteFundamentalRehearsal")
    if len(context.rankings["rankings"]) < 20:
        raise ValueError("InsufficientPITSafeRankings")
    market = await get_live_market_snapshot()
    write_json(directory / "mcp-market.json", market)
    if any(r["as_of"] != previous for r in market["payload"]["index"].values()):
        raise ValueError("MCPMarketSnapshotStale")
    cutoff = now_iso()
    if (
        aware(market["first_observed_at"]) > aware(cutoff)
        or aware(cutoff).date().isoformat() != on
    ):
        raise ValueError("MCPEvidenceAfterCutoffOrDateChanged")
    # Database snapshot remains frozen; advancing the receipt cutoff adds no rows.
    context = build_context(
        data=data,
        state=state,
        trade_date=on,
        cutoff=cutoff,
        availability=policy,
        alpha=settings.alpha,
        calendar=sessions,
    )
    snapshots, benchmark_report = ReviewedFileBenchmarkProvider(
        Path(inputs["benchmarks"])
        if inputs.get("benchmarks")
        else directory / "missing-benchmarks"
    ).load(cutoff)
    context = _attach_market(
        replace(
            context,
            benchmark_snapshots=tuple(snapshots),
            benchmark_evidence={
                **benchmark_report,
                "calculation_method": settings.competition["active_share_method"],
                "method_confirmed": settings.policy.active_share_method_confirmed,
            },
        ),
        market,
    )
    write_json(directory / "frozen-data.json", data)
    write_json(directory / "context.json", context)
    write_json(directory / "settings.json", settings)
    write_json(directory / "benchmarks.json", benchmark_report)
    report.update(
        data_cutoff=cutoff, active_share=benchmark_report, active_stage="decision"
    )
    RunStore(Path(request["output_root"])).save(directory, report)
    # Preserve the original formal validation including period/timestamp gates.
    result = await competition_dry_run(context, settings, directory / "decision")
    write_json(directory / "workflow.json", result)
    report["workflow_status"] = result["status"]
    report["formal_gates"] = result.get("gates", {})
    report["formal_validation"] = result.get("validation")
    decision = result.get("agent_decision")
    report["gemini"] = "DECISION_PARSED" if decision else result.get("reason", "FAILED")
    if result["status"] in {"FAILED", "SKIPPED"} or not decision:
        raise ValueError("InvestmentWorkflowFailed")
    attempts = result.get("attempts", [])
    if (
        context.fixture
        or not attempts
        or attempts[-1].get("synthetic") is not False
        or not attempts[-1].get("model_events")
        or not attempts[-1].get("tool_calls")
        or decision["model_version"].startswith("MOCK")
    ):
        raise ValueError("LiveModelEvidenceMissing")
    plan_path = (
        Path(result["plan_path"])
        if result.get("plan_path")
        else directory / "decision/invalid-dplan-audit.json"
    )
    saved_plan = json.loads(
        plan_path.read_text(encoding="utf-8"), parse_float=__import__("decimal").Decimal
    )
    # Generic audit JSON serializes Decimal as strings. Regenerate from the exact
    # typed decision/metadata, never patch orders or cast weights to float.
    plan = DPlanGenerator(settings.policy).from_decision(
        context,
        InvestmentDecision.model_validate(decision),
        team_id=settings.competition["team_id"],
        metadata=saved_plan["agent_metadata"],
    )
    # Engineering validation uses actual run completion as receipt time; it does
    # not overwrite formal validation against the earlier frozen evidence cutoff.
    completion = plan["agent_metadata"]["run_completed_at"]
    if aware(completion) > aware(now_iso()) or aware(completion) < aware(cutoff):
        raise ValueError("InvalidRehearsalCompletionTimestamp")
    validation = DPlanValidator(policy=settings.policy, costs=settings.costs).validate(
        plan,
        state=state,
        previous_nav=context.previous_nav,
        previous_closes=context.previous_closes,
        cutoff=completion,
        authenticated_team=settings.competition["team_id"],
        strict=True,
    )
    write_json(directory / "rehearsal-validation.json", validation)
    (directory / "rehearsal-dplan-audit.json").write_text(
        exact_json(plan) + "\n", encoding="utf-8", newline="\n"
    )
    report.update(
        schema=validation.schema_valid,
        semantics=validation.status,
        holdings=sum(float(v) > 0 for v in decision["target_weights"].values()),
        cash_ratio=1 - sum(float(v) for v in decision["target_weights"].values()),
        target_cash_ratio=1
        - sum(float(v) for v in decision["target_weights"].values()),
        estimated_cash_ntd=json_value(result.get("optimizer", {}))
        .get("diagnostics", {})
        .get("cash"),
        estimated_cash_ratio=json_value(result.get("optimizer", {}))
        .get("diagnostics", {})
        .get("cash_ratio"),
        dplan_audit_path=str(directory / "rehearsal-dplan-audit.json"),
        rehearsal_checks_passed=validation.schema_valid
        and not any(i.severity == "HARD_FAIL" for i in validation.issues),
    )
    report["errors"].extend(
        name for name, passed in result.get("gates", {}).items() if not passed
    )
    if not report["rehearsal_checks_passed"]:
        raise ValueError("RehearsalDPlanValidationFailed")
    from .decision_report import export_decision_report

    export_decision_report(directory, directory)
    report["decision_report_path"] = str(directory / "investment-report.md")


def run_rehearsal_worker(request: dict) -> dict:
    directory = Path(request["run_directory"])
    store = RunStore(Path(request["output_root"]))
    report = json.loads((directory / "status.json").read_text(encoding="utf-8"))
    try:
        asyncio.run(_bounded_execute(request, report))
        report["execution_status"] = "COMPLETED"
    except Exception as error:
        report["execution_status"] = "FAILED"
        report["errors"].append(type(error).__name__)
        if isinstance(error, ValueError) and str(error) in {
            "MissingGeminiAPIKey",
            "LocalMarketDatabaseMissing",
            "PreviousSessionCoverageIncomplete",
            "OfficialCalendarPriceConflict",
            "DataQualityErrors",
            "MCPMarketSnapshotStale",
            "SourceChangedBeforeRehearsal",
            "RehearsalInputChanged",
            "ResearchLedgerNotPreviousSession",
            "LiveModeNotExplicitlyEnabled",
            "InvestmentWorkflowFailed",
            "RehearsalDPlanValidationFailed",
            "ExplicitResearchInventoryRequired",
            "LiveModelEvidenceMissing",
            "InsufficientPITSafeRankings",
        }:
            report["errors"].append(str(error))
    report.update(
        submission_status="BLOCKED", completed_at=now_iso(), active_stage=None
    )
    report["artifact_hashes"] = {
        p.relative_to(directory).as_posix(): sha256(p)
        for p in directory.rglob("*")
        if p.is_file()
        and p.name not in {"status.json", "report.md", "notification.json"}
    }
    store.save(directory, report)
    return report


async def _bounded_execute(request: dict, report: dict) -> None:
    async with asyncio.timeout(900):
        await _execute(request, report)


if __name__ == "__main__":
    result = run_rehearsal_worker(
        json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    )
    raise SystemExit(1 if result["execution_status"] == "FAILED" else 0)
