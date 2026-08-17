"""
FX Macro & News Intelligence Desk
----------------------------------
سیستەمی پێشبینیکردن و شیکاری هەواڵی داراییو جیۆپۆلیتیکی.

ڕێکخراوە بە شێوەی: config → style → data-layer → forecast-engine → UI (tabs).

v2: نیشاندەرە زیاتر (GDP, Retail Sales, Interest Rate, Employment) + پێشبینی
    گشتی بۆ هەموو دراوەکان (نەک تەنها USD) + بەشێکی تایبەت بۆ زێڕ (Gold)
    کە پشت بە Real Yield دەبەستێت.
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

# هەر نیشاندەرێک: FRED series id + کاتیگۆری (بۆ لۆجیکی ئاراستە) + کێش
# (کێشی بەرزتر = لە ForexFactory‌دا دەکەوێتە پۆلی "فۆڵدەری سوور" / High Impact)
CURRENCY_SERIES = {
    "USD دۆلار": {
        "CPI":            {"series": "CPIAUCSL", "category": "inflation",  "weight": 1.5},
        "PPI":            {"series": "PPIACO",   "category": "inflation",  "weight": 1.0},
        "NFP":            {"series": "PAYEMS",   "category": "labor_good", "weight": 1.5},
        "Unemployment":   {"series": "UNRATE",   "category": "labor_bad",  "weight": 1.5},
        "GDP":            {"series": "GDP",      "category": "growth",     "weight": 1.3},
        "Retail Sales":   {"series": "RSAFS",    "category": "growth",     "weight": 1.0},
        "Interest Rate":  {"series": "FEDFUNDS", "category": "rate",       "weight": 1.5},
    },
    "GBP پاوەند": {
        "CPI":            {"series": "GBRCPIALLMINMEI",  "category": "inflation",  "weight": 1.5},
        "Production":     {"series": "GBRPROINDMISMEI",  "category": "growth",     "weight": 1.0},
        "Unemployment":   {"series": "LRUN64TTGBM156S",  "category": "labor_bad",  "weight": 1.5},
        "Interest Rate":  {"series": "IRLTLT01GBM156N",  "category": "rate",       "weight": 1.3},
    },
    "CAD کەنەدی": {
        "CPI":            {"series": "CANCPIALLMINMEI", "category": "inflation",  "weight": 1.5},
        "Employment":     {"series": "LFEMTTTTCAM647S", "category": "labor_good", "weight": 1.3},
        "Unemployment":   {"series": "LRUN64TTCAM156S", "category": "labor_bad",  "weight": 1.5},
        "Interest Rate":  {"series": "IRLTLT01CAM156N", "category": "rate",       "weight": 1.3},
    },
    "JPY یەن": {
        "CPI":            {"series": "JPNCPIALLMINMEI", "category": "inflation",  "weight": 1.5},
        "Production":     {"series": "JPNPROINDMISMEI", "category": "growth",     "weight": 1.0},
        "Unemployment":   {"series": "LRUN64TTJPM156S", "category": "labor_bad",  "weight": 1.5},
        "Interest Rate":  {"series": "IRLTLT01JPM156N", "category": "rate",       "weight": 1.3},
    },
}

# سیریاڵی تایبەت بە شیکاری زێڕ: خاوی حکومەتی ١٠ ساڵە و چاوەڕوانی هەڵکشانی نرخ
GOLD_YIELD_SERIES = "DGS10"       # 10-Year Treasury Yield
GOLD_INFLATION_EXP_SERIES = "T10YIE"  # 10-Year Breakeven Inflation Rate

CATEGORY_LABELS = {
    "inflation":  "هەڵکشانی نرخ",
    "labor_good": "بازاڕی کار",
    "labor_bad":  "بێکاری",
    "growth":     "گەشەی ئابووری",
    "rate":       "ڕێژەی سوود",
}

# دوو ڕستە بۆ هەر کاتیگۆری: ئەگەر نیشاندەرەکە بەرزبووەتەوە یان دابەزیوە
INDICATOR_PHRASES = {
    ("inflation", "up"):   "{name} بەرزبووەتەوە، کە فشار بۆ بەرزکردنەوەی ڕێژەی سوود زیاد دەکات و بۆ ماوەیەکی کورت بۆ دراوەکە ئەرێنییە.",
    ("inflation", "down"): "{name} دابەزیوە، کە ئاماژە بە کەمبوونەوەی فشاری نرخ دەکات و بانکی ناوەندی پێویستی بە بەرزکردنەوەی سوود کەمتر دەبێت.",
    ("labor_good", "up"):  "{name} باشتربووە، بازاڕی کار بەهێزە کە پشتگیری بۆ دراوەکە دەکات.",
    ("labor_good", "down"):"{name} لاوازتر بووە، ئاماژە بە سستبوونی بازاڕی کار دەکات.",
    ("labor_bad", "up"):   "{name} زیادی کردووە، کە نیشانەی لاوازبوونی بازاڕی کارە و بۆ دراوەکە نەرێنییە.",
    ("labor_bad", "down"): "{name} کەمبووەتەوە، بازاڕی کار بەهێزتر بووە کە بۆ دراوەکە ئەرێنییە.",
    ("growth", "up"):      "{name} بەرزبووەتەوە کە ئاماژە بە بەهێزبوونی ئابووری دەکات.",
    ("growth", "down"):    "{name} دابەزیوە کە ئاماژە بە سستبوونی ئابووری دەکات.",
    ("rate", "up"):        "{name} بەرزبووەتەوە کە ئاڵوگۆڕی وەبەرهێنان لە دراوەکەدا باشتر دەکات.",
    ("rate", "down"):      "{name} دابەزیوە کە ئاڵوگۆڕی وەبەرهێنان لە دراوەکەدا کەمتر دەکاتەوە.",
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

        .app-eyebrow {
            text-align: center;
            color: #e2b714;
            letter-spacing: 2px;
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 2px;
        }
        .app-title { text-align: center; color: #ffffff; margin-top: 2px; margin-bottom: 22px; }

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

        .badge-lg { font-size: 16px; padding: 8px 18px; border-radius: 10px; font-weight: 800; }

        .reasoning-box {
            background-color: #0f1623;
            border-right: 4px solid #e2b714;
            padding: 14px;
            margin-top: 10px;
            border-radius: 6px;
            font-size: 14px;
            line-height: 2;
        }
        .reasoning-box ul { margin: 0; padding-right: 18px; }
        .reasoning-box li { margin-bottom: 6px; }

        th { color: #e2b714 !important; background-color: #1f293d !important; }
        td { color: #f3f4f6 !important; }

        section[data-testid="stSidebar"] { background-color: #10141f; }
        .footer-note { text-align: center; color: #4b5563; font-size: 12px; margin-top: 40px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown('<div class="app-eyebrow">FX MACRO & GEOPOLITICAL DESK</div>', unsafe_allow_html=True)
    st.markdown('<h2 class="app-title">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h2>', unsafe_allow_html=True)


def bias_from_score(score: float):
    """Return (label, badge_css_class) for a composite score."""
    if score > 0.3:
        return "📈 بەهێز / Bullish", "badge-bullish"
    if score < -0.3:
        return "📉 لاواز / Bearish", "badge-bearish"
    return "⚖️ سەقامگیر / Neutral", "badge-neutral"


def badge_html(score: float, large: bool = False) -> str:
    label, css_class = bias_from_score(score)
    size_class = "badge-lg" if large else ""
    return f'<span class="badge {css_class} {size_class}">{label}</span>'


# ============================================================
# DATA LAYER
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fred_series(series_id: str, key: str, limit: int = 24):
    """Return a DataFrame[date, value] (ascending, last `limit` obs) or None."""
    if not key:
        return None

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": key, "file_type": "json"}

    try:
        res = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        obs = res.json().get("observations", [])
        df = pd.DataFrame(obs)
        if df.empty:
            return None
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        if df.empty:
            return None
        return df[["date", "value"]].tail(limit).reset_index(drop=True)
    except (requests.RequestException, ValueError, KeyError):
        return None


def latest_and_history(df: pd.DataFrame, n: int = 12):
    if df is None or df.empty:
        return None, None
    tail = df.tail(n)
    return tail["value"].tolist(), tail["date"].iloc[-1]


@st.cache_data(ttl=900, show_spinner=False)
def fetch_news(query: str, key: str):
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
# FORECAST ENGINE (generic — works for any currency)
# ============================================================

def compute_currency_composite(currency: str, fred_key: str):
    """
    Fetch every configured indicator for `currency`, score each one,
    and return a dict with the composite score, per-indicator rows,
    and ranked reasoning phrases.
    """
    indicators = CURRENCY_SERIES[currency]
    rows, weighted_scores = [], []

    for name, meta in indicators.items():
        df = fetch_fred_series(meta["series"], fred_key)
        vals, date = latest_and_history(df)
        if not vals:
            continue

        z = calc_z_score(vals)
        # بۆ بێکاری، بەرزبوونەوە خراپە، بۆیە ئاراستەکە دەگۆڕدرێت
        interpreted = -z if meta["category"] == "labor_bad" else z
        direction = "up" if interpreted >= 0 else "down"

        rows.append(
            {
                "name": name,
                "category": meta["category"],
                "value": round(vals[-1], 2),
                "date": date,
                "z": round(interpreted, 2),
                "weight": meta["weight"],
                "direction": direction,
                "phrase": INDICATOR_PHRASES.get((meta["category"], direction), "").format(name=name),
            }
        )
        weighted_scores.append(interpreted * meta["weight"])

    if not rows:
        return None

    total_weight = sum(r["weight"] for r in rows)
    composite = sum(weighted_scores) / total_weight if total_weight else 0.0

    # گرنگترین ٣ نیشاندەر (بەپێی کاریگەری |z * weight|) بۆ لیستی هۆکار
    top_drivers = sorted(rows, key=lambda r: abs(r["z"] * r["weight"]), reverse=True)[:3]

    return {"composite": composite, "rows": rows, "top_drivers": top_drivers}


def render_reasoning_box(top_drivers) -> str:
    items = "".join(f"<li>{d['phrase']}</li>" for d in top_drivers if d["phrase"])
    if not items:
        items = "<li>هیچ ئاراستەیەکی بەهێز لە نیشاندەرەکاندا دیار نییە.</li>"
    return f'<div class="reasoning-box"><ul>{items}</ul></div>'


# ============================================================
# TAB 1 — Macro Strength & Predictive Engine
# ============================================================

def render_macro_tab(fred_key: str) -> None:
    selected_currency = st.radio("دراوەکە هەڵبژێرە:", list(CURRENCY_SERIES.keys()), horizontal=True)

    if not fred_key:
        st.info("🔑 تکایە FRED API Key لە لای ڕاست بنووسە بۆ بارکردنی داتاکان.")
        return

    with st.spinner("کۆتا داتاکان و بەروارەکانیان ڕادەکێشرێن..."):
        result = compute_currency_composite(selected_currency, fred_key)

    if not result:
        st.warning("⚠️ هیچ داتایەک نەدۆزرایەوە. تکایە API Key‌ەکەت بپشکنە.")
        return

    rows = result["rows"]

    # --- Quick-glance indicator cards ---
    st.subheader("📋 کۆتا داتا ڕاستەقینەکان")
    cols = st.columns(len(rows))
    for col, row in zip(cols, rows):
        with col:
            st.markdown(
                f"""
                <div class="indicator-card">
                    <div class="indicator-name">{row['name']}</div>
                    <div class="indicator-value">{row['value']}</div>
                    {badge_html(row['z'])}
                    <div class="indicator-date">{row['date']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with st.expander("📊 خشتەی تەواوی داتاکان"):
        table_df = pd.DataFrame(
            [
                {
                    "نیشاندەر": r["name"],
                    "کاتیگۆری": CATEGORY_LABELS.get(r["category"], r["category"]),
                    "کۆتا داتا": r["value"],
                    "Z-Score": r["z"],
                    "کێش (Impact)": r["weight"],
                    "بەروار": r["date"],
                }
                for r in rows
            ]
        )
        st.dataframe(table_df, use_container_width=True, hide_index=True)

    # --- Composite forecast — now generic for every currency ---
    st.markdown("---")
    st.subheader("🔮 پێشبینیکردنی گشتی دراوەکە (پشتبەستن بە هەموو نیشاندەرە گرنگەکان)")

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("**ئاراستەی گشتی:**")
        st.markdown(badge_html(result["composite"], large=True), unsafe_allow_html=True)
        st.caption(f"Composite score: {round(result['composite'], 2)}")
    with col2:
        st.markdown("**هۆکارە سەرەکییەکان (Logical Reasoning):**")
        st.markdown(render_reasoning_box(result["top_drivers"]), unsafe_allow_html=True)

    st.caption(
        "ℹ️ ئەم پێشبینییە لەسەر بنەمای Z-Score‌ی نیشاندەرە ئابوورییەکانی FRED دروستکراوە "
        "(وەک فۆڵدەری سوور لە ForexFactory) — جێگرەوەی شیکاری تەکنیکی یان بنیاتی نایە."
    )


# ============================================================
# TAB 2 — Live World News
# ============================================================

def render_news_tab(news_key: str) -> None:
    st.subheader("📰 هەواڵە جیهانییە خێراکان بەپێی بەشەکان")

    category = st.radio(
        "بەشی هەواڵەکە هەڵبژێرە:",
        [
            "💣 Geopolitics & War (جەنگ)",
            "🛢️ Energy & Oil (نەوت)",
            "🏛️ Central Banks (بانکی ناوەندی)",
            "🤝 Trade Wars & Tariffs (جەنگی بازرگانی)",
        ],
        horizontal=True,
    )

    keywords = {
        "💣 Geopolitics & War (جەنگ)": "war OR military OR conflict OR sanctions",
        "🛢️ Energy & Oil (نەوت)": "oil OR opec OR crude OR energy crisis",
        "🏛️ Central Banks (بانکی ناوەندی)": "fed OR central bank OR interest rates OR inflation",
        "🤝 Trade Wars & Tariffs (جەنگی بازرگانی)": "tariffs OR trade war OR import tax",
    }

    if not news_key:
        st.warning(
            "🔑 تکایە NewsAPI Keyی خۆڕایی لە لای ڕاست بنووسە بۆ بارکردنی هەواڵە ڕاستەوخۆکان "
            "(دەتوانیت لە newsapi.org وەریبگریت)."
        )
        return

    with st.spinner("هەواڵەکان ڕادەکێشرێن..."):
        articles = fetch_news(keywords[category], news_key)

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
# TAB 3 — Gold (XAUUSD) via Real Yield
# ============================================================

def render_gold_tab(fred_key: str) -> None:
    st.subheader("🥇 شیکاری زێڕ (XAUUSD) — لە ڕێگەی Real Yield و بەهێزی دۆلار")

    if not fred_key:
        st.info("🔑 تکایە FRED API Key لە لای ڕاست بنووسە بۆ بارکردنی شیکاری زێڕ.")
        return

    with st.spinner("داتای خاو و چاوەڕوانی هەڵکشانی نرخ ڕادەکێشرێت..."):
        yield_df = fetch_fred_series(GOLD_YIELD_SERIES, fred_key, limit=30)
        infl_df = fetch_fred_series(GOLD_INFLATION_EXP_SERIES, fred_key, limit=30)
        usd_result = compute_currency_composite("USD دۆلار", fred_key)

    if yield_df is None or infl_df is None:
        st.warning("⚠️ نەتوانرا داتای خاو (DGS10) یان چاوەڕوانی هەڵکشانی نرخ (T10YIE) وەربگیرێت.")
        return

    # ڕێکخستنی هەردوو سیریاڵ بەپێی بەروار، پاشان دانانی Real Yield
    merged = pd.merge(yield_df, infl_df, on="date", suffixes=("_yield", "_infl"))
    if merged.empty or len(merged) < 2:
        st.warning("⚠️ داتای پێویست بۆ ژماردنی Real Yield تەواو نییە.")
        return

    merged["real_yield"] = merged["value_yield"] - merged["value_infl"]
    real_yield_vals = merged["real_yield"].tail(12).tolist()
    latest_date = merged["date"].iloc[-1]

    real_yield_z = calc_z_score(real_yield_vals)
    # بەرزبوونەوەی Real Yield = خراپ بۆ زێڕ (opportunity cost بەرز دەبێتەوە)
    gold_component_yield = -real_yield_z

    usd_component = -usd_result["composite"] if usd_result else 0.0  # دۆلاری بەهێز = خراپ بۆ زێڕ

    # کۆکردنەوەی هەردوو هۆکار: ٦٠٪ Real Yield, ٤٠٪ بەهێزی دۆلار
    gold_score = 0.6 * gold_component_yield + 0.4 * usd_component

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            f"""
            <div class="indicator-card">
                <div class="indicator-name">Real Yield (10Y)</div>
                <div class="indicator-value">{round(real_yield_vals[-1], 2)}%</div>
                <div class="indicator-date">{latest_date}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        usd_composite_display = round(usd_result["composite"], 2) if usd_result else "—"
        st.markdown(
            f"""
            <div class="indicator-card">
                <div class="indicator-name">بەهێزی دۆلار (USD Composite)</div>
                <div class="indicator-value">{usd_composite_display}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f"""
            <div class="indicator-card">
                <div class="indicator-name">ئاراستەی گشتی زێڕ</div>
                <div style="margin-top:6px;">{badge_html(gold_score, large=True)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("**هۆکارە سەرەکییەکان بۆ زێڕ:**")

    reasons = []
    if real_yield_z > 0.3:
        reasons.append("Real Yield بەرزبووەتەوە — واتە هەڵگرتنی زێڕ (کە سوودی نییە) لە بەراورد بە بۆند تێچووی زیاتری هەیە، ئەمە فشار دەخاتە سەر زێڕ.")
    elif real_yield_z < -0.3:
        reasons.append("Real Yield دابەزیوە — واتە تێچووی هەڵگرتنی زێڕ کەمتر بووە، ئەمە پشتگیری لە نرخی زێڕ دەکات.")
    else:
        reasons.append("Real Yield بە جێگیری مایەوە، کاریگەرییەکی ڕوونی لەسەر زێڕ نییە لە ئێستادا.")

    if usd_result:
        if usd_result["composite"] > 0.3:
            reasons.append("دۆلار بەهێز بووە بەپێی نیشاندەرە ئابوورییەکان، کە دەبێتە هۆی گرانتربوونی زێڕ بۆ کڕیارانی دراوەکانی تر و فشار دەخاتە سەری.")
        elif usd_result["composite"] < -0.3:
            reasons.append("دۆلار لاواز بووە بەپێی نیشاندەرە ئابوورییەکان، کە پشتگیری لە نرخی زێڕ دەکات.")
        else:
            reasons.append("دۆلار لە بارودۆخێکی سەقامگیردایە، کاریگەری زۆری نییە لەسەر زێڕ لە ئێستادا.")

    st.markdown(
        '<div class="reasoning-box"><ul>' + "".join(f"<li>{r}</li>" for r in reasons) + "</ul></div>",
        unsafe_allow_html=True,
    )

    st.caption(
        "ℹ️ Real Yield = خاوی حکومەتی ئەمریکای ١٠ ساڵە (DGS10) − چاوەڕوانی هەڵکشانی نرخ (T10YIE). "
        "ئەم مۆدێلە بنەمای زانستی بۆ هەڵسەنگاندنی زێڕە، بەڵام کاریگەری هەواڵی جیۆپۆلیتیکی و "
        "پرسیاری Safe-haven لەخۆناگرێت — بۆ ئەوە سەیری تابی 'Live World News' بکە."
    )


# ============================================================
# TAB 4 — Impact Reference Table
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

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "📊 Macro Strength & Predictive Engine",
            "📰 Live World News & Categories",
            "🥇 Gold (XAUUSD) Analysis",
            "💡 Impact Analysis on Currencies",
        ]
    )

    with tab1:
        render_macro_tab(fred_key)
    with tab2:
        render_news_tab(news_api_key)
    with tab3:
        render_gold_tab(fred_key)
    with tab4:
        render_impact_tab()

    st.markdown(
        '<div class="footer-note">FX Macro & News Intelligence Desk — بۆ مەبەستی شیکاری و فێربوون، '
        "نەک ڕاوێژی دارایی.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
