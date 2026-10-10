from __future__ import annotations
from .base import BaseProvider, ProviderError, days, price_row, provenance, number
from ..http import HttpClient


class TWSEProvider(BaseProvider):
    name, market = "TWSE", "TWSE"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def snapshot(self, day: str, flows: bool = False) -> list[dict]:
        compact = day.replace("-", "")
        url = (
            "https://www.twse.com.tw/fund/T86"
            if flows
            else "https://www.twse.com.tw/exchangeReport/MI_INDEX"
        )
        params = dict(
            date=compact,
            response="json",
            **({"selectType": "ALLBUT0999"} if flows else {"type": "ALLBUT0999"}),
        )
        p, fetched, raw = self.client.get(url, params)
        if p.get("stat") != "OK":
            if "沒有符合" in str(p.get("stat", "")):
                return []
            raise ProviderError("TWSE unexpected status/schema")
        if p.get("date") != compact:
            raise ProviderError("TWSE response date mismatch")
        if flows:
            fields, data = p.get("fields", []), p.get("data", [])
        else:
            tables = [
                t
                for t in p.get("tables", [])
                if "證券代號" in t.get("fields", []) and "開盤價" in t.get("fields", [])
            ]
            if len(tables) != 1:
                raise ProviderError("TWSE prices schema changed")
            fields, data = tables[0]["fields"], tables[0]["data"]
        required = (
            ["證券代號", "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數", "自營商買賣超股數"]
            if flows
            else ["證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"]
        )
        if not set(required) <= set(fields):
            raise ProviderError("TWSE required columns missing")
        result = []
        for values in data:
            if len(values) != len(fields):
                raise ProviderError("TWSE row width changed")
            r = dict(zip(fields, values))
            ticker = r["證券代號"].strip()
            if flows:
                row = provenance(ticker, self.market, day, self.name, fetched, raw)
                row.update(
                    trade_date=day,
                    foreign_net_buy=number(r[required[1]], True),
                    investment_trust_net_buy=number(r[required[2]], True),
                    dealer_net_buy=number(r[required[3]], True),
                    flow_unit="shares",
                    foreign_definition="excludes foreign dealer",
                )
            else:
                row = price_row(
                    ticker,
                    self.market,
                    day,
                    self.name,
                    [r[k] for k in required[1:5]],
                    r["成交股數"],
                    r["成交金額"],
                    fetched,
                    raw,
                )
            result.append(row)
        return result

    def get_daily_prices(self, ticker: str, start_date: str, end_date: str) -> list[dict]:
        return [
            r for d in days(start_date, end_date) for r in self.snapshot(d) if r["ticker"] == ticker
        ]
