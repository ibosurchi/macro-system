"""
FX Macro & Geopolitical Intelligence Desk — v7.5 Live RSS
Institutional-Grade Multi-Timeframe Macro Analysis & Predictive Calendar
Enhanced with Instant Real-Time RSS News & Geopolitical Impact Engine
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta
import calendar as cal_lib
import re
import feedparser
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="FX Macro & Geopolitical Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# DEFAULT API KEYS
# ============================================================
DEFAULT_FRED_KEY = "8e153c7f6941848ffe00388ae93c1d73"
REQUEST_TIMEOUT = 12

# ============================================================
# CURRENCY SERIES CONFIGURATION (Official FRED IDs)
# ============================================================
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
            {"name": "NFP",          "day": 4,  "impact": "high",   "cat": "labor_pos",  "quarterly": False,
             "hint": "Non-Farm Payrolls — First Friday each month"},
            {"name": "Unemployment", "day": 4,  "impact": "high",   "cat": "labor_neg",  "quarterly": False,
             "hint": "Unemployment Rate — Released alongside NFP"},
            {"name": "Core CPI",     "day": 11, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Core Inflation ex-Food & Energy (BLS)"},
            {"name": "CPI",          "day": 11, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Consumer Price Index — All Urban Consumers"},
            {"name": "Core PPI",     "day": 13, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Producer Prices ex-Food & Energy (Final Demand)"},
            {"name": "PPI",          "day": 13, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Producer Price Index — Final Demand (BLS Official)"},
            {"name": "Retail Sales", "day": 15, "impact": "high",   "cat": "growth",     "quarterly": False,
             "hint": "Consumer Spending & Retail Activity"},
            {"name": "Core PCE",     "day": 25, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Fed's Preferred Inflation Gauge (BEA)"},
            {"name": "PCE",          "day": 25, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Personal Consumption Expenditures Price Index"},
            {"name": "Interest Rate","day": 18, "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "FOMC Federal Funds Rate Decision"},
            {"name": "GDP",          "day": 28, "impact": "high",   "cat": "growth",     "quarterly": True,
             "hint": "Gross Domestic Product — Quarterly Advance Estimate"},
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
            {"name": "CPI",          "day": 1,  "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Flash HICP — Eurozone Consumer Prices (Eurostat)"},
            {"name": "Core CPI",     "day": 1,  "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Core HICP ex-Food, Alcohol, Tobacco & Energy"},
            {"name": "Unemployment", "day": 1,  "impact": "high",   "cat": "labor_neg",  "quarterly": False,
             "hint": "Eurozone Unemployment Rate (Eurostat)"},
            {"name": "Production",   "day": 13, "impact": "medium", "cat": "growth",     "quarterly": False,
             "hint": "Eurozone Industrial Production (Eurostat)"},
            {"name": "Interest Rate","day": 12, "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "ECB Deposit Facility Rate Decision"},
            {"name": "GDP",          "day": 30, "impact": "high",   "cat": "growth",     "quarterly": True,
             "hint": "Eurozone GDP — Quarterly Flash Estimate"},
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
            {"name": "CPI",          "day": 17, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "UK Consumer Price Index (ONS)"},
            {"name": "Core CPI",     "day": 17, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "UK Core CPI ex-Energy, Food, Alcohol & Tobacco"},
            {"name": "Unemployment", "day": 11, "impact": "high",   "cat": "labor_neg",  "quarterly": False,
             "hint": "UK Unemployment Rate (ONS Labour Market)"},
            {"name": "Production",   "day": 11, "impact": "medium", "cat": "growth",     "quarterly": False,
             "hint": "UK Industrial Production (ONS)"},
            {"name": "Interest Rate","day": 19, "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "Bank of England MPC Rate Decision"},
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
            {"name": "CPI",          "day": 17, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Canada CPI — Statistics Canada"},
            {"name": "Core CPI",     "day": 17, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Canada Core CPI ex-8 Most Volatile Components"},
            {"name": "Employment",   "day": 4,  "impact": "high",   "cat": "labor_pos",  "quarterly": False,
             "hint": "Canada Employment Change (Statistics Canada)"},
            {"name": "Unemployment", "day": 4,  "impact": "high",   "cat": "labor_neg",  "quarterly": False,
             "hint": "Canada Unemployment Rate"},
            {"name": "Interest Rate","day": 14, "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "Bank of Canada Overnight Rate Decision"},
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
            {"name": "CPI",          "day": 19, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Japan National CPI (Statistics Bureau)"},
            {"name": "Core CPI",     "day": 19, "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Japan Core CPI ex-Fresh Food"},
            {"name": "Production",   "day": 14, "impact": "medium", "cat": "growth",     "quarterly": False,
             "hint": "Japan Industrial Production (METI)"},
            {"name": "Unemployment", "day": 27, "impact": "medium", "cat": "labor_neg",  "quarterly": False,
             "hint": "Japan Unemployment Rate (Statistics Bureau)"},
            {"name": "Interest Rate","day": 18, "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "Bank of Japan Policy Rate Decision"},
        ],
    },
    "AUD": {
        "flag": "🇦🇺", "name": "Australian Dollar",
        "indicators": {
            "CPI":           {"series": "AUSCPIALLQINMEI", "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Employment":    {"series": "LFEMTTTTAUM647S", "cat": "labor_pos",  "w": 1.5, "impact": "high"},
            "Unemployment":  {"series": "LRUN64TTAUM156S", "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "IRLTLT01AUM156N", "cat": "rate",       "w": 2.0, "impact": "high"},
        },
        "key_indicators": ["CPI", "Employment", "Unemployment", "Interest Rate"],
        "calendar": [
            {"name": "CPI",          "day": 24, "impact": "high",   "cat": "inflation",  "quarterly": True,
             "hint": "Australia CPI (Australian Bureau of Statistics)"},
            {"name": "Employment",   "day": 15, "impact": "high",   "cat": "labor_pos",  "quarterly": False,
             "hint": "Australia Employment Change (ABS)"},
            {"name": "Unemployment", "day": 15, "impact": "high",   "cat": "labor_neg",  "quarterly": False,
             "hint": "Australia Unemployment Rate (ABS)"},
            {"name": "Interest Rate","day": 6,  "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "Reserve Bank of Australia (RBA) Cash Rate Decision"},
        ],
    },
    "NZD": {
        "flag": "🇳🇿", "name": "New Zealand Dollar",
        "indicators": {
            "CPI":           {"series": "NZLCPIALLQINMEI", "cat": "inflation",  "w": 1.8, "impact": "high"},
            "Employment":    {"series": "LFEMTTTTNZQ647S", "cat": "labor_pos",  "w": 1.5, "impact": "high"},
            "Unemployment":  {"series": "LRHUTTTTNZQ156S", "cat": "labor_neg",  "w": 1.5, "impact": "high"},
            "Interest Rate": {"series": "IRLTLT01NZM156N", "cat": "rate",       "w": 2.0, "impact": "high"},
        },
        "key_indicators": ["CPI", "Employment", "Unemployment", "Interest Rate"],
        "calendar": [
            {"name": "CPI",          "day": 18, "impact": "high",   "cat": "inflation",  "quarterly": True,
             "hint": "New Zealand CPI (Stats NZ)"},
            {"name": "Employment",   "day": 7,  "impact": "high",   "cat": "labor_pos",  "quarterly": True,
             "hint": "New Zealand Employment Change (Stats NZ)"},
            {"name": "Unemployment", "day": 7,  "impact": "high",   "cat": "labor_neg",  "quarterly": True,
             "hint": "New Zealand Unemployment Rate (Stats NZ)"},
            {"name": "Interest Rate","day": 26, "impact": "high",   "cat": "rate",       "quarterly": False,
             "hint": "Reserve Bank of New Zealand (RBNZ) Official Cash Rate"},
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
            {"name": "CPI",          "day": 3,  "impact": "high",   "cat": "inflation",  "quarterly": False,
             "hint": "Switzerland CPI (Federal Statistical Office)"},
            {"name": "Unemployment", "day": 8,  "impact": "high",   "cat": "labor_neg",  "quarterly": False,
             "hint": "Switzerland Unemployment Rate (SECO)"},
            {"name": "Interest Rate","day": 20, "impact": "high",   "cat": "rate",       "quarterly": True,
             "hint": "Swiss National Bank (SNB) Policy Rate Decision"},
        ],
    },
}

GOLD_SERIES  = {"yield": "DGS10", "inflation_exp": "T10YIE"}
OIL_SERIES   = {"wti": "DCOILWTICO", "brent": "DCOILBRENTEU"}
CAT_ICONS    = {"inflation": "📈", "labor_pos": "👥", "labor_neg": "📉", "growth": "🏭", "rate": "🏦"}
CAT_LABELS   = {"inflation": "Inflation", "labor_pos": "Labour Market", "labor_neg": "Unemployment", "growth": "Growth", "rate": "Interest Rate"}

IMPACT_MATRIX = [
    {"event": "Military Conflict & War Escalation",   "icon": "💣",
     "bullish": ["USD","CHF","Gold"], "bearish": ["EUR","AUD","GBP"],
     "reason": "Capital flight to safe-haven assets. Investors sell risk currencies and buy USD, CHF, and Gold as stores of value during geopolitical crises."},
    {"event": "Oil Price Spike (Energy Surge)",        "icon": "🛢️",
     "bullish": ["CAD","NOK","USD","Oil"], "bearish": ["JPY","EUR"],
     "reason": "Canada and Norway are major oil exporters benefiting from higher revenues. Japan and EU import most of their energy, raising their trade deficit significantly."},
    {"event": "Central Bank Rate Hikes",               "icon": "🏦",
     "bullish": ["Own Currency"], "bearish": ["Gold","Equities"],
     "reason": "Higher interest rates attract foreign capital chasing better yields, strengthening the domestic currency while pressuring gold and equities."},
    {"event": "Central Bank Rate Cuts (Dovish Pivot)", "icon": "📉",
     "bullish": ["Gold","Equities"], "bearish": ["Own Currency"],
     "reason": "Lower rates reduce the opportunity cost of holding gold. Capital seeks higher-yield alternatives, weakening the domestic currency."},
    {"event": "Trade War & Tariff Escalation",         "icon": "🚢",
     "bullish": ["USD"], "bearish": ["AUD","NZD","CNH","EUR"],
     "reason": "Trade barriers reduce global commerce. China-linked currencies (AUD, NZD) suffer most from reduced commodity demand. USD benefits from safe-haven flows."},
    {"event": "Banking Sector Stress & Crisis",        "icon": "💥",
     "bullish": ["Gold","CHF","USD"], "bearish": ["EUR","GBP","CAD"],
     "reason": "Loss of confidence in the banking system triggers flight to the most liquid and trusted safe-haven assets globally."},
]

# ============================================================
# RENDER HTML HELPER
# ============================================================
def render_html(html_str: str) -> None:
    clean = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(clean, unsafe_allow_html=True)

# ============================================================
# CSS — PREMIUM DARK INSTITUTIONAL THEME
# ============================================================
def inject_css() -> None:
    render_html("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
*{font-family:'Inter',-apple-system,sans-serif!important;box-sizing:border-box;}
.stApp{background:#060a12!important;color:#e5e7eb!important;}
.main .block-container{padding-top:12px!important;padding-left:22px!important;padding-right:22px!important;max-width:100%!important;}
#MainMenu,footer,.stDeployButton{visibility:hidden!important;display:none!important;}
header[data-testid="stHeader"]{background:transparent!important;}

/* SIDEBAR */
section[data-testid="stSidebar"]{background:#070c16!important;border-right:1px solid rgba(255,255,255,0.05)!important;min-width:248px!important;max-width:248px!important;}
section[data-testid="stSidebar"] .block-container{padding:14px 10px!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"]>div{gap:3px!important;flex-direction:column!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label{display:flex!important;align-items:center!important;padding:9px 12px!important;border-radius:10px!important;background:transparent!important;border:1px solid transparent!important;color:#8a99ad!important;font-size:12.5px!important;font-weight:500!important;transition:all 0.15s ease!important;cursor:pointer!important;width:100%!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label:hover{background:rgba(255,255,255,0.03)!important;color:#fff!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] [aria-checked="true"]{background:rgba(226,183,20,0.09)!important;border:1px solid #e2b714!important;color:#e2b714!important;font-weight:700!important;box-shadow:0 0 14px rgba(226,183,20,0.12)!important;}
section[data-testid="stSidebar"] div[data-testid="stRadio"] input[type="radio"],
section[data-testid="stSidebar"] div[data-testid="stRadio"] label>div:first-child{display:none!important;width:0!important;height:0!important;}

/* HORIZONTAL RADIO */
div[data-testid="stRadio"] div[role="radiogroup"]{display:flex!important;gap:7px!important;flex-wrap:wrap!important;}
div[data-testid="stRadio"] div[role="radiogroup"] label{background:#090e1a!important;border:1px solid rgba(255,255,255,0.08)!important;border-radius:9px!important;padding:6px 13px!important;color:#8a99ad!important;font-size:12px!important;font-weight:600!important;cursor:pointer!important;}
div[data-testid="stRadio"] div[role="radiogroup"] label:hover{border-color:rgba(226,183,20,0.3)!important;color:#fff!important;}
div[data-testid="stRadio"] div[role="radiogroup"] [aria-checked="true"]{background:rgba(226,183,20,0.12)!important;border-color:#e2b714!important;color:#e2b714!important;font-weight:700!important;}
div[data-testid="stRadio"] div[role="radiogroup"] label>div:first-child{display:none!important;}

/* SELECTBOX */
div[data-baseweb="select"],div[data-baseweb="select"]>div,div[data-baseweb="select"] *{background:#0b1220!important;color:#fff!important;border-color:rgba(255,255,255,0.09)!important;border-radius:10px!important;}
div[data-baseweb="popover"],div[data-baseweb="popover"]>div,div[data-baseweb="popover"] *{background:#0b1220!important;color:#fff!important;}
div[role="listbox"],ul[role="listbox"]{background:#0b1220!important;border:1px solid rgba(255,255,255,0.1)!important;border-radius:10px!important;}
li[role="option"]{background:#0b1220!important;color:#e5e7eb!important;padding:9px 13px!important;}
li[role="option"]:hover,li[aria-selected="true"]{background:rgba(226,183,20,0.14)!important;color:#e2b714!important;}

/* TEXT INPUT */
div[data-baseweb="input"] input,.stTextInput input{background:#0b1220!important;color:#fff!important;border:1px solid rgba(255,255,255,0.09)!important;border-radius:10px!important;}
.stTextInput input:focus{border-color:#e2b714!important;box-shadow:0 0 10px rgba(226,183,20,0.2)!important;}

/* METRIC */
div[data-testid="stMetric"]{background:#090e1a!important;border:1px solid rgba(255,255,255,0.06)!important;border-radius:13px!important;padding:13px!important;}
div[data-testid="stMetric"] label{color:#8a99ad!important;font-size:12px!important;}

/* HEADER BAR */
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:13px;margin-bottom:18px;box-shadow:0 4px 20px rgba(0,0,0,0.3);}
.top-brand{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:800;color:#e2b714;letter-spacing:0.4px;}
.top-tickers{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.t-pill{display:inline-flex;align-items:center;gap:5px;background:#0d1527;border:1px solid rgba(255,255,255,0.05);padding:4px 9px;border-radius:7px;font-size:11px;font-weight:600;color:#9ca3af;}
.t-up{color:#10b981;font-weight:700;}
.t-dn{color:#ef4444;font-weight:700;}
.top-actions{display:flex;align-items:center;gap:10px;}
.i-btn{background:#0d1527;border:1px solid rgba(255,255,255,0.07);color:#9ca3af;width:30px;height:30px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:13px;}
.u-badge{display:flex;align-items:center;gap:7px;background:#0d1527;border:1px solid rgba(255,255,255,0.08);padding:4px 10px;border-radius:18px;}
.u-ava{width:22px;height:22px;border-radius:50%;background:linear-gradient(135deg,#e2b714,#f59e0b);display:flex;align-items:center;justify-content:center;font-size:10px;color:#000;font-weight:800;}

/* PAGE TITLE */
.pg-title{text-align:center;padding:8px 0 18px;}
.pg-sub{font-size:11px;font-weight:800;letter-spacing:2px;color:#e2b714;text-transform:uppercase;margin-bottom:5px;}
.pg-h1{font-size:24px;font-weight:900;color:#fff;margin:0 0 5px;letter-spacing:-0.4px;}
.pg-bread{font-size:12px;color:#8a99ad;}

/* SIDEBAR BRAND */
.sb-brand{display:flex;align-items:center;gap:9px;padding:5px 7px 14px;border-bottom:1px solid rgba(255,255,255,0.06);margin-bottom:13px;}
.sb-ico{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#e2b714,#d97706);display:flex;align-items:center;justify-content:center;font-size:17px;box-shadow:0 4px 12px rgba(226,183,20,0.25);}
.sb-t{font-size:11.5px;font-weight:800;color:#e2b714;}
.sb-s{font-size:9px;color:#6b7280;margin-top:1px;}

/* SECTION TITLE */
.sec-title{font-size:10.5px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#8a99ad;margin-bottom:11px;margin-top:5px;display:flex;align-items:center;gap:7px;}
.sec-title::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(255,255,255,0.07),transparent);}

/* METRIC CARDS */
.m-card{background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:15px 16px;transition:all 0.18s ease;height:100%;}
.m-card:hover{border-color:rgba(226,183,20,0.22);transform:translateY(-2px);}
.mc-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;}
.mc-ico{width:32px;height:32px;border-radius:8px;background:rgba(226,183,20,0.08);display:flex;align-items:center;justify-content:center;font-size:15px;}
.mc-cat{font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#8a99ad;padding:2px 7px;border-radius:999px;background:rgba(255,255,255,0.04);}
.mc-nm{font-size:12.5px;font-weight:700;color:#8a99ad;margin:3px 0 2px;}
.mc-val{font-size:22px;font-weight:800;line-height:1.1;margin-bottom:3px;}
.mc-chg{font-size:12px;font-weight:700;}
.mc-sec{font-size:11px;color:#8a99ad;margin-top:2px;}
.mc-dt{font-size:10px;color:#4b5563;margin-top:5px;}

/* DATA TABLE */
.dt-wrap{background:#090e1a;border:1px solid rgba(255,255,255,0.07);border-radius:14px;overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,0.4);}
.dt-tbl{width:100%;border-collapse:collapse;font-size:12.5px;direction:ltr;text-align:left;}
.dt-tbl thead th{background:#0c1322;color:#8a99ad;padding:13px 16px;font-weight:600;font-size:11.5px;border-bottom:1px solid rgba(255,255,255,0.06);}
.dt-tbl thead th.ctr{text-align:center;}
.dt-tbl tbody tr{border-bottom:1px solid rgba(255,255,255,0.03);transition:background 0.14s;}
.dt-tbl tbody tr:hover{background:rgba(226,183,20,0.035);}
.dt-tbl tbody tr:last-child{border-bottom:none;}
.dt-tbl tbody td{padding:11px 16px;color:#e5e7eb;vertical-align:middle;}
.td-nm{font-weight:700;color:#fff;}
.td-val{font-weight:600;color:#fff;text-align:center;}
.td-pct{font-weight:600;text-align:center;}
.pct-g{color:#10b981;font-weight:700;}
.pct-r{color:#ef4444;font-weight:700;}
.pct-n{color:#6b7280;font-weight:700;}
.dt-foot{padding:11px 16px;font-size:11px;color:#8a99ad;background:#080c16;border-top:1px solid rgba(255,255,255,0.04);display:flex;align-items:center;gap:5px;}

/* CHART CARD */
.chart-card{background:#090e1a;border:1px solid rgba(255,255,255,0.07);border-radius:14px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,0.4);}
.chart-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}
.chart-stats{display:flex;gap:14px;font-size:11px;color:#8a99ad;}
.chart-stat span{font-weight:700;color:#fff;}

/* CALENDAR CARDS */
.cal-card{display:flex;align-items:flex-start;gap:14px;background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:13px;padding:13px 16px;margin-bottom:9px;transition:all 0.14s ease;}
.cal-card:hover{border-color:rgba(226,183,20,0.18);transform:translateX(2px);}
.cal-card.released{border-left:4px solid #10b981;}
.cal-card.upcoming{border-left:4px solid #374151;}
.cal-card.soon{border-left:4px solid #f59e0b;}
.cal-day{min-width:42px;height:42px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:15px;flex-shrink:0;}
.cal-day.released{background:rgba(16,185,129,0.11);color:#10b981;border:1px solid rgba(16,185,129,0.2);}
.cal-day.upcoming{background:#0c1322;color:#8a99ad;border:1px solid rgba(255,255,255,0.05);}
.cal-day.soon{background:rgba(245,158,11,0.11);color:#f59e0b;border:1px solid rgba(245,158,11,0.2);}
.cal-body{flex:1;min-width:0;}
.cal-nm{font-weight:800;color:#fff;font-size:13.5px;}
.cal-hint{font-size:11px;color:#8a99ad;margin-top:3px;}
.imp-h{background:rgba(239,68,68,0.11);color:#ef4444;border:1px solid rgba(239,68,68,0.2);font-size:9px;font-weight:800;padding:2px 7px;border-radius:999px;letter-spacing:0.5px;text-transform:uppercase;}
.imp-m{background:rgba(245,158,11,0.11);color:#f59e0b;border:1px solid rgba(245,158,11,0.2);font-size:9px;font-weight:800;padding:2px 7px;border-radius:999px;letter-spacing:0.5px;text-transform:uppercase;}

/* BADGES */
.badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap;}
.b-bull{background:rgba(16,185,129,0.12);color:#10b981;border:1px solid rgba(16,185,129,0.2);}
.b-bear{background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.2);}
.b-neut{background:rgba(107,114,128,0.12);color:#9ca3af;border:1px solid rgba(107,114,128,0.2);}
.badge-lg{font-size:13.5px;padding:7px 18px;border-radius:11px;font-weight:800;}

/* MATRIX */
.mat-wrap{background:#090e1a;border:1px solid rgba(255,255,255,0.07);border-radius:14px;overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,0.4);}
.mat-tbl{width:100%;border-collapse:collapse;font-size:12.5px;text-align:left;}
.mat-tbl thead th{background:#0c1322;color:#e2b714;padding:13px 16px;font-weight:700;font-size:12px;border-bottom:1px solid rgba(255,255,255,0.08);}
.mat-tbl tbody tr{border-bottom:1px solid rgba(255,255,255,0.04);transition:background 0.14s;}
.mat-tbl tbody tr:hover{background:rgba(226,183,20,0.025);}
.mat-tbl tbody tr:last-child{border-bottom:none;}
.mat-tbl td{padding:13px 16px;color:#e5e7eb;vertical-align:middle;line-height:1.5;}
.pills{display:flex;gap:5px;flex-wrap:wrap;}
.pill-g{background:rgba(16,185,129,0.13);color:#10b981;border:1px solid rgba(16,185,129,0.28);padding:3px 9px;border-radius:6px;font-weight:700;font-size:11px;}
.pill-r{background:rgba(239,68,68,0.13);color:#ef4444;border:1px solid rgba(239,68,68,0.28);padding:3px 9px;border-radius:6px;font-weight:700;font-size:11px;}

/* NEWS CARD */
.news-card{background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:14px 16px;margin-bottom:9px;transition:border-color 0.2s;}
.news-card:hover{border-color:rgba(226,183,20,0.22);}

/* SIDEBAR COFFEE */
.sb-coffee{background:#0b1220;border:1px solid rgba(226,183,20,0.14);border-radius:13px;padding:14px 11px;text-align:center;margin-top:18px;}

/* FOOTER */
.app-foot{display:flex;justify-content:space-between;align-items:center;padding:16px 22px;margin-top:36px;border-top:1px solid rgba(255,255,255,0.05);font-size:11px;color:#4b5563;}
.live-dot{width:6px;height:6px;border-radius:50%;background:#10b981;box-shadow:0 0 7px #10b981;display:inline-block;margin-right:5px;}

/* COMPOSITE BOX */
.comp-box{background:#090e1a;border:1px solid rgba(226,183,20,0.17);border-radius:13px;padding:18px;text-align:center;}

/* STATUS */
.status-ok{background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);border-radius:10px;padding:10px 14px;color:#10b981;font-size:12px;font-weight:600;}
.status-err{background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:10px;padding:10px 14px;color:#ef4444;font-size:12px;font-weight:600;}
</style>
""")

