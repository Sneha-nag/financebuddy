"""Fraud / anomaly scoring with Isolation Forest + rule-based reason codes."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import joblib
import numpy as np
import polars as pl
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler


FEATURE_COLS = [
    "amount_abs",
    "log_amount",
    "hour",
    "is_night",
    "is_new_merchant",
    "is_new_city",
    "user_amount_z",
    "merchant_rarity",
    "burst_count_1h",
    "micro_auth",
]


def _build_features(tx: pl.DataFrame) -> pl.DataFrame:
    df = tx.with_columns(
        pl.col("transaction_ts").str.to_datetime().alias("transaction_ts"),
        pl.col("posted_date").str.to_date().alias("posted_date"),
    ).sort(["user_id", "transaction_ts"])

    df = df.with_columns(
        pl.col("transaction_ts").dt.hour().alias("hour"),
        pl.col("amount_abs").log1p().alias("log_amount"),
        (pl.col("amount_abs") <= 2.5).cast(pl.Int8).alias("micro_auth"),
    ).with_columns(
        ((pl.col("hour") < 6) | (pl.col("hour") >= 23)).cast(pl.Int8).alias("is_night"),
        pl.col("merchant_id").cum_count().over(["user_id", "merchant_id"]).alias("merchant_seen"),
        pl.col("city").cum_count().over(["user_id", "city"]).alias("city_seen"),
        pl.col("transaction_id").count().over("merchant_id").alias("merchant_count"),
        pl.col("amount_abs").median().over("user_id").alias("user_median"),
    ).with_columns(
        (pl.col("merchant_seen") == 1).cast(pl.Int8).alias("is_new_merchant"),
        (pl.col("city_seen") == 1).cast(pl.Int8).alias("is_new_city"),
        (1.0 / pl.col("merchant_count")).alias("merchant_rarity"),
        (pl.col("amount_abs") - pl.col("user_median")).abs().median().over("user_id").alias("user_mad"),
    ).with_columns(
        pl.when(pl.col("user_mad") == 0).then(1.0).otherwise(pl.col("user_mad")).alias("user_mad"),
    ).with_columns(
        ((pl.col("amount_abs") - pl.col("user_median")) / pl.col("user_mad")).alias("user_amount_z"),
    )

    # burst count in prior 60 minutes (python loop — fine at this scale)
    from datetime import timedelta

    times_by_user: dict[str, list] = {}
    burst = []
    for row in df.select(["user_id", "transaction_ts"]).iter_rows(named=True):
        uid = row["user_id"]
        t = row["transaction_ts"]
        hist = times_by_user.setdefault(uid, [])
        start = t - timedelta(minutes=60)
        cnt = sum(1 for prev in hist if prev >= start) + 1
        hist.append(t)
        burst.append(cnt)

    df = df.with_columns(pl.Series("burst_count_1h", burst))
    return df


def _reason_codes(row: dict) -> list[str]:
    reasons = []
    if row["micro_auth"] == 1 and row["burst_count_1h"] >= 3:
        reasons.append("card_test_micro_auth_burst")
    if row["is_new_merchant"] == 1 and row["amount_abs"] >= 100:
        reasons.append("large_amount_new_merchant")
    if row["is_new_city"] == 1 and row["amount_abs"] >= 50:
        reasons.append("new_city_spend")
    if row["user_amount_z"] is not None and row["user_amount_z"] >= 6:
        reasons.append("amount_far_above_user_typical")
    if row["is_night"] == 1 and row["amount_abs"] >= 80:
        reasons.append("unusual_night_activity")
    if not reasons and row.get("anomaly_score", 0) > 0.7:
        reasons.append("statistical_outlier")
    return reasons


def train_and_score_fraud(
    duckdb_path: str,
    artifacts_dir: str,
    contamination: float = 0.02,
) -> pl.DataFrame:
    con = duckdb.connect(duckdb_path, read_only=True)
    tx = con.execute(
        """
        select
            transaction_id, user_id, cast(transaction_ts as varchar) as transaction_ts,
            cast(posted_date as varchar) as posted_date,
            merchant_id, merchant_name, category, amount_abs, city, state,
            is_fraud_label
        from marts.fct_transactions
        where cash_direction = 'outflow'
        """
    ).pl()
    con.close()

    feats = _build_features(tx).sort("transaction_ts")
    X = feats.select(FEATURE_COLS).to_numpy().astype(float)
    X = np.nan_to_num(X, nan=0.0)
    y = feats["is_fraud_label"].to_numpy().astype(int)

    split = int(len(feats) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_test = y[split:]

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_all_s = scaler.transform(X)

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train_s)

    raw = -model.decision_function(X_all_s)
    anomaly = (raw - raw.min()) / (raw.max() - raw.min() + 1e-9)
    feats = feats.with_columns(pl.Series("anomaly_score", anomaly))

    rule_boost = (
        ((feats["micro_auth"].to_numpy() == 1) & (feats["burst_count_1h"].to_numpy() >= 4)).astype(float) * 0.35
        + ((feats["is_new_merchant"].to_numpy() == 1) & (feats["amount_abs"].to_numpy() >= 150)).astype(float) * 0.25
        + ((feats["is_new_city"].to_numpy() == 1) & (feats["amount_abs"].to_numpy() >= 100)).astype(float) * 0.15
    )
    fraud_score = np.clip(anomaly + rule_boost, 0, 1)
    # Prefer high-precision personal-finance fraud patterns over broad anomalies
    category = feats["category"].to_numpy()
    merchant = feats["merchant_name"].to_numpy()
    rule_hit = (
        ((feats["micro_auth"].to_numpy() == 1) & (feats["burst_count_1h"].to_numpy() >= 4))
        | (
            (feats["is_new_merchant"].to_numpy() == 1)
            & (feats["amount_abs"].to_numpy() >= 150)
            & ((category == "Other") | (merchant == "Unknown Merchant"))
        )
        | (
            (feats["is_new_city"].to_numpy() == 1)
            & (feats["micro_auth"].to_numpy() == 1)
            & (feats["burst_count_1h"].to_numpy() >= 3)
        )
    )
    predicted = rule_hit.astype(int)
    feats = feats.with_columns(
        pl.Series("fraud_score", fraud_score),
        pl.Series("is_fraud_predicted", predicted),
    )

    reasons = []
    for row in feats.iter_rows(named=True):
        reasons.append(json.dumps(_reason_codes(row)))
    feats = feats.with_columns(pl.Series("reason_codes", reasons))

    metrics = {}
    test_scores = fraud_score[split:]
    if y_test.sum() > 0:
        metrics["roc_auc"] = float(roc_auc_score(y_test, test_scores))
        metrics["average_precision"] = float(average_precision_score(y_test, test_scores))
    metrics["n_flagged"] = int(predicted.sum())
    metrics["n_true_fraud"] = int(y.sum())

    art = Path(artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "scaler": scaler, "features": FEATURE_COLS}, art / "fraud_iforest.joblib")
    (art / "fraud_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return feats.select(
        [
            "transaction_id",
            "user_id",
            "transaction_ts",
            "merchant_name",
            "category",
            "amount_abs",
            "city",
            "state",
            "anomaly_score",
            "fraud_score",
            "is_fraud_predicted",
            "is_fraud_label",
            "reason_codes",
        ]
    )


def write_fraud_scores(duckdb_path: str, gold_dir: str, artifacts_dir: str, contamination: float = 0.02) -> Path:
    scored = train_and_score_fraud(duckdb_path, artifacts_dir, contamination=contamination)
    out = Path(gold_dir) / "mart_fraud_scores.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    scored.write_parquet(out)

    con = duckdb.connect(duckdb_path)
    con.execute("create schema if not exists marts")
    con.register("fraud_df", scored.to_arrow())
    con.execute("create or replace table marts.mart_fraud_scores as select * from fraud_df")
    con.close()
    return out