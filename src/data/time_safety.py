from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

TAIPEI = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TAIPEI).isoformat()


def aware(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a timezone-aware ISO string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result


def filter_available_data(
    rows: list[dict[str, Any]], as_of_datetime: str, strict: bool = True
) -> list[dict[str, Any]]:
    """Never equate period dates with publication time or infer historic availability."""
    cutoff = aware(as_of_datetime)
    result = []
    for original in rows:
        row = dict(original)
        observation = row.get("observation_date", row.get("trade_date"))
        if observation and str(observation) > cutoff.astimezone(TAIPEI).date().isoformat():
            continue
        available = row.get("available_at") or row.get("published_at")
        verified = row.get("availability_verified", False)
        if available:
            if aware(available) > cutoff:
                continue
            if strict and not verified:
                continue
        elif strict:
            continue
        row["availability_uncertain"] = not bool(available and verified)
        result.append(row)
    return result
