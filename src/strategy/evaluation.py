"""Execute frozen-policy folds, retaining failed and successful evaluations."""

from __future__ import annotations

from dataclasses import asdict

from .availability import AvailabilityPolicy
from .backtest import FactorBacktester, walk_forward_splits
from .ranking import AlphaModel, AlphaConfig


def evaluate_walk_forward(
    data: dict,
    *,
    mode: str = "research_assumptions",
    expanding: bool = True,
    alpha: AlphaConfig | None = None,
    runner=None,
) -> dict:
    sessions = sorted({row["trade_date"] for row in data["prices"]})
    splits = walk_forward_splits(sessions, expanding=expanding)
    config = alpha or AlphaConfig()
    execute = runner or (
        lambda start, end: FactorBacktester(20, AvailabilityPolicy(mode), AlphaModel(config)).run(
            **data, start=start, end=end, baseline="multifactor"
        )
    )
    folds = []
    for number, split in enumerate(splits):
        periods = {
            "training": split.training[21:],
            "validation": split.validation,
            "test": split.test,
        }
        results = {}
        for role, window in periods.items():
            results[role] = (
                execute(window[0], window[-1])
                if window
                else {"status": "BLOCKED", "reason": "Insufficient training warm-up"}
            )
        folds.append({"fold": number, "split": asdict(split), "results": results})
    return {
        "status": "EXECUTED_CONDITIONAL" if folds else "BLOCKED",
        "mode": mode,
        "expanding": expanding,
        "strategy_version": config.version,
        "parameter_selection": "Fixed before evaluation; no fitting or final-test tuning; each segment starts from independent initial cash",
        "training_warmup_sessions": 21,
        "rolling_24_session_evaluations": [
            {
                "fold": f["fold"],
                "range": [f["split"]["test"][0], f["split"]["test"][-1]],
                "status": f["results"]["test"]["status"],
                "metrics": f["results"]["test"].get("metrics"),
            }
            for f in folds
        ],
        "folds": folds,
    }
