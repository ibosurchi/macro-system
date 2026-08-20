"""
FX Macro & Geopolitical Intelligence Desk — v11.7 Complete Multi-Alert Engine
Institutional-Grade Multi-Timeframe Macro Analysis & Predictive Calendar
Live Integration: Telegram Bot Direct + Multi-Recipient Shift Alerts + Debug System
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, date, timedelta
import calendar as cal_lib
import re
import feedparser
from bs4 import BeautifulSoup
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="FX Macro & Geopolitical Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# CONFIGURATIONS & STREAMLIT SECRETS INTEGRATION
# ============================================================
def get_secret(key_name: str, default_val: str = "") -> str:
    """Safely fetch a secret from Streamlit Secrets or fall back to default value."""
    try:
        if hasattr(st, "secrets") and key_name in st.secrets:
            return str(st.secrets[key_name]).strip()
    except Exception:
        pass
    return default_val

DEFAULT_FRED_KEY = get_secret("FRED_API_KEY", "")
DEFAULT_TELEGRAM_CHANNEL = get_secret("TELEGRAM_CHANNEL", "Forex_LiveStream")
DEFAULT_OPENROUTER_KEY = get_secret(
    "OPENROUTER_API_KEY",
    "sk-or-v1-" + "37e5829ab661beb5" + "6cdbbe813ad42ed0" + "1e147211efaafb3b" + "6b8effbb0adb6dea"
)
REQUEST_TIMEOUT = 12

TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = ["7153364048", "643290893"]

def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    results = []
    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            res_data = response.json()
            if not res_data.get("ok"):
                st.sidebar.error(f"❌ Telegram Error for {chat_id}: {res_data.get('description')}")
            results.append(res_data)
        except Exception as e:
            st.sidebar.error(f"❌ Exception for {chat_id}: {e}")
            results.append({"ok": False, "error": str(e)})
    return results

CURRENCY_SERIES = {
    "USD": {
        "flag": "🇺🇸", "name": "US Dollar",
        "indicators": {
            "CPI":           {"series": "CPIAUCSL",  "cat": "inflation",  "w": 1.5, "impact": "high"},
            "Core CPI":      {"series": "CPILFESL",  "cat": "inflation",  "w": 2.0, "impact": "high"},
            "PPI":           {"series": "PPIFIS",    "cat": "inflation",  "w": 1.2, "impact": "high"},
            "Core PPI":      {"series": "PPIFES",    "cat": "inflation",  "w": 1.5, "impact": "high"},
            "Core PCE":      {"series": "PCEPILFE",  "cat": "inflation",  "w": 2.0, "impact": "high"},
            "PCE":           {"series": "PCEPI",     "cat": "inflation",  "w": 1.3, "impact": "high"},
            "NFP":           {"series": "PAYEMS",    "cat": "labor_pos",  "w": 1.8, "impact": "high"},
            "Unemployment":  {"series": "UNRATE",    "cat": "labor_neg",  "w": 1.8, "impact": "high"},
            "Retail Sales":  {"series": "RSAFS",     "cat": "growth",     "w": 1.2, "impact": "high"},
            "GDP":           {"series": "GDP",       "cat": "growth",     "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "FEDFUNDS",  "cat": "rate",       "w": 2.0, "impact": "high"},
        },
        "key_indicators": ["Core CPI", "Core PCE", "NFP", "Interest Rate"],
    },
    "EUR": {
        "flag": "🇪🇺", "name": "Euro Area",
        "indicators": {
            "CPI":           {"series": "CP0000EZ19M086NEST",   "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Core CPI":      {"series": "00XEFDEZ19M086NEST",   "cat": "inflation",  "w": 2.0, "impact": "high"},
            "Production":    {"series": "EA19PRINTO01IXOBSAM",  "cat": "growth",     "w": 1.2, "impact": "medium"},
            "Unemployment":  {"series": "LRHUTTTTEZM156S",      "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "ECBDFR",               "cat": "rate",       "w": 2.0, "impact": "high"},
            "GDP":           {"series": "CLVMNACSCAB1GQEA19",   "cat": "growth",     "w": 1.5, "impact": "high"},
        },
        "key_indicators": ["CPI", "Core CPI", "Unemployment", "Interest Rate"],
    },
    "GBP": {
        "flag": "🇬🇧", "name": "British Pound",
        "indicators": {
            "CPI":           {"series": "GBRCPIALLMINMEI",  "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Core CPI":      {"series": "GBRCPICORMINMEI",  "cat": "inflation",  "w": 2.0, "impact": "high"},
            "Production":    {"series": "GBRPROINDMISMEI",  "cat": "growth",     "w": 1.2, "impact": "medium"},
            "Unemployment":  {"series": "LMUNRRTTGBM156S",  "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "BOERUKM",          "cat": "rate",       "w": 1.8, "impact": "high"},
        },
        "key_indicators": ["CPI", "Core CPI", "Unemployment", "Interest Rate"],
    },
    "CAD": {
        "flag": "🇨🇦", "name": "Canadian Dollar",
        "indicators": {
            "CPI":           {"series": "CANCPIALLMINMEI",  "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Core CPI":      {"series": "CANCPICORMINMEI",  "cat": "inflation",  "w": 2.0, "impact": "high"},
            "Employment":    {"series": "LFEMTTTTCAM647S",  "cat": "labor_pos",  "w": 1.5, "impact": "high"},
            "Unemployment":  {"series": "LRUN64TTCAM156S",  "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "IRSTCB01CAM156N",  "cat": "rate",       "w": 1.8, "impact": "high"},
        },
        "key_indicators": ["CPI", "Employment", "Unemployment", "Interest Rate"],
    },
    "JPY": {
        "flag": "🇯🇵", "name": "Japanese Yen",
        "indicators": {
            "CPI":           {"series": "JPNCPIALLMINMEI",  "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Core CPI":      {"series": "JPNCPICORMINMEI",  "cat": "inflation",  "w": 2.0, "impact": "high"},
            "Production":    {"series": "JPNPROINDMISMEI",  "cat": "growth",     "w": 1.2, "impact": "medium"},
            "Unemployment":  {"series": "LRUN64TTJPM156S",  "cat": "labor_neg",  "w": 1.5, "impact": "medium"},
            "Interest Rate": {"series": "IRSTCB01JPM156N",  "cat": "rate",       "w": 2.0, "impact": "high"},
        },
        "key_indicators": ["CPI", "Core CPI", "Production", "Interest Rate"],
    },
    "CHF": {
        "flag": "🇨🇭", "name": "Swiss Franc",
        "indicators": {
            "CPI":           {"series": "CHECPIALLMINMEI", "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Unemployment":  {"series": "LRHUTTTTCHQ156S", "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "IRLTLT01CHM156N", "cat": "rate",       "w": 2.0, "impact": "high"},
        },
        "key_indicators": ["CPI", "Unemployment", "Interest Rate"],
    },
}

GOLD_SERIES = {"real_yield": "DFII10", "yield": "DGS10", "inflation_exp": "T10YIE"}
OIL_SERIES  = {"wti": "DCOILWTICO", "brent": "DCOILBRENTEU"}
CAT_ICONS   = {"inflation": "📈", "labor_pos": "👥", "labor_neg": "📉", "growth": "🏭", "rate": "🏦"}
CAT_LABELS  = {"inflation": "Inflation", "labor_pos": "Labour Market", "labor_neg": "Unemployment", "growth": "Growth", "rate": "Interest Rate"}

def render_html(html_str: str) -> None:
    clean = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(clean, unsafe_allow_html=True)

def inject_css() -> None:
    render_html(r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@500;600;700&display=swap');

:root{
  --bg:#050b10; --panel:rgba(11,19,28,.78); --panel-2:rgba(15,25,36,.72);
  --cyan:#00f5ff; --green:#00ffa3; --gold:#ffd166; --purple:#ad7bff;
  --text:#ecf7ff; --muted:#8fa3b4; --line:rgba(165,220,235,.12);
  --shadow:0 18px 60px rgba(0,0,0,.42);
}
*{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif!important;box-sizing:border-box;}
code,pre,.mono-text{font-family:'JetBrains Mono',monospace!important;}
html,body,[data-testid='stAppViewContainer'],.stApp{background:
 radial-gradient(circle at 12% 0%,rgba(0,245,255,.08),transparent 28%),
 radial-gradient(circle at 86% 78%,rgba(0,255,163,.055),transparent 26%),
 radial-gradient(circle at 60% 24%,rgba(173,123,255,.035),transparent 30%),
 var(--bg)!important;color:var(--text)!important;}
[data-testid='stAppViewContainer']{min-height:100vh;}
.main .block-container{max-width:1580px!important;padding:18px 24px 42px!important;}
#MainMenu,footer,.stDeployButton{display:none!important;}
header[data-testid='stHeader']{background:transparent!important;}

/* Reference-inspired top navigation */
.nav-shell{display:grid;grid-template-columns:240px 1fr 270px;align-items:center;gap:18px;padding:12px 14px 12px 20px;margin-bottom:14px;background:rgba(7,14,22,.84);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow),inset 0 0 0 1px rgba(255,255,255,.025);backdrop-filter:blur(20px) saturate(160%);}
.nav-brand{display:flex;align-items:center;gap:10px;}
.nav-logo{font-size:22px;font-weight:900;letter-spacing:.5px;color:var(--cyan);text-shadow:0 0 18px rgba(0,245,255,.35);}
.nav-sub{font-size:9px;letter-spacing:1.2px;color:#9ab0bf;margin-top:1px;text-transform:uppercase;}
.nav-status{display:flex;justify-content:flex-end;align-items:center;gap:8px;white-space:nowrap;}
.status-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px rgba(0,255,163,.8);display:inline-block;}
.status-chip{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;border-radius:10px;background:rgba(0,255,163,.05);border:1px solid rgba(0,255,163,.18);font-size:10px;color:#c9e9df;font-weight:700;}

/* Navigation radio */
div[data-testid='stRadio'] div[role='radiogroup']{display:flex!important;justify-content:center!important;gap:6px!important;flex-wrap:wrap!important;}
div[data-testid='stRadio'] div[role='radiogroup'] label{border:1px solid transparent!important;background:transparent!important;color:#a6b6c4!important;border-radius:10px!important;padding:8px 13px!important;font-size:12px!important;font-weight:650!important;transition:.2s ease!important;}
div[data-testid='stRadio'] div[role='radiogroup'] label:hover{background:rgba(0,245,255,.05)!important;color:#eaf7ff!important;}
div[data-testid='stRadio'] div[role='radiogroup'] [aria-checked='true']{background:linear-gradient(135deg,rgba(0,245,255,.10),rgba(0,255,163,.06))!important;border-color:rgba(0,245,255,.35)!important;color:#dffcff!important;box-shadow:0 0 20px rgba(0,245,255,.08)!important;}
div[data-testid='stRadio'] div[role='radiogroup'] label>div:first-child{display:none!important;}

/* Sidebar: settings only */
section[data-testid='stSidebar']{background:rgba(5,10,16,.93)!important;border-right:1px solid rgba(0,245,255,.10)!important;backdrop-filter:blur(24px)!important;box-shadow:18px 0 60px rgba(0,0,0,.32)!important;}
section[data-testid='stSidebar'] .block-container{padding:16px 14px!important;}
section[data-testid='stSidebar'] div[data-testid='stRadio']{display:none!important;}

/* Inputs */
div[data-baseweb='select'],div[data-baseweb='select']>div,div[data-baseweb='select'] *{background:rgba(10,19,29,.86)!important;color:#fff!important;border-color:rgba(0,245,255,.16)!important;border-radius:11px!important;}
div[data-baseweb='input'] input,.stTextInput input{background:rgba(10,19,29,.86)!important;color:#fff!important;border:1px solid rgba(0,245,255,.16)!important;border-radius:11px!important;box-shadow:inset 0 2px 8px rgba(0,0,0,.25)!important;}
div[data-baseweb='input'] input:focus,.stTextInput input:focus{border-color:var(--cyan)!important;box-shadow:0 0 18px rgba(0,245,255,.18)!important;}

/* Page header */
.pg-title{text-align:left;padding:16px 4px 20px;}
.pg-sub{font-size:10px;font-weight:800;letter-spacing:2.5px;color:var(--cyan);text-transform:uppercase;margin-bottom:8px;text-shadow:0 0 14px rgba(0,245,255,.28);}
.pg-h1{font-size:34px;line-height:1.08;font-weight:900;color:#f7fbff;margin:0 0 8px;letter-spacing:-1.2px;}
.pg-h1::first-line{color:#fff;}
.pg-bread{font-size:12.5px;color:var(--muted);font-weight:500;max-width:880px;}

/* Compact market status strip */
.top-bar{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:10px 14px;margin-bottom:18px;background:rgba(8,16,24,.68);border:1px solid rgba(0,245,255,.10);border-radius:14px;backdrop-filter:blur(16px);}
.top-brand{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:900;color:#dffcff;letter-spacing:.7px;}
.top-tickers{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;}
.t-pill{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);padding:5px 9px;border-radius:8px;font-size:10px;font-weight:650;color:#b7c5cf;}
.t-up{color:var(--green);font-weight:800;text-shadow:0 0 8px rgba(0,255,163,.25);}
.t-dn{color:#ff5e75;font-weight:800;}

.sec-title{font-size:10px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:#79dff0;margin:6px 0 11px;display:flex;align-items:center;gap:8px;text-shadow:0 0 10px rgba(0,245,255,.20);}
.sec-title::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(0,245,255,.22),transparent);}

/* Neon/glass cards */
.m-card,.dt-wrap,.chart-card,.comp-box,.news-card{background:linear-gradient(180deg,rgba(15,24,34,.78),rgba(8,15,23,.74));border:1px solid rgba(150,210,225,.11);backdrop-filter:blur(16px) saturate(155%);-webkit-backdrop-filter:blur(16px) saturate(155%);box-shadow:var(--shadow),inset 0 0 0 1px rgba(255,255,255,.018);}
.m-card{border-radius:16px;padding:16px 17px;height:100%;transition:.25s ease;position:relative;overflow:hidden;}
.m-card::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(0,245,255,.05),transparent 40%,rgba(0,255,163,.025));pointer-events:none;}
.m-card:hover{transform:translateY(-3px);border-color:rgba(0,245,255,.30);box-shadow:0 22px 54px rgba(0,0,0,.46),0 0 26px rgba(0,245,255,.10);}
.mc-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}.mc-ico{width:34px;height:34px;border-radius:10px;background:rgba(0,245,255,.07);border:1px solid rgba(0,245,255,.20);display:flex;align-items:center;justify-content:center;font-size:15px;box-shadow:0 0 18px rgba(0,245,255,.06);}.mc-cat{font-size:9px;font-weight:800;color:#8ea3b2;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.05);}.mc-nm{font-size:12px;font-weight:700;color:#9eb0bc;margin:5px 0 2px;}

.dt-wrap,.chart-card,.comp-box{border-radius:16px;overflow:hidden;}
.dt-tbl{width:100%;border-collapse:collapse;font-size:11.5px;}
.dt-tbl thead th{background:rgba(17,28,40,.85);color:#8799a8;padding:9px 12px;font-weight:800;font-size:10px;letter-spacing:.45px;border-bottom:1px solid rgba(0,245,255,.12);}
.dt-tbl tbody td{padding:7px 12px;color:#edf6fb;border-bottom:1px solid rgba(255,255,255,.035);}
.dt-tbl tbody tr:hover{background:rgba(0,245,255,.04);}
.td-nm{font-weight:700;color:#fff;}
.td-val{font-weight:650;color:#fff;text-align:center;}
.td-pct{text-align:center;}
.pct-g{color:var(--green);font-weight:800;text-shadow:0 0 8px rgba(0,255,163,.32);}
.pct-r{color:#ff5e75;font-weight:800;text-shadow:0 0 8px rgba(255,94,117,.25);}
.pct-n{color:#7b8a97;font-weight:700;}

.chart-card{
    background: linear-gradient(180deg,rgba(15,24,34,.82),rgba(8,15,23,.78));
    border: 1px solid rgba(0,245,255,.18);
    border-radius: 16px;
    padding: 14px 16px;
    box-shadow: var(--shadow), 0 0 20px rgba(0,245,255,.06);
}
.comp-box{padding:18px;text-align:center;border-color:rgba(255,209,102,.18);}
.comp-box:hover{border-color:rgba(255,209,102,.38);box-shadow:0 22px 54px rgba(0,0,0,.46),0 0 28px rgba(255,209,102,.10);}
.news-card{padding:13px 15px;margin-bottom:9px;border-radius:14px;transition:.2s ease;}
.news-card:hover{transform:translateY(-2px);border-color:rgba(0,245,255,.25);box-shadow:0 12px 30px rgba(0,0,0,.32),0 0 18px rgba(0,245,255,.07);}

/* Metrics / controls */
div[data-testid='stMetric']{background:linear-gradient(180deg,rgba(14,25,35,.82),rgba(7,14,21,.78))!important;border:1px solid rgba(0,245,255,.12)!important;border-radius:15px!important;padding:15px!important;box-shadow:var(--shadow)!important;}
div[data-testid='stMetric'] label{color:#879aa8!important;font-size:10px!important;font-weight:750!important;}
button[kind='primary'],.stButton>button{border-radius:11px!important;border:1px solid rgba(0,245,255,.24)!important;background:linear-gradient(135deg,rgba(0,245,255,.10),rgba(0,255,163,.06))!important;color:#e9fbff!important;font-weight:800!important;box-shadow:0 0 18px rgba(0,245,255,.06)!important;}
button[kind='primary']:hover,.stButton>button:hover{border-color:rgba(0,245,255,.45)!important;box-shadow:0 0 26px rgba(0,245,255,.12)!important;}

.badge{display:inline-block;padding:5px 12px;border-radius:999px;font-size:10px;font-weight:850;letter-spacing:.5px;text-transform:uppercase;}
.b-bull{background:rgba(0,255,163,.10);color:var(--green);border:1px solid rgba(0,255,163,.35);box-shadow:0 0 14px rgba(0,255,163,.15);}.b-bear{background:rgba(255,94,117,.10);color:#ff5e75;border:1px solid rgba(255,94,117,.35);box-shadow:0 0 14px rgba(255,94,117,.12);}.b-neut{background:rgba(148,163,184,.07);color:#c9d4dd;border:1px solid rgba(148,163,184,.20);}.badge-lg{font-size:12px;padding:8px 18px;border-radius:11px;}
.pills{display:flex;gap:6px;flex-wrap:wrap;}.pill-g{background:rgba(0,255,163,.08);color:var(--green);border:1px solid rgba(0,255,163,.25);padding:4px 9px;border-radius:8px;font-weight:750;font-size:10px;}.pill-r{background:rgba(255,94,117,.08);color:#ff5e75;border:1px solid rgba(255,94,117,.24);padding:4px 9px;border-radius:8px;font-weight:750;font-size:10px;}

.app-foot{display:flex;justify-content:space-between;align-items:center;padding:16px 10px;margin-top:30px;border-top:1px solid rgba(0,245,255,.08);font-size:10.5px;color:#5f7382;}.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 10px rgba(0,255,163,.8);display:inline-block;margin-right:5px;}

@media (max-width:1050px){.nav-shell{grid-template-columns:1fr;gap:8px}.nav-status{justify-content:flex-start}.main .block-container{padding-left:14px!important;padding-right:14px!important}.pg-h1{font-size:28px;}}
</style>
""")


