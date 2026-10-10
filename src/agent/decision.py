from __future__ import annotations

import json
from decimal import Decimal
from datetime import timedelta
from src.competition.models import day
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.data.time_safety import aware
from src.data.universe import UniverseLoader


class Rationale(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premise_refs: list[str] = Field(min_length=1, max_length=10)
    logic: str = Field(min_length=20, max_length=1000)
    counter_evidence: str | None
    summary: str = Field(default="依資料評估後決定持股配置。", max_length=300)


class InvestmentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: str
    data_cutoff: str
    regime: Literal["risk_on", "neutral", "risk_off"]
    stance: Literal["aggressive", "neutral", "defensive"]
    investment_posture: Literal["increase", "hold", "reduce"]
    candidates: list[str] = Field(min_length=20, max_length=60)
    target_weights: dict[str, Decimal]
    rationale: dict[str, Rationale]
    observation_refs: list[str] = Field(min_length=1, max_length=10)
    market_logic: str = Field(min_length=20, max_length=1000)
    counter_evidence: str | None
    cash_range: tuple[Decimal, Decimal]
    additional_research_needed: list[str]
    model_version: str
    strategy_version: str

    @model_validator(mode="after")
    def check(self):
        universe = UniverseLoader()
        day(self.trade_date)
        if (
            aware(self.data_cutoff).utcoffset() != timedelta(hours=8)
            or self.data_cutoff[:10] > self.trade_date
        ):
            raise ValueError("Cutoff must be +08:00 and no later than trade date")
        if not {t for t, w in self.target_weights.items() if w > 0} <= set(self.candidates):
            raise ValueError("Positive targets must be explicitly selected candidates")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("Duplicate candidates")
        if any(
            not universe.is_valid_ticker(t) for t in set(self.candidates) | set(self.target_weights)
        ):
            raise ValueError("Unsupported stock")
        if set(self.rationale) != set(self.target_weights):
            raise ValueError("Every target requires rationale")
        if any(
            not value.is_finite()
            or value < 0
            or value > (Decimal("0.25") if ticker == "2330" else Decimal("0.10"))
            for ticker, value in self.target_weights.items()
        ):
            raise ValueError("Invalid or concentrated target weight")
        if sum(self.target_weights.values()) > 1:
            raise ValueError("Total targets exceed 100%")
        if not 0 <= self.cash_range[0] <= self.cash_range[1] < Decimal("0.25"):
            raise ValueError("Cash range must leave buffer below 25%")
        return self


def parse_decision(text: str) -> InvestmentDecision:
    # No fallback portfolio and no guessing a broken response. Markdown fences
    # are accepted only around one complete JSON document.
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    return InvestmentDecision.model_validate(json.loads(text, parse_float=Decimal))
