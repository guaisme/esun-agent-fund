from __future__ import annotations
import logging
from .providers.base import days, ProviderError
from .quality import DataQualityValidator
from .storage import DataStore
from .universe import UniverseLoader
from .providers.finmind import FinMindProvider
from .providers.twse import TWSEProvider
from .providers.tpex import TPExProvider

LOG = logging.getLogger(__name__)


class Downloader:
    def __init__(
        self,
        universe: UniverseLoader,
        store: DataStore,
        providers: dict[str, TWSEProvider | TPExProvider],
        fallback: FinMindProvider | None = None,
    ) -> None:
        self.universe, self.store, self.providers, self.fallback = (
            universe,
            store,
            providers,
            fallback,
        )

    def download(
        self,
        start: str,
        end: str,
        ticker: str | None = None,
        dataset: str = "prices",
        refresh: bool = False,
    ) -> dict:
        if dataset not in {"prices", "flows"}:
            raise ValueError("Batch downloader supports prices/flows")
        stocks = [self.universe.get_stock_info(ticker)] if ticker else self.universe.load_universe()
        self.store.set_universe(self.universe.load_universe())
        failures = []
        written = 0
        skipped = 0
        dates = list(days(start, end))
        validator = DataQualityValidator(self.universe)
        for index, day in enumerate(dates, 1):
            for market, p in self.providers.items():
                selected = {s["ticker"] for s in stocks if s["market"] == market}
                if not selected:
                    continue
                # Full-market checkpoint is only skipped for a full-universe request.
                if not ticker and not refresh and self.store.completed(dataset, market, day):
                    skipped += 1
                    continue
                try:
                    try:
                        snapshot = p.snapshot(day, flows=dataset == "flows")
                    except ProviderError:
                        if self.fallback is None or dataset != "prices":
                            raise
                        LOG.warning(
                            "official_provider_unavailable fallback=FinMind market=%s day=%s",
                            market,
                            day,
                        )
                        snapshot = []
                        for t in sorted(selected):
                            try:
                                snapshot.extend(self.fallback.get_daily_prices(t, day, day))
                            except ProviderError as fallback_error:
                                self.store.failure(dataset, t, day, fallback_error)
                        if not snapshot:
                            raise ProviderError("All configured price providers failed")
                    rows = [r for r in snapshot if r["ticker"] in selected]
                    errors = [i for i in validator.validate(rows, dataset) if i["level"] == "ERROR"]
                    if errors:
                        raise ProviderError("Data quality rejected snapshot: " + errors[0]["code"])
                    missing = selected - {r["ticker"] for r in rows}
                    if snapshot and missing and self.fallback and dataset == "prices":
                        for t in sorted(missing):
                            extra = self.fallback.get_daily_prices(t, day, day)
                            if any(
                                i["level"] == "ERROR" for i in validator.validate(extra, dataset)
                            ):
                                raise ProviderError("Fallback quality error")
                            rows.extend(extra)
                        missing = selected - {r["ticker"] for r in rows}
                    written += self.store.save(dataset, rows)
                    status = (
                        "complete"
                        if snapshot and not missing and not ticker
                        else "partial" if snapshot else "empty_unverified"
                    )
                    # A single ticker must never invalidate a previously complete full snapshot.
                    if not ticker or not self.store.completed(dataset, market, day):
                        self.store.checkpoint(dataset, market, day, status, bool(snapshot))
                    if snapshot and missing:
                        for t in sorted(missing):
                            failures.append(
                                dict(
                                    ticker=t,
                                    date=day,
                                    error="missing_observation_possible_suspension",
                                )
                            )
                            self.store.failure(
                                dataset, t, day, ProviderError("Missing observation")
                            )
                except Exception as error:
                    LOG.error(
                        "download_failed market=%s day=%s type=%s",
                        market,
                        day,
                        type(error).__name__,
                    )
                    self.store.failure(dataset, ticker or market, day, error)
                    self.store.checkpoint(dataset, market, day, "failed", False)
                    failures.append(
                        dict(ticker=ticker or market, date=day, error=type(error).__name__)
                    )
            print(
                f"{dataset} {index}/{len(dates)} {day} written={written} failures={len(failures)}",
                flush=True,
            )
        return dict(written=written, skipped_market_days=skipped, failures=failures)
