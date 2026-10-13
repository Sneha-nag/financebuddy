"""Compare planted ground truth with pipeline detections (portfolio quality check)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl


def evaluate(duckdb_path: str, ground_truth_dir: str, artifacts_dir: str) -> dict:
    gt_path = Path(ground_truth_dir) / "planted_events.parquet"
    planted = pl.read_parquet(gt_path)

    con = duckdb.connect(duckdb_path, read_only=True)
    dup_subs = con.execute("select user_id, overlap_group from marts.mart_duplicate_subscriptions").pl()
    fraud = con.execute(
        """
        select user_id, count(*) as n
        from marts.mart_fraud_scores
        where is_fraud_predicted = 1
        group by 1
        """
    ).pl()
    price = con.execute(
        """
        select user_id
        from marts.mart_subscriptions
        where coalesce(price_increased, false) = true
        """
    ).pl()
    con.close()

    planted_dup_users = set(
        planted.filter(pl.col("event_type") == "duplicate_subscription")["user_id"].to_list()
    )
    found_dup_users = set(dup_subs["user_id"].to_list()) if not dup_subs.is_empty() else set()

    planted_fraud_users = set(planted.filter(pl.col("event_type") == "fraud_burst")["user_id"].to_list())
    found_fraud_users = set(fraud["user_id"].to_list()) if not fraud.is_empty() else set()

    planted_price_users = set(planted.filter(pl.col("event_type") == "price_creep")["user_id"].to_list())
    found_price_users = set(price["user_id"].to_list()) if not price.is_empty() else set()

    def recall(planted_set: set, found_set: set) -> float:
        if not planted_set:
            return 1.0
        return len(planted_set & found_set) / len(planted_set)

    report = {
        "duplicate_subscription_recall": round(recall(planted_dup_users, found_dup_users), 3),
        "fraud_burst_user_recall": round(recall(planted_fraud_users, found_fraud_users), 3),
        "price_creep_recall": round(recall(planted_price_users, found_price_users), 3),
        "n_planted_duplicate_subscription_users": len(planted_dup_users),
        "n_found_duplicate_subscription_users": len(found_dup_users),
        "n_planted_fraud_users": len(planted_fraud_users),
        "n_found_fraud_users": len(found_fraud_users),
        "n_planted_price_creep_users": len(planted_price_users),
        "n_found_price_creep_users": len(found_price_users),
    }

    out = Path(artifacts_dir) / "ground_truth_recovery.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report