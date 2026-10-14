"""Shared helpers for Kinesis → Firehose → S3 demo scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_settings


def aws_session():
    import boto3

    settings = load_settings()
    region = os.getenv("AWS_REGION", settings["aws"].get("region", "us-east-1"))
    profile = os.getenv("AWS_PROFILE") or None
    if profile:
        return boto3.Session(profile_name=profile, region_name=region), region
    return boto3.Session(region_name=region), region


def stream_names(settings: dict | None = None) -> dict[str, str]:
    settings = settings or load_settings()
    streaming = settings.get("aws", {}).get("streaming", {})
    return {
        "stream": os.getenv("FINANCEBUDDY_KINESIS_STREAM")
        or streaming.get("stream_name")
        or "financebuddy-txns",
        "firehose": os.getenv("FINANCEBUDDY_FIREHOSE_STREAM")
        or streaming.get("firehose_name")
        or "financebuddy-txns-to-s3",
        "role": os.getenv("FINANCEBUDDY_FIREHOSE_ROLE")
        or streaming.get("role_name")
        or "financebuddy-firehose-role",
        "s3_prefix": os.getenv("FINANCEBUDDY_STREAM_S3_PREFIX")
        or streaming.get("s3_prefix")
        or "financebuddy/raw/stream/",
    }


def require_bucket(settings: dict | None = None) -> str:
    settings = settings or load_settings()
    bucket = os.getenv("FINANCEBUDDY_S3_BUCKET") or settings["aws"].get("bucket")
    if not bucket:
        raise SystemExit("Set FINANCEBUDDY_S3_BUCKET (or aws.bucket in settings.yaml)")
    return bucket


def account_id(session) -> str:
    return session.client("sts").get_caller_identity()["Account"]
