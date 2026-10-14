"""
Subscription helpers on top of the dbt marts.

dbt finds "looks monthly" merchants. This module adds same-merchant double-charge
detection (two similar amounts within a couple of days) and writes gold Parquet.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


def detect_duplicate_charges(duckdb_path: str) -> pl.DataFrame:
    con = duckdb.connect(duckdb_path, read_only=True)
    tx = con.execute(
        """
        select
            transaction_id, user_id, merchant_id, merchant_name,
            posted_date, cast(transaction_ts as varchar) as transaction_ts,
            amount_abs, is_duplicate_charge_label
        from marts.fct_transactions
        where cash_direction = 'outflow'
        order by user_id, merchant_id, transaction_ts
        """
    ).pl()
    con.close()

    tx = tx.with_columns(pl.col("transaction_ts").str.to_datetime())
    rows = []
    for keys, grp in tx.group_by(["user_id", "merchant_id"], maintain_order=True):
        # Walk each user+merchant timeline; flag near-duplicate pairs.
        grp = grp.sort("transaction_ts")
        prev = None
        for row in grp.iter_rows(named=True):
            if prev is not None:
                hours = (row["transaction_ts"] - prev["transaction_ts"]).total_seconds() / 3600.0
                # Allow a little amount drift (tips / FX noise) without matching everything.
                amt_close = abs(row["amount_abs"] - prev["amount_abs"]) <= max(0.5, 0.02 * prev["amount_abs"])
                if hours <= 72 and amt_close:
                    rows.append(
                        {
                            "user_id": row["user_id"],
                            "merchant_id": row["merchant_id"],
                            "merchant_name": row["merchant_name"],
                            "transaction_id": row["transaction_id"],
                            "prior_transaction_id": prev["transaction_id"],
                            "hours_apart": round(hours, 2),
                            "amount": round(float(row["amount_abs"]), 2),
                            "is_duplicate_charge_predicted": 1,
                            "is_duplicate_charge_label": int(row["is_duplicate_charge_label"]),
                            "reason": f"Possible double charge at {row['merchant_name']} within {hours:.0f}h",
                            "estimated_monthly_savings": round(float(row["amount_abs"]), 2),
                        }
                    )
            prev = row

    if not rows:
        return pl.DataFrame(
            schema={
                "user_id": pl.Utf8,
                "merchant_id": pl.Utf8,
                "merchant_name": pl.Utf8,
                "transaction_id": pl.Utf8,
                "prior_transaction_id": pl.Utf8,
                "hours_apart": pl.Float64,
                "amount": pl.Float64,
                "is_duplicate_charge_predicted": pl.Int64,
                "is_duplicate_charge_label": pl.Int64,
                "reason": pl.Utf8,
                "estimated_monthly_savings": pl.Float64,
            }
        )
    return pl.DataFrame(rows)


def write_subscription_enrichment(duckdb_path: str, gold_dir: str) -> dict[str, Path]:
    dup_charges = detect_duplicate_charges(duckdb_path)
    gold = Path(gold_dir)
    gold.mkdir(parents=True, exist_ok=True)

    dup_path = gold / "mart_duplicate_charges.parquet"
    dup_charges.write_parquet(dup_path)

    con = duckdb.connect(duckdb_path)
    con.execute("create schema if not exists marts")
    con.register("dup_charges", dup_charges.to_arrow())
    con.execute("create or replace table marts.mart_duplicate_charges as select * from dup_charges")

    for table in [
        "mart_subscriptions",
        "mart_duplicate_subscriptions",
        "mart_monthly_cashflow",
        "fct_transactions",
        "dim_user",
    ]:
        df = con.execute(f"select * from marts.{table}").pl()
        df.write_parquet(gold / f"{table}.parquet")

    con.close()
    return {"duplicate_charges": dup_path}