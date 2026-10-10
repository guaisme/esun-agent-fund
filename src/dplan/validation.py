from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from src.competition.models import AccountingError, PortfolioState, TransactionCosts, decimal, day
from src.competition.orders import derive_orders
from src.competition.policy import CompetitionPolicy
from src.data.time_safety import TAIPEI, aware
from src.data.universe import ROOT, UniverseLoader


@dataclass(frozen=True)
class DPlanIssue:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class DPlanValidationReport:
    issues: tuple[DPlanIssue, ...]
    schema_valid: bool
    estimated_cash: Decimal | None = None
    net_exposure_ntd: Decimal | None = None

    @property
    def status(self) -> str:
        if any(issue.severity == "HARD_FAIL" for issue in self.issues):
            return "INVALID"
        if any(issue.severity == "UNVERIFIED" for issue in self.issues):
            return "BLOCKED"
        return "VALID"


class DPlanValidator:
    def __init__(
        self,
        schema_path: Path | None = None,
        policy: CompetitionPolicy | None = None,
        costs: TransactionCosts | None = None,
    ) -> None:
        self.schema_path = schema_path or ROOT / "docs/official/D-Plan.schema_v4.2.json"
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())
        self.policy, self.costs = policy or CompetitionPolicy(), costs or TransactionCosts()
        self.universe = UniverseLoader()

    def validate(
        self,
        plan: dict,
        *,
        state: PortfolioState | None = None,
        previous_nav: Decimal | None = None,
        previous_closes: dict[str, Decimal] | None = None,
        cutoff: str | None = None,
        received_at: str | None = None,
        authenticated_team: str | None = None,
        filename: str | None = None,
        trading_days: set[str] | None = None,
        inventory_reconciled: bool = False,
        evidence_verified: bool = False,
        strict: bool = True,
    ) -> DPlanValidationReport:
        errors = sorted(self.validator.iter_errors(plan), key=lambda e: str(list(e.path)))
        issues = [
            DPlanIssue("SCHEMA", "HARD_FAIL", f"{'/'.join(map(str, e.path))}: {e.message}")
            for e in errors
        ]
        if errors:
            return DPlanValidationReport(tuple(issues), False)

        def fail(code: str, message: str, severity: str = "HARD_FAIL") -> None:
            issues.append(DPlanIssue(code, severity, message))

        for section, identifier, prefix in (
            ("sources", "source_id", "S"),
            ("observations", "obs_id", "O"),
            ("inferences", "inf_id", "I"),
            ("decisions", "decision_id", "D"),
        ):
            actual = [row[identifier] for row in plan[section]]
            if actual != [f"{prefix}{i}" for i in range(1, len(actual) + 1)]:
                fail(
                    "C1_ID",
                    f"{section}: IDs must start at 1, be consecutive and unique without leading zeros",
                )
        sources = {r["source_id"] for r in plan["sources"]}
        observations = {r["obs_id"] for r in plan["observations"]}
        inferences = {r["inf_id"] for r in plan["inferences"]}
        decisions = {r["decision_id"]: r for r in plan["decisions"]}
        for rows, field, allowed in (
            (plan["observations"], "source_ref", sources),
            (plan["inferences"], "premise_refs", observations),
            (plan["decisions"], "inference_refs", inferences),
            (plan["no_trade_decisions"], "reason_refs", inferences),
            ([plan["market_view"]], "basis_refs", observations),
        ):
            for row in rows:
                if not set(row[field]) <= allowed or len(set(row[field])) != len(row[field]):
                    fail("C1_REF", f"Invalid or duplicate {field}")
        for order in plan["orders"]:
            linked = decisions.get(order["decision_ref"])
            if linked is None or linked["ticker"] != order["ticker"]:
                fail("C1_ORDER", "Order must reference its own ticker decision")
        tickers = [r["ticker"] for r in plan["decisions"] + plan["no_trade_decisions"]]
        if len(tickers) != len(set(tickers)):
            fail("C1_TICKER", "Ticker appears more than once across change/no-trade decisions")
        if any(not self.universe.is_valid_ticker(ticker) for ticker in tickers):
            fail("C9_UNIVERSE", "Decision outside official 150-stock universe")
        for decision in plan["decisions"]:
            weight = decimal(str(decision["target_weight"]))
            if weight > self.policy.cap(decision["ticker"]):
                fail("C9_WEIGHT", f"Weight cap exceeded: {decision['ticker']}")
            if decision["action"] == "SELL_ALL" and weight != 0:
                fail("C2_ACTION", "SELL_ALL target weight must be zero")
            funding = decision.get("funding_for", [])
            if funding and (
                decision["action"] not in {"TRIM", "SELL_ALL"}
                or any(
                    ref not in decisions or decisions[ref]["action"] not in {"BUY", "ADD"}
                    for ref in funding
                )
            ):
                fail(
                    "C14_REF",
                    "funding_for only permits sell decisions referencing same-day buy decisions",
                )
        try:
            day(plan["trade_date"])
            started = aware(plan["agent_metadata"]["run_started_at"])
            completed = aware(plan["agent_metadata"]["run_completed_at"])
            if started > completed:
                fail("C6_RUN", "Run completion precedes start")
            decision_cutoff = aware(cutoff) if cutoff else completed
            if decision_cutoff.utcoffset() != timedelta(hours=8):
                fail("C6_ZONE", "Decision cutoff must use +08:00")
            if completed > decision_cutoff:
                fail("C6_CUTOFF", "Run completed after decision cutoff")
            if received_at and completed > aware(received_at):
                fail("C6_RECEIVED", "Run completed after receipt")
            for source in plan["sources"]:
                for field in ("content_as_of", "published_at"):
                    if field in source and aware(source[field]) > decision_cutoff:
                        fail("PIT_SOURCE", f"Future source {source['source_id']} {field}")
                if "fetched_at" in source and aware(source["fetched_at"]) > (
                    aware(received_at) if received_at else decision_cutoff
                ):
                    fail("C6_FETCH", "Source fetch time after cutoff/receipt")
            if trading_days is not None and plan["trade_date"] not in trading_days:
                fail("CALENDAR", "trade_date is not an official trading session")
        except (ValueError, TypeError) as exc:
            fail("C6_TIMESTAMP", str(exc))
        if authenticated_team and plan["team_id"] != authenticated_team:
            fail("TEAM", "Team differs from authenticated configuration")
        if (
            filename
            and Path(filename).name != f"D-Plan_{plan['team_id']}_{plan['trade_date']}.json"
        ):
            fail("FILENAME", "Official filename differs from plan team/date")
        cash_range = [
            decimal(str(v)) for v in plan["market_view"]["posture"]["target_cash_pct_range"]
        ]
        if cash_range[0] > cash_range[1]:
            fail("C12_RANGE", "Cash range lower bound exceeds upper bound")
        if state is None or previous_nav is None or previous_closes is None:
            fail(
                "SETTLED_INPUT",
                "Previous settled inventory/NAV/official closes unavailable",
                "UNVERIFIED",
            )
            return DPlanValidationReport(tuple(issues), True)
        if not set(state.positions) <= set(tickers):
            fail("COVERAGE", "Every current holding requires a decision or no-trade entry")
        if any(r["ticker"] not in state.positions for r in plan["no_trade_decisions"]):
            fail("NO_TRADE", "No-trade decisions must describe existing holdings")
        targets = {r["ticker"]: decimal(str(r["target_weight"])) for r in plan["decisions"]}
        refs = {r["ticker"]: r["decision_id"] for r in plan["decisions"]}
        with localcontext() as context:
            context.prec = 50
            try:
                previous_nav = decimal(previous_nav)
                unchanged = {r["ticker"] for r in plan["no_trade_decisions"]}
                expected = derive_orders(
                    targets, state, previous_nav, previous_closes, refs, self.policy, unchanged
                )
                expected_core = sorted(
                    (o.ticker, o.side, o.shares, o.decision_ref) for o in expected
                )
                declared_core = sorted(
                    (o["ticker"], o["side"], o["shares"], o["decision_ref"]) for o in plan["orders"]
                )
                if expected_core != declared_core:
                    fail(
                        "C2_ORDERS",
                        "Orders differ from official target-weight mechanical derivation",
                    )
                by_ticker = {o.ticker: o for o in expected}
                for decision in plan["decisions"]:
                    ticker = decision["ticker"]
                    order = by_ticker.get(ticker)
                    previous_shares = (
                        state.positions[ticker].shares if ticker in state.positions else 0
                    )
                    if order is None:
                        fail("C2_ZERO", "Unchanged target must be a no-trade decision")
                        continue
                    action = (
                        "BUY"
                        if order.side == "BUY" and previous_shares == 0
                        else (
                            "ADD"
                            if order.side == "BUY"
                            else "SELL_ALL" if order.shares == previous_shares else "TRIM"
                        )
                    )
                    if decision["action"] != action:
                        fail(
                            "C2_ACTION",
                            f"Action disagrees with mechanical share difference: {ticker}",
                        )
                estimated_cash, net = self._estimated_cash(state, plan["orders"], previous_closes)
                if estimated_cash < 0:
                    fail("C13_CASH", "Estimated cash is negative")
                if estimated_cash / previous_nav >= self.policy.cash_cap:
                    fail("C11_CASH", "Estimated cash / previous NAV must be below 25%")
                if not cash_range[0] <= estimated_cash / previous_nav <= cash_range[1]:
                    fail("C12_CASH", "Estimated cash ratio is outside declared range")
                intent = plan["market_view"]["posture"]["net_exposure_intent"]
                if (
                    (intent == "increase" and net <= 0)
                    or (intent == "reduce" and net >= 0)
                    or (intent == "hold" and abs(net) > previous_nav * Decimal("0.02"))
                ):
                    fail("C12_POSTURE", "Net buy/sell exposure contradicts declared posture")
                funded_ids = {r["decision_id"] for r in plan["decisions"] if r.get("funding_for")}
                if funded_ids:
                    removed_cash, _ = self._estimated_cash(
                        state,
                        [r for r in plan["orders"] if r["decision_ref"] not in funded_ids],
                        previous_closes,
                    )
                    if removed_cash / previous_nav >= cash_range[0]:
                        fail(
                            "C14_SHORTFALL",
                            "Removing all funding sells does not create declared cash shortfall",
                        )
                shares = {t: p.shares for t, p in state.positions.items()}
                for order in plan["orders"]:
                    shares[order["ticker"]] = shares.get(order["ticker"], 0) + order["shares"] * (
                        1 if order["side"] == "BUY" else -1
                    )
                if any(value < 0 for value in shares.values()):
                    fail("C13_SELL", "Orders oversell settled inventory")
                if (
                    not self.policy.min_holdings
                    <= sum(v > 0 for v in shares.values())
                    <= self.policy.max_holdings
                ):
                    fail("C11_COUNT", "Estimated holding count outside 20–30")
            except (ValueError, KeyError, ArithmeticError) as exc:
                fail("C2_INPUT", str(exc))
                estimated_cash, net = None, None
        if strict:
            if not inventory_reconciled:
                fail(
                    "INVENTORY",
                    "Official settled-inventory reconciliation unverified",
                    "UNVERIFIED",
                )
            if not evidence_verified:
                fail(
                    "EVIDENCE",
                    "Observation values and source availability not independently verified",
                    "UNVERIFIED",
                )
            if trading_days is None:
                fail("CALENDAR", "Official calendar unavailable", "UNVERIFIED")
            if not authenticated_team:
                fail("TEAM_AUTH", "Authenticated team configuration missing", "UNVERIFIED")
        return DPlanValidationReport(tuple(issues), True, estimated_cash, net)

    def _estimated_cash(
        self, state: PortfolioState, orders: list[dict], closes: dict[str, Decimal]
    ) -> tuple[Decimal, Decimal]:
        cash, net = state.cash, Decimal(0)
        for order in orders:
            gross = decimal(closes[order["ticker"]]) * order["shares"]
            commission, tax = self.costs.fees(gross, order["side"])
            sign = 1 if order["side"] == "BUY" else -1
            net += gross * sign
            cash -= gross * sign + commission + tax
        return cash, net
