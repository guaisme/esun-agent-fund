from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .models import AccountingError, decimal


@dataclass(frozen=True)
class CompetitionPolicy:
    min_holdings: int = 20
    max_holdings: int = 30
    tsmc_cap: Decimal = Decimal("0.25")
    stock_cap: Decimal = Decimal("0.10")
    cash_cap: Decimal = Decimal("0.25")
    lot_size: int = 1000
    overweight_grace_days: int = 5
    warning_limit: int = 3
    active_share_min: Decimal = Decimal("0.20")
    active_share_breach_days: int = 2
    missing_execution: Literal["block", "reject_day", "skip_order"] = "block"
    cash_rounding_confirmed: bool = False
    missing_execution_confirmed: bool = False
    active_share_method_confirmed: bool = False

    def __post_init__(self) -> None:
        for name in ("tsmc_cap", "stock_cap", "cash_cap", "active_share_min"):
            value = decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if not 0 < value <= 1:
                raise AccountingError("Invalid ratio policy")
        if (
            self.lot_size != 1000
            or not 20 <= self.min_holdings <= self.max_holdings <= 30
        ):
            raise AccountingError(
                "Policy may not relax official holdings or lot constraints"
            )
        if (
            self.tsmc_cap > Decimal("0.25")
            or self.stock_cap > Decimal("0.10")
            or self.cash_cap > Decimal("0.25")
            or self.active_share_min != Decimal("0.20")
        ):
            raise AccountingError("Policy may not relax official weight/cash limits")
        if (
            self.overweight_grace_days != 5
            or self.warning_limit != 3
            or self.active_share_breach_days != 2
        ):
            raise AccountingError(
                "Official grace and disqualification rules cannot be relaxed"
            )
        if self.missing_execution not in {"block", "reject_day", "skip_order"}:
            raise AccountingError("Unknown missing-price policy")

    def cap(self, ticker: str) -> Decimal:
        return self.tsmc_cap if ticker == "2330" else self.stock_cap
