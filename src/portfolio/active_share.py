from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pathlib import Path
import csv
import json

from src.data.universe import ROOT

from src.competition.models import AccountingError, decimal


class ActiveETFLoader:
    def __init__(
        self,
        path: Path = ROOT / "config/active_etfs.csv",
        manifest: Path = ROOT / "config/active_etfs_manifest.json",
    ):
        self.path, self.manifest = path, manifest

    def load(self) -> list[dict]:
        with self.path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        official = json.loads(self.manifest.read_text(encoding="utf-8"))["etfs"]
        if (
            len(rows) != 30
            or len({r["ticker"] for r in rows}) != 30
            or sorted(rows, key=lambda r: r["ticker"])
            != sorted(official, key=lambda r: r["ticker"])
        ):
            raise AccountingError(
                "Active ETF list differs from all 30 official benchmarks"
            )
        return rows

    def tickers(self) -> set[str]:
        return {r["ticker"] for r in self.load()}


@dataclass(frozen=True)
class BenchmarkHoldings:
    ticker: str
    weights: dict[str, Decimal]
    content_as_of: str
    available_at: str
    source_url: str
    verified: bool
    synthetic: bool = False

    def validate(
        self, cutoff: datetime, max_age_days: int, fixture: bool = False
    ) -> None:
        as_of, available = datetime.fromisoformat(
            self.content_as_of
        ), datetime.fromisoformat(self.available_at)
        if as_of.tzinfo is None or available.tzinfo is None or cutoff.tzinfo is None:
            raise AccountingError("Benchmark timestamps require timezone")
        if available < as_of:
            raise AccountingError(
                "Benchmark availability precedes its observation timestamp"
            )
        if (
            as_of > cutoff
            or available > cutoff
            or (cutoff - as_of).total_seconds() > max_age_days * 86400
        ):
            raise AccountingError("Future or stale benchmark holdings")
        if (
            not self.source_url
            or (self.verified is not True and not fixture)
            or (self.synthetic and not fixture)
        ):
            raise AccountingError("Unverified benchmark holdings")
        checked = {ticker: decimal(weight) for ticker, weight in self.weights.items()}
        if (
            not checked
            or any(weight < 0 for weight in checked.values())
            or sum(checked.values()) > 1
        ):
            raise AccountingError("Invalid benchmark weights")


def top_ten(
    weights: dict[str, Decimal], method: Literal["raw", "renormalized"]
) -> dict[str, Decimal]:
    if method not in {"raw", "renormalized"}:
        raise AccountingError("Unknown top-10 Active Share methodology")
    weights = {ticker: decimal(weight) for ticker, weight in weights.items()}
    if any(weight < 0 for weight in weights.values()) or sum(weights.values()) > 1:
        raise AccountingError("Invalid portfolio weights")
    result = dict(sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:10])
    total = sum(result.values(), Decimal(0))
    if not total:
        raise AccountingError("Active Share undefined for an empty equity portfolio")
    return (
        {ticker: value / total for ticker, value in result.items()}
        if method == "renormalized"
        else result
    )


def calculate_active_share(
    portfolio: dict[str, Decimal],
    benchmark: dict[str, Decimal],
    method: Literal["raw", "renormalized"],
) -> Decimal:
    left, right = top_ten(portfolio, method), top_ten(benchmark, method)
    return (
        sum(
            (
                abs(left.get(ticker, Decimal(0)) - right.get(ticker, Decimal(0)))
                for ticker in set(left) | set(right)
            ),
            Decimal(0),
        )
        / 2
    )


class BenchmarkHoldingsProvider:
    """Validate supplied official snapshots; never invent an endpoint or holdings.

    All officially listed ETFs must be covered. Acquisition adapters can later
    populate these snapshots from each fund's published portfolio with evidence.
    """

    def compare(
        self,
        portfolio: dict[str, Decimal],
        snapshots: list[BenchmarkHoldings],
        required: set[str],
        cutoff: datetime,
        method: Literal["raw", "renormalized"],
        max_age_days: int = 7,
        fixture: bool = False,
    ) -> dict[str, Decimal]:
        if not fixture and required != ActiveETFLoader().tickers():
            raise AccountingError(
                "Missing full production benchmark set: Active Share requires all 30 official ETFs"
            )
        if {row.ticker for row in snapshots} != required or len(snapshots) != len(
            required
        ):
            raise AccountingError(
                "Missing or duplicate official active-ETF benchmark snapshots"
            )
        result = {}
        for row in snapshots:
            row.validate(cutoff, max_age_days, fixture)
            result[row.ticker] = calculate_active_share(portfolio, row.weights, method)
        return result