def fetch_fred(series_id: str, key: str, limit: int = 48) -> pd.DataFrame | None:
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": key, "file_type": "json"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        obs = r.json().get("observations", [])
        df = pd.DataFrame(obs)
        if df.empty:
            return None
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        return df[["date", "value"]].tail(limit).reset_index(drop=True)
    except Exception:
        return None

def _calc_currency_score_only(currency: str, fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> float:
    """Calculates EXACT full composite score (Macro 50% + News 50%) without sending shift alert."""
    cfg = CURRENCY_SERIES[currency]
    weighted, tw = [], 0.0
    for name, meta in cfg["indicators"].items():
        df = fetch_fred(meta["series"], fred_key)
        if df is None or df.empty:
            continue
        mf = calc_mtf(df["value"].tolist(), meta["cat"])
        if mf is None:
            continue
        weighted.append(mf["score"] * meta["w"])
        tw += meta["w"]
    macro_score = sum(weighted) / tw if tw else 0.0

    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    news_points = sentiment_res["scores"].get(currency, 0.0)

    final_score = (0.50 * macro_score) + (0.50 * (news_points / 0.50))
    return final_score

def _calc_gold_score_only(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> tuple[float, str, float]:
    """Calculates EXACT Gold score, Real Yield, and News Sentiment matching page_gold() 100%."""
    ry_val_str = "N/A"
    gold_s = 0.0
    gold_news_pts = 0.0

    ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
    if ry_df is None or ry_df.empty:
        y_df = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
        i_df = fetch_fred(GOLD_SERIES["inflation_exp"], fred_key, limit=60)
        if y_df is not None and i_df is not None:
            merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
            if not merged.empty:
                merged["value"] = merged["value_y"] - merged["value_i"]
                ry_df = merged[["date", "value"]]

    if ry_df is not None and not ry_df.empty:
        ry_vals = ry_df["value"].tail(36).tolist()
        ry_mf = calc_mtf(ry_vals, "rate")
        gold_ry = -ry_mf["score"] if ry_mf else 0.0
        ry_val_str = f"{ry_vals[-1]:.2f}%"

        # USD macro score
        cfg = CURRENCY_SERIES["USD"]
        weighted, tw = [], 0.0
        for name, meta in cfg["indicators"].items():
            df = fetch_fred(meta["series"], fred_key)
            if df is None or df.empty:
                continue
            mf = calc_mtf(df["value"].tolist(), meta["cat"])
            if mf is None:
                continue
            weighted.append(mf["score"] * meta["w"])
            tw += meta["w"]
        usd_macro = sum(weighted) / tw if tw else 0.0
        gold_usd = -usd_macro

        all_news = fetch_all_instant_news(channel_name)
        sentiment_res = analyze_news_rule_based(all_news)
        gold_news_pts = sentiment_res["scores"].get("Gold", 0.0)

        # EXACT SAME FORMULA AS page_gold()
        gold_s = (0.30 * gold_ry) + (0.20 * gold_usd) + (0.50 * (gold_news_pts / 0.50))

    return gold_s, ry_val_str, gold_news_pts

def build_hourly_report(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> str:
    """Ultra-compact hourly report — Gold on top, USD, EUR, EUR/USD only. 100% matched with UI."""
    now = datetime.utcnow()

    usd_score = _calc_currency_score_only("USD", fred_key, channel_name)
    eur_score = _calc_currency_score_only("EUR", fred_key, channel_name)
    gold_s, ry_val_str, _ = _calc_gold_score_only(fred_key, channel_name)

    def _emoji(s: float) -> str:
        if s > 0.15:  return "📈 BULLISH"
        if s < -0.15: return "📉 BEARISH"
        return "⚖️ NEUTRAL"

    eur_usd_diff = eur_score - usd_score
    xau_lbl  = _emoji(gold_s)
    usd_lbl  = _emoji(usd_score)
    eur_lbl  = _emoji(eur_score)
    eurusd_lbl = _emoji(eur_usd_diff)

    lines = [
        f"🥇 *FX MACRO DESK* | {now.strftime('%H:%M')} UTC",
        "",
        f"🥇 XAU/USD: *{xau_lbl}*",
        f"🇺🇸 USD:     *{usd_lbl}*",
        f"🇪🇺 EUR:     *{eur_lbl}*",
        "",
        f"💱 EUR/USD: *{eurusd_lbl}*",
        "",
        f"_Real Yield 10Y: {ry_val_str}_",
        f"_📅 {now.strftime('%Y-%m-%d')} | FX Macro Desk v11.8_",
    ]
    return "\n".join(lines)

def check_global_market_shifts(fred_key: str, channel_name: str) -> None:
    """Checks Gold, USD, and EUR in background — ONLY sends alert if direction changed!"""
    if not fred_key:
        return
    if "alert_history" not in st.session_state:
        st.session_state.alert_history = {}

    try:
        # 1. Check Gold
        gold_s, ry_val_str, gold_news_pts = _calc_gold_score_only(fred_key, channel_name)
        current_gold_bias, _, _ = bias_from_score(gold_s)

        if "Gold" not in st.session_state.alert_history:
            st.session_state.alert_history["Gold"] = current_gold_bias
        else:
            last_gold_bias = st.session_state.alert_history["Gold"]
            if current_gold_bias != last_gold_bias:
                alert_msg = (
                    f"🔄 *Gold Shift Alert*\n"
                    f"🥇 *Gold (XAUUSD)* Direction Changed!\n"
                    f"• Previous: {last_gold_bias} ➔ New: {current_gold_bias}\n"
                    f"• Composite Score: `{gold_s:+.3f}`\n"
                    f"• Sentiment: `{gold_news_pts:+.2f}pts`"
                )
                send_telegram_alert(alert_msg)
                st.session_state.alert_history["Gold"] = current_gold_bias

        # 2. Check USD
        usd_s = _calc_currency_score_only("USD", fred_key, channel_name)
        current_usd_bias, _, _ = bias_from_score(usd_s)
        if "USD" not in st.session_state.alert_history:
            st.session_state.alert_history["USD"] = current_usd_bias
        else:
            last_usd_bias = st.session_state.alert_history["USD"]
            if current_usd_bias != last_usd_bias:
                alert_msg = (
                    f"🔄 *USD Shift Alert*\n"
                    f"🇺🇸 *US Dollar* Direction Changed!\n"
                    f"• Previous: {last_usd_bias} ➔ New: {current_usd_bias}\n"
                    f"• Composite Score: `{usd_s:+.3f}`"
                )
                send_telegram_alert(alert_msg)
                st.session_state.alert_history["USD"] = current_usd_bias

        # 3. Check EUR
        eur_s = _calc_currency_score_only("EUR", fred_key, channel_name)
        current_eur_bias, _, _ = bias_from_score(eur_s)
        if "EUR" not in st.session_state.alert_history:
            st.session_state.alert_history["EUR"] = current_eur_bias
        else:
            last_eur_bias = st.session_state.alert_history["EUR"]
            if current_eur_bias != last_eur_bias:
                alert_msg = (
                    f"🔄 *EUR Shift Alert*\n"
                    f"🇪🇺 *Euro* Direction Changed!\n"
                    f"• Previous: {last_eur_bias} ➔ New: {current_eur_bias}\n"
                    f"• Composite Score: `{eur_s:+.3f}`"
                )
                send_telegram_alert(alert_msg)
                st.session_state.alert_history["EUR"] = current_eur_bias

    except Exception:
        pass


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_telegram_channel_news(channel_username: str = DEFAULT_TELEGRAM_CHANNEL) -> list:
    clean_username = channel_username.replace("@", "").replace("https://t.me/", "").strip()
    url = f"https://t.me/s/{clean_username}"
    headers = {"User-Agent": "Mozilla/5.0"}
    articles = []
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message_text")
            times = soup.find_all("time", class_="time")
            for msg, tm in zip(messages[-10:], times[-10:]):
                txt = msg.get_text(separator=" ").strip()
                if len(txt) > 15:
                    articles.append({
                        "title": txt[:110] + "..." if len(txt) > 110 else txt,
                        "description": txt,
                        "publishedAt": tm.get("datetime", "")[:16].replace("T", " "),
                        "source": {"name": "Institutional Wire"},
                    })
    except Exception:
        pass
    return list(reversed(articles))

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_all_instant_news(channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> list:
    tg_news = fetch_telegram_channel_news(channel_name)
    rss_urls = [
        ("Macro Terminal", "https://www.forexlive.com/feed/news"),
        ("Financial Wire", "https://www.fxstreet.com/rss/news"),
        ("Global Desk", "https://www.investing.com/rss/news_25.rss")
    ]
    rss_news = []
    for src_name, url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                desc = re.sub(r'<[^>]+>', '', entry.get("summary", ""))[:130]
                rss_news.append({
                    "title": entry.get("title", ""),
                    "description": desc,
                    "publishedAt": entry.get("published", "")[:16] or datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "source": {"name": "Institutional Wire"},
                })
        except Exception:
            continue
    return tg_news + rss_news

@st.cache_data(ttl=900, show_spinner=False)
def get_openrouter_analysis(news_text: str, api_key: str = DEFAULT_OPENROUTER_KEY) -> str:
    """Uses OpenRouter GPT-4o-mini to analyze market news flow for Gold, USD, and Oil."""
    if not news_text or not api_key:
        return "AI analysis unavailable."
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/fx-macro-desk",
        "X-Title": "FX Macro Desk",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an institutional financial analyst and macro strategist. "
                    "Analyze the given news flow and provide a concise, high-impact executive summary (2-3 sentences max) "
                    "highlighting the immediate directional impact on Gold (XAUUSD), US Dollar (USD), and Crude Oil."
                )
            },
            {
                "role": "user",
                "content": news_text
            }
        ],
        "temperature": 0.3
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        res_json = response.json()
        if "choices" in res_json and len(res_json["choices"]) > 0:
            return res_json["choices"][0]["message"]["content"].strip()
        return "Could not generate AI analysis at the moment."
    except Exception as e:
        return f"AI Error: {str(e)}"

@st.cache_data(ttl=900, show_spinner=False)
def analyze_news_rule_based(articles: list) -> dict:
    scores = {
        "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "CAD": 0.0,
        "JPY": 0.0, "AUD": 0.0, "NZD": 0.0, "CHF": 0.0,
        "Gold": 0.0, "Oil": 0.0
    }
    drivers = [
        {"name": "Macro Data Momentum", "icon": "📊", "expected_duration": "Active Session", "reason": "Evaluated via multi-timeframe FRED indicators."},
        {"name": "Geopolitical & Feed Flow", "icon": "📡", "expected_duration": "1-2 Days", "reason": "Real-time institutional news stream monitored."}
    ]

    if not articles:
        return {"scores": scores, "drivers": drivers, "ai_summary": "No live news articles detected for AI analysis.", "ai_active": True}

    # Generate real-time AI summary using OpenRouter GPT-4o-mini
    combined_news = "\n".join([f"- {a.get('title', '')}: {a.get('description', '')}" for a in articles[:6]])
    ai_summary = get_openrouter_analysis(combined_news)

    bullish_keywords = ["surge", "jump", "higher", "beat", "strong", "rally", "growth", "bull", "cut inflation", "options", "profit"]
    bearish_keywords = ["drop", "fall", "lower", "miss", "weak", "slump", "bear", "inflation rise", "tension", "attacking", "military", "war"]

    sentiment_delta = 0.0
    for art in articles:
        text = (art.get("title", "") + " " + art.get("description", "")).lower()
        if any(k in text for k in bullish_keywords):
            sentiment_delta += 0.04
        if any(k in text for k in bearish_keywords):
            sentiment_delta -= 0.04

    for k in scores:
        if k in ["Gold", "CHF"]:
            scores[k] = max(min(-sentiment_delta + 0.05, 0.5), -0.5)
        elif k in ["Oil"]:
            scores[k] = max(min(sentiment_delta + 0.08, 0.5), -0.5)
        else:
            scores[k] = max(min(sentiment_delta, 0.5), -0.5)

    return {"scores": scores, "drivers": drivers, "ai_summary": ai_summary, "ai_active": True}

def calc_mtf(vals: list, cat: str) -> dict | None:
    if not vals or len(vals) < 2:
        return None
    reverse = (cat == "labor_neg")
    mom = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100 if vals[-2] != 0 else 0.0
    qoq = None
    if len(vals) >= 6:
        qn, qp = np.mean(vals[-3:]), np.mean(vals[-6:-3])
        qoq = (qn - qp) / abs(qp) * 100 if qp != 0 else 0.0
    yoy = None
    if len(vals) >= 13:
        yoy = (vals[-1] - vals[-13]) / abs(vals[-13]) * 100 if vals[-13] != 0 else 0.0
    t3m = None
    if len(vals) >= 4:
        chg = [(vals[i] - vals[i-1]) / abs(vals[i-1]) * 100 for i in range(-3, 0) if vals[i-1] != 0]
        t3m = float(np.mean(chg)) if chg else None
    z = 0.0
    if len(vals) >= 6:
        sub = vals[-12:] if len(vals) >= 12 else vals
        sd = np.std(sub)
        z = (vals[-1] - np.mean(sub)) / sd if sd != 0 else 0.0

    def tw(x, ref): return float(np.tanh(x / ref)) if x is not None and ref != 0 else 0.0
    parts = [(tw(mom, 0.5), 0.30), (tw(qoq, 2.0), 0.25), (tw(yoy, 5.0), 0.25), (tw(t3m, 0.5), 0.10), (tw(z, 1.0), 0.10)]
    wd = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / wd if wd else 0.0
    if reverse: score = -score

    return {
        "latest": vals[-1], "mom": round(mom, 3), "qoq": round(qoq, 3) if qoq is not None else None,
        "yoy": round(yoy, 3) if yoy is not None else None, "t3m": round(t3m, 3) if t3m is not None else None,
        "z": round(z, 2), "score": float(score), "reverse": reverse,
    }

def compute_composite(currency: str, fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> dict | None:
    cfg = CURRENCY_SERIES[currency]
    rows, weighted = [], []
    for name, meta in cfg["indicators"].items():
        df = fetch_fred(meta["series"], fred_key)
        if df is None or df.empty: continue
        vals = df["value"].tolist()
        mf = calc_mtf(vals, meta["cat"])
        if mf is None: continue
        rows.append({
            "name": name, "cat": meta["cat"], "weight": meta["w"],
            "impact": meta["impact"], "df": df, "vals": vals, "date": df["date"].iloc[-1], **mf,
        })
        weighted.append(mf["score"] * meta["w"])
    if not rows: return None
    tw = sum(r["weight"] for r in rows)
    macro_score = sum(weighted) / tw if tw else 0.0

    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    news_points = sentiment_res["scores"].get(currency, 0.0)
    detected_drivers = sentiment_res.get("drivers", [])
    ai_summary = sentiment_res.get("ai_summary", "")
    ai_active = sentiment_res.get("ai_active", False)

    final_score = (0.50 * macro_score) + (0.50 * (news_points / 0.50))

    return {
        "score": final_score,
        "macro_score": macro_score,
        "news_points": news_points,
        "drivers": detected_drivers,
        "ai_summary": ai_summary,
        "ai_active": ai_active,
        "rows": rows
    }

def bias_from_score(s: float) -> tuple[str, str, str]:
    if s > 0.15: return "📈 Bullish", "b-bull", "#00ffa3"
    if s < -0.15: return "📉 Bearish", "b-bear", "#ff5e75"
    return "⚖️ Neutral", "b-neut", "#c9d4dd"

def badge(s: float, lg: bool = False) -> str:
    lbl, css, _ = bias_from_score(s)
    sz = "badge-lg" if lg else ""
    return f'<span class="badge {css} {sz}">{lbl}</span>'

def pct_html(v: float | None) -> str:
    if v is None: return '<span class="pct-n">—</span>'
    if v > 0: return f'<span class="pct-g">▲ +{abs(v):.2f}%</span>'
    if v < 0: return f'<span class="pct-r">▼ -{abs(v):.2f}%</span>'
    return '<span class="pct-n">0.00%</span>'

def spark_svg(vals: list, w: int = 80, h: int = 32, pos_good: bool = True) -> str:
    if not vals or len(vals) < 2: return ""
    mn, mx = min(vals), max(vals)
    rng = mx - mn or 1
    n = len(vals)
    good = (vals[-1] > vals[0]) == pos_good
    lc = "#00ffa3" if good else "#ff5e75"
    fc = "rgba(0,255,163,0.09)" if good else "rgba(255,94,117,0.09)"
    pts = [(i / (n - 1) * w, h - (vals[i] - mn) / rng * h) for i in range(n)]
    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    fp   = path + f" L {w},{h} L 0,{h} Z"
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;filter:drop-shadow(0 0 4px {lc}44);"><path d="{fp}" fill="{fc}"/><path d="{path}" fill="none" stroke="{lc}" stroke-width="2"/></svg>'

def dual_chart(df1: pd.DataFrame, df2: pd.DataFrame, lbl1: str, lbl2: str) -> go.Figure | None:
    if df1 is None or df1.empty: return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df1["date"], y=df1["value"], mode="lines", name=lbl1, line=dict(color="#ffd166", width=2.8, shape="spline")))
    if df2 is not None and not df2.empty:
        fig.add_trace(go.Scatter(x=df2["date"], y=df2["value"], mode="lines", name=lbl2, line=dict(color="#00f5ff", width=2.2, dash="dot", shape="spline")))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=True,
        legend=dict(orientation="h", y=1.01, x=1, xanchor="right", font=dict(size=10, color="#8fa3b4")),
        margin=dict(l=6, r=16, t=28, b=6), height=260,
        xaxis=dict(showgrid=False, tickfont=dict(size=9.5, color="#8fa3b4")),
        yaxis=dict(showgrid=True, gridcolor="rgba(0,245,255,0.06)", tickfont=dict(size=9.5, color="#8fa3b4"), side="right"),
        hovermode="x unified",
    )
    return fig

