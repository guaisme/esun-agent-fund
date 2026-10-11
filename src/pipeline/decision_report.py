"""Readable export of saved decisions; does not rerun APIs or simulate fills."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.data.universe import UniverseLoader


def export_decision_report(run_directory: Path, output: Path) -> Path:
    run_directory, output = Path(run_directory), Path(output)

    def read(name: str) -> dict:
        return json.loads((run_directory / name).read_text(encoding="utf-8"))

    workflow, context, status = (
        read("workflow.json"),
        read("context.json"),
        read("status.json"),
    )
    decision = workflow.get("agent_decision")
    if not decision:
        raise ValueError("SavedInvestmentDecisionMissing")
    optimizer = workflow.get("optimizer", {})
    diagnostics = optimizer.get("diagnostics", {})
    orders = optimizer.get("orders", [])
    observations = {r["obs_id"]: r for r in context.get("observations", [])}
    universe = UniverseLoader()
    output.mkdir(parents=True, exist_ok=True)

    def safe(value) -> str:
        return (
            str(value if value is not None else "未提供")
            .replace("|", "\\|")
            .replace("\n", " ")
        )

    def pct(value) -> str:
        return f"{float(value):.2%}" if value is not None else "未提供"

    target_cash = 1 - sum(float(v) for v in decision["target_weights"].values())
    lines = [
        "# 投資決策與委託演練報告",
        "",
        "**沒有實際成交：以下是模型決策、預計委託與成本估計，不是交易績效或正式提交文件。**",
        "",
        f"執行：{status.get('run_id')}；正式提交：BLOCKED。",
        f"模型：{decision.get('model_version')}；資料凍結時間：{context.get('data_cutoff')}。",
        f"市場判斷：{decision.get('regime')}；姿態：{decision.get('stance')}。",
        "",
        f"市場分析：{decision.get('market_logic')}",
        f"反證：{decision.get('counter_evidence')}",
        "",
        "## 資金與資料",
        "",
        f"- 初始／輸入現金：{context.get('state', {}).get('cash')} 元。",
        f"- 目標權重剩餘現金比例：{pct(target_cash)}。",
        f"- 委託後估計現金：{diagnostics.get('cash')} 元；占輸入 NAV：{pct(diagnostics.get('cash_ratio'))}。",
        f"- 估計委託金額：{workflow.get('validation', {}).get('net_exposure_ntd')} 元。",
        f"- 估計手續費：{diagnostics.get('commission')} 元；交易稅：{diagnostics.get('tax')} 元。",
        f"- 壓力情境估計現金：{diagnostics.get('stress_cash')} 元。",
        "- 假設價格與成本不能保證成交；成交均價、實際費用及正式結算須另行取得。",
        f"- 資料使用：{json.dumps(status.get('data_quality', {}).get('selection', {}), ensure_ascii=False)}。",
        "- 財報 EPS、資產負債、現金流尚未接入；價格因子不能證明獲利或產業回穩。",
        "",
        "## 逐檔投資決策",
        "",
        "| 股票 | 模型目標權重 | Optimizer 預估權重 | 理由 | 引用觀察 |",
        "|---|---:|---:|---|---|",
    ]
    for ticker, weight in sorted(decision["target_weights"].items()):
        rationale = decision.get("rationale", {}).get(ticker, {})
        refs = rationale.get("premise_refs", [])
        lines.append(
            f"| {ticker} {safe(universe.get_stock_info(ticker)['name'])} | {pct(weight)} | {pct(diagnostics.get('estimated_weights', {}).get(ticker))} | {safe(rationale.get('logic'))} | {safe(', '.join(refs))} |"
        )
    lines += [
        "",
        "## 預計委託（未成交）",
        "",
        "| 股票 | 方向 | 股數 | 整張數 |",
        "|---|---|---:|---:|",
    ]
    for order in orders:
        lines.append(
            f"| {order['ticker']} {safe(universe.get_stock_info(order['ticker'])['name'])} | {order['side']} | {order['shares']} | {int(order['shares']) / 1000:g} |"
        )
    lines += ["", "## 引用資料與來源", ""]
    used = set(decision.get("observation_refs", []))
    for rationale in decision.get("rationale", {}).values():
        used.update(rationale.get("premise_refs", []))
    for identifier in sorted(used):
        row = observations.get(identifier, {})
        lines += [
            f"- {identifier}：{row.get('statement', '找不到觀察')}；來源 {row.get('source_ref', [])}；數值 {json.dumps(row.get('values', {}), ensure_ascii=False)}"
        ]
    for source in context.get("sources", []):
        lines.append(
            f"- {source.get('source_id')}：{source.get('name')}；{source.get('url')}；資料日期 {source.get('content_as_of')}；取得時間 {source.get('fetched_at')}。"
        )
    lines += ["", "## 尚待驗證", ""]
    lines += [
        f"- {reason}"
        for reason in status.get("errors", []) + status.get("warnings", [])
    ]
    lines += [
        f"- 模型要求補充：{item}"
        for item in decision.get("additional_research_needed", [])
    ]
    lines += [
        "",
        "## 稽核檔案位置",
        "",
        "- status.json／report.md：執行摘要；workflow.json：決策、Optimizer、正式驗證。",
        "- context.json／frozen-data.json：當時可見資料；decision/agent-trace-0.json：模型及工具紀錄。",
        "- rehearsal-dplan-audit.json／rehearsal-validation.json：工程 D-Plan 與檢查。",
        f"- 原始 run directory：{run_directory}",
    ]
    target = output / "investment-report.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (output / "proposed-orders.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ticker", "name", "side", "shares", "lots", "execution_status"],
        )
        writer.writeheader()
        for row in orders:
            writer.writerow(
                dict(
                    ticker=row["ticker"],
                    name=universe.get_stock_info(row["ticker"])["name"],
                    side=row["side"],
                    shares=row["shares"],
                    lots=int(row["shares"]) / 1000,
                    execution_status="NOT_EXECUTED",
                )
            )
    return target
