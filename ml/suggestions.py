"""Ranked, explainable improvement suggestions."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


def build_suggestions(duckdb_path: str) -> pl.DataFrame:
    con = duckdb.connect(duckdb_path, read_only=True)

    def safe_df(sql: str) -> pl.DataFrame:
        try:
            return con.execute(sql).pl()
        except Exception:
            return pl.DataFrame()

    dup_subs = safe_df("select * from marts.mart_duplicate_subscriptions")
    price = safe_df(
        """
        select user_id, merchant_id, merchant_name, estimated_monthly_cost,
               min_amount, max_amount, price_increased
        from marts.mart_subscriptions
        where coalesce(price_increased, false) = true
        """
    )
    dup_charges = safe_df("select * from marts.mart_duplicate_charges")
    habits = safe_df("select * from marts.mart_habit_insights")
    fraud = safe_df(
        """
        select user_id, transaction_id, merchant_name, amount_abs, fraud_score, reason_codes
        from marts.mart_fraud_scores
        where is_fraud_predicted = 1
        """
    )
    con.close()

    suggestions: list[dict] = []

    if not dup_subs.is_empty():
        for row in dup_subs.iter_rows(named=True):
            savings = float(row.get("potential_monthly_savings") or 0)
            suggestions.append(
                {
                    "user_id": row["user_id"],
                    "suggestion_type": "cancel_duplicate_subscription",
                    "title": f"Overlapping {str(row['overlap_group']).replace('_', ' ')} services",
                    "reason": row["reason"],
                    "estimated_monthly_savings": savings,
                    "confidence": 0.9,
                    "priority_score": savings * 0.9,
                    "related_ids": str(row.get("merchant_ids")),
                }
            )

    if not price.is_empty():
        for row in price.iter_rows(named=True):
            bump = float(row["max_amount"] - row["min_amount"])
            suggestions.append(
                {
                    "user_id": row["user_id"],
                    "suggestion_type": "review_price_increase",
                    "title": f"Review {row['merchant_name']} price increase",
                    "reason": (
                        f"{row['merchant_name']} rose from ${row['min_amount']:.2f} to "
                        f"${row['max_amount']:.2f} (+${bump:.2f}/mo)."
                    ),
                    "estimated_monthly_savings": round(bump, 2),
                    "confidence": 0.8,
                    "priority_score": bump * 0.8,
                    "related_ids": row["merchant_id"],
                }
            )

    if not dup_charges.is_empty():
        for row in dup_charges.iter_rows(named=True):
            suggestions.append(
                {
                    "user_id": row["user_id"],
                    "suggestion_type": "dispute_duplicate_charge",
                    "title": f"Possible double charge at {row['merchant_name']}",
                    "reason": row["reason"],
                    "estimated_monthly_savings": float(row["estimated_monthly_savings"]),
                    "confidence": 0.85,
                    "priority_score": float(row["estimated_monthly_savings"]) * 0.85,
                    "related_ids": row["transaction_id"],
                }
            )

    if not habits.is_empty():
        for row in habits.iter_rows(named=True):
            impact = float(row.get("dollar_impact") or 0)
            if impact < 20:
                continue
            suggestions.append(
                {
                    "user_id": row["user_id"],
                    "suggestion_type": row["insight_type"],
                    "title": f"Spending pattern: {row['category']}",
                    "reason": row["reason"],
                    "estimated_monthly_savings": max(impact * 0.3, 0),
                    "confidence": float(row.get("confidence") or 0.6),
                    "priority_score": max(impact * 0.3, 0) * float(row.get("confidence") or 0.6),
                    "related_ids": row["category"],
                }
            )

    if not fraud.is_empty():
        for user_id, grp in fraud.group_by("user_id"):
            uid = user_id[0] if isinstance(user_id, tuple) else user_id
            top = grp.sort("fraud_score", descending=True).row(0, named=True)
            suggestions.append(
                {
                    "user_id": uid,
                    "suggestion_type": "fraud_alert",
                    "title": f"Review suspicious charge at {top['merchant_name']}",
                    "reason": (
                        f"${top['amount_abs']:.2f} flagged (score {top['fraud_score']:.2f}). "
                        f"Reasons: {top['reason_codes']}"
                    ),
                    "estimated_monthly_savings": float(top["amount_abs"]),
                    "confidence": float(top["fraud_score"]),
                    "priority_score": float(top["fraud_score"]) * 1000,
                    "related_ids": top["transaction_id"],
                }
            )

    if not suggestions:
        return pl.DataFrame()

    out = pl.DataFrame(suggestions).sort(["user_id", "priority_score"], descending=[False, True])
    out = out.with_columns(
        pl.Series("suggestion_id", [f"s_{i:06d}" for i in range(out.height)])
    )
    cols = [
        "suggestion_id",
        "user_id",
        "suggestion_type",
        "title",
        "reason",
        "estimated_monthly_savings",
        "confidence",
        "priority_score",
        "related_ids",
    ]
    return out.select(cols)


def write_suggestions(duckdb_path: str, gold_dir: str) -> Path:
    df = build_suggestions(duckdb_path)
    out = Path(gold_dir) / "mart_suggestions.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)

    con = duckdb.connect(duckdb_path)
    con.execute("create schema if not exists marts")
    if df.is_empty():
        con.execute(
            """
            create or replace table marts.mart_suggestions as
            select * from (
                select
                    cast(null as varchar) as suggestion_id,
                    cast(null as varchar) as user_id,
                    cast(null as varchar) as suggestion_type,
                    cast(null as varchar) as title,
                    cast(null as varchar) as reason,
                    cast(null as double) as estimated_monthly_savings,
                    cast(null as double) as confidence,
                    cast(null as double) as priority_score,
                    cast(null as varchar) as related_ids
                where false
            )
            """
        )
    else:
        con.register("sug_df", df.to_arrow())
        con.execute("create or replace table marts.mart_suggestions as select * from sug_df")
    con.close()
    return out