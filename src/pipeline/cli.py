from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path

from src.competition.configuration import code_fingerprint, load_settings
from src.competition.serialization import json_value, write_json, load_state
from src.competition.models import PortfolioState
from src.data.storage import DataStore
from src.data.universe import ROOT, UniverseLoader
from src.dplan.validation import DPlanValidator
from src.strategy.availability import AvailabilityPolicy
from src.strategy.backtest import FactorBacktester, walk_forward_splits
from src.strategy.features import FeatureEngine
from src.strategy.ranking import AlphaModel
from .offline import replay_fixture
from .context import build_context


def load_local(root: Path) -> dict[str, list[dict]]:
    store = DataStore(root)
    try:
        return {name: store.query(name) for name in ("prices", "flows", "revenue")}
    finally:
        store.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="玉山競賽：本地資料、研究回測與受控 dry-run"
    )
    result.add_argument("--data-root", type=Path, default=ROOT / "data")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("universe")
    commands.add_parser("availability")
    offline = commands.add_parser("offline-dry-run")
    offline.add_argument(
        "--output", type=Path, default=ROOT / "outputs/offline-decision-pipeline"
    )
    replay = commands.add_parser(
        "replay-decision",
        help="Verify saved inputs and regenerate an identical plan without model/API calls",
    )
    replay.add_argument("run_directory", type=Path)
    replay.add_argument("--expected-manifest-sha256")
    replay.add_argument(
        "--output", type=Path, default=ROOT / "outputs/decision-replay-report.json"
    )
    simulation = commands.add_parser("simulate")
    simulation.add_argument(
        "--fixture", type=Path, default=ROOT / "tests/fixtures/competition_case.json"
    )
    simulation.add_argument(
        "--output", type=Path, default=ROOT / "outputs/offline-replay"
    )
    feature = commands.add_parser("features")
    feature.add_argument("--as-of", required=True)
    feature.add_argument(
        "--mode",
        choices=["strict_verified", "research_assumptions", "synthetic_fixture"],
        default="strict_verified",
    )
    feature.add_argument("--output", type=Path, default=ROOT / "outputs/features.json")
    for name in ("backtest", "compare"):
        backtest = commands.add_parser(name)
        backtest.add_argument("--start", required=True)
        backtest.add_argument("--end", required=True)
        backtest.add_argument(
            "--mode",
            choices=["strict_verified", "research_assumptions"],
            default="strict_verified",
        )
        backtest.add_argument(
            "--baseline",
            choices=["equal_weight", "momentum", "multifactor"],
            default="multifactor",
        )
        backtest.add_argument(
            "--holdings", type=int, choices=[20, 22, 25, 30], default=20
        )
        backtest.add_argument("--output", type=Path, default=ROOT / "outputs/backtest")
    evaluation = commands.add_parser("walk-forward")
    evaluation.add_argument(
        "--mode",
        choices=["strict_verified", "research_assumptions"],
        default="strict_verified",
    )
    evaluation.add_argument("--rolling", action="store_true")
    evaluation.add_argument(
        "--output", type=Path, default=ROOT / "outputs/walk-forward.json"
    )
    declaration = commands.add_parser("strategy-document")
    declaration.add_argument("--live", action="store_true")
    declaration.add_argument(
        "--output", type=Path, default=ROOT / "outputs/strategy-document"
    )
    validation = commands.add_parser("validate-dplan", aliases=["compliance"])
    validation.add_argument("file", type=Path)
    validation.add_argument(
        "--settled-input",
        type=Path,
        help="JSON with state, previous_nav, previous_closes and cutoff; not authentication proof",
    )
    validation.add_argument(
        "--output", type=Path, default=ROOT / "outputs/dplan-validation.json"
    )
    live = commands.add_parser("dry-run", aliases=["decide", "generate-dplan"])
    live.add_argument("--trade-date", required=True)
    live.add_argument("--as-of", required=True)
    live.add_argument(
        "--mode",
        choices=["strict_verified", "research_assumptions"],
        default="strict_verified",
    )
    live.add_argument(
        "--live", action="store_true", help="Explicitly enables billable Gemini calls"
    )
    inventory = live.add_mutually_exclusive_group(required=True)
    inventory.add_argument(
        "--initial",
        action="store_true",
        help="Explicit preinvestment initial cash ledger",
    )
    inventory.add_argument("--state", type=Path, help="Saved settled inventory ledger")
    live.add_argument(
        "--output", type=Path, default=ROOT / "outputs/competition-dry-run"
    )
    return result


