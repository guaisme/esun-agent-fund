from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path

from .models import PortfolioState, Position


def json_value(value):
    """Audit ledgers keep Decimal as decimal strings to preserve exact values."""
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(value)
    return value


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_state(value: dict) -> PortfolioState:
    fields = dict(value)
    fields["positions"] = {
        ticker: Position(**position) for ticker, position in fields.get("positions", {}).items()
    }
    fields["applied_actions"] = set(fields.get("applied_actions", []))
    return PortfolioState(**fields)
