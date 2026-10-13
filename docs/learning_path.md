# Suggested learning path

1. Run `python scripts/run_pipeline.py` and open the Streamlit app.
2. Pick a user with fraud alerts and duplicate subscriptions; read `mart_suggestions`.
3. Open `generator/generate.py` and see how signals are planted.
4. Read dbt models under `staging` → `intermediate` → `marts`.
5. Inspect `ml/artifacts/ground_truth_recovery.json` — this is the “did the system find what we hid?” scorecard.
6. When ready for cloud: follow `aws_deploy.md` with a budget alert first.
