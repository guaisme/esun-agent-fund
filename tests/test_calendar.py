"""MOCK annual calendar parser tests, not live exchange-calendar verification."""

import pytest
from src.competition.calendar import TWSECalendarProvider
from src.data.providers.base import ProviderError


def payload():
    return {
        "stat": "ok",
        "queryYear": 2026,
        "fields": ["日期", "名稱", "說明"],
        "total": 2,
        "data": [
            ["2026-10-26", "測試補假", "依規定補假。"],
            ["2026-01-02", "國曆新年開始交易日", "開始交易。"],
        ],
    }


def test_holiday_weekends_and_first_competition_session():
    calendar = TWSECalendarProvider.parse(payload(), 2026, "2026-10-11T05:00:00+08:00", "MOCK")
    sessions = calendar.sessions("2026-10-26", "2026-11-27")
    assert len(sessions) == 24 and min(sessions) == "2026-10-27"
    assert "2026-10-31" not in sessions
    assert calendar.reconcile_observed({"2026-10-26"})[0]["severity"] == "ERROR"


def test_unknown_calendar_classification_and_wrong_year_fail_closed():
    value = payload()
    value["data"][0] = ["2026-10-26", "未知特殊事件", "待確認"]
    with pytest.raises(ProviderError):
        TWSECalendarProvider.parse(value, 2026, "MOCK", "MOCK")
    with pytest.raises(ProviderError):
        TWSECalendarProvider.parse(payload(), 2025, "MOCK", "MOCK")
