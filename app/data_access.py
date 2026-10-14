"""
How Streamlit gets its data.

Three backends, same methods. Local work uses DuckDB. Portfolio / live demo
usually uses S3 gold Parquet (cheap, cached). Athena is there when you want
the "query Glue" story — fraud/suggestions still prefer S3 because those
tables are fatter and easy to over-scan.
"""

from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import polars as pl

# Generator IDs look like u_0007 — keep this tight so we never interpolate junk into SQL.
USER_ID_RE = re.compile(r"^u_\d{4}$")

# Filename map for the S3 gold layout (upload_to_s3 / pipeline exports).
GOLD_TABLES = {
    "dim_user": "dim_user.parquet",
    "mart_suggestions": "mart_suggestions.parquet",
    "mart_monthly_cashflow": "mart_monthly_cashflow.parquet",
    "mart_subscriptions": "mart_subscriptions.parquet",
    "mart_duplicate_subscriptions": "mart_duplicate_subscriptions.parquet",
    "mart_habit_insights": "mart_habit_insights.parquet",
    "mart_fraud_scores": "mart_fraud_scores.parquet",
}


def validate_user_id(user_id: str) -> str:
    if not USER_ID_RE.match(user_id):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    return user_id


def resolve_backend_name(settings: dict) -> str:
    env = (os.getenv("FINANCEBUDDY_BACKEND") or "").strip().lower()
    if env in {"duckdb", "s3", "athena"}:
        return env
    return str(settings.get("app", {}).get("backend", "duckdb")).lower()


class DataBackend(ABC):
    name: str

    @abstractmethod
    def list_users(self) -> list[dict]:
        ...

    @abstractmethod
    def suggestion_counts(self) -> list[dict]:
        ...

    @abstractmethod
    def suggestions(self, user_id: str) -> list[dict]:
        ...

    @abstractmethod
    def cashflow(self, user_id: str) -> list[dict]:
        ...

    @abstractmethod
    def subscriptions(self, user_id: str) -> list[dict]:
        ...

    @abstractmethod
    def duplicate_subscriptions(self, user_id: str) -> list[dict]:
        ...

    @abstractmethod
    def habits(self, user_id: str) -> list[dict]:
        ...

    @abstractmethod
    def fraud_alerts(self, user_id: str) -> list[dict]:
        ...


def _records(df: pl.DataFrame) -> list[dict]:
    if df.is_empty():
        return []
    return df.to_dicts()