class ActiveShareCalculator:
    """Produce per-benchmark evidence; missing coverage never means zero holdings."""

    def evaluate(
        self,
        portfolio: dict[str, Decimal],
        snapshots: list[BenchmarkHoldings],
        cutoff: datetime,
        method: Literal["raw", "renormalized"],
        *,
        method_confirmed: bool = False,
        max_age_days: int = 7,
        fixture: bool = False,
    ) -> dict:
        if (
            method not in {"raw", "renormalized"}
            or type(max_age_days) is not int
            or max_age_days < 0
        ):
            raise AccountingError("Invalid Active Share method or maximum snapshot age")
        required = ActiveETFLoader().tickers()
        grouped: dict[str, list[BenchmarkHoldings]] = {}
        for row in snapshots:
            grouped.setdefault(row.ticker, []).append(row)
        records = []
        for ticker in sorted(required):
            found = grouped.get(ticker, [])
            record = {
                "benchmark_id": ticker,
                "benchmark_as_of": found[0].content_as_of if len(found) == 1 else None,
                "available_at": found[0].available_at if len(found) == 1 else None,
                "source_url": found[0].source_url if len(found) == 1 else None,
                "active_share": None,
                "calculation_method": method,
                "data_completeness": "MISSING",
                "compliance_status": "UNVERIFIED",
            }
            try:
                if len(found) != 1:
                    raise AccountingError("Missing or duplicate benchmark snapshot")
                row = found[0]
                row.validate(cutoff, max_age_days, fixture)
                record["active_share"] = calculate_active_share(
                    portfolio, row.weights, method
                )
                record["data_completeness"] = (
                    "VERIFIED" if not fixture else "SYNTHETIC_FIXTURE"
                )
                if method_confirmed:
                    record["compliance_status"] = (
                        "BELOW_LEGAL_MINIMUM"
                        if record["active_share"] < Decimal("0.20")
                        else "PASS"
                    )
                else:
                    record["reason"] = "Organizer top-ten methodology unconfirmed"
            except (ValueError, ArithmeticError) as error:
                record["reason"] = str(error)
            records.append(record)
        values = [r["active_share"] for r in records if r["active_share"] is not None]
        complete = len(values) == len(required) and set(grouped) == required
        return {
            "records": records,
            "calculation_cutoff": cutoff.isoformat(),
            "minimum_active_share": min(values) if values else None,
            "data_completeness": "COMPLETE" if complete else "INCOMPLETE",
            "method_confirmed": method_confirmed,
            "synthetic": fixture,
            "unknown_benchmarks": sorted(set(grouped) - required),
            "status": "EVALUATED" if complete and method_confirmed else "BLOCKED",
        }


class ActiveShareValidator:
    """Advance confirmed counters over the supplied competition trading sessions.

    The session set must cover the competition period, not the whole exchange
    calendar. Missing prior evidence cannot be assumed to be a clean counter.
    """

    def __init__(self, safety_threshold: Decimal = Decimal("0.25")) -> None:
        self.safety_threshold = decimal(safety_threshold)
        if not Decimal("0.20") <= self.safety_threshold <= 1:
            raise AccountingError(
                "Internal safety threshold may not relax the legal 20% minimum"
            )

    def validate(
        self,
        calculation: dict,
        trade_date: str,
        trading_days: set[str],
        *,
        previous_date: str | None = None,
        previous_streaks: dict[str, int] | None = None,
    ) -> dict:
        from src.competition.models import day

        day(trade_date)
        previous = dict(previous_streaks or {})
        if any(type(v) is not int or v < 0 for v in previous.values()):
            raise AccountingError("Active Share counters must be nonnegative integers")
        if (
            trade_date not in trading_days
            or previous_date
            and previous_date >= trade_date
        ):
            raise AccountingError(
                "Nontrading, duplicate, or out-of-order Active Share session"
            )
        records = calculation.get("records", [])
        required = ActiveETFLoader().tickers()
        complete = (
            len(records) == 30
            and {r.get("benchmark_id") for r in records} == required
            and calculation.get("data_completeness") == "COMPLETE"
            and calculation.get("method_confirmed") is True
        )
        if calculation["status"] != "EVALUATED" or not complete:
            return {
                "status": "BLOCKED",
                "streaks_advanced": False,
                "streaks": previous,
                "reason": "Coverage or methodology unverified",
                "synthetic": calculation["synthetic"],
            }
        from src.data.time_safety import aware, TAIPEI

        try:
            instant = aware(calculation["calculation_cutoff"])
            if instant.astimezone(TAIPEI).date().isoformat() < trade_date:
                raise AccountingError(
                    "Cannot advance a future session's compliance counters"
                )
            for row in records:
                if (
                    aware(row["benchmark_as_of"]).astimezone(TAIPEI).date().isoformat()
                    != trade_date
                    or aware(row["available_at"]) > instant
                ):
                    raise AccountingError(
                        "Same-session available benchmark evidence required to advance official counters"
                    )
                value = decimal(row["active_share"])
                if not 0 <= value <= 1:
                    raise AccountingError("Invalid Active Share value")
        except (ValueError, KeyError, TypeError) as error:
            return {
                "status": "BLOCKED",
                "streaks_advanced": False,
                "streaks": previous,
                "reason": str(error),
                "synthetic": calculation["synthetic"],
            }
        preceding = max((d for d in trading_days if d < trade_date), default=None)
        if (previous_date is None and trade_date != min(trading_days)) or (
            previous_date is not None and previous_date != preceding
        ):
            return {
                "status": "BLOCKED",
                "streaks_advanced": False,
                "streaks": previous,
                "reason": "Prior verified consecutive-session evidence missing",
                "synthetic": calculation["synthetic"],
            }
        streaks, issues = {}, []
        for row in calculation["records"]:
            value = decimal(row["active_share"])
            ticker = row["benchmark_id"]
            streaks[ticker] = (
                previous.get(ticker, 0) + 1 if value < Decimal("0.20") else 0
            )
            if streaks[ticker] >= 2:
                issues.append(
                    {
                        "benchmark_id": ticker,
                        "severity": "HARD_FAIL",
                        "code": "two_consecutive_breaches",
                    }
                )
            elif value < self.safety_threshold:
                issues.append(
                    {
                        "benchmark_id": ticker,
                        "severity": "WARNING",
                        "code": "internal_safety_buffer",
                    }
                )
        return {
            "status": (
                "DISQUALIFIED"
                if any(i["severity"] == "HARD_FAIL" for i in issues)
                else "WARNING" if issues else "PASS"
            ),
            "streaks_advanced": True,
            "streaks": streaks,
            "issues": issues,
            "synthetic": calculation["synthetic"],
            "trade_date": trade_date,
        }
