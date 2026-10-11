from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from src.data.time_safety import TAIPEI, aware, filter_available_data

DataMode = Literal["strict_verified", "research_assumptions", "synthetic_fixture"]


@dataclass(frozen=True)
class AvailabilityPolicy:
    mode: DataMode = "strict_verified"
    assume_previous_day_prices: bool = True
    assume_previous_day_flows: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"strict_verified", "research_assumptions", "synthetic_fixture"}:
            raise ValueError("Unknown data-availability mode")

    def select(self, rows: list[dict], dataset: str, cutoff: str) -> tuple[list[dict], list[dict]]:
        """No revenue publication assumptions, no same-session final prices/flows.

        In research mode, previous-session EOD availability is explicit and
        assumption-dependent. Revisions fetched after historical cutoff cannot
        be authenticated as historical revisions and are labeled accordingly.
        """
        if dataset not in {"prices", "flows", "revenue", "market"}:
            raise ValueError("Unknown feature input dataset")
        instant = aware(cutoff).astimezone(TAIPEI)
        selected, excluded = [], []
        seen = set()
        for original in sorted(
            rows,
            key=lambda r: (
                r.get("ticker", ""),
                r.get("observation_date", ""),
                r.get("fetched_at", ""),
            ),
            reverse=True,
        ):
            row = dict(original)
            observation = row.get("observation_date") or row.get("trade_date")
            if not observation:
                excluded.append({"ticker": row.get("ticker"), "reason": "missing_observation_date"})
                continue
            obs_day = datetime.fromisoformat(observation).date()
            key = (row.get("ticker"), observation)
            if obs_day > instant.date() or (
                dataset in {"prices", "flows", "market"} and obs_day >= instant.date()
            ):
                excluded.append(
                    {
                        "ticker": row.get("ticker"),
                        "date": observation,
                        "reason": "future_or_same_session_final_data",
                    }
                )
                continue
            synthetic = row.get("synthetic", False) or row.get("source") == "SYNTHETIC_FIXTURE"
            if self.mode == "synthetic_fixture" and not synthetic:
                raise ValueError("Fixture mode requires exclusively labeled synthetic inputs")
            if synthetic and self.mode != "synthetic_fixture":
                raise ValueError("Synthetic input forbidden outside fixture mode")
            verified = filter_available_data([row], cutoff, strict=True)
            if dataset == "revenue":
                # A first observed snapshot is adequate live, but never imply an
                # unknown original publication date was available historically.
                if (
                    row.get("published_at")
                    and row.get("availability_verified")
                    and aware(row["published_at"]) <= instant
                ):
                    row["available_at"] = row["published_at"]
                    verified = filter_available_data([row], cutoff, strict=True)
                elif not verified:
                    excluded.append(
                        {
                            "ticker": row.get("ticker"),
                            "date": observation,
                            "reason": "unknown_revenue_publication",
                        }
                    )
                    continue
            if verified:
                row["availability_assumption"] = None
            elif self.mode == "synthetic_fixture" and synthetic:
                available = row.get("available_at") or row.get("published_at")
                if not available or aware(available) > instant:
                    excluded.append(
                        {
                            "ticker": row.get("ticker"),
                            "date": observation,
                            "reason": "fixture_not_yet_available",
                        }
                    )
                    continue
                row["availability_assumption"] = "SYNTHETIC_FIXTURE"
            elif self.mode == "research_assumptions" and (
                (dataset == "prices" and self.assume_previous_day_prices)
                or (dataset == "flows" and self.assume_previous_day_flows)
            ):
                assumed = datetime.combine(obs_day + timedelta(days=1), time.min, TAIPEI)
                if assumed > instant:
                    continue
                row["available_at"] = assumed.isoformat()
                row["availability_verified"] = False
                row["availability_assumption"] = (
                    "Previous-session EOD assumed available next calendar day; historical revision provenance unverified"
                )
            else:
                excluded.append(
                    {
                        "ticker": row.get("ticker"),
                        "date": observation,
                        "reason": "unverified_historical_availability",
                    }
                )
                continue
            if key not in seen:
                seen.add(key)
                selected.append(row)
        return (
            sorted(selected, key=lambda r: (r.get("ticker", ""), r["observation_date"])),
            excluded,
        )
