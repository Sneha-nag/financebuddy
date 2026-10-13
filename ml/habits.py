"""Habit and spending-pattern insights vs each user's own history."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


def _q(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    return con.execute(sql).pl()


def compute_habit_insights(duckdb_path: str, lookback_days: int = 90) -> pl.DataFrame:
    con = duckdb.connect(duckdb_path, read_only=True)
    cashflow = _q(con, "select * from marts.mart_monthly_cashflow")
    users = _q(con, "select user_id, persona, monthly_income from marts.dim_user")
    tx = _q(
        con,
        """
        select user_id, posted_date, category, amount_abs, cash_direction
        from marts.fct_transactions
        where cash_direction = 'outflow'
        """,
    )
    con.close()

    if cashflow.is_empty():
        return pl.DataFrame()

    cashflow = cashflow.with_columns(pl.col("month_start").cast(pl.Date))
    latest_month = cashflow["month_start"].max()
    recent = cashflow.filter(pl.col("month_start") == latest_month)
    history = cashflow.filter(pl.col("month_start") < latest_month)

    hist_avg = (
        history.group_by(["user_id", "category"])
        .agg(pl.col("spend").mean().alias("hist_avg_spend"))
    )
    merged = recent.join(hist_avg, on=["user_id", "category"], how="left").with_columns(
        pl.col("hist_avg_spend").fill_null(pl.col("spend"))
    ).with_columns(
        (pl.col("spend") / pl.col("hist_avg_spend")).alias("spend_ratio"),
        (pl.col("spend") - pl.col("hist_avg_spend")).alias("delta_spend"),
    )

    insights: list[dict] = []
    for row in merged.iter_rows(named=True):
        if row["category"] == "Income":
            continue
        ratio = row["spend_ratio"]
        delta = row["delta_spend"]
        if ratio is None or delta is None:
            continue
        if ratio >= 1.35 and delta >= 25:
            insights.append(
                {
                    "user_id": row["user_id"],
                    "insight_type": "category_spike",
                    "category": row["category"],
                    "metric_value": round(float(ratio), 2),
                    "dollar_impact": round(float(delta), 2),
                    "reason": (
                        f"{row['category']} spend is {ratio:.1f}x your prior monthly average "
                        f"(+${delta:.0f} this month)."
                    ),
                    "confidence": min(0.95, 0.55 + float(ratio - 1) * 0.2),
                }
            )

    from datetime import timedelta

    tx = tx.with_columns(pl.col("posted_date").cast(pl.Date))
    max_date = tx["posted_date"].max()
    if max_date is not None:
        cutoff_date = max_date - timedelta(days=lookback_days)
        recent_tx = tx.filter(pl.col("posted_date") >= cutoff_date)
        dining = recent_tx.filter(pl.col("category") == "Dining").with_columns(
            pl.col("posted_date").dt.weekday().alias("weekday")  # Mon=1 .. Sun=7 in polars
        )
        if not dining.is_empty():
            dining = dining.with_columns((pl.col("weekday") >= 6).alias("is_weekend"))
            weekend = (
                dining.filter(pl.col("is_weekend"))
                .group_by("user_id")
                .agg(pl.col("amount_abs").mean().alias("weekend_dining_avg"))
            )
            weekday = (
                dining.filter(~pl.col("is_weekend"))
                .group_by("user_id")
                .agg(pl.col("amount_abs").mean().alias("weekday_dining_avg"))
            )
            dining_cmp = weekend.join(weekday, on="user_id", how="full", coalesce=True).with_columns(
                pl.col("weekend_dining_avg").fill_null(0.0),
                pl.col("weekday_dining_avg").fill_null(0.0),
            )
            for row in dining_cmp.iter_rows(named=True):
                wkd = row["weekday_dining_avg"]
                we = row["weekend_dining_avg"]
                if wkd > 0:
                    ratio = we / wkd
                    if ratio >= 1.6 and we >= 15:
                        insights.append(
                            {
                                "user_id": row["user_id"],
                                "insight_type": "weekend_dining",
                                "category": "Dining",
                                "metric_value": round(float(ratio), 2),
                                "dollar_impact": round(float(we - wkd) * 4, 2),
                                "reason": (
                                    f"Weekend dining averages ${we:.0f} vs "
                                    f"${wkd:.0f} on weekdays ({ratio:.1f}x)."
                                ),
                                "confidence": 0.7,
                            }
                        )

    needs = recent.filter(pl.col("category").is_in(["Housing", "Utilities"]))
    needs_tot = needs.group_by("user_id").agg(pl.col("spend").sum().alias("needs_spend")).join(
        users, on="user_id", how="left"
    ).with_columns((pl.col("needs_spend") / pl.col("monthly_income")).alias("needs_ratio"))
    for row in needs_tot.iter_rows(named=True):
        if row["needs_ratio"] is not None and row["needs_ratio"] > 0.45:
            insights.append(
                {
                    "user_id": row["user_id"],
                    "insight_type": "high_fixed_costs",
                    "category": "Housing",
                    "metric_value": round(float(row["needs_ratio"]), 2),
                    "dollar_impact": round(float(row["needs_spend"] - 0.4 * row["monthly_income"]), 2),
                    "reason": (
                        f"Housing + utilities are {row['needs_ratio']:.0%} of income "
                        f"(above a 40% comfort band)."
                    ),
                    "confidence": 0.75,
                }
            )

    if not insights:
        return pl.DataFrame()
    return pl.DataFrame(insights).sort(["user_id", "dollar_impact"], descending=[False, True])


def write_habit_insights(duckdb_path: str, gold_dir: str, lookback_days: int = 90) -> Path:
    df = compute_habit_insights(duckdb_path, lookback_days=lookback_days)
    out = Path(gold_dir) / "mart_habit_insights.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)

    con = duckdb.connect(duckdb_path)
    con.execute("create schema if not exists marts")
    if df.is_empty():
        con.execute(
            """
            create or replace table marts.mart_habit_insights as
            select * from (
                select
                    cast(null as varchar) as user_id,
                    cast(null as varchar) as insight_type,
                    cast(null as varchar) as category,
                    cast(null as double) as metric_value,
                    cast(null as double) as dollar_impact,
                    cast(null as varchar) as reason,
                    cast(null as double) as confidence
                where false
            )
            """
        )
    else:
        con.register("habit_df", df.to_arrow())
        con.execute("create or replace table marts.mart_habit_insights as select * from habit_df")
    con.close()
    return out