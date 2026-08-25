"""Shared terminal navigation with real Streamlit routes and the existing button appearance."""
import streamlit as st

ROUTES = {
    "forex": ("💱 Forex", "pages/forex.py"),
    "gold": ("🥇 Gold", "pages/gold.py"),
    "oil": ("🛢️ Oil", "pages/oil.py"),
    "nasdaq": ("📊 Nasdaq-100", "pages/nasdaq.py"),
    "forecaster": ("🔮 Forecaster", "pages/forecaster.py"),
    "admin": ("👑 MASTER ADMIN", "pages/admin.py"),
}

def render_terminal_nav(active_page: str, auth_user: dict | None = None) -> None:
    is_admin = bool(auth_user and auth_user.get("is_admin"))
    keys = ["forex", "gold", "oil", "nasdaq", "forecaster"]
    if is_admin:
        keys.append("admin")

    cols = st.columns(len(keys))
    for col, key in zip(cols, keys):
        label, path = ROUTES[key]
        with col:
            if st.button(
                label,
                key=f"terminal_nav_{key}",
                use_container_width=True,
                type="primary" if active_page == key else "secondary",
            ):
                # Keep the legacy key synchronized for backward compatibility.
                st.session_state["active_tab"] = label
                st.switch_page(path)

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
