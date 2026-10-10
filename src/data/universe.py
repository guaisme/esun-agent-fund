from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class UniverseError(ValueError):
    pass


class UniverseLoader:
    def __init__(
        self,
        path: Path | str = ROOT / "config/universe.csv",
        manifest: Path | str = ROOT / "config/universe_manifest.json",
    ) -> None:
        self.path, self.manifest = Path(path), Path(manifest)
        self.stocks: dict[str, dict[str, str]] = {}

    def load_universe(self) -> list[dict[str, str]]:
        try:
            with self.path.open(encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames != ["ticker", "name", "market"]:
                    raise UniverseError("CSV columns must be ticker,name,market")
                rows = list(reader)
            canonical = json.loads(self.manifest.read_text(encoding="utf-8"))["stocks"]
        except (OSError, KeyError, json.JSONDecodeError) as error:
            raise UniverseError("Official universe CSV/manifest missing or unreadable") from error
        if len(rows) != 150:
            raise UniverseError(f"Expected 150 stocks, received {len(rows)}")
        tickers = [r["ticker"] for r in rows]
        if len(set(tickers)) != len(tickers):
            raise UniverseError("Duplicate ticker")
        for row in rows:
            if not isinstance(row["ticker"], str) or not re.fullmatch(r"[0-9]{4}", row["ticker"]):
                raise UniverseError(f"Invalid ticker: {row['ticker']}")
            if not isinstance(row["name"], str) or not row["name"].strip():
                raise UniverseError("Empty stock name")
            if row["market"] not in {"TWSE", "TPEx"}:
                raise UniverseError("Invalid market")
        if (
            sum(r["market"] == "TWSE" for r in rows) != 100
            or sum(r["market"] == "TPEx" for r in rows) != 50
        ):
            raise UniverseError("Expected TWSE=100 and TPEx=50")
        if sorted(rows, key=lambda r: r["ticker"]) != sorted(canonical, key=lambda r: r["ticker"]):
            raise UniverseError("Universe differs from the verified official PDF manifest")
        self.stocks = {r["ticker"]: r for r in rows}
        return [dict(r) for r in rows]

    def _ensure(self) -> None:
        if not self.stocks:
            self.load_universe()

    def get_all_tickers(self) -> list[str]:
        self._ensure()
        return list(self.stocks)

    def get_twse_tickers(self) -> list[str]:
        self._ensure()
        return [t for t, r in self.stocks.items() if r["market"] == "TWSE"]

    def get_tpex_tickers(self) -> list[str]:
        self._ensure()
        return [t for t, r in self.stocks.items() if r["market"] == "TPEx"]

    def is_valid_ticker(self, ticker: str) -> bool:
        self._ensure()
        return ticker in self.stocks

    def get_stock_info(self, ticker: str) -> dict[str, str]:
        self._ensure()
        if ticker not in self.stocks:
            raise UniverseError(f"Ticker not in official universe: {ticker}")
        return dict(self.stocks[ticker])
