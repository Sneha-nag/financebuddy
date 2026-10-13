"""FinanceBuddy coach UI — habits, subscriptions, fraud, suggestions."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_settings


st.set_page_config(page_title="FinanceBuddy", page_icon="💰", layout="wide")


@st.cache_resource
def get_con(db_path: str):
    return duckdb.connect(db_path, read_only=True)


def load_records(con, sql: str) -> list[dict]:
    try:
        df = con.execute(sql).pl()
        if df.is_empty():
            return []
        return df.to_dicts()
    except Exception:
        return []


def main() -> None:
    settings = load_settings()
    db_path = settings["paths"]["duckdb"]

    st.title("FinanceBuddy")
    st.caption(
        "Personal finance coach on a synthetic lakehouse — habits, duplicate subscriptions, fraud, and savings ideas."
    )

    if not Path(db_path).exists():
        st.error("DuckDB database not found. Run: `python scripts/run_pipeline.py`")
        st.stop()

    con = get_con(db_path)
    users = load_records(
        con,
        "select user_id, display_name, city, state, persona, monthly_income from marts.dim_user order by user_id",
    )
    if not users:
        st.error("No users found. Run the pipeline first.")
        st.stop()

    sug_counts = load_records(con, "select user_id, count(*) as n from marts.mart_suggestions group by 1")
    default_idx = 0
    if sug_counts:
        top_user = max(sug_counts, key=lambda r: r["n"])["user_id"]
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
    st.sidebar.markdown(
        f"**Persona:** {user['persona']}  \n"
        f"**Location:** {user['city']}, {user['state']}  \n"
        f"**Monthly income:** ${user['monthly_income']:,.0f}"
    )

    suggestions = load_records(
        con,
        f"""
        select suggestion_type, title, reason, estimated_monthly_savings, confidence, priority_score
        from marts.mart_suggestions
        where user_id = '{user_id}'
        order by priority_score desc
        """,
    )
    cashflow = load_records(
        con,
        f"""
        select cast(month_start as varchar) as month_start, category, spend, income
        from marts.mart_monthly_cashflow
        where user_id = '{user_id}'
        """,
    )
    subs = load_records(
        con,
        f"""
        select merchant_name, category, charge_count, avg_amount, avg_gap_days,
               estimated_monthly_cost, is_subscription, price_increased
        from marts.mart_subscriptions
        where user_id = '{user_id}'
        order by estimated_monthly_cost desc
        """,
    )
    dup_subs = load_records(
        con,
        f"select * from marts.mart_duplicate_subscriptions where user_id = '{user_id}'",
    )
    habits = load_records(
        con,
        f"select * from marts.mart_habit_insights where user_id = '{user_id}' order by dollar_impact desc",
    )
    fraud = load_records(
        con,
        f"""
        select cast(transaction_ts as varchar) as transaction_ts, merchant_name, amount_abs,
               fraud_score, reason_codes, is_fraud_predicted
        from marts.mart_fraud_scores
        where user_id = '{user_id}' and is_fraud_predicted = 1
        order by fraud_score desc
        """,
    )

    total_savings = sum(float(s.get("estimated_monthly_savings") or 0) for s in suggestions)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Suggestions", len(suggestions))
    c2.metric("Est. monthly opportunity", f"${total_savings:,.0f}")
    c3.metric("Duplicate subscription groups", len(dup_subs))
    c4.metric("Fraud alerts", len(fraud))

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["Suggestions", "Cashflow", "Subscriptions", "Habits", "Fraud"]
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

    with st.expander("Architecture"):
        st.markdown(
            """
            Local path: **generator → S3-style lake → DuckDB bronze → dbt → ML → gold marts → Streamlit**.

            AWS path: upload lake to **S3**, query with **Athena**, transform with **dbt-athena**,
            train in **SageMaker** using the same `ml/` modules. See `docs/aws_deploy.md`.
            """
        )


if __name__ == "__main__":
    main()