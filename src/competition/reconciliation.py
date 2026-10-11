"""Compare a normalized organizer export with our ledger; no invented API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import hashlib

from src.competition.models import PortfolioState, decimal, day
from src.data.time_safety import aware


@dataclass(frozen=True)
class OfficialSettlement:
    team_id: str
    trade_date: str
    cash: Decimal
    positions: dict[str, int]
    nav: Decimal
    source_file: Path
    source_sha256: str
    retrieved_at: str
    authenticity_verified: bool = False


class SettlementReconciler:
    def reconcile(
        self,
        state: PortfolioState,
        expected_nav: Decimal,
        settlement: OfficialSettlement,
        team_id: str,
    ) -> dict:
        day(settlement.trade_date)
        aware(settlement.retrieved_at)
        problems = []
        if settlement.team_id != team_id or settlement.trade_date != state.last_trade_date:
            problems.append("Organizer team or settled date differs")
        if not settlement.authenticity_verified:
            problems.append("Organizer export authenticity has not been verified")
        if (
            not settlement.source_file.is_file()
            or hashlib.sha256(settlement.source_file.read_bytes()).hexdigest()
            != settlement.source_sha256
        ):
            problems.append("Original organizer export missing or hash mismatch")
        if any(type(count) is not int or count <= 0 for count in settlement.positions.values()):
            problems.append("Invalid organizer share counts")
        if settlement.positions != {t: p.shares for t, p in state.positions.items()}:
            problems.append("Share inventory differs")
        if decimal(settlement.cash) != state.cash or decimal(settlement.nav) != decimal(
            expected_nav
        ):
            problems.append("Cash or NAV differs; do not silently replace local ledger")
        return {
            "status": "BLOCKED" if problems else "RECONCILED",
            "issues": problems,
            "team_id": team_id,
            "trade_date": settlement.trade_date,
            "source_sha256": settlement.source_sha256,
        }
