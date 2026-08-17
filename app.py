"""
FX Macro & News Intelligence Desk
----------------------------------
سیستەمی پێشبینیکردن و شیکاری هەواڵی داراییو جیۆپۆلیتیکی.

ڕێکخراوە بە شێوەی: config → helpers → data-layer → UI (tabs).
"""

import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

st.set_page_config(
    page_title="FX Macro & News Intelligence Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

REQUEST_TIMEOUT = 10  # seconds, so a slow API never freezes the app

CURRENCY_SERIES = {
    "USD دۆلار": {"CPI": "CPIAUCSL", "NFP": "PAYEMS", "PPI": "PPIACO", "Unemployment": "UNRATE"},
    "GBP پاوەند": {"CPI": "GBRCPIALLMINMEI", "Production": "GBRPROINDMISMEI", "Unemployment": "LRUN64TTGBM156S"},
    "CAD کەنەدی": {"CPI": "CANCPIALLMINMEI", "Employment": "LFEMTTTTCAM647S", "Unemployment": "LRUN64TTCAM156S"},
    "JPY یەن": {"CPI": "JPNCPIALLMINMEI", "Production": "JPNPROINDMISMEI", "Unemployment": "LRUN64TTJPM156S"},
}

NEWS_CATEGORIES = {
    "💣 Geopolitics & War (جەنگ)": "war OR military OR conflict OR sanctions",
    "🛢️ Energy & Oil (نەوت)": "oil OR opec OR crude OR energy crisis",
    "🏛️ Central Banks (بانکی ناوەندی)": "fed OR central bank OR interest rates OR inflation",
    "🤝 Trade Wars & Tariffs (جەنگی بازرگانی)": "tariffs OR trade war OR import tax",
}

IMPACT_TABLE_MD = """
| ڕووداوی جیهانی (Event) | دراوە بەهێزەکان (Bullish) | دراوە لاوازەکان (Bearish) | هۆکارەکە |
| :--- | :--- | :--- | :--- |
| **هەڵگیرسانی جەنگ یان ئاڵۆزی سەربازی** | **USD, CHF, Gold** | **EUR, AUD** | ڕاکردنی سەرمایە بۆ ناو دراوە ئەمنەکان (Safe-havens). |
| **بەرزبوونەوەی بەرچاوی نرخی نەوت** | **CAD, NOK** | **JPY, EUR** | کەنەدا و نەرویج نەوت دەنێرنە دەرەوە؛ ژاپۆن و ئەوروپا هاوردەی دەکەن. |
| **بەرزکردنەوەی ڕێژەی سوود (Rate Hikes)** | **دراوەکەی خۆی (واتە USD/GBP)** | **زێڕ (Gold)** | ڕاکێشانی وەبەرهێنەران بۆ بەدەستهێنانی سوودی بەرزتر. |
| **جەنگی بازرگانی و باجی گومرگی** | **USD** | **AUD, NZD, CNH** | لاوازبوونی بازرگانی چین بە شێوەیەکی ڕاستەوخۆ دۆلاری ئوسترالی دادەبەزێنێت. |
"""

# ============================================================
# STYLE
# ============================================================

def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp { background-color: #0b0f19; color: #ffffff; }

        /* ---- Tabs ---- */
        .stTabs [data-baseweb="tab-list"] { gap: 8px; }
        .stTabs [data-baseweb="tab"] {
            background-color: #151c2c;
            border-radius: 8px;
            color: #9ca3af;
            padding: 10px 20px;
            font-weight: 600;
            transition: all 0.15s ease-in-out;
        }
        .stTabs [data-baseweb="tab"]:hover { color: #e2b714; }
        .stTabs [aria-selected="true"] {
            background-color: #e2b714 !important;
            color: #000000 !important;
        }

        /* ---- Header ---- */
        .app-eyebrow {
            text-align: center;
            color: #e2b714;
            letter-spacing: 2px;
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 2px;
        }
        .app-title {
            text-align: center;
            color: #ffffff;
            margin-top: 2px;
            margin-bottom: 22px;
        }

        /* ---- Cards ---- */
        .metric-card {
            background-color: #151c2c;
            border: 1px solid #2a3447;
            border-radius: 12px;
            padding: 16px 18px;
            margin-bottom: 14px;
            transition: border-color 0.15s ease-in-out;
        }
        .metric-card:hover { border-color: #e2b714; }

        .indicator-card {
            background-color: #151c2c;
            border: 1px solid #2a3447;
            border-radius: 12px;
            padding: 14px 16px;
            text-align: center;
        }
        .indicator-name { color: #9ca3af; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
        .indicator-value { color: #ffffff; font-size: 22px; font-weight: 700; }
        .indicator-date { color: #6b7280; font-size: 11px; margin-top: 4px; }

        .badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            margin-top: 6px;
        }
        .badge-bullish { background-color: rgba(16,185,129,0.15); color: #10b981; }
        .badge-bearish { background-color: rgba(239,68,68,0.15); color: #ef4444; }
        .badge-neutral { background-color: rgba(156,163,175,0.15); color: #9ca3af; }

        .reasoning-box {
            background-color: #0f1623;
            border-right: 4px solid #e2b714;
            padding: 14px;
            margin-top: 10px;
            border-radius: 6px;
            font-size: 14px;
            line-height: 1.9;
        }

        /* ---- Tables ---- */
        th { color: #e2b714 !important; background-color: #1f293d !important; }
        td { color: #f3f4f6 !important; }

        /* ---- Misc ---- */
        section[data-testid="stSidebar"] { background-color: #10141f; }
        .footer-note { text-align: center; color: #4b5563; font-size: 12px; margin-top: 40px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown('<div class="app-eyebrow">FX MACRO & GEOPOLITICAL DESK</div>', unsafe_allow_html=True)
    st.markdown(
        '<h2 class="app-title">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h2>',
        unsafe_allow_html=True,
    )


def badge_html(score: float) -> str:
    if score > 0.3:
        return '<span class="badge badge-bullish">📈 Bullish</span>'
    if score < -0.3:
        return '<span class="badge badge-bearish">📉 Bearish</span>'
    return '<span class="badge badge-neutral">⚖️ Neutral</span>'


# ============================================================
# DATA LAYER
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fred_data_with_dates(series_id: str, key: str):
    """Return (last-12-values, latest_date) for a FRED series, or (None, None) on failure."""
    if not key:
        return None, None

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": key, "file_type": "json"}

    try:
        res = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        obs = res.json().get("observations", [])
        df = pd.DataFrame(obs)
        if df.empty:
            return None, None
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        if df.empty:
            return None, None
        return df["value"].tail(12).tolist(), df["date"].iloc[-1]
    except (requests.RequestException, ValueError, KeyError):
        return None, None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_news(query: str, key: str):
    """Return a list of article dicts for a NewsAPI query, or None on failure."""
    if not key:
        return None

    url = "https://newsapi.org/v2/everything"
    params = {"q": query, "sortBy": "publishedAt", "apiKey": key, "pageSize": 5, "language": "en"}

    try:
        res = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        return res.json().get("articles", [])[:5]
    except requests.RequestException:
        return None


def calc_z_score(vals) -> float:
    if not vals or len(vals) < 2:
        return 0.0
    std = np.std(vals)
    return (vals[-1] - np.mean(vals)) / std if std != 0 else 0.0


# ============================================================
# TAB 1 — Macro Strength & Predictive Engine
# ============================================================

def render_macro_tab(fred_key: str) -> None:
    selected_currency = st.radio(
        "دراوەکە هەڵبژێرە:",
        list(CURRENCY_SERIES.keys()),
        horizontal=True,
    )

    if not fred_key:
        st.info("🔑 تکایە FRED API Key لە لای ڕاست بنووسە بۆ بارکردنی داتاکان.")
        return

    indicators = CURRENCY_SERIES[selected_currency]
    results, raw_values = [], {}

    with st.spinner("کۆتا داتاکان و بەروارەکانیان ڕادەکێشرێن..."):
        for name, series_id in indicators.items():
            vals, date = fetch_fred_data_with_dates(series_id, fred_key)
            if vals:
                z = calc_z_score(vals)
                score = -z if "Unemployment" in name else z
                raw_values[name] = vals
                results.append(
                    {
                        "نیشاندەر": name,
                        "کۆتا داتا": round(vals[-1], 2),
                        "Z-Score": round(score, 2),
                        "کۆتا بەرواری دەرچوون": date,
                    }
                )

    if not results:
        st.warning("⚠️ هیچ داتایەک نەدۆزرایەوە. تکایە API Key‌ەکەت بپشکنە.")
        return

    # --- Quick-glance indicator cards ---
    st.subheader("📋 کۆتا داتا ڕاستەقینەکان")
    cols = st.columns(len(results))
    for col, row in zip(cols, results):
        with col:
            st.markdown(
                f"""
                <div class="indicator-card">
                    <div class="indicator-name">{row['نیشاندەر']}</div>
                    <div class="indicator-value">{row['کۆتا داتا']}</div>
                    {badge_html(row['Z-Score'])}
                    <div class="indicator-date">{row['کۆتا بەرواری دەرچوون']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with st.expander("📊 خشتەی تەواوی داتاکان"):
        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)

    # --- Predictive reasoning (USD only, as in the original logic) ---
    st.markdown("---")
    st.subheader("🔮 پێشبینیکردنی هەواڵی داهاتوو (پشتبەستن بە Forecast)")

    if selected_currency == "USD دۆلار" and "CPI" in raw_values and "PPI" in raw_values:
        ppi_trend = raw_values["PPI"][-1] - raw_values["PPI"][-2]
        unemp_trend = (
            raw_values["Unemployment"][-1] - raw_values["Unemployment"][-2]
            if "Unemployment" in raw_values
            else 0
        )

        if ppi_trend > 0 and unemp_trend <= 0:
            cpi_pred = "📈 بەرزبوونەوەی CPI (هەڵکشان)"
            reason = (
                "چونکە شاخصی PPI (تێچووی بەرهەمهێنان) بەرزبووەتەوە و بێکاری کەمیکردووە. "
                "تێچووی بەرزی دروستکردنی کەلوپەل بە شێوەیەکی ڕاستەوخۆ دەگوازرێتەوە بۆ بەکارهێنەر، "
                "کە دەبێتە هۆی بەرزبوونەوەی CPIی مانگی داهاتوو."
            )
        elif ppi_trend < 0 and unemp_trend > 0:
            cpi_pred = "📉 دابەزینی CPI (سستبوون)"
            reason = (
                "چونکە PPI دابەزیوە و ڕێژەی بێکاری زیادی کردووە. ئەمەش داواکاریی بازار کەم دەکاتەوە "
                "و فشار لەسەر بەرزبوونەوەی نرخەکان کەم دەکاتەوە."
            )
        else:
            cpi_pred = "⚖️ سەقامگیر / Neutral"
            reason = "داتاکانی PPI و بێکاری ئاراستەیەکی دژبەیەکیان نیشانداوە، بۆیە CPI بە جێگیری دەمێنێتەوە."

        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown("**پێشبینی بۆ CPIی داهاتوو:**")
            st.info(cpi_pred)
        with col2:
            st.markdown("**هۆکاری پێشبینییەکە (Logical Reasoning):**")
            st.markdown(f'<div class="reasoning-box">{reason}</div>', unsafe_allow_html=True)
    else:
        st.caption("ℹ️ پێشبینی تەنها بۆ USD چالاک دەبێت، کاتێک هەردوو داتای CPI و PPI بارببنەوە.")


# ============================================================
# TAB 2 — Live World News
# ============================================================

def render_news_tab(news_key: str) -> None:
    st.subheader("📰 هەواڵە جیهانییە خێراکان بەپێی بەشەکان")

    category = st.radio(
        "بەشی هەواڵەکە هەڵبژێرە:",
        list(NEWS_CATEGORIES.keys()),
        horizontal=True,
    )

    if not news_key:
        st.warning(
            "🔑 تکایە NewsAPI Keyی خۆڕایی لە لای ڕاست بنووسە بۆ بارکردنی هەواڵە ڕاستەوخۆکان "
            "(دەتوانیت لە newsapi.org وەریبگریت)."
        )
        return

    with st.spinner("هەواڵەکان ڕادەکێشرێن..."):
        articles = fetch_news(NEWS_CATEGORIES[category], news_key)

    if articles is None:
        st.error("⚠️ پەیوەندی لەگەڵ NewsAPI ڕوونەگرت. تکایە API Key‌ەکەت بپشکنە.")
        return
    if not articles:
        st.info("هیچ هەواڵێکی نوێ لەم بەشەدا نەدۆزرایەوە.")
        return

    for art in articles:
        title = art.get("title") or "—"
        source = (art.get("source") or {}).get("name", "نەزانراو")
        published = (art.get("publishedAt") or "")[:10]
        description = art.get("description") or ""
        link = art.get("url") or "#"

        st.markdown(
            f"""
            <div class="metric-card">
                <h4 style="color:#e2b714; margin:0 0 6px 0;">{title}</h4>
                <p style="font-size:12px; color:#9ca3af; margin:0 0 8px 0;">
                    سەرچاوە: {source} &nbsp;|&nbsp; بەروار: {published}
                </p>
                <p style="font-size:14px; margin:0 0 8px 0;">{description}</p>
                <a href="{link}" target="_blank" style="color:#10b981;">خوێندنەوەی زیاتر ↗</a>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# TAB 3 — Impact Reference Table
# ============================================================

def render_impact_tab() -> None:
    st.subheader("💡 شیکاری کاریگەری رووداوە جیهانییەکان لەسەر دراوەکان")
    st.markdown(IMPACT_TABLE_MD)


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    inject_css()
    render_header()

    with st.sidebar:
        st.markdown("### 🔐 ڕێکخستنی API")
        fred_key = st.text_input("FRED API Key (ئۆتۆماتیکی):", type="password")
        news_api_key = st.text_input("NewsAPI Key (بۆ هەواڵەکان):", type="password")
        st.caption(f"🕓 دوایین نوێکردنەوە: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    tab1, tab2, tab3 = st.tabs(
        [
            "📊 Macro Strength & Predictive Engine",
            "📰 Live World News & Categories",
            "💡 Impact Analysis on Currencies",
        ]
    )

    with tab1:
        render_macro_tab(fred_key)
    with tab2:
        render_news_tab(news_api_key)
    with tab3:
        render_impact_tab()

    st.markdown(
        '<div class="footer-note">FX Macro & News Intelligence Desk — بۆ مەبەستی شیکاری و فێربوون، '
        "نەک ڕاوێژی دارایی.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
