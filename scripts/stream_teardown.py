"""
Tear down streaming demo resources.

Order: Firehose first (it depends on the stream + role), then Kinesis, then the
IAM role. Pass --delete-s3-data if you also want the landed gzip objects gone.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings
from streaming_common import aws_session, require_bucket, stream_names


def delete_firehose(firehose, name: str) -> None:
    try:
        firehose.describe_delivery_stream(DeliveryStreamName=name)
    except firehose.exceptions.ResourceNotFoundException:
        print(f"Firehose not found (ok): {name}")
        return
    firehose.delete_delivery_stream(DeliveryStreamName=name, AllowForceDelete=True)
    print(f"Deleting Firehose: {name}")
    for _ in range(60):
        try:
            firehose.describe_delivery_stream(DeliveryStreamName=name)
            time.sleep(3)
        except firehose.exceptions.ResourceNotFoundException:
            print("  Firehose deleted")
            return
    print("  Warning: Firehose delete still pending; check console")


def delete_kinesis(kinesis, name: str) -> None:
    try:
        kinesis.describe_stream_summary(StreamName=name)
    except kinesis.exceptions.ResourceNotFoundException:
        print(f"Kinesis stream not found (ok): {name}")
        return
    kinesis.delete_stream(StreamName=name, EnforceConsumerDeletion=True)
    print(f"Deleting Kinesis stream: {name}")
    for _ in range(60):
        try:
            kinesis.describe_stream_summary(StreamName=name)
            time.sleep(3)
        except kinesis.exceptions.ResourceNotFoundException:
            print("  Stream deleted")
            return
    print("  Warning: stream delete still pending; check console")


def delete_role(iam, role_name: str) -> None:
    try:
        iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        print(f"IAM role not found (ok): {role_name}")
        return
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="financehose-firehose-inline")
    except Exception:
        pass
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="financebuddy-firehose-inline")
    except Exception:
        pass
    # Detach any managed policies if present
    attached = iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
    for p in attached:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
    iam.delete_role(RoleName=role_name)
    print(f"Deleted IAM role: {role_name}")


def clear_s3_prefix(session, bucket: str, prefix: str) -> int:
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs = page.get("Contents") or []
        if not objs:
            continue
        keys = [{"Key": o["Key"]} for o in objs]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
        deleted += len(keys)
    return deleted


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Tear down FinanceBuddy streaming demo resources")
    parser.add_argument(
        "--delete-s3-data",
        action="store_true",
        help="Also delete objects under the stream S3 prefix (and stream-errors)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation",
    )
    args = parser.parse_args()

    names = stream_names(settings)
    session, _ = aws_session()

    print(
        "Will delete:\n"
        f"  Firehose: {names['firehose']}\n"
        f"  Kinesis:  {names['stream']}\n"
        f"  IAM role: {names['role']}\n"
        f"  Delete S3 stream objects: {bool(args.delete_s3_data)}\n"
    )
    if not args.yes:
        ans = input("Type DELETE to confirm: ").strip()
        if ans != "DELETE":
            raise SystemExit("Aborted")

    firehose = session.client("firehose")
    kinesis = session.client("kinesis")
    iam = session.client("iam")

    # Firehose first (depends on stream + role)
    delete_firehose(firehose, names["firehose"])
    delete_kinesis(kinesis, names["stream"])
    delete_role(iam, names["role"])

    if args.delete_s3_data:
        bucket = require_bucket(settings)
        prefix = names["s3_prefix"].rstrip("/") + "/"
        n = clear_s3_prefix(session, bucket, prefix)
        err_prefix = prefix.replace("/stream/", "/stream-errors/", 1)
        n += clear_s3_prefix(session, bucket, err_prefix)
        print(f"Deleted {n} S3 objects under stream prefixes")

    print(
        "\nTeardown finished. Confirm in console that Kinesis/Firehose are gone "
        "so shard-hour charges stop."
    )


if __name__ == "__main__":
    main()
