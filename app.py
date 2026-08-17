"""
FX Macro & News Intelligence Desk
----------------------------------
سیستەمی پێشبینیکردن و شیکاری هەواڵی داراییو جیۆپۆلیتیکی.

v3: زیادکردنی نیشاندەرە m/m (Core CPI, Core PPI, Core PCE, PCE...) +
    تابی نوێی Monthly Calendar & Outlook کە هەواڵی بڵاوبووەوە و
    پێشبینیی داهاتو لەم مانگەدا نیشان دەدات.
"""

import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, date
import calendar

# ============================================================
# CONFIG
# ============================================================

st.set_page_config(
    page_title="FX Macro & News Intelligence Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

REQUEST_TIMEOUT = 10

# ----------------------------------------------------------------
# CURRENCY_SERIES — v3: زیادکردنی m/m ی گرنگ بۆ هەموو دراوەکان
# category_type: "level"  = ئاستی نیشاندەر (واتا z-score بەپێی ئاست)
#                "mom"    = m/m (گۆڕانی ئەم مانگ لەگەڵ مانگی پێش خۆی)
# ----------------------------------------------------------------
CURRENCY_SERIES = {
    "USD دۆلار": {
        # --- نیشاندەرە گشتی‌یەکان ---
        "CPI (Headline) m/m":  {"series": "CPIAUCSL",  "category": "inflation",  "weight": 1.5, "type": "mom"},
        "Core CPI m/m":        {"series": "CPILFESL",  "category": "inflation",  "weight": 2.0, "type": "mom"},
        "PPI m/m":             {"series": "PPIACO",    "category": "inflation",  "weight": 1.0, "type": "mom"},
        "Core PPI m/m":        {"series": "WPSFD4131", "category": "inflation",  "weight": 1.2, "type": "mom"},
        "PCE Price Index m/m": {"series": "PCEPI",     "category": "inflation",  "weight": 1.3, "type": "mom"},
        "Core PCE m/m":        {"series": "PCEPILFE",  "category": "inflation",  "weight": 2.0, "type": "mom"},
        "NFP (Non-Farm)":      {"series": "PAYEMS",    "category": "labor_good", "weight": 1.5, "type": "mom"},
        "Unemployment Rate":   {"series": "UNRATE",    "category": "labor_bad",  "weight": 1.5, "type": "level"},
        "GDP":                 {"series": "GDP",        "category": "growth",     "weight": 1.3, "type": "level"},
        "Retail Sales m/m":    {"series": "RSAFS",     "category": "growth",     "weight": 1.0, "type": "mom"},
        "Interest Rate":       {"series": "FEDFUNDS",  "category": "rate",       "weight": 1.5, "type": "level"},
    },
    "GBP پاوەند": {
        "CPI m/m":             {"series": "GBRCPIALLMINMEI",  "category": "inflation",  "weight": 1.5, "type": "mom"},
        "Core CPI m/m":        {"series": "GBRCP01IXOBSAM",  "category": "inflation",  "weight": 1.8, "type": "mom"},
        "Production m/m":      {"series": "GBRPROINDMISMEI",  "category": "growth",     "weight": 1.0, "type": "mom"},
        "Unemployment Rate":   {"series": "LRUN64TTGBM156S",  "category": "labor_bad",  "weight": 1.5, "type": "level"},
        "Interest Rate":       {"series": "IRLTLT01GBM156N",  "category": "rate",       "weight": 1.3, "type": "level"},
    },
    "CAD کەنەدی": {
        "CPI m/m":             {"series": "CANCPIALLMINMEI", "category": "inflation",  "weight": 1.5, "type": "mom"},
        "Core CPI m/m":        {"series": "CANCP01IXOBSAM",  "category": "inflation",  "weight": 1.8, "type": "mom"},
        "Employment m/m":      {"series": "LFEMTTTTCAM647S", "category": "labor_good", "weight": 1.3, "type": "mom"},
        "Unemployment Rate":   {"series": "LRUN64TTCAM156S", "category": "labor_bad",  "weight": 1.5, "type": "level"},
        "Interest Rate":       {"series": "IRLTLT01CAM156N", "category": "rate",       "weight": 1.3, "type": "level"},
    },
    "JPY یەن": {
        "CPI m/m":             {"series": "JPNCPIALLMINMEI", "category": "inflation",  "weight": 1.5, "type": "mom"},
        "Core CPI m/m":        {"series": "JPNCP01IXOBSAM",  "category": "inflation",  "weight": 1.8, "type": "mom"},
        "Production m/m":      {"series": "JPNPROINDMISMEI", "category": "growth",     "weight": 1.0, "type": "mom"},
        "Unemployment Rate":   {"series": "LRUN64TTJPM156S", "category": "labor_bad",  "weight": 1.5, "type": "level"},
        "Interest Rate":       {"series": "IRLTLT01JPM156N", "category": "rate",       "weight": 1.3, "type": "level"},
    },
}

GOLD_YIELD_SERIES = "DGS10"
GOLD_INFLATION_EXP_SERIES = "T10YIE"

CATEGORY_LABELS = {
    "inflation":  "هەڵکشانی نرخ",
    "labor_good": "بازاڕی کار",
    "labor_bad":  "بێکاری",
    "growth":     "گەشەی ئابووری",
    "rate":       "ڕێژەی سوود",
}

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

# ----------------------------------------------------------------
# MONTHLY_CALENDAR v3 — بەروار + ئایا کوارتەرلی یان مانگانەیە
# "day": ڕۆژی تیپیکی بڵاوکردنەوە لەم مانگەدا
# "quarterly": True = هەر ٣ مانگ یەک جار
# ----------------------------------------------------------------
MONTHLY_CALENDAR = {
    "USD دۆلار": [
        {"name": "NFP (Non-Farm)",      "day": 4,  "hint": "یەکەم هەینی مانگ", "impact": "high", "quarterly": False},
        {"name": "Unemployment Rate",   "day": 4,  "hint": "هاوکات لەگەڵ NFP", "impact": "high", "quarterly": False},
        {"name": "Core CPI m/m",        "day": 11, "hint": "نزیکەی ڕۆژی ١٠-١٣", "impact": "high", "quarterly": False},
        {"name": "CPI (Headline) m/m",  "day": 11, "hint": "هاوکات لەگەڵ Core CPI", "impact": "high", "quarterly": False},
        {"name": "Core PPI m/m",        "day": 13, "hint": "ڕۆژێک-دوو دوای CPI", "impact": "medium", "quarterly": False},
        {"name": "PPI m/m",             "day": 13, "hint": "هاوکات لەگەڵ Core PPI", "impact": "medium", "quarterly": False},
        {"name": "Retail Sales m/m",    "day": 15, "hint": "نزیکەی ڕۆژی ١٥-١٧", "impact": "high", "quarterly": False},
        {"name": "Core PCE m/m",        "day": 25, "hint": "نزیکەی کۆتایی مانگ", "impact": "high", "quarterly": False},
        {"name": "PCE Price Index m/m", "day": 25, "hint": "هاوکات لەگەڵ Core PCE", "impact": "medium", "quarterly": False},
        {"name": "Interest Rate",       "day": 18, "hint": "FOMC — ٨ جار لە ساڵ", "impact": "high", "quarterly": False},
        {"name": "GDP",                 "day": 28, "hint": "کوارتەرلی — هەر ٣ مانگ", "impact": "high", "quarterly": True},
    ],
    "GBP پاوەند": [
        {"name": "CPI m/m",            "day": 17, "hint": "نزیکەی ڕۆژی ١٥-٢٠ (ONS)", "impact": "high", "quarterly": False},
        {"name": "Core CPI m/m",       "day": 17, "hint": "هاوکات لەگەڵ CPI", "impact": "high", "quarterly": False},
        {"name": "Unemployment Rate",  "day": 11, "hint": "نزیکەی ڕۆژی ١٠-١٤", "impact": "high", "quarterly": False},
        {"name": "Production m/m",     "day": 11, "hint": "هاوکات لەگەڵ بازرگانی", "impact": "medium", "quarterly": False},
        {"name": "Interest Rate",      "day": 19, "hint": "BoE — نزیکەی ٨ جار لە ساڵ", "impact": "high", "quarterly": False},
    ],
    "CAD کەنەدی": [
        {"name": "CPI m/m",            "day": 17, "hint": "نزیکەی ڕۆژی ١٥-٢٠ (StatCan)", "impact": "high", "quarterly": False},
        {"name": "Core CPI m/m",       "day": 17, "hint": "هاوکات لەگەڵ CPI", "impact": "high", "quarterly": False},
        {"name": "Employment m/m",     "day": 4,  "hint": "یەکەم هەینی مانگ", "impact": "high", "quarterly": False},
        {"name": "Unemployment Rate",  "day": 4,  "hint": "هاوکات لەگەڵ داتای دامەزراندن", "impact": "high", "quarterly": False},
        {"name": "Interest Rate",      "day": 14, "hint": "BoC — نزیکەی ٨ جار لە ساڵ", "impact": "high", "quarterly": False},
    ],
    "JPY یەن": [
        {"name": "CPI m/m",            "day": 19, "hint": "نزیکەی ڕۆژی ١٩-٢٣", "impact": "high", "quarterly": False},
        {"name": "Core CPI m/m",       "day": 19, "hint": "هاوکات لەگەڵ CPI", "impact": "high", "quarterly": False},
        {"name": "Production m/m",     "day": 14, "hint": "نزیکەی ڕۆژی ١٤-١٦", "impact": "medium", "quarterly": False},
        {"name": "Unemployment Rate",  "day": 27, "hint": "نزیکەی کۆتایی مانگ", "impact": "medium", "quarterly": False},
        {"name": "Interest Rate",      "day": 18, "hint": "BoJ — نزیکەی ٨ جار لە ساڵ", "impact": "high", "quarterly": False},
    ],
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
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        * { font-family: 'Inter', sans-serif; }
        .stApp { background-color: #080c14; color: #e5e7eb; }

        .stTabs [data-baseweb="tab-list"] { gap: 6px; background: #0f1523; padding: 6px; border-radius: 12px; }
        .stTabs [data-baseweb="tab"] {
            background-color: transparent;
            border-radius: 8px;
            color: #6b7280;
            padding: 10px 18px;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.18s ease;
        }
        .stTabs [data-baseweb="tab"]:hover { color: #e2b714; background: #151c2c; }
        .stTabs [aria-selected="true"] {
            background-color: #e2b714 !important;
            color: #000000 !important;
        }

        .app-eyebrow {
            text-align: center; color: #e2b714;
            letter-spacing: 3px; font-size: 11px;
            font-weight: 800; margin-bottom: 4px;
            text-transform: uppercase;
        }
        .app-title { text-align: center; color: #ffffff; margin-top: 0px; margin-bottom: 28px; font-size: 28px; font-weight: 800; }

        .metric-card {
            background: linear-gradient(135deg, #111827 0%, #0f1622 100%);
            border: 1px solid #1f2a3d;
            border-radius: 14px;
            padding: 18px 20px;
            margin-bottom: 14px;
            transition: border-color 0.2s ease, transform 0.15s ease;
        }
        .metric-card:hover { border-color: #e2b714; transform: translateY(-1px); }

        .indicator-card {
            background: linear-gradient(135deg, #111827 0%, #0f1622 100%);
            border: 1px solid #1f2a3d;
            border-radius: 14px;
            padding: 16px 14px;
            text-align: center;
            transition: border-color 0.2s ease;
            height: 100%;
        }
        .indicator-card:hover { border-color: #e2b714; }
        .indicator-name { color: #6b7280; font-size: 12px; font-weight: 700; margin-bottom: 8px; letter-spacing: 0.5px; text-transform: uppercase; }
        .indicator-value { color: #ffffff; font-size: 24px; font-weight: 800; }
        .indicator-mom { color: #9ca3af; font-size: 12px; margin-top: 4px; }
        .indicator-date { color: #4b5563; font-size: 11px; margin-top: 6px; }

        .badge {
            display: inline-block; padding: 4px 12px;
            border-radius: 999px; font-size: 12px;
            font-weight: 700; margin-top: 8px; letter-spacing: 0.3px;
        }
        .badge-bullish { background-color: rgba(16,185,129,0.12); color: #10b981; border: 1px solid rgba(16,185,129,0.25); }
        .badge-bearish { background-color: rgba(239,68,68,0.12); color: #ef4444; border: 1px solid rgba(239,68,68,0.25); }
        .badge-neutral { background-color: rgba(156,163,175,0.12); color: #9ca3af; border: 1px solid rgba(156,163,175,0.2); }
        .badge-lg { font-size: 15px; padding: 8px 20px; border-radius: 10px; font-weight: 800; }

        .reasoning-box {
            background: linear-gradient(135deg, #0c1220 0%, #0f1523 100%);
            border-right: 3px solid #e2b714;
            padding: 16px 18px;
            margin-top: 12px;
            border-radius: 8px;
            font-size: 14px;
            line-height: 2;
        }
        .reasoning-box ul { margin: 0; padding-right: 20px; }
        .reasoning-box li { margin-bottom: 8px; color: #d1d5db; }

        th { color: #e2b714 !important; background-color: #111827 !important; font-size: 13px !important; }
        td { color: #f3f4f6 !important; font-size: 13px !important; }

        section[data-testid="stSidebar"] { background: linear-gradient(180deg, #090d17 0%, #0b1020 100%); }
        .footer-note { text-align: center; color: #374151; font-size: 12px; margin-top: 50px; padding: 20px; border-top: 1px solid #111827; }

        /* ---- Monthly Calendar Cards ---- */
        .cal-section-title {
            font-size: 13px; font-weight: 800; letter-spacing: 2px;
            text-transform: uppercase; color: #6b7280; margin: 20px 0 10px 0;
        }
        .cal-card {
            display: flex; align-items: flex-start; gap: 14px;
            background: linear-gradient(135deg, #0f1623 0%, #0c1220 100%);
            border: 1px solid #1a2436;
            border-radius: 12px; padding: 14px 16px;
            margin-bottom: 8px; transition: all 0.18s ease;
            position: relative; overflow: hidden;
        }
        .cal-card:hover { border-color: #2d3f5c; transform: translateX(-2px); }
        .cal-card.released { border-right: 3px solid #10b981; }
        .cal-card.upcoming { border-right: 3px solid #374151; }
        .cal-card.upcoming-soon { border-right: 3px solid #f59e0b; }
        .cal-card.high-impact { background: linear-gradient(135deg, #120f0a 0%, #0f1018 100%); }
        .cal-day-badge {
            min-width: 42px; height: 42px; border-radius: 10px;
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; font-weight: 800;
        }
        .cal-day-badge.released { background: rgba(16,185,129,0.12); color: #10b981; font-size: 16px; }
        .cal-day-badge.upcoming { background: rgba(55,65,81,0.4); color: #6b7280; font-size: 16px; }
        .cal-day-badge.upcoming-soon { background: rgba(245,158,11,0.12); color: #f59e0b; font-size: 16px; }
        .cal-content { flex: 1; }
        .cal-name { font-weight: 700; color: #e5e7eb; font-size: 14px; }
        .cal-hint { font-size: 11px; color: #4b5563; margin-top: 2px; }
        .cal-forecast { font-size: 13px; color: #d1d5db; margin-top: 6px; line-height: 1.6; }
        .cal-impact-badge {
            font-size: 10px; font-weight: 800; padding: 2px 7px;
            border-radius: 999px; letter-spacing: 0.5px; text-transform: uppercase;
        }
        .impact-high { background: rgba(239,68,68,0.12); color: #ef4444; }
        .impact-medium { background: rgba(245,158,11,0.12); color: #f59e0b; }
        .impact-low { background: rgba(107,114,128,0.12); color: #6b7280; }

        /* cumulative bias bar */
        .bias-bar-wrap { background: #111827; border-radius: 999px; height: 8px; margin: 8px 0; overflow: hidden; }
        .bias-bar { height: 100%; border-radius: 999px; transition: width 0.6s ease; }
        .bias-bar.bullish { background: linear-gradient(90deg, #059669, #10b981); }
        .bias-bar.bearish { background: linear-gradient(90deg, #b91c1c, #ef4444); }
        .bias-bar.neutral  { background: linear-gradient(90deg, #374151, #6b7280); }

        /* divider */
        .section-divider { border: none; border-top: 1px solid #111827; margin: 24px 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown('<div class="app-eyebrow">FX MACRO & GEOPOLITICAL DESK</div>', unsafe_allow_html=True)
    st.markdown('<h2 class="app-title">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h2>', unsafe_allow_html=True)


def bias_from_score(score: float):
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
def fetch_fred_series(series_id: str, key: str, limit: int = 30):
    """Return DataFrame[date, value] ascending, last `limit` obs."""
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


def calc_z_score(vals) -> float:
    if not vals or len(vals) < 2:
        return 0.0
    std = np.std(vals)
    return (vals[-1] - np.mean(vals)) / std if std != 0 else 0.0


def calc_mom_change(df: pd.DataFrame):
    """
    ژماردنی گۆڕانی مانگانە (m/m %) لە سیریاڵێکی ئاستدا.
    Returns: (mom_series_vals_list, latest_mom_pct, latest_date)
    """
    if df is None or len(df) < 2:
        return None, None, None
    df = df.copy()
    df["mom"] = df["value"].pct_change() * 100
    df = df.dropna(subset=["mom"])
    if df.empty:
        return None, None, None
    mom_vals = df["mom"].tolist()
    return mom_vals, round(mom_vals[-1], 3), df["date"].iloc[-1]


def latest_and_history(df, n: int = 12):
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


# ============================================================
# FORECAST ENGINE
# ============================================================

def compute_currency_composite(currency: str, fred_key: str):
    indicators = CURRENCY_SERIES[currency]
    rows, weighted_scores = [], []

    for name, meta in indicators.items():
        df = fetch_fred_series(meta["series"], fred_key, limit=30)
        if df is None or df.empty:
            continue

        if meta["type"] == "mom":
            mom_vals, latest_mom, latest_date = calc_mom_change(df)
            if mom_vals is None or len(mom_vals) < 2:
                continue
            z = calc_z_score(mom_vals)
            display_val = latest_mom
            display_suffix = "%"
            mom_info = f"m/m: {latest_mom:+.3f}%"
        else:
            vals, latest_date = latest_and_history(df)
            if not vals:
                continue
            z = calc_z_score(vals)
            display_val = round(vals[-1], 2)
            display_suffix = ""
            mom_info = ""

        interpreted = -z if meta["category"] == "labor_bad" else z
        direction = "up" if interpreted >= 0 else "down"

        rows.append({
            "name": name,
            "category": meta["category"],
            "value": display_val,
            "suffix": display_suffix,
            "mom_info": mom_info,
            "date": latest_date,
            "z": round(interpreted, 2),
            "weight": meta["weight"],
            "direction": direction,
            "phrase": INDICATOR_PHRASES.get((meta["category"], direction), "").format(name=name),
        })
        weighted_scores.append(interpreted * meta["weight"])

    if not rows:
        return None

    total_weight = sum(r["weight"] for r in rows)
    composite = sum(weighted_scores) / total_weight if total_weight else 0.0
    top_drivers = sorted(rows, key=lambda r: abs(r["z"] * r["weight"]), reverse=True)[:3]

    return {"composite": composite, "rows": rows, "top_drivers": top_drivers}


def render_reasoning_box(top_drivers) -> str:
    items = "".join(f"<li>{d['phrase']}</li>" for d in top_drivers if d["phrase"])
    if not items:
        items = "<li>هیچ ئاراستەیەکی بەهێز لە نیشاندەرەکاندا دیار نییە.</li>"
    return f'<div class="reasoning-box"><ul>{items}</ul></div>'


# ============================================================
# MONTHLY OUTLOOK ENGINE
# ============================================================

def get_forecast_for_indicator(name: str, meta: dict, fred_key: str) -> dict:
    """
    پێشبینی دینامیکی بۆ هەر نیشاندەرێک بەپێی:
    - مۆمێنتۆمی ١٢ مانگی ڕابوردوو
    - Z-Score
    - ئاراستەی ٣ مانگی ڕابوردوو (Short-term trend)
    """
    df = fetch_fred_series(meta["series"], fred_key, limit=30)
    if df is None or df.empty:
        return {"score": 0.0, "confidence": "low", "reason": "داتا بەردەست نییە", "latest_val": None, "date": None, "mom": None}

    if meta["type"] == "mom":
        mom_vals, latest_mom, latest_date = calc_mom_change(df)
        if mom_vals is None or len(mom_vals) < 3:
            return {"score": 0.0, "confidence": "low", "reason": "داتای پێویست کەم", "latest_val": None, "date": None, "mom": None}
        
        z = calc_z_score(mom_vals)
        # Short-term momentum: ئایا ٣ مانگی ڕابوردوو ئەرێنی بوون؟
        recent_3 = mom_vals[-3:]
        short_trend = np.mean(recent_3)
        
        # Acceleration: گۆڕانی لەنێوان مانگی ئەمسا و مانگی پێش خۆی
        acceleration = mom_vals[-1] - mom_vals[-2] if len(mom_vals) >= 2 else 0

        score = z * 0.5 + np.sign(short_trend) * 0.35 + np.sign(acceleration) * 0.15
        interpreted = -score if meta["category"] == "labor_bad" else score

        confidence = "high" if abs(z) > 0.8 else ("medium" if abs(z) > 0.4 else "low")
        
        reason = (
            f"کۆتا m/m: {latest_mom:+.3f}% | "
            f"مامناوەی ٣ مانگی ڕابوردوو: {short_trend:+.3f}% | "
            f"Z-Score: {z:+.2f}"
        )
        return {
            "score": interpreted,
            "confidence": confidence,
            "reason": reason,
            "latest_val": latest_mom,
            "date": latest_date,
            "mom": latest_mom,
        }
    else:
        vals, latest_date = latest_and_history(df)
        if not vals or len(vals) < 3:
            return {"score": 0.0, "confidence": "low", "reason": "داتای پێویست کەم", "latest_val": None, "date": None, "mom": None}
        
        z = calc_z_score(vals)
        recent_trend = vals[-1] - vals[-3]  # گۆڕان لە ٣ مانگدا
        score = z * 0.7 + np.sign(recent_trend) * 0.3
        interpreted = -score if meta["category"] == "labor_bad" else score
        
        confidence = "high" if abs(z) > 0.8 else ("medium" if abs(z) > 0.4 else "low")
        reason = f"کۆتا ئاست: {round(vals[-1], 2)} | Z-Score: {z:+.2f} | گۆڕانی ٣ مانگ: {recent_trend:+.2f}"
        return {
            "score": interpreted,
            "confidence": confidence,
            "reason": reason,
            "latest_val": round(vals[-1], 2),
            "date": latest_date,
            "mom": None,
        }


def build_monthly_outlook(currency: str, fred_key: str) -> list:
    """
    دروستکردنی لیستی هەواڵەکانی ئەم مانگ لەگەڵ:
    - بەروار + ئایا بڵاوبووەتەوە
    - پێشبینی + هۆکار
    - کاریگەری بازاری
    """
    today = date.today()
    calendar_events = MONTHLY_CALENDAR.get(currency, [])
    indicators = CURRENCY_SERIES.get(currency, {})

    results = []
    for event in calendar_events:
        name = event["name"]
        day = event["day"]
        hint = event["hint"]
        impact = event["impact"]
        quarterly = event.get("quarterly", False)

        # ئایا ئەم مانگ بڵاوکردنەوەی هەیە؟ (کوارتەرلی: تەنها مانگی ١، ٤، ٧، ١٠)
        is_this_month = True
        if quarterly:
            if today.month not in [1, 4, 7, 10]:
                is_this_month = False

        try:
            max_day = calendar.monthrange(today.year, today.month)[1]
            safe_day = min(day, max_day)
            release_date = date(today.year, today.month, safe_day)
        except ValueError:
            continue

        is_released = today >= release_date and is_this_month

        # پێشبینی لەپێی داتای FRED
        meta = indicators.get(name)
        forecast = None
        if meta and fred_key:
            forecast = get_forecast_for_indicator(name, meta, fred_key)

        score = forecast["score"] if forecast else 0.0
        label, _ = bias_from_score(score)
        confidence = forecast["confidence"] if forecast else "low"
        reason = forecast["reason"] if forecast else ""
        latest_val = forecast["latest_val"] if forecast else None
        latest_date = forecast["date"] if forecast else None

        days_until = (release_date - today).days

        results.append({
            "name": name,
            "day": day,
            "release_date": release_date,
            "hint": hint,
            "impact": impact,
            "is_released": is_released,
            "is_this_month": is_this_month,
            "quarterly": quarterly,
            "score": score,
            "label": label,
            "confidence": confidence,
            "reason": reason,
            "latest_val": latest_val,
            "latest_date": latest_date,
            "days_until": days_until,
        })

    results.sort(key=lambda x: x["day"])
    return results


# ============================================================
# TAB 1 — Macro Strength & Predictive Engine
# ============================================================

def render_macro_tab(fred_key: str) -> None:
    selected_currency = st.radio("دراوەکە هەڵبژێرە:", list(CURRENCY_SERIES.keys()), horizontal=True)

    if not fred_key:
        st.info("🔑 تکایە FRED API Key لە لای ڕاست بنووسە بۆ بارکردنی داتاکان.")
        return

    with st.spinner("داتاکان ڕادەکێشرێن (m/m و ئاست هەردووکیان)..."):
        result = compute_currency_composite(selected_currency, fred_key)

    if not result:
        st.warning("⚠️ هیچ داتایەک نەدۆزرایەوە. تکایە API Key‌ەکەت بپشکنە.")
        return

    rows = result["rows"]

    # --- Quick-glance indicator cards ---
    st.subheader("📋 کۆتا داتا ڕاستەقینەکان")

    # ٤ ستون بۆ کاریگەریی باشتر
    chunk = 4
    for i in range(0, len(rows), chunk):
        cols = st.columns(min(chunk, len(rows) - i))
        for col, row in zip(cols, rows[i:i + chunk]):
            with col:
                val_display = f"{row['value']}{row['suffix']}"
                mom_line = f"<div class='indicator-mom'>{row['mom_info']}</div>" if row["mom_info"] else ""
                st.markdown(
                    f"""
                    <div class="indicator-card">
                        <div class="indicator-name">{row['name']}</div>
                        <div class="indicator-value">{val_display}</div>
                        {mom_line}
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
                    "شێوە": "m/m %" if r["suffix"] == "%" else "ئاست",
                    "Z-Score": r["z"],
                    "کێش (Impact)": r["weight"],
                    "بەروار": r["date"],
                }
                for r in rows
            ]
        )
        st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("🔮 پێشبینیکردنی گشتی دراوەکە")

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("**ئاراستەی گشتی:**")
        st.markdown(badge_html(result["composite"], large=True), unsafe_allow_html=True)
        st.caption(f"Composite score: {round(result['composite'], 2)}")
    with col2:
        st.markdown("**هۆکارە سەرەکییەکان:**")
        st.markdown(render_reasoning_box(result["top_drivers"]), unsafe_allow_html=True)

    st.caption(
        "ℹ️ نیشاندەرە m/m کانیش (Core CPI, Core PPI, Core PCE…) لە ژماردن بەشداریان کردووە. "
        "Z-Score بەپێی ١٢ مانگی ڕابوردوو ژمێردراوە."
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
        st.warning("🔑 تکایە NewsAPI Key لە لای ڕاست بنووسە (newsapi.org).")
        return

    with st.spinner("هەواڵەکان ڕادەکێشرێن..."):
        articles = fetch_news(keywords[category], news_key)

    if articles is None:
        st.error("⚠️ پەیوەندی لەگەڵ NewsAPI ڕوونەگرت.")
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
                <p style="font-size:12px; color:#6b7280; margin:0 0 8px 0;">
                    سەرچاوە: {source} &nbsp;|&nbsp; بەروار: {published}
                </p>
                <p style="font-size:14px; margin:0 0 8px 0; color:#d1d5db;">{description}</p>
                <a href="{link}" target="_blank" style="color:#10b981; font-size:13px; font-weight:600;">خوێندنەوەی زیاتر ↗</a>
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
        st.info("🔑 تکایە FRED API Key لە لای ڕاست بنووسە.")
        return

    with st.spinner("داتای خاو و چاوەڕوانی هەڵکشانی نرخ ڕادەکێشرێت..."):
        yield_df = fetch_fred_series(GOLD_YIELD_SERIES, fred_key, limit=30)
        infl_df = fetch_fred_series(GOLD_INFLATION_EXP_SERIES, fred_key, limit=30)
        usd_result = compute_currency_composite("USD دۆلار", fred_key)

    if yield_df is None or infl_df is None:
        st.warning("⚠️ نەتوانرا داتای DGS10 یان T10YIE وەربگیرێت.")
        return

    merged = pd.merge(yield_df, infl_df, on="date", suffixes=("_yield", "_infl"))
    if merged.empty or len(merged) < 2:
        st.warning("⚠️ داتای پێویست بۆ ژماردنی Real Yield تەواو نییە.")
        return

    merged["real_yield"] = merged["value_yield"] - merged["value_infl"]
    real_yield_vals = merged["real_yield"].tail(12).tolist()
    latest_date = merged["date"].iloc[-1]

    real_yield_z = calc_z_score(real_yield_vals)
    gold_component_yield = -real_yield_z
    usd_component = -usd_result["composite"] if usd_result else 0.0
    gold_score = 0.6 * gold_component_yield + 0.4 * usd_component

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            f"""<div class="indicator-card">
                <div class="indicator-name">Real Yield (10Y)</div>
                <div class="indicator-value">{round(real_yield_vals[-1], 2)}%</div>
                <div class="indicator-date">{latest_date}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col2:
        usd_disp = round(usd_result["composite"], 2) if usd_result else "—"
        st.markdown(
            f"""<div class="indicator-card">
                <div class="indicator-name">بەهێزی دۆلار (USD Composite)</div>
                <div class="indicator-value">{usd_disp}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f"""<div class="indicator-card">
                <div class="indicator-name">ئاراستەی گشتی زێڕ</div>
                <div style="margin-top:8px;">{badge_html(gold_score, large=True)}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("**هۆکارە سەرەکییەکان بۆ زێڕ:**")
    reasons = []
    if real_yield_z > 0.3:
        reasons.append("Real Yield بەرزبووەتەوە — هەڵگرتنی زێڕ تێچووی زیاتری هەیە، فشار دەخاتە سەر نرخی زێڕ.")
    elif real_yield_z < -0.3:
        reasons.append("Real Yield دابەزیوە — تێچووی هەڵگرتنی زێڕ کەمتر بووە، پشتگیری لە نرخی زێڕ دەکات.")
    else:
        reasons.append("Real Yield بە جێگیری مایەوە، کاریگەریی ڕوونی نییە لە ئێستادا.")

    if usd_result:
        if usd_result["composite"] > 0.3:
            reasons.append("دۆلار بەهێز بووە — گرانتربوونی زێڕ بۆ کڕیارانی دراوەکانی تر.")
        elif usd_result["composite"] < -0.3:
            reasons.append("دۆلار لاواز بووە — پشتگیری لە نرخی زێڕ دەکات.")
        else:
            reasons.append("دۆلار لە بارودۆخێکی سەقامگیردایە.")

    st.markdown(
        '<div class="reasoning-box"><ul>' + "".join(f"<li>{r}</li>" for r in reasons) + "</ul></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "ℹ️ Real Yield = DGS10 − T10YIE. ئەم مۆدێلە کاریگەری هەواڵی جیۆپۆلیتیکی لەخۆناگرێت — بۆ ئەوە تابی 'Live World News' بپشکنە."
    )


# ============================================================
# TAB 4 — Monthly Calendar & Outlook  ✨ تابی نوێ
# ============================================================

def render_monthly_tab(fred_key: str) -> None:
    today = date.today()
    month_name_ku = {
        1: "کانوونی دووەم", 2: "شوبات", 3: "ئازار", 4: "نیسان",
        5: "ئایار", 6: "حوزەیران", 7: "تەممووز", 8: "ئاب",
        9: "ئەیلوول", 10: "تشرینی یەکەم", 11: "تشرینی دووەم", 12: "کانوونی یەکەم",
    }

    st.subheader(f"📅 کالێندەری شیکاری مانگانە — {month_name_ku[today.month]} {today.year}")
    st.markdown(
        f"<p style='color:#6b7280; font-size:13px;'>ئەمڕۆ: <strong style='color:#e2b714'>{today.strftime('%Y-%m-%d')}</strong> | "
        "سیستەمەکە بەپێی ڕۆژی ئەمڕۆ دیاری دەکات کام هەواڵ بڵاوبووەتەوە و کامیان داهاتوون</p>",
        unsafe_allow_html=True,
    )

    selected_currency = st.radio(
        "دراوەکە هەڵبژێرە:", list(MONTHLY_CALENDAR.keys()), horizontal=True, key="monthly_tab_currency"
    )

    if not fred_key:
        st.info("🔑 تکایە FRED API Key لە لای ڕاست بنووسە بۆ پێشبینیی هەواڵەکان.")
        return

    with st.spinner("کالێندەری مانگ دروستدەکرێت و پێشبینیی داهاتوو ژمێردەکرێت..."):
        events = build_monthly_outlook(selected_currency, fred_key)

    released_events = [e for e in events if e["is_released"] and e["is_this_month"]]
    upcoming_events = [e for e in events if not e["is_released"] and e["is_this_month"]]
    skipped_quarterly = [e for e in events if not e["is_this_month"]]

    # --------- Cumulative Bias Summary ----------
    all_scored = [e for e in events if e["is_this_month"]]
    if all_scored:
        cum_score = np.mean([e["score"] for e in all_scored])
        label_cum, cls_cum = bias_from_score(cum_score)
        bar_pct = min(abs(cum_score) / 1.5 * 100, 100)
        bar_cls = "bullish" if cum_score > 0.3 else ("bearish" if cum_score < -0.3 else "neutral")

        st.markdown("---")
        col_b1, col_b2 = st.columns([2, 1])
        with col_b1:
            st.markdown(f"**📊 ئاراستەی کۆی مانگ بۆ {selected_currency}:**")
            st.markdown(
                f"""<div class="bias-bar-wrap"><div class="bias-bar {bar_cls}" style="width:{bar_pct}%"></div></div>""",
                unsafe_allow_html=True,
            )
            released_pct = len(released_events) / len(all_scored) * 100 if all_scored else 0
            st.caption(
                f"بڵاوبووەوە: {len(released_events)} هەواڵ | داهاتوو: {len(upcoming_events)} هەواڵ | "
                f"پێشکەوتنی مانگ: {released_pct:.0f}%"
            )
        with col_b2:
            st.markdown(badge_html(cum_score, large=True), unsafe_allow_html=True)
        st.markdown("---")

    # --------- Released Events ----------
    if released_events:
        st.markdown('<div class="cal-section-title">✅ بڵاوبووەتەوە — Released</div>', unsafe_allow_html=True)
        for ev in released_events:
            impact_cls = f"impact-{ev['impact']}"
            val_str = f"{ev['latest_val']:+.3f}%" if ev.get("latest_val") is not None and isinstance(ev["latest_val"], float) else (str(ev["latest_val"]) if ev["latest_val"] is not None else "—")
            forecast_html = (
                f"<div class='cal-forecast'>"
                f"<strong>داتای کۆتا (FRED):</strong> {val_str}"
                f"{'  |  بەروار: ' + str(ev['latest_date']) if ev['latest_date'] else ''}<br>"
                f"<strong>شیکاری:</strong> {ev['reason']}</div>"
            ) if ev["reason"] else ""

            st.markdown(
                f"""<div class="cal-card released">
                    <div class="cal-day-badge released">{ev['day']:02d}</div>
                    <div class="cal-content">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span class="cal-name">{ev['name']}</span>
                            <span class="cal-impact-badge {impact_cls}">{ev['impact'].upper()}</span>
                        </div>
                        <div class="cal-hint">📌 {ev['hint']}</div>
                        {forecast_html}
                        <div style="margin-top:6px;">{badge_html(ev['score'])}</div>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )

    # --------- Upcoming Events ----------
    if upcoming_events:
        st.markdown('<div class="cal-section-title">🔮 داهاتوو — Upcoming (پێشبینی)</div>', unsafe_allow_html=True)
        for ev in upcoming_events:
            impact_cls = f"impact-{ev['impact']}"
            card_cls = "upcoming-soon" if 0 <= ev["days_until"] <= 3 else "upcoming"
            day_cls = "upcoming-soon" if 0 <= ev["days_until"] <= 3 else "upcoming"

            days_str = (
                f"⚡ هەفتان دوا! ({ev['days_until']} ڕۆژ)" if 0 <= ev["days_until"] <= 3
                else f"{ev['days_until']} ڕۆژ دوا"
            )

            conf_color = {"high": "#10b981", "medium": "#f59e0b", "low": "#6b7280"}.get(ev["confidence"], "#6b7280")
            conf_ku = {"high": "دڵنیایی بەرز", "medium": "دڵنیایی ناوەند", "low": "دڵنیایی کەم"}.get(ev["confidence"], "")

            forecast_html = (
                f"<div class='cal-forecast'>"
                f"<strong>پێشبینی بەپێی داتای FRED:</strong><br>{ev['reason']}<br>"
                f"<span style='color:{conf_color}; font-size:11px; font-weight:700;'>● {conf_ku}</span>"
                f"</div>"
            ) if ev["reason"] else ""

            st.markdown(
                f"""<div class="cal-card {card_cls}">
                    <div class="cal-day-badge {day_cls}">{ev['day']:02d}</div>
                    <div class="cal-content">
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <span class="cal-name">{ev['name']}</span>
                            <span class="cal-impact-badge {impact_cls}">{ev['impact'].upper()}</span>
                            <span style="font-size:11px; color:#6b7280;">{days_str}</span>
                        </div>
                        <div class="cal-hint">📌 {ev['hint']}</div>
                        {forecast_html}
                        <div style="margin-top:6px;">{badge_html(ev['score'])} <span style="font-size:11px; color:#4b5563; margin-right:8px;">← پێشبینیی ئاراستە</span></div>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )

    if skipped_quarterly:
        with st.expander(f"📆 {len(skipped_quarterly)} هەواڵی کوارتەرلی (ئەم مانگ نییە)"):
            for ev in skipped_quarterly:
                st.markdown(f"- **{ev['name']}** — {ev['hint']}")

    st.caption(
        "ℹ️ پێشبینیکانی 'داهاتوو' لەسەر بنەمای Z-Score، مۆمێنتۆمی ١٢ مانگی ڕابوردوو، و ئاراستەی ٣ مانگی ڕابوردوو دروستکراون. "
        "ئەمانە راهێنانی ستاتیستیکین — نەک پێشبینیی ١٠٠٪ ورد. بەروارەکانیش تیپیکن، نەک فەرمی."
    )


# ============================================================
# TAB 5 — Impact Reference Table
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
        fred_key = st.text_input("FRED API Key:", type="password")
        news_api_key = st.text_input("NewsAPI Key (بۆ هەواڵەکان):", type="password")
        st.caption(f"🕓 دوایین نوێکردنەوە: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        st.markdown("---")
        st.markdown("**چۆن API Key وەربگرین؟**")
        st.markdown(
            "• [FRED API Key](https://fred.stlouisfed.org/docs/api/api_key.html) — خۆڕایی\n"
            "• [NewsAPI Key](https://newsapi.org/register) — خۆڕایی"
        )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "📊 Macro Strength",
            "📰 Live World News",
            "🥇 Gold Analysis",
            "📅 Monthly Outlook",
            "💡 Impact Table",
        ]
    )

    with tab1:
        render_macro_tab(fred_key)
    with tab2:
        render_news_tab(news_api_key)
    with tab3:
        render_gold_tab(fred_key)
    with tab4:
        render_monthly_tab(fred_key)
    with tab5:
        render_impact_tab()

    st.markdown(
        '<div class="footer-note">FX Macro & News Intelligence Desk v3 — بۆ مەبەستی شیکاری و فێربوون، '
        "نەک ڕاوێژی دارایی.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
