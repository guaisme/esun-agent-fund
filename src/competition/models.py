from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal


class AccountingError(ValueError):
    """An invalid input must never silently alter the ledger."""


def decimal(value: Decimal | str | int) -> Decimal:
    # Reject binary floats at the accounting boundary, including bool (an int subclass).
    if isinstance(value, (float, bool)):
        raise AccountingError("Accounting values require Decimal, integer or decimal string")
    try:
        result = Decimal(value)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise AccountingError("Invalid decimal value") from exc
    if not result.is_finite():
        raise AccountingError("Non-finite accounting value")
    return result


def day(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise AccountingError("Date must be YYYY-MM-DD")
    return value


@dataclass(frozen=True)
class Position:
    ticker: str
    shares: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ticker, str)
            or len(self.ticker) != 4
            or not self.ticker.isascii()
            or not self.ticker.isdigit()
        ):
            raise AccountingError("Invalid four-digit ticker")
        if type(self.shares) is not int or self.shares <= 0:
            raise AccountingError("Position shares must be a positive integer")


@dataclass(frozen=True)
class TradeOrder:
    ticker: str
    side: Literal["BUY", "SELL"]
    shares: int
    decision_ref: str | None = None

    def __post_init__(self) -> None:
        Position(self.ticker, self.shares)
        if self.shares % 1000:
            raise AccountingError("Orders must be whole 1,000-share lots")
        if self.side not in {"BUY", "SELL"}:
            raise AccountingError("Only BUY and SELL are supported")


@dataclass(frozen=True)
class TransactionCosts:
    commission_rate: Decimal = Decimal("0.001425")
    sell_tax_rate: Decimal = Decimal("0.003")
    quantum: Decimal = Decimal("0.01")
    rounding: str = ROUND_HALF_UP

    def __post_init__(self) -> None:
        for name in ("commission_rate", "sell_tax_rate", "quantum"):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if (
            not 0 <= self.commission_rate < 1
            or not 0 <= self.sell_tax_rate < 1
            or self.quantum <= 0
        ):
            raise AccountingError("Invalid transaction cost policy")
        # Fail early for an unsupported rounding identifier.
        Decimal("1").quantize(self.quantum, rounding=self.rounding)

    def fees(self, gross: Decimal, side: str) -> tuple[Decimal, Decimal]:
        gross = decimal(gross)
        if gross < 0 or side not in {"BUY", "SELL"}:
            raise AccountingError("Invalid fee request")
        commission = (gross * self.commission_rate).quantize(self.quantum, rounding=self.rounding)
        tax = (
            (gross * self.sell_tax_rate).quantize(self.quantum, rounding=self.rounding)
            if side == "SELL"
            else Decimal(0)
        )
        return commission, tax


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    trade_date: str
    close: Decimal | None
    volume: int | None
    turnover: Decimal | None
    source: str
    volume_unit: str = "shares"
    turnover_unit: str = "NTD"
    synthetic: bool = False

    @classmethod
    def from_data_row(cls, row: dict) -> "PriceBar":
        """Explicit adapter for Phase 2's TWD/share + integer shares/turnover.

        TWD and NTD denote the same currency here. Phase 2 OHLC are parsed
        decimal-source floats; str conversion preserves their exported decimal
        representation, never the binary float expansion. VWAP is recomputed
        from original integer turnover and volume, not the float vwap column.
        """
        if (
            row.get("price_unit") != "TWD/share"
            or row.get("volume_unit") != "shares"
            or row.get("turnover_unit") != "TWD"
        ):
            raise AccountingError("Unsupported Phase 2 price units")
        return cls(
            row["ticker"],
            row["trade_date"],
            None if row["close"] is None else decimal(str(row["close"])),
            row["volume"],
            None if row["turnover"] is None else decimal(row["turnover"]),
            row["source"],
            synthetic=row.get("synthetic", False),
        )

    def __post_init__(self) -> None:
        Position(self.ticker, 1)
        day(self.trade_date)
        if not self.source:
            raise AccountingError("Price source is required")
        if self.close is not None:
            object.__setattr__(self, "close", decimal(self.close))
            if self.close <= 0:
                raise AccountingError("Closing price must be positive")
        if self.turnover is not None:
            object.__setattr__(self, "turnover", decimal(self.turnover))
            if self.turnover < 0:
                raise AccountingError("Negative turnover")
        if self.volume is not None and (type(self.volume) is not int or self.volume < 0):
            raise AccountingError("Volume must be non-negative integer shares")
        if self.volume_unit != "shares" or self.turnover_unit != "NTD":
            raise AccountingError("Execution requires shares and NTD, never lots or thousands")

    def execution_price(self) -> Decimal:
        if not self.volume or self.turnover is None or self.turnover <= 0:
            raise AccountingError(f"No verified daily average execution price: {self.ticker}")
        return self.turnover / Decimal(self.volume)


