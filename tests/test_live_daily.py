"""Opt-in real daily dry run. BLOCKED is not a successful formal submission."""

import os
from pathlib import Path
import pytest


@pytest.mark.live_e2e
def test_live_daily_dry_run(tmp_path):
    if os.getenv("RUN_LIVE_DAILY") != "1":
        pytest.skip("Explicit RUN_LIVE_DAILY=1 required; consumes Gemini quota")
    from dotenv import load_dotenv

    load_dotenv("research_agent/.env")
    if (
        not os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_API_KEY") == "YOUR_API_KEY_HERE"
    ):
        pytest.skip("Missing Gemini API key")
    required = ["DAILY_CALENDAR", "DAILY_STATE"]
    if any(not os.getenv(name) for name in required):
        pytest.skip("Real calendar and previous settled ledger must be supplied")
    from src.pipeline.cli import parser
    from src.pipeline.daily import daily_run

    args = parser().parse_args(
        [
            "daily-run",
            "--live",
            "--calendar",
            os.environ["DAILY_CALENDAR"],
            "--state",
            os.environ["DAILY_STATE"],
            "--output",
            str(tmp_path),
        ]
    )
    report = daily_run(args)
    assert report["execution_status"] == "COMPLETED"
    assert report.get("gemini") == "DECISION_PARSED", report.get("errors")
    assert report.get("schema") is True
    assert (
        report["submission_status"] == "BLOCKED"
    )  # external formal evidence remains unconfirmed
