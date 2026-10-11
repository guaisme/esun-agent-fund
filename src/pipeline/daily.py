"""Daily orchestration around existing modules; never creates settlement or bypasses gates."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import yaml

from src.competition.calendar import TWSECalendarProvider
from src.competition.configuration import code_fingerprint, load_settings
from src.competition.models import PortfolioState
from src.competition.serialization import write_json, load_state, json_value
from src.data.time_safety import TAIPEI, aware, now_iso
from src.data.universe import ROOT, UniverseLoader
from .run_store import RunStore, exclusive_lock, sha256


def operations_config(path: Path | None = None) -> dict:
    value = yaml.safe_load(
        (path or ROOT / "config/operations.yaml").read_text(encoding="utf-8")
    )
    if (
        value["timezone"] != "Asia/Taipei"
        or not 1 <= float(value["overall_deadline_seconds"]) <= 3600
    ):
        raise ValueError("InvalidOperationsPolicy")
    for key in (
        "decision_cutoff",
        "scheduled_time",
        "submission_open",
        "submission_close",
    ):
        if value.get(key) is not None:
            datetime.strptime(value[key], "%H:%M")
    if bool(value.get("submission_open")) != bool(value.get("submission_close")):
        raise ValueError("IncompleteSubmissionWindow")
    if (
        value.get("submission_open")
        and value["submission_open"] >= value["submission_close"]
    ):
        raise ValueError("InvalidSubmissionWindow")
    return value


def window_check(now: datetime, trade_date: str, config: dict) -> dict:
    if now.tzinfo is None:
        raise ValueError("SubmissionClockRequiresTimezone")
    local = now.astimezone(TAIPEI)
    if local.date().isoformat() != trade_date:
        return {"status": "BLOCKED", "reason": "RunDateDiffersFromToday"}
    if not config.get("submission_close") or not config.get("submission_window_source"):
        return {
            "status": "BLOCKED",
            "reason": "OfficialSubmissionWindowUnknown",
            "may_research": True,
        }
    clock = local.strftime("%H:%M")
    if clock < config["submission_open"]:
        return {
            "status": "BLOCKED",
            "reason": "BeforeSubmissionWindow",
            "may_research": True,
        }
    if clock >= config["submission_close"]:
        return {
            "status": "BLOCKED",
            "reason": "SubmissionDeadlinePassed",
            "may_research": False,
        }
    return {
        "status": "IN_WINDOW",
        "source": config["submission_window_source"],
        "may_research": True,
    }


def read_calendar(path: Path, year: int):
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if envelope["url"] != TWSECalendarProvider.URL:
        raise ValueError("UnsupportedCalendarAuthority")
    raw_path = Path(envelope["raw_ref"])
    if sha256(raw_path) != envelope["raw_sha256"]:
        raise ValueError("CalendarOriginalHashMismatch")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if (
        raw["url"] != envelope["url"]
        or raw["payload"] != envelope["payload"]
        or raw["fetched_at"] != envelope["fetched_at"]
    ):
        raise ValueError("CalendarOriginalContentMismatch")
    calendar = TWSECalendarProvider.parse(
        envelope["payload"], year, envelope["fetched_at"], str(path)
    )
    if aware(calendar.fetched_at) > datetime.now(TAIPEI):
        raise ValueError("FutureCalendarReceipt")
    return calendar


def daily_run(args) -> dict:
    """Parent keeps an OS lock and kills the worker at the total deadline.

    HTTP and ADK retain their own bounded per-request policies. Killing a child
    cannot certify an incomplete model decision; resume never reissues it.
    """
    config = operations_config(args.operations)
    today = datetime.now(TAIPEI).date().isoformat()
    on = args.trade_date or today
    datetime.strptime(on, "%Y-%m-%d")
    if args.live and on != today:
        return {
            "execution_status": "COMPLETED",
            "submission_status": "BLOCKED",
            "errors": ["HistoricalLiveExecutionForbidden"],
        }
    store = RunStore(args.output)
    try:
        # A different output folder must not create a second concurrent runner.
        registry_path = args.data_root / "metadata/daily-registry.json"
        with exclusive_lock(args.data_root / "metadata/daily.lock"):
            registry = (
                json.loads(registry_path.read_text(encoding="utf-8"))
                if registry_path.exists()
                else {}
            )
            if registry.get(on) and not args.rerun and not args.resume:
                raise ValueError("ExistingRunUseResumeOrRerun")
            fingerprint = code_fingerprint()
            inputs = {
                "trade_date": on,
                "live": args.live,
                "update_data": args.update_data,
                "data_root": str(args.data_root.resolve()),
                "code_version": fingerprint["code_version"],
                "operations": config,
            }
            for label in ("state", "calendar", "benchmarks", "settlement"):
                path = getattr(args, label, None)
                inputs[label] = str(path.resolve()) if path else None
                if path and path.is_file():
                    inputs[label + "_sha256"] = sha256(path)
                if path and path.is_dir():
                    inputs[label + "_hashes"] = {
                        p.relative_to(path).as_posix(): sha256(p)
                        for p in sorted(path.rglob("*"))
                        if p.is_file()
                    }
            inputs["initial"] = args.initial
            settled_directory = (
                getattr(args, "settled_directory", None)
                or args.data_root / "metadata/settled"
            )
            inputs["settled_directory"] = str(settled_directory.resolve())
            inputs["settled_directory_hashes"] = {
                p.name: sha256(p) for p in sorted(settled_directory.glob("*.json"))
            }
            directory, report = store.prepare(
                on, rerun=args.rerun, resume=args.resume, inputs=inputs
            )
            registry.setdefault(on, [])
            if str(directory.resolve()) not in registry[on]:
                registry[on].append(str(directory.resolve()))
            write_json(registry_path, registry)
            report["code_fingerprint"] = fingerprint
            request = {
                "run_directory": str(directory.resolve()),
                "output_root": str(args.output.resolve()),
                "inputs": inputs,
            }
            write_json(directory / "request.json", request)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "src.pipeline.daily_worker",
                    str((directory / "request.json").resolve()),
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            timeout = float(config["overall_deadline_seconds"])
            if args.live and on == today:
                limits = [config["decision_cutoff"]]
                if config.get("submission_close"):
                    limits.append(config["submission_close"])
                remaining = min(
                    (
                        aware(f"{on}T{clock}:00+08:00") - datetime.now(TAIPEI)
                    ).total_seconds()
                    for clock in limits
                )
                timeout = max(0.01, min(timeout, remaining))
            try:
                process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                process.kill()
                process.wait(timeout=10)
                report = json.loads(
                    (directory / "status.json").read_text(encoding="utf-8")
                )
                report.update(
                    execution_status="FAILED",
                    submission_status="BLOCKED",
                    completed_at=now_iso(),
                )
                report["errors"].append("WorkerInterruptedOrOverallDeadlineExceeded")
                store.save(directory, report)
            report = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if report["execution_status"] == "RUNNING":
                report.update(
                    execution_status="FAILED",
                    submission_status="BLOCKED",
                    completed_at=now_iso(),
                )
                report["errors"].append("WorkerExitedWithoutCompletion")
                store.save(directory, report)
            report["report_directory"] = str(directory)
            return report
    except (RuntimeError, ValueError, OSError) as error:
        # Fixed internal codes only; arbitrary API exceptions/credentials never printed.
        known = {
            "DuplicateExecution",
            "ExistingRunUseResumeOrRerun",
            "CompletedRunImmutable",
            "ResumeInputsChanged",
            "ResumeArtifactChanged",
            "InterruptedDecisionRequiresExplicitRerun",
            "InvalidRunId",
        }
        code = str(error) if str(error) in known else type(error).__name__
        return {
            "execution_status": "COMPLETED",
            "submission_status": "BLOCKED",
            "errors": [code],
        }


def run_worker(request: dict) -> dict:
    from src.data.storage import DataStore
    from src.data.http import HttpClient
    from src.data.download import Downloader
    from src.data.providers.twse import TWSEProvider
    from src.data.providers.tpex import TPExProvider
    from src.data.quality import DataQualityValidator
    from src.data.session_quality import session_quality
    from src.data.providers.benchmark import ReviewedFileBenchmarkProvider
    from src.portfolio.active_share import ActiveETFLoader
    from src.strategy.availability import AvailabilityPolicy
    from .context import build_context
    from .workflow import competition_dry_run

    directory, inputs = Path(request["run_directory"]), request["inputs"]
    store = RunStore(Path(request["output_root"]))
    report = json.loads((directory / "status.json").read_text(encoding="utf-8"))
    settings = load_settings()
    on, config = inputs["trade_date"], inputs["operations"]
    started = time.monotonic()

    def checkpoint(name: str, operation):
        filename = name + ".json"
        if name in report["stages"]:
            return json.loads((directory / filename).read_text(encoding="utf-8"))
        report["active_stage"] = name
        store.save(directory, report)
        result = json_value(operation())
        write_json(directory / filename, result)
        report["artifact_hashes"][filename] = sha256(directory / filename)
        report["stages"][name] = {
            "completed_at": now_iso(),
            "elapsed_seconds": time.monotonic() - started,
        }
        report["active_stage"] = None
        store.save(directory, report)
        return result

    def finish(reason: str | None = None):
        if reason and reason not in report["errors"]:
            report["errors"].append(reason)
        report.update(execution_status="COMPLETED", completed_at=now_iso())
        # Recheck at completion, not just before potentially slow model calls.
        window = window_check(datetime.now(TAIPEI), on, config)
        report["submission_window"] = window
        if window["status"] != "IN_WINDOW":
            report["submission_status"] = "BLOCKED"
        store.save(directory, report)
        return report

    try:
        checkpoint(
            "preflight",
            lambda: {
                "window": window_check(datetime.now(TAIPEI), on, config),
                "competition_period": settings.competition["start_date"]
                <= on
                <= settings.competition["end_date"],
            },
        )
        if (
            not settings.competition["start_date"]
            <= on
            <= settings.competition["end_date"]
        ):
            return finish("OutsideCompetitionPeriod")
        if (
            window_check(datetime.now(TAIPEI), on, config).get("may_research")
            is not True
        ):
            return finish("OutsidePermittedExecutionWindow")
        calendar_path = Path(inputs["calendar"]) if inputs["calendar"] else None
        if calendar_path is None:
            return finish("MissingOfficialCalendar")
        calendar = read_calendar(calendar_path, int(on[:4]))
        sessions = calendar.sessions(f"{on[:4]}-01-01", f"{on[:4]}-12-31")
        checkpoint(
            "calendar",
            lambda: {
                "TWSE": "OPEN" if on in sessions else "CLOSED",
                "TPEx": "UNVERIFIED",
                "source_sha256": sha256(calendar_path),
            },
        )
        if on not in sessions:
            return finish("TWSEClosedNoTradingDecision")
        previous = max(d for d in sessions if d < on)
        state_path = (
            Path(inputs["state"])
            if inputs["state"]
            else Path(inputs["settled_directory"]) / (previous + ".json")
        )
        if state_path.exists():
            state = load_state(json.loads(state_path.read_text(encoding="utf-8")))
            if state.last_trade_date != previous:
                return finish("PortfolioStateNotPreviousSession")
        elif inputs["initial"] and on == min(
            d for d in sessions if d >= settings.competition["start_date"]
        ):
            state = PortfolioState()
        else:
            return finish("MissingPreviousSettledPortfolioState")
        checkpoint(
            "inventory",
            lambda: {
                "state": state,
                "reconciled": False,
                "reason": "OfficialSettlementExportNotIntegrated",
            },
        )
        report["holdings"] = len(state.positions)
        report["warnings"] = sorted(
            set(
                report["warnings"]
                + [
                    "OfficialSettlementUnverified",
                    "TPExCalendarUnverified",
                    "OvernightUSMarketAdapterMissing",
                    "HistoricalPublicationAndCorporateActionsIncomplete",
                ]
            )
        )

        def capture():
            database = DataStore(Path(inputs["data_root"]))
            try:
                download = None
                if inputs["update_data"]:
                    first = (
                        (
                            datetime.fromisoformat(previous)
                            - timedelta(
                                days=int(config["download_lookback_calendar_days"])
                            )
                        )
                        .date()
                        .isoformat()
                    )
                    http = HttpClient(Path(inputs["data_root"]) / "raw", refresh=False)
                    download = Downloader(
                        UniverseLoader(),
                        database,
                        {"TWSE": TWSEProvider(http), "TPEx": TPExProvider(http)},
                    ).download(first, previous)
                data = {
                    kind: database.query(kind)
                    for kind in ("prices", "flows", "revenue")
                }
                quality = DataQualityValidator(UniverseLoader()).report(
                    database, "prices", end=previous
                )
                session_report = session_quality(
                    data["prices"],
                    quality["unverified_or_failed_days"],
                    {"TWSE": calendar},
                )
                deadline_cutoff = aware(f"{on}T{config['decision_cutoff']}:00+08:00")
                cutoff = min(datetime.now(TAIPEI), deadline_cutoff).isoformat()
                return dict(
                    data=data,
                    data_quality=quality,
                    session_quality=session_report,
                    download=download,
                    data_cutoff=cutoff,
                )
            finally:
                database.close()

        captured = checkpoint("data", capture)
        report["data_quality"] = {
            key: captured["data_quality"][key]
            for key in (
                "stocks_with_data",
                "rows",
                "quality_counts",
                "below_target",
                "missing_stocks",
            )
        }
        report["data_cutoff"] = captured["data_cutoff"]
        if captured["data_quality"]["quality_counts"]["ERROR"]:
            return finish("DataQualityErrors")
        if captured["session_quality"]["counts"].get("CALENDAR_CONFLICT"):
            return finish("OfficialCalendarPriceConflict")
        if captured.get("download") and captured["download"].get("failures"):
            return finish("DataUpdateIncomplete")
        # Both markets must actually have previous-session observations; stale data is not silently accepted.
        coverage = {
            s["ticker"]
            for s in captured["data"]["prices"]
            if s["trade_date"] == previous
            and aware(s["available_at"]) <= aware(captured["data_cutoff"])
        }
        missing = sorted(set(UniverseLoader().get_all_tickers()) - coverage)
        report["previous_session_missing_tickers"] = missing
        if missing:
            return finish("PreviousSessionCoverageIncomplete")
        if inputs["live"]:
            if (
                not os.getenv("GOOGLE_API_KEY")
                or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE"
            ):
                report["gemini"] = "MISSING_API_KEY"
                return finish("MissingGeminiAPIKey")
            from .market_research import get_live_market_snapshot

            if datetime.now(TAIPEI) >= aware(
                f"{on}T{config['decision_cutoff']}:00+08:00"
            ):
                return finish("DecisionCutoffPassed")
            market = checkpoint(
                "mcp_market", lambda: asyncio.run(get_live_market_snapshot())
            )
            # Advance to the actual evidence receipt, never beyond configured cutoff.
            report["data_cutoff"] = min(
                datetime.now(TAIPEI),
                aware(f"{on}T{config['decision_cutoff']}:00+08:00"),
            ).isoformat()
            if aware(market["first_observed_at"]) > aware(report["data_cutoff"]):
                return finish("MCPEvidenceAfterDecisionCutoff")
        else:
            market = None
        cutoff_receipt = checkpoint(
            "cutoff", lambda: {"data_cutoff": report["data_cutoff"]}
        )
        report["data_cutoff"] = cutoff_receipt["data_cutoff"]
        snapshots, benchmark_report = ReviewedFileBenchmarkProvider(
            Path(inputs["benchmarks"])
            if inputs["benchmarks"]
            else directory / "missing-benchmarks"
        ).load(report["data_cutoff"])
        checkpoint("benchmarks", lambda: benchmark_report)
        report["active_share"] = benchmark_report
        context = build_context(
            data=captured["data"],
            state=state,
            trade_date=on,
            cutoff=report["data_cutoff"],
            availability=AvailabilityPolicy("strict_verified"),
            alpha=settings.alpha,
            calendar=sessions,
        )
        report["cash_ratio"] = str(state.cash / context.previous_nav)
        if inputs.get("settlement"):
            from decimal import Decimal
            from src.competition.reconciliation import (
                OfficialSettlement,
                SettlementReconciler,
            )

            receipt_path = Path(inputs["settlement"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            settlement = OfficialSettlement(
                receipt["team_id"],
                receipt["trade_date"],
                Decimal(receipt["cash"]),
                receipt["positions"],
                Decimal(receipt["nav"]),
                (receipt_path.parent / receipt["original_file"]).resolve(),
                receipt["original_sha256"],
                receipt["retrieved_at"],
                False,
            )
            reconciliation = checkpoint(
                "settlement",
                lambda: SettlementReconciler().reconcile(
                    state,
                    context.previous_nav,
                    settlement,
                    settings.competition["team_id"],
                ),
            )
            report["settlement"] = reconciliation
            if any(
                issue != "Organizer export authenticity has not been verified"
                for issue in reconciliation["issues"]
            ):
                return finish("OrganizerLedgerMismatch")
        context = replace(
            context,
            benchmark_snapshots=tuple(snapshots),
            benchmark_evidence={
                **benchmark_report,
                "calculation_method": settings.competition["active_share_method"],
                "method_confirmed": settings.policy.active_share_method_confirmed,
            },
        )
        if market:
            if any(
                row["as_of"] != previous for row in market["payload"]["index"].values()
            ):
                return finish("MCPMarketSnapshotStale")
            source_id = f"S{len(context.sources)+1}"
            sources = context.sources + [
                {
                    "source_id": source_id,
                    "authority": "other",
                    "url": market["payload"]["link"],
                    "name": "ToAlpha MCP market_overview",
                    "content_as_of": previous + "T00:00:00+08:00",
                    "fetched_at": market["first_observed_at"],
                }
            ]
            observations = list(context.observations)
            for name, row in market["payload"]["index"].items():
                observations.append(
                    {
                        "obs_id": f"O{len(observations)+1}",
                        "source_ref": [source_id],
                        "topic": "market_index",
                        "statement": f"{previous} {name}：收盤 {row['close']} 點；指數變動 {row['change']} 點。",
                        "values": {
                            "close": row["close"],
                            "change": row["change"],
                            "change_pct": row["change_pct"],
                        },
                    }
                )
            context = replace(
                context,
                market_evidence=market["payload"],
                sources=sources,
                observations=observations,
            )
        checkpoint("features", lambda: context.snapshot)
        checkpoint("rankings", lambda: context.rankings)
        if not inputs["live"]:
            report["gemini"] = "NOT_ENABLED"
            return finish("LiveModeNotExplicitlyEnabled")
        if (
            window_check(datetime.now(TAIPEI), on, config).get("may_research")
            is not True
        ):
            return finish("SubmissionDeadlinePassed")
        # Existing workflow retains every formal gate; no synthetic evidence or override.
        result = checkpoint(
            "decision",
            lambda: asyncio.run(
                competition_dry_run(
                    context, settings, directory / "decision", trading_days=sessions
                )
            ),
        )
        for path in sorted((directory / "decision").rglob("*")):
            if path.is_file():
                report["artifact_hashes"][path.relative_to(directory).as_posix()] = (
                    sha256(path)
                )
        decision = result.get("agent_decision", {})
        report.update(
            gemini="DECISION_PARSED" if decision else result.get("reason", "FAILED"),
            holdings=sum(
                float(v) > 0 for v in decision.get("target_weights", {}).values()
            ),
            cash_ratio=(
                1 - sum(float(v) for v in decision.get("target_weights", {}).values())
                if decision
                else None
            ),
            schema=result.get("validation", {}).get("schema_valid"),
            semantics=result.get("validation", {}).get("status"),
            submission_status="BLOCKED",
            workflow_status=result["status"],
        )
        # Formal evidence acquisition is still incomplete; a workflow status alone cannot certify READY.
        report["errors"] += [
            name for name, passed in result.get("gates", {}).items() if not passed
        ]
        if result["status"] == "FAILED":
            raise ValueError("InvestmentWorkflowFailed")
        return finish("FormalEvidenceIncomplete")
    except Exception as error:
        report.update(
            execution_status="FAILED",
            submission_status="BLOCKED",
            completed_at=now_iso(),
        )
        report["errors"].append(type(error).__name__)
        store.save(directory, report)
        return report
