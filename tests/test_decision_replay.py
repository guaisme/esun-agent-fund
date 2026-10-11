"""MOCK captured decisions only; no live Gemini or financial data verification."""

import asyncio
import hashlib
import json
from decimal import Decimal

import pytest

from src.competition.configuration import load_settings
from src.pipeline.replay import replay_decision
from src.pipeline.workflow import competition_dry_run
from test_workflow import fixture, trace


@pytest.mark.parametrize("field", ["file_hashes", "code_fingerprint", "source_hashes"])
def test_malformed_evidence_objects_produce_diagnostics_not_uncaught_errors(
    saved_run, field
):
    directory, original = saved_run
    path = directory / "decision-replay.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if field == "source_hashes":
        manifest["code_fingerprint"]["file_hashes"] = "MOCK-INVALID"
    else:
        manifest[field] = "MOCK-INVALID"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = replay_decision(directory)
    assert result["status"] == "FAILED" and "JSON object" in result["details"]


@pytest.fixture
def saved_run(tmp_path):
    context, decision = fixture()
    # Decimal observation numbers must not turn into quoted numeric strings.
    context.observations[0]["values"]["exact_mock_number"] = Decimal("100.123000")

    async def runner(context, feedback=None):
        return decision, {**trace(), "decision": decision.model_dump(mode="json")}

    result = asyncio.run(
        competition_dry_run(context, load_settings(), tmp_path, decision_runner=runner)
    )
    assert result["dplan_generated"] and result["status"] == "BLOCKED"
    return tmp_path, result


def rehash(directory, relative):
    path = directory / "decision-replay.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["file_hashes"][relative] = hashlib.sha256(
        (directory / relative).read_bytes()
    ).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_saved_decision_reproduces_exact_plan_and_validation_without_live(saved_run):
    directory, original = saved_run
    result = replay_decision(
        directory, expected_manifest_sha256=original["replay_manifest"]["sha256"]
    )
    assert result["status"] == "REPRODUCED", result
    assert (
        result["same_dplan_bytes"]
        and result["same_validation"]
        and result["same_optimizer"]
    )
    assert result["synthetic"] and not result["live_model_called"]
    plan_bytes = (
        directory / "draft" / "D-Plan_TEAM_11586_2026-10-27.json"
    ).read_bytes()
    assert plan_bytes.endswith(b"}\n") and not plan_bytes.endswith(b"\r\n")
    assert (
        result["original_readiness"] == "BLOCKED" and result["independent_hash_checked"]
    )


@pytest.mark.parametrize(
    "entry", ["decision-inputs.json", "trace", "plan", "decision-source.zip"]
)
def test_any_saved_evidence_mutation_is_rejected(saved_run, entry):
    directory, original = saved_run
    manifest = json.loads(
        (directory / "decision-replay.json").read_text(encoding="utf-8")
    )
    relative = manifest[entry + "_file"] if entry in {"trace", "plan"} else entry
    path = directory / relative
    path.write_bytes(path.read_bytes() + b"\nMOCK tampering")
    result = replay_decision(directory)
    assert result["status"] == "FAILED" and "evidence changed" in result["details"]


def test_rehashed_manual_order_edit_still_differs_from_actual_decision(saved_run):
    directory, original = saved_run
    manifest = json.loads(
        (directory / "decision-replay.json").read_text(encoding="utf-8")
    )
    path = directory / manifest["plan_file"]
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["orders"][0]["shares"] += 1000
    path.write_text(json.dumps(plan), encoding="utf-8")
    rehash(directory, manifest["plan_file"])
    result = replay_decision(directory)
    assert (
        result["status"] == "FAILED"
        and "Regenerated D-Plan differs" in result["details"]
    )


def test_rehashed_changed_target_is_not_a_captured_agent_decision(saved_run):
    directory, original = saved_run
    path = directory / "decision-inputs.json"
    inputs = json.loads(path.read_text(encoding="utf-8"))
    ticker = next(iter(inputs["decision"]["target_weights"]))
    inputs["decision"]["target_weights"][ticker] = "0.039"
    path.write_text(json.dumps(inputs), encoding="utf-8")
    rehash(directory, "decision-inputs.json")
    result = replay_decision(directory)
    assert (
        result["status"] == "FAILED" and "Agent decision differs" in result["details"]
    )


def test_changed_code_requires_restoring_exact_decision_source(saved_run, monkeypatch):
    import src.pipeline.replay as replay

    directory, original = saved_run
    actual = replay.code_fingerprint()
    changed = {
        **actual,
        "file_hashes": {
            **actual["file_hashes"],
            "src/pipeline/replay.py": "MOCK-DIFFERENT",
        },
    }
    monkeypatch.setattr(replay, "code_fingerprint", lambda root: changed)
    result = replay_decision(directory)
    assert result["status"] == "BLOCKED" and result["changed_files"] == [
        "src/pipeline/replay.py"
    ]


def test_legacy_run_is_not_filled_with_guessed_context(tmp_path):
    result = replay_decision(tmp_path)
    assert result["status"] == "BLOCKED" and "legacy" in result["reason"]


def test_independently_retained_manifest_hash_rejects_manifest_mutation(saved_run):
    directory, original = saved_run
    path = directory / "decision-replay.json"
    path.write_bytes(path.read_bytes() + b"\n")
    result = replay_decision(
        directory, expected_manifest_sha256=original["replay_manifest"]["sha256"]
    )
    assert (
        result["status"] == "FAILED" and "independently retained" in result["details"]
    )


def test_evidence_paths_cannot_read_outside_saved_run(saved_run):
    directory, original = saved_run
    path = directory / "decision-replay.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    old = manifest["trace_file"]
    manifest["trace_file"] = "../outside-MOCK.json"
    manifest["file_hashes"][manifest["trace_file"]] = manifest["file_hashes"].pop(old)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = replay_decision(directory)
    assert result["status"] == "FAILED" and "escapes" in result["details"]


def test_cli_replays_saved_run_and_writes_local_report(saved_run, capsys):
    from src.pipeline.cli import main

    directory, original = saved_run
    output = directory / "replay-report.json"
    assert main(["replay-decision", str(directory), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "REPRODUCED" and not report["live_model_called"]


def test_captured_context_cutoff_cannot_be_replaced_by_agent_output(tmp_path):
    context, decision = fixture()
    decision = decision.model_copy(update={"data_cutoff": "2026-10-27T08:31:00+08:00"})

    async def runner(context, feedback=None):
        return decision, trace()

    result = asyncio.run(
        competition_dry_run(context, load_settings(), tmp_path, decision_runner=runner)
    )
    assert result["status"] == "FAILED" and not result["dplan_generated"]
    assert "cutoff differs" in result["details"]


def test_cli_missing_saved_inputs_returns_blocked_exit_code(tmp_path, capsys):
    from src.pipeline.cli import main

    assert (
        main(
            [
                "replay-decision",
                str(tmp_path),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
        == 2
    )


def test_cli_bad_independent_hash_returns_failure_exit_code(saved_run, capsys):
    from src.pipeline.cli import main

    directory, original = saved_run
    assert (
        main(
            [
                "replay-decision",
                str(directory),
                "--expected-manifest-sha256",
                "MOCK-WRONG-HASH",
                "--output",
                str(directory / "report.json"),
            ]
        )
        == 1
    )
