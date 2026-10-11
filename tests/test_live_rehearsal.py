"""Explicit opt-in billable rehearsal; ordinary pytest never enables it."""

import os
from pathlib import Path
import pytest


@pytest.mark.live_e2e
def test_live_precompetition_rehearsal():
    if os.getenv("RUN_LIVE_REHEARSAL") != "1":
        pytest.skip("Explicit RUN_LIVE_REHEARSAL=1 required; consumes Gemini quota")
    from dotenv import load_dotenv

    load_dotenv("research_agent/.env", override=False)
    if (
        not os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE"
    ):
        pytest.skip("Missing Gemini key")
    if not os.getenv("REHEARSAL_CALENDAR"):
        pytest.skip("Real archived calendar must be supplied")
    from src.pipeline.cli import parser
    from src.pipeline.rehearsal import rehearsal_run

    arguments = [
        "rehearsal-run",
        "--live",
        "--initial",
        "--calendar",
        os.environ["REHEARSAL_CALENDAR"],
        "--output",
        "outputs/live-rehearsal-test",
    ]
    if os.getenv("LIVE_REHEARSAL_RERUN") == "1":
        arguments.append("--rerun")
    report = rehearsal_run(parser().parse_args(arguments))
    assert report["execution_status"] == "COMPLETED", report.get("errors")
    assert report["submission_status"] == "BLOCKED"
    assert report.get("gemini") == "DECISION_PARSED" and report.get(
        "rehearsal_checks_passed"
    )
    assert Path(report["dplan_audit_path"]).exists()
