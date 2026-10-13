# FinanceBuddy

Personal finance lakehouse for learning: synthetic transactions on S3-style storage, **dbt** transforms, ML for habits / duplicate subscriptions / fraud, and a Streamlit coach UI.

Runs **fully locally** with DuckDB (no AWS required). The same layout maps to **S3 + Glue + Athena + SageMaker** when you are ready.

## Features

- Synthetic multi-user bank ledger with planted ground truth (duplicates, price creep, fraud bursts)
- Medallion layout: `raw` → `bronze` → `silver` → `gold`
- **dbt** models and tests (DuckDB local; Athena profile included)
- Recurring subscription detection and duplicate-service flags
- Habit / spending-pattern insights vs a user's own history
- Fraud / anomaly scoring with explainable reason codes
- Ranked improvement suggestions with estimated monthly savings
- Streamlit app for exploring one user end-to-end

## Quick start (local)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
python scripts/run_pipeline.py
streamlit run app/streamlit_app.py
```

> **Note:** This project uses **Polars** (not Pandas). On some Windows machines Application Control blocks Pandas DLLs; Polars avoids that and is used end-to-end.


`run_pipeline.py` generates data, loads bronze tables into DuckDB, runs `dbt build`, scores subscriptions / habits / fraud, and writes gold suggestion tables.

## Project layout

```text
financebuddy/
├── config/                 # paths and pipeline settings
├── data/                   # local lake (gitignored except samples)
│   └── lake/{raw,bronze,silver,gold}/
├── generator/              # synthetic transaction generator
├── dbt_financebuddy/       # dbt project (DuckDB / Athena)
├── ml/                     # subscription, habit, fraud, suggestions
├── app/                    # Streamlit UI
├── scripts/                # pipeline + AWS helpers
└── docs/                   # architecture and AWS deploy notes
```

## AWS path (optional)

See [docs/aws_deploy.md](docs/aws_deploy.md). Short version:

1. Upload `data/lake/raw` Parquet to an S3 bucket.
2. Register tables in Glue / Athena.
3. Point `profiles.yml` at the Athena target and run `dbt build`.
4. Train / score with SageMaker Studio or the same `ml/` scripts against Athena/S3.

Keep a **$10–20 AWS budget alert** on. Prefer Athena + serverless; stop SageMaker notebooks when idle.

## Cost note

Local DuckDB path: **$0**.  
AWS with ~1 GB Parquet and careful Athena scans: usually **cents to a few dollars / month**. Avoid always-on notebooks, Redshift, EMR, and NAT gateways for this project.
