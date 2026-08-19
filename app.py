"""
FX Macro & Geopolitical Intelligence Desk — v9.3 Universal Gemini AI Engine
Institutional-Grade Multi-Timeframe Macro Analysis & Predictive Calendar
Live Integration: Google Gemini AI (Direct REST Engine) + Telegram (@Forex_LiveStream) + FRED (DFII10)
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
import json
import feedparser
from bs4 import BeautifulSoup
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="FX Macro & Geopolitical Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# CONFIGURATIONS
# ============================================================
DEFAULT_FRED_KEY = "8e153c7f6941848ffe00388ae93c1d73"
DEFAULT_TELEGRAM_CHANNEL = "Forex_LiveStream"
REQUEST_TIMEOUT = 12

# بەکارهێنانی کلیل لە نهێنییەکان گەر هەبێت
DEFAULT_GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")

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
        "calendar": [
            {"name": "NFP",          "day": 4,  "impact": "high",   "cat": "labor_pos",  "quarterly": False, "hint": "Non-Farm Payrolls — First Friday"},
            {"name": "Unemployment", "day": 4,  "impact": "high",   "cat": "labor_neg",  "quarterly": False, "hint": "Unemployment Rate (BLS)"},
            {"name": "Core CPI",     "day": 11, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Core CPI ex-Food & Energy"},
            {"name": "CPI",          "day": 11, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Consumer Price Index"},
            {"name": "Core PPI",     "day": 13, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Producer Prices Final Demand"},
            {"name": "Retail Sales", "day": 15, "impact": "high",   "cat": "growth",     "quarterly": False, "hint": "Retail Spending"},
            {"name": "Core PCE",     "day": 25, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Fed Preferred Inflation Metric"},
            {"name": "Interest Rate","day": 18, "impact": "high",   "cat": "rate",       "quarterly": False, "hint": "FOMC Rate Decision"},
        ],
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
        "calendar": [
            {"name": "CPI",          "day": 1,  "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Flash HICP Eurozone"},
            {"name": "Unemployment", "day": 1,  "impact": "high",   "cat": "labor_neg",  "quarterly": False, "hint": "Eurozone Unemployment Rate"},
            {"name": "Interest Rate","day": 12, "impact": "high",   "cat": "rate",       "quarterly": False, "hint": "ECB Policy Rate Decision"},
        ],
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
        "calendar": [
            {"name": "CPI",          "day": 17, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "UK CPI (ONS)"},
            {"name": "Interest Rate","day": 19, "impact": "high",   "cat": "rate",       "quarterly": False, "hint": "Bank of England Rate Decision"},
        ],
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
        "calendar": [
            {"name": "CPI",          "day": 17, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Canada CPI"},
            {"name": "Interest Rate","day": 14, "impact": "high",   "cat": "rate",       "quarterly": False, "hint": "Bank of Canada Rate Decision"},
        ],
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
        "calendar": [
            {"name": "CPI",          "day": 19, "impact": "high",   "cat": "inflation",  "quarterly": False, "hint": "Japan National CPI"},
            {"name": "Interest Rate","day": 18, "impact": "high",   "cat": "rate",       "quarterly": False, "hint": "Bank of Japan Policy Rate"},
        ],
    },
    "CHF": {
        "flag": "🇨🇭", "name": "Swiss Franc",
        "indicators": {
            "CPI":           {"series": "CHECPIALLMINMEI", "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Unemployment":  {"series": "LRHUTTTTCHQ156S", "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "IRLTLT01CHM156N", "cat": "rate",       "w": 2.0, "impact": "high"},
        },
        "key_indicators": ["CPI", "Unemployment", "Interest Rate"],
        "calendar": [
            {"name": "Interest Rate","day": 20, "impact": "high",   "cat": "rate",       "quarterly": True, "hint": "SNB Rate Decision"},
        ],
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
    render_html("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
*{font-family:'Inter',-apple-system,sans-serif!important;box-sizing:border-box;}
.stApp{background:#060a12!important;color:#e5e7eb!important;}
.main .block-container{padding-top:12px!important;padding-left:22px!important;padding-right:22px!important;max-width:100%!important;}
#MainMenu,footer,.stDeployButton{visibility:hidden!important;display:none!important;}
header[data-testid="stHeader"]{background:transparent!important;}

section[data-testid="stSidebar"]{background:#070c16!important;border-right:1px solid rgba(255,255,255,0.05)!important;min-width:250px!important;max-width:250px!important;}
section[data-testid="stSidebar"] .block-container{padding:14px 10px!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"]>div{gap:3px!important;flex-direction:column!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label{display:flex!important;align-items:center!important;padding:9px 12px!important;border-radius:10px!important;background:transparent!important;border:1px solid transparent!important;color:#8a99ad!important;font-size:12.5px!important;font-weight:500!important;cursor:pointer!important;width:100%!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] [aria-checked="true"]{background:rgba(226,183,20,0.09)!important;border:1px solid #e2b714!important;color:#e2b714!important;font-weight:700!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] input[type="radio"],section[data-testid="stSidebar"] div[data-testid="stRadio"] label>div:first-child{display:none!important;}

div[data-testid="stRadio"] div[role="radiogroup"]{display:flex!important;gap:7px!important;flex-wrap:wrap!important;}
div[data-testid="stRadio"] div[role="radiogroup"] label{background:#090e1a!important;border:1px solid rgba(255,255,255,0.08)!important;border-radius:9px!important;padding:6px 13px!important;color:#8a99ad!important;font-size:12px!important;font-weight:600!important;}
div[data-testid="stRadio"] div[role="radiogroup"] [aria-checked="true"]{background:rgba(226,183,20,0.12)!important;border-color:#e2b714!important;color:#e2b714!important;font-weight:700!important;}
div[data-testid="stRadio"] div[role="radiogroup"] label>div:first-child{display:none!important;}

div[data-baseweb="select"],div[data-baseweb="select"]>div,div[data-baseweb="select"] *{background:#0b1220!important;color:#fff!important;border-color:rgba(255,255,255,0.09)!important;border-radius:10px!important;}
div[data-baseweb="input"] input,.stTextInput input{background:#0b1220!important;color:#fff!important;border:1px solid rgba(255,255,255,0.09)!important;border-radius:10px!important;}
div[data-testid="stMetric"]{background:#090e1a!important;border:1px solid rgba(255,255,255,0.06)!important;border-radius:13px!important;padding:13px!important;}
div[data-testid="stMetric"] label{color:#8a99ad!important;font-size:12px!important;}

.top-bar{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:13px;margin-bottom:18px;}
.top-brand{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:800;color:#e2b714;}
.top-tickers{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.t-pill{display:inline-flex;align-items:center;gap:5px;background:#0d1527;border:1px solid rgba(255,255,255,0.05);padding:4px 9px;border-radius:7px;font-size:11px;font-weight:600;color:#9ca3af;}
.t-up{color:#10b981;font-weight:700;}
.t-dn{color:#ef4444;font-weight:700;}

.pg-title{text-align:center;padding:8px 0 18px;}
.pg-sub{font-size:11px;font-weight:800;letter-spacing:2px;color:#e2b714;text-transform:uppercase;margin-bottom:5px;}
.pg-h1{font-size:24px;font-weight:900;color:#fff;margin:0 0 5px;}
.pg-bread{font-size:12px;color:#8a99ad;}

.sec-title{font-size:10.5px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#8a99ad;margin-bottom:11px;margin-top:5px;display:flex;align-items:center;gap:7px;}
.sec-title::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(255,255,255,0.07),transparent);}

.m-card{background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:15px 16px;height:100%;}
.mc-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;}
.mc-ico{width:32px;height:32px;border-radius:8px;background:rgba(226,183,20,0.08);display:flex;align-items:center;justify-content:center;font-size:15px;}
.mc-cat{font-size:9px;font-weight:800;color:#8a99ad;padding:2px 7px;border-radius:999px;background:rgba(255,255,255,0.04);}
.mc-nm{font-size:12.5px;font-weight:700;color:#8a99ad;margin:3px 0 2px;}

.dt-wrap{background:#090e1a;border:1px solid rgba(255,255,255,0.07);border-radius:14px;overflow:hidden;}
.dt-tbl{width:100%;border-collapse:collapse;font-size:12.5px;}
.dt-tbl thead th{background:#0c1322;color:#8a99ad;padding:13px 16px;font-weight:600;font-size:11.5px;}
.dt-tbl thead th.ctr{text-align:center;}
.dt-tbl tbody td{padding:11px 16px;color:#e5e7eb;vertical-align:middle;border-bottom:1px solid rgba(255,255,255,0.03);}
.td-nm{font-weight:700;color:#fff;}
.td-val{font-weight:600;color:#fff;text-align:center;}
.td-pct{font-weight:600;text-align:center;}
.pct-g{color:#10b981;font-weight:700;}
.pct-r{color:#ef4444;font-weight:700;}
.pct-n{color:#6b7280;font-weight:700;}

.chart-card{background:#090e1a;border:1px solid rgba(255,255,255,0.07);border-radius:14px;padding:16px;}
.badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;}
.b-bull{background:rgba(16,185,129,0.12);color:#10b981;border:1px solid rgba(16,185,129,0.2);}
.b-bear{background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.2);}
.b-neut{background:rgba(107,114,128,0.12);color:#9ca3af;border:1px solid rgba(107,114,128,0.2);}
.badge-lg{font-size:13.5px;padding:7px 18px;border-radius:11px;font-weight:800;}

.news-card{background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:14px 16px;margin-bottom:9px;}
.comp-box{background:#090e1a;border:1px solid rgba(226,183,20,0.17);border-radius:13px;padding:18px;text-align:center;}
.pills{display:flex;gap:5px;flex-wrap:wrap;}
.pill-g{background:rgba(16,185,129,0.13);color:#10b981;border:1px solid rgba(16,185,129,0.28);padding:3px 9px;border-radius:6px;font-weight:700;font-size:11px;}
.pill-r{background:rgba(239,68,68,0.13);color:#ef4444;border:1px solid rgba(239,68,68,0.28);padding:3px 9px;border-radius:6px;font-weight:700;font-size:11px;}
.app-foot{display:flex;justify-content:space-between;align-items:center;padding:16px 22px;margin-top:36px;border-top:1px solid rgba(255,255,255,0.05);font-size:11px;color:#4b5563;}
.live-dot{width:6px;height:6px;border-radius:50%;background:#10b981;box-shadow:0 0 7px #10b981;display:inline-block;margin-right:5px;}
</style>
""")

