# Architecture

## Local (default)

```text
generator (Python)
    -> data/lake/raw   (CSV landing zone)
    -> data/lake/bronze (Parquet)
    -> DuckDB bronze.* tables
    -> dbt (staging / intermediate / marts)
    -> ml/ (habits, fraud, duplicate charges, suggestions)
    -> data/lake/gold + DuckDB marts.*
    -> Streamlit app
```

## AWS (optional)

```text
generator / local pipeline
    -> S3://bucket/financebuddy/{raw,bronze,gold}/
    -> Glue Data Catalog tables
    -> Athena SQL + dbt-athena
    -> SageMaker Studio (same ml/ scripts)
    -> Streamlit locally or App Runner
```

## Medallion layers

| Layer | Contents |
|---|---|
| raw | CSV dumps as landed |
| bronze | Typed Parquet users / transactions / merchants |
| silver | dbt staging + intermediate (normalized merchants, recurring gaps) |
| gold | marts: cashflow, subscriptions, duplicates, habits, fraud, suggestions |

## Planted signals (ground truth)

The generator hides known cases so you can measure recovery:

- Duplicate music / cloud subscriptions
- Netflix price creep
- Same-merchant double charges
- Card-test micro-auth bursts + large new-geo charges
- Weekend dining spikes (spender persona)
- Seasonal travel bursts

See `ml/artifacts/ground_truth_recovery.json` after `run_pipeline.py`.
