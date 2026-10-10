from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal

from src.competition.models import PortfolioState
from src.competition.serialization import json_value
from src.portfolio.optimizer import PortfolioOptimizer
from src.portfolio.active_share import BenchmarkHoldings
from src.data.time_safety import aware
from src.strategy.features import FeatureSnapshot


@dataclass(frozen=True)
class DecisionContext:
    trade_date: str
    data_cutoff: str
    state: PortfolioState
    previous_nav: Decimal
    previous_closes: dict[str, Decimal]
    snapshot: FeatureSnapshot
    rankings: dict
    sources: list[dict]
    observations: list[dict]
    market_evidence: dict
    benchmark_evidence: dict
    strategy_version: str
    fixture: bool = False
    benchmark_snapshots: tuple[BenchmarkHoldings, ...] = ()
    sector_memberships: dict[str, dict] | None = None

    def __post_init__(self) -> None:
        if (
            self.snapshot.data_cutoff != self.data_cutoff
            or self.rankings["data_cutoff"] != self.data_cutoff
        ):
            raise ValueError("Decision context has inconsistent cutoffs")
        if self.fixture != (self.snapshot.mode == "synthetic_fixture"):
            raise ValueError("Fixture and availability mode disagree")

    def optimizer_evidence_inputs(self) -> dict:
        return {
            "benchmark_snapshots": (
                list(self.benchmark_snapshots) if self.benchmark_snapshots else None
            ),
            "benchmark_cutoff": aware(self.data_cutoff),
            "active_share_method": self.benchmark_evidence.get(
                "calculation_method", "raw"
            ),
            "active_share_method_confirmed": self.benchmark_evidence.get(
                "method_confirmed"
            )
            is True,
            "sector_memberships": self.sector_memberships,
            "fixture": self.fixture,
        }


def readonly_tools(
    context: DecisionContext, optimizer: PortfolioOptimizer | None = None
) -> list:
    """Closures expose a frozen copy of cutoff-filtered inputs, never execution bars.

    There is no filesystem/network/SQL interface through which Gemini can query
    future rows. Missing market/ETF evidence stays missing and visible.
    """
    context = deepcopy(context)
    optimizer = optimizer or PortfolioOptimizer()

    def get_portfolio() -> dict:
        """Get settled cash, holdings, previous NAV and previous official closes."""
        return json_value(
            {
                "state": context.state,
                "previous_nav": context.previous_nav,
                "previous_closes": context.previous_closes,
                "data_cutoff": context.data_cutoff,
            }
        )

    def get_market_snapshot() -> dict:
        """Get only cutoff-filtered actual observations, sources and market evidence."""
        return json_value(
            {
                "sources": context.sources,
                "observations": context.observations,
                "market_evidence": context.market_evidence,
                "data_cutoff": context.data_cutoff,
                "mode": context.snapshot.mode,
            }
        )

    def get_features(ticker: str) -> dict:
        """Get a stock's available factor values, missing inputs and provenance."""
        if ticker not in context.snapshot.values:
            return {"status": "UNAVAILABLE", "ticker": ticker}
        return deepcopy(
            {
                "ticker": ticker,
                "values": context.snapshot.values[ticker],
                "provenance": context.snapshot.provenance.get(ticker, [])[-25:],
                "data_cutoff": context.data_cutoff,
            }
        )

    def get_rankings() -> dict:
        """Get versioned transparent rankings; predictive validity is unproven."""
        return deepcopy(context.rankings)

    def get_institutional_flows(ticker: str) -> dict:
        """Get verified available flow factors; missing history stays null."""
        features = get_features(ticker)
        return {
            "ticker": ticker,
            "data_cutoff": context.data_cutoff,
            "values": {
                k: v for k, v in features.get("values", {}).items() if "flow_" in k
            },
        }

    def get_monthly_revenue(ticker: str) -> dict:
        """Get PIT-safe published revenue factors, never assume a publication date."""
        features = get_features(ticker)
        return {
            "ticker": ticker,
            "data_cutoff": context.data_cutoff,
            "values": {
                k: v
                for k, v in features.get("values", {}).items()
                if k.startswith("revenue_")
            },
        }

    def get_market_regime() -> dict:
        """Get actual regime evidence; decide the regime rather than invent indicators."""
        return deepcopy(context.market_evidence)

    def get_active_etf_holdings() -> dict:
        """Get verified benchmark information, including missing/stale status."""
        visible, unavailable = [], []
        for row in context.benchmark_snapshots:
            try:
                row.validate(aware(context.data_cutoff), 7, context.fixture)
                visible.append(json_value(row))
            except (ValueError, ArithmeticError) as error:
                unavailable.append(
                    {
                        "benchmark_id": row.ticker,
                        "status": "UNVERIFIED",
                        "reason": str(error),
                    }
                )
        return {
            "status": "SNAPSHOTS_AVAILABLE" if visible else "UNVERIFIED",
            "snapshots": visible,
            "unavailable": unavailable,
            "calculation_method": context.benchmark_evidence.get(
                "calculation_method", "raw"
            ),
            "method_confirmed": context.benchmark_evidence.get("method_confirmed")
            is True,
        }

    def estimate_transaction_cost(target_weights_json: str, regime: str) -> dict:
        """Estimate costs and constraints for target weights; cannot execute trades."""
        import json

        targets = {
            t: Decimal(str(w)) for t, w in json.loads(target_weights_json).items()
        }
        result = optimizer.optimize(
            rankings=context.rankings["rankings"],
            state=context.state,
            previous_nav=context.previous_nav,
            previous_closes=context.previous_closes,
            regime=regime,
            proposed_targets=targets,
            **context.optimizer_evidence_inputs(),
        )
        return json_value(result)

    def run_optimizer(target_weights_json: str, regime: str) -> dict:
        """Suggest bounded repairs for your selected targets; you must approve revisions."""
        return estimate_transaction_cost(target_weights_json, regime)

    def validate_proposal(target_weights_json: str, regime: str) -> dict:
        """Check estimated cash, lots, holdings, turnover and cost of your proposal."""
        return estimate_transaction_cost(target_weights_json, regime)

    def get_data_availability() -> dict:
        """Get missing factors, excluded future data, mode and external readiness gates."""
        return {
            "mode": context.snapshot.mode,
            "data_cutoff": context.data_cutoff,
            "issue_count": len(context.snapshot.quality_issues),
            "issues_sample": list(context.snapshot.quality_issues)[:30],
            "excluded_count": len(context.snapshot.excluded),
            "excluded_sample": list(context.snapshot.excluded)[:30],
            "benchmark_status": context.benchmark_evidence.get("status", "UNVERIFIED"),
        }

    return [
        get_portfolio,
        get_market_snapshot,
        get_features,
        get_rankings,
        get_institutional_flows,
        get_monthly_revenue,
        get_market_regime,
        get_active_etf_holdings,
        estimate_transaction_cost,
        run_optimizer,
        validate_proposal,
        get_data_availability,
    ]
