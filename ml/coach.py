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


def _strip_code_fences(text: str) -> str:
    text = text.strip().lstrip("\ufeff")
    # ```json ... ``` or bare ``` ... ```
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_balanced_object(text: str) -> str | None:
    """Return the first top-level {...} span, respecting strings/escapes."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _repair_json_text(blob: str) -> str:
    """Best-effort cleanup for common LLM JSON mistakes."""
    blob = blob.strip()
    # Smart quotes → plain
    blob = blob.replace("\u201c", '"').replace("\u201d", '"')
    blob = blob.replace("\u2018", "'").replace("\u2019", "'")
    # Trailing commas before } or ]
    blob = re.sub(r",(\s*[}\]])", r"\1", blob)
    # Bare newlines inside strings are illegal JSON; escape them when clearly inside quotes.
    # (Simple pass: only when we see an unescaped newline between quotes.)
    out: list[str] = []
    in_str = False
    escape = False
    for ch in blob:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Parse a JSON object out of messy model output.
    Raises json.JSONDecodeError if nothing workable is found.
    """
    cleaned = _strip_code_fences(text)
    candidates: list[str] = []
    balanced = _extract_balanced_object(cleaned)
    if balanced:
        candidates.append(balanced)
    candidates.append(cleaned)
    # Greedy fallback (last resort)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        candidates.append(match.group(0))

    errors: list[Exception] = []
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        for variant in (cand, _repair_json_text(cand)):
            if variant in seen and variant != cand:
                pass
            try:
                obj = json.loads(variant)
                if isinstance(obj, dict):
                    return obj
                errors.append(TypeError(f"JSON root was {type(obj).__name__}, expected object"))
            except Exception as exc:  # noqa: BLE001 — collect and try next variant
                errors.append(exc)

    # Prefer the last JSONDecodeError message (most informative)
    for exc in reversed(errors):
        if isinstance(exc, json.JSONDecodeError):
            raise exc
    raise json.JSONDecodeError("Could not extract a JSON object from model output", text, 0)


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
        # Haiku 4.5 needs an inference profile ID on Bedrock (not the raw foundation ID).
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )


def _converse_text(
    client,
    *,
    model_id: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
) -> tuple[str, dict]:
    resp = client.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=messages,
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    )
    parts = resp.get("output", {}).get("message", {}).get("content", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    usage = resp.get("usage") or {}
    return text, usage


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
    Retries once with a JSON-repair prompt if the first reply won't parse.
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
        "Create a grounded action plan from this FinanceBuddy mart context JSON.\n"
        "Reply with ONE JSON object only — no markdown, no commentary.\n"
        + json.dumps(context, default=str)
    )
    messages: list[dict] = [{"role": "user", "content": [{"text": user_msg}]}]

    text, usage = _converse_text(
        client,
        model_id=model_id,
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    repaired = False
    parse_error: Exception | None = None

    try:
        plan_obj = extract_json_object(text)
    except Exception as exc:  # noqa: BLE001
        parse_error = exc
        # Second try: ask the model to fix its own JSON (cheap, usually works).
        repair_messages = messages + [
            {"role": "assistant", "content": [{"text": text}]},
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            "Your previous reply was not valid JSON "
                            f"({type(exc).__name__}: {exc}). "
                            "Return ONLY a corrected JSON object matching the required schema. "
                            "No markdown fences, no trailing commas, no commentary."
                        )
                    }
                ],
            },
        ]
        text2, usage2 = _converse_text(
            client,
            model_id=model_id,
            system=SYSTEM_PROMPT,
            messages=repair_messages,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        input_tokens += int(usage2.get("inputTokens") or 0)
        output_tokens += int(usage2.get("outputTokens") or 0)
        text = text2
        repaired = True
        plan_obj = extract_json_object(text)

    plan = validate_coach_response(plan_obj, context)
    if parse_error is not None:
        plan.setdefault("harness_warnings", []).append(f"json_repair_retry:{type(parse_error).__name__}")

    meta = {
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_hash": context_hash(context),
        "raw_chars": len(text),
        "json_repaired": repaired,
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
