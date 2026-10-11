"""Replaceable, evidence-preserving benchmark acquisition interface.

No universal fund endpoint is assumed. The local adapter consumes reviewed
normalized exports with original file hashes, not ToAlpha bulk downloads.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlparse
from src.data.time_safety import aware
from src.pipeline.run_store import sha256
from src.portfolio.active_share import ActiveETFLoader, BenchmarkHoldings


class BenchmarkProvider(ABC):
    @abstractmethod
    def load(self, cutoff: str) -> tuple[list[BenchmarkHoldings], dict]: ...


class ReviewedFileBenchmarkProvider(BenchmarkProvider):
    def __init__(self, directory: Path):
        self.directory = directory

    def load(self, cutoff: str) -> tuple[list[BenchmarkHoldings], dict]:
        required = ActiveETFLoader().tickers()
        snapshots, problems = [], []
        for etf in sorted(required):
            path = self.directory / f"{etf}.json"
            if not path.exists():
                problems.append(
                    dict(
                        etf=etf,
                        required_as_of=cutoff,
                        code="missing_snapshot",
                        fields=[
                            "weights",
                            "content_as_of",
                            "available_at",
                            "source_url",
                            "original_file",
                            "original_sha256",
                        ],
                    )
                )
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                original = (self.directory / value["original_file"]).resolve()
                if (
                    not original.is_relative_to(self.directory.resolve())
                    or sha256(original) != value["original_sha256"]
                ):
                    raise ValueError("OriginalEvidenceMissingOrChanged")
                # Review is a local acquisition receipt, not an organizer certification.
                host = urlparse(value["source_url"]).hostname
                if (
                    not host
                    or urlparse(value["source_url"]).scheme != "https"
                    or host.endswith(".test")
                    or host in {"example.com", "example.org"}
                    or value.get("synthetic") is True
                ):
                    raise ValueError("InvalidSourceURL")
                if (
                    value.get("reviewed_source_domain") != host
                    or value.get("reviewed_original_sha256") != value["original_sha256"]
                ):
                    raise ValueError("SourceReviewRequired")
                if (
                    value.get("ticker") != etf
                    or value.get("weight_unit") != "fraction"
                    or value.get("complete_equity_holdings") is not True
                ):
                    raise ValueError("IncompleteOrAmbiguousHoldings")
                entries = value["holdings"]
                if len({r["ticker"] for r in entries}) != len(entries):
                    raise ValueError("DuplicateHoldings")
                row = BenchmarkHoldings(
                    etf,
                    {r["ticker"]: Decimal(str(r["weight"])) for r in entries},
                    value["content_as_of"],
                    value["available_at"],
                    value["source_url"],
                    True,
                    False,
                )
                # A freshly downloaded file cannot certify an earlier publication time.
                if aware(value["available_at"]) < aware(value["first_observed_at"]):
                    raise ValueError("HistoricalAvailabilityEvidenceRequired")
                row.validate(aware(cutoff), 7)
                snapshots.append(row)
            except (KeyError, ValueError, TypeError, OSError, ArithmeticError) as error:
                problems.append(
                    dict(etf=etf, required_as_of=cutoff, code=type(error).__name__)
                )
        return snapshots, {
            "status": "COMPLETE" if not problems else "BLOCKED",
            "required_etfs": len(required),
            "accepted": len(snapshots),
            "missing_or_invalid": problems,
            "official_method_confirmed": False,
            "note": "Reviewed acquisition receipt is not official methodology or historical publication certification.",
        }