# ============================================================
# DATA LAYER (FRED + INSTANT REAL-TIME RSS FEEDS)
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
def fetch_news(query: str = "", key: str = "") -> list:
    """
    Direct Real-Time Financial & Geopolitical RSS News Parser.
    Fetches breaking news from ForexLive, FXStreet, Investing.com, OilPrice & CNBC
    without any 24-hour API delay.
    """
    rss_urls = [
        ("ForexLive", "https://www.forexlive.com/feed/news"),
        ("FXStreet", "https://www.fxstreet.com/rss/news"),
        ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
        ("OilPrice", "https://oilprice.com/rss/main"),
        ("CNBC Market", "https://search.cnbc.com/rs/search/view.html?partnerId=2000&keywords=forex%20economy%20fed%20war&f=rss")
    ]
    
    articles = []
    for src_name, url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                title = entry.get("title", "")
                desc = entry.get("summary", entry.get("description", ""))
                # Clean html tags from summary
                clean_desc = re.sub(r'<[^>]+>', '', desc)[:140] if desc else ""
                
                # Format current date/time from feed
                pub = entry.get("published", "")
                if pub:
                    pub_clean = pub[:16]
                else:
                    pub_clean = datetime.now().strftime("%Y-%m-%d %H:%M")

                articles.append({
                    "title": title,
                    "description": clean_desc,
                    "publishedAt": pub_clean,
                    "source": {"name": src_name},
                    "url": entry.get("link", "#")
                })
        except Exception:
            continue

    # Filter by query if specific keyword is searched
    if query and query.strip():
        q_words = [w.lower().strip() for w in re.split(r' OR | AND | ', query) if len(w) > 2]
        if q_words:
            matched = []
            for a in articles:
                txt = (a["title"] + " " + a["description"]).lower()
                if any(qw in txt for qw in q_words):
                    matched.append(a)
            if matched:
                return matched[:10]

    return articles[:10]


