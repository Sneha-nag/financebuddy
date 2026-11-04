"""
Amazon Bedrock Guardrail for the coach.

Narrow on purpose: contextual grounding against the mart JSON, three denied
topics (investment picks, tax advice, licensed-advice claims), and a medium
prompt-attack filter. No PII filter — synthetic names and dollar amounts
would get redacted.

The AWS resource is created by scripts/create_bedrock_guardrail.py. At call
time the coach only attaches it when FINANCEBUDDY_BEDROCK_GUARDRAIL_ID is set.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

BLOCKED_INPUT = (
    "FinanceBuddy blocked this request. It only explains the synthetic mart data "
    "already on screen."
)
BLOCKED_OUTPUT = (
    "FinanceBuddy blocked this reply because it was not grounded in your mart data "
    "or it crossed a denied topic (investment picks, tax advice, or licensed advice)."
)


class GuardrailBlocked(RuntimeError):
    """Bedrock stopped the coach call. The reply is not a plan."""


def load_env_defaults() -> None:
    """Fill missing os.environ keys from the repo .env. Never overrides."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


def guardrail_config_from_env() -> dict[str, str] | None:
    """Converse guardrailConfig, or None when the coach should run without one.

    Environment wins. If it is unset, settings.yaml supplies the published
    financebuddy-coach id so a hosted app does not silently skip the screen.
    """
    load_env_defaults()
    identifier = (os.getenv("FINANCEBUDDY_BEDROCK_GUARDRAIL_ID") or "").strip()
    version = (os.getenv("FINANCEBUDDY_BEDROCK_GUARDRAIL_VERSION") or "").strip()
    if not identifier:
        from config import load_settings

        cfg = ((load_settings().get("ml") or {}).get("coach") or {}).get("guardrail") or {}
        identifier = str(cfg.get("id") or "").strip()
        version = version or str(cfg.get("version") or "").strip()
    if not identifier:
        return None
    return {
        "guardrailIdentifier": identifier,
        "guardrailVersion": version or "DRAFT",
        "trace": "enabled",
    }


def coach_message_content(instruction: str, grounding_text: str, *, guardrail: bool) -> list[dict]:
    """
    User content for one coach turn.

    With a guardrail, the mart JSON is the grounding source and the untrusted
    input. The instruction is the query only, so the prompt-attack filter does
    not score our own "reply in JSON" wording. A plain text block carries both,
    because that is what the model answers. Qualifiers are omitted when no
    guardrail is attached.
    """
    visible = instruction.rstrip() + "\n" + grounding_text
    if not guardrail:
        return [{"text": visible}]
    # The instruction is the query only. Tagging it guard_content makes the
    # prompt-attack filter treat "reply with JSON only" as an attack.
    # The mart JSON is both the grounding source and the untrusted input.
    return [
        {
            "guardContent": {
                "text": {
                    "text": grounding_text,
                    "qualifiers": ["grounding_source", "guard_content"],
                }
            }
        },
        {
            "guardContent": {
                "text": {
                    "text": instruction,
                    "qualifiers": ["query"],
                }
            }
        },
        {"text": visible},
    ]


def _assessments(trace: dict[str, Any] | None) -> list[dict]:
    guardrail = (trace or {}).get("guardrail") or {}
    found: list[dict] = []
    for assessment in (guardrail.get("inputAssessment") or {}).values():
        if isinstance(assessment, dict):
            found.append(assessment)
    for assessments in (guardrail.get("outputAssessments") or {}).values():
        if isinstance(assessments, list):
            found.extend(a for a in assessments if isinstance(a, dict))
        elif isinstance(assessments, dict):
            found.append(assessments)
    return found


def summarize_guardrail_trace(trace: dict[str, Any] | None) -> str:
    """Short reason list, e.g. 'GROUNDING score 0.21 < 0.70; topic:tax-advice'."""
    parts: list[str] = []
    for assessment in _assessments(trace):
        topics = ((assessment.get("topicPolicy") or {}).get("topics")) or []
        for topic in topics:
            if str(topic.get("action", "")).upper() == "BLOCKED":
                parts.append(f"topic:{topic.get('name')}")
        filters = ((assessment.get("contentPolicy") or {}).get("filters")) or []
        for item in filters:
            if str(item.get("action", "")).upper() == "BLOCKED":
                parts.append(f"content:{item.get('type')}")
        grounding = ((assessment.get("contextualGroundingPolicy") or {}).get("filters")) or []
        for item in grounding:
            if str(item.get("action", "")).upper() != "BLOCKED":
                continue
            score = item.get("score")
            threshold = item.get("threshold")
            if isinstance(score, (int, float)) and isinstance(threshold, (int, float)):
                parts.append(f"{item.get('type')} score {score:.2f} < {threshold:.2f}")
            else:
                parts.append(str(item.get("type") or "contextual-grounding"))
    return "; ".join(parts)


