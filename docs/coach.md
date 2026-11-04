# Bedrock coach (optional)

I added this so the UI can explain the marts in plain English. The model only sees a small JSON pack (suggestions, habits, fraud alerts, latest spend mix). It should narrate — not invent balances or merchants. If it cites something that wasn't in the pack, we strip it.

## Enable

1. AWS Console → **Amazon Bedrock** → Model access → enable **Claude Haiku 4.5** in `us-east-1`.
2. IAM: allow `bedrock:InvokeModel` / Converse on that model for `finance_buddy_cli`. Also allow `bedrock:ApplyGuardrail` on the guardrail created below.
3. Create the guardrail (once per account) and copy the printed id into the environment or Streamlit secrets:

```bash
python scripts/create_bedrock_guardrail.py
```

The script writes `FINANCEBUDDY_BEDROCK_GUARDRAIL_ID` and `FINANCEBUDDY_BEDROCK_GUARDRAIL_VERSION` into `.env`. Re-run it after changing thresholds in `config/settings.yaml`. Creating it needs `bedrock:CreateGuardrail`, `bedrock:UpdateGuardrail`, and `bedrock:CreateGuardrailVersion`. The running app only needs `bedrock:ApplyGuardrail`.

4. Env:

```bash
FINANCEBUDDY_COACH_ENABLED=1
FINANCEBUDDY_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
FINANCEBUDDY_BEDROCK_GUARDRAIL_ID=your-guardrail-id
FINANCEBUDDY_BEDROCK_GUARDRAIL_VERSION=1
AWS_REGION=us-east-1
AWS_PROFILE=default
```

Haiku 4.5 needs the `us.` inference profile ID (not the bare foundation-model ID). Leave the guardrail id unset and the coach still runs; the call just is not screened.

5. Run Streamlit → **AI Coach** tab → **Generate plan**.

## Harness

- Compact capped context (top suggestions/habits/fraud only)
- System prompt: synthetic data, no invented numbers, JSON-only output
- Cite validation against strings present in the context
- Bedrock Guardrail on the Converse call, when the id is set:
  - Contextual grounding against the mart JSON, threshold 0.3. A faithful plan scores about 0.5, so 0.7 blocked good replies. The relevance check is off; it scored those same plans near 0.1 and blocked them.
  - Denied topics: `investment-picks`, `tax-advice`, `licensed-advice`. Spending actions such as canceling a duplicate subscription are not denied.
  - Prompt-attack filter at MEDIUM on the input. Other content filters are set to NONE. There is no PII filter, so synthetic names and amounts are not redacted.
  - A blocked reply raises in the UI and still counts against the daily call budget.
- In-process cache by context hash (no repeat Bedrock $ on re-click)
- Daily call budget file: `ml/artifacts/coach_daily_budget.json` (default 30)
- Per-browser-session call cap (`FINANCEBUDDY_COACH_SESSION_LIMIT`, default 2; `0` = unlimited). Cache hits do not count. Resets when the Streamlit session ends (new tab / refresh after timeout).

## Cost

Haiku + small prompts is usually cents per demo session. For a public portfolio demo, keep both limits low, e.g.:

```bash
FINANCEBUDDY_COACH_DAILY_LIMIT=15
FINANCEBUDDY_COACH_SESSION_LIMIT=2
```