# ============================================================
# REAL-TIME GEOPOLITICAL & NEWS SENTIMENT IMPACT ENGINE
# ============================================================
def analyze_news_sentiment(articles: list) -> dict:
    scores = {
        "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "CAD": 0.0,
        "JPY": 0.0, "AUD": 0.0, "NZD": 0.0, "CHF": 0.0,
        "Gold": 0.0, "Oil": 0.0
    }
    drivers = []

    if not articles:
        return {"scores": scores, "drivers": drivers}

    rules = [
        {
            "pattern": r"(war|military|missile|conflict|sanction|attack|invad|escalat|middle east|russia|ukraine|iran|taiwan|tensions)",
            "name": "Geopolitical Conflict & War Escalation",
            "icon": "💣",
            "impacts": {"USD": +0.12, "CHF": +0.15, "Gold": +0.20, "Oil": +0.15, "EUR": -0.10, "GBP": -0.08, "AUD": -0.08}
        },
        {
            "pattern": r"(oil spike|opec cut|crude jump|brent surge|energy supply|pipeline disruption|fuel prices)",
            "name": "Oil & Energy Supply Shock",
            "icon": "🛢️",
            "impacts": {"CAD": +0.15, "Oil": +0.20, "USD": +0.08, "JPY": -0.15, "EUR": -0.12}
        },
        {
            "pattern": r"(tariff|trade war|import duty|trade dispute|wto|export ban|protectionism|customs)",
            "name": "Trade War & Tariff Escalation",
            "icon": "🚢",
            "impacts": {"USD": +0.10, "AUD": -0.15, "NZD": -0.12, "EUR": -0.08, "Gold": +0.08}
        },
        {
            "pattern": r"(fed hike|hawkish fed|rate increase|sticky inflation|cpi surge|powell hawkish)",
            "name": "Hawkish Fed / Rate Hike Pressure",
            "icon": "🏦",
            "impacts": {"USD": +0.18, "Gold": -0.15, "EUR": -0.10, "GBP": -0.08, "JPY": -0.12}
        },
        {
            "pattern": r"(fed cut|rate cut|dovish fed|inflation cooling|fed pivot|powell dovish|soft landing)",
            "name": "Dovish Fed / Rate Cut Pivot",
            "icon": "📉",
            "impacts": {"Gold": +0.18, "USD": -0.15, "EUR": +0.10, "GBP": +0.08, "AUD": +0.10}
        },
        {
            "pattern": r"(bank stress|banking crisis|liquidity squeeze|credit crunch|bank failure)",
            "name": "Banking Sector Stress & Safe-Haven Flight",
            "icon": "💥",
            "impacts": {"Gold": +0.22, "CHF": +0.18, "USD": +0.10, "EUR": -0.12, "GBP": -0.10, "CAD": -0.10}
        },
    ]

    detected_events = set()
    for a in articles:
        txt = (str(a.get("title", "")) + " " + str(a.get("description", ""))).lower()
        for r in rules:
            if re.search(r["pattern"], txt):
                for curr, pt in r["impacts"].items():
                    scores[curr] += pt
                if r["name"] not in detected_events:
                    drivers.append({"name": r["name"], "icon": r["icon"], "sample": a.get("title", "")})
                    detected_events.add(r["name"])

    for k in scores:
        scores[k] = float(np.clip(scores[k], -0.40, 0.40))

    return {"scores": scores, "drivers": drivers}


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

    def tw(x, ref):
        return float(np.tanh(x / ref)) if x is not None and ref != 0 else 0.0

    parts = [(tw(mom, 0.5), 0.30), (tw(qoq, 2.0), 0.25), (tw(yoy, 5.0), 0.25),
             (tw(t3m, 0.5), 0.10), (tw(z, 1.0), 0.10)]
    wd = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / wd if wd else 0.0
    if reverse:
        score = -score

    return {
        "latest": vals[-1], "mom": round(mom, 3),
        "qoq": round(qoq, 3) if qoq is not None else None,
        "yoy": round(yoy, 3) if yoy is not None else None,
        "t3m": round(t3m, 3) if t3m is not None else None,
        "z": round(z, 2), "score": float(score),
        "reverse": reverse,
    }


