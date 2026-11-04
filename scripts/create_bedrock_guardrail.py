"""
Create or update the FinanceBuddy Bedrock Guardrail and publish a version.

Idempotent: if a guardrail named in settings already exists, its draft is
updated and a new version is published. Writes the id and version into .env
unless --no-write-env is passed.

Requires bedrock:CreateGuardrail, UpdateGuardrail, and CreateGuardrailVersion
on the caller. The Streamlit app user only needs bedrock:ApplyGuardrail.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_settings
from ml.bedrock_guardrail import load_env_defaults, policy_args, upsert_env


def _session():
    import boto3

    load_env_defaults()
    region = os.getenv("AWS_REGION", "us-east-1")
    profile = os.getenv("AWS_PROFILE") or None
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def _find_id(client, name: str) -> str | None:
    token = None
    while True:
        kwargs: dict = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = client.list_guardrails(**kwargs)
        for item in resp.get("guardrails", []):
            if item.get("name") == name:
                return item["id"]
        token = resp.get("nextToken")
        if not token:
            return None


def _latest_numbered_version(client, guardrail_id: str) -> str | None:
    token = None
    numbers: list[int] = []
    while True:
        kwargs: dict = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = client.list_guardrails(**kwargs)
        for item in resp.get("guardrails", []):
            if item.get("id") != guardrail_id:
                continue
            version = str(item.get("version") or "")
            if version.isdigit():
                numbers.append(int(version))
        token = resp.get("nextToken")
        if not token:
            break
    if not numbers:
        return None
    return str(max(numbers))


def ensure_guardrail(settings: dict) -> tuple[str, str]:
    cfg = settings["ml"]["coach"]["guardrail"]
    name = str(cfg.get("name") or "financebuddy-coach")
    client = _session().client("bedrock")
    fields = policy_args(settings)
    existing = _find_id(client, name)
    if existing:
        client.update_guardrail(guardrailIdentifier=existing, name=name, **fields)
        guardrail_id = existing
        print(f"Updated draft {name} ({guardrail_id})")
    else:
        created = client.create_guardrail(name=name, **fields)
        guardrail_id = created["guardrailId"]
        print(f"Created {name} ({guardrail_id})")

    from botocore.exceptions import ClientError

    try:
        published = client.create_guardrail_version(
            guardrailIdentifier=guardrail_id,
            description="FinanceBuddy coach guardrail",
        )
        version = str(published["version"])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        message = str(exc).lower()
        unchanged = code == "ValidationException" and (
            "no change" in message or "conflict" in message
        )
        if not unchanged:
            raise
        version = _latest_numbered_version(client, guardrail_id) or "DRAFT"
        print(f"Draft unchanged; using version {version}")
    return guardrail_id, version


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the FinanceBuddy Bedrock Guardrail")
    parser.add_argument(
        "--no-write-env",
        action="store_true",
        help="Print the id and version without updating .env",
    )
    args = parser.parse_args()
    settings = load_settings()
    guardrail_id, version = ensure_guardrail(settings)
    print(f"FINANCEBUDDY_BEDROCK_GUARDRAIL_ID={guardrail_id}")
    print(f"FINANCEBUDDY_BEDROCK_GUARDRAIL_VERSION={version}")
    if not args.no_write_env:
        upsert_env(
            ROOT / ".env",
            {
                "FINANCEBUDDY_BEDROCK_GUARDRAIL_ID": guardrail_id,
                "FINANCEBUDDY_BEDROCK_GUARDRAIL_VERSION": version,
            },
        )
        print(f"Wrote {ROOT / '.env'}")


if __name__ == "__main__":
    main()
