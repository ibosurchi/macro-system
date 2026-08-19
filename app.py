"""
FX Macro & News Intelligence Desk — v10 Auto-Refresh
سیستەمی پێشبینیکردن، شیکاری مەکرۆ، زێڕ و ڕۆژژمێری ForexFactory بە نوێبوونەوەی ئۆتۆماتیکی
"""

from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, date
import pytz
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="FX Macro & ForexFactory Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

REQUEST_TIMEOUT = 10

# ============================================================
# FIXED API KEYS
# ============================================================
FRED_API_KEY = "8e153c7f6941848ffe00388ae93c1d73"
NEWS_API_KEY = "70fc541920ca43e69ee716ad442405fb"

# ============================================================
# CURRENCY & METRIC CONFIGURATIONS (ACTIVE FRED SERIES)
# ============================================================

CURRENCY_SERIES = {
    "USD دۆلار": {
        "CPI":           {"series": "CPIAUCSL", "category": "inflation",  "weight": 1.5, "impact": "high"},
        "Core CPI":      {"series": "CPILFESL", "category": "inflation",  "weight": 2.0, "impact": "high"},
        "PPI":           {"series": "PPIFIS",   "category": "inflation",  "weight": 1.2, "impact": "high"},
        "Core PPI":      {"series": "PPIFES",   "category": "inflation",  "weight": 1.5, "impact": "high"},
        "Core PCE":      {"series": "PCEPILFE", "category": "inflation",  "weight": 2.0, "impact": "high"},
        "PCE":           {"series": "PCEPI",    "category": "inflation",  "weight": 1.3, "impact": "high"},
        "NFP":           {"series": "PAYEMS",   "category": "labor_good", "weight": 1.8, "impact": "high"},
        "Unemployment":  {"series": "UNRATE",   "category": "labor_bad",  "weight": 1.8, "impact": "high"},
        "Retail Sales":  {"series": "RSAFS",    "category": "growth",     "weight": 1.2, "impact": "high"},
        "GDP":           {"series": "GDP",      "category": "growth",     "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "FEDFUNDS", "category": "rate",       "weight": 2.0, "impact": "high"},
    },
    "EUR یۆرۆ": {
        "CPI":           {"series": "CP0000EZ19M086NEST", "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "CP0000EZ00M086NEST", "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Production":    {"series": "PRINTO01EZM661S",    "category": "growth",     "weight": 1.2, "impact": "medium"},
        "Unemployment":  {"series": "LRHUTTTTEZM156S",    "category": "labor_bad",  "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "ECBDFR",             "category": "rate",       "weight": 2.0, "impact": "high"},
        "GDP":           {"series": "CLVMNACSCAB1GQEA19", "category": "growth",     "weight": 1.5, "impact": "high"},
    },
    "GBP پاوەند": {
        "CPI":           {"series": "GBRCPIALLMINMEI",    "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "GBRCPICORMINMEI",    "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Production":    {"series": "GBRPROINDMISMEI",    "category": "growth",     "weight": 1.2, "impact": "medium"},
        "Unemployment":  {"series": "LMUNRRTTGBM156S",    "category": "labor_bad",  "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "BOERUKM",            "category": "rate",       "weight": 1.8, "impact": "high"},
    },
    "CAD کەنەدی": {
        "CPI":           {"series": "CANCPIALLMINMEI",    "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "CANCPICORMINMEI",    "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Employment":    {"series": "LFEMTTTTCAM647S",    "category": "labor_good", "weight": 1.5, "impact": "high"},
        "Unemployment":  {"series": "LRUN64TTCAM156S",    "category": "labor_bad",  "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "IRSTCB01CAM156N",    "category": "rate",       "weight": 1.8, "impact": "high"},
    },
    "JPY یەن": {
        "CPI":           {"series": "JPNCPIALLMINMEI",    "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "JPNCPICORMINMEI",    "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Production":    {"series": "JPNPROINDMISMEI",    "category": "growth",     "weight": 1.2, "impact": "medium"},
        "Unemployment":  {"series": "LRUN64TTJPM156S",    "category": "labor_bad",  "weight": 1.5, "impact": "medium"},
        "Interest Rate": {"series": "IRSTCB01JPM156N",    "category": "rate",       "weight": 2.0, "impact": "high"},
    },
}

KEY_INDICATORS = {
    "USD دۆلار":  ["Core CPI", "Core PCE", "NFP", "Interest Rate"],
    "EUR یۆرۆ":   ["CPI", "Core CPI", "Unemployment", "Interest Rate"],
    "GBP پاوەند": ["CPI", "Core CPI", "Unemployment", "Interest Rate"],
    "CAD کەنەدی": ["CPI", "Employment", "Unemployment", "Interest Rate"],
    "JPY یەن":    ["CPI", "Core CPI", "Production", "Interest Rate"],
}

CATEGORY_LABELS = {
    "inflation":  "هەڵکشانی نرخ",
    "labor_good": "بازاڕی کار",
    "labor_bad":  "بێکاری",
    "growth":     "گەشەی ئابووری",
    "rate":       "ڕێژەی سوود",
}

CATEGORY_ICONS = {
    "inflation":  "📈",
    "labor_good": "👥",
    "labor_bad":  "📊",
    "growth":     "🏭",
    "rate":       "🏦",
}

GOLD_YIELD_SERIES = "DGS10"
GOLD_INFLATION_EXP_SERIES = "T10YIE"

# ============================================================
# MOBILE & DESKTOP CSS
# ============================================================

def render_html(html_str: str) -> None:
    clean_html = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(clean_html, unsafe_allow_html=True)


def inject_css() -> None:
    css_content = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; box-sizing: border-box; }

.stApp { background-color: #060a12 !important; color: #e5e7eb !important; }
.main .block-container {
    padding-top: 10px !important;
    padding-left: 14px !important;
    padding-right: 14px !important;
    max-width: 100% !important;
}

.top-header-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #090e1a;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px; margin-bottom: 14px;
    flex-wrap: wrap; gap: 8px;
}
.top-brand { display: flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 800; color: #e2b714; }
.top-tickers { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ticker-pill { display: inline-flex; align-items: center; gap: 5px; background: #0d1527; border: 1px solid rgba(255, 255, 255, 0.05); padding: 4px 8px; border-radius: 8px; font-size: 10.5px; font-weight: 600; color: #9ca3af; }
.ticker-up { color: #10b981; font-weight: 700; }

.main-title-wrap { text-align: center; padding: 6px 0 16px 0; }
.main-gold-sub { font-size: 10px; font-weight: 800; letter-spacing: 1.5px; color: #e2b714; text-transform: uppercase; margin-bottom: 4px; }
.main-big-heading { font-size: 20px; font-weight: 900; color: #ffffff; margin: 0 0 4px 0; }
.main-breadcrumb { font-size: 11px; color: #8a99ad; font-weight: 500; }

.metric-card {
    background: #090e1a; border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px; padding: 14px; margin-bottom: 10px;
}
.mc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.mc-icon-wrap { width: 30px; height: 30px; border-radius: 8px; background: rgba(226, 183, 20, 0.08); display: flex; align-items: center; justify-content: center; font-size: 14px; }
.mc-cat { font-size: 9px; font-weight: 800; color: #8a99ad; padding: 2px 6px; border-radius: 999px; background: rgba(255, 255, 255, 0.04); }
.mc-name { font-size: 12.5px; font-weight: 700; color: #8a99ad; margin: 2px 0; }
.mc-value { font-size: 20px; font-weight: 800; color: #ffffff; line-height: 1.1; margin-bottom: 2px; }
.mc-change { font-size: 11px; font-weight: 700; }
.mc-date { font-size: 9.5px; color: #4b5563; margin-top: 4px; }

.ref-table-card {
    background: #090e1a; border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px; overflow-x: auto; -webkit-overflow-scrolling: touch;
    margin-bottom: 12px;
}
.ref-table { width: 100%; min-width: 320px; border-collapse: collapse; font-size: 12px; direction: rtl; text-align: right; }
.ref-table thead th { background: #0c1322; color: #8a99ad; padding: 10px 12px; font-weight: 600; font-size: 11px; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
.ref-table thead th.th-ctr { text-align: center; direction: ltr; }
.ref-table tbody td { padding: 10px 12px; color: #e5e7eb; vertical-align: middle; border-bottom: 1px solid rgba(255, 255, 255, 0.03); }
.ref-table td.td-name { font-weight: 700; color: #ffffff; }
.ref-table td.td-val, .ref-table td.td-pct { font-family: 'Inter', monospace, sans-serif; font-weight: 600; text-align: center; direction: ltr; }
.ref-badge-green { color: #10b981; font-weight: 700; }
.ref-badge-red { color: #ef4444; font-weight: 700; }
.ref-badge-gray { color: #6b7280; font-weight: 700; }
.ref-table-footer { padding: 8px 12px; font-size: 10px; color: #8a99ad; background: #080c16; }

.ff-card {
    display: flex; align-items: center; gap: 12px; background: #090e1a;
    border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 12px;
    padding: 12px 14px; margin-bottom: 8px;
}
.ff-time-badge {
    min-width: 65px; height: 38px; border-radius: 10px; display: flex;
    align-items: center; justify-content: center; font-weight: 800;
    font-size: 11px; flex-shrink: 0; background: #0c1322; color: #e2b714;
    border: 1px solid rgba(255, 255, 255, 0.05);
}
.ff-content { flex: 1; min-width: 0; }
.ff-name { font-weight: 800; color: #ffffff; font-size: 13px; }
.ff-impact-badge { font-size: 9px; font-weight: 800; padding: 2px 7px; border-radius: 999px; }
.impact-high { background: rgba(239, 68, 68, 0.14); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
.impact-medium { background: rgba(245, 158, 11, 0.14); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3); }
.impact-low { background: rgba(107, 114, 128, 0.14); color: #9ca3af; border: 1px solid rgba(107, 114, 128, 0.3); }

div[data-testid="stRadio"] div[role="radiogroup"] { display: flex !important; gap: 6px !important; flex-wrap: wrap !important; }
div[data-testid="stRadio"] div[role="radiogroup"] label { background: #090e1a !important; border: 1px solid rgba(255, 255, 255, 0.08) !important; border-radius: 8px !important; padding: 5px 10px !important; color: #8a99ad !important; font-size: 11.5px !important; cursor: pointer !important; }
div[data-testid="stRadio"] div[role="radiogroup"] [aria-checked="true"] { background: rgba(226, 183, 20, 0.12) !important; border-color: #e2b714 !important; color: #e2b714 !important; font-weight: 700 !important; }
div[data-testid="stRadio"] input[type="radio"], div[data-testid="stRadio"] label > div:first-child { display: none !important; }

.badge { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 700; }
.badge-bullish { background: rgba(16, 185, 129, 0.12); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.2); }
.badge-bearish { background: rgba(239, 68, 68, 0.12); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.2); }
.badge-neutral { background: rgba(107, 114, 128, 0.12); color: #9ca3af; border: 1px solid rgba(107, 114, 128, 0.2); }
.badge-lg { font-size: 13px; padding: 6px 16px; border-radius: 10px; font-weight: 800; }

.section-title { font-size: 10.5px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; color: #8a99ad; margin-bottom: 10px; margin-top: 4px; display: flex; align-items: center; gap: 6px; }
.section-title::after { content: ''; flex: 1; height: 1px; background: linear-gradient(90deg, rgba(255, 255, 255, 0.08), transparent); }

.app-footer { display: flex; justify-content: space-between; align-items: center; padding: 14px; margin-top: 30px; border-top: 1px solid rgba(255, 255, 255, 0.05); font-size: 10px; color: #4b5563; flex-wrap: wrap; gap: 6px; }
.live-status { display: flex; align-items: center; gap: 5px; color: #10b981; font-weight: 600; }
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; box-shadow: 0 0 6px #10b981; }

#MainMenu, footer, .stDeployButton { display: none !important; }
header[data-testid="stHeader"] { background: transparent !important; }

@media (max-width: 768px) {
    .main-big-heading { font-size: 17px !important; }
    .top-brand { font-size: 11.5px !important; }
    .top-tickers { display: none !important; }
}
</style>
"""
    render_html(css_content)


# ============================================================
# DATA FETCHING ENGINE
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fred_series(series_id: str, key: str, limit: int = 36):
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
        return df[["date", "value"]].tail(limit).reset_index(drop=True) if not df.empty else None
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_forexfactory_calendar():
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        events = res.json()
        if not events:
            return pd.DataFrame()

        df = pd.DataFrame(events)
        df['datetime'] = pd.to_datetime(df['date'])
        local_tz = pytz.timezone('Asia/Baghdad')
        df['local_time'] = df['datetime'].dt.tz_convert(local_tz)
        df['date_str'] = df['local_time'].dt.strftime('%Y-%m-%d')
        df['time_str'] = df['local_time'].dt.strftime('%I:%M %p')
        return df
    except Exception:
        return pd.DataFrame()


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
    except Exception:
        return None


# ============================================================
# MULTI-TIMEFRAME CALCULATION
# ============================================================

def calc_multiframe(vals: list, dates: list, category: str) -> dict | None:
    if not vals or len(vals) < 2:
        return None

    last_date_str = dates[-1]
    is_stale = False
    try:
        last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
        if (datetime.now() - last_dt).days > 240:
            is_stale = True
    except Exception:
        pass

    reverse = (category == "labor_bad")
    mom = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100 if vals[-2] != 0 else 0.0

    qoq = None
    if len(vals) >= 6:
        qnow  = np.mean(vals[-3:])
        qprev = np.mean(vals[-6:-3])
        qoq = (qnow - qprev) / abs(qprev) * 100 if qprev != 0 else 0.0

    yoy = None
    if len(vals) >= 13:
        yoy = (vals[-1] - vals[-13]) / abs(vals[-13]) * 100 if vals[-13] != 0 else 0.0

    z_level = 0.0
    if len(vals) >= 6:
        sub = vals[-12:] if len(vals) >= 12 else vals
        std = np.std(sub)
        z_level = (vals[-1] - np.mean(sub)) / std if std != 0 else 0.0

    def t(x, ref):
        return float(np.tanh(x / ref)) if ref != 0 and x is not None else 0.0

    parts = [
        (t(mom,     0.5),  0.35),
        (t(qoq,     2.0),  0.25),
        (t(yoy,     5.0),  0.25),
        (t(z_level, 1.0),  0.15),
    ]
    denom = sum(w for _, w in parts)
    composite = sum(s * w for s, w in parts) / denom if denom else 0.0
    if reverse:
        composite = -composite

    return {
        "latest":    vals[-1],
        "mom":       round(mom, 3),
        "qoq":       round(qoq, 3) if qoq is not None else None,
        "yoy":       round(yoy, 3) if yoy is not None else None,
        "composite": float(composite),
        "is_stale":  is_stale,
    }


def compute_currency_composite(currency: str, fred_key: str):
    indicators = CURRENCY_SERIES[currency]
    rows, weighted = [], []

    for name, meta in indicators.items():
        df = fetch_fred_series(meta["series"], fred_key, limit=36)
        if df is None or df.empty:
            continue
        vals  = df["value"].tolist()
        dates = df["date"].tolist()
        mf = calc_multiframe(vals, dates, meta["category"])
        if mf is None:
            continue

        rows.append({
            "name":      name,
            "category":  meta["category"],
            "weight":    meta["weight"],
            "impact":    meta.get("impact", "high"),
            "df":        df,
            "vals":      vals,
            "date":      dates[-1],
            **mf,
        })
        if not mf["is_stale"]:
            weighted.append(mf["composite"] * meta["weight"])

    if not rows:
        return None

    tw = sum(r["weight"] for r in rows if not r["is_stale"])
    composite = sum(weighted) / tw if tw else 0.0
    return {"composite": composite, "rows": rows}


# ============================================================
# CHART BUILDERS & HELPERS
# ============================================================

def bias_from_score(score: float):
    if score > 0.15:
        return "📈 Bullish", "badge-bullish"
    if score < -0.15:
        return "📉 Bearish", "badge-bearish"
    return "⚖️ Neutral", "badge-neutral"


def badge_html(score: float, large: bool = False) -> str:
    label, css = bias_from_score(score)
    sz = "badge-lg" if large else ""
    return f'<span class="badge {css} {sz}">{label}</span>'


def svg_spark(vals: list, width: int = 70, height: int = 28, positive_is_good: bool = True) -> str:
    if not vals or len(vals) < 2:
        return ""
    mn, mx = min(vals), max(vals)
    rng = mx - mn or 1
    n = len(vals)
    trend_up = vals[-1] > vals[0]
    good = trend_up == positive_is_good
    line_c = "#10b981" if good else "#ef4444"
    fill_c = "rgba(16,185,129,0.08)" if good else "rgba(239,68,68,0.08)"

    pts = [(i / (n - 1) * width, height - (vals[i] - mn) / rng * height) for i in range(n)]
    path  = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    fpath = path + f" L {width},{ height} L 0,{height} Z"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="display:block;">'
        f'<path d="{fpath}" fill="{fill_c}"/>'
        f'<path d="{path}" fill="none" stroke="{line_c}" stroke-width="1.8"/>'
        f'</svg>'
    )


def make_dynamic_chart(df: pd.DataFrame, indicator_name: str) -> go.Figure | None:
    if df is None or df.empty:
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["value"],
        mode="lines+markers", marker=dict(size=4, color="#e2b714"),
        line=dict(color="#e2b714", width=2.5, shape="spline"),
        fill="tozeroy", fillcolor="rgba(226,183,20,0.08)",
        hovertemplate="<b>%{x}</b><br>ئاست: <b>%{y:,.2f}</b><extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, margin=dict(l=5, r=15, t=10, b=10), height=200,
        xaxis=dict(showgrid=False, color="#6b7280", tickfont=dict(size=9, color="#8a99ad")),
        yaxis=dict(autorange=True, showgrid=True, gridcolor="rgba(255,255,255,0.05)", color="#6b7280", side="right", tickfont=dict(size=9, color="#8a99ad")),
        hovermode="x unified",
    )
    return fig


def make_gold_dual_chart(ry_df: pd.DataFrame, exp_df: pd.DataFrame) -> go.Figure | None:
    if ry_df is None or ry_df.empty:
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ry_df["date"], y=ry_df["value"], mode="lines",
        name="Real Yield 10Y", line=dict(color="#e2b714", width=2.8, shape="spline"),
        hovertemplate="<b>%{x}</b><br>Real Yield: <b>%{y:.2f}%</b><extra></extra>",
    ))
    if exp_df is not None and not exp_df.empty:
        fig.add_trace(go.Scatter(
            x=exp_df["date"], y=exp_df["value"], mode="lines",
            name="Inflation Exp", line=dict(color="#3b82f6", width=2, dash="dot", shape="spline"),
            hovertemplate="<b>%{x}</b><br>Inflation Exp: <b>%{y:.2f}%</b><extra></extra>",
        ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True, legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=9.5, color="#8a99ad")),
        margin=dict(l=5, r=15, t=25, b=10), height=220,
        xaxis=dict(showgrid=False, color="#6b7280", tickfont=dict(size=9, color="#8a99ad")),
        yaxis=dict(autorange=True, showgrid=True, gridcolor="rgba(255,255,255,0.05)", side="right", color="#6b7280", tickfont=dict(size=9, color="#8a99ad")),
        hovermode="x unified",
    )
    return fig


def render_reference_table_html(rows: list) -> None:
    def fmt_pct(v):
        if v is None: return '<span class="ref-badge-gray">0.00%</span>'
        if v > 0: return f'<span class="ref-badge-green">▲ +{abs(v):.2f}%</span>'
        if v < 0: return f'<span class="ref-badge-red">▼ -{abs(v):.2f}%</span>'
        return '<span class="ref-badge-gray">0.00%</span>'

    tbody_rows = []
    for r in rows:
        stale_badge = ' <span style="color:#f59e0b;font-size:10px;">⚠️</span>' if r.get("is_stale") else ''
        tbody_rows.append(f"""
        <tr>
          <td class="td-name"><span style="color:#e2b714;">📈</span> {r['name']}{stale_badge}</td>
          <td class="td-val">{r['latest']:,.2f}</td>
          <td class="td-pct">{fmt_pct(r['mom'])}</td>
          <td class="td-pct">{fmt_pct(r.get('qoq'))}</td>
          <td class="td-pct">{fmt_pct(r.get('yoy'))}</td>
        </tr>
        """)

    render_html(f"""
    <div class="ref-table-card">
      <table class="ref-table">
        <thead>
          <tr>
            <th style="width:30%;">نیشاندەر</th>
            <th class="th-ctr" style="width:17%;">کۆتا</th>
            <th class="th-ctr" style="width:17%;">m/m</th>
            <th class="th-ctr" style="width:18%;">q/q</th>
            <th class="th-ctr" style="width:18%;">y/y</th>
          </tr>
        </thead>
        <tbody>{''.join(tbody_rows)}</tbody>
      </table>
      <div class="ref-table-footer">ⓘ گۆڕانکاری % بەپێی کاتی دەرچوون.</div>
    </div>
    """)


# ============================================================
# PAGE 1: 🏠 سەرەکی (DASHBOARD)
# ============================================================

def render_dashboard() -> None:
    render_html("""
    <div class="top-header-bar">
      <div class="top-brand"><span>📊</span> <span>FX MACRO &amp; GEOPOLITICAL DESK</span></div>
      <div class="top-tickers">
        <div class="ticker-pill"><span>🇺🇸 USD</span> <span class="ticker-up">Live DXY</span></div>
        <div class="ticker-pill"><span>🇪🇺 EUR</span> <span class="ticker-up">EUR/USD</span></div>
        <div class="ticker-pill"><span>🥇 Gold</span> <span class="ticker-up">XAU/USD</span></div>
      </div>
    </div>
    """)

    banner_html = """
    <div class="main-title-wrap">
      <div class="main-gold-sub">FX MACRO &amp; GEOPOLITICAL DESK</div>
      <h1 class="main-big-heading">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h1>
      <div class="main-breadcrumb">تەحلیل، تایبەتمەندی و کارکردنی بازاڕە داراییەکان</div>
    </div>
    """
    render_html(banner_html)

    c1, c2 = st.columns([1.5, 1])
    with c1:
        asset_type = st.radio("بازاڕ:", ["💱 Forex", "🥇 Gold & Metals"], horizontal=True, label_visibility="collapsed")
    with c2:
        selected = st.selectbox("دراو:", list(CURRENCY_SERIES.keys()), label_visibility="collapsed") if "Forex" in asset_type else "USD دۆلار"

    if "Gold" in asset_type:
        render_gold_page()
        return

    with st.spinner("داتاکان دەهێنرێن..."):
        result = compute_currency_composite(selected, FRED_API_KEY)

    if not result:
        st.warning("⚠️ داتا نەدۆزرایەوە.")
        return

    rows = result["rows"]
    row_map = {r["name"]: r for r in rows}
    key_inds = KEY_INDICATORS.get(selected, [r["name"] for r in rows[:4]])
    key_rows = [row_map[k] for k in key_inds if k in row_map]

    st.markdown('<div class="section-title">کۆتا داتا ڕاستەقینەکان</div>', unsafe_allow_html=True)
    cols = st.columns(len(key_rows) or 1)

    for col, row in zip(cols, key_rows):
        pos_good = row["category"] != "labor_bad"
        mom = row["mom"]
        spark = svg_spark(row["vals"][-20:], positive_is_good=pos_good)
        mom_arrow = "▲" if mom > 0 else "▼"
        mom_color = "#10b981" if (mom > 0) == pos_good else "#ef4444"
        stale_alert = " <b style='color:#f59e0b;'>(⚠️ کۆنە)</b>" if row.get("is_stale") else ""

        card_html = f"""
        <div class="metric-card">
          <div class="mc-header">
            <div class="mc-icon-wrap">{CATEGORY_ICONS.get(row['category'], '📊')}</div>
            <span class="mc-cat">{CATEGORY_LABELS.get(row['category'], '')}</span>
          </div>
          <div class="mc-name">{row['name']}{stale_alert}</div>
          <div class="mc-value">{row['latest']:,.2f}</div>
          <div class="mc-change">
            <span style="color:{mom_color};">{mom_arrow} {abs(mom):.2f}%</span>
            <span style="color:#6b7280; font-size:10px;"> (m/m)</span>
          </div>
          <div class="mc-date">📅 {row['date']}</div>
          <div style="margin-top:6px;">{spark}</div>
        </div>
        """
        with col:
            render_html(card_html)

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    t_col, c_col = st.columns([1.1, 1.2])

    with t_col:
        st.markdown('<div class="section-title">کۆتا ئاستیەکان (Multi-Timeframe Table)</div>', unsafe_allow_html=True)
        render_reference_table_html(rows)

    with c_col:
        st.markdown('<div class="section-title">کەش و هەوای بازاڕەکان (Live Chart)</div>', unsafe_allow_html=True)
        chosen_ind = st.selectbox("نیشاندەر:", [r["name"] for r in rows], label_visibility="collapsed")
        crow = row_map.get(chosen_ind, rows[0])
        fig_dyn = make_dynamic_chart(crow["df"], chosen_ind)
        if fig_dyn:
            st.plotly_chart(fig_dyn, use_container_width=True, config={"displayModeBar": False})

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    n_col, d_col = st.columns([1.1, 1.1])

    with n_col:
        st.markdown('<div class="section-title">هەواڵی جیهانی (News Feed)</div>', unsafe_allow_html=True)
        arts = fetch_news(f"{selected.split()[0]} OR forex OR economy", NEWS_API_KEY)
        if arts:
            for art in arts[:3]:
                t_str = art.get("title", "—")
                src = (art.get("source") or {}).get("name", "Desk")
                pub = (art.get("publishedAt") or "")[:10]
                link = art.get("url", "#")
                render_html(f"""
                <a href="{link}" target="_blank" style="text-decoration:none;">
                <div style="background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:10px 12px;margin-bottom:6px;">
                  <div style="color:#ffffff;font-size:12px;font-weight:600;line-height:1.4;">{t_str}</div>
                  <div style="font-size:10px;color:#8a99ad;margin-top:4px;display:flex;justify-content:space-between;"><span>📰 {src}</span><span>🕒 {pub}</span></div>
                </div></a>
                """)
        else:
            st.info("هەواڵ نەدۆزرایەوە.")

    with d_col:
        st.markdown('<div class="section-title">ئاراستەی گشتی دراو (Composite Signal)</div>', unsafe_allow_html=True)
        render_html(f"""
        <div style="background:#090e1a;border:1px solid rgba(226,183,20,0.18);border-radius:14px;padding:18px;text-align:center;">
          <div style="font-size:10.5px;font-weight:800;color:#8a99ad;margin-bottom:6px;">ئاراستەی مەکرۆی {selected}</div>
          <div style="margin:10px 0;">{badge_html(result['composite'], large=True)}</div>
          <div style="font-size:13px;font-weight:700;color:#ffffff;">Score: <span style="color:#e2b714;">{result['composite']:+.3f}</span></div>
        </div>
        """)


# ============================================================
# PAGE 2: 🥇 GOLD ANALYSIS
# ============================================================

def render_gold_page() -> None:
    header_html = """
    <div class="main-title-wrap">
      <div class="main-gold-sub">COMMODITY &amp; SAFE-HAVEN INTELLIGENCE</div>
      <h1 class="main-big-heading">شیکاری زێڕ (XAUUSD) — Real Yield &amp; USD</h1>
      <div class="main-breadcrumb">Real Yield 10Y (DGS10 - T10YIE) + USD Multi-Timeframe</div>
    </div>
    """
    render_html(header_html)

    with st.spinner("شیکاری زێڕ دەکرێت..."):
        y_df   = fetch_fred_series(GOLD_YIELD_SERIES, FRED_API_KEY, limit=36)
        i_df   = fetch_fred_series(GOLD_INFLATION_EXP_SERIES, FRED_API_KEY, limit=36)
        usd_r  = compute_currency_composite("USD دۆلار", FRED_API_KEY)

    if y_df is None or i_df is None:
        st.warning("⚠️ نەتوانرا داتاکانی DGS10 یان T10YIE بهێنرێت.")
        return

    merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
    merged["ry"] = merged["value_y"] - merged["value_i"]
    ry_vals = merged["ry"].tail(24).tolist()
    ry_dates = merged["date"].tail(24).tolist()
    ry_mf   = calc_multiframe(ry_vals, ry_dates, "rate")

    gold_ry   = -ry_mf["composite"] if ry_mf else 0.0
    gold_usd  = -usd_r["composite"] if usd_r else 0.0
    gold_score = (0.55 * gold_ry) + (0.45 * gold_usd)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Real Yield 10Y", f"{ry_vals[-1]:.2f}%", delta=f"{ry_mf['mom']:+.2f}% m/m" if ry_mf else None, delta_color="inverse")
    with c2:
        st.metric("USD Composite", f"{usd_r['composite']:+.3f}" if usd_r else "0.00")
    with c3:
        render_html(f"""
        <div style="background:#090e1a;border:1px solid rgba(226,183,20,0.2);border-radius:12px;padding:10px;text-align:center;">
          <div style="font-size:9.5px;font-weight:800;color:#8a99ad;margin-bottom:4px;">ئاراستەی گشتی زێڕ</div>
          {badge_html(gold_score, large=True)}
        </div>
        """)

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-title">نموداری گۆڕانی سوودی ڕاستەقینە (Real Yield)</div>', unsafe_allow_html=True)

    ry_df = merged[["date", "ry"]].rename(columns={"ry": "value"})
    exp_df = merged[["date", "value_i"]].rename(columns={"value_i": "value"})
    fig = make_gold_dual_chart(ry_df, exp_df)
    if fig:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ============================================================
# PAGE 3: 📅 FOREXFACTORY REAL-TIME CALENDAR
# ============================================================

def render_forexfactory_calendar() -> None:
    render_html("""
    <div class="main-title-wrap">
      <div class="main-gold-sub">REAL-TIME ECONOMIC CALENDAR</div>
      <h1 class="main-big-heading">ڕۆژژمێری ڕاستەوخۆی ForexFactory</h1>
      <div class="main-breadcrumb">داتای خێرا و فەرمی بە کاتی کوردستان (UTC+3)</div>
    </div>
    """)

    with st.spinner("ڕۆژژمێری ئابووری بار دەکرێت..."):
        df = fetch_forexfactory_calendar()

    if df.empty:
        st.warning("⚠️ پەیوەندی لەگەڵ سێرڤەری ForexFactory بەردەست نییە.")
        return

    c1, c2 = st.columns([1.5, 1])
    with c1:
        countries = ["هەموو دراوەکان"] + sorted([c for c in df["country"].dropna().unique() if c])
        sel_country = st.selectbox("فلتەری دراو:", countries, label_visibility="collapsed")
    with c2:
        sel_impact = st.radio("فلتەری گرنگی:", ["High Only (سوور)", "هەموو گرنگییەکان"], horizontal=True, label_visibility="collapsed")

    filtered_df = df.copy()
    if sel_country != "هەموو دراوەکان":
        filtered_df = filtered_df[filtered_df["country"] == sel_country]

    if "High" in sel_impact:
        filtered_df = filtered_df[filtered_df["impact"].str.lower() == "high"]

    today_str = datetime.now().strftime('%Y-%m-%d')
    unique_dates = filtered_df['date_str'].unique()

    for d_str in unique_dates:
        day_events = filtered_df[filtered_df['date_str'] == d_str]
        is_today = (d_str == today_str)
        day_label = f"📅 {d_str} {' (ئەمڕۆ)' if is_today else ''}"

        st.markdown(f'<div class="section-title">{day_label}</div>', unsafe_allow_html=True)

        for _, ev in day_events.iterrows():
            impact_val = str(ev.get('impact', '')).lower()
            impact_cls = f"impact-{impact_val}"
            border_c = "#ef4444" if impact_val == "high" else ("#f59e0b" if impact_val == "medium" else "#6b7280")

            actual = str(ev.get('actual', '')) or '—'
            forecast = str(ev.get('forecast', '')) or '—'
            previous = str(ev.get('previous', '')) or '—'

            act_color = "#e5e7eb"
            if actual != '—' and forecast != '—' and actual != '' and forecast != '':
                try:
                    act_num = float(actual.replace('%', '').replace('K', '').replace('M', '').replace('B', ''))
                    fc_num = float(forecast.replace('%', '').replace('K', '').replace('M', '').replace('B', ''))
                    act_color = "#10b981" if act_num >= fc_num else "#ef4444"
                except ValueError:
                    pass

            card_html = f"""
            <div class="ff-card" style="border-right: 4px solid {border_c};">
              <div class="ff-time-badge">{ev['time_str']}</div>
              <div class="ff-content">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
                  <span class="ff-name"><b style="color:#e2b714;">{ev.get('country', '')}</b> — {ev.get('title', '')}</span>
                  <span class="ff-impact-badge {impact_cls}">{impact_val.upper()}</span>
                </div>
                <div style="margin-top:6px; font-size:11.5px; display:flex; gap:14px; color:#8a99ad; flex-wrap:wrap;">
                  <span>Actual: <b style="color:{act_color}; font-size:12.5px;">{actual}</b></span>
                  <span>Forecast: <b style="color:#ffffff;">{forecast}</b></span>
                  <span>Previous: <b style="color:#6b7280;">{previous}</b></span>
                </div>
              </div>
            </div>
            """
            render_html(card_html)


# ============================================================
# MAIN ROUTER
# ============================================================

def main() -> None:
    # ── Auto-Refresh: هەر 60 چرکە جارێک هەموو پەڕەکە نوێ دەبێتەوە ──
    st_autorefresh(interval=60 * 1000, key="auto_refresh_counter")

    inject_css()

    with st.sidebar:
        render_html("""
        <div style="padding:10px 4px;border-bottom:1px solid rgba(255,255,255,0.06);margin-bottom:10px;">
          <div style="font-size:12px;font-weight:800;color:#e2b714;">FX MACRO DESK</div>
        </div>
        """)

        page = st.radio(
            "دەستەی بەڕێوەبردن:",
            ["🏠 سەرەکی", "🥇 Gold (XAUUSD)", "📅 ForexFactory Calendar"],
            label_visibility="collapsed",
        )

        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        render_html("""
        <div style="background:#090e1a;border:1px solid rgba(16,185,129,0.2);border-radius:10px;padding:8px 12px;text-align:center;">
          <span style="color:#10b981;font-size:11px;font-weight:700;">🟢 Live Auto-Sync Active (60s)</span>
        </div>
        """)

    if page == "🏠 سەرەکی":
        render_dashboard()
    elif page == "🥇 Gold (XAUUSD)":
        render_gold_page()
    elif page == "📅 ForexFactory Calendar":
        render_forexfactory_calendar()

    render_html(f"""
    <div class="app-footer">
      <div>© 2026 FX Macro Desk</div>
      <div class="live-status"><span class="live-dot"></span><span>Auto-Sync Live &nbsp; {datetime.now().strftime('%H:%M:%S')}</span></div>
    </div>
    """)


if __name__ == "__main__":
    main()