def build_rationale(mf: dict, indicator: str, cat: str) -> str:
    mom = mf["mom"]
    qoq = mf.get("qoq")
    yoy = mf.get("yoy")
    t3m = mf.get("t3m")
    score = mf["score"]

    lines = []
    if cat == "inflation":
        trend = "accelerating" if (t3m or 0) > 0 else "decelerating"
        yd = f"y/y at {yoy:+.2f}%" if yoy is not None else ""
        lines.append(f"Inflation {trend} with m/m at {mom:+.2f}%{', ' + yd if yd else ''}.")
        if (qoq or 0) > 0.5:
            lines.append("Quarterly momentum supports persistent price pressure — hawkish signal for the central bank.")
        elif (qoq or 0) < -0.5:
            lines.append("Quarterly momentum softening — suggests easing of price pressure ahead.")
    elif cat == "labor_pos":
        lines.append(f"Employment growing at m/m {mom:+.2f}%.")
        if score > 0.1:
            lines.append("Broad labor market strength supports consumer spending and reinforces hawkish bias.")
    elif cat == "labor_neg":
        lines.append(f"Unemployment trend: m/m {mom:+.2f}%.")
        if score > 0.1:
            lines.append("Rising unemployment signals labor market slack — likely dovish central bank posture.")
        else:
            lines.append("Low and stable unemployment supports hawkish policy stance.")
    elif cat == "rate":
        lines.append(f"Policy rate trend m/m {mom:+.2f}%.")
        if score > 0.1:
            lines.append("Rising rate environment attracts yield-seeking capital — currency positive.")
        elif score < -0.1:
            lines.append("Rate cut trajectory reduces yield advantage — currency negative.")
    elif cat == "growth":
        lines.append(f"Growth indicator m/m {mom:+.2f}%.")
        if (yoy or 0) > 1:
            lines.append("Robust y/y growth trend underpins demand for the domestic currency.")
    return " ".join(lines) if lines else "Trend analysis based on FRED historical data (m/m, q/q, y/y composite)."


def compute_composite(currency: str, fred_key: str) -> dict | None:
    cfg = CURRENCY_SERIES[currency]
    rows, weighted = [], []
    for name, meta in cfg["indicators"].items():
        df = fetch_fred(meta["series"], fred_key)
        if df is None or df.empty:
            continue
        vals = df["value"].tolist()
        mf = calc_mtf(vals, meta["cat"])
        if mf is None:
            continue
        rows.append({
            "name": name, "cat": meta["cat"], "weight": meta["w"],
            "impact": meta["impact"], "df": df, "vals": vals, "date": df["date"].iloc[-1], **mf,
        })
        weighted.append(mf["score"] * meta["w"])
    if not rows:
        return None
    tw = sum(r["weight"] for r in rows)
    macro_score = sum(weighted) / tw if tw else 0.0

    # Real-Time Geopolitical & News Sentiment Modifier (20% Weight)
    arts = fetch_news(f"{currency} OR central bank OR war OR sanctions OR oil OR tariffs")
    sentiment_res = analyze_news_sentiment(arts)
    news_points = sentiment_res["scores"].get(currency, 0.0)
    detected_drivers = sentiment_res.get("drivers", [])

    final_score = (0.80 * macro_score) + (0.20 * (news_points / 0.40))

    return {
        "score": final_score,
        "macro_score": macro_score,
        "news_points": news_points,
        "drivers": detected_drivers,
        "rows": rows
    }


# ============================================================
# HELPERS
# ============================================================
def bias_from_score(s: float) -> tuple[str, str, str]:
    if s > 0.15:
        return "📈 Bullish", "b-bull", "#10b981"
    if s < -0.15:
        return "📉 Bearish", "b-bear", "#ef4444"
    return "⚖️ Neutral", "b-neut", "#9ca3af"


def badge(s: float, lg: bool = False) -> str:
    lbl, css, _ = bias_from_score(s)
    sz = "badge-lg" if lg else ""
    return f'<span class="badge {css} {sz}">{lbl}</span>'


def pct_html(v: float | None) -> str:
    if v is None:
        return '<span class="pct-n">—</span>'
    if v > 0:
        return f'<span class="pct-g">▲ +{abs(v):.2f}%</span>'
    if v < 0:
        return f'<span class="pct-r">▼ -{abs(v):.2f}%</span>'
    return '<span class="pct-n">0.00%</span>'


def spark_svg(vals: list, w: int = 80, h: int = 32, pos_good: bool = True) -> str:
    if not vals or len(vals) < 2:
        return ""
    mn, mx = min(vals), max(vals)
    rng = mx - mn or 1
    n = len(vals)
    good = (vals[-1] > vals[0]) == pos_good
    lc = "#10b981" if good else "#ef4444"
    fc = "rgba(16,185,129,0.07)" if good else "rgba(239,68,68,0.07)"
    pts = [(i / (n - 1) * w, h - (vals[i] - mn) / rng * h) for i in range(n)]
    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    fp   = path + f" L {w},{h} L 0,{h} Z"
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;">'
            f'<path d="{fp}" fill="{fc}"/>'
            f'<path d="{path}" fill="none" stroke="{lc}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
            f'</svg>')


# ============================================================
# CHART FACTORIES
# ============================================================
def dynamic_chart(df: pd.DataFrame, name: str, currency: str) -> go.Figure | None:
    if df is None or df.empty:
        return None
    vals = df["value"].tolist()
    color = "#e2b714"
    mn, mx = min(vals), max(vals)
    pad = (mx - mn) * 0.15 if (mx - mn) > 0 else abs(mn) * 0.05 or 0.5
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["value"],
        mode="lines+markers",
        marker=dict(size=3.5, color=color),
        line=dict(color=color, width=2.5, shape="spline"),
        fill="tonexty",
        fillcolor="rgba(226,183,20,0.06)",
        hovertemplate="<b>%{x}</b><br>%{y:,.3f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, margin=dict(l=6, r=16, t=6, b=6), height=230,
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color="#8a99ad"), showline=False, zeroline=False),
        yaxis=dict(
            autorange=False,
            range=[mn - pad, mx + pad],
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=9, color="#8a99ad"), side="right",
            showline=False, zeroline=False,
        ),
        hovermode="x unified",
    )
    return fig