@dataclass(frozen=True)
class CorporateAction:
    action_id: str
    ticker: str
    effective_date: str
    kind: Literal["cash_dividend", "split", "stock_dividend"]
    value: Decimal
    source: str
    verified: bool
    available_at: str

    def __post_init__(self) -> None:
        Position(self.ticker, 1)
        day(self.effective_date)
        object.__setattr__(self, "value", decimal(self.value))
        if self.kind not in {"cash_dividend", "split", "stock_dividend"} or self.value <= 0:
            raise AccountingError("Invalid corporate action")
        if not self.action_id or not self.source or not self.verified:
            raise AccountingError("Corporate action requires verifiable source and unique ID")
        stamp = datetime.fromisoformat(self.available_at)
        if stamp.tzinfo is None:
            raise AccountingError("Corporate-action availability requires timezone")


@dataclass
class PortfolioState:
    cash: Decimal = Decimal("1000000000")
    positions: dict[str, Position] = field(default_factory=dict)
    deferred_dividends: Decimal = Decimal(0)
    last_trade_date: str | None = None
    initialized: bool = False
    warning_count: int = 0
    disqualified: bool = False
    overweight_streaks: dict[str, int] = field(default_factory=dict)
    active_share_streaks: dict[str, int] = field(default_factory=dict)
    applied_actions: set[str] = field(default_factory=set)
    dividends_settled: bool = False

    def __post_init__(self) -> None:
        self.cash = decimal(self.cash)
        self.deferred_dividends = decimal(self.deferred_dividends)
        if self.cash < 0 or self.deferred_dividends < 0:
            raise AccountingError("Negative cash or dividend balance")
        if self.last_trade_date:
            day(self.last_trade_date)
        if any(ticker != position.ticker for ticker, position in self.positions.items()):
            raise AccountingError("Position dictionary key differs from ticker")


@dataclass(frozen=True)
class TradeExecution:
    order: TradeOrder
    price: Decimal
    gross: Decimal
    commission: Decimal
    tax: Decimal
    source: str


@dataclass(frozen=True)
class DailyNAV:
    trade_date: str
    cash: Decimal
    market_value: Decimal
    nav: Decimal
    deferred_dividends: Decimal
    weights: dict[str, Decimal]
    cash_ratio: Decimal


@dataclass(frozen=True)
class ComplianceViolation:
    code: str
    severity: Literal["HARD_FAIL", "WARNING", "INFO", "UNVERIFIED"]
    message: str
    ticker: str | None = None


@dataclass(frozen=True)
class SimulationResult:
    status: Literal["EXECUTED", "REJECTED", "BLOCKED", "DISQUALIFIED"]
    state: PortfolioState
    daily_nav: DailyNAV | None
    executions: tuple[TradeExecution, ...]
    violations: tuple[ComplianceViolation, ...]
    trade_date: str
    synthetic: bool = False
