from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from src.data.http import HttpClient
from src.data.providers.base import ProviderError


@dataclass(frozen=True)
class TradingCalendar:
    year: int
    closed_dates: frozenset[str]
    explicit_open_dates: frozenset[str]
    source_url: str
    fetched_at: str
    raw_ref: str

    def sessions(self, start: str, end: str) -> set[str]:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.year != self.year or last.year != self.year or first > last:
            raise ValueError("Calendar dates must belong to the verified year")
        result = set()
        on = first
        while on <= last:
            day = on.isoformat()
            if day in self.explicit_open_dates or (
                on.weekday() < 5 and day not in self.closed_dates
            ):
                result.add(day)
            on += timedelta(days=1)
        return result

    def reconcile_observed(self, observed: set[str]) -> list[dict]:
        sessions = self.sessions(f"{self.year}-01-01", f"{self.year}-12-31")
        return [
            {
                "severity": "ERROR",
                "code": "price_on_calendar_closed_day",
                "date": day,
                "message": "Official annual calendar and observed exchange payload disagree; must investigate, never silently override",
            }
            for day in sorted(observed - sessions)
        ]


class TWSECalendarProvider:
    URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule"

    def __init__(self, client: HttpClient):
        self.client = client

    def get_year(self, year: int) -> TradingCalendar:
        payload, fetched, raw = self.client.get(
            self.URL, {"queryYear": year - 1911, "response": "json"}
        )
        return self.parse(payload, year, fetched, raw)

    @classmethod
    def parse(cls, payload: dict, year: int, fetched: str, raw: str) -> TradingCalendar:
        if (
            payload.get("stat") != "ok"
            or payload.get("queryYear") != year
            or payload.get("fields") != ["日期", "名稱", "說明"]
            or not isinstance(payload.get("data"), list)
            or payload.get("total") != len(payload["data"])
        ):
            raise ProviderError("TWSE calendar schema/year changed")
        opened, closed = set(), set()
        for row in payload["data"]:
            if len(row) != 3 or date.fromisoformat(row[0]).year != year:
                raise ProviderError("Invalid annual calendar row")
            on, name, description = row
            if "開始交易日" in name or "最後交易日" in name:
                opened.add(on)
            elif "無交易" in name or "放假" in description or "補假" in description:
                closed.add(on)
            else:
                raise ProviderError("Unknown calendar classification; manual verification required")
        if opened & closed:
            raise ProviderError("Contradictory annual calendar")
        return TradingCalendar(year, frozenset(closed), frozenset(opened), cls.URL, fetched, raw)