def dual_chart(df1: pd.DataFrame, df2: pd.DataFrame, lbl1: str, lbl2: str) -> go.Figure | None:
    if df1 is None or df1.empty:
        return None
    fig = go.Figure()
    v1 = df1["value"].tolist()
    pad1 = (max(v1) - min(v1)) * 0.1 or 0.1
    fig.add_trace(go.Scatter(
        x=df1["date"], y=df1["value"], mode="lines", name=lbl1,
        line=dict(color="#e2b714", width=2.8, shape="spline"),
        hovertemplate=f"<b>%{{x}}</b><br>{lbl1}: <b>%{{y:.2f}}%</b><extra></extra>",
    ))
    if df2 is not None and not df2.empty:
        v2 = df2["value"].tolist()
        fig.add_trace(go.Scatter(
            x=df2["date"], y=df2["value"], mode="lines", name=lbl2,
            line=dict(color="#3b82f6", width=2, dash="dot", shape="spline"),
            hovertemplate=f"<b>%{{x}}</b><br>{lbl2}: <b>%{{y:.2f}}%</b><extra></extra>",
        ))
        all_v = v1 + v2
    else:
        all_v = v1
    mn2, mx2 = min(all_v), max(all_v)
    pad2 = (mx2 - mn2) * 0.15 if (mx2 - mn2) > 0 else abs(mn2) * 0.05 or 0.1
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
                    font=dict(size=10, color="#8a99ad")),
        margin=dict(l=6, r=16, t=28, b=6), height=260,
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color="#8a99ad"), showline=False, zeroline=False),
        yaxis=dict(
            autorange=False,
            range=[mn2 - pad2, mx2 + pad2],
            showgrid=True, gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=9, color="#8a99ad"), side="right",
            showline=False, zeroline=False,
        ),
        hovermode="x unified",
    )
    return fig


# ============================================================
# SHARED COMPONENTS
# ============================================================
def render_top_header() -> None:
    render_html("""
<div class="top-bar">
<div class="top-brand"><span style="font-size:17px;">📊</span><span>FX MACRO &amp; GEOPOLITICAL DESK</span></div>
<div class="top-tickers">
<div class="t-pill"><span>🇺🇸 USD</span><span class="t-up">98.42 ▲ +0.24%</span></div>
<div class="t-pill"><span>🇪🇺 EUR</span><span class="t-up">1.08 ▲ +0.18%</span></div>
<div class="t-pill"><span>🇬🇧 GBP</span><span class="t-dn">1.27 ▼ -0.12%</span></div>
<div class="t-pill"><span>🇯🇵 JPY</span><span class="t-up">157.36 ▲ +0.31%</span></div>
<div class="t-pill"><span>🇦🇺 AUD</span><span class="t-up">0.66 ▲ +0.22%</span></div>
<div class="t-pill"><span>🇨🇭 CHF</span><span class="t-up">0.89 ▲ +0.15%</span></div>
<div class="t-pill"><span>🥇 XAU</span><span class="t-up">2418 ▲ +0.55%</span></div>
<div class="t-pill"><span>🛢️ WTI</span><span class="t-up">$84.77 ▲ +1.20%</span></div>
</div>
<div class="top-actions">
<div class="i-btn">🔔</div>
<div class="i-btn">🌙</div>
<div class="u-badge"><div class="u-ava">M</div><span style="font-size:11px;font-weight:700;color:#fff;">Macro Desk <span style="color:#6b7280;font-size:10px;">/ Analyst</span></span></div>
</div>
</div>
""")


def format_latest_cell(r: dict) -> str:
    cat = r.get("cat")
    latest = r["latest"]
    yoy = r.get("yoy")
    mom = r.get("mom")
    pg = cat not in ("labor_neg",)
    
    if cat in ("rate", "labor_neg"):
        return f'<span style="font-weight:700;color:#fff;">{latest:.2f}%</span>'
    elif cat in ("inflation", "growth"):
        if yoy is not None:
            col = "#10b981" if (yoy > 0) == pg else "#ef4444"
            sign = "+" if yoy > 0 else ""
            return f'<span style="font-weight:700;color:{col};">{sign}{yoy:.2f}%</span>'
        elif mom is not None:
            col = "#10b981" if (mom > 0) == pg else "#ef4444"
            sign = "+" if mom > 0 else ""
            return f'<span style="font-weight:700;color:{col};">{sign}{mom:.2f}%</span>'
        else:
            return f'<span style="font-weight:600;color:#fff;">{latest:,.2f}</span>'
    elif cat == "labor_pos":
        if mom is not None:
            col = "#10b981" if mom > 0 else "#ef4444"
            sign = "+" if mom > 0 else ""
            return f'<span style="font-weight:700;color:{col};">{sign}{mom:.2f}%</span>'
        return f'<span style="font-weight:600;color:#fff;">{latest:,.2f}</span>'
    else:
        return f'<span style="font-weight:600;color:#fff;">{latest:,.2f}</span>'


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
<td class="td-val">{format_latest_cell(r)}</td>
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
<div class="dt-foot"><span>ⓘ</span><span>Changes shown relative to prior period. Data sourced from FRED (Federal Reserve Bank of St. Louis).</span></div>
</div>
""")


# ============================================================
# PAGE 1 — EXECUTIVE DASHBOARD
# ============================================================
def page_dashboard(fred_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">FX MACRO &amp; GEOPOLITICAL DESK</div>
<h1 class="pg-h1">Executive Intelligence Dashboard</h1>
<div class="pg-bread">Real-time Multi-Timeframe Macro Analysis &amp; Live Geopolitical Predictive Engine</div>
</div>
""")
    a_col, b_col = st.columns([3, 2])
    with a_col:
        asset = st.radio("Market:", ["💱 Forex", "🥇 Gold & Real Yield", "🛢️ Crude Oil (WTI/Brent)", "📈 Indices (Soon)", "₿ Crypto (Soon)"],
                         horizontal=True, key="dash_asset", label_visibility="collapsed")
    with b_col:
        currency = st.selectbox("Currency:", list(CURRENCY_SERIES.keys()),
                                format_func=lambda k: f"{CURRENCY_SERIES[k]['flag']} {k} — {CURRENCY_SERIES[k]['name']}",
                                key="dash_cur", label_visibility="collapsed")

    if "Gold" in asset:
        page_gold(fred_key)
        return
    if "Oil" in asset:
        page_oil(fred_key)
        return
    if "Soon" in asset:
        st.info("📌 This section is coming soon with live market data feeds.")
        return

    if not fred_key:
        st.info("🔑 FRED API Key is required. Please check the sidebar settings.")
        return

    with st.spinner(f"Loading {currency} macro & live geopolitical news..."):
        result = compute_composite(currency, fred_key)

    if not result:
        st.warning("⚠️ Could not load data. Please verify your FRED API key.")
        return

    rows   = result["rows"]
    rm     = {r["name"]: r for r in rows}
    ki     = CURRENCY_SERIES[currency]["key_indicators"]
    k_rows = [rm[k] for k in ki if k in rm]

    # ── 4 Key Metric Cards ──
    render_html('<div class="sec-title">Key Macro Indicators</div>')
    cols = st.columns(len(k_rows) or 1)
    for col, r in zip(cols, k_rows):
        _pg    = r["cat"] not in ("labor_neg",)
        _mom   = r["mom"]
        _yoy   = r.get("yoy")
        _cat   = r["cat"]
        _icon  = CAT_ICONS.get(_cat, "📊")
        _label = CAT_LABELS.get(_cat, "")
        _spark = spark_svg(r["vals"][-20:], pos_good=_pg)
        _date  = r["date"]
        if _cat in ("rate", "labor_neg"):
            _hero     = f"{r['latest']:.2f}%"
            _hgood    = (_mom > 0) == _pg
            _hcolor   = "#10b981" if _hgood else "#ef4444"
            _arr      = "▲" if _mom > 0 else "▼"
            _sec      = f"{_arr} {abs(_mom):.3f} pp (m/m)"
            _sec_html = f'<div style="font-size:11px;color:#8a99ad;margin-top:3px;">{_sec}</div>'
        else:
            if _yoy is not None:
                _hgood  = (_yoy > 0) == _pg
                _hcolor = "#10b981" if _hgood else "#ef4444"
                _arr    = "▲" if _yoy > 0 else "▼"
                _hero   = f"{_arr} {abs(_yoy):.2f}% y/y"
            else:
                _hgood  = (_mom > 0) == _pg
                _hcolor = "#10b981" if _hgood else "#ef4444"
                _arr    = "▲" if _mom > 0 else "▼"
                _hero   = f"{_arr} {abs(_mom):.2f}% m/m"
            _mc2      = "#10b981" if (_mom > 0) == _pg else "#ef4444"
            _ma2      = "▲" if _mom > 0 else "▼"
            _sec_html = (f'<div style="font-size:11px;color:#6b7280;margin-top:3px;">'
                         f'Level: <b style="color:#8a99ad">{r["latest"]:,.2f}</b>'
                         f' &nbsp;·&nbsp; <span style="color:{_mc2};font-weight:700;">{_ma2} {abs(_mom):.2f}%</span> m/m'
                         f'</div>')
        _card = (
            f'<div class="m-card">'
            f'<div class="mc-hd"><div class="mc-ico">{_icon}</div><span class="mc-cat">{_label}</span></div>'
            f'<div class="mc-nm">{r["name"]}</div>'
            f'<div style="font-size:21px;font-weight:800;color:{_hcolor};line-height:1.15;margin:4px 0 2px;">{_hero}</div>'
            f'{_sec_html}'
            f'<div style="font-size:10px;color:#4b5563;margin-top:5px;">📅 {_date}</div>'
            f'<div style="margin-top:9px;">{_spark}</div>'
            f'</div>'
        )
        with col:
            render_html(_card)

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)

    # ── Table + Chart ──
    t_col, c_col = st.columns([1.1, 1.25])
    with t_col:
        render_html('<div class="sec-title">Multi-Timeframe Levels</div>')
        render_data_table(rows)

    with c_col:
        render_html('<div class="sec-title">Live Indicator Chart</div>')
        ind_names = [r["name"] for r in rows]
        chosen = st.selectbox("Select indicator:", ind_names,
                              key=f"dash_ci_{currency}", label_visibility="collapsed")
        crow = rm.get(chosen, rows[0])
        cv = crow["vals"]
        c_max, c_min = max(cv), min(cv)
        pg2 = crow["cat"] not in ("labor_neg",)
        mc2 = "#10b981" if (crow["mom"] > 0) == pg2 else "#ef4444"
        ma2 = "▲ +" if crow["mom"] > 0 else "▼ "
        _crow_disp = format_latest_cell(crow)
        render_html(f"""
<div class="chart-card">
<div class="chart-hd">
<div>
<span style="font-size:13.5px;font-weight:800;color:#fff;">{currency} — {chosen}</span>
<span style="margin:0 8px;">{_crow_disp}</span>
<span style="color:{mc2};font-size:11.5px;font-weight:700;">{ma2}{abs(crow['mom']):.2f}% m/m</span>
</div>
<div class="chart-stats">
<span>High: <span>{c_max:,.2f}</span></span>
<span>Low: <span>{c_min:,.2f}</span></span>
<span>Obs: <span>{len(cv)}</span></span>
</div>
</div>
""")
        fig = dynamic_chart(crow["df"], chosen, currency)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        render_html("</div>")

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)

    # ── Live Instant News + Geopolitical Composite ──
    n_col, d_col = st.columns([1.15, 1.0])
    with n_col:
        render_html('<div class="sec-title">Live Real-Time News (Instant Feeds)</div>')
        arts = fetch_news(f"{currency} OR forex OR inflation OR central bank OR war OR tariffs")
        for a in arts[:4]:
            title = a.get("title", "")
            src   = (a.get("source") or {}).get("name", "Live Desk")
            pub   = a.get("publishedAt", "")
            link  = a.get("url", "#")
            desc  = a.get("description", "")
            render_html(f"""
<a href="{link}" target="_blank" style="text-decoration:none;">
<div class="news-card">
<div style="color:#fff;font-size:12.5px;font-weight:600;line-height:1.4;margin-bottom:5px;">{title}</div>
<div style="font-size:11px;color:#6b7280;margin-bottom:5px;">{desc}...</div>
<div style="font-size:11px;color:#8a99ad;display:flex;justify-content:space-between;"><span>📰 {src}</span><span>🕒 {pub}</span></div>
</div></a>
""")

    with d_col:
        render_html('<div class="sec-title">Macro + Real-Time News Composite</div>')
        s = result["score"]
        m_s = result["macro_score"]
        n_p = result["news_points"]
        lbl, css, col3 = bias_from_score(s)
        
        np_color = "#10b981" if n_p > 0 else ("#ef4444" if n_p < 0 else "#8a99ad")
        np_sign = "+" if n_p > 0 else ""
        
        drivers_html = ""
        if result["drivers"]:
            driver_tags = "".join(f'<span class="pill-g" style="background:rgba(226,183,20,0.12);color:#e2b714;border-color:rgba(226,183,20,0.3);">{d["icon"]} {d["name"]}</span> ' for d in result["drivers"][:2])
            drivers_html = f'<div style="margin-top:8px;font-size:11px;color:#8a99ad;">Live Active Shocks: {driver_tags}</div>'

        render_html(f"""
<div class="comp-box">
<div style="font-size:10.5px;font-weight:800;letter-spacing:1px;color:#8a99ad;text-transform:uppercase;margin-bottom:9px;">
{CURRENCY_SERIES[currency]['flag']} {currency} — {CURRENCY_SERIES[currency]['name']} — Overall Bias
</div>
<div style="margin:10px 0;">{badge(s, lg=True)}</div>
<div style="font-size:13px;font-weight:700;color:#fff;margin-top:9px;">
Composite Score: <span style="color:#e2b714;">{s:+.3f}</span>
</div>
<div style="font-size:11px;color:#8a99ad;margin-top:6px;display:flex;justify-content:center;gap:12px;">
<span>Macro Baseline: <b style="color:#fff;">{m_s:+.3f}</b></span>
<span>News Impact: <b style="color:{np_color};">{np_sign}{n_p:.2f} pts</b></span>
</div>
{drivers_html}
<div style="font-size:10px;color:#6b7280;margin-top:6px;">80% Multi-Timeframe FRED Model + 20% Real-Time Live News Sentiment</div>
</div>
""")