def grounding_scores(trace: dict[str, Any] | None) -> dict[str, float]:
    scores: dict[str, float] = {}
    for assessment in _assessments(trace):
        filters = ((assessment.get("contextualGroundingPolicy") or {}).get("filters")) or []
        for item in filters:
            kind = str(item.get("type") or "").lower()
            score = item.get("score")
            if kind in {"grounding", "relevance"} and isinstance(score, (int, float)):
                scores[kind] = float(score)
    return scores


def raise_if_guardrail_blocked(stop_reason: str | None, trace: dict[str, Any] | None) -> None:
    if stop_reason != "guardrail_intervened":
        return
    reason = summarize_guardrail_trace(trace)
    detail = f" ({reason})" if reason else ""
    raise GuardrailBlocked(
        "Bedrock Guardrail blocked this coach reply"
        f"{detail}. No plan was shown."
    )


def policy_args(settings: dict) -> dict[str, Any]:
    """CreateGuardrail / UpdateGuardrail fields, minus the name."""
    cfg = (settings.get("ml") or {}).get("coach", {}).get("guardrail") or {}
    attack = str(cfg.get("prompt_attack_strength") or "MEDIUM").upper()
    if attack not in {"NONE", "LOW", "MEDIUM", "HIGH"}:
        attack = "MEDIUM"
    grounding = float(cfg.get("grounding_threshold", 0.3))

    def _filter(kind: str, input_strength: str, output_strength: str) -> dict[str, str]:
        return {
            "type": kind,
            "inputStrength": input_strength,
            "outputStrength": output_strength,
        }

    return {
        "description": (
            "FinanceBuddy coach. Ground replies in the mart JSON, deny investment "
            "picks, tax advice, and licensed-advice claims, and filter prompt attacks. "
            "No PII filter."
        ),
        "topicPolicyConfig": {
            "topicsConfig": [
                {
                    "name": "investment-picks",
                    "definition": (
                        "Recommending a named security, fund, or crypto asset to buy, "
                        "sell, or hold, including a ticker or an allocation percentage."
                    ),
                    "examples": [
                        "Buy shares of AAPL tomorrow",
                        "Put 40 percent of your portfolio in Bitcoin",
                    ],
                    "type": "DENY",
                },
                {
                    "name": "tax-advice",
                    "definition": (
                        "Telling someone how to file taxes, claim a deduction, "
                        "or reduce their tax bill."
                    ),
                    "examples": [
                        "Claim this subscription as a business expense on your return",
                        "File as head of household so you pay less tax",
                    ],
                    "type": "DENY",
                },
                {
                    "name": "licensed-advice",
                    "definition": (
                        "Claiming to be a licensed advisor, broker, or fiduciary, "
                        "or saying a product is suitable for this person."
                    ),
                    "examples": [
                        "As your advisor this allocation is suitable for you",
                        "I am your fiduciary and you should roll over your 401k",
                    ],
                    "type": "DENY",
                },
            ]
        },
        "contentPolicyConfig": {
            "filtersConfig": [
                _filter("SEXUAL", "NONE", "NONE"),
                _filter("VIOLENCE", "NONE", "NONE"),
                _filter("HATE", "NONE", "NONE"),
                _filter("INSULTS", "NONE", "NONE"),
                _filter("MISCONDUCT", "NONE", "NONE"),
                _filter("PROMPT_ATTACK", attack, "NONE"),
            ]
        },
        # Relevance is not used. On this coach it scored a faithful JSON plan
        # around 0.1 and blocked it. Grounding is the check that separates
        # a restatement of the mart JSON from an off-topic reply.
        "contextualGroundingPolicyConfig": {
            "filtersConfig": [
                {"type": "GROUNDING", "threshold": grounding},
            ]
        },
        "blockedInputMessaging": BLOCKED_INPUT,
        "blockedOutputsMessaging": BLOCKED_OUTPUT,
    }


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    """Set keys in a .env file, keeping every other line."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            found.add(key)
        else:
            out.append(line)
    missing = [k for k in updates if k not in found]
    if missing and out and out[-1].strip():
        out.append("")
    for key in missing:
        out.append(f"{key}={updates[key]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
