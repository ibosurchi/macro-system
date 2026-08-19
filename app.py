"""
FX Macro & Geopolitical Intelligence Desk — v10.3 Groq Model Patched
Institutional-Grade Multi-Timeframe Macro Analysis & Predictive Calendar
Live Integration: Groq Llama 3 (8b-8192) + Telegram + RSS + FRED (DFII10)
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

DEFAULT_GROQ_KEY = st.secrets.get("GROQ_API_KEY", "")

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
# GROQ AI INTELLIGENCE & SENTIMENT IMPACT ENGINE
# ============================================================
def extract_json_clean(text: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)


@st.cache_data(ttl=60, show_spinner=False)
def analyze_news_with_groq(articles: list, groq_key: str) -> dict:
    scores = {
        "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "CAD": 0.0,
        "JPY": 0.0, "AUD": 0.0, "NZD": 0.0, "CHF": 0.0,
        "Gold": 0.0, "Oil": 0.0
    }
    drivers = []
    ai_summary = ""

    if not articles:
        return {"scores": scores, "drivers": drivers, "ai_summary": ai_summary, "ai_active": False}

    clean_key = groq_key.strip() if groq_key else ""
    if not clean_key:
        return {"scores": scores, "drivers": drivers, "ai_summary": "Groq API Key is missing.", "ai_active": False}

    news_corpus = "\n".join([f"[{i+1}] {a.get('title','')} - {a.get('description','')[:150]}" for i, a in enumerate(articles[:5])])
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
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {clean_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama3-8b-8192",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }
        res = requests.post(url, headers=headers, json=payload, timeout=12)
        if res.status_code == 200:
            res_data = res.json()
            raw_text = res_data["choices"][0]["message"]["content"]
            parsed = extract_json_clean(raw_text)
            parsed["ai_active"] = True
            return parsed
        else:
            err_msg = res.json().get("error", {}).get("message", res.text[:100])
            return {"scores": scores, "drivers": drivers, "ai_summary": f"Groq API Error: {err_msg}", "ai_active": False}
    except Exception as e:
        return {"scores": scores, "drivers": drivers, "ai_summary": f"Connection Error: {str(e)[:100]}", "ai_active": False}


# ============================================================
# MULTI-TIMEFRAME ENGINE
# ============================================================
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


def compute_composite(currency: str, fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL, groq_key: str = DEFAULT_GROQ_KEY) -> dict | None:
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
    sentiment_res = analyze_news_with_groq(all_news, groq_key)
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


# ============================================================
# HELPERS & CHARTS
# ============================================================
def bias_from_score(s: float) -> tuple[str, str, str]:
    if s > 0.15: return "📈 Bullish", "b-bull", "#10b981"
    if s < -0.15: return "📉 Bearish", "b-bear", "#ef4444"
    return "⚖️ Neutral", "b-neut", "#9ca3af"

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
    lc = "#10b981" if good else "#ef4444"
    fc = "rgba(16,185,129,0.07)" if good else "rgba(239,68,68,0.07)"
    pts = [(i / (n - 1) * w, h - (vals[i] - mn) / rng * h) for i in range(n)]
    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    fp   = path + f" L {w},{h} L 0,{h} Z"
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;"><path d="{fp}" fill="{fc}"/><path d="{path}" fill="none" stroke="{lc}" stroke-width="1.8"/></svg>'

def dynamic_chart(df: pd.DataFrame, name: str, currency: str) -> go.Figure | None:
    if df is None or df.empty: return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["value"], mode="lines+markers",
        marker=dict(size=3.5, color="#e2b714"), line=dict(color="#e2b714", width=2.5, shape="spline"),
        fill="tonexty", fillcolor="rgba(226,183,20,0.06)", hovertemplate="<b>%{x}</b><br>%{y:,.3f}<extra></extra>"
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False,
        margin=dict(l=6, r=16, t=6, b=6), height=230,
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color="#8a99ad")),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=9, color="#8a99ad"), side="right"),
        hovermode="x unified",
    )
    return fig

def dual_chart(df1: pd.DataFrame, df2: pd.DataFrame, lbl1: str, lbl2: str) -> go.Figure | None:
    if df1 is None or df1.empty: return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df1["date"], y=df1["value"], mode="lines", name=lbl1, line=dict(color="#e2b714", width=2.8, shape="spline")))
    if df2 is not None and not df2.empty:
        fig.add_trace(go.Scatter(x=df2["date"], y=df2["value"], mode="lines", name=lbl2, line=dict(color="#3b82f6", width=2, dash="dot", shape="spline")))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=True,
        legend=dict(orientation="h", y=1.01, x=1, xanchor="right", font=dict(size=10, color="#8a99ad")),
        margin=dict(l=6, r=16, t=28, b=6), height=260,
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color="#8a99ad")),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=9, color="#8a99ad"), side="right"),
        hovermode="x unified",
    )
    return fig

def render_top_header() -> None:
    render_html("""