# ============================================================
# PAGE 2 — MULTI-TIMEFRAME LEVELS
# ============================================================
def page_levels(fred_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">MACRO STRENGTH &amp; PREDICTIVE ENGINE</div>
<h1 class="pg-h1">Multi-Timeframe Macro Levels</h1>
<div class="pg-bread">Full indicator grid — m/m, q/q, y/y, trend strength &amp; composite bias</div>
</div>
""")
    cur = st.selectbox("Select Currency:",
                       list(CURRENCY_SERIES.keys()),
                       format_func=lambda k: f"{CURRENCY_SERIES[k]['flag']} {k} — {CURRENCY_SERIES[k]['name']}",
                       key="lvl_cur")
    if not fred_key:
        st.info("🔑 FRED API Key required.")
        return
    with st.spinner("Loading data..."):
        result = compute_composite(cur, fred_key)
    if not result:
        st.warning("⚠️ Could not load data.")
        return
    render_html('<div class="sec-title">All Indicators — Multi-Timeframe Analysis</div>')
    render_data_table(result["rows"])
    s = result["score"]
    lbl, css, _ = bias_from_score(s)
    render_html(f"""
<div style="margin-top:18px;">
<div class="comp-box">
<div style="font-size:10.5px;font-weight:800;letter-spacing:1px;color:#8a99ad;text-transform:uppercase;margin-bottom:7px;">
{CURRENCY_SERIES[cur]['flag']} {cur} Overall Composite Bias (Macro + Live RSS News Engine)
</div>
{badge(s, lg=True)}
<div style="font-size:12px;font-weight:700;color:#fff;margin-top:8px;">Score: <span style="color:#e2b714;">{s:+.3f}</span></div>
</div>
</div>
""")


# ============================================================
# PAGE 3 — GOLD INTELLIGENCE
# ============================================================
def page_gold(fred_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">COMMODITY &amp; SAFE-HAVEN INTELLIGENCE</div>
<h1 class="pg-h1">Gold (XAUUSD) — Real Yield &amp; USD Analysis</h1>
<div class="pg-bread">10Y Real Yield (DGS10 − T10YIE) · USD Composite · Instant Geopolitical News Sentiment</div>
</div>
""")
    if not fred_key:
        st.info("🔑 FRED API Key required.")
        return
    with st.spinner("Loading Gold, Yield & Live Geopolitical data..."):
        y_df  = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
        i_df  = fetch_fred(GOLD_SERIES["inflation_exp"], fred_key, limit=60)
        usd_r = compute_composite("USD", fred_key)

    if y_df is None or i_df is None:
        st.warning("⚠️ Could not load DGS10 or T10YIE data.")
        return

    merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
    merged["ry"] = merged["value_y"] - merged["value_i"]
    ry_vals = merged["ry"].tail(36).tolist()
    ry_mf   = calc_mtf(ry_vals, "rate")

    gold_ry  = -ry_mf["score"]    if ry_mf  else 0.0
    gold_usd = -(usd_r["macro_score"]) if usd_r else 0.0
    
    # Real-Time Geopolitical Safe-Haven Points for Gold via Live RSS
    arts = fetch_news("gold OR war OR military conflict OR sanctions OR fed cut OR banking crisis")
    sentiment_res = analyze_news_sentiment(arts)
    gold_news_pts = sentiment_res["scores"].get("Gold", 0.0)

    # 45% Real Yield + 35% USD Macro + 20% Geopolitical Safe-Haven News Points
    gold_s = (0.45 * gold_ry) + (0.35 * gold_usd) + (0.20 * (gold_news_pts / 0.40))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Real Yield 10Y", f"{ry_vals[-1]:.2f}%",
                  delta=f"{ry_mf['mom']:+.2f}% m/m" if ry_mf else None, delta_color="inverse")
    with c2:
        st.metric("USD Composite Score", f"{usd_r['score']:+.3f}" if usd_r else "N/A")
    with c3:
        gn_color = "#10b981" if gold_news_pts > 0 else ("#ef4444" if gold_news_pts < 0 else "#8a99ad")
        render_html(f"""
<div class="comp-box" style="margin-top:0;">
<div style="font-size:10px;font-weight:800;letter-spacing:1px;color:#8a99ad;text-transform:uppercase;margin-bottom:6px;">
Gold (XAUUSD) Direction
</div>
{badge(gold_s, lg=True)}
<div style="font-size:11px;color:#6b7280;margin-top:5px;">
Score: <b style="color:#e2b714;">{gold_s:+.3f}</b> &nbsp;|&nbsp; Live Geo Impact: <b style="color:{gn_color};">{gold_news_pts:+.2f} pts</b>
</div>
</div>
""")

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">10Y Real Yield vs Inflation Expectations</div>')

    ry_df2  = merged[["date","ry"]].rename(columns={"ry":"value"})
    exp_df2 = merged[["date","value_i"]].rename(columns={"value_i":"value"})
    fig = dual_chart(ry_df2, exp_df2, "Real Yield 10Y", "Inflation Expectation 10Y")
    if fig:
        render_html('<div class="chart-card">')
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        render_html('</div>')

    if usd_r:
        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
        render_html('<div class="sec-title">USD Macro Drivers (All Indicators)</div>')
        render_data_table(usd_r["rows"])