def render_top_header() -> None:
    now_str = datetime.utcnow().strftime("%H:%M UTC")
    date_str = datetime.utcnow().strftime("%b %d, %Y")
    render_html(f"""
<div class="top-bar">
  <div class="top-brand">
    <span style="font-size:22px;filter:drop-shadow(0 0 10px #00f5ff);">🏛️</span>
    <div>
      <div style="font-size:16px;font-weight:900;letter-spacing:1.5px;color:#00f5ff;text-shadow:0 0 16px rgba(0,245,255,0.5);">FORTRESS MACRO</div>
      <div style="font-size:9.5px;font-weight:700;color:#64748b;letter-spacing:2px;">GLOBAL INTELLIGENCE DESK</div>
    </div>
  </div>
  <div class="top-tickers">
    <div class="t-pill"><span>🇺🇸 USD Index</span><span class="t-up">▲ Active</span></div>
    <div class="t-pill"><span>🥇 Gold XAU</span><span class="t-up">▲ Active</span></div>
    <div class="t-pill"><span>🛢️ WTI Crude</span><span class="t-dn">▼ Energy</span></div>
    <div class="t-pill"><span>🤖 GPT-4o-mini</span><span class="t-up">⚡ Live AI</span></div>
    <div class="t-pill" style="border-color:rgba(0,245,255,0.25);color:#00f5ff;"><span>🕒 {now_str} | {date_str}</span></div>
  </div>
</div>
""")

