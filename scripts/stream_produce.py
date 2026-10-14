"""
Push JSON transaction events into Kinesis.

Default: invent a few hundred demo rows. --from-bronze replays real local Parquet
if you've already run the batch pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings
from streaming_common import aws_session, stream_names


def _events_from_parquet(path: Path, count: int) -> list[dict]:
    import polars as pl

    df = pl.read_parquet(path).head(count)
    return df.to_dicts()


def _synthetic_events(count: int, seed: int = 42) -> list[dict]:
    rng = np.random.default_rng(seed)
    now = datetime.now(timezone.utc)
    events = []
    for i in range(count):
        events.append(
            {
                "transaction_id": f"stream_{now.strftime('%Y%m%d%H%M%S')}_{i:05d}",
                "user_id": f"u_{int(rng.integers(0, 250)):04d}",
                "transaction_ts": now.isoformat(),
                "posted_date": now.date().isoformat(),
                "merchant_id": "m_stream_demo",
                "merchant_name": "Stream Demo Merchant",
                "merchant_descriptor": "STREAM*DEMO",
                "category": "Shopping",
                "amount": round(float(-rng.uniform(5, 80)), 2),
                "currency": "USD",
                "txn_type": "debit",
                "channel": "online",
                "city": "Demo City",
                "state": "NY",
                "status": "completed",
                "is_fraud_label": 0,
                "is_duplicate_charge_label": 0,
                "planted_tag": "kinesis_demo",
                "event_source": "kinesis_producer",
            }
        )
    return events


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Produce txn events to Kinesis")
    parser.add_argument("--count", type=int, default=200, help="Number of records to send")
    parser.add_argument("--batch-size", type=int, default=25, help="Kinesis put_records batch size (max 500)")
    parser.add_argument("--sleep-ms", type=int, default=50, help="Pause between batches")
    parser.add_argument(
        "--from-bronze",
        action="store_true",
        help="Replay rows from local data/lake/bronze/transactions.parquet when present",
    )
    args = parser.parse_args()

    names = stream_names(settings)
    stream = names["stream"]
    session, _ = aws_session()
    kinesis = session.client("kinesis")

    bronze = Path(settings["paths"]["bronze"]) / "transactions.parquet"
    if args.from_bronze and bronze.exists():
        events = _events_from_parquet(bronze, args.count)
        print(f"Loaded {len(events)} events from {bronze}")
    else:
        events = _synthetic_events(args.count, seed=int(settings.get("seed", 42)))
        print(f"Generated {len(events)} synthetic stream events")

    sent = 0
    failed = 0
    batch_size = max(1, min(args.batch_size, 500))
    for start in range(0, len(events), batch_size):
        chunk = events[start : start + batch_size]
        # Newline-delimited JSON plays nicer when Firehose concatenates records into one object.
        records = [
            {
                "Data": (json.dumps(ev, default=str) + "\n").encode("utf-8"),
                "PartitionKey": str(ev.get("user_id") or "unknown"),
            }
            for ev in chunk
        ]
        resp = kinesis.put_records(StreamName=stream, Records=records)
        failed += int(resp.get("FailedRecordCount") or 0)
        sent += len(records) - int(resp.get("FailedRecordCount") or 0)
        if args.sleep_ms:
            time.sleep(args.sleep_ms / 1000.0)
        print(f"  batch {start // batch_size + 1}: sent={sent} failed={failed}")

    print(
        f"\nDone. Put ~{sent} records to Kinesis stream `{stream}` "
        f"({failed} failed).\n"
        "Firehose buffers ~60s (or 1 MB) before writing gzip objects under "
        f"`{names['s3_prefix']}`."
    )


if __name__ == "__main__":
    main()
