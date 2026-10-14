"""
Streamlit UI.

Most of the heavy lifting is in app/data_access.py (where data comes from) and
ml/ (how suggestions/fraud/etc. were built). This file is mostly layout + wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import require_password
from app.data_access import get_backend, resolve_backend_name
from config import load_settings


st.set_page_config(page_title="FinanceBuddy", page_icon="💰", layout="wide")


_SECRET_KEYS = (
    "FINANCEBUDDY_BACKEND",
    "FINANCEBUDDY_S3_BUCKET",
    "FINANCEBUDDY_APP_PASSWORD",
    "FINANCEBUDDY_ATHENA_S3_STAGING",
    "FINANCEBUDDY_ATHENA_MARTS_SCHEMA",
    "FINANCEBUDDY_ATHENA_WORKGROUP",
    "FINANCEBUDDY_COACH_ENABLED",
    "FINANCEBUDDY_BEDROCK_MODEL",
    "FINANCEBUDDY_COACH_DAILY_LIMIT",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_PROFILE",
)


def _flatten_secrets(obj, prefix: str = "") -> dict[str, str]:
    """Flatten nested Streamlit secret tables into KEY -> string value."""
    out: dict[str, str] = {}
    if obj is None:
        return out
    try:
        items = dict(obj).items()
    except Exception:
        return out
    for key, val in items:
        name = f"{prefix}{key}"
        if isinstance(val, (dict,)):
            out.update(_flatten_secrets(val, prefix=""))
            continue
        # AttrDict / mapping nested sections
        try:
            nested = dict(val)
            out.update(_flatten_secrets(nested, prefix=""))
            continue
        except Exception:
            pass
        if val is not None and str(val).strip() != "":
            out[str(name)] = str(val).strip()
    return out


def _export_secrets_to_env() -> list[str]:
    # Streamlit Cloud puts config in st.secrets; boto3 / our backends read os.environ.
    import os

    found_keys: list[str] = []
    try:
        secrets = st.secrets  # type: ignore[attr-defined]
    except Exception:
        return found_keys

    flat = _flatten_secrets(secrets)
    found_keys = sorted(flat.keys())

    # Also accept common aliases people type by habit.
    aliases = {
        "S3_BUCKET": "FINANCEBUDDY_S3_BUCKET",
        "BUCKET": "FINANCEBUDDY_S3_BUCKET",
        "BACKEND": "FINANCEBUDDY_BACKEND",
        "AWS_KEY": "AWS_ACCESS_KEY_ID",
        "AWS_SECRET": "AWS_SECRET_ACCESS_KEY",
    }
    for src, dest in aliases.items():
        if src in flat and dest not in flat:
            flat[dest] = flat[src]

    for key in _SECRET_KEYS:
        if key in flat:
            os.environ[key] = flat[key]
    return found_keys


def _secrets_help(found_keys: list[str]) -> None:
    import os

    st.caption(
        "Env check — "
        f"BACKEND={os.getenv('FINANCEBUDDY_BACKEND')!r} | "
        f"BUCKET set: {bool(os.getenv('FINANCEBUDDY_S3_BUCKET'))} | "
        f"AWS key set: {bool(os.getenv('AWS_ACCESS_KEY_ID'))}"
    )
    st.caption(
        "Secret key names Streamlit loaded: "
        + (", ".join(found_keys) if found_keys else "(none — Secrets are empty for this app)")
    )
    st.markdown(
        """
Paste this **exactly** into **Streamlit Cloud → your app → ⋮ → Settings → Secrets**
(not GitHub, not `.env`):

```toml
FINANCEBUDDY_BACKEND = "s3"
FINANCEBUDDY_S3_BUCKET = "financebuddy-sneha"
AWS_REGION = "us-east-1"
AWS_ACCESS_KEY_ID = "AKIAXXXXXXXX"
AWS_SECRET_ACCESS_KEY = "your-secret-here"
FINANCEBUDDY_APP_PASSWORD = "your-demo-password"
```

