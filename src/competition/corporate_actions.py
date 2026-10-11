from __future__ import annotations

from dataclasses import dataclass

from .models import AccountingError, CorporateAction


@dataclass(frozen=True)
class CompetitionCorporateActionPolicy:
    cash_dividends: str = "defer_until_competition_end"
    fractional_shares: str = "block"
    verified_source_required: bool = True

    def validate(self, action: CorporateAction) -> None:
        if (
            self.cash_dividends != "defer_until_competition_end"
            or self.fractional_shares != "block"
            or not self.verified_source_required
        ):
            raise AccountingError("Unconfirmed corporate-action settlement policy")
        if not action.verified:
            raise AccountingError("Unverified corporate action")
