from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
import pyarrow as pa
import pyarrow.parquet as pq
from .time_safety import filter_available_data, now_iso, aware
from .quality import DataQualityValidator
from .universe import UniverseLoader


class DataStore:
    """SQLite metadata and immutable Parquet chunks. SQLite serializes writers.

    Chunks are published before catalog commits; interrupted unpublished chunks can
    remain on disk but are never queried. A resumed download writes an idempotent chunk.
    """

    DATASETS = {"prices", "flows", "revenue"}

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.validator = DataQualityValidator(UniverseLoader())
        (self.root / "metadata").mkdir(parents=True, exist_ok=True)
        (self.root / "processed").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "metadata" / "catalog.sqlite", timeout=30)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS universe(ticker TEXT PRIMARY KEY,name TEXT,market TEXT);
        CREATE TABLE IF NOT EXISTS records(dataset TEXT,ticker TEXT,observation TEXT,fingerprint TEXT,path TEXT,fetched_at TEXT,source TEXT,available_at TEXT,PRIMARY KEY(dataset,ticker,observation,fingerprint));
        CREATE TABLE IF NOT EXISTS updates(dataset TEXT,market TEXT,day TEXT,status TEXT,updated_at TEXT,PRIMARY KEY(dataset,market,day));
        CREATE TABLE IF NOT EXISTS calendar(market TEXT,day TEXT,PRIMARY KEY(market,day));
        CREATE TABLE IF NOT EXISTS failures(id INTEGER PRIMARY KEY,dataset TEXT,ticker TEXT,day TEXT,error_type TEXT,created_at TEXT);
        """)

    def close(self) -> None:
        self.db.close()

    def set_universe(self, rows: list[dict]) -> None:
        with self.db:
            self.db.execute("DELETE FROM universe")
            self.db.executemany("INSERT INTO universe VALUES(:ticker,:name,:market)", rows)

    def save(self, dataset: str, rows: list[dict]) -> int:
        if dataset not in self.DATASETS:
            raise ValueError("Unknown dataset")
        if not rows:
            return 0
        errors = [
            issue
            for issue in self.validator.validate(rows, dataset)
            if issue["level"] == "ERROR" and issue["code"] != "duplicate"
        ]
        if errors:
            raise ValueError("Storage rejected data quality: " + errors[0]["code"])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            fresh = []
            for r in rows:
                observation = r["observation_date"]
                ticker = r["ticker"]
                if not self.db.execute(
                    "SELECT 1 FROM universe WHERE ticker=?", (ticker,)
                ).fetchone():
                    raise ValueError("Ticker outside official universe")
                content = {
                    k: v for k, v in r.items() if k not in {"fetched_at", "available_at", "raw_ref"}
                }
                fingerprint = hashlib.sha256(
                    json.dumps(
                        content, sort_keys=True, ensure_ascii=False, allow_nan=False
                    ).encode()
                ).hexdigest()
                key = (dataset, ticker, observation, fingerprint)
                if self.db.execute(
                    "SELECT 1 FROM records WHERE dataset=? AND ticker=? AND observation=? AND fingerprint=?",
                    key,
                ).fetchone():
                    continue
                if any(x[0] == key for x in fresh):
                    continue
                fresh.append((key, dict(r, _fingerprint=fingerprint)))
            if fresh:
                identity = hashlib.sha256(
                    json.dumps([x[0] for x in fresh], sort_keys=True).encode()
                ).hexdigest()
                path = self.root / "processed" / dataset / (identity + ".parquet")
                path.parent.mkdir(exist_ok=True)
                temp = path.with_suffix(".tmp")
                # Arrow uses all rows when inferring optional fields (not only the first).
                table = pa.Table.from_pylist([r for _, r in fresh])
                pq.write_table(table, temp, compression="zstd")
                temp.replace(path)
                for key, r in fresh:
                    self.db.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                        (
                            *key,
                            str(path.relative_to(self.root)),
                            r["fetched_at"],
                            r["source"],
                            r.get("available_at"),
                        ),
                    )
            self.db.commit()
            return len(fresh)
        except BaseException:
            self.db.rollback()
            raise

    def query(
        self,
        dataset: str,
        ticker: str | None = None,
        start: str | None = None,
        end: str | None = None,
        as_of: str | None = None,
    ) -> list[dict]:
        if dataset not in self.DATASETS:
            raise ValueError("Unknown dataset")
        sql = "SELECT DISTINCT path FROM records WHERE dataset=?"
        args: list[Any] = [dataset]
        for clause, val in [
            ("ticker=?", ticker),
            ("observation>=?", start),
            ("observation<=?", end),
        ]:
            if val is not None:
                sql += " AND " + clause
                args.append(val)
        rows = []
        for (path,) in self.db.execute(sql, args):
            for r in pq.read_table(self.root / path).to_pylist():
                if ticker and r["ticker"] != ticker:
                    continue
                if start and r["observation_date"] < start:
                    continue
                if end and r["observation_date"] > end:
                    continue
                rows.append(r)
        if as_of:
            rows = filter_available_data(rows, as_of, strict=True)
        # Apply PIT BEFORE picking a revision; future revisions cannot displace older ones.
        latest = {}
        for r in sorted(rows, key=lambda x: aware(x["fetched_at"])):
            latest[(r["ticker"], r["observation_date"])] = r
        return sorted(latest.values(), key=lambda r: (r["ticker"], r["observation_date"]))

    def completed(self, dataset: str, market: str, day: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM updates WHERE dataset=? AND market=? AND day=? AND status='complete'",
                (dataset, market, day),
            ).fetchone()
            is not None
        )

    def checkpoint(self, dataset: str, market: str, day: str, status: str, trading: bool):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO updates VALUES(?,?,?,?,?)",
                (dataset, market, day, status, now_iso()),
            )
            if trading:
                self.db.execute("INSERT OR IGNORE INTO calendar VALUES(?,?)", (market, day))

    def failure(self, dataset: str, ticker: str, day: str, error: Exception):
        with self.db:
            self.db.execute(
                "INSERT INTO failures(dataset,ticker,day,error_type,created_at) VALUES(?,?,?,?,?)",
                (dataset, ticker, day, type(error).__name__, now_iso()),
            )
