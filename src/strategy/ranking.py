from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean, pstdev

from .features import FeatureSnapshot


@dataclass(frozen=True)
class AlphaConfig:
    version: str = "transparent-baseline-1.0"
    weights: dict[str, float] | None = None
    missing_policy: str = "exclude"
    minimum_coverage: float = 0.7
    winsor_fraction: float = 0.05

    def __post_init__(self):
        if (
            self.missing_policy not in {"exclude", "renormalize"}
            or not 0 < self.minimum_coverage <= 1
            or not 0 <= self.winsor_fraction < 0.5
        ):
            raise ValueError("Invalid alpha missing-data or winsorization policy")
        weights = self.weights or {"momentum_5d": 0.3, "momentum_20d": 0.5, "volatility_20d": -0.2}
        if (
            not weights
            or any(not isfinite(value) for value in weights.values())
            or not sum(abs(value) for value in weights.values())
        ):
            raise ValueError("Invalid alpha factor weights")
        object.__setattr__(self, "weights", dict(weights))


def quantile(values: list[float], proportion: float) -> float:
    values = sorted(values)
    location = (len(values) - 1) * proportion
    lower = int(location)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (location - lower)


class AlphaModel:
    def __init__(self, config: AlphaConfig | None = None) -> None:
        self.config = config or AlphaConfig()

    def rank(self, snapshot: FeatureSnapshot, baseline: str = "multifactor") -> dict:
        if baseline not in {"equal_weight", "momentum", "multifactor"}:
            raise ValueError("Unknown baseline")
        weights = {"momentum_20d": 1.0} if baseline == "momentum" else self.config.weights
        total = sum(abs(weight) for weight in weights.values())
        eligible = {}
        exclusions = {}
        for ticker, factors in snapshot.values.items():
            available = {
                name: factors.get(name)
                for name in weights
                if factors.get(name) is not None and isfinite(factors[name])
            }
            coverage = sum(abs(weights[name]) for name in available) / total
            if (
                not available
                or coverage < self.config.minimum_coverage
                or (self.config.missing_policy == "exclude" and coverage < 1)
            ):
                exclusions[ticker] = {"reason": "missing_required_factors", "coverage": coverage}
            else:
                eligible[ticker] = available
        standardized: dict[str, dict[str, float]] = {ticker: {} for ticker in eligible}
        for name in weights:
            cross = [values[name] for values in eligible.values() if name in values]
            if not cross:
                continue
            lower, upper = quantile(cross, self.config.winsor_fraction), quantile(
                cross, 1 - self.config.winsor_fraction
            )
            clipped = [max(lower, min(upper, value)) for value in cross]
            center, dispersion = mean(clipped), pstdev(clipped)
            for ticker, values in eligible.items():
                if name in values:
                    value = max(lower, min(upper, values[name]))
                    standardized[ticker][name] = (
                        (value - center) / dispersion if dispersion else 0.0
                    )
        scores = []
        for ticker, factors in eligible.items():
            denominator = sum(abs(weights[name]) for name in factors)
            score = (
                sum(weights[name] * standardized[ticker][name] for name in factors) / denominator
            )
            scores.append(
                {
                    "ticker": ticker,
                    "score": 0.0 if baseline == "equal_weight" else score,
                    "factor_zscores": standardized[ticker],
                    "factor_coverage": denominator / total,
                    "missing_factors": sorted(set(weights) - set(factors)),
                }
            )
        return {
            "strategy_version": self.config.version,
            "factor_version": snapshot.factor_version,
            "mode": snapshot.mode,
            "data_cutoff": snapshot.data_cutoff,
            "baseline": baseline,
            "rankings": sorted(scores, key=lambda r: (-r["score"], r["ticker"])),
            "excluded": exclusions,
            "validation_status": "RESEARCH_ONLY; predictive value unvalidated",
        }
