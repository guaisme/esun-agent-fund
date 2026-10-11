"""All rows are SYNTHETIC fixtures; no downloaded data or API calls."""

from datetime import date, timedelta

import pytest

from src.strategy.availability import AvailabilityPolicy
from src.strategy.features import FeatureEngine, FeatureSnapshot
from src.strategy.ranking import AlphaConfig, AlphaModel


def rows(ticker="2330", count=25):
    start = date(2026, 8, 3)
    days = []
    while len(days) < count:
        if start.weekday() < 5:
            days.append(start.isoformat())
        start += timedelta(days=1)
    return [
        dict(
            ticker=ticker,
            observation_date=on,
            trade_date=on,
            close=100.0 + i,
            volume=1000000,
            price_unit="TWD/share",
            volume_unit="shares",
            source="SYNTHETIC_FIXTURE",
            synthetic=True,
            fetched_at=f"{on}T18:00:00+08:00",
            available_at=f"{on}T18:00:00+08:00",
            availability_verified=False,
        )
        for i, on in enumerate(days)
    ]


def test_feature_golden_momentum_volatility_and_flow_units():
    prices = rows()
    flows = [
        {
            **r,
            "flow_unit": "shares",
            "foreign_net_buy": 10000,
            "investment_trust_net_buy": -2000,
            "dealer_net_buy": 1000,
        }
        for r in prices
    ]
    snapshot = FeatureEngine(AvailabilityPolicy("synthetic_fixture")).build(
        prices=prices,
        flows=flows,
        revenue=[],
        cutoff="2026-09-08T08:30:00+08:00",
        trading_days={r["trade_date"] for r in prices},
    )
    factors = snapshot.values["2330"]
    assert factors["momentum_5d"] == pytest.approx(124 / 119 - 1)
    assert factors["momentum_20d"] == pytest.approx(124 / 104 - 1)
    assert factors["foreign_flow_5d"] == pytest.approx(0.01)
    assert factors["trust_flow_20d"] == pytest.approx(-0.002)
    assert factors["dealer_flow_10d"] == pytest.approx(0.001)
    assert factors["drawdown_20d"] == 0
    assert factors["volatility_20d"] > 0
    assert factors["relative_strength_20d"] is None
    assert factors["revenue_acceleration"] is None
    assert snapshot.mode == "synthetic_fixture"


def test_future_same_day_extreme_price_cannot_change_snapshot():
    prices = rows()
    policy = AvailabilityPolicy("synthetic_fixture")
    baseline = FeatureEngine(policy).build(
        prices=prices, flows=[], revenue=[], cutoff="2026-09-08T08:30:00+08:00"
    )
    future = {
        **prices[-1],
        "observation_date": "2026-09-08",
        "close": 999999999,
        "available_at": "2026-09-08T08:29:00+08:00",
    }
    result = FeatureEngine(policy).build(
        prices=prices + [future], flows=[], revenue=[], cutoff="2026-09-08T08:30:00+08:00"
    )
    assert result.values == baseline.values
    assert result.excluded[-1]["reason"] == "future_or_same_session_final_data"


def test_unknown_revenue_cannot_leak_into_historical_research():
    revenue = dict(
        ticker="2330",
        observation_date="2026-08-01",
        revenue_year=2026,
        revenue_month=8,
        revenue_yoy=999,
        revenue_mom=888,
        published_at=None,
        available_at="2026-10-11T08:00:00+08:00",
        availability_verified=True,
        source="FinMind",
    )
    selected, excluded = AvailabilityPolicy("research_assumptions").select(
        [revenue], "revenue", "2026-09-08T08:30:00+08:00"
    )
    assert not selected and excluded[0]["reason"] == "unknown_revenue_publication"


def test_strict_snapshot_excludes_today_fetched_history():
    row = {
        **rows()[0],
        "synthetic": False,
        "source": "TWSE",
        "available_at": "2026-10-11T08:00:00+08:00",
        "availability_verified": True,
    }
    strict, _ = AvailabilityPolicy().select([row], "prices", "2026-09-08T08:30:00+08:00")
    research, _ = AvailabilityPolicy("research_assumptions").select(
        [row], "prices", "2026-09-08T08:30:00+08:00"
    )
    assert not strict
    assert len(research) == 1 and not research[0]["availability_verified"]
    assert research[0]["availability_assumption"]


