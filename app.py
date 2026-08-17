"""
FX Macro & News Intelligence Desk — v4
سیستەمی شیکاری فراوانتیمفریم
Multi-Timeframe: m/m • q/q • y/y • Trend 3m • Z-Level
"""

import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, date
import calendar as cal_lib

st.set_page_config(
    page_title="FX Macro & Geopolitical Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

REQUEST_TIMEOUT = 10

# ============================================================
# CONSTANTS
# ============================================================

CURRENCY_SERIES = {
    "USD دۆلار": {
        "CPI":           {"series": "CPIAUCSL",  "category": "inflation",  "weight": 1.5},
        "Core CPI":      {"series": "CPILFESL",  "category": "inflation",  "weight": 2.0},
        "PPI":           {"series": "PPIACO",    "category": "inflation",  "weight": 1.0},
        "Core PPI":      {"series": "WPSFD4131", "category": "inflation",  "weight": 1.2},
        "Core PCE":      {"series": "PCEPILFE",  "category": "inflation",  "weight": 2.0},
        "PCE":           {"series": "PCEPI",     "category": "inflation",  "weight": 1.3},
        "NFP":           {"series": "PAYEMS",    "category": "labor_good", "weight": 1.5},
        "Unemployment":  {"series": "UNRATE",    "category": "labor_bad",  "weight": 1.5},
        "Retail Sales":  {"series": "RSAFS",     "category": "growth",     "weight": 1.0},
        "GDP":           {"series": "GDP",        "category": "growth",     "weight": 1.3},
        "Interest Rate": {"series": "FEDFUNDS",  "category": "rate",       "weight": 1.5},
    },
    "GBP پاوەند": {
        "CPI":           {"series": "GBRCPIALLMINMEI",  "category": "inflation",  "weight": 1.5},
        "Core CPI":      {"series": "GBRCP01IXOBSAM",  "category": "inflation",  "weight": 1.8},
        "Production":    {"series": "GBRPROINDMISMEI",  "category": "growth",     "weight": 1.0},
        "Unemployment":  {"series": "LRUN64TTGBM156S",  "category": "labor_bad",  "weight": 1.5},
        "Interest Rate": {"series": "IRLTLT01GBM156N",  "category": "rate",       "weight": 1.3},
    },
    "CAD کەنەدی": {
        "CPI":           {"series": "CANCPIALLMINMEI", "category": "inflation",  "weight": 1.5},
        "Core CPI":      {"series": "CANCP01IXOBSAM",  "category": "inflation",  "weight": 1.8},
        "Employment":    {"series": "LFEMTTTTCAM647S", "category": "labor_good", "weight": 1.3},
        "Unemployment":  {"series": "LRUN64TTCAM156S", "category": "labor_bad",  "weight": 1.5},
        "Interest Rate": {"series": "IRLTLT01CAM156N", "category": "rate",       "weight": 1.3},
    },
    "JPY یەن": {
        "CPI":           {"series": "JPNCPIALLMINMEI", "category": "inflation",  "weight": 1.5},
        "Core CPI":      {"series": "JPNCP01IXOBSAM",  "category": "inflation",  "weight": 1.8},
        "Production":    {"series": "JPNPROINDMISMEI", "category": "growth",     "weight": 1.0},
        "Unemployment":  {"series": "LRUN64TTJPM156S", "category": "labor_bad",  "weight": 1.5},
        "Interest Rate": {"series": "IRLTLT01JPM156N", "category": "rate",       "weight": 1.3},
    },
}

KEY_INDICATORS = {
    "USD دۆلار":  ["Core CPI", "Core PCE", "NFP", "Interest Rate"],
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

MONTHLY_CALENDAR = {
    "USD دۆلار": [
        {"name": "NFP",          "day": 4,  "hint": "یەکەم هەینی مانگ",         "impact": "high",   "quarterly": False, "category": "labor_good"},
        {"name": "Unemployment", "day": 4,  "hint": "هاوکات لەگەڵ NFP",          "impact": "high",   "quarterly": False, "category": "labor_bad"},
        {"name": "Core CPI",     "day": 11, "hint": "نزیکەی ڕۆژی ١٠-١٣",        "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "CPI",          "day": 11, "hint": "هاوکات لەگەڵ Core CPI",     "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core PPI",     "day": 13, "hint": "ڕۆژێک-دوو دوای CPI",       "impact": "medium", "quarterly": False, "category": "inflation"},
        {"name": "PPI",          "day": 13, "hint": "هاوکات لەگەڵ Core PPI",    "impact": "medium", "quarterly": False, "category": "inflation"},
        {"name": "Retail Sales", "day": 15, "hint": "نزیکەی ڕۆژی ١٥-١٧",        "impact": "high",   "quarterly": False, "category": "growth"},
        {"name": "Core PCE",     "day": 25, "hint": "نزیکەی کۆتایی مانگ",       "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "PCE",          "day": 25, "hint": "هاوکات لەگەڵ Core PCE",    "impact": "medium", "quarterly": False, "category": "inflation"},
        {"name": "Interest Rate","day": 18, "hint": "FOMC — ٨ جار لە ساڵ",      "impact": "high",   "quarterly": False, "category": "rate"},
        {"name": "GDP",          "day": 28, "hint": "کوارتەرلی — هەر ٣ مانگ",   "impact": "high",   "quarterly": True,  "category": "growth"},
    ],
    "GBP پاوەند": [
        {"name": "CPI",          "day": 17, "hint": "نزیکەی ڕۆژی ١٥-٢٠ (ONS)", "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 17, "hint": "هاوکات لەگەڵ CPI",          "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Unemployment", "day": 11, "hint": "نزیکەی ڕۆژی ١٠-١٤",        "impact": "high",   "quarterly": False, "category": "labor_bad"},
        {"name": "Production",   "day": 11, "hint": "هاوکات لەگەڵ بازرگانی",    "impact": "medium", "quarterly": False, "category": "growth"},
        {"name": "Interest Rate","day": 19, "hint": "BoE — نزیکەی ٨ جار لە ساڵ","impact": "high",  "quarterly": False, "category": "rate"},
    ],
    "CAD کەنەدی": [
        {"name": "CPI",          "day": 17, "hint": "نزیکەی ڕۆژی ١٥-٢٠ (StatCan)","impact": "high","quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 17, "hint": "هاوکات لەگەڵ CPI",           "impact": "high", "quarterly": False, "category": "inflation"},
        {"name": "Employment",   "day": 4,  "hint": "یەکەم هەینی مانگ",           "impact": "high", "quarterly": False, "category": "labor_good"},
        {"name": "Unemployment", "day": 4,  "hint": "هاوکات لەگەڵ Employment",    "impact": "high", "quarterly": False, "category": "labor_bad"},
        {"name": "Interest Rate","day": 14, "hint": "BoC — نزیکەی ٨ جار لە ساڵ", "impact": "high", "quarterly": False, "category": "rate"},
    ],
    "JPY یەن": [
        {"name": "CPI",          "day": 19, "hint": "نزیکەی ڕۆژی ١٩-٢٣",         "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 19, "hint": "هاوکات لەگەڵ CPI",           "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Production",   "day": 14, "hint": "نزیکەی ڕۆژی ١٤-١٦",         "impact": "medium", "quarterly": False, "category": "growth"},
        {"name": "Unemployment", "day": 27, "hint": "نزیکەی کۆتایی مانگ",        "impact": "medium", "quarterly": False, "category": "labor_bad"},
        {"name": "Interest Rate","day": 18, "hint": "BoJ — نزیکەی ٨ جار لە ساڵ", "impact": "high",   "quarterly": False, "category": "rate"},
    ],
}

IMPACT_TABLE_MD = """
| ڕووداوی جیهانی (Event) | دراوە بەهێزەکان (Bullish) | دراوە لاوازەکان (Bearish) | هۆکارەکە |
| :--- | :--- | :--- | :--- |
| **هەڵگیرسانی جەنگ یان ئاڵۆزی سەربازی** | **USD, CHF, Gold** | **EUR, AUD** | ڕاکردنی سەرمایە بۆ ناو دراوە ئەمنەکان (Safe-havens). |
| **بەرزبوونەوەی بەرچاوی نرخی نەوت** | **CAD, NOK** | **JPY, EUR** | کەنەدا و نەرویج نەوت دەنێرنە دەرەوە؛ ژاپۆن و ئەوروپا هاوردەی دەکەن. |
| **بەرزکردنەوەی ڕێژەی سوود (Rate Hikes)** | **دراوەکەی خۆی** | **زێڕ (Gold)** | ڕاکێشانی وەبەرهێنەران بۆ بەدەستهێنانی سوودی بەرزتر. |
| **جەنگی بازرگانی و باجی گومرگی** | **USD** | **AUD, NZD, CNH** | لاوازبوونی بازرگانی چین بە شێوەیەکی ڕاستەوخۆ دۆلاری ئوسترالی دادەبەزێنێت. |
"""

# ============================================================
# CSS — Premium Dark Dashboard
# ============================================================

def inject_css() -> None:
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
* { font-family: 'Inter', sans-serif !important; box-sizing: border-box; }

/* === GLOBAL === */
.stApp { background: #070b12 !important; }
.main .block-container {
    padding-top: 20px !important;
    padding-left: 28px !important;
    padding-right: 28px !important;
    max-width: 100% !important;
}

/* === SIDEBAR === */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #06091280 0%, #06091200 100%),
                linear-gradient(180deg, #080d18 0%, #070b14 100%) !important;
    border-right: 1px solid rgba(255,255,255,0.04) !important;
    min-width: 220px !important; max-width: 220px !important;
}
section[data-testid="stSidebar"] .block-container {
    padding: 18px 12px !important;
}
section[data-testid="stSidebar"] .stRadio > div {
    gap: 2px !important; flex-direction: column !important;
}
section[data-testid="stSidebar"] .stRadio > div > label {
    display: flex !important; align-items: center !important;
    padding: 9px 12px !important; border-radius: 10px !important;
    color: #6b7280 !important; font-size: 13px !important;
    font-weight: 500 !important; cursor: pointer !important;
    transition: all 0.15s ease !important; margin-bottom: 1px !important;
    border: 1px solid transparent !important; width: 100% !important;
}
section[data-testid="stSidebar"] .stRadio > div > label:hover {
    background: rgba(226,183,20,0.05) !important; color: #d1d5db !important;
}
section[data-testid="stSidebar"] .stRadio [aria-checked="true"] {
    background: linear-gradient(135deg, rgba(226,183,20,0.08), rgba(226,183,20,0.04)) !important;
    color: #e2b714 !important; border-color: rgba(226,183,20,0.18) !important;
}
section[data-testid="stSidebar"] .stRadio > div > label > div:first-child {
    display: none !important;
}
section[data-testid="stSidebar"] h3 {
    color: #9ca3af !important; font-size: 12px !important;
    letter-spacing: 1px !important; margin-bottom: 8px !important;
}
.sidebar-logo {
    display: flex; align-items: center; gap: 10px;
    padding: 4px 4px 16px 4px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    margin-bottom: 14px;
}
.sidebar-logo-icon {
    width: 38px; height: 38px; border-radius: 10px;
    background: linear-gradient(135deg, #e2b714, #f5cc45);
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; box-shadow: 0 4px 12px rgba(226,183,20,0.25);
}
.sidebar-logo-title { font-size: 12px; font-weight: 800; color: #e2b714; letter-spacing: 0.5px; }
.sidebar-logo-sub { font-size: 9px; color: #374151; letter-spacing: 0.5px; margin-top: 1px; }
.sidebar-bottom {
    background: linear-gradient(135deg, #0c1624, #0a1220);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 14px; text-align: center; margin-top: 12px;
}
hr.sdivider {
    border: none !important; border-top: 1px solid rgba(255,255,255,0.05) !important;
    margin: 12px 0 !important;
}

/* === HERO === */
.hero-section {
    background: linear-gradient(135deg, #0b1728 0%, #091320 40%, #070b16 100%);
    border: 1px solid rgba(226,183,20,0.07);
    border-radius: 20px; padding: 36px 44px;
    margin-bottom: 22px; position: relative; overflow: hidden;
}
.hero-section::before {
    content: ''; position: absolute; top: -60px; right: -60px;
    width: 280px; height: 280px; border-radius: 50%;
    background: radial-gradient(circle, rgba(226,183,20,0.06) 0%, transparent 70%);
    pointer-events: none;
}
.hero-section::after {
    content: ''; position: absolute; bottom: -40px; left: -40px;
    width: 200px; height: 200px; border-radius: 50%;
    background: radial-gradient(circle, rgba(16,185,129,0.04) 0%, transparent 70%);
    pointer-events: none;
}
.hero-eyebrow {
    font-size: 10px; font-weight: 800; letter-spacing: 3px;
    color: #e2b714; text-transform: uppercase; margin-bottom: 10px;
    display: flex; align-items: center; gap: 8px;
}
.hero-eyebrow::before {
    content: ''; display: inline-block; width: 20px; height: 2px;
    background: #e2b714; border-radius: 1px;
}
.hero-title {
    font-size: 30px; font-weight: 900; color: #ffffff;
    margin: 0 0 10px 0; line-height: 1.25;
}
.hero-sub { font-size: 14px; color: #6b7280; margin: 0 0 20px 0; }
.hero-footer {
    display: flex; align-items: center; gap: 20px;
    font-size: 11px; color: #374151;
}
.live-badge {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.2);
    color: #10b981; font-size: 10px; font-weight: 700;
    padding: 3px 10px; border-radius: 999px; letter-spacing: 0.5px;
}
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981;
    animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* === ASSET SELECTOR === */
.stRadio[data-testid="stRadioGroup"] .stRadio > div { flex-direction: row !important; }

/* === METRIC CARDS === */
.metric-card {
    background: linear-gradient(145deg, #0f1825 0%, #0c1320 100%);
    border: 1px solid rgba(255,255,255,0.055);
    border-radius: 16px; padding: 16px 16px 0 16px;
    transition: border-color 0.2s, transform 0.15s;
    height: 100%;
}
.metric-card:hover { border-color: rgba(226,183,20,0.2); transform: translateY(-2px); }
.mc-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
.mc-icon-wrap {
    width: 36px; height: 36px; border-radius: 10px;
    background: rgba(226,183,20,0.08);
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
}
.mc-cat { font-size: 9px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase;
          color: #374151; padding: 2px 8px; border-radius: 999px;
          background: rgba(255,255,255,0.04); }
.mc-name { font-size: 12px; font-weight: 700; color: #6b7280; margin: 6px 0 4px; letter-spacing: 0.3px; }
.mc-value { font-size: 24px; font-weight: 800; color: #f3f4f6; line-height: 1; margin-bottom: 5px; }
.mc-change { font-size: 12px; font-weight: 600; margin-bottom: 1px; }
.mc-secondary { font-size: 11px; color: #374151; margin-bottom: 2px; }
.mc-date { font-size: 10px; color: #1f2937; padding-bottom: 10px; }
.up-good   { color: #10b981; }
.down-good { color: #ef4444; }
.up-bad    { color: #ef4444; }
.down-bad  { color: #10b981; }
.neutral-c { color: #6b7280; }

/* === SECTION TITLE === */
.section-title {
    font-size: 11px; font-weight: 800; letter-spacing: 2px;
    text-transform: uppercase; color: #4b5563;
    margin-bottom: 12px; margin-top: 2px;
    display: flex; align-items: center; gap: 8px;
}
.section-title::after {
    content: ''; flex: 1; height: 1px;
    background: linear-gradient(90deg, rgba(255,255,255,0.05), transparent);
}

/* === DATA TABLE === */
.data-table-wrap {
    background: linear-gradient(145deg, #0f1825 0%, #0c1320 100%);
    border: 1px solid rgba(255,255,255,0.055);
    border-radius: 16px; overflow: hidden;
}
.data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.data-table thead th {
    background: rgba(226,183,20,0.05);
    color: #e2b714; padding: 10px 12px;
    font-weight: 700; font-size: 10px; letter-spacing: 0.5px;
    text-align: right; border-bottom: 1px solid rgba(255,255,255,0.05);
}
.data-table tbody tr { border-bottom: 1px solid rgba(255,255,255,0.03); transition: background 0.1s; }
.data-table tbody tr:hover { background: rgba(226,183,20,0.02); }
.data-table tbody tr:last-child { border-bottom: none; }
.data-table tbody td { padding: 8px 12px; color: #9ca3af; text-align: right; vertical-align: middle; }
.agr-full { color: #10b981; font-weight: 700; }
.agr-half { color: #f59e0b; font-weight: 700; }
.agr-low  { color: #ef4444; font-weight: 700; }

/* === BADGES === */
.badge {
    display: inline-block; padding: 3px 10px;
    border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap;
}
.badge-bullish { background: rgba(16,185,129,0.1); color: #10b981; border: 1px solid rgba(16,185,129,0.18); }
.badge-bearish { background: rgba(239,68,68,0.1);  color: #ef4444; border: 1px solid rgba(239,68,68,0.18); }
.badge-neutral { background: rgba(107,114,128,0.1);color: #9ca3af; border: 1px solid rgba(107,114,128,0.15); }
.badge-lg { font-size: 14px; padding: 8px 18px; border-radius: 10px; font-weight: 800; }

/* === COMPOSITE CARD === */
.composite-card {
    background: linear-gradient(145deg, #0f1c2e, #0a1320);
    border: 1px solid rgba(226,183,20,0.12);
    border-radius: 16px; padding: 22px; text-align: center;
}
.cc-title { font-size: 10px; color: #4b5563; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 12px; }
.cc-score { font-size: 12px; color: #374151; margin-top: 10px; font-family: monospace; }
.cc-tfs { font-size: 10px; color: #1f2937; margin-top: 6px; letter-spacing: 0.5px; }

/* === DRIVER CARD === */
.driver-card {
    background: rgba(15,24,37,0.8);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 12px 14px; margin-bottom: 8px;
    transition: border-color 0.15s;
}
.driver-card:hover { border-color: rgba(226,183,20,0.12); }
.driver-name { font-size: 13px; font-weight: 700; color: #e5e7eb; }
.driver-cat { font-size: 11px; color: #4b5563; margin-right: 8px; }
.driver-tfs { font-size: 11px; color: #374151; margin-top: 6px; }

/* === NEWS CARDS === */
.news-card {
    background: rgba(15,24,37,0.8);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 13px 14px; margin-bottom: 8px;
    transition: all 0.15s; display: block;
}
.news-card:hover { border-color: rgba(226,183,20,0.15); transform: translateX(-2px); }
.nc-source-dot { width: 6px; height: 6px; border-radius: 50%; background: #e2b714; display: inline-block; margin-left: 6px; }
.nc-title { font-size: 13px; font-weight: 600; color: #e5e7eb; line-height: 1.5; margin-bottom: 6px; }
.nc-meta { font-size: 11px; color: #374151; display: flex; align-items: center; gap: 6px; }
.nc-time { color: #1f2937; font-size: 10px; margin-right: auto; }

/* === ANALYSIS ITEMS === */
.analysis-item {
    display: flex; align-items: center; gap: 12px;
    background: rgba(15,24,37,0.8);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 13px 14px; margin-bottom: 8px;
    transition: all 0.15s; cursor: pointer;
}
.analysis-item:hover { border-color: rgba(226,183,20,0.15); background: rgba(15,24,37,1); }
.ai-icon {
    width: 38px; height: 38px; border-radius: 10px;
    background: rgba(226,183,20,0.07);
    display: flex; align-items: center; justify-content: center; font-size: 18px;
    flex-shrink: 0;
}
.ai-title { font-size: 13px; font-weight: 700; color: #e5e7eb; }
.ai-sub { font-size: 11px; color: #374151; margin-top: 2px; }
.ai-arrow { margin-right: auto; color: #1f2937; font-size: 16px; transition: color 0.15s; }
.analysis-item:hover .ai-arrow { color: #e2b714; }

/* === CALENDAR CARDS === */
.cal-card {
    display: flex; align-items: flex-start; gap: 14px;
    background: linear-gradient(145deg, #0f1825 0%, #0c1320 100%);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px; padding: 13px 16px;
    margin-bottom: 8px; transition: all 0.15s;
}
.cal-card:hover { border-color: rgba(255,255,255,0.09); transform: translateX(-2px); }
.cal-card.released { border-right: 3px solid #10b981; }
.cal-card.upcoming { border-right: 3px solid #1f2937; }
.cal-card.upcoming-soon { border-right: 3px solid #f59e0b; }
.cal-day-badge {
    min-width: 42px; height: 42px; border-radius: 11px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 15px; flex-shrink: 0;
}
.cal-day-badge.released { background: rgba(16,185,129,0.1); color: #10b981; }
.cal-day-badge.upcoming { background: rgba(30,40,55,0.6); color: #4b5563; }
.cal-day-badge.upcoming-soon { background: rgba(245,158,11,0.1); color: #f59e0b; }
.cal-content { flex: 1; min-width: 0; }
.cal-name { font-weight: 700; color: #e5e7eb; font-size: 13px; }
.cal-hint { font-size: 11px; color: #374151; margin-top: 2px; }
.cal-impact-badge {
    font-size: 9px; font-weight: 800; padding: 2px 7px;
    border-radius: 999px; letter-spacing: 0.5px; text-transform: uppercase;
}
.impact-high   { background: rgba(239,68,68,0.1); color: #ef4444; }
.impact-medium { background: rgba(245,158,11,0.1); color: #f59e0b; }

/* === MISC === */
.section-divider {
    border: none; border-top: 1px solid rgba(255,255,255,0.04); margin: 22px 0;
}
.footer-note {
    text-align: center; color: #111827; font-size: 11px;
    margin-top: 48px; padding: 20px;
    border-top: 1px solid rgba(255,255,255,0.03);
}

/* Streamlit overrides */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
.stDeployButton { display: none; }
header[data-testid="stHeader"] { background: transparent !important; }
.stSpinner > div { border-top-color: #e2b714 !important; }
.stSelectbox [data-baseweb="select"] {
    background: #0f1825 !important;
    border-color: rgba(255,255,255,0.08) !important;
    border-radius: 10px !important;
}
.stTextInput input {
    background: #0f1825 !important;
    border-color: rgba(255,255,255,0.07) !important;
    color: #e5e7eb !important;
    border-radius: 8px !important;
}
.stRadio > label { color: #6b7280 !important; font-size: 12px !important; }
div[data-testid="stMetric"] {
    background: rgba(15,24,37,0.6);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 12px;
}
div[data-testid="stMetric"] label { color: #6b7280 !important; font-size: 12px !important; }
div[data-testid="stMetricValue"] { color: #f3f4f6 !important; }
div[data-testid="stMetricDelta"] svg { display: none; }
.stProgress > div > div { background: #e2b714 !important; border-radius: 999px !important; }
.stProgress { background: #111827 !important; border-radius: 999px !important; }

/* Plotly transparent */
.js-plotly-plot { background: transparent !important; }
.plotly .bg { fill: transparent !important; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# DATA LAYER
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


@st.cache_data(ttl=900, show_spinner=False)
def fetch_news(query: str, key: str):
    if not key:
        return None
    url = "https://newsapi.org/v2/everything"
    params = {"q": query, "sortBy": "publishedAt", "apiKey": key, "pageSize": 6, "language": "en"}
    try:
        res = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        return res.json().get("articles", [])[:6]
    except Exception:
        return None


# ============================================================
# MULTI-TIMEFRAME ANALYSIS ENGINE
# ============================================================

def calc_multiframe(vals: list, category: str) -> dict | None:
    """
    ژماردنی هەموو تایمفریمەکان:
    - m/m  : گۆڕانی مانگانە
    - q/q  : مامناوەی ٣ مانگی ئەمسا vs مامناوەی ٣ مانگی پێش خۆیان
    - y/y  : گۆڕانی ساڵانە (مانگی ئەمسا vs هەمان مانگی ساڵی ڕابوردوو)
    - t3m  : مۆمێنتۆمی ٣ مانگ (مامناوەی m/m ی ٣ مانگی ڕابوردوو)
    - z    : z-score ئاستی ئێستا لەگەڵ مامناوەی ١٢ مانگ
    - composite: ژماردنی گشتی بە tanh scaling
    """
    if not vals or len(vals) < 2:
        return None

    reverse = (category == "labor_bad")

    # --- m/m ---
    mom = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100 if vals[-2] != 0 else 0.0

    # --- q/q ---
    qoq = None
    if len(vals) >= 6:
        qnow  = np.mean(vals[-3:])
        qprev = np.mean(vals[-6:-3])
        qoq = (qnow - qprev) / abs(qprev) * 100 if qprev != 0 else 0.0

    # --- y/y ---
    yoy = None
    if len(vals) >= 13:
        yoy = (vals[-1] - vals[-13]) / abs(vals[-13]) * 100 if vals[-13] != 0 else 0.0

    # --- trend 3m (avg of last 3 m/m changes) ---
    t3m = None
    if len(vals) >= 4:
        changes = [(vals[i] - vals[i-1]) / abs(vals[i-1]) * 100
                   for i in range(-3, 0) if vals[i-1] != 0]
        t3m = np.mean(changes) if changes else None

    # --- Z-score of level vs last 12m ---
    z_level = 0.0
    if len(vals) >= 6:
        sub = vals[-12:] if len(vals) >= 12 else vals
        std = np.std(sub)
        z_level = (vals[-1] - np.mean(sub)) / std if std != 0 else 0.0

    # --- Composite via tanh (normalises each to roughly [-1, +1]) ---
    def t(x, ref):
        return float(np.tanh(x / ref)) if ref != 0 and x is not None else 0.0

    parts = [
        (t(mom,     0.5),  0.30),
        (t(qoq,     2.0),  0.25),
        (t(yoy,     5.0),  0.25),
        (t(t3m,     0.5),  0.10),
        (t(z_level, 1.0),  0.10),
    ]
    denom = sum(w for _, w in parts if _ != 0 or True)
    composite = sum(s * w for s, w in parts) / denom if denom else 0.0
    if reverse:
        composite = -composite

    # Direction of each timeframe (after reversal)
    def dir_val(x):
        if x is None:
            return None
        return (-x if reverse else x)

    dirs = [np.sign(v) for v in [dir_val(mom), dir_val(qoq), dir_val(yoy)] if v is not None]
    main_sign = np.sign(composite)
    agreement = sum(1 for d in dirs if d == main_sign) / len(dirs) if dirs else 0.0

    return {
        "latest":    vals[-1],
        "mom":       round(mom, 3),
        "qoq":       round(qoq, 3)  if qoq  is not None else None,
        "yoy":       round(yoy, 3)  if yoy  is not None else None,
        "t3m":       round(t3m, 3)  if t3m  is not None else None,
        "z_level":   round(z_level, 2),
        "composite": float(composite),
        "agreement": float(agreement),
        "reverse":   reverse,
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
        mf = calc_multiframe(vals, meta["category"])
        if mf is None:
            continue

        rows.append({
            "name":      name,
            "category":  meta["category"],
            "weight":    meta["weight"],
            "df":        df,
            "vals":      vals,
            "date":      dates[-1],
            **mf,
        })
        weighted.append(mf["composite"] * meta["weight"])

    if not rows:
        return None

    tw = sum(r["weight"] for r in rows)
    composite = sum(weighted) / tw if tw else 0.0
    top4 = sorted(rows, key=lambda r: abs(r["composite"] * r["weight"]), reverse=True)[:4]
    return {"composite": composite, "rows": rows, "top4": top4}


# ============================================================
# HELPERS
# ============================================================

def bias_from_score(score: float):
    if score > 0.15:
        return "📈 Bullish", "badge-bullish", "#10b981"
    if score < -0.15:
        return "📉 Bearish", "badge-bearish", "#ef4444"
    return "⚖️ Neutral", "badge-neutral", "#6b7280"


def badge_html(score: float, large: bool = False) -> str:
    label, css, _ = bias_from_score(score)
    sz = "badge-lg" if large else ""
    return f'<span class="badge {css} {sz}">{label}</span>'


def pct_html(val, positive_is_good: bool = True) -> str:
    """Coloured percentage string."""
    if val is None:
        return "<span style='color:#1f2937'>—</span>"
    arrow = "▲" if val > 0 else "▼"
    good  = (val > 0) == positive_is_good
    color = "#10b981" if good else "#ef4444"
    return f"<span style='color:{color}; font-weight:600;'>{arrow} {abs(val):.2f}%</span>"


def agr_html(agreement: float) -> str:
    if agreement >= 0.85:
        return "<span class='agr-full'>●●●</span>"
    if agreement >= 0.5:
        return "<span class='agr-half'>●●○</span>"
    return "<span class='agr-low'>●○○</span>"


def svg_spark(vals: list, width: int = 80, height: int = 34, positive_is_good: bool = True) -> str:
    """Return inline SVG sparkline."""
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
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="display:block;">'
        f'<path d="{fpath}" fill="{fill_c}"/>'
        f'<path d="{path}" fill="none" stroke="{line_c}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


def make_trend_chart(df: pd.DataFrame, name: str) -> go.Figure | None:
    if df is None or df.empty:
        return None
    vals = df["value"].tolist()
    trend_up = vals[-1] > vals[0] if len(vals) >= 2 else True
    color = "#e2b714"
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["value"],
        mode="lines",
        line=dict(color=color, width=2, shape="spline"),
        fill="tozeroy",
        fillcolor="rgba(226,183,20,0.05)",
        hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        margin=dict(l=0, r=0, t=6, b=0),
        height=200,
        xaxis=dict(showgrid=False, color="#374151", tickfont=dict(size=10, color="#374151"), showline=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.03)", color="#374151",
                   tickfont=dict(size=10, color="#374151"), showline=False, zeroline=False),
        hovermode="x unified",
    )
    return fig


# ============================================================
# PAGE: DASHBOARD
# ============================================================

def render_dashboard(fred_key: str, news_key: str) -> None:
    today = datetime.now()

    # Hero
    st.markdown(f"""
    <div class="hero-section">
      <div class="hero-eyebrow">FX MACRO &amp; GEOPOLITICAL DESK</div>
      <h1 class="hero-title">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h1>
      <p class="hero-sub">تەحلیل، تایبەتمەندی و کارکردنی بازارە دارایی و سیاسی جیهان — فراوانتیمفریم</p>
      <div class="hero-footer">
        <span>📅 {today.strftime('%Y-%m-%d')}</span>
        <span>🕐 {today.strftime('%H:%M')}</span>
        <span>📍 کوردستان</span>
        <span class="live-badge"><span class="live-dot"></span>Live Market Data</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Asset type + currency selector
    a_col, b_col = st.columns([3, 2])
    with a_col:
        asset_type = st.radio(
            "", ["💱 Forex", "🥇 Gold & Metals", "📈 Indices", "🛢️ Commodities", "₿ Crypto"],
            horizontal=True, key="dash_asset", label_visibility="collapsed",
        )
    with b_col:
        if "Forex" in asset_type:
            selected = st.selectbox("", list(CURRENCY_SERIES.keys()),
                                    key="dash_cur", label_visibility="collapsed")
        else:
            selected = "USD دۆلار"

    if "Indices" in asset_type or "Commodities" in asset_type or "Crypto" in asset_type:
        st.info("📌 ئەم بەشە بەزوودی زیاد دەبێت. ئێستا Forex و Gold بەردەستن.")
        return

    if "Gold" in asset_type:
        render_gold_page(fred_key)
        return

    if not fred_key:
        st.info("🔑 تکایە FRED API Key لە سایدبار بنووسە بۆ بارکردنی داتاکان.")
        return

    with st.spinner("داتاکان ڕادەکێشرێن (m/m • q/q • y/y • Trend)..."):
        result = compute_currency_composite(selected, fred_key)

    if not result:
        st.warning("⚠️ داتا نەدۆزرایەوە. FRED API Key‌ەکەت بپشکنە.")
        return

    rows    = result["rows"]
    row_map = {r["name"]: r for r in rows}

    # ----------------------------------------------------------------
    # 4 Metric Cards
    # ----------------------------------------------------------------
    key_inds = KEY_INDICATORS.get(selected, [r["name"] for r in rows[:4]])
    key_rows = [row_map[k] for k in key_inds if k in row_map]

    st.markdown('<div class="section-title">کۆتا داتا ڕاستەقینەکان</div>', unsafe_allow_html=True)
    cols = st.columns(len(key_rows) or 1)

    for col, row in zip(cols, key_rows):
        pos_good = row["category"] != "labor_bad"
        mom      = row["mom"]
        yoy      = row.get("yoy")
        spark    = svg_spark(row["vals"][-20:], positive_is_good=pos_good)
        _, _, accent = bias_from_score(row["composite"])

        mom_arrow = "▲" if mom > 0 else "▼"
        mom_good  = (mom > 0) == pos_good
        mom_color = "#10b981" if mom_good else "#ef4444"

        yoy_str = ""
        if yoy is not None:
            yoy_arrow = "▲" if yoy > 0 else "▼"
            yoy_good  = (yoy > 0) == pos_good
            yoy_color = "#10b981" if yoy_good else "#ef4444"
            yoy_str = f"<div class='mc-secondary'><span style='color:{yoy_color};font-weight:600;'>{yoy_arrow} {abs(yoy):.2f}%</span> <span style='color:#1f2937;font-size:10px;'>(y/y)</span></div>"

        with col:
            st.markdown(f"""
            <div class="metric-card">
              <div class="mc-header">
                <div class="mc-icon-wrap">{CATEGORY_ICONS.get(row['category'], '📊')}</div>
                <span class="mc-cat">{CATEGORY_LABELS.get(row['category'], '')}</span>
              </div>
              <div class="mc-name">{row['name']}</div>
              <div class="mc-value">{row['latest']:,.2f}</div>
              <div class="mc-change">
                <span style="color:{mom_color}; font-weight:700;">{mom_arrow} {abs(mom):.2f}%</span>
                <span style="color:#1f2937; font-size:10px;"> (m/m)</span>
              </div>
              {yoy_str}
              <div class="mc-date">{row['date']}</div>
              <div style="margin-top:6px;">{spark}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ----------------------------------------------------------------
    # Table + Chart
    # ----------------------------------------------------------------
    t_col, c_col = st.columns([1, 1.4])

    with t_col:
        st.markdown('<div class="section-title">کۆتا ئاراستیەکان</div>', unsafe_allow_html=True)
        tbl = """
        <div class="data-table-wrap">
        <table class="data-table">
        <thead>
          <tr>
            <th>بازار</th><th>کۆتا</th><th>m/m</th><th>q/q</th><th>y/y</th><th>ئاراستە</th><th>کۆک</th>
          </tr>
        </thead><tbody>
        """
        for row in rows:
            pos = row["category"] != "labor_bad"
            lbl, cls, _ = bias_from_score(row["composite"])
            tbl += f"""
            <tr>
              <td style="font-weight:700;color:#e5e7eb;">{row['name']}</td>
              <td style="color:#e2b714;font-weight:600;">{row['latest']:,.2f}</td>
              <td>{pct_html(row['mom'], pos)}</td>
              <td>{pct_html(row.get('qoq'), pos)}</td>
              <td>{pct_html(row.get('yoy'), pos)}</td>
              <td><span class="badge {cls}" style="font-size:10px;padding:2px 7px;">{lbl}</span></td>
              <td style="text-align:center;">{agr_html(row['agreement'])}</td>
            </tr>"""
        tbl += "</tbody></table></div>"
        st.markdown(tbl, unsafe_allow_html=True)

    with c_col:
        st.markdown('<div class="section-title">کش و هەواڵی بازارەکان</div>', unsafe_allow_html=True)
        chart_ind = st.selectbox("", [r["name"] for r in rows],
                                  key="dash_chart_ind", label_visibility="collapsed")
        crow = row_map.get(chart_ind)
        if crow:
            fig = make_trend_chart(crow["df"], chart_ind)
            if fig:
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ----------------------------------------------------------------
    # Composite + Top Drivers
    # ----------------------------------------------------------------
    comp_lbl, comp_cls, comp_clr = bias_from_score(result["composite"])

    cd1, cd2 = st.columns([1, 2])
    with cd1:
        st.markdown(f"""
        <div class="composite-card">
          <div class="cc-title">ئاراستەی گشتی {selected}</div>
          <div style="margin:10px 0;">{badge_html(result['composite'], large=True)}</div>
          <div class="cc-score">Score: {result['composite']:+.3f}</div>
          <div class="cc-tfs">m/m • q/q • y/y • Trend • Z-Level</div>
        </div>
        """, unsafe_allow_html=True)

    with cd2:
        st.markdown('<div class="section-title">گرنگترین هۆکارەکان</div>', unsafe_allow_html=True)
        for drv in result["top4"]:
            pos = drv["category"] != "labor_bad"
            lbl, cls, _ = bias_from_score(drv["composite"])
            agr_i = agr_html(drv["agreement"])
            st.markdown(f"""
            <div class="driver-card">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span class="driver-name">{drv['name']}</span>
                <div style="display:flex;align-items:center;gap:8px;">
                  {agr_i}
                  <span class="badge {cls}" style="font-size:10px;padding:2px 8px;">{lbl}</span>
                </div>
              </div>
              <div class="driver-cat">{CATEGORY_LABELS.get(drv['category'], '')}</div>
              <div class="driver-tfs">
                m/m: {pct_html(drv['mom'], pos)} &nbsp;&nbsp;
                q/q: {pct_html(drv.get('qoq'), pos)} &nbsp;&nbsp;
                y/y: {pct_html(drv.get('yoy'), pos)} &nbsp;&nbsp;
                Trend 3m: {pct_html(drv.get('t3m'), pos)}
              </div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # ----------------------------------------------------------------
    # News + Analysis
    # ----------------------------------------------------------------
    n_col, a_col = st.columns(2)

    with n_col:
        st.markdown('<div class="section-title">هەواڵی جیهانی و ئابووری</div>', unsafe_allow_html=True)
        if news_key:
            with st.spinner(""):
                arts = fetch_news("forex OR inflation OR central bank OR economy", news_key)
            if arts:
                for art in arts[:4]:
                    title  = art.get("title", "—")
                    source = (art.get("source") or {}).get("name", "")
                    pub    = (art.get("publishedAt") or "")[:10]
                    link   = art.get("url", "#")
                    st.markdown(f"""
                    <a href="{link}" target="_blank" style="text-decoration:none;">
                    <div class="news-card">
                      <div class="nc-title">{title}</div>
                      <div class="nc-meta">
                        <span class="nc-source-dot"></span>
                        {source}
                        <span class="nc-time">{pub}</span>
                      </div>
                    </div></a>
                    """, unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#374151;font-size:13px;padding:16px;">🔑 NewsAPI Key لە سایدبار بنووسە</div>', unsafe_allow_html=True)

    with a_col:
        st.markdown('<div class="section-title">ئێناليزی سەرکی</div>', unsafe_allow_html=True)
        items = [
            {"icon": "🥇", "title": "پێشبینیکردنی زێرو ئۆنس زێر",      "sub": "بیشکلۆفی ئارازی زێر — Real Yield Analysis"},
            {"icon": "🛢️", "title": "بازاری نەوت",                       "sub": "پێشبینیکردنی نرخی نەوت — Energy Markets"},
            {"icon": "💱", "title": f"بازاری FX — {selected}",            "sub": "کەمکی ڕێژەی ئاراستەی دراوەکان"},
            {"icon": "📅", "title": "کالێندەری ئەم مانگ",                 "sub": "هەواڵی بڵاوبووەوە + پێشبینیی داهاتوو"},
        ]
        for it in items:
            st.markdown(f"""
            <div class="analysis-item">
              <div class="ai-icon">{it['icon']}</div>
              <div>
                <div class="ai-title">{it['title']}</div>
                <div class="ai-sub">{it['sub']}</div>
              </div>
              <div class="ai-arrow">→</div>
            </div>
            """, unsafe_allow_html=True)


# ============================================================
# PAGE: CURRENCY DETAIL (Full Multi-Timeframe)
# ============================================================

def render_currency_detail(fred_key: str) -> None:
    st.markdown('<h3 style="color:#e5e7eb;margin-bottom:16px;">📊 شیکاری تەواو — هەموو تایمفریمەکان</h3>', unsafe_allow_html=True)
    selected = st.radio("دراوەکە:", list(CURRENCY_SERIES.keys()), horizontal=True, key="det_cur")

    if not fred_key:
        st.info("🔑 FRED API Key پێویستە.")
        return

    with st.spinner("..."):
        result = compute_currency_composite(selected, fred_key)
    if not result:
        st.warning("داتا نەدۆزرایەوە.")
        return

    rows = result["rows"]

    # Summary bar
    lbl, cls, _ = bias_from_score(result["composite"])
    s1, s2, s3 = st.columns([1, 1, 2])
    with s1:
        st.metric("Composite Score", f"{result['composite']:+.3f}")
    with s2:
        st.markdown(f"<br>{badge_html(result['composite'], large=True)}", unsafe_allow_html=True)
    with s3:
        n_bull = sum(1 for r in rows if r["composite"] > 0.15)
        n_bear = sum(1 for r in rows if r["composite"] < -0.15)
        n_neut = len(rows) - n_bull - n_bear
        st.markdown(f"""
        <div style="background:#0f1825;border:1px solid rgba(255,255,255,0.05);border-radius:12px;padding:12px;display:flex;gap:20px;align-items:center;">
          <span style="color:#10b981;font-weight:700;font-size:14px;">📈 {n_bull} Bullish</span>
          <span style="color:#ef4444;font-weight:700;font-size:14px;">📉 {n_bear} Bearish</span>
          <span style="color:#6b7280;font-weight:700;font-size:14px;">⚖️ {n_neut} Neutral</span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # Full multi-timeframe table
    st.markdown('<div class="section-title">خشتەی فراوانتایمفریم — هەموو نیشاندەرەکان</div>', unsafe_allow_html=True)

    tbl = """
    <div class="data-table-wrap">
    <table class="data-table">
    <thead>
      <tr>
        <th>نیشاندەر</th><th>کاتیگۆری</th><th>کۆتا ئاست</th>
        <th>m/m %</th><th>q/q %</th><th>y/y %</th>
        <th>Trend 3m</th><th>Z-Level</th><th>ئاراستە</th><th>کۆکبوون</th>
      </tr>
    </thead><tbody>
    """
    for row in rows:
        pos = row["category"] != "labor_bad"
        lbl2, cls2, _ = bias_from_score(row["composite"])
        tbl += f"""
        <tr>
          <td style="font-weight:700;color:#e5e7eb;">{row['name']}</td>
          <td style="color:#6b7280;font-size:11px;">{CATEGORY_LABELS.get(row['category'], '')}</td>
          <td style="color:#e2b714;font-weight:600;">{row['latest']:,.2f}</td>
          <td>{pct_html(row['mom'], pos)}</td>
          <td>{pct_html(row.get('qoq'), pos)}</td>
          <td>{pct_html(row.get('yoy'), pos)}</td>
          <td>{pct_html(row.get('t3m'), pos)}</td>
          <td style="color:#6b7280;font-family:monospace;">{row['z_level']:+.2f}</td>
          <td><span class="badge {cls2}" style="font-size:10px;padding:2px 7px;">{lbl2}</span></td>
          <td>{agr_html(row['agreement'])}</td>
        </tr>"""
    tbl += "</tbody></table></div>"
    st.markdown(tbl, unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # Detail chart
    st.markdown('<div class="section-title">نموداری نیشاندەر</div>', unsafe_allow_html=True)
    row_map = {r["name"]: r for r in rows}
    sel_ind = st.selectbox("نیشاندەرەکە هەڵبژێرە:", list(row_map.keys()), key="det_ind")

    if sel_ind in row_map:
        row = row_map[sel_ind]
        pos = row["category"] != "labor_bad"

        fig = make_trend_chart(row["df"], sel_ind)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        # Timeframe breakdown cards
        tf_items = [
            ("m/m",      row["mom"],          "گۆڕانی مانگانە"),
            ("q/q",      row.get("qoq"),      "گۆڕانی کوارتەرلی"),
            ("y/y",      row.get("yoy"),      "گۆڕانی ساڵانە"),
            ("Trend 3m", row.get("t3m"),      "مۆمێنتۆمی ٣ مانگ"),
            ("Z-Level",  row.get("z_level"),  "ئاستی ئێستا vs مامناوە"),
        ]

        tf_cols = st.columns(5)
        for col, (lbl_tf, val, hint) in zip(tf_cols, tf_items):
            with col:
                if val is None:
                    v_str = "—"
                    clr   = "#374151"
                elif lbl_tf == "Z-Level":
                    v_str = f"{val:+.2f}σ"
                    good  = (val > 0) == pos
                    clr   = "#10b981" if good else "#ef4444"
                else:
                    v_str = f"{val:+.3f}%"
                    good  = (val > 0) == pos
                    clr   = "#10b981" if good else "#ef4444"

                st.markdown(f"""
                <div style="background:#0f1825;border:1px solid rgba(255,255,255,0.05);border-radius:12px;
                            padding:14px;text-align:center;">
                  <div style="font-size:10px;color:#4b5563;font-weight:700;letter-spacing:1px;
                              text-transform:uppercase;margin-bottom:8px;">{lbl_tf}</div>
                  <div style="font-size:20px;font-weight:800;color:{clr};">{v_str}</div>
                  <div style="font-size:10px;color:#374151;margin-top:6px;">{hint}</div>
                </div>
                """, unsafe_allow_html=True)


# ============================================================
# PAGE: MONTHLY CALENDAR
# ============================================================

def render_monthly(fred_key: str) -> None:
    today = date.today()
    month_ku = {1:"کانوونی دووەم",2:"شوبات",3:"ئازار",4:"نیسان",
                5:"ئایار",6:"حوزەیران",7:"تەممووز",8:"ئاب",
                9:"ئەیلوول",10:"تشرینی یەکەم",11:"تشرینی دووەم",12:"کانوونی یەکەم"}

    st.markdown(f'<h3 style="color:#e5e7eb;margin-bottom:4px;">📅 کالێندەری شیکاری — {month_ku[today.month]} {today.year}</h3>', unsafe_allow_html=True)
    st.markdown(f"<p style='color:#374151;font-size:12px;margin-bottom:16px;'>ئەمڕۆ: <strong style='color:#e2b714'>{today.strftime('%Y-%m-%d')}</strong></p>", unsafe_allow_html=True)

    selected = st.radio("دراوەکە:", list(MONTHLY_CALENDAR.keys()), horizontal=True, key="month_cur")

    if not fred_key:
        st.info("🔑 FRED API Key بنووسە.")
        return

    indicators = CURRENCY_SERIES.get(selected, {})
    events = []

    for ev in MONTHLY_CALENDAR.get(selected, []):
        quarterly = ev.get("quarterly", False)
        is_this   = not (quarterly and today.month not in [1, 4, 7, 10])
        try:
            max_d = cal_lib.monthrange(today.year, today.month)[1]
            rel_d = date(today.year, today.month, min(ev["day"], max_d))
        except ValueError:
            continue

        is_released = today >= rel_d and is_this
        days_until  = (rel_d - today).days

        mf = None
        meta = indicators.get(ev["name"])
        if meta and fred_key and is_this:
            df2 = fetch_fred_series(meta["series"], fred_key, limit=36)
            if df2 is not None and not df2.empty:
                mf = calc_multiframe(df2["value"].tolist(), meta["category"])

        events.append({**ev, "release_date": rel_d, "is_released": is_released,
                        "is_this": is_this, "days_until": days_until, "mf": mf})

    events.sort(key=lambda x: x["day"])

    released  = [e for e in events if e["is_released"]  and e["is_this"]]
    upcoming  = [e for e in events if not e["is_released"] and e["is_this"]]
    skipped   = [e for e in events if not e["is_this"]]

    # Summary
    all_scored = [e for e in events if e["is_this"] and e["mf"]]
    if all_scored:
        cum = np.mean([e["mf"]["composite"] for e in all_scored])
        lbl_c, cls_c, _ = bias_from_score(cum)
        pct_done = len(released) / len([e for e in events if e["is_this"]]) * 100

        sc1, sc2 = st.columns([3, 1])
        with sc1:
            st.progress(pct_done / 100, text=f"پێشکەوتنی مانگ: {pct_done:.0f}%  —  بڵاوبووەوە: {len(released)}  |  داهاتوو: {len(upcoming)}")
        with sc2:
            st.markdown(f"<div style='text-align:center;padding:4px;'>{badge_html(cum, large=True)}</div>", unsafe_allow_html=True)
        st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    def render_event(ev, card_cls, day_cls):
        mf  = ev.get("mf")
        pos = ev.get("category", "inflation") != "labor_bad"
        lbl2, cls2, _ = bias_from_score(mf["composite"] if mf else 0)
        agr = agr_html(mf["agreement"]) if mf else ""
        imt = f"impact-{ev['impact']}"

        tf_line = ""
        if mf:
            tf_line = (
                f"<div style='margin-top:7px;font-size:11px;'>"
                f"m/m:{pct_html(mf['mom'], pos)} &nbsp; "
                f"q/q:{pct_html(mf.get('qoq'), pos)} &nbsp; "
                f"y/y:{pct_html(mf.get('yoy'), pos)} &nbsp; "
                f"{agr}"
                f"</div>"
            )

        days_str = ""
        if not ev["is_released"] and ev["is_this"]:
            if ev["days_until"] <= 3:
                days_str = f"<span style='color:#f59e0b;font-size:10px;font-weight:700;'> ⚡ {ev['days_until']} ڕۆژ</span>"
            else:
                days_str = f"<span style='color:#374151;font-size:10px;'> {ev['days_until']} ڕۆژ</span>"

        st.markdown(f"""
        <div class="cal-card {card_cls}">
          <div class="cal-day-badge {day_cls}">{ev['day']:02d}</div>
          <div class="cal-content">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span class="cal-name">{ev['name']}</span>
              <span class="cal-impact-badge {imt}">{ev['impact'].upper()}</span>
              {days_str}
            </div>
            <div class="cal-hint">📌 {ev['hint']}</div>
            {tf_line}
            <div style="margin-top:7px;">
              <span class="badge {cls2}" style="font-size:10px;padding:2px 8px;">
                {lbl2} {'← پێشبینی' if not ev['is_released'] else ''}
              </span>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    if released:
        st.markdown('<div class="section-title">✅ بڵاوبووەتەوە — Released</div>', unsafe_allow_html=True)
        for ev in released:
            render_event(ev, "released", "released")

    if upcoming:
        st.markdown('<div class="section-title">🔮 داهاتوو — Upcoming | پێشبینیی فراوانتایمفریم</div>', unsafe_allow_html=True)
        for ev in upcoming:
            cc = "upcoming-soon" if 0 <= ev["days_until"] <= 3 else "upcoming"
            render_event(ev, cc, cc)

    if skipped:
        with st.expander(f"📆 {len(skipped)} هەواڵی کوارتەرلی — ئەم مانگ نییە"):
            for ev in skipped:
                st.markdown(f"- **{ev['name']}** — {ev['hint']}")

    st.caption("ℹ️ پێشبینیکانی 'داهاتوو' بەپێی m/m، q/q، y/y و Z-Score ی ١٢-٢٤ مانگی ڕابوردوون.")


# ============================================================
# PAGE: GOLD
# ============================================================

def render_gold_page(fred_key: str) -> None:
    st.markdown('<h3 style="color:#e5e7eb;margin-bottom:16px;">🥇 شیکاری زێڕ (XAUUSD) — Real Yield + USD</h3>', unsafe_allow_html=True)

    if not fred_key:
        st.info("🔑 FRED API Key بنووسە.")
        return

    with st.spinner("..."):
        y_df   = fetch_fred_series(GOLD_YIELD_SERIES, fred_key, limit=36)
        i_df   = fetch_fred_series(GOLD_INFLATION_EXP_SERIES, fred_key, limit=36)
        usd_r  = compute_currency_composite("USD دۆلار", fred_key)

    if y_df is None or i_df is None:
        st.warning("⚠️ داتای DGS10 یان T10YIE نەدۆزرایەوە.")
        return

    merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
    if merged.empty:
        return

    merged["ry"] = merged["value_y"] - merged["value_i"]
    ry_vals = merged["ry"].tail(24).tolist()
    ry_mf   = calc_multiframe(ry_vals, "rate")

    gold_ry   = -ry_mf["composite"]  if ry_mf  else 0.0
    gold_usd  = -usd_r["composite"]  if usd_r  else 0.0
    gold_score = 0.55 * gold_ry + 0.45 * gold_usd

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Real Yield (10Y)",
                  f"{ry_vals[-1]:.2f}%",
                  delta=f"{ry_mf['mom']:+.3f}% m/m" if ry_mf else None,
                  delta_color="inverse")
    with c2:
        usd_val = round(usd_r["composite"], 3) if usd_r else 0
        st.metric("USD Composite", f"{usd_val:+.3f}")
    with c3:
        st.markdown(f"""
        <div style="background:#0f1825;border:1px solid rgba(226,183,20,0.1);border-radius:12px;
                    padding:14px;text-align:center;margin-top:8px;">
          <div style="font-size:10px;color:#4b5563;font-weight:800;letter-spacing:1px;
                      text-transform:uppercase;margin-bottom:8px;">ئاراستەی زێڕ</div>
          {badge_html(gold_score, large=True)}
          <div style="font-size:11px;color:#1f2937;margin-top:8px;">Score: {gold_score:+.3f}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # Real yield chart
    ry_df = merged[["date", "ry"]].rename(columns={"ry": "value"})
    fig = make_trend_chart(ry_df, "Real Yield")
    if fig:
        st.markdown('<div class="section-title">نموداری Real Yield (24 مانگ)</div>', unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Multi-timeframe breakdown
    if ry_mf:
        st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
        st.markdown('<div class="section-title">فراوانتایمفریم — Real Yield</div>', unsafe_allow_html=True)

        tf_items = [
            ("m/m",      ry_mf["mom"],        "بەرزبوون = خراپ بۆ زێڕ"),
            ("q/q",      ry_mf.get("qoq"),    "کوارتەرلی"),
            ("y/y",      ry_mf.get("yoy"),    "ساڵانە"),
            ("Trend 3m", ry_mf.get("t3m"),    "مۆمێنتۆم"),
        ]
        tf_cols = st.columns(4)
        for col, (lbl_tf, val, hint) in zip(tf_cols, tf_items):
            with col:
                v_str = f"{val:+.3f}%" if val is not None else "—"
                # For gold: Real Yield UP = bad
                good  = (val or 0) < 0
                clr   = "#10b981" if good else "#ef4444"
                eff   = "باش بۆ زێڕ" if good else "خراپ بۆ زێڕ"
                st.markdown(f"""
                <div style="background:#0f1825;border:1px solid rgba(255,255,255,0.05);
                            border-radius:12px;padding:14px;text-align:center;">
                  <div style="font-size:10px;color:#4b5563;font-weight:700;text-transform:uppercase;
                              margin-bottom:8px;letter-spacing:1px;">{lbl_tf}</div>
                  <div style="font-size:20px;font-weight:800;color:{clr};">{v_str}</div>
                  <div style="font-size:10px;color:{clr};margin-top:4px;">{eff}</div>
                  <div style="font-size:10px;color:#1f2937;margin-top:2px;">{hint}</div>
                </div>
                """, unsafe_allow_html=True)

    st.caption("ℹ️ Real Yield = DGS10 − T10YIE. بەرزبوونی Real Yield فشار دەخاتە سەر زێڕ (opportunity cost).")


# ============================================================
# PAGE: NEWS
# ============================================================

def render_news(news_key: str) -> None:
    st.markdown('<h3 style="color:#e5e7eb;margin-bottom:16px;">📰 هەواڵە جیهانییە خێراکان</h3>', unsafe_allow_html=True)

    cat = st.radio("", [
        "💣 Geopolitics & War", "🛢️ Energy & Oil",
        "🏛️ Central Banks",     "🤝 Trade Wars",
    ], horizontal=True, label_visibility="collapsed")

    kw = {
        "💣 Geopolitics & War": "war OR military OR conflict OR sanctions",
        "🛢️ Energy & Oil":      "oil OR opec OR crude OR energy crisis",
        "🏛️ Central Banks":     "fed OR central bank OR interest rates OR inflation",
        "🤝 Trade Wars":        "tariffs OR trade war OR import tax",
    }

    if not news_key:
        st.warning("🔑 NewsAPI Key لە سایدبار بنووسە (newsapi.org — خۆڕایی).")
        return

    with st.spinner("هەواڵەکان..."):
        arts = fetch_news(kw[cat], news_key)

    if not arts:
        st.info("هیچ هەواڵێک نەدۆزرایەوە.")
        return

    for art in arts:
        title  = art.get("title", "—")
        source = (art.get("source") or {}).get("name", "")
        pub    = (art.get("publishedAt") or "")[:10]
        desc   = art.get("description", "") or ""
        link   = art.get("url", "#")
        st.markdown(f"""
        <div class="metric-card" style="padding:16px;">
          <h4 style="color:#e2b714;margin:0 0 6px 0;font-size:14px;line-height:1.5;">{title}</h4>
          <p style="font-size:11px;color:#374151;margin:0 0 8px 0;">{source} • {pub}</p>
          <p style="font-size:13px;color:#9ca3af;margin:0 0 10px 0;line-height:1.6;">{desc}</p>
          <a href="{link}" target="_blank"
             style="color:#10b981;font-size:12px;font-weight:700;text-decoration:none;">خوێندنەوەی زیاتر ↗</a>
        </div>
        """, unsafe_allow_html=True)


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    inject_css()

    with st.sidebar:
        st.markdown("""
        <div class="sidebar-logo">
          <div class="sidebar-logo-icon">📊</div>
          <div>
            <div class="sidebar-logo-title">FX MACRO</div>
            <div class="sidebar-logo-sub">& GEOPOLITICAL DESK</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        page = st.radio("", [
            "🏠 سەرەکی",
            "📊 بازاری دراوەکان",
            "🌍 هەواڵی جیهانی",
            "🏦 زێڕ (Gold Analysis)",
            "📅 Monthly Outlook",
            "💡 Impact Table",
        ], label_visibility="collapsed")

        st.markdown("<hr class='sdivider'>", unsafe_allow_html=True)
        st.markdown("**🔐 API Keys**")
        fred_key     = st.text_input("FRED API Key:",    type="password", key="fred_key")
        news_api_key = st.text_input("NewsAPI Key:",     type="password", key="news_key")

        st.markdown("<hr class='sdivider'>", unsafe_allow_html=True)
        st.caption(f"🕓 {datetime.now().strftime('%Y-%m-%d  %H:%M')}")
        st.markdown("""
        <div class="sidebar-bottom">
          <div style="font-size:20px;">📡</div>
          <div style="font-size:12px;font-weight:700;color:#e5e7eb;margin-top:6px;">ئابووری و سیاسی جیهان</div>
          <div style="font-size:10px;color:#374151;margin-top:2px;">لە یەک شوێندا</div>
          <div style="font-size:9px;color:#1f2937;margin-top:4px;">Macro • Geopolitics • Markets</div>
        </div>
        """, unsafe_allow_html=True)

    # Routing
    if page == "🏠 سەرەکی":
        render_dashboard(fred_key, news_api_key)
    elif page == "📊 بازاری دراوەکان":
        render_currency_detail(fred_key)
    elif page == "🌍 هەواڵی جیهانی":
        render_news(news_api_key)
    elif page == "🏦 زێڕ (Gold Analysis)":
        render_gold_page(fred_key)
    elif page == "📅 Monthly Outlook":
        render_monthly(fred_key)
    elif page == "💡 Impact Table":
        st.markdown('<h3 style="color:#e5e7eb;margin-bottom:16px;">💡 کاریگەری رووداوە جیهانییەکان</h3>', unsafe_allow_html=True)
        st.markdown(IMPACT_TABLE_MD)

    st.markdown(
        '<div class="footer-note">FX Macro & News Intelligence Desk v4 — '
        'بۆ مەبەستی شیکاری و فێربوون، نەک ڕاوێژی دارایی.</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