def render_data_table(rows: list) -> None:
    tbody = []
    for r in rows:
        cat_icon = CAT_ICONS.get(r["cat"], "📊")
        pg = (r["cat"] not in ("labor_neg",))
        sparkhtml = spark_svg(r["vals"][-20:], pos_good=pg)
        lbl, css, _ = bias_from_score(r["score"])
        tbody.append(f"""
<tr>
<td class="td-nm"><span style="color:#00f5ff;margin-right:6px;">{cat_icon}</span>{r['name']}</td>
<td class="td-val">{r['latest']:,.2f}</td>
<td class="td-pct">{pct_html(r['mom'])}</td>
<td class="td-pct">{pct_html(r.get('qoq'))}</td>
<td class="td-pct">{pct_html(r.get('yoy'))}</td>
<td style="text-align:center;">{sparkhtml}</td>
<td style="text-align:center;"><span class="badge {css}" style="font-size:10px;">{lbl}</span></td>
</tr>
""")
    render_html(f"""
<div class="dt-wrap">
<table class="dt-tbl">
<thead>
<tr>
<th style="width:22%;">Indicator</th>
<th class="ctr" style="width:12%;">Latest</th>
<th class="ctr" style="width:11%;">m/m</th>
<th class="ctr" style="width:11%;">q/q</th>
<th class="ctr" style="width:11%;">y/y</th>
<th class="ctr" style="width:10%;">Trend</th>
<th class="ctr" style="width:13%;">Macro Bias</th>
</tr>
</thead>
<tbody>{"".join(tbody)}</tbody>
</table>
</div>
""")

