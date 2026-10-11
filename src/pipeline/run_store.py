"""Durable run receipts with an OS-held lock; no TTL guessing after a crash."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from src.competition.serialization import write_json
from src.data.time_safety import now_iso


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+b")
    try:
        file.seek(0, 2)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        raise RuntimeError("DuplicateExecution") from None
    try:
        yield
    finally:
        file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        file.close()


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def prepare(
        self, trade_date: str, *, rerun: bool, resume: str | None, inputs: dict
    ) -> tuple[Path, dict]:
        day = self.root / trade_date
        day.mkdir(exist_ok=True)
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        if resume:
            if Path(resume).name != resume:
                raise ValueError("InvalidRunId")
            directory = day / resume
            report = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if report["input_sha256"] != digest:
                raise ValueError("ResumeInputsChanged")
            if report["execution_status"] == "COMPLETED":
                raise ValueError("CompletedRunImmutable")
            for name, expected in report.get("artifact_hashes", {}).items():
                target = (directory / name).resolve()
                if (
                    not target.is_relative_to(directory.resolve())
                    or sha256(target) != expected
                ):
                    raise ValueError("ResumeArtifactChanged")
            # Agent or external side-effect stages are never silently reissued.
            if report.get("active_stage") == "decision":
                raise ValueError("InterruptedDecisionRequiresExplicitRerun")
            report["recoveries"] = report.get("recoveries", 0) + 1
            report["execution_status"] = "RUNNING"
        else:
            if not rerun and any(day.glob("*/status.json")):
                raise ValueError("ExistingRunUseResumeOrRerun")
            run_id = f"{trade_date}-{uuid4().hex[:16]}"
            directory = day / run_id
            directory.mkdir()
            report = dict(
                run_id=run_id,
                trade_date=trade_date,
                started_at=now_iso(),
                execution_status="RUNNING",
                submission_status="BLOCKED",
                inputs=inputs,
                input_sha256=digest,
                stages={},
                artifact_hashes={},
                errors=[],
                warnings=[],
                recoveries=0,
            )
        self.save(directory, report)
        return directory, report

    def save(self, directory: Path, report: dict) -> None:
        write_json(directory / "status.json", report)
        write_json(
            directory / "notification.json",
            {
                "run_id": report["run_id"],
                "execution_status": report["execution_status"],
                "submission_status": report["submission_status"],
                "message": (
                    "文件可提交"
                    if report["submission_status"] == "READY"
                    else "不可提交：請檢查阻擋原因"
                ),
                "errors": report["errors"],
                "warnings": report["warnings"],
            },
        )
        text = [
            f"# 每日執行 {report['run_id']}",
            "",
            f"執行狀態：{report['execution_status']}",
            f"正式提交：{report['submission_status']}",
            "",
            "COMPLETED 只代表執行結束；READY 才代表所有正式閘門通過。",
            "",
        ]
        for key in (
            "data_quality",
            "gemini",
            "holdings",
            "cash_ratio",
            "target_cash_ratio",
            "estimated_cash_ntd",
            "estimated_cash_ratio",
            "fundamental_coverage",
            "active_share",
            "schema",
            "semantics",
        ):
            text.append(f"- {key}：{json.dumps(report.get(key), ensure_ascii=False)}")
        text += ["", "阻擋／錯誤："] + [
            f"- {e}" for e in report["errors"] + report["warnings"]
        ]
        if report.get("decision_report_path"):
            text += [
                "",
                "逐檔決策、委託股數、估計現金：investment-report.md；委託清單：proposed-orders.csv。",
                "cash_ratio 為舊版目標權重剩餘欄位；估計結餘請看 estimated_cash_ntd／estimated_cash_ratio。",
            ]
        (directory / "report.md").write_text("\n".join(text), encoding="utf-8")


def compare_runs(left: Path, right: Path) -> dict:
    values = [
        json.loads((p / "status.json").read_text(encoding="utf-8"))
        for p in (left, right)
    ]
    keys = sorted(
        set(values[0].get("artifact_hashes", {}))
        | set(values[1].get("artifact_hashes", {}))
    )
    return {
        "run_ids": [v["run_id"] for v in values],
        "same_inputs": values[0]["input_sha256"] == values[1]["input_sha256"],
        "changed_artifacts": [
            k
            for k in keys
            if values[0].get("artifact_hashes", {}).get(k)
            != values[1].get("artifact_hashes", {}).get(k)
        ],
        "submission_status": [v["submission_status"] for v in values],
    }