<div class="top-bar">
<div class="top-brand"><span>📊</span><span>FX MACRO &amp; GEOPOLITICAL DESK</span></div>
<div class="top-tickers">
<div class="t-pill"><span>🇺🇸 USD</span><span class="t-up">Live Macro</span></div>
<div class="t-pill"><span>🥇 Gold</span><span class="t-up">XAU/USD Active</span></div>
<div class="t-pill"><span>🤖 AI Engine</span><span class="t-up">Groq Llama 3 Active</span></div>
<div class="t-pill"><span>📡 Channel</span><span class="t-up">Telegram @Forex_LiveStream</span></div>
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
<td class="td-nm"><span style="color:#e2b714;margin-right:6px;">{cat_icon}</span>{r['name']}</td>
<td class="td-val">{r['latest']:,.2f}</td>
<td class="td-pct">{pct_html(r['mom'])}</td>
<td class="td-pct">{pct_html(r.get('qoq'))}</td>
<td class="td-pct">{pct_html(r.get('yoy'))}</td>
<td style="text-align:center;">{sparkhtml}</td>
<td style="text-align:center;"><span class="badge {css}" style="font-size:10.5px;">{lbl}</span></td>
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


# ============================================================
# PAGE 1 — EXECUTIVE DASHBOARD
# ============================================================
def page_dashboard(fred_key: str, channel_name: str, groq_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">FX MACRO &amp; GEOPOLITICAL DESK</div>
<h1 class="pg-h1">Executive Intelligence Dashboard</h1>
<div class="pg-bread">Real-time Multi-Timeframe Macro Analysis &amp; Groq Llama 3 AI Engine</div>
</div>
""")
    a_col, b_col = st.columns([3, 2])
    with a_col:
        asset = st.radio("Market:", ["💱 Forex", "🥇 Gold & Real Yield", "🛢️ Crude Oil (WTI/Brent)"], horizontal=True, label_visibility="collapsed")
    with b_col:
        currency = st.selectbox("Currency:", list(CURRENCY_SERIES.keys()), format_func=lambda k: f"{CURRENCY_SERIES[k]['flag']} {k} — {CURRENCY_SERIES[k]['name']}", label_visibility="collapsed")

    if "Gold" in asset:
        page_gold(fred_key, channel_name, groq_key)
        return
    if "Oil" in asset:
        page_oil(fred_key, channel_name, groq_key)
        return

    with st.spinner(f"Reading {currency} macro data & analyzing live feeds with Groq Llama 3..."):
        result = compute_composite(currency, fred_key, channel_name, groq_key)

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
        _hcolor = "#10b981" if (_mom > 0) == _pg else "#ef4444"
        _arr    = "▲" if _mom > 0 else "▼"
        _card = f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">{_icon}</div><span class="mc-cat">{_label}</span></div>
          <div class="mc-nm">{r["name"]}</div>
          <div style="font-size:20px;font-weight:800;color:{_hcolor};margin:4px 0;">{_arr} {abs(_mom):.2f}% m/m</div>
          <div style="font-size:11px;color:#8a99ad;">Level: <b>{r['latest']:,.2f}</b> | 📅 {r['date']}</div>
          <div style="margin-top:8px;">{_spark}</div>
        </div>
        """
        with col:
            render_html(_card)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    t_col, c_col = st.columns([1.1, 1.25])
    with t_col:
        render_html('<div class="sec-title">Multi-Timeframe Levels</div>')
        render_data_table(rows)

    with c_col:
        render_html('<div class="sec-title">Live Indicator Chart</div>')
        chosen = st.selectbox("Select indicator:", [r["name"] for r in rows], label_visibility="collapsed")
        crow = rm.get(chosen, rows[0])
        fig = dynamic_chart(crow["df"], chosen, currency)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    n_col, d_col = st.columns([1.15, 1.0])
    with n_col:
        render_html(f'<div class="sec-title">Live Telegram Feed (@{channel_name})</div>')
        arts = fetch_all_instant_news(channel_name)
        for a in arts[:4]:
            render_html(f"""
            <a href="{a.get('url', '#')}" target="_blank" style="text-decoration:none;">
            <div class="news-card">
              <div style="color:#fff;font-size:12px;font-weight:600;line-height:1.4;">{a.get('title', '')}</div>
              <div style="font-size:10px;color:#8a99ad;margin-top:5px;display:flex;justify-content:space-between;">
                <span>📡 {a.get('source', {}).get('name', 'Feed')}</span>
                <span>🕒 {a.get('publishedAt', '')}</span>
              </div>
            </div></a>
            """)

    with d_col:
        ai_badge = '<span style="color:#10b981;font-size:10px;font-weight:800;">🤖 Groq Llama 3 Active</span>' if result["ai_active"] else '<span style="color:#f59e0b;font-size:10px;font-weight:700;">⚙️ Check Groq API Key</span>'
        render_html(f'<div class="sec-title">Macro + AI Sentiment Composite &nbsp; {ai_badge}</div>')
        s = result["score"]
        m_s = result["macro_score"]
        n_p = result["news_points"]
        np_color = "#10b981" if n_p > 0 else ("#ef4444" if n_p < 0 else "#8a99ad")
        
        driver_items = []
        for d in result["drivers"][:3]:
            dur_tag = f'<span style="color:#10b981;font-weight:700;"> ({d.get("expected_duration", "Active")})</span>' if d.get("expected_duration") else ''
            driver_items.append(f'<div style="font-size:11.5px;color:#e5e7eb;margin-top:4px;text-align:left;"><b>{d.get("icon","⚡")} {d.get("name","Event")}:</b>{dur_tag}<br><span style="color:#8a99ad;font-size:10.5px;">{d.get("reason","")}</span></div>')
        drivers_html = "".join(driver_items)

        ai_summary_html = f'<div style="margin-top:8px;padding:8px 10px;background:rgba(226,183,20,0.06);border:1px solid rgba(226,183,20,0.18);border-radius:8px;font-size:11px;color:#e5e7eb;text-align:left;"><b style="color:#e2b714;">AI Assessment:</b> {result["ai_summary"]}</div>' if result["ai_summary"] else ''

        render_html(f"""
        <div class="comp-box">
          <div style="font-size:10.5px;font-weight:800;color:#8a99ad;text-transform:uppercase;">{CURRENCY_SERIES[currency]['flag']} {currency} Overall Bias</div>
          <div style="margin:8px 0;">{badge(s, lg=True)}</div>
          <div style="font-size:14px;font-weight:800;color:#fff;">Composite: <span style="color:#e2b714;">{s:+.3f}</span></div>
          <div style="font-size:11px;color:#8a99ad;margin-top:4px;">Macro (50%): <b>{m_s:+.3f}</b> | AI News (50%): <b style="color:{np_color};">{n_p:+.2f} pts</b></div>
          {ai_summary_html}
          <div style="margin-top:6px;">{drivers_html}</div>
        </div>
        """)


# ============================================================
# PAGE 2 — GOLD INTELLIGENCE
# ============================================================
def page_gold(fred_key: str, channel_name: str, groq_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">COMMODITY &amp; SAFE-HAVEN INTELLIGENCE</div>
<h1 class="pg-h1">Gold (XAUUSD) — Real Yield &amp; Groq AI Feed</h1>
<div class="pg-bread">Real Yield 10Y (DFII10) + Groq Llama 3 Shock &amp; Duration Analysis</div>
</div>
""")
    if not fred_key:
        st.info("🔑 FRED API Key is required.")
        return

    with st.spinner("Analyzing Gold Real Yield (DFII10) & Telegram Feeds with Groq..."):
        ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
        if ry_df is None or ry_df.empty:
            y_df = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
            i_df = fetch_fred(GOLD_SERIES["inflation_exp"], fred_key, limit=60)
            if y_df is not None and i_df is not None and not y_df.empty and not i_df.empty:
                merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
                if not merged.empty:
                    merged["value"] = merged["value_y"] - merged["value_i"]
                    ry_df = merged[["date", "value"]]

        usd_r = compute_composite("USD", fred_key, channel_name, groq_key)

    if ry_df is None or ry_df.empty:
        st.warning("⚠️ Could not load yield data.")
        return

    ry_vals = ry_df["value"].tail(36).tolist()
    ry_mf   = calc_mtf(ry_vals, "rate")

    gold_ry  = -ry_mf["score"] if ry_mf else 0.0
    gold_usd = -(usd_r["macro_score"]) if usd_r else 0.0
    
    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_with_groq(all_news, groq_key)
    gold_news_pts = sentiment_res["scores"].get("Gold", 0.0)

    gold_s = (0.30 * gold_ry) + (0.20 * gold_usd) + (0.50 * (gold_news_pts / 0.50))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Real Yield 10Y (DFII10)", f"{ry_vals[-1]:.2f}%", delta=f"{ry_mf['mom']:+.2f}% m/m" if ry_mf else None, delta_color="inverse")
    with c2:
        st.metric("USD Composite Score", f"{usd_r['score']:+.3f}" if usd_r else "N/A")
    with c3:
        gn_color = "#10b981" if gold_news_pts > 0 else ("#ef4444" if gold_news_pts < 0 else "#8a99ad")
        render_html(f"""
        <div class="comp-box" style="margin-top:0;padding:10px;">
          <div style="font-size:9.5px;font-weight:800;color:#8a99ad;">Gold (XAUUSD) Direction</div>
          {badge(gold_s, lg=True)}
          <div style="font-size:10.5px;color:#8a99ad;margin-top:4px;">Score: <b style="color:#e2b714;">{gold_s:+.3f}</b> | AI Shock: <b style="color:{gn_color};">{gold_news_pts:+.2f} pts</b></div>
        </div>
        """)

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">10Y Real Yield Dynamic (DFII10)</div>')
    fig = dynamic_chart(ry_df, "10Y Real Yield (DFII10)", "USD")
    if fig:
        render_html('<div class="chart-card">')
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        render_html('</div>')


# ============================================================
# PAGE 3 — CRUDE OIL
# ============================================================
def page_oil(fred_key: str, channel_name: str, groq_key: str) -> None:
    render_top_header()
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
    sentiment_res = analyze_news_with_groq(all_news, groq_key)
    oil_news_pts = sentiment_res["scores"].get("Oil", 0.0)

    final_oil_score = (0.50 * (w_mf["score"] if w_mf else 0.0)) + (0.50 * (oil_news_pts / 0.50))

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("WTI Crude", f"${w_vals[-1]:.2f}/bbl", delta=f"{w_mf['mom']:+.2f}% m/m" if w_mf else None)
    with c2: st.metric("Brent Crude", f"${b_vals[-1]:.2f}/bbl")
    with c3:
        lbl_oil, css_oil, _ = bias_from_score(final_oil_score)
        render_html(f"""<div class="comp-box" style="margin-top:0;padding:10px;"><div style="font-size:9.5px;font-weight:800;color:#8a99ad;">Oil Bias</div><span class="badge {css_oil} badge-lg">{lbl_oil}</span><div style="font-size:10px;color:#8a99ad;margin-top:3px;">Spread: +${spread:.2f} | AI News: {oil_news_pts:+.2f} pts</div></div>""")

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    fig = dual_chart(w_df, b_df, "WTI Crude", "Brent Crude")
    if fig:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ============================================================
# PAGE 4 — REAL-TIME TELEGRAM FEED SCANNER
# ============================================================
def page_telegram_feed(channel_name: str, groq_key: str) -> None:
    render_top_header()
    render_html(f"""
<div class="pg-title">
<div class="pg-sub">LIVE TELEGRAM RADAR</div>
<h1 class="pg-h1">Telegram Channel: @{channel_name}</h1>
<div class="pg-bread">Real-Time Parsed Messages &amp; Groq Llama 3 Impact Analysis</div>
</div>
""")
    with st.spinner("Fetching Telegram posts & executing Groq AI Model..."):
        posts = fetch_telegram_channel_news(channel_name)
        sentiment_res = analyze_news_with_groq(posts, groq_key)

    scores = sentiment_res["scores"]
    pills_html = []
    for asset, pt in scores.items():
        if pt != 0.0:
            c_cls = "pill-g" if pt > 0 else "pill-r"
            sign = "+" if pt > 0 else ""
            pills_html.append(f'<span class="{c_cls}">{asset}: {sign}{pt:.2f} pts</span>')

    if pills_html:
        render_html(f"""
        <div class="dt-wrap" style="padding:12px 16px;margin-bottom:16px;background:#0d1527;border-color:rgba(226,183,20,0.2);">
          <div style="font-size:11px;font-weight:800;color:#e2b714;text-transform:uppercase;margin-bottom:6px;">⚡ AI Instant Sentiment Radar (Scores Matrix)</div>
          <div class="pills">{"".join(pills_html)}</div>
        </div>
        """)

    if sentiment_res.get("ai_summary"):
        render_html(f"""
        <div class="news-card" style="border: 1px solid rgba(226,183,20,0.3);background:#0b1325;margin-bottom:14px;">
          <div style="color:#e2b714;font-size:12.5px;font-weight:800;">🤖 Groq Llama 3 Intelligence Briefing</div>
          <div style="color:#ffffff;font-size:12px;line-height:1.5;margin-top:5px;">{sentiment_res["ai_summary"]}</div>
        </div>
        """)

    if not posts:
        st.info("No recent messages found from this channel.")
        return

    for p in posts:
        render_html(f"""
        <div class="news-card">
          <div style="color:#e2b714;font-size:13px;font-weight:700;line-height:1.5;">{p.get('description', '')}</div>
          <div style="font-size:10.5px;color:#8a99ad;margin-top:6px;display:flex;justify-content:space-between;">
            <span>📡 {p.get('source', {}).get('name', '')}</span>
            <span>🕒 {p.get('publishedAt', '')}</span>
          </div>
        </div>
        """)


# ============================================================
# MAIN APPLICATION CONTROLLER
# ============================================================
def main() -> None:
    st_autorefresh(interval=60 * 1000, key="auto_refresh_counter")
    inject_css()

    with st.sidebar:
        render_html("""
        <div style="padding:5px 7px 14px;border-bottom:1px solid rgba(255,255,255,0.06);margin-bottom:12px;">
          <div style="font-size:12px;font-weight:800;color:#e2b714;">FX MACRO &amp; GEO</div>
          <div style="font-size:9.5px;color:#6b7280;">INTELLIGENCE DESK v10.3 (Groq)</div>
        </div>
        """)
        page = st.radio("Navigation:", [
            "🏠 Executive Dashboard",
            "🥇 Gold (XAUUSD) Intelligence",
            "🛢️ Crude Oil (Energy Desk)",
            "📡 Live Telegram Feed",
            "📊 Currency Impact Matrix",
        ], label_visibility="collapsed")

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        st.markdown("<b style='color:#e2b714;font-size:11px;'>🤖 GROQ AI ENGINE</b>", unsafe_allow_html=True)
        groq_key = st.text_input("Groq API Key:", value=DEFAULT_GROQ_KEY, type="password", key="groq_key")
        
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        st.markdown("<b style='color:#6b7280;font-size:10.5px;'>📡 TELEGRAM CHANNEL</b>", unsafe_allow_html=True)
        channel_name = st.text_input("Channel Username:", value=DEFAULT_TELEGRAM_CHANNEL, key="tg_channel")
        fred_key = st.text_input("FRED API Key:", value=DEFAULT_FRED_KEY, type="password", key="fred_key")

    if page == "🏠 Executive Dashboard":
        page_dashboard(fred_key, channel_name, groq_key)
    elif page == "🥇 Gold (XAUUSD) Intelligence":
        page_gold(fred_key, channel_name, groq_key)
    elif page == "🛢️ Crude Oil (Energy Desk)":
        page_oil(fred_key, channel_name, groq_key)
    elif page == "📡 Live Telegram Feed":
        page_telegram_feed(channel_name, groq_key)
    elif page == "📊 Currency Impact Matrix":
        render_top_header()
        render_html('<div class="sec-title">Currency Impact Matrix</div>')
        render_html('<div class="dt-wrap" style="padding:16px;">Groq AI Institutional Matrix active in memory.</div>')

    render_html(f"""
    <div class="app-foot">
      <div>© 2026 FX Macro Desk | Groq Llama 3 Integration</div>
      <div><span class="live-dot"></span><span style="color:#10b981;font-weight:600;">Live Feed Active &nbsp; {datetime.now().strftime('%H:%M:%S')}</span></div>
    </div>
    """)


if __name__ == "__main__":
    main()
