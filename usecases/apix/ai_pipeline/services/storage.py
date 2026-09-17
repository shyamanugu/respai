"""Azure Blob / ADLS storage helpers."""

from __future__ import annotations

import os
from typing import Optional

import duckdb
import fsspec
import polars as pl

from ai_pipeline.programs_config.base import StorageConfig
from ai_pipeline.logging_config import get_logger

logger = get_logger("services.storage")


class StorageService:
    """Thin wrapper around fsspec + duckdb for Azure Blob access."""

    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        self.fs = fsspec.filesystem(
            "abfs",
            account_name=config.account_name,
            account_key=config.account_key,
        )
        try:
            duckdb.register_filesystem(self.fs)
        except duckdb.InvalidInputException:
            pass  # already registered in this session
        logger.info("StorageService initialised | account=%s", config.account_name)

    # ── reads ────────────────────────────────────────────────────────────

    def read_parquet_sql(self, container: str, filename: str, where: Optional[str] = None) -> pl.DataFrame:
        path = f"abfs://{container}/{filename}"
        q = f"SELECT * FROM read_parquet('{path}')"
        if where:
            q += f" WHERE {where}"
        logger.info("read_parquet_sql | %s", q)
        try:
            return duckdb.query(q).pl()
        except duckdb.IOException as exc:
            if "No files found" in str(exc):
                raise FileNotFoundError(f"No source file at {path}") from exc
            raise

    def read_parquet_sql_multi(self, container: str, filenames: list[str], where: Optional[str] = None, columns: Optional[list[str]] = None) -> pl.DataFrame:
        paths = [f"abfs://{container}/{f}" for f in filenames]
        cols = ", ".join(columns) if columns else "*"
        q = f"SELECT {cols} FROM read_parquet({paths}, filename=true)"
        if where:
            q += f" WHERE {where}"
        logger.info("read_parquet_sql_multi | files=%d", len(filenames))
        return duckdb.query(q).pl()

    def read_parquet(self, container: str, filename: str) -> pl.DataFrame:
        path = f"abfs://{container}/{filename}"
        with self.fs.open(path, "rb") as f:
            return pl.read_parquet(f)

    def read_json(self, container: str, filename: str) -> dict:
        import json
        path = f"abfs://{container}/{filename}"
        with self.fs.open(path, "r") as f:
            return json.loads(f.read())

    # ── writes ───────────────────────────────────────────────────────────

    def _ensure_container(self, container: str) -> None:
        """Create the top-level blob container if it does not already exist.

        ``container`` may include a virtual sub-path (e.g. ``temp/weekly-summary``);
        only the first segment is a real Azure Blob container.
        """
        root = str(container).split("/")[0]
        if not root:
            return
        try:
            if not self.fs.exists(root):
                self.fs.mkdir(root)
                logger.info("Created missing container | %s", root)
        except Exception as exc:  # noqa: BLE001 - best-effort; write will surface real errors
            logger.debug("ensure_container(%s) skipped: %s", root, exc)

    def write_parquet(self, df: pl.DataFrame, container: str, filename: str) -> None:
        self._ensure_container(container)
        path = f"abfs://{container}/{filename}"
        logger.info("write_parquet | %s | rows=%d", path, len(df))
        with self.fs.open(path, "wb") as f:
            df.write_parquet(f)

    def write_json(self, data: dict, container: str, filename: str) -> None:
        import json
        self._ensure_container(container)
        path = f"abfs://{container}/{filename}"
        logger.info("write_json | %s", path)
        with self.fs.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=4, ensure_ascii=True))

    def exists(self, container: str, filename: str) -> bool:
        return self.fs.exists(f"abfs://{container}/{filename}")

    def mkdir(self, container: str, dirname: str) -> None:
        self._ensure_container(container)
        self.fs.mkdir(f"{container}/{dirname}")

    def list_files(self, container: str, prefix: str = "", suffix: str | None = None) -> list[str]:
        """List blob names directly under ``container/prefix``.

        Returns bare names (not full paths). ``prefix`` scopes the listing to a
        virtual sub-directory (e.g. a date folder); ``suffix`` optionally filters
        by extension (e.g. ``.json``).
        """
        base = f"{container}/{prefix}".rstrip("/")
        try:
            entries = self.fs.ls(base, detail=False)
        except FileNotFoundError:
            return []
        names = [str(e).rstrip("/").split("/")[-1] for e in entries]
        if suffix:
            names = [n for n in names if n.endswith(suffix)]
        return names