# ============================================================
# DATA FETCHING ENGINE (FRED + TELEGRAM + RSS)
# ============================================================
@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
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
                        "source": {"name": f"Telegram @{clean_username}"},
                        "url": f"https://t.me/{clean_username}"
                    })
    except Exception:
        pass
    return list(reversed(articles))


@st.cache_data(ttl=60, show_spinner=False)
def fetch_all_instant_news(channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> list:
    tg_news = fetch_telegram_channel_news(channel_name)
    rss_urls = [
        ("ForexLive", "https://www.forexlive.com/feed/news"),
        ("FXStreet", "https://www.fxstreet.com/rss/news"),
        ("Investing.com", "https://www.investing.com/rss/news_25.rss")
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
                    "source": {"name": src_name},
                    "url": entry.get("link", "#")
                })
        except Exception:
            continue
    return tg_news + rss_news


# ============================================================
# GEMINI AI INTELLIGENCE & SENTIMENT IMPACT ENGINE (UNIVERSAL REST)
# ============================================================
@st.cache_data(ttl=90, show_spinner=False)
def analyze_news_with_gemini(articles: list, gemini_key: str) -> dict:
    scores = {
        "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "CAD": 0.0,
        "JPY": 0.0, "AUD": 0.0, "NZD": 0.0, "CHF": 0.0,
        "Gold": 0.0, "Oil": 0.0
    }
    drivers = []
    ai_summary = ""

    if not articles:
        return {"scores": scores, "drivers": drivers, "ai_summary": ai_summary, "ai_active": False}

    clean_key = gemini_key.strip() if gemini_key else ""
    if not clean_key:
        rules = [
            {"pattern": r"(war|military|missile|conflict|sanction|attack|invad|escalat|iran|israel|russia|ukraine|tensions)", "name": "Geopolitical Conflict & War Escalation", "icon": "💣", "dur": "1-2 Weeks", "impacts": {"USD": +0.10, "CHF": +0.25, "Gold": +0.35, "Oil": +0.25, "EUR": -0.15, "GBP": -0.12}},
            {"pattern": r"(treasury.*buyback|bond repurchase|yields.*decline|yield.*fall|dollar.*decline)", "name": "US Treasury Bond Buybacks / Yield Drop", "icon": "📉", "dur": "1-3 Days", "impacts": {"Gold": +0.35, "EUR": +0.20, "GBP": +0.18, "USD": -0.35}},
            {"pattern": r"(oil spike|opec cut|crude jump|brent surge|energy supply|pipeline)", "name": "Oil & Energy Supply Shock", "icon": "🛢️", "dur": "3-5 Days", "impacts": {"CAD": +0.22, "Oil": +0.30, "USD": +0.10, "JPY": -0.22, "EUR": -0.15}},
            {"pattern": r"(fed hike|hawkish fed|rate increase|sticky inflation|cpi surge)", "name": "Hawkish Fed / Rate Hike Pressure", "icon": "🏦", "dur": "1-2 Weeks", "impacts": {"USD": +0.25, "Gold": -0.20, "EUR": -0.15, "JPY": -0.18}},
            {"pattern": r"(fed cut|rate cut|dovish fed|inflation cooling|fed pivot)", "name": "Dovish Fed / Rate Cut Pivot", "icon": "📉", "dur": "1-2 Weeks", "impacts": {"Gold": +0.30, "USD": -0.25, "EUR": +0.18, "GBP": +0.15}},
        ]
        detected = set()
        for a in articles:
            txt = (str(a.get("title", "")) + " " + str(a.get("description", ""))).lower()
            for r in rules:
                if re.search(r["pattern"], txt):
                    for curr, pt in r["impacts"].items():
                        scores[curr] += pt
                    if r["name"] not in detected:
                        drivers.append({"name": r["name"], "icon": r["icon"], "expected_duration": r["dur"], "reason": "Rule-based pattern matching"})
                        detected.add(r["name"])
        for k in scores:
            scores[k] = float(np.clip(scores[k], -0.60, 0.60))
        return {"scores": scores, "drivers": drivers, "ai_summary": "Rule-based engine active (Add Gemini Key).", "ai_active": False}

    news_corpus = "\n".join([f"[{i+1}] {a.get('title','')} - {a.get('description','')[:180]}" for i, a in enumerate(articles[:8])])
    prompt = f"""
You are an elite Institutional Macro Strategist and Quantitative FX & Commodity Portfolio Manager.
Evaluate the following breaking news articles and determine exact directional impact points, expected duration, and key drivers.

LIVE NEWS STREAM:
{news_corpus}

INSTRUCTIONS:
1. Provide impact scores for: USD, EUR, GBP, CAD, JPY, AUD, NZD, CHF, Gold, Oil.
   Scale: -0.50 (Extremely Bearish) to +0.50 (Extremely Bullish). 0.0 is Neutral.
2. Identify top 2-3 market-moving catalyst events.
3. For each catalyst, specify 'name', 'icon' (emoji), 'expected_duration' (e.g. '1-4 Hours', '1-3 Days', '1-2 Weeks'), and 'reason'.
4. Provide a concise 'ai_summary' explaining the primary market theme in 1-2 sharp sentences.

Return ONLY a JSON object strictly matching this schema:
{{
  "scores": {{
    "USD": float, "EUR": float, "GBP": float, "CAD": float,
    "JPY": float, "AUD": float, "NZD": float, "CHF": float,
    "Gold": float, "Oil": float
  }},
  "drivers": [
    {{
      "name": string,
      "icon": string,
      "expected_duration": string,
      "reason": string
    }}
  ],
  "ai_summary": string
}}
"""
    # بانگکردنی ڕاستەوخۆ لە ڕێگەی Gemini REST API بۆ گەرەنتی ١٠٠٪ کارکردن
    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
    for model_name in models_to_try:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={clean_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1
                }
            }
            res = requests.post(url, headers=headers, json=payload, timeout=12)
            if res.status_code == 200:
                res_data = res.json()
                raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                clean_json_str = re.sub(r"^```json\s*|\s*