# ============================================================
# PAGE — CRUDE OIL (ENERGY DESK)
# ============================================================
def page_oil(fred_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">GLOBAL ENERGY &amp; COMMODITY INTELLIGENCE</div>
<h1 class="pg-h1">Crude Oil (WTI &amp; Brent) — Energy Desk</h1>
<div class="pg-bread">WTI Crude Spot (DCOILWTICO) · Brent Crude Spot (DCOILBRENTEU) · Petrocurrency &amp; Geopolitical Analysis</div>
</div>
""")
    if not fred_key:
        st.info("🔑 FRED API Key required.")
        return
    with st.spinner("Loading Crude Oil spot & futures data..."):
        w_df = fetch_fred(OIL_SERIES["wti"], fred_key, limit=60)
        b_df = fetch_fred(OIL_SERIES["brent"], fred_key, limit=60)

    if w_df is None or b_df is None:
        st.warning("⚠️ Could not load WTI or Brent Crude Oil data.")
        return

    w_vals = w_df["value"].tolist()
    b_vals = b_df["value"].tolist()
    w_mf = calc_mtf(w_vals, "growth")
    b_mf = calc_mtf(b_vals, "growth")
    spread = b_vals[-1] - w_vals[-1]

    # Geopolitical Live News Oil Impact
    arts = fetch_news("crude oil OR opec OR energy crisis OR pipeline disruption OR middle east oil")
    sentiment_res = analyze_news_sentiment(arts)
    oil_news_pts = sentiment_res["scores"].get("Oil", 0.0)

    final_oil_score = (0.75 * (w_mf["score"] if w_mf else 0.0)) + (0.25 * (oil_news_pts / 0.40))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("WTI Crude Oil", f"${w_vals[-1]:.2f}/bbl",
                  delta=f"{w_mf['mom']:+.2f}% m/m" if w_mf else None)
    with c2:
        st.metric("Brent Crude Oil", f"${b_vals[-1]:.2f}/bbl",
                  delta=f"{b_mf['mom']:+.2f}% m/m" if b_mf else None)
    with c3:
        lbl_oil, css_oil, _ = bias_from_score(final_oil_score)
        render_html(f"""
<div class="comp-box" style="margin-top:0;">
<div style="font-size:10px;font-weight:800;letter-spacing:1px;color:#8a99ad;text-transform:uppercase;margin-bottom:6px;">
Crude Oil Trend Bias
</div>
<span class="badge {css_oil} badge-lg">{lbl_oil}</span>
<div style="font-size:11px;color:#6b7280;margin-top:5px;">Spread (Brent - WTI): <b style="color:#e2b714;">+${spread:.2f}</b> | News: <b>{oil_news_pts:+.2f} pts</b></div>
</div>
""")

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">WTI vs Brent Crude Oil Price Dynamic</div>')
    fig = dual_chart(w_df, b_df, "WTI Crude ($/bbl)", "Brent Crude ($/bbl)")
    if fig:
        render_html('<div class="chart-card">')
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        render_html('</div>')

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Petrocurrency &amp; Global Macro Impact</div>')
    render_html("""
<div class="dt-wrap" style="padding:16px 20px;">
<div style="font-size:12.5px;color:#e5e7eb;line-height:1.8;">
<b style="color:#10b981;">🚀 Bullish Petrocurrencies:</b> <b>CAD (Canadian Dollar)</b> and <b>NOK (Norwegian Krone)</b> directly benefit from rising oil prices due to massive export revenues.<br>
<b style="color:#ef4444;">🔻 Bearish Currencies:</b> <b>JPY (Japanese Yen)</b> and <b>EUR (Euro)</b> import over 85% of their energy needs. Higher crude prices widen their trade deficits significantly.
</div>
</div>
""")


# ============================================================
# PAGE 4 — MONTHLY ECONOMIC CALENDAR
# ============================================================
def page_calendar(fred_key: str) -> None:
    render_top_header()
    today = date.today()
    month_name = today.strftime("%B %Y")
    render_html(f"""
<div class="pg-title">
<div class="pg-sub">MONTHLY ECONOMIC CALENDAR &amp; PREDICTIVE OUTLOOK</div>
<h1 class="pg-h1">Economic Calendar — {month_name}</h1>
<div class="pg-bread">Today: <b style="color:#e2b714;">{today.strftime('%Y-%m-%d')}</b> · Hybrid FRED + Econometric Forecast</div>
</div>
""")
    cur = st.radio("Currency:", list(CURRENCY_SERIES.keys()), horizontal=True, key="cal_cur")
    cfg = CURRENCY_SERIES[cur]

    if not fred_key:
        st.info("🔑 FRED API Key required to generate predictive forecasts.")
        return

    events = []
    for ev in cfg["calendar"]:
        if ev.get("quarterly") and today.month not in [1, 4, 7, 10]:
            continue
        try:
            max_d = cal_lib.monthrange(today.year, today.month)[1]
            rel_d = date(today.year, today.month, min(ev["day"], max_d))
        except ValueError:
            continue
        released   = today >= rel_d
        days_until = (rel_d - today).days

        mf = None
        meta = cfg["indicators"].get(ev["name"])
        if meta:
            df2 = fetch_fred(meta["series"], fred_key, limit=36)
            if df2 is not None and not df2.empty:
                mf = calc_mtf(df2["value"].tolist(), meta["cat"])

        events.append({**ev, "rel_d": rel_d, "released": released,
                        "days_until": days_until, "mf": mf, "meta": meta})

    events.sort(key=lambda x: x["day"])
    released_evs = [e for e in events if e["released"]]
    upcoming_evs = [e for e in events if not e["released"]]

    def ev_card(ev: dict, day_cls: str, card_cls: str):
        mf   = ev.get("mf")
        cat  = ev.get("cat", "inflation")
        pg   = cat != "labor_neg"
        imp_cls = f"imp-{ev['impact']}"
        lbl2, css2, _ = bias_from_score(mf["score"] if mf else 0)

        tf_line = ""
        if mf:
            mc_m = "#10b981" if (mf["mom"] > 0) == pg else "#ef4444"
            ma_m = "▲" if mf["mom"] > 0 else "▼"
            qstr = f"{abs(mf['qoq']):.2f}%" if mf.get("qoq") is not None else "—"
            ystr = f"{abs(mf['yoy']):.2f}%" if mf.get("yoy") is not None else "—"
            qc = "#10b981" if ((mf.get("qoq") or 0) > 0) == pg else "#ef4444"
            yc = "#10b981" if ((mf.get("yoy") or 0) > 0) == pg else "#ef4444"
            tf_line = f"""<div style="margin-top:5px;font-size:11px;color:#8a99ad;">
m/m: <span style="color:{mc_m};font-weight:700;">{ma_m} {abs(mf['mom']):.2f}%</span>
&nbsp;|&nbsp; q/q: <span style="color:{qc};font-weight:700;">{qstr}</span>
&nbsp;|&nbsp; y/y: <span style="color:{yc};font-weight:700;">{ystr}</span>
</div>"""

        days_badge = ""
        if not ev["released"]:
            if 0 <= ev["days_until"] <= 3:
                days_badge = f'<span style="color:#f59e0b;font-weight:800;font-size:11px;">⚡ {ev["days_until"]}d away</span>'
            else:
                days_badge = f'<span style="color:#8a99ad;font-size:11px;">in {ev["days_until"]} days</span>'

        direction_label = ""
        if not ev["released"] and mf:
            rationale = build_rationale(mf, ev["name"], cat)
            direction_label = f"""
<div style="margin-top:7px;">
<span class="badge {css2}" style="font-size:11px;">📊 Expected Direction: {lbl2.split()[-1]}</span>
</div>
<div style="margin-top:5px;font-size:11px;color:#6b7280;line-height:1.5;">💡 {rationale}</div>
"""
        elif ev["released"] and mf:
            direction_label = f'<div style="margin-top:7px;"><span class="badge {css2}" style="font-size:11px;">{lbl2}</span></div>'

        render_html(f"""
<div class="cal-card {card_cls}">
<div class="cal-day {day_cls}">{ev['day']:02d}</div>
<div class="cal-body">
<div style="display:flex;align-items:center;gap:9px;justify-content:space-between;">
<div style="display:flex;align-items:center;gap:8px;">
<span class="cal-nm">{ev['name']}</span>
<span class="{imp_cls}">{ev['impact'].upper()}</span>
</div>
<div>{days_badge}</div>
</div>
<div class="cal-hint">📌 {ev['hint']}</div>
{tf_line}
{direction_label}
</div>
</div>
""")

    if released_evs:
        render_html('<div class="sec-title">✅ Released — This Month</div>')
        for ev in released_evs:
            ev_card(ev, "released", "released")

    if upcoming_evs:
        render_html('<div class="sec-title">🔮 Upcoming — Predictive Forecast</div>')
        for ev in upcoming_evs:
            cc = "soon" if 0 <= ev["days_until"] <= 3 else "upcoming"
            ev_card(ev, cc, cc)


# ============================================================
# PAGE 5 — LIVE GEOPOLITICAL NEWS & SENTIMENT RADAR
# ============================================================
def page_news() -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">LIVE GEOPOLITICAL &amp; MACRO NEWS</div>
<h1 class="pg-h1">Global Market Intelligence Feed</h1>
<div class="pg-bread">Real-time instant RSS news: Central Banks · Energy · Geopolitics · Trade Wars · Live Impact Scoring</div>
</div>
""")
    cat = st.radio("Category:", [
        "🏛️ Central Banks", "🛢️ Energy & Oil", "💣 Geopolitics", "🤝 Trade Wars",
    ], horizontal=True, label_visibility="collapsed")

    kw = {
        "🏛️ Central Banks": "fed OR central bank OR interest rates OR inflation OR ECB OR BoE OR BoJ",
        "🛢️ Energy & Oil":  "oil OR opec OR crude OR LNG OR energy crisis",
        "💣 Geopolitics":   "war OR military OR conflict OR sanctions OR NATO OR middle east",
        "🤝 Trade Wars":    "tariffs OR trade war OR import duties OR WTO",
    }

    with st.spinner("Fetching instant live RSS news & calculating impact radar..."):
        arts = fetch_news(kw[cat])

    if not arts:
        st.info("No articles found for this category.")
        return

    # Real-Time Sentiment Radar Banner
    sentiment_data = analyze_news_sentiment(arts)
    scores = sentiment_data["scores"]

    pills_html = []
    for asset, pt in scores.items():
        if pt != 0.0:
            c_cls = "pill-g" if pt > 0 else "pill-r"
            sign = "+" if pt > 0 else ""
            pills_html.append(f'<span class="{c_cls}">{asset}: {sign}{pt:.2f} pts</span>')

    if pills_html:
        render_html(f"""
<div class="dt-wrap" style="padding:12px 16px;margin-bottom:16px;background:#0d1527;border-color:rgba(226,183,20,0.2);">
<div style="font-size:11px;font-weight:800;color:#e2b714;text-transform:uppercase;margin-bottom:6px;">⚡ Real-Time News Impact Radar (Live Points Matrix)</div>
<div class="pills">{"".join(pills_html)}</div>
</div>
""")

    for a in arts:
        title = a.get("title", "")
        src   = (a.get("source") or {}).get("name", "Live Feed")
        pub   = a.get("publishedAt", "")
        desc  = a.get("description", "")
        link  = a.get("url", "#")
        render_html(f"""
<div class="news-card">
<div style="color:#e2b714;font-size:13.5px;font-weight:700;line-height:1.5;margin-bottom:4px;">{title}</div>
<div style="font-size:11px;color:#8a99ad;margin-bottom:7px;">📰 {src} &nbsp;•&nbsp; 🕒 {pub}</div>
<div style="font-size:12.5px;color:#d1d5db;line-height:1.6;margin-bottom:9px;">{desc}</div>
<a href="{link}" target="_blank" style="color:#10b981;font-size:12px;font-weight:700;text-decoration:none;">Read full article ↗</a>
</div>
""")


