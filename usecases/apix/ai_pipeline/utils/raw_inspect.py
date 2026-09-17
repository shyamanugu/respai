"""Utility to inspect raw transcript files in Azure Blob storage.

Reports how many files exist, total record count, and per-program record
counts — without downloading full files (DuckDB pushes the aggregation down
to the parquet reader).

Usage
-----
    # All raw files in the container
    python -m ai_pipeline.utils.raw_inspect

    # A single date
    python -m ai_pipeline.utils.raw_inspect --date 2026-07-24

    # A date range (inclusive)
    python -m ai_pipeline.utils.raw_inspect --start 2026-07-18 --end 2026-07-24

    # Point at a different container (defaults to the raw container)
    python -m ai_pipeline.utils.raw_inspect --container denoised-transcripts
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import duckdb
import polars as pl

from ai_pipeline.programs_config.base import StorageConfig
from ai_pipeline.logging_config import get_logger
from ai_pipeline.services.storage import StorageService

logger = get_logger("utils.raw_inspect")

# Column that holds the program name in the raw parquet files.
PROGRAM_COLUMN = "ProgramName"


@dataclass
class FileReport:
    filename: str
    records: int
    programs: dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class InspectReport:
    container: str
    files_found: int
    files_read: int
    total_records: int
    program_counts: dict[str, int]
    per_file: list[FileReport]

    def print_summary(self) -> None:
        print(f"\n=== Raw file inspection | container='{self.container}' ===")
        print(f"Files found : {self.files_found}")
        print(f"Files read  : {self.files_read}")
        print(f"Total records: {self.total_records:,}")

        print("\nPrograms (name : count):")
        if self.program_counts:
            for name, cnt in sorted(self.program_counts.items(), key=lambda kv: kv[1], reverse=True):
                print(f"  {name:<40} {cnt:,}")
        else:
            print("  (none)")

        print("\nPer-file breakdown:")
        for fr in self.per_file:
            if fr.error:
                print(f"  {fr.filename:<28} ERROR: {fr.error}")
            else:
                print(f"  {fr.filename:<28} records={fr.records:,} programs={len(fr.programs)}")
        print()


def _date_range(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def _list_parquet(storage: StorageService, container: str) -> list[str]:
    """Return sorted parquet filenames (basename only) inside *container*."""
    try:
        entries = storage.fs.ls(container, detail=False)
    except Exception as exc:  # container missing / auth issue
        logger.error("Could not list container '%s': %s", container, exc)
        return []
    names = [e.split("/")[-1] for e in entries]
    return sorted(n for n in names if n.endswith(".parquet"))


def _inspect_file(storage: StorageService, container: str, filename: str) -> FileReport:
    path = f"abfs://{container}/{filename}"
    try:
        # Total record count (aggregation pushed into the parquet reader).
        total = duckdb.query(
            f"SELECT COUNT(*) AS n FROM read_parquet('{path}')"
        ).pl()["n"][0]

        programs: dict[str, int] = {}
        # Per-program counts only if the column exists.
        cols = duckdb.query(f"SELECT * FROM read_parquet('{path}') LIMIT 0").pl().columns
        if PROGRAM_COLUMN in cols:
            grp = duckdb.query(
                f"SELECT {PROGRAM_COLUMN} AS name, COUNT(*) AS n "
                f"FROM read_parquet('{path}') GROUP BY {PROGRAM_COLUMN}"
            ).pl()
            programs = {
                (row["name"] if row["name"] is not None else "<null>"): int(row["n"])
                for row in grp.iter_rows(named=True)
            }
        return FileReport(filename=filename, records=int(total), programs=programs)
    except Exception as exc:
        logger.error("Failed reading '%s': %s", filename, exc)
        return FileReport(filename=filename, records=0, error=str(exc))


def inspect_raw(
    storage: StorageService,
    container: str,
    filenames: Optional[list[str]] = None,
) -> InspectReport:
    """Inspect raw parquet files and return counts.

    If *filenames* is None, every ``*.parquet`` in *container* is inspected.
    """
    if filenames is None:
        filenames = _list_parquet(storage, container)

    per_file: list[FileReport] = []
    program_counts: dict[str, int] = {}
    total_records = 0
    files_read = 0

    for fname in filenames:
        fr = _inspect_file(storage, container, fname)
        per_file.append(fr)
        if fr.error:
            continue
        files_read += 1
        total_records += fr.records
        for name, cnt in fr.programs.items():
            program_counts[name] = program_counts.get(name, 0) + cnt

    return InspectReport(
        container=container,
        files_found=len(filenames),
        files_read=files_read,
        total_records=total_records,
        program_counts=program_counts,
        per_file=per_file,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect raw transcript parquet files.")
    p.add_argument("--container", help="Container to inspect (defaults to the raw container).")
    p.add_argument("--date", help="Single date YYYY-MM-DD.")
    p.add_argument("--start", help="Range start YYYY-MM-DD (inclusive).")
    p.add_argument("--end", help="Range end YYYY-MM-DD (inclusive).")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    storage_cfg = StorageConfig()
    storage = StorageService(storage_cfg)
    container = args.container or storage_cfg.raw_container

    filenames: Optional[list[str]] = None
    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
        filenames = [f"{d}.parquet" for d in _date_range(start, end)]
    elif args.date:
        filenames = [f"{args.date}.parquet"]

    report = inspect_raw(storage, container, filenames)
    report.print_summary()


if __name__ == "__main__":
    main()
