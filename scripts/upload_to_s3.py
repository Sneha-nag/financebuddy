"""Upload local lake folders to S3 (optional AWS step)."""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_settings


def upload_dir(local_dir: Path, bucket: str, prefix: str, profile: str | None, region: str) -> int:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
    s3 = session.client("s3")
    count = 0
    for path in local_dir.rglob("*"):
        if path.is_file():
            key = f"{prefix.rstrip('/')}/{path.relative_to(local_dir).as_posix()}"
            extra = {}
            content_type, _ = mimetypes.guess_type(path.name)
            if content_type:
                extra["ContentType"] = content_type
            s3.upload_file(str(path), bucket, key, ExtraArgs=extra or None)
            print(f"s3://{bucket}/{key}")
            count += 1
    return count


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Upload FinanceBuddy lake data to S3")
    parser.add_argument("--bucket", default=os.getenv("FINANCEBUDDY_S3_BUCKET"))
    parser.add_argument("--prefix", default=settings["aws"]["s3_prefix"])
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", settings["aws"]["region"]))
    parser.add_argument(
        "--layers",
        nargs="+",
        default=["raw", "bronze", "gold"],
        help="Which lake layers to upload",
    )
    args = parser.parse_args()

    if not args.bucket:
        raise SystemExit("Set --bucket or FINANCEBUDDY_S3_BUCKET")

    lake = Path(settings["paths"]["lake_root"])
    total = 0
    for layer in args.layers:
        local = lake / layer
        if not local.exists():
            print(f"Skip missing layer: {local}")
            continue
        total += upload_dir(local, args.bucket, f"{args.prefix}/{layer}", args.profile, args.region)
    print(f"Uploaded {total} files")


if __name__ == "__main__":
    main()