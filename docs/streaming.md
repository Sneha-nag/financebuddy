# Streaming demo: Kinesis → Firehose → S3

Side quest for learning near-real-time ingest. The main UI still reads gold marts from batch — this just proves events can land in the lake. Tear it down when you're done so you're not paying for an idle shard.

## Architecture

```text
stream_produce.py  →  Amazon Kinesis Data Streams (1 shard)
                              ↓
                   Amazon Data Firehose (~60s / 1 MB buffer)
                              ↓
                   s3://BUCKET/financebuddy/raw/stream/  (gzip JSON lines)
```

## Prerequisites

- `FINANCEBUDDY_S3_BUCKET` set (bucket already exists)
- IAM user can manage Kinesis, Firehose, IAM roles, and write to the bucket
- Budget alert on

## Commands

```bash
# from repo root, venv on
python scripts/stream_setup.py
python scripts/stream_produce.py --count 200
# optional: replay local bronze rows
python scripts/stream_produce.py --count 500 --from-bronze

# wait ~60–90 seconds, then list landed objects
aws s3 ls s3://$FINANCEBUDDY_S3_BUCKET/financebuddy/raw/stream/ --recursive
```

**Always tear down** after the demo:

```bash
python scripts/stream_teardown.py --yes
# also remove landed demo objects:
python scripts/stream_teardown.py --yes --delete-s3-data
```

## Portfolio talking points

- Simulated card/bank **event stream** (not only batch CSV)
- **Kinesis** for durable stream storage + fan-out
- **Firehose** for managed delivery into the data lake (**S3**)
- Batch lakehouse (dbt/Athena/ML) can later pick up `raw/stream/` as an additional source

## Cost notes

- 1 shard is enough for this demo; do not leave the stream running overnight
- Firehose has a small buffer; you pay for delivery + S3 PUT/storage of tiny demo files
- Optional next step (not included): Managed Flink for windowed aggregates

## Env overrides

See `.env.example` (`FINANCEBUDDY_KINESIS_STREAM`, `FINANCEBUDDY_FIREHOSE_STREAM`, etc.).
