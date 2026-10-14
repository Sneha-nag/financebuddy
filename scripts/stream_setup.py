"""
Spin up the streaming demo: Kinesis (inbox) + Firehose (delivery truck to S3).

Creates a small IAM role Firehose can assume. Pair with stream_teardown.py —
leaving a shard up overnight is how these demos get expensive.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings
from streaming_common import account_id, aws_session, require_bucket, stream_names


def wait_stream_active(kinesis, name: str, timeout_s: int = 180) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        desc = kinesis.describe_stream_summary(StreamName=name)["StreamDescriptionSummary"]
        status = desc["StreamStatus"]
        arn = desc["StreamARN"]
        if status == "ACTIVE":
            return arn
        time.sleep(2)
    raise TimeoutError(f"Stream {name} not ACTIVE in time")


def ensure_role(iam, role_name: str, stream_arn: str, bucket: str, acct: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "firehose.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "KinesisRead",
                "Effect": "Allow",
                "Action": [
                    "kinesis:DescribeStream",
                    "kinesis:DescribeStreamSummary",
                    "kinesis:GetShardIterator",
                    "kinesis:GetRecords",
                    "kinesis:ListShards",
                    "kinesis:SubscribeToShard",
                ],
                "Resource": stream_arn,
            },
            {
                "Sid": "S3Write",
                "Effect": "Allow",
                "Action": [
                    "s3:AbortMultipartUpload",
                    "s3:GetBucketLocation",
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:ListBucketMultipartUploads",
                    "s3:PutObject",
                ],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            },
        ],
    }

    try:
        iam.get_role(RoleName=role_name)
        print(f"IAM role exists: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="FinanceBuddy Firehose to S3 (demo; delete with stream_teardown.py)",
            Tags=[{"Key": "project", "Value": "financebuddy"}],
        )
        print(f"Created IAM role: {role_name}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="financebuddy-firehose-inline",
        PolicyDocument=json.dumps(policy),
    )
    # IAM is eventually consistent; Firehose create fails if we race it.
    time.sleep(10)
    return f"arn:aws:iam::{acct}:role/{role_name}"


def main() -> None:
    settings = load_settings()
    session, _region = aws_session()
    names = stream_names(settings)
    bucket = require_bucket(settings)
    acct = account_id(session)

    kinesis = session.client("kinesis")
    firehose = session.client("firehose")
    iam = session.client("iam")

    stream = names["stream"]
    delivery = names["firehose"]
    role_name = names["role"]
    s3_prefix = names["s3_prefix"].rstrip("/") + "/"

    try:
        kinesis.describe_stream_summary(StreamName=stream)
        print(f"Kinesis stream exists: {stream}")
    except kinesis.exceptions.ResourceNotFoundException:
        kinesis.create_stream(StreamName=stream, ShardCount=1)
        print(f"Creating Kinesis stream: {stream} (1 shard)")

    stream_arn = wait_stream_active(kinesis, stream)
    print(f"Stream ACTIVE: {stream_arn}")

    role_arn = ensure_role(iam, role_name, stream_arn, bucket, acct)

    try:
        firehose.describe_delivery_stream(DeliveryStreamName=delivery)
        print(f"Firehose delivery stream exists: {delivery}")
    except firehose.exceptions.ResourceNotFoundException:
        error_prefix = (
            s3_prefix.replace("/stream/", "/stream-errors/", 1)
            if "/stream/" in s3_prefix
            else f"{s3_prefix.rstrip('/')}-errors/"
        )
        firehose.create_delivery_stream(
            DeliveryStreamName=delivery,
            DeliveryStreamType="KinesisStreamAsSource",
            KinesisStreamSourceConfiguration={
                "KinesisStreamARN": stream_arn,
                "RoleARN": role_arn,
            },
            ExtendedS3DestinationConfiguration={
                "RoleARN": role_arn,
                "BucketARN": f"arn:aws:s3:::{bucket}",
                "Prefix": s3_prefix,
                "ErrorOutputPrefix": error_prefix,
                # 60s / 1MB so a short demo actually lands files without waiting forever.
                "BufferingHints": {"SizeInMBs": 1, "IntervalInSeconds": 60},
                "CompressionFormat": "GZIP",
                "CloudWatchLoggingOptions": {"Enabled": False},
            },
            Tags=[{"Key": "project", "Value": "financebuddy"}],
        )
        print(f"Creating Firehose: {delivery} -> s3://{bucket}/{s3_prefix}")
        for _ in range(60):
            status = firehose.describe_delivery_stream(DeliveryStreamName=delivery)[
                "DeliveryStreamDescription"
            ]["DeliveryStreamStatus"]
            if status == "ACTIVE":
                break
            time.sleep(3)
        else:
            raise TimeoutError(f"Firehose {delivery} not ACTIVE in time")

    print(
        "\nSetup complete.\n"
        f"  Stream:   {stream}\n"
        f"  Firehose: {delivery}\n"
        f"  S3 dest:  s3://{bucket}/{s3_prefix}\n"
        "Next:\n"
        "  python scripts/stream_produce.py --count 200\n"
        "Wait ~60s for Firehose buffer, then:\n"
        f"  aws s3 ls s3://{bucket}/{s3_prefix} --recursive\n"
        "When done learning, tear down to stop charges:\n"
        "  python scripts/stream_teardown.py\n"
    )


if __name__ == "__main__":
    main()