class DuckDBBackend(DataBackend):
    name = "duckdb"

    def __init__(self, db_path: str):
        import duckdb

        self._con = duckdb.connect(db_path, read_only=True)

    def _query(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        try:
            df = self._con.execute(sql, params or []).pl()
            return _records(df)
        except Exception:
            return []

    def list_users(self) -> list[dict]:
        return self._query(
            "select user_id, display_name, city, state, persona, monthly_income "
            "from marts.dim_user order by user_id"
        )

    def suggestion_counts(self) -> list[dict]:
        return self._query("select user_id, count(*) as n from marts.mart_suggestions group by 1")

    def suggestions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        return self._query(
            """
            select suggestion_type, title, reason, estimated_monthly_savings, confidence, priority_score
            from marts.mart_suggestions
            where user_id = ?
            order by priority_score desc
            """,
            [uid],
        )

    def cashflow(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        return self._query(
            """
            select cast(month_start as varchar) as month_start, category, spend, income
            from marts.mart_monthly_cashflow
            where user_id = ?
            """,
            [uid],
        )

    def subscriptions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        return self._query(
            """
            select merchant_name, category, charge_count, avg_amount, avg_gap_days,
                   estimated_monthly_cost, is_subscription, price_increased
            from marts.mart_subscriptions
            where user_id = ?
            order by estimated_monthly_cost desc
            """,
            [uid],
        )

    def duplicate_subscriptions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        return self._query(
            "select * from marts.mart_duplicate_subscriptions where user_id = ?",
            [uid],
        )

    def habits(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        return self._query(
            "select * from marts.mart_habit_insights where user_id = ? order by dollar_impact desc",
            [uid],
        )

    def fraud_alerts(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        return self._query(
            """
            select cast(transaction_ts as varchar) as transaction_ts, merchant_name, amount_abs,
                   fraud_score, reason_codes, is_fraud_predicted
            from marts.mart_fraud_scores
            where user_id = ? and is_fraud_predicted = 1
            order by fraud_score desc
            """,
            [uid],
        )


class S3GoldBackend(DataBackend):
    """Pull gold Parquet from S3 once per process, filter in memory per user."""

    name = "s3"

    def __init__(self, bucket: str, prefix: str, region: str, profile: str | None):
        import boto3
        import pyarrow.fs as pafs
        import pyarrow.parquet as pq

        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        self._pq = pq
        # PyArrow's S3FileSystem wants explicit keys; pull them from the boto3 chain
        # so AWS_PROFILE / env / instance roles all still work.
        session = (
            boto3.Session(profile_name=profile, region_name=region)
            if profile
            else boto3.Session(region_name=region)
        )
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials found for S3 backend")
        frozen = creds.get_frozen_credentials()
        self._fs = pafs.S3FileSystem(
            access_key=frozen.access_key,
            secret_key=frozen.secret_key,
            session_token=frozen.token,
            region=region,
        )
        self._cache: dict[str, pl.DataFrame] = {}

    def _load_table(self, logical: str) -> pl.DataFrame:
        # Cache whole tables — switching users shouldn't re-download gold files.
        if logical in self._cache:
            return self._cache[logical]
        filename = GOLD_TABLES[logical]
        key = f"{self._prefix}/gold/{filename}"
        path = f"{self._bucket}/{key}"
        table = self._pq.read_table(path, filesystem=self._fs)
        df = pl.from_arrow(table)
        self._cache[logical] = df
        return df

    def list_users(self) -> list[dict]:
        df = self._load_table("dim_user").select(
            "user_id", "display_name", "city", "state", "persona", "monthly_income"
        ).sort("user_id")
        return _records(df)

    def suggestion_counts(self) -> list[dict]:
        df = (
            self._load_table("mart_suggestions")
            .group_by("user_id")
            .agg(pl.len().alias("n"))
        )
        return _records(df)

    def suggestions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        df = (
            self._load_table("mart_suggestions")
            .filter(pl.col("user_id") == uid)
            .select(
                "suggestion_type",
                "title",
                "reason",
                "estimated_monthly_savings",
                "confidence",
                "priority_score",
            )
            .sort("priority_score", descending=True)
        )
        return _records(df)

    def cashflow(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        df = (
            self._load_table("mart_monthly_cashflow")
            .filter(pl.col("user_id") == uid)
            .with_columns(pl.col("month_start").cast(pl.Utf8))
            .select("month_start", "category", "spend", "income")
        )
        return _records(df)

    def subscriptions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        df = (
            self._load_table("mart_subscriptions")
            .filter(pl.col("user_id") == uid)
            .select(
                "merchant_name",
                "category",
                "charge_count",
                "avg_amount",
                "avg_gap_days",
                "estimated_monthly_cost",
                "is_subscription",
                "price_increased",
            )
            .sort("estimated_monthly_cost", descending=True)
        )
        return _records(df)

    def duplicate_subscriptions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        df = self._load_table("mart_duplicate_subscriptions").filter(pl.col("user_id") == uid)
        return _records(df)

    def habits(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        df = (
            self._load_table("mart_habit_insights")
            .filter(pl.col("user_id") == uid)
            .sort("dollar_impact", descending=True)
        )
        return _records(df)

    def fraud_alerts(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        df = (
            self._load_table("mart_fraud_scores")
            .filter((pl.col("user_id") == uid) & (pl.col("is_fraud_predicted") == 1))
            .with_columns(pl.col("transaction_ts").cast(pl.Utf8))
            .select(
                "transaction_ts",
                "merchant_name",
                "amount_abs",
                "fraud_score",
                "reason_codes",
                "is_fraud_predicted",
            )
            .sort("fraud_score", descending=True)
        )
        return _records(df)


class AthenaBackend(DataBackend):
    """
    Talk to Glue via Athena for dbt marts.

    Still falls back to S3 for ML-only gold (suggestions / habits / fraud) —
    those aren't always registered in Glue after a plain dbt run, and fraud
    especially is a bad table to full-scan on every click.
    """

    name = "athena"

    def __init__(
        self,
        region: str,
        database: str,
        output_location: str,
        workgroup: str,
        profile: str | None,
        s3_fallback: S3GoldBackend | None,
        max_wait_s: int = 60,
    ):
        import boto3

        session = (
            boto3.Session(profile_name=profile, region_name=region)
            if profile
            else boto3.Session(region_name=region)
        )
        self._client = session.client("athena")
        self._database = database
        self._output = output_location
        self._workgroup = workgroup
        self._s3 = s3_fallback
        self._max_wait_s = max_wait_s
        self._cache: dict[str, list[dict]] = {}

    def _run_sql(self, sql: str, cache_key: str) -> list[dict]:
        if cache_key in self._cache:
            return self._cache[cache_key]

        resp = self._client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": self._database},
            ResultConfiguration={"OutputLocation": self._output},
            WorkGroup=self._workgroup,
        )
        qid = resp["QueryExecutionId"]
        deadline = time.time() + self._max_wait_s
        while time.time() < deadline:
            meta = self._client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = meta["Status"]["State"]
            if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.4)
        else:
            raise TimeoutError(f"Athena query timed out: {qid}")

        if state != "SUCCEEDED":
            reason = meta["Status"].get("StateChangeReason", state)
            raise RuntimeError(f"Athena query failed: {reason}")

        rows: list[dict] = []
        paginator = self._client.get_paginator("get_query_results")
        headers: list[str] | None = None
        for page in paginator.paginate(QueryExecutionId=qid):
            result_rows = page["ResultSet"]["Rows"]
            if headers is None:
                headers = [c.get("VarCharValue", "") for c in result_rows[0]["Data"]]
                result_rows = result_rows[1:]
            for row in result_rows:
                values = [c.get("VarCharValue") for c in row["Data"]]
                rows.append(dict(zip(headers, values, strict=False)))
        self._cache[cache_key] = rows
        return rows

    def _athena_or_s3(self, sql: str, cache_key: str, s3_fn):
        try:
            return self._run_sql(sql, cache_key)
        except Exception:
            if self._s3 is None:
                raise
            return s3_fn()

    def list_users(self) -> list[dict]:
        sql = (
            "select user_id, display_name, city, state, persona, monthly_income "
            "from dim_user order by user_id"
        )
        return self._athena_or_s3(sql, "users", lambda: self._s3.list_users())

    def suggestion_counts(self) -> list[dict]:
        # Usually ML gold only — prefer S3 to avoid missing-table Athena errors
        if self._s3 is not None:
            return self._s3.suggestion_counts()
        sql = "select user_id, count(*) as n from mart_suggestions group by 1"
        return self._run_sql(sql, "sug_counts")

    def suggestions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        if self._s3 is not None:
            return self._s3.suggestions(uid)
        sql = f"""
            select suggestion_type, title, reason, estimated_monthly_savings, confidence, priority_score
            from mart_suggestions
            where user_id = '{uid}'
            order by priority_score desc
            limit 200
        """
        return self._run_sql(sql, f"sug:{uid}")

    def cashflow(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        sql = f"""
            select cast(month_start as varchar) as month_start, category, spend, income
            from mart_monthly_cashflow
            where user_id = '{uid}'
            limit 5000
        """
        return self._athena_or_s3(sql, f"cf:{uid}", lambda: self._s3.cashflow(uid))

    def subscriptions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        sql = f"""
            select merchant_name, category, charge_count, avg_amount, avg_gap_days,
                   estimated_monthly_cost, is_subscription, price_increased
            from mart_subscriptions
            where user_id = '{uid}'
            order by estimated_monthly_cost desc
            limit 500
        """
        return self._athena_or_s3(sql, f"subs:{uid}", lambda: self._s3.subscriptions(uid))

    def duplicate_subscriptions(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        sql = f"""
            select * from mart_duplicate_subscriptions
            where user_id = '{uid}'
            limit 100
        """
        return self._athena_or_s3(sql, f"dup:{uid}", lambda: self._s3.duplicate_subscriptions(uid))

    def habits(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        if self._s3 is not None:
            return self._s3.habits(uid)
        sql = f"""
            select * from mart_habit_insights
            where user_id = '{uid}'
            order by dollar_impact desc
            limit 200
        """
        return self._run_sql(sql, f"hab:{uid}")

    def fraud_alerts(self, user_id: str) -> list[dict]:
        uid = validate_user_id(user_id)
        # ~one row per transaction. Prefer the gold Parquet filter over Athena $.
        if self._s3 is not None:
            return self._s3.fraud_alerts(uid)
        sql = f"""
            select cast(transaction_ts as varchar) as transaction_ts, merchant_name, amount_abs,
                   fraud_score, reason_codes, is_fraud_predicted
            from mart_fraud_scores
            where user_id = '{uid}' and is_fraud_predicted = 1
            order by fraud_score desc
            limit 200
        """
        return self._run_sql(sql, f"fraud:{uid}")


def get_backend(settings: dict) -> DataBackend:
    name = resolve_backend_name(settings)
    aws = settings.get("aws", {})
    region = os.getenv("AWS_REGION", aws.get("region", "us-east-1"))
    profile = os.getenv("AWS_PROFILE") or None
    bucket = os.getenv("FINANCEBUDDY_S3_BUCKET") or aws.get("bucket")
    prefix = aws.get("s3_prefix", "financebuddy")
    app_cfg = settings.get("app", {})

    if name == "duckdb":
        return DuckDBBackend(settings["paths"]["duckdb"])

    if not bucket:
        raise RuntimeError(
            "FINANCEBUDDY_S3_BUCKET (or aws.bucket in settings) is required for s3/athena backends"
        )

    s3 = S3GoldBackend(bucket=bucket, prefix=prefix, region=region, profile=profile)

    if name == "s3":
        return s3

    if name == "athena":
        output = (
            os.getenv("FINANCEBUDDY_ATHENA_S3_STAGING")
            or aws.get("athena_s3_staging")
            or f"s3://{bucket}/athena-results/"
        )
        database = (
            os.getenv("FINANCEBUDDY_ATHENA_MARTS_SCHEMA")
            or app_cfg.get("athena_marts_database")
            or "marts"
        )
        workgroup = (
            os.getenv("FINANCEBUDDY_ATHENA_WORKGROUP")
            or aws.get("athena_workgroup")
            or "primary"
        )
        return AthenaBackend(
            region=region,
            database=database,
            output_location=output,
            workgroup=workgroup,
            profile=profile,
            s3_fallback=s3,
        )

    raise RuntimeError(f"Unknown backend: {name}")
