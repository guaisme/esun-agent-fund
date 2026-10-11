from __future__ import annotations
import argparse
import json
import logging
import os
from pathlib import Path
from .universe import UniverseLoader, ROOT
from .storage import DataStore
from .http import HttpClient
from .providers.twse import TWSEProvider
from .providers.tpex import TPExProvider
from .providers.finmind import FinMindProvider, MonthlyRevenueProvider
from .download import Downloader
from .quality import DataQualityValidator
from .providers.base import ProviderError


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / "research_agent/.env", override=False)
    parser = argparse.ArgumentParser(
        description="ESUN official universe and market data infrastructure"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("universe")
    for name in ["download", "flows", "revenue", "query", "quality", "session-quality"]:
        p = sub.add_parser(name)
        p.add_argument("--ticker")
        p.add_argument("--start")
        p.add_argument("--end")
        if name in {"download", "flows", "revenue"}:
            p.add_argument("--refresh", action="store_true")
            p.add_argument("--interval", type=float, default=2)
            p.add_argument("--timeout", type=float, default=20)
            p.add_argument("--max-retries", type=int, default=3)
        if name in {"query", "quality", "session-quality"}:
            p.add_argument(
                "--dataset", choices=["prices", "flows", "revenue"], default="prices"
            )
        if name == "query":
            p.add_argument(
                "--as-of",
                help="Timezone-aware ISO timestamp; strict availability filter",
            )
        if name == "quality":
            p.add_argument("--min-days", type=int, default=120)
        if name == "session-quality":
            p.add_argument("--calendar", type=Path, required=True)
        p.add_argument("--output", type=Path)
    a = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    universe = UniverseLoader()
    universe.load_universe()
    if a.command == "universe":
        print(
            json.dumps(
                dict(
                    total=len(universe.get_all_tickers()),
                    TWSE=len(universe.get_twse_tickers()),
                    TPEx=len(universe.get_tpex_tickers()),
                    official_manifest_verified=True,
                )
            )
        )
        return 0
    store = DataStore(a.data_dir)
    store.set_universe(universe.load_universe())
    try:
        if a.ticker:
            universe.get_stock_info(a.ticker)
        if a.command in {"download", "flows", "revenue"}:
            if not a.start or not a.end:
                parser.error("--start and --end required for downloads")
            client = HttpClient(
                a.data_dir / "raw",
                timeout=a.timeout,
                max_retries=a.max_retries,
                interval=a.interval,
                refresh=a.refresh,
            )
            fallback = (
                FinMindProvider(client, universe)
                if os.getenv("FINMIND_ENABLED") == "1"
                else None
            )
            if a.command == "revenue":
                if not fallback:
                    raise ValueError(
                        "Revenue currently requires explicit FINMIND_ENABLED=1"
                    )
                from .revenue_download import download_revenue

                result = download_revenue(
                    MonthlyRevenueProvider(fallback),
                    universe,
                    store,
                    a.start,
                    a.end,
                    a.ticker,
                )
            else:
                result = Downloader(
                    universe,
                    store,
                    {"TWSE": TWSEProvider(client), "TPEx": TPExProvider(client)},
                    fallback,
                ).download(
                    a.start,
                    a.end,
                    a.ticker,
                    "flows" if a.command == "flows" else "prices",
                    a.refresh,
                )
        elif a.command == "query":
            result = store.query(a.dataset, a.ticker, a.start, a.end, a.as_of)
        elif a.command == "session-quality":
            from src.pipeline.daily import read_calendar
            from .session_quality import session_quality

            if not a.start or not a.end or a.start[:4] != a.end[:4]:
                parser.error("session-quality requires same-year --start and --end")
            calendar = read_calendar(a.calendar, int(a.start[:4]))
            rows = store.query(a.dataset, start=a.start, end=a.end)
            updates = [
                dict(market=m, date=d, status=s)
                for m, d, s in store.db.execute(
                    "SELECT market,day,status FROM updates WHERE dataset=? AND day>=? AND day<=?",
                    (a.dataset, a.start, a.end),
                )
            ]
            result = session_quality(rows, updates, {"TWSE": calendar})
        else:
            result = DataQualityValidator(universe).report(
                store, a.dataset, a.start, a.end, a.min_days
            )
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            a.output.write_text(rendered, encoding="utf-8")
            print("Saved:", a.output)
        else:
            print(rendered)
        return (
            2
            if isinstance(result, dict)
            and (
                result.get("failures")
                or result.get("empty_stocks")
                or result.get("unattempted")
                or result.get("quality_counts", {}).get("ERROR")
                or (
                    a.command == "quality"
                    and (
                        result.get("below_target")
                        or result.get("unverified_or_failed_days")
                    )
                )
            )
            else 0
        )
    except (ValueError, RuntimeError) as error:
        # No raw SDK exception/credentials in CLI logs.
        logging.error("Operation failed type=%s", type(error).__name__)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
