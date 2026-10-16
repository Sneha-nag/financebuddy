# Bedrock coach (optional)

I added this so the UI can explain the marts in plain English. The model only sees a small JSON pack (suggestions, habits, fraud alerts, latest spend mix). It should narrate — not invent balances or merchants. If it cites something that wasn't in the pack, we strip it.

## Enable

1. AWS Console → **Amazon Bedrock** → Model access → enable **Claude Haiku 4.5** in `us-east-1`.
2. IAM: allow `bedrock:InvokeModel` / Converse on that model for `finance_buddy_cli`.
3. Env:

```bash
FINANCEBUDDY_COACH_ENABLED=1
FINANCEBUDDY_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
AWS_REGION=us-east-1
AWS_PROFILE=default
```

Haiku 4.5 needs the `us.` inference profile ID (not the bare foundation-model ID).


4. Run Streamlit → **AI Coach** tab → **Generate plan**.

## Harness

- Compact capped context (top suggestions/habits/fraud only)
- System prompt: synthetic data, no invented numbers, JSON-only output
- Cite validation against strings present in the context
- In-process cache by context hash (no repeat Bedrock $ on re-click)
- Daily call budget file: `ml/artifacts/coach_daily_budget.json` (default 30)
- Per-browser-session call cap (`FINANCEBUDDY_COACH_SESSION_LIMIT`, default 2; `0` = unlimited). Cache hits do not count. Resets when the Streamlit session ends (new tab / refresh after timeout).

## Cost

Haiku + small prompts is usually cents per demo session. For a public portfolio demo, keep both limits low, e.g.:

```bash
FINANCEBUDDY_COACH_DAILY_LIMIT=15
FINANCEBUDDY_COACH_SESSION_LIMIT=2
```
