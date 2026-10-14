"""
Bedrock coach — turns mart rows into a short action plan.

Important: the model is a narrator, not a calculator. We only send a small JSON
pack from tables we already trust, then check that any "cites" actually appear
in that pack. If Bedrock is off, the Streamlit tab just tells you how to enable it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

USER_ID_RE = re.compile(r"^u_\d{4}$")

# Keep this strict. Loose prompts are how you get invented merchants and fake $.
SYSTEM_PROMPT = """You are FinanceBuddy Coach for a SYNTHETIC personal-finance demo.
You are NOT a licensed advisor. Do not invent numbers, merchants, or transactions.
Use ONLY the JSON context provided by the user message.
If something is missing, say you do not know.
Return ONLY valid JSON (no markdown) matching this schema:
{
  "summary": "2-4 sentences",
  "actions": [
    {
      "title": "short action",
      "why": "grounded in context",
      "estimated_monthly_savings": number or null,
      "priority": "high|medium|low",
      "cites": ["exact title or merchant_name from context"]
    }
  ],
  "risks": [{"title": "...", "why": "...", "cites": ["..."]}],
  "disclaimers": ["..."]
}
Prefer at most 5 actions. Cite only strings that appear in the context.
Always include a disclaimer that data is synthetic / educational.
"""


def validate_user_id(user_id: str) -> str:
    if not USER_ID_RE.match(user_id):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    return user_id


def _trim_rows(rows: list[dict], n: int, fields: list[str] | None = None) -> list[dict]:
    out = []
    for row in rows[:n]:
        if fields:
            out.append({k: row.get(k) for k in fields if k in row})
        else:
            out.append(row)
    return out


def build_coach_context(
    *,
    user: dict,
    suggestions: list[dict],
    habits: list[dict],
    fraud: list[dict],
    dup_subs: list[dict],
    cashflow: list[dict],
    max_suggestions: int = 5,
    max_habits: int = 3,
    max_fraud: int = 5,
    max_dup_subs: int = 3,
) -> dict[str, Any]:
    """Shrink the mart dump into something cheap enough to send to Haiku."""
    uid = validate_user_id(str(user["user_id"]))

    # Only the latest month's category mix — full cashflow history is noisy + pricey.
    spend_by_month: dict[str, float] = {}
    for row in cashflow:
        m = str(row.get("month_start"))
        spend_by_month[m] = spend_by_month.get(m, 0.0) + float(row.get("spend") or 0)
    latest = max(spend_by_month) if spend_by_month else None
    latest_mix = []
    if latest:
        for row in cashflow:
            if str(row.get("month_start")) == latest and float(row.get("spend") or 0) > 0:
                latest_mix.append(
                    {
                        "category": row.get("category"),
                        "spend": round(float(row.get("spend") or 0), 2),
                    }
                )
        latest_mix = sorted(latest_mix, key=lambda r: r["spend"], reverse=True)[:8]

    return {
        "user": {
            "user_id": uid,
            "display_name": user.get("display_name"),
            "persona": user.get("persona"),
            "city": user.get("city"),
            "state": user.get("state"),
            "monthly_income": float(user.get("monthly_income") or 0),
        },
        "suggestions": _trim_rows(
            suggestions,
            max_suggestions,
            [
                "suggestion_type",
                "title",
                "reason",
                "estimated_monthly_savings",
                "confidence",
                "priority_score",
            ],
        ),
        "habits": _trim_rows(habits, max_habits),
        "fraud_alerts": _trim_rows(
            fraud,
            max_fraud,
            ["transaction_ts", "merchant_name", "amount_abs", "fraud_score", "reason_codes"],
        ),
        "duplicate_subscriptions": _trim_rows(dup_subs, max_dup_subs),
        "latest_month_spend_mix": {"month": latest, "categories": latest_mix},
        "totals": {
            "suggestion_count": len(suggestions),
            "estimated_monthly_opportunity": round(
                sum(float(s.get("estimated_monthly_savings") or 0) for s in suggestions), 2
            ),
            "fraud_alert_count": len(fraud),
            "duplicate_subscription_groups": len(dup_subs),
        },
    }


def context_hash(context: dict[str, Any]) -> str:
    blob = json.dumps(context, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _allowed_cite_strings(context: dict[str, Any]) -> set[str]:
    # Anything the model is allowed to put in cites[]. If it's not here, we drop it.
    allowed: set[str] = set()
    for s in context.get("suggestions") or []:
        if s.get("title"):
            allowed.add(str(s["title"]))
        if s.get("suggestion_type"):
            allowed.add(str(s["suggestion_type"]))
    for h in context.get("habits") or []:
        for key in ("merchant_name", "category", "insight", "title", "habit_type"):
            if h.get(key):
                allowed.add(str(h[key]))
    for f in context.get("fraud_alerts") or []:
        if f.get("merchant_name"):
            allowed.add(str(f["merchant_name"]))
    for d in context.get("duplicate_subscriptions") or []:
        if d.get("overlap_group"):
            allowed.add(str(d["overlap_group"]))
        names = d.get("merchant_names")
        if isinstance(names, list):
            allowed.update(str(x) for x in names)
        elif names:
            allowed.add(str(names))
    for c in (context.get("latest_month_spend_mix") or {}).get("categories") or []:
        if c.get("category"):
            allowed.add(str(c["category"]))
    return allowed


def extract_json_object(text: str) -> dict[str, Any]:
    # Models love wrapping JSON in ``` fences even when you say not to.
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def validate_coach_response(plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Don't fail the whole request on a bad cite — strip it and keep a warning."""
    allowed = _allowed_cite_strings(context)
    warnings: list[str] = []

    if not isinstance(plan.get("summary"), str) or not plan["summary"].strip():
        warnings.append("missing_summary")
        plan["summary"] = "No summary returned by the model."

    for key in ("actions", "risks", "disclaimers"):
        if key not in plan or plan[key] is None:
            plan[key] = []
        if not isinstance(plan[key], list):
            warnings.append(f"invalid_{key}")
            plan[key] = []

    def check_items(items: list, label: str) -> list:
        cleaned = []
        for item in items[:5]:
            if not isinstance(item, dict):
                warnings.append(f"{label}_non_object")
                continue
            cites = item.get("cites") or []
            if not isinstance(cites, list):
                cites = []
            bad = [c for c in cites if str(c) not in allowed]
            if bad:
                warnings.append(f"ungrounded_cite:{label}:{bad[0]}")
                item = {**item, "cites": [c for c in cites if str(c) in allowed]}
            cleaned.append(item)
        return cleaned

    plan["actions"] = check_items(plan.get("actions") or [], "actions")
    plan["risks"] = check_items(plan.get("risks") or [], "risks")
    plan["disclaimers"] = [str(d) for d in (plan.get("disclaimers") or [])][:5]
    if not any("synthetic" in d.lower() or "educat" in d.lower() or "not financial" in d.lower() for d in plan["disclaimers"]):
        plan["disclaimers"].append(
            "Educational demo on synthetic data — not personalized financial advice."
        )
    plan["harness_warnings"] = warnings
    return plan