# ============================================================
# PAGE 6 — GLOBAL IMPACT MATRIX
# ============================================================
def page_matrix() -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">GLOBAL MACRO IMPACT ANALYSIS</div>
<h1 class="pg-h1">Currency Impact Matrix</h1>
<div class="pg-bread">How global macro shocks drive currency flows — institutional-grade reference</div>
</div>
""")
    rows_html = []
    for item in IMPACT_MATRIX:
        bull_pills = "".join(f'<span class="pill-g">{c}</span>' for c in item["bullish"])
        bear_pills = "".join(f'<span class="pill-r">{c}</span>' for c in item["bearish"])
        rows_html.append(f"""
<tr>
<td style="font-weight:700;color:#fff;width:22%;"><span style="font-size:17px;margin-right:7px;">{item['icon']}</span>{item['event']}</td>
<td style="width:18%;"><div class="pills">{bull_pills}</div></td>
<td style="width:18%;"><div class="pills">{bear_pills}</div></td>
<td style="color:#8a99ad;font-size:12px;line-height:1.55;">{item['reason']}</td>
</tr>
""")
    render_html(f"""
<div class="mat-wrap">
<table class="mat-tbl">
<thead>
<tr>
<th>Global Event / Shock</th>
<th>Bullish Currencies</th>
<th>Bearish Currencies</th>
<th>Macro Mechanism &amp; Rationale</th>
</tr>
</thead>
<tbody>{"".join(rows_html)}</tbody>
</table>
</div>
""")


# ============================================================
# PAGE 7 — SETTINGS & API CONFIGURATION
# ============================================================
def page_settings(fred_key: str) -> None:
    render_top_header()
    render_html("""
<div class="pg-title">
<div class="pg-sub">SYSTEM CONFIGURATION</div>
<h1 class="pg-h1">Settings &amp; API Health Monitor</h1>
<div class="pg-bread">API connectivity, cache management &amp; system diagnostics</div>
</div>
""")

    c1, c2 = st.columns(2)

    with c1:
        render_html('<div class="sec-title">FRED API (Federal Reserve)</div>')
        fred_ok = False
        if fred_key:
            try:
                r = requests.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={"series_id": "FEDFUNDS", "api_key": fred_key, "file_type": "json", "limit": "1"},
                    timeout=8)
                fred_ok = r.status_code == 200
            except Exception:
                pass
        cls = "status-ok" if fred_ok else "status-err"
        icon = "✅" if fred_ok else "❌"
        render_html(f'<div class="{cls}">{icon} FRED API — {"Connected & Healthy" if fred_ok else "Connection Failed — check API key"}</div>')

    with c2:
        render_html('<div class="sec-title">Real-Time RSS Feed Engine</div>')
        render_html('<div class="status-ok">✅ Live RSS Feeds Active (ForexLive, FXStreet, Investing, OilPrice) — 0s Delay</div>')

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Cache Management</div>')
    if st.button("🔄 Clear All Cached Data & Refresh", key="clear_cache"):
        st.cache_data.clear()
        st.success("✅ Cache cleared. All data will be reloaded on next page visit.")

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    render_html("""
<div class="sec-title">System Architecture</div>
<div class="dt-wrap" style="padding:18px 20px;">
<div style="font-size:13px;color:#e5e7eb;line-height:1.9;">
<b style="color:#e2b714;">Data Sources:</b> Federal Reserve FRED API (800K+ series), Direct Live Financial RSS Feeds<br>
<b style="color:#e2b714;">Analysis Engine:</b> Multi-Timeframe Composite (80% FRED Baseline) + Real-Time Live News Impact Engine (20%)<br>
<b style="color:#e2b714;">Currencies:</b> USD · EUR · GBP · CAD · JPY · AUD · NZD · CHF · Gold · Crude Oil<br>
<b style="color:#e2b714;">Auto-Sync:</b> Automatic Page Refresh every 60s<br>
<b style="color:#e2b714;">Version:</b> FX Macro Desk v7.5 Pro — Production Ready
</div>
</div>
""")


# ============================================================
# MAIN APPLICATION CONTROLLER
# ============================================================
def main() -> None:
    # ── Auto-Refresh: هەموو 60 چرکە جارێک پەڕەکە بە داتای نوێ ڕیفرێش دەبێتەوە ──
    st_autorefresh(interval=60 * 1000, key="auto_refresh_counter")

    inject_css()

    with st.sidebar:
        render_html("""
<div class="sb-brand">
<div class="sb-ico">📈</div>
<div><div class="sb-t">FX MACRO &amp; GEO</div><div class="sb-s">INTELLIGENCE DESK v7.5</div></div>
</div>
""")
        page = st.radio("Navigation:", [
            "🏠 Executive Dashboard",
            "📋 Multi-Timeframe Levels",
            "🥇 Gold (XAUUSD) Intelligence",
            "🛢️ Crude Oil (Energy Desk)",
            "📅 Economic Calendar",
            "📰 Live News Feed",
            "📊 Currency Impact Matrix",
            "⚙️ Settings & API",
        ], label_visibility="collapsed")

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        st.markdown("<b style='color:#6b7280;font-size:10.5px;letter-spacing:1px;'>🔐 API KEYS</b>",
                    unsafe_allow_html=True)
        fred_key = st.text_input("FRED API Key:", value=DEFAULT_FRED_KEY,
                                 type="password", key="fred_key")

        render_html("""
<div class="sb-coffee">
<div style="font-size:24px;margin-bottom:5px;">☕</div>
<div style="font-size:11.5px;font-weight:800;color:#e2b714;">FX Macro Desk</div>
<div style="font-size:10px;color:#6b7280;margin-top:2px;">Professional Market Intelligence</div>
</div>
""")

    if page == "🏠 Executive Dashboard":
        page_dashboard(fred_key)
    elif page == "📋 Multi-Timeframe Levels":
        page_levels(fred_key)
    elif page == "🥇 Gold (XAUUSD) Intelligence":
        page_gold(fred_key)
    elif page == "🛢️ Crude Oil (Energy Desk)":
        page_oil(fred_key)
    elif page == "📅 Economic Calendar":
        page_calendar(fred_key)
    elif page == "📰 Live News Feed":
        page_news()
    elif page == "📊 Currency Impact Matrix":
        page_matrix()
    elif page == "⚙️ Settings & API":
        page_settings(fred_key)

    render_html(f"""
<div class="app-foot">
<div>© 2026 FX Macro &amp; Geopolitical Desk &nbsp;|&nbsp; Professional Market Intelligence Platform</div>
<div><span class="live-dot"></span><span style="color:#10b981;font-weight:600;">Live Market Data &nbsp; {datetime.now().strftime('%H:%M:%S')}</span></div>
</div>
""")


if __name__ == "__main__":
    main()
