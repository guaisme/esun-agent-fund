from .base import Row
from .twse import TWSEProvider
from .tpex import TPExProvider
from ..universe import UniverseLoader


class InstitutionalFlowProvider:
    """Official daily share flows; foreign excludes foreign-dealer proprietary trades."""

    def __init__(self, twse: TWSEProvider, tpex: TPExProvider, universe: UniverseLoader) -> None:
        self.providers = {"TWSE": twse, "TPEx": tpex}
        self.universe = universe

    def get_daily_flows(self, ticker: str, start_date: str, end_date: str) -> list[Row]:
        from .base import days

        p = self.providers[self.universe.get_stock_info(ticker)["market"]]
        return [
            r
            for d in days(start_date, end_date)
            for r in p.snapshot(d, flows=True)
            if r["ticker"] == ticker
        ]
