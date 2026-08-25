import streamlit as st

# Central page configuration: called once by the Streamlit Cloud entry point.
st.set_page_config(
    page_title="ApexMacro — Global Intelligence Desk",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from apex.bootstrap import initialize_background_services

initialize_background_services()

pages = [
    st.Page("pages/home.py", title="Home", url_path="", default=True),
    st.Page("pages/login.py", title="Login", url_path="login"),
    st.Page("pages/vip.py", title="VIP", url_path="vip"),
    st.Page("pages/dashboard.py", title="Dashboard", url_path="dashboard"),
    st.Page("pages/forex.py", title="Forex", url_path="forex"),
    st.Page("pages/gold.py", title="Gold", url_path="gold"),
    st.Page("pages/oil.py", title="Oil", url_path="oil"),
    st.Page("pages/nasdaq.py", title="Nasdaq", url_path="nasdaq"),
    st.Page("pages/forecaster.py", title="Forecaster", url_path="forecaster"),
    st.Page("pages/admin.py", title="Admin", url_path="admin"),
]

# Hidden position keeps the approved custom ApexMacro navigation as the only visible navigation.
navigation = st.navigation(pages, position="hidden")
navigation.run()