class CoachCallBudget:
    """Dumb but effective: a JSON file counting Bedrock calls for today."""

    def __init__(self, path: Path, daily_limit: int):
        self.path = path
        self.daily_limit = daily_limit

    def _load(self) -> dict:
        if not self.path.exists():
            return {"day": str(date.today()), "count": 0}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"day": str(date.today()), "count": 0}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def remaining(self) -> int:
        data = self._load()
        if data.get("day") != str(date.today()):
            return self.daily_limit
        return max(0, self.daily_limit - int(data.get("count") or 0))

    def consume(self) -> None:
        data = self._load()
        if data.get("day") != str(date.today()):
            data = {"day": str(date.today()), "count": 0}
        data["count"] = int(data.get("count") or 0) + 1
        self._save(data)


def coach_enabled() -> bool:
    return os.getenv("FINANCEBUDDY_COACH_ENABLED", "0").strip().lower() in {"1", "true", "yes"}


def default_model_id() -> str:
    return os.getenv(
        "FINANCEBUDDY_BEDROCK_MODEL",
        # Claude Haiku 4.5 (cheap). If Converse requires an inference profile, use:
        # us.anthropic.claude-haiku-4-5-20251001-v1:0
        "anthropic.claude-haiku-4-5-20251001-v1:0",
    )


def invoke_bedrock_coach(
    context: dict[str, Any],
    *,
    model_id: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Call Bedrock Converse API. Returns (plan, meta).
    Requires bedrock:InvokeModel / Converse permission and model access enabled.
    """
    import boto3

    model_id = model_id or default_model_id()
    region = region or os.getenv("AWS_REGION", "us-east-1")
    profile = profile if profile is not None else os.getenv("AWS_PROFILE")

    session = (
        boto3.Session(profile_name=profile, region_name=region)
        if profile
        else boto3.Session(region_name=region)
    )
    client = session.client("bedrock-runtime")

    user_msg = (
        "Create a grounded action plan from this FinanceBuddy mart context JSON:\n"
        + json.dumps(context, default=str)
    )
    resp = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    )
    parts = resp.get("output", {}).get("message", {}).get("content", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    usage = resp.get("usage") or {}
    plan = validate_coach_response(extract_json_object(text), context)
    meta = {
        "model_id": model_id,
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "context_hash": context_hash(context),
        "raw_chars": len(text),
    }
    return plan, meta


def run_coach(
    context: dict[str, Any],
    *,
    artifacts_dir: str | Path,
    daily_limit: int = 30,
    cache: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Full harness: enable flag, daily budget, response cache, Bedrock call, validation.
    `cache` is an optional mutable dict (e.g. st.session_state) keyed by context hash.
    """
    if not coach_enabled():
        raise RuntimeError(
            "Coach disabled. Set FINANCEBUDDY_COACH_ENABLED=1 and enable Bedrock model access."
        )

    # Same user + same mart snapshot => reuse the last answer (Streamlit re-runs a lot).
    key = context_hash(context)
    if cache is not None and key in cache:
        plan = cache[key]["plan"]
        meta = {**cache[key]["meta"], "cache_hit": True}
        return plan, meta

    budget = CoachCallBudget(Path(artifacts_dir) / "coach_daily_budget.json", daily_limit)
    if budget.remaining() <= 0:
        raise RuntimeError(f"Daily Bedrock coach limit reached ({daily_limit} calls).")

    plan, meta = invoke_bedrock_coach(context)
    budget.consume()
    meta = {**meta, "cache_hit": False, "daily_remaining": budget.remaining()}
    if cache is not None:
        cache[key] = {"plan": plan, "meta": meta}
    return plan, meta
