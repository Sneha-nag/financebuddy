"""Optional password gate for a hosted demo. Local runs usually skip this."""

from __future__ import annotations

import os

import streamlit as st


def _expected_password() -> str | None:
    try:
        secret = st.secrets.get("FINANCEBUDDY_APP_PASSWORD")  # type: ignore[attr-defined]
        if secret:
            return str(secret)
    except Exception:
        pass
    return os.getenv("FINANCEBUDDY_APP_PASSWORD") or None


def require_password() -> bool:
    """No password configured => open. Configured => block until it matches."""
    expected = _expected_password()
    if not expected:
        return True

    if st.session_state.get("fb_auth_ok"):
        return True

    st.title("FinanceBuddy")
    st.caption("Enter the demo password to continue.")
    pwd = st.text_input("Password", type="password")
    if st.button("Unlock", type="primary"):
        if pwd == expected:
            st.session_state["fb_auth_ok"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
    return False