Then click **Save**, open **⋮ → Reboot app**.  
If the key-name list above stays `(none)`, secrets were not saved on this app.
        """
    )


@st.cache_resource
def cached_backend(backend_name: str):
    # cache_resource => one connection/client for the Streamlit process, not per rerun.
    settings = load_settings()
    return get_backend(settings)


def main() -> None:
    found_keys = _export_secrets_to_env()
    require_password()
    settings = load_settings()

    st.title("FinanceBuddy")
    st.caption(
        "Personal finance coach on a synthetic lakehouse — habits, duplicate subscriptions, fraud, and savings ideas."
    )

    try:
        backend_name = resolve_backend_name(settings)
        backend = cached_backend(backend_name)
    except Exception as exc:
        st.error(str(exc))
        _secrets_help(found_keys)
        st.stop()

    st.sidebar.markdown(f"**Data backend:** `{backend.name}`")
    if backend.name in {"s3", "athena"}:
        st.sidebar.caption(
            "Live AWS path: gold marts on S3"
            + ("; dbt marts via Athena with S3 fallback" if backend.name == "athena" else " (no per-click Athena scans)")
        )

    users = backend.list_users()
    if not users:
        st.error("No users found. Build/upload gold marts first.")
        st.stop()

    sug_counts = backend.suggestion_counts()
    default_idx = 0
    if sug_counts:
        top_user = max(sug_counts, key=lambda r: int(r["n"] or 0))["user_id"]
        for i, u in enumerate(users):
            if u["user_id"] == top_user:
                default_idx = i
                break

    user_ids = [u["user_id"] for u in users]
    user_map = {u["user_id"]: u for u in users}
    user_id = st.sidebar.selectbox(
        "User",
        user_ids,
        index=default_idx,
        format_func=lambda u: f"{u} — {user_map[u]['display_name']}",
    )
    user = user_map[user_id]
    income = float(user["monthly_income"] or 0)
    st.sidebar.markdown(
        f"**Persona:** {user['persona']}  \n"
        f"**Location:** {user['city']}, {user['state']}  \n"
        f"**Monthly income:** ${income:,.0f}"
    )

    suggestions = backend.suggestions(user_id)
    cashflow = backend.cashflow(user_id)
    subs = backend.subscriptions(user_id)
    dup_subs = backend.duplicate_subscriptions(user_id)
    habits = backend.habits(user_id)
    fraud = backend.fraud_alerts(user_id)

    total_savings = sum(float(s.get("estimated_monthly_savings") or 0) for s in suggestions)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Suggestions", len(suggestions))
    c2.metric("Est. monthly opportunity", f"${total_savings:,.0f}")
    c3.metric("Duplicate subscription groups", len(dup_subs))
    c4.metric("Fraud alerts", len(fraud))

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["Suggestions", "Cashflow", "Subscriptions", "Habits", "Fraud", "AI Coach"]
    )

    with tab1:
        if not suggestions:
            st.info("No suggestions for this user.")
        else:
            st.dataframe(suggestions, use_container_width=True, hide_index=True)

    with tab2:
        if not cashflow:
            st.info("No cashflow data.")
        else:
            spend_by_month: dict[str, float] = {}
            for row in cashflow:
                spend_by_month[row["month_start"]] = spend_by_month.get(row["month_start"], 0.0) + float(
                    row["spend"] or 0
                )
            months = sorted(spend_by_month)
            fig = px.bar(x=months, y=[spend_by_month[m] for m in months], labels={"x": "month", "y": "spend"})
            fig.update_layout(title="Monthly total spend")
            st.plotly_chart(fig, use_container_width=True)

            latest = months[-1]
            pie_rows = [r for r in cashflow if r["month_start"] == latest and float(r["spend"] or 0) > 0]
            if pie_rows:
                fig2 = px.pie(
                    names=[r["category"] for r in pie_rows],
                    values=[float(r["spend"]) for r in pie_rows],
                    title=f"Spend mix — {latest}",
                )
                st.plotly_chart(fig2, use_container_width=True)

    with tab3:
        if dup_subs:
            st.subheader("Overlapping services")
            st.dataframe(dup_subs, use_container_width=True, hide_index=True)
        st.subheader("Detected recurring charges")
        st.dataframe(subs, use_container_width=True, hide_index=True)

    with tab4:
        if not habits:
            st.info("No habit insights for this user.")
        else:
            st.dataframe(habits, use_container_width=True, hide_index=True)

    with tab5:
        if not fraud:
            st.success("No high-risk fraud flags for this user.")
        else:
            st.dataframe(fraud, use_container_width=True, hide_index=True)

    with tab6:
        from ml.coach import build_coach_context, coach_enabled, run_coach

        st.markdown(
            "Grounded **Bedrock Claude** plan over this user's marts "
            "(does not invent numbers). See `docs/coach.md`."
        )
        if not coach_enabled():
            st.info(
                "Coach is off. Set `FINANCEBUDDY_COACH_ENABLED=1`, enable Claude Haiku 4.5 in "
                "Bedrock model access, and grant `bedrock:InvokeModel` / Converse."
            )
        else:
            coach_cfg = settings.get("ml", {}).get("coach", {})
            daily_limit = int(
                __import__("os").getenv(
                    "FINANCEBUDDY_COACH_DAILY_LIMIT",
                    coach_cfg.get("daily_limit", 30),
                )
            )
            if "coach_cache" not in st.session_state:
                st.session_state["coach_cache"] = {}

            if st.button("Generate plan", type="primary"):
                context = build_coach_context(
                    user=user,
                    suggestions=suggestions,
                    habits=habits,
                    fraud=fraud,
                    dup_subs=dup_subs,
                    cashflow=cashflow,
                )
                with st.expander("Context sent to model (capped JSON)", expanded=False):
                    st.json(context)
                try:
                    with st.spinner("Calling Bedrock..."):
                        plan, meta = run_coach(
                            context,
                            artifacts_dir=settings["paths"]["ml_artifacts"],
                            daily_limit=daily_limit,
                            cache=st.session_state["coach_cache"],
                        )
                    st.session_state["coach_last"] = {"plan": plan, "meta": meta, "user_id": user_id}
                except Exception as exc:
                    st.error(str(exc))

            last = st.session_state.get("coach_last")
            if last and last.get("user_id") == user_id:
                plan = last["plan"]
                meta = last["meta"]
                st.subheader("Summary")
                st.write(plan.get("summary", ""))
                if plan.get("actions"):
                    st.subheader("Actions")
                    st.dataframe(plan["actions"], use_container_width=True, hide_index=True)
                if plan.get("risks"):
                    st.subheader("Risks")
                    st.dataframe(plan["risks"], use_container_width=True, hide_index=True)
                if plan.get("disclaimers"):
                    for d in plan["disclaimers"]:
                        st.caption(d)
                st.caption(
                    f"model={meta.get('model_id')} | "
                    f"tokens in/out={meta.get('input_tokens')}/{meta.get('output_tokens')} | "
                    f"cache_hit={meta.get('cache_hit')} | "
                    f"warnings={plan.get('harness_warnings')}"
                )

    with st.expander("Architecture"):
        st.markdown(
            f"""
            **Active backend:** `{backend.name}`

            - **duckdb** — local demo (`python scripts/run_pipeline.py`)
            - **s3** — portfolio live path: Streamlit reads **gold Parquet on S3** (cheap, cached)
            - **athena** — Streamlit queries **Glue/Athena** dbt marts; ML tables use S3 gold fallback
            - **AI Coach** — optional Bedrock Claude Haiku over capped mart JSON (`docs/coach.md`)

            Pipeline: generator → lake → **S3** → **dbt on Athena** → ML gold → this UI.

            Cost guardrails: password auth, in-process cache, user-scoped queries, LIMITs,
            prefer S3 for large fraud scores, coach daily call budget + response cache.
            See `docs/live_demo.md`.
            """
        )


if __name__ == "__main__":
    main()
