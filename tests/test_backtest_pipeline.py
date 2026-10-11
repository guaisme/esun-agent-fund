"""SYNTHETIC complete replay and backtest tests; no network or API usage."""

from datetime import date, timedelta
from decimal import Decimal as D

from src.data.universe import ROOT, UniverseLoader
from src.pipeline.offline import replay_fixture
from src.strategy.availability import AvailabilityPolicy
from src.strategy.backtest import FactorBacktester, spearman, walk_forward_splits


def test_complete_multiday_fixture_evidence_orders_costs_and_next_state(tmp_path):
    report = replay_fixture(ROOT / "tests/fixtures/competition_case.json", tmp_path)
    assert report["status"] == "OFFLINE_FIXTURE_PASSED"
    assert not report["live_model_called"] and report["synthetic"]
    assert report["terminal_nav"] == D("998620150")
    history = report["history"]
    assert [len(r["plan"]["orders"]) for r in history] == [20, 0, 2, 2]
    assert {d["action"] for d in history[2]["plan"]["decisions"]} == {"ADD", "TRIM"}
    assert {d["action"] for d in history[3]["plan"]["decisions"]} == {"SELL_ALL", "BUY"}
    assert all(r["validation"].status == "VALID" for r in history)
    assert all(r["result"].state.active_share_streaks == {"SYNTHETIC_ETF": 0} for r in history)
    assert report["final_state"].positions["1101"].shares == 410000
    assert "1301" not in report["final_state"].positions
    assert len(list((tmp_path / "dplans").glob("*.json"))) == 4


def test_fixture_replay_is_deterministic():
    path = ROOT / "tests/fixtures/competition_case.json"
    assert replay_fixture(path) == replay_fixture(path)


def test_walkforward_no_overlap_no_final_test_tuning():
    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(140)]
    splits = walk_forward_splits(days)
    assert len(splits) == 3
    assert len(splits[0].validation) == len(splits[0].test) == 24
    assert all(
        max(split.training) < min(split.validation) < max(split.validation) < min(split.test)
        for split in splits
    )
    assert len(splits[-1].training) > len(splits[0].training)
    rolling = walk_forward_splits(days, expanding=False)
    assert all(len(split.training) == 40 for split in rolling)


def test_rank_ic_ties_and_constant_scores():
    assert spearman([1, 2, 3], [3, 2, 1]) == -1
    assert spearman([1, 1, 2, 3], [1, 1, 2, 3]) == 1
    assert spearman([1, 1, 1], [1, 2, 3]) is None


def test_strict_backtest_never_promotes_unverified_data():
    report = FactorBacktester().run(
        prices=[], flows=[], revenue=[], start="2026-09-01", end="2026-10-08"
    )
    assert report["status"] == "BLOCKED" and report["metrics"] is None


def test_synthetic_baseline_backtest_has_separate_decision_execution_dates():
    tickers = sorted(UniverseLoader().get_all_tickers())[:22]
    days = []
    on = date(2026, 8, 3)
    while len(days) < 24:
        if on.weekday() < 5:
            days.append(on.isoformat())
        on += timedelta(days=1)
    prices = [
        dict(
            ticker=t,
            market=UniverseLoader().get_stock_info(t)["market"],
            trade_date=day,
            observation_date=day,
            close=100.0 + i * (1 + j / 100),
            volume=1000000,
            turnover=int((100 + i * (1 + j / 100)) * 1000000),
            source="SYNTHETIC_FIXTURE",
            synthetic=True,
            price_unit="TWD/share",
            volume_unit="shares",
            turnover_unit="TWD",
            available_at=f"{day}T18:00:00+08:00",
            fetched_at=f"{day}T18:00:00+08:00",
            availability_verified=False,
        )
        for i, day in enumerate(days)
        for j, t in enumerate(tickers)
    ]
    report = FactorBacktester(availability=AvailabilityPolicy("synthetic_fixture")).run(
        prices=prices,
        flows=[],
        revenue=[],
        start=days[21],
        end=days[-1],
        baseline="momentum",
        calendar=set(days),
    )
    assert report["status"] == "COMPLETED_CONDITIONAL", report["audit"]
    assert report["sessions_simulated"] == 3 and report["synthetic"]
    assert all(row["decision_feature_latest_date"] < row["trade_date"] for row in report["audit"])
    assert report["metrics"]["commission_ntd"] != "0"