def test_synthetic_inputs_cannot_become_real_backtest():
    with pytest.raises(ValueError, match="Synthetic"):
        AvailabilityPolicy("research_assumptions").select(
            rows(), "prices", "2026-09-08T08:30:00+08:00"
        )


def test_missing_history_or_session_not_zero_filled():
    prices = rows()
    prices[-3]["close"] = None
    result = FeatureEngine(AvailabilityPolicy("synthetic_fixture")).build(
        prices=prices, flows=[], revenue=[], cutoff="2026-09-08T08:30:00+08:00"
    )
    assert result.values["2330"]["momentum_5d"] is None
    prices = rows()
    calendar = {r["observation_date"] for r in prices}
    del prices[-3]
    result = FeatureEngine(AvailabilityPolicy("synthetic_fixture")).build(
        prices=prices,
        flows=[],
        revenue=[],
        cutoff="2026-09-08T08:30:00+08:00",
        trading_days=calendar,
    )
    assert result.values["2330"]["momentum_20d"] is None


def test_market_relative_strength_requires_matching_verified_series():
    prices = rows()
    market = [{**r, "ticker": "TAIEX", "close": 100.0} for r in prices]
    result = FeatureEngine(AvailabilityPolicy("synthetic_fixture")).build(
        prices=prices, flows=[], revenue=[], market=market, cutoff="2026-09-08T08:30:00+08:00"
    )
    assert result.values["2330"]["relative_strength_20d"] == result.values["2330"]["momentum_20d"]


def test_revenue_acceleration_only_consecutive_published_months():
    revenue = [
        dict(
            ticker="2330",
            observation_date=f"2026-{month:02d}-01",
            revenue_year=2026,
            revenue_month=month,
            revenue_yoy=yoy,
            revenue_mom=1.0,
            published_at=f"2026-{month + 1:02d}-05T18:00:00+08:00",
            available_at=f"2026-{month + 1:02d}-05T18:00:00+08:00",
            availability_verified=True,
            synthetic=True,
            source="SYNTHETIC_FIXTURE",
        )
        for month, yoy in [(6, 10.0), (7, 15.0)]
    ]
    engine = FeatureEngine(AvailabilityPolicy("synthetic_fixture"))
    snapshot = engine.build(
        prices=rows(), flows=[], revenue=revenue, cutoff="2026-09-08T08:30:00+08:00"
    )
    assert snapshot.values["2330"]["revenue_acceleration"] == 5
    revenue[0]["revenue_month"] = 5
    assert (
        engine.build(
            prices=rows(), flows=[], revenue=revenue, cutoff="2026-09-08T08:30:00+08:00"
        ).values["2330"]["revenue_acceleration"]
        is None
    )


def snapshot(values):
    return FeatureSnapshot(
        "2026-09-08T08:30:00+08:00", "synthetic_fixture", "fixture-1", values, {}, (), ()
    )


def test_cross_section_zscore_and_explicit_missing_policy():
    result = AlphaModel(AlphaConfig(weights={"a": 1.0})).rank(
        snapshot({"2330": {"a": 1.0}, "2454": {"a": 2.0}, "2317": {"a": None}})
    )
    assert [r["ticker"] for r in result["rankings"]] == ["2454", "2330"]
    assert result["rankings"][0]["score"] == pytest.approx(1)
    assert "2317" in result["excluded"]
    result = AlphaModel(
        AlphaConfig(weights={"a": 0.8, "b": 0.2}, missing_policy="renormalize")
    ).rank(snapshot({"2330": {"a": 1.0}}))
    assert result["rankings"][0]["missing_factors"] == ["b"]
    assert result["rankings"][0]["factor_coverage"] == pytest.approx(0.8)


def test_unknown_baseline_and_nonfinite_config_rejected():
    with pytest.raises(ValueError):
        AlphaConfig(weights={"a": float("nan")})
    with pytest.raises(ValueError):
        AlphaModel().rank(snapshot({}), "predictive_winner")
