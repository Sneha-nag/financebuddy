# Live AWS demo (portfolio)

Run the Streamlit coach against **S3 gold marts** (and optionally Athena), with a password and cost guardrails.

## Architecture (what to say on your resume)

1. Synthetic ledger → lake on **S3**
2. **dbt** transforms on **Athena** / Glue
3. ML writes **gold** Parquet (suggestions, habits, fraud, …)
4. **Streamlit** live app reads gold from S3 (cheap) or queries Athena marts

DuckDB remains for local development only; it is not required for the live AWS story.

## Backends

Set `FINANCEBUDDY_BACKEND`:

| Value | Behavior |
|-------|----------|
| `duckdb` | Local `data/financebuddy.duckdb` (default) |
| `s3` | **Recommended live path** — read `s3://bucket/financebuddy/gold/*.parquet` |
| `athena` | Query Glue database `marts` for dbt tables; ML tables fall back to S3 gold |

## One-time setup

1. Budget alert ($10–20) on the AWS account.
2. Pipeline + upload (from a machine that has the lake):

   ```bash
   python scripts/run_pipeline.py
   python scripts/upload_to_s3.py --bucket YOUR_BUCKET --layers raw bronze gold
   dbt build --project-dir dbt_financebuddy --profiles-dir dbt_financebuddy --target athena
   ```

3. Athena workgroup (console → Athena → Workgroups → your workgroup → Edit):
   - Set a **bytes scanned cutoff per query** (e.g. 100 MB–1 GB)
   - Results location: `s3://YOUR_BUCKET/athena-results/`

4. App env (local or Streamlit Cloud secrets):

   ```bash
   FINANCEBUDDY_BACKEND=s3
   FINANCEBUDDY_S3_BUCKET=financebuddy-sneha
   AWS_REGION=us-east-1
   AWS_PROFILE=default
   FINANCEBUDDY_APP_PASSWORD=choose-a-demo-password
   ```

   For Athena mode also set:

   ```bash
   FINANCEBUDDY_BACKEND=athena
   FINANCEBUDDY_ATHENA_S3_STAGING=s3://financebuddy-sneha/athena-results/
   FINANCEBUDDY_ATHENA_MARTS_SCHEMA=marts
   FINANCEBUDDY_ATHENA_WORKGROUP=primary
   ```

## Run locally against AWS

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
$env:FINANCEBUDDY_BACKEND="s3"
$env:FINANCEBUDDY_S3_BUCKET="financebuddy-sneha"
$env:FINANCEBUDDY_APP_PASSWORD="your-demo-password"
streamlit run app/streamlit_app.py
```

## Host on Streamlit Community Cloud

### A. IAM user for the app (read-only)

Create a dedicated user (e.g. `financebuddy-streamlit`) with a policy limited to gold reads:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::financebuddy-sneha",
      "Condition": {
        "StringLike": { "s3:prefix": ["financebuddy/gold", "financebuddy/gold/*"] }
      }
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::financebuddy-sneha/financebuddy/gold/*"
    }
  ]
}
```

Create an access key for that user. Do **not** paste your admin/`finance_buddy_cli` key into Cloud if you can avoid it.

Leave `FINANCEBUDDY_COACH_ENABLED` unset on the public app unless you also grant Bedrock and accept the cost.

### B. Deploy

1. Push `main` to GitHub (`Sneha-nag/financebuddy`).
2. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub.
3. **New app**:
   - Repo: `Sneha-nag/financebuddy`
   - Branch: `main`
   - Main file path: `app/streamlit_app.py`
   - Python version: 3.11 or 3.12 if offered
4. **Advanced settings → Secrets** (TOML):

   ```toml
   FINANCEBUDDY_APP_PASSWORD = "D3MOTRY@OUT"
   # Optional; shown beside the login box (defaults to D3MOTRY@OUT in the app)
   # FINANCEBUDDY_APP_PASSWORD_HINT = "D3MOTRY@OUT"
   FINANCEBUDDY_BACKEND = "s3"
   FINANCEBUDDY_S3_BUCKET = "financebuddy-sneha"
   AWS_REGION = "us-east-1"
   AWS_ACCESS_KEY_ID = "AKIA..."
   AWS_SECRET_ACCESS_KEY = "..."
   # Optional coach (also grant Bedrock on the IAM user):
   # FINANCEBUDDY_COACH_ENABLED = "1"
   # FINANCEBUDDY_BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
   # FINANCEBUDDY_COACH_DAILY_LIMIT = "15"
   # FINANCEBUDDY_COACH_SESSION_LIMIT = "2"
   ```

5. Deploy. First build installs lean `requirements.txt` (a few minutes).
6. Open the URL → enter the demo password → pick a user.

If the app errors on S3, check the Cloud logs, that gold still exists under `s3://financebuddy-sneha/financebuddy/gold/`, and that the IAM key can `GetObject` there.

## Cost guardrails (built into the app)

- Password gate when `FINANCEBUDDY_APP_PASSWORD` is set
- In-process cache of S3/Athena results (no re-download every widget click)
- User-scoped queries only (`u_####`); validated IDs
- SQL `LIMIT`s on Athena paths
- Large fraud mart prefers **S3 filter** even in Athena mode
- Prefer **`s3` backend** for the public demo URL; use `athena` when you want to show SQL-on-Glue live
- Coach: daily call budget + per-browser-session cap (`FINANCEBUDDY_COACH_SESSION_LIMIT`) + response cache

## IAM sketch (demo user)

- `s3:GetObject`, `s3:ListBucket` on the gold prefix
- If Athena: `athena:StartQueryExecution`, `GetQuery*`, Glue `GetTable`/`GetDatabase`, and write to the Athena results prefix
