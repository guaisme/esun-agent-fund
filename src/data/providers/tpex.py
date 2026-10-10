from __future__ import annotations
from .base import BaseProvider, ProviderError, days, price_row, provenance, number
from ..http import HttpClient


class TPExProvider(BaseProvider):
    name, market = "TPEx", "TPEx"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def snapshot(self, day: str, flows: bool = False) -> list[dict]:
        endpoint = "insti/dailyTrade" if flows else "afterTrading/dailyQuotes"
        p, fetched, raw = self.client.get(
            "https://www.tpex.org.tw/www/zh-tw/" + endpoint,
            dict(
                date=day.replace("-", "/"),
                response="json",
                **({"type": "Daily"} if flows else {"id": ""}),
            ),
        )
        tables = p.get("tables")
        if not isinstance(tables, list) or not tables:
            raise ProviderError("TPEx unexpected schema")
        t = tables[0]
        fields = t.get("fields", [])
        data = t.get("data", [])
        if not data:
            # Empty response is recorded as no observations, not a verified holiday.
            return []
        reported = str(t.get("date", p.get("date", "")))
        expected_roc = f"{int(day[:4])-1911}/{day[5:7]}/{day[8:]}"
        if reported not in {day.replace("-", ""), day.replace("-", "/"), expected_roc}:
            raise ProviderError("TPEx response date mismatch")
        if flows:
            if (
                len(fields) != 24
                or fields[:2] != ["代號", "名稱"]
                or fields[23] != "三大法人買賣超股數合計"
            ):
                raise ProviderError("TPEx flows schema changed")
            if fields[2:23] != ["買進股數", "賣出股數", "買賣超股數"] * 7:
                raise ProviderError("TPEx flows group layout changed")
        else:
            if not {"代號", "開盤", "最高", "最低", "收盤", "成交股數", "成交金額(元)"} <= set(
                fields
            ):
                raise ProviderError("TPEx prices schema changed")
        result = []
        for v in data:
            if len(v) != len(fields):
                raise ProviderError("TPEx row width changed")
            ticker = v[0].strip()
            if flows:
                row = provenance(ticker, self.market, day, self.name, fetched, raw)
                row.update(
                    trade_date=day,
                    foreign_net_buy=number(v[4], True),
                    investment_trust_net_buy=number(v[13], True),
                    dealer_net_buy=number(v[22], True),
                    flow_unit="shares",
                    foreign_definition="excludes foreign dealer",
                )
            else:
                r = dict(zip(fields, v))
                row = price_row(
                    ticker,
                    self.market,
                    day,
                    self.name,
                    [r[k] for k in ["開盤", "最高", "最低", "收盤"]],
                    r["成交股數"],
                    r["成交金額(元)"],
                    fetched,
                    raw,
                )
                row["provider_average"] = number(r.get("均價"))
            result.append(row)
        return result

    def get_daily_prices(self, ticker: str, start_date: str, end_date: str) -> list[dict]:
        return [
            r for d in days(start_date, end_date) for r in self.snapshot(d) if r["ticker"] == ticker
        ]
