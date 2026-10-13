# AWS deploy (optional, keep costs low)

You do **not** need AWS to learn the project. Use this when you want cloud keywords on a resume.

## 1. Prerequisites

- AWS account with a **budget alert** ($10–20)
- IAM user/role with S3 + Athena + Glue permissions
- Bucket name, e.g. `financebuddy-<yourname>`
- Region: `us-east-1` (or update config)

```bash
# after local pipeline has produced data/lake/*
set FINANCEBUDDY_S3_BUCKET=financebuddy-yourname
python scripts/upload_to_s3.py --layers raw bronze gold
```

## 2. Glue / Athena

In Athena:

1. Create database `financebuddy`.
2. Create external tables pointing at:

```text
s3://YOUR_BUCKET/financebuddy/bronze/users.parquet
s3://YOUR_BUCKET/financebuddy/bronze/transactions.parquet
s3://YOUR_BUCKET/financebuddy/bronze/merchants.parquet
```

Or run a Glue crawler on `s3://YOUR_BUCKET/financebuddy/bronze/`.

For Parquet single files, `CREATE EXTERNAL TABLE ... STORED AS PARQUET LOCATION 's3://.../bronze/'` works if you organize one table per prefix (recommended):

```text
s3://bucket/financebuddy/bronze/users/
s3://bucket/financebuddy/bronze/transactions/
s3://bucket/financebuddy/bronze/merchants/
```

You can re-export with one folder per table if you prefer crawlers.

## 3. dbt on Athena

```bash
pip install dbt-athena-community
```

Edit `dbt_financebuddy/profiles.yml` → `athena` output (`s3_staging_dir`, schema, region). Then:

```bash
dbt build --project-dir dbt_financebuddy --profiles-dir dbt_financebuddy --target athena
```

Partition / convert to columnar layouts before large scans. Athena bills ~$5/TB scanned.

## 4. SageMaker

Open SageMaker Studio, upload this repo (or clone), open a small `ml.t3.medium` notebook, and run the same `ml/*.py` modules against data pulled from S3 / Athena. **Stop the notebook** when finished.

## 5. What not to turn on

- EMR / Redshift for this dataset size
- Always-on SageMaker endpoints
- NAT Gateway “for convenience”
- Unbounded Athena workgroups (set a bytes-scanned cutoff)

## Free tier notes

New accounts may receive AWS credits (see current AWS Free Tier). Glue Data Catalog has always-free monthly object/request limits. Athena is pay-per-scan. SageMaker has a short notebook/training trial window — verify current Free Tier table in the AWS console.
