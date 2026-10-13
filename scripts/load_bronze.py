"""Load bronze Parquet into DuckDB schemas for dbt sources."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_settings


def load_bronze(settings: dict | None = None) -> str:
    settings = settings or load_settings()
    paths = settings["paths"]
    db_path = paths["duckdb"]
    bronze = Path(paths["bronze"])

    con = duckdb.connect(db_path)
    con.execute("create schema if not exists bronze")
    con.execute("create schema if not exists staging")
    con.execute("create schema if not exists intermediate")
    con.execute("create schema if not exists marts")

    for table in ("users", "transactions", "merchants"):
        parquet = bronze / f"{table}.parquet"
        if not parquet.exists():
            raise FileNotFoundError(f"Missing bronze table: {parquet}")
        con.execute(
            f"""
            create or replace table bronze.{table} as
            select * from read_parquet('{parquet.as_posix()}')
            """
        )
        n = con.execute(f"select count(*) from bronze.{table}").fetchone()[0]
        print(f"Loaded bronze.{table}: {n:,} rows")

    con.close()
    return db_path


if __name__ == "__main__":
    print(load_bronze())