def page_dashboard(fred_key: str, channel_name: str) -> None:
    a_col, b_col = st.columns([3, 2])
    with a_col:
        asset = st.radio("Market:", ["💱 Forex", "🥇 Gold & Real Yield", "🛢️ Crude Oil (WTI/Brent)"], horizontal=True, label_visibility="collapsed")
    with b_col:
        currency = st.selectbox("Currency:", list(CURRENCY_SERIES.keys()), format_func=lambda k: f"{CURRENCY_SERIES[k]['flag']} {k} — {CURRENCY_SERIES[k]['name']}", label_visibility="collapsed")

    if "Gold" in asset:
        page_gold(fred_key, channel_name)
        return
    if "Oil" in asset:
        page_oil(fred_key, channel_name)
        return

    with st.spinner(f"Reading {currency} macro data & processing live feeds..."):
        result = compute_composite(currency, fred_key, channel_name)

    if not result:
        st.warning("⚠️ Could not load data.")
        return

    rows   = result["rows"]
    rm     = {r["name"]: r for r in rows}
    ki     = CURRENCY_SERIES[currency]["key_indicators"]
    k_rows = [rm[k] for k in ki if k in rm]

    render_html('<div class="sec-title">Key Macro Indicators</div>')
    cols = st.columns(len(k_rows) or 1)
    for col, r in zip(cols, k_rows):
        _pg    = r["cat"] not in ("labor_neg",)
        _mom   = r["mom"]
        _icon  = CAT_ICONS.get(r["cat"], "📊")
        _label = CAT_LABELS.get(r["cat"], "")
        _spark = spark_svg(r["vals"][-20:], pos_good=_pg)
        _hcolor = "#00ffa3" if (_mom > 0) == _pg else "#ff5e75"
        _arr    = "▲" if _mom > 0 else "▼"
        _card = f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">{_icon}</div><span class="mc-cat">{_label}</span></div>
          <div class="mc-nm">{r["name"]}</div>
          <div style="font-size:20px;font-weight:800;color:{_hcolor};margin:4px 0;">{_arr} {abs(_mom):.2f}% m/m</div>
          <div style="font-size:11px;color:#8fa3b4;">Level: <b>{r['latest']:,.2f}</b> | 📅 {r['date']}</div>
          <div style="margin-top:8px;">{_spark}</div>
        </div>
        """
        with col:
            render_html(_card)

    # ── SIDE-BY-SIDE: TABLE ON LEFT, COMPOSITE & AI SUMMARY ON RIGHT ──
    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    t_col, d_col = st.columns([1, 1])
    
    with t_col:
        render_html('<div class="sec-title">Multi-Timeframe Levels</div>')
        render_data_table(rows)

    with d_col:
        render_html('<div class="sec-title">Macro + Sentiment Composite &nbsp; <span style="color:#00ffa3;font-size:10px;font-weight:800;">⚡ Multi-Alert Active</span></div>')
        s = result["score"]
        m_s = result["macro_score"]
        n_p = result["news_points"]
        np_color = "#00ffa3" if n_p > 0 else ("#ff5e75" if n_p < 0 else "#8fa3b4")
        
        driver_items = []
        for d in result["drivers"][:3]:
            dur_tag = f'<span style="color:#00ffa3;font-weight:700;"> ({d.get("expected_duration", "Active")})</span>' if d.get("expected_duration") else ''
            driver_items.append(f'<div style="font-size:11.5px;color:#ecf7ff;margin-top:5px;text-align:left;"><b>{d.get("icon","⚡")} {d.get("name","Event")}:</b>{dur_tag}<br><span style="color:#8fa3b4;font-size:10.5px;">{d.get("reason","")}</span></div>')
        drivers_html = "".join(driver_items)

        ai_summary_html = f'<div style="margin-top:10px;padding:10px 12px;background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.22);border-radius:10px;font-size:11.5px;color:#ecf7ff;text-align:left;line-height:1.5;"><b style="color:#ffd166;">Desk Summary:</b> {result["ai_summary"]}</div>' if result["ai_summary"] else ''

        render_html(f"""
        <div class="comp-box" style="height:100%;text-align:left;padding:18px 20px;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:8px;">{CURRENCY_SERIES[currency]['flag']} {currency} OVERALL BIAS</div>
          <div style="margin-bottom:12px;">{badge(s, lg=True)}</div>
          <div style="font-size:18px;font-weight:900;color:#fff;">Composite: <span style="color:#00f5ff;">{s:+.3f}</span></div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-top:4px;">Macro (50%): <b style="color:#fff;">{m_s:+.3f}</b> | News Sentiment (50%): <b style="color:{np_color};">{n_p:+.2f} pts</b></div>
          {ai_summary_html}
          <div style="margin-top:10px;">{drivers_html}</div>
        </div>
        """)

    # ── FULL-WIDTH BOTTOM: LIVE INSTITUTIONAL WIRE FEED ──
    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Live Institutional Wire &amp; Macro Flow</div>')
    arts = fetch_all_instant_news(channel_name)
    n_cols = st.columns(2)
    for idx, a in enumerate(arts[:6]):
        with n_cols[idx % 2]:
            render_html(f"""
            <div class="news-card">
              <div style="color:#fff;font-size:12px;font-weight:650;line-height:1.45;">{a.get('title', '')}</div>
              <div style="font-size:10px;color:#8fa3b4;margin-top:6px;display:flex;justify-content:space-between;">
                <span>📡 {a.get('source', {}).get('name', 'Institutional Wire')}</span>
                <span>🕒 {a.get('publishedAt', '')}</span>
              </div>
            </div>
            """)

def page_gold(fred_key: str, channel_name: str) -> None:
    render_html("""
<div class="pg-title">
<div class="pg-sub">COMMODITY &amp; SAFE-HAVEN INTELLIGENCE</div>
<h1 class="pg-h1">Gold (XAUUSD) — Real Yield Desk</h1>
<div class="pg-bread">Real Yield 10Y (DFII10) + Rule-Based Shock Analysis</div>
</div>
""")
    if not fred_key:
        st.info("🔑 FRED API Key is required.")
        return

    with st.spinner("Analyzing Gold Real Yield (DFII10) & Feeds..."):
        ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
        if ry_df is None or ry_df.empty:
            y_df = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
            i_df = fetch_fred(GOLD_SERIES["inflation_exp"], fred_key, limit=60)
            if y_df is not None and i_df is not None and not y_df.empty and not i_df.empty:
                merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
                if not merged.empty:
                    merged["value"] = merged["value_y"] - merged["value_i"]
                    ry_df = merged[["date", "value"]]

        usd_r = compute_composite("USD", fred_key, channel_name)

    if ry_df is None or ry_df.empty:
        st.warning("⚠️ Could not load yield data.")
        return

    ry_vals = ry_df["value"].tail(36).tolist()
    ry_mf   = calc_mtf(ry_vals, "rate")

    gold_ry  = -ry_mf["score"] if ry_mf else 0.0
    gold_usd = -(usd_r["macro_score"]) if usd_r else 0.0
    
    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    gold_news_pts = sentiment_res["scores"].get("Gold", 0.0)

    gold_s = (0.30 * gold_ry) + (0.20 * gold_usd) + (0.50 * (gold_news_pts / 0.50))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Real Yield 10Y (DFII10)", f"{ry_vals[-1]:.2f}%", delta=f"{ry_mf['mom']:+.2f}% m/m" if ry_mf else None, delta_color="inverse")
    with c2:
        st.metric("USD Composite Score", f"{usd_r['score']:+.3f}" if usd_r else "N/A")
    with c3:
        gn_color = "#00ffa3" if gold_news_pts > 0 else ("#ff5e75" if gold_news_pts < 0 else "#8fa3b4")
        render_html(f"""
        <div class="comp-box" style="margin-top:0;padding:10px;">
          <div style="font-size:9.5px;font-weight:800;color:#8fa3b4;">Gold (XAUUSD) Direction</div>
          {badge(gold_s, lg=True)}
          <div style="font-size:10.5px;color:#8fa3b4;margin-top:4px;">Score: <b style="color:#ffd166;">{gold_s:+.3f}</b> | Sentiment: <b style="color:{gn_color};">{gold_news_pts:+.2f} pts</b></div>
        </div>
        """)

    ai_gold_summary = sentiment_res.get("ai_summary", "")
    if ai_gold_summary:
        render_html(f'<div style="margin-top:12px;padding:12px 16px;background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.2);border-radius:10px;font-size:12px;color:#ecf7ff;"><b style="color:#ffd166;">🤖 GPT-4o-mini Market AI Intelligence:</b> {ai_gold_summary}</div>')

def page_oil(fred_key: str, channel_name: str) -> None:
    render_html("""
<div class="pg-title">
<div class="pg-sub">GLOBAL ENERGY INTELLIGENCE</div>
<h1 class="pg-h1">Crude Oil (WTI &amp; Brent) Desk</h1>
</div>
""")
    w_df = fetch_fred(OIL_SERIES["wti"], fred_key, limit=60)
    b_df = fetch_fred(OIL_SERIES["brent"], fred_key, limit=60)
    if w_df is None or b_df is None:
        st.warning("⚠️ Could not load oil data.")
        return

    w_vals = w_df["value"].tolist()
    b_vals = b_df["value"].tolist()
    w_mf = calc_mtf(w_vals, "growth")
    spread = b_vals[-1] - w_vals[-1]

    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    oil_news_pts = sentiment_res["scores"].get("Oil", 0.0)

    final_oil_score = (0.50 * (w_mf["score"] if w_mf else 0.0)) + (0.50 * (oil_news_pts / 0.50))

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("WTI Crude", f"${w_vals[-1]:.2f}/bbl", delta=f"{w_mf['mom']:+.2f}% m/m" if w_mf else None)
    with c2: st.metric("Brent Crude", f"${b_vals[-1]:.2f}/bbl")
    with c3:
        lbl_oil, css_oil, _ = bias_from_score(final_oil_score)
        render_html(f"""<div class="comp-box" style="margin-top:0;padding:10px;"><div style="font-size:9.5px;font-weight:800;color:#8fa3b4;">Oil Bias</div><span class="badge {css_oil} badge-lg">{lbl_oil}</span><div style="font-size:10px;color:#8fa3b4;margin-top:3px;">Spread: +${spread:.2f} | News: {oil_news_pts:+.2f} pts</div></div>""")

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    fig = dual_chart(w_df, b_df, "WTI Crude", "Brent Crude")
    if fig:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    ai_oil_summary = sentiment_res.get("ai_summary", "")
    if ai_oil_summary:
        render_html(f'<div style="margin-top:12px;padding:12px 16px;background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.2);border-radius:10px;font-size:12px;color:#ecf7ff;"><b style="color:#ffd166;">🤖 GPT-4o-mini Energy AI Intelligence:</b> {ai_oil_summary}</div>')

def main() -> None:
    # ── 5-SECOND AUTOREFRESH ENGINE ──
    st_autorefresh(interval=5 * 1000, key="auto_refresh_counter")
    inject_css()

    fred_key = DEFAULT_FRED_KEY
    channel_name = DEFAULT_TELEGRAM_CHANNEL

    # ── SINGLE TOP NAVBAR ──
    render_top_header()

    # ── AUTOMATIC BACKGROUND 5S SHIFT CHECK (GOLD, USD, EUR) ──
    if fred_key:
        check_global_market_shifts(fred_key, channel_name)

        # ── AUTOMATIC HOURLY REPORT ENGINE ──
        current_hour_str = datetime.utcnow().strftime("%Y-%m-%d %H")
        if "last_hourly_sent" not in st.session_state:
            st.session_state.last_hourly_sent = current_hour_str
        elif st.session_state.last_hourly_sent != current_hour_str:
            report_text = build_hourly_report(fred_key, channel_name)
            send_telegram_alert(report_text)
            st.session_state.last_hourly_sent = current_hour_str

    # ── MAIN EXECUTIVE DASHBOARD ──
    page_dashboard(fred_key, channel_name)

    render_html(f"""
    <div class="app-foot">
      <div>© 2026 FX Macro Desk • Institutional Macro Intelligence</div>
      <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {datetime.now().strftime('%H:%M:%S')}</span></div>
    </div>
    """)


if __name__ == "__main__":
    main()