def write_backtest(report: dict, directory: Path, label: str) -> None:
    write_json(directory / f"{label}.json", report)
    metrics = report.get("metrics") or {}
    markdown = [
        f"# {label} 實際研究回測",
        "",
        f"狀態：{report['status']}；資料模式：{report['mode']}；合成：{report.get('synthetic', False)}。",
        "",
        "本報告僅記錄實際執行結果，條件式研究不等於嚴格歷史可得或競賽驗收。",
        "",
        "| 指標 | 結果 |",
        "|---|---|",
    ]
    markdown += [
        f"| {key} | {value} |"
        for key, value in metrics.items()
        if not isinstance(value, (list, dict))
    ]
    markdown += ["", "## 限制", ""] + [
        f"- {text}" for text in report.get("limitations", [])
    ]
    (directory / f"{label}.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.command = {
        "compliance": "validate-dplan",
        "decide": "dry-run",
        "generate-dplan": "dry-run",
    }.get(args.command, args.command)
    settings = load_settings()
    if args.command == "universe":
        universe = UniverseLoader()
        universe.load_universe()
        result = {
            "status": "VALID",
            "total": len(universe.get_all_tickers()),
            "TWSE": len(universe.get_twse_tickers()),
            "TPEx": len(universe.get_tpex_tickers()),
        }
    elif args.command == "offline-dry-run":
        from .offline_decision import complete_offline_dry_run

        result = asyncio.run(complete_offline_dry_run(args.output))
    elif args.command == "simulate":
        result = replay_fixture(args.fixture, args.output)
        write_json(args.output / "replay.json", result)
        result = {
            key: value
            for key, value in result.items()
            if key not in {"history", "final_state"}
        }
    elif args.command == "replay-decision":
        from .replay import replay_decision

        result = replay_decision(
            args.run_directory, expected_manifest_sha256=args.expected_manifest_sha256
        )
        write_json(args.output, result)
    elif args.command == "validate-dplan":
        plan = json.loads(
            args.file.read_text(encoding="utf-8"),
            parse_float=__import__("decimal").Decimal,
        )
        inputs = {}
        if args.settled_input:
            from decimal import Decimal

            values = json.loads(
                args.settled_input.read_text(encoding="utf-8"), parse_float=Decimal
            )
            inputs = {
                "state": load_state(values["state"]),
                "previous_nav": Decimal(str(values["previous_nav"])),
                "previous_closes": {
                    t: Decimal(str(v)) for t, v in values["previous_closes"].items()
                },
                "cutoff": values["cutoff"],
            }
        report = DPlanValidator(policy=settings.policy, costs=settings.costs).validate(
            plan,
            filename=args.file.name,
            authenticated_team=settings.competition["team_id"],
            **inputs,
        )
        result = {"status": report.status, "report": report}
        write_json(args.output, result)
    else:
        data = load_local(args.data_root)
        if args.command == "availability":
            result = {
                "rows": {name: len(rows) for name, rows in data.items()},
                "dates": {
                    name: [
                        min((r["observation_date"] for r in rows), default=None),
                        max((r["observation_date"] for r in rows), default=None),
                    ]
                    for name, rows in data.items()
                },
                "production_status": "BLOCKED",
                "gates": [
                    "Official ETF holdings and Active Share methodology",
                    "Historical corporate-action completeness",
                    "Official settled inventory reconciliation",
                    "Fee rounding and first-session organizer clarification",
                ],
            }
        elif args.command == "walk-forward":
            from src.strategy.evaluation import evaluate_walk_forward

            result = evaluate_walk_forward(
                data, mode=args.mode, expanding=not args.rolling, alpha=settings.alpha
            )
            result["code_fingerprint"] = code_fingerprint()
            write_json(args.output, result)
            result = {
                "status": result["status"],
                "fold_count": len(result["folds"]),
                "output": str(args.output),
                "evaluation_policy": result["parameter_selection"],
            }
        elif args.command == "strategy-document":
            if not args.live:
                result = {
                    "status": "SKIPPED",
                    "reason": "--live explicitly enables billable Agent text generation",
                }
            else:
                from src.agent.declaration import generate_strategy_declaration
                from src.dplan.strategy_document import fill_official_template

                declaration, trace = asyncio.run(
                    generate_strategy_declaration(settings)
                )
                write_json(args.output / "agent-trace.json", trace)
                result = {"status": trace["status"]}
                if declaration:
                    write_json(
                        args.output / "declaration.json", declaration.model_dump()
                    )
                    template = next(
                        (ROOT / "docs/official").glob("*繳交格式*.docx"), None
                    )
                    if template is None:
                        result = {
                            "status": "BLOCKED",
                            "reason": "Original organizer DOCX template is required locally; see manifest",
                        }
                    else:
                        result.update(
                            fill_official_template(
                                template, args.output / "strategy.docx", declaration
                            )
                        )
                        result["status"] = "VISUAL_QA_REQUIRED"
                write_json(args.output / "readiness.json", result)
        elif args.command == "dry-run":
            if not args.live:
                result = {
                    "status": "SKIPPED",
                    "reason": "Use --live to explicitly enable billable Gemini; use simulate for complete offline fixtures",
                }
                write_json(args.output / "readiness.json", result)
            else:
                from .workflow import competition_dry_run

                state = (
                    PortfolioState()
                    if args.initial
                    else load_state(json.loads(args.state.read_text(encoding="utf-8")))
                )
                context = build_context(
                    data=data,
                    state=state,
                    trade_date=args.trade_date,
                    cutoff=args.as_of,
                    availability=replace(settings.availability, mode=args.mode),
                    alpha=settings.alpha,
                )
                report = asyncio.run(
                    competition_dry_run(context, settings, args.output)
                )
                result = {
                    key: value
                    for key, value in report.items()
                    if key
                    not in {
                        "attempts",
                        "agent_decision",
                        "optimizer",
                        "code_fingerprint",
                    }
                }
        elif args.command == "features":
            snapshot = FeatureEngine(
                replace(settings.availability, mode=args.mode)
            ).build(**data, cutoff=args.as_of)
            ranking = AlphaModel(settings.alpha).rank(snapshot)
            write_json(
                args.output,
                {
                    "snapshot": snapshot,
                    "ranking": ranking,
                    "code_fingerprint": code_fingerprint(),
                },
            )
            result = {
                "mode": args.mode,
                "eligible_rankings": len(ranking["rankings"]),
                "stocks_with_features": len(snapshot.values),
                "output": str(args.output),
            }
        else:
            reports = []
            modes = (
                [(args.baseline, args.holdings)]
                if args.command == "backtest"
                else [
                    (baseline, count)
                    for baseline in ("equal_weight", "momentum", "multifactor")
                    for count in (20, 22, 25, 30)
                ]
            )
            for baseline, count in modes:
                report = FactorBacktester(
                    count, AvailabilityPolicy(args.mode), AlphaModel(settings.alpha)
                ).run(**data, start=args.start, end=args.end, baseline=baseline)
                report["code_fingerprint"] = code_fingerprint()
                write_backtest(report, args.output, f"{baseline}-{count}")
                reports.append(
                    {
                        key: value
                        for key, value in report.items()
                        if key not in {"daily_results", "audit", "code_fingerprint"}
                    }
                )
                print(
                    json.dumps(
                        {
                            "progress": f"{baseline}/{count}",
                            "status": report["status"],
                            "sessions": report.get("sessions_simulated", 0),
                        },
                        ensure_ascii=True,
                    )
                )
            sessions = sorted({r["trade_date"] for r in data["prices"]})
            result = {
                "reports": reports,
                "walk_forward_splits": walk_forward_splits(sessions),
                "evaluation_policy": "Parameters frozen; all outcomes reported, no final-test optimization; split metadata alone is not evidence that each fold executed",
            }
            write_json(args.output / "summary.json", result)
            result = {
                "reports": reports,
                "walk_forward_fold_count": len(result["walk_forward_splits"]),
                "output": str(args.output),
                "evaluation_policy": result["evaluation_policy"],
            }
    print(json.dumps(json_value(result), ensure_ascii=True, indent=2, allow_nan=False))
    if args.command == "replay-decision":
        return {"REPRODUCED": 0, "BLOCKED": 2}.get(result["status"], 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
