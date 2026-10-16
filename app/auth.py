"""Optional password gate for a hosted demo. Local runs usually skip this."""

from __future__ import annotations

import os

import streamlit as st


# Shown next to the password box on the public demo so recruiters can try it.
# Must match FINANCEBUDDY_APP_PASSWORD in Streamlit secrets.
DEMO_PASSWORD_HINT = "D3MOTRY@OUT"


def _expected_password() -> str | None:
    try:
        secret = st.secrets.get("FINANCEBUDDY_APP_PASSWORD")  # type: ignore[attr-defined]
        if secret:
            return str(secret)
    except Exception:
        pass
    return os.getenv("FINANCEBUDDY_APP_PASSWORD") or None


def _password_hint() -> str:
    try:
        hint = st.secrets.get("FINANCEBUDDY_APP_PASSWORD_HINT")  # type: ignore[attr-defined]
        if hint:
            return str(hint)
    except Exception:
        pass
    return os.getenv("FINANCEBUDDY_APP_PASSWORD_HINT") or DEMO_PASSWORD_HINT


def require_password() -> bool:
    """No password configured => open. Configured => block until it matches."""
    expected = _expected_password()
    if not expected:
        return True

    if st.session_state.get("fb_auth_ok"):
        return True

    st.title("FinanceBuddy")
    st.caption("Synthetic personal-finance demo — enter the password to continue.")

    col_box, col_hint = st.columns([2, 1])
    with col_box:
        # Plain text box (not masked) so the demo password is easy to type/paste.
        pwd = st.text_input("Password", value="", autocomplete="off")
    with col_hint:
        st.markdown("**Demo password**")
        st.code(_password_hint(), language=None)

    if st.button("Unlock", type="primary"):
        if pwd == expected:
            st.session_state["fb_auth_ok"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
    return False
