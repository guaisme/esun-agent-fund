"""Explain gaps without turning empty API responses into verified holidays."""

from __future__ import annotations
from datetime import date
from src.competition.calendar import TradingCalendar
from src.data.universe import UniverseLoader


def session_quality(
    rows: list[dict], updates: list[dict], calendars: dict[str, TradingCalendar]
) -> dict:
    universe = UniverseLoader().load_universe()
    findings = []
    for update in updates:
        market, on = update["market"], update["date"]
        observed = {
            r["ticker"]
            for r in rows
            if r.get("market") == market and r.get("trade_date") == on
        }
        expected = {s["ticker"] for s in universe if s["market"] == market}
        calendar = calendars.get(market)
        if calendar is not None and date.fromisoformat(on).year == calendar.year:
            is_open = on in calendar.sessions(on, on)
            state = (
                ("CALENDAR_CONFLICT" if observed else "VERIFIED_CLOSED")
                if not is_open
                else ("COMPLETE" if not expected - observed else "MISSING_OBSERVATIONS")
            )
        else:
            state = (
                "CALENDAR_UNVERIFIED"
                if not observed
                else (
                    "OBSERVED_COMPLETE"
                    if not expected - observed
                    else "PARTIAL_UNEXPLAINED"
                )
            )
        findings.append(
            dict(
                market=market,
                date=on,
                classification=state,
                previous_status=update["status"],
                observed=len(observed),
                missing_tickers=(
                    sorted(expected - observed)
                    if state not in {"VERIFIED_CLOSED", "CALENDAR_UNVERIFIED"}
                    else []
                ),
                reason="Listing/suspension/corporate action must be evidenced; never filled with zero",
            )
        )
    return {
        "sessions": findings,
        "counts": {
            state: sum(r["classification"] == state for r in findings)
            for state in sorted({r["classification"] for r in findings})
        },
        "historical_publication_verified": False,
        "note": "Calendar classification does not verify historical publication or revisions. TPEx requires its own evidence.",
    }
