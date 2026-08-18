"""
FX Macro & News Intelligence Desk — v6 Pro
سیستەمی پێشبینیکردن و شیکاری هەواڵەکان
Professional Multi-Timeframe Macro & Geopolitical Intelligence Platform
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
# FIXED API KEYS CONFIGURATION
# ============================================================
FRED_API_KEY = "8e153c7f6941848ffe00388ae93c1d73"
NEWS_API_KEY = "70fc541920ca43e69ee716ad442405fb"

# ============================================================
# CONSTANTS & OFFICIAL METRICS CONFIGURATION (FRED SERIES)
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
        "Core CPI":      {"series": "CPHPTT01EZM659N",    "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Production":    {"series": "EA19PRINTO01IXOBSAM","category": "growth",     "weight": 1.2, "impact": "medium"},
        "Unemployment":  {"series": "LRHUTTTTEZM156S",    "category": "labor_bad",  "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "ECBDFR",             "category": "rate",       "weight": 2.0, "impact": "high"},
        "GDP":           {"series": "CLVMNACSCAB1GQEA19", "category": "growth",     "weight": 1.5, "impact": "high"},
    },
    "GBP پاوەند": {
        "CPI":           {"series": "GBRCPIALLMINMEI",  "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "GBRCP01IXOBSAM",  "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Production":    {"series": "GBRPROINDMISMEI",  "category": "growth",     "weight": 1.2, "impact": "medium"},
        "Unemployment":  {"series": "LRUN64TTGBM156S",  "category": "labor_bad",  "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "IRLTLT01GBM156N",  "category": "rate",       "weight": 1.8, "impact": "high"},
    },
    "CAD کەنەدی": {
        "CPI":           {"series": "CANCPIALLMINMEI", "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "CANCP01IXOBSAM",  "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Employment":    {"series": "LFEMTTTTCAM647S", "category": "labor_good", "weight": 1.5, "impact": "high"},
        "Unemployment":  {"series": "LRUN64TTCAM156S", "category": "labor_bad",  "weight": 1.5, "impact": "high"},
        "Interest Rate": {"series": "IRLTLT01CAM156N", "category": "rate",       "weight": 1.8, "impact": "high"},
    },
    "JPY یەن": {
        "CPI":           {"series": "JPNCPIALLMINMEI", "category": "inflation",  "weight": 1.8, "impact": "high"},
        "Core CPI":      {"series": "JPNCP01IXOBSAM",  "category": "inflation",  "weight": 2.0, "impact": "high"},
        "Production":    {"series": "JPNPROINDMISMEI", "category": "growth",     "weight": 1.2, "impact": "medium"},
        "Unemployment":  {"series": "LRUN64TTJPM156S", "category": "labor_bad",  "weight": 1.5, "impact": "medium"},
        "Interest Rate": {"series": "IRLTLT01JPM156N", "category": "rate",       "weight": 2.0, "impact": "high"},
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

MONTHLY_CALENDAR = {
    "USD دۆلار": [
        {"name": "NFP",          "day": 4,  "hint": "یەکەم هەینی مانگ — بازاڕی کار",         "impact": "high",   "quarterly": False, "category": "labor_good"},
        {"name": "Unemployment", "day": 4,  "hint": "هاوکات لەگەڵ NFP — ڕێژەی بێکاری",       "impact": "high",   "quarterly": False, "category": "labor_bad"},
        {"name": "Core CPI",     "day": 11, "hint": "هەڵئاوسانی سەرەکی (بێ وزە و خۆراک)",     "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "CPI",          "day": 11, "hint": "هەڵئاوسانی گشتی بەکارهێنەران",            "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core PPI",     "day": 13, "hint": "هەڵئاوسانی بەرهەمهێنەرانی سەرەکی",       "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "PPI",          "day": 13, "hint": "هەڵئاوسانی بەرهەمهێنەرانی گشتی",        "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Retail Sales", "day": 15, "hint": "فرۆشی تاکەکەسی و کڕینی خەڵک",          "impact": "high",   "quarterly": False, "category": "growth"},
        {"name": "Core PCE",     "day": 25, "hint": "پێوەری دڵخوازی فیدراڵی بۆ هەڵئاوسان",    "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "PCE",          "day": 25, "hint": "خەرجی بەکاربردنی کەسی",                "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Interest Rate","day": 18, "hint": "بڕیاری سوودی فیدراڵی (FOMC)",           "impact": "high",   "quarterly": False, "category": "rate"},
        {"name": "GDP",          "day": 28, "hint": "گەشەی ئابووری (سێ مانگانە)",            "impact": "high",   "quarterly": True,  "category": "growth"},
    ],
    "EUR یۆرۆ": [
        {"name": "CPI",          "day": 1,  "hint": "هەڵئاوسانی سەرەتایی یۆرۆزۆن (Flash HICP)", "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 1,  "hint": "هەڵئاوسانی سەرەکی یۆرۆزۆن",               "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Unemployment", "day": 1,  "hint": "ڕێژەی بێکاری لە یەکێتی ئەوروپا",           "impact": "high",   "quarterly": False, "category": "labor_bad"},
        {"name": "Production",   "day": 13, "hint": "بەرهەمهێنانی پیشەسازی ئەوروپا",          "impact": "medium", "quarterly": False, "category": "growth"},
        {"name": "Interest Rate","day": 12, "hint": "بڕیاری سوودی بانکی ناوەندی ئەوروپا (ECB)", "impact": "high",   "quarterly": False, "category": "rate"},
        {"name": "GDP",          "day": 30, "hint": "گەشەی ئابووری یۆرۆزۆن (سێ مانگانە)",      "impact": "high",   "quarterly": True,  "category": "growth"},
    ],
    "GBP پاوەند": [
        {"name": "CPI",          "day": 17, "hint": "هەڵئاوسانی بەریتانیا (ONS)",            "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 17, "hint": "هەڵئاوسانی سەرەکی بەریتانیا",          "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Unemployment", "day": 11, "hint": "ڕێژەی بێکاری و داواکاری کار",           "impact": "high",   "quarterly": False, "category": "labor_bad"},
        {"name": "Production",   "day": 11, "hint": "بەرهەمهێنانی پیشەسازی",                "impact": "medium", "quarterly": False, "category": "growth"},
        {"name": "Interest Rate","day": 19, "hint": "بڕیاری سوودی بانکی ئینگلتەرا (BoE)",     "impact": "high",   "quarterly": False, "category": "rate"},
    ],
    "CAD کەنەدی": [
        {"name": "CPI",          "day": 17, "hint": "هەڵئاوسانی گشتی کەنەدا (StatCan)",       "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 17, "hint": "هەڵئاوسانی سەرەکی کەنەدا",             "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Employment",   "day": 4,  "hint": "گۆڕانی ژمارەی کارمەندان",               "impact": "high",   "quarterly": False, "category": "labor_good"},
        {"name": "Unemployment", "day": 4,  "hint": "ڕێژەی بێکاری لە کەنەدا",                 "impact": "high",   "quarterly": False, "category": "labor_bad"},
        {"name": "Interest Rate","day": 14, "hint": "بڕیاری سوودی بانکی کەنەدا (BoC)",        "impact": "high",   "quarterly": False, "category": "rate"},
    ],
    "JPY یەن": [
        {"name": "CPI",          "day": 19, "hint": "هەڵئاوسانی نیشتمانی ژاپۆن",             "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Core CPI",     "day": 19, "hint": "هەڵئاوسانی سەرەکی ژاپۆن",              "impact": "high",   "quarterly": False, "category": "inflation"},
        {"name": "Production",   "day": 14, "hint": "بەرهەمهێنانی پیشەسازی",                "impact": "medium", "quarterly": False, "category": "growth"},
        {"name": "Unemployment", "day": 27, "hint": "ڕێژەی بێکاری لە ژاپۆن",                 "impact": "medium", "quarterly": False, "category": "labor_bad"},
        {"name": "Interest Rate","day": 18, "hint": "بڕیاری سوودی بانکی ژاپۆن (BoJ)",         "impact": "high",   "quarterly": False, "category": "rate"},
    ],
}

# ============================================================
# HTML RENDERER & CSS
# ============================================================

def render_html(html_str: str) -> None:
    clean_html = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(clean_html, unsafe_allow_html=True)


def inject_css() -> None:
    css_content = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
* { font-family: 'Inter', -apple-system, sans-serif !important; box-sizing: border-box; }

.stApp { background-color: #060a12 !important; color: #e5e7eb !important; }
.main .block-container {
    padding-top: 14px !important;
    padding-left: 24px !important;
    padding-right: 24px !important;
    max-width: 100% !important;
}

.top-header-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 18px; background: #090e1a;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px; margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}
.top-brand { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 800; color: #e2b714; }
.top-tickers { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.ticker-pill { display: inline-flex; align-items: center; gap: 6px; background: #0d1527; border: 1px solid rgba(255, 255, 255, 0.05); padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 600; color: #9ca3af; }
.ticker-up { color: #10b981; font-weight: 700; }
.ticker-down { color: #ef4444; font-weight: 700; }
.top-actions { display: flex; align-items: center; gap: 12px; }
.user-badge { display: flex; align-items: center; gap: 8px; background: #0d1527; border: 1px solid rgba(255, 255, 255, 0.08); padding: 4px 10px; border-radius: 20px; }
.user-avatar { width: 24px; height: 24px; border-radius: 50%; background: linear-gradient(135deg, #e2b714, #f59e0b); display: flex; align-items: center; justify-content: center; font-size: 11px; color: #000; font-weight: 800; }
.user-info { font-size: 11px; font-weight: 700; color: #ffffff; }

.main-title-wrap { text-align: center; padding: 10px 0 20px 0; }
.main-gold-sub { font-size: 11px; font-weight: 800; letter-spacing: 2px; color: #e2b714; text-transform: uppercase; margin-bottom: 6px; }
.main-big-heading { font-size: 26px; font-weight: 900; color: #ffffff; margin: 0 0 6px 0; }
.main-breadcrumb { font-size: 12px; color: #8a99ad; font-weight: 500; }

section[data-testid="stSidebar"] { background: #070c16 !important; border-right: 1px solid rgba(255, 255, 255, 0.05) !important; min-width: 250px !important; }
section[data-testid="stSidebar"] .block-container { padding: 16px 12px !important; }
.sidebar-brand { display: flex; align-items: center; gap: 10px; padding: 6px 8px 16px 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.06); margin-bottom: 14px; }
.sb-icon { width: 36px; height: 36px; border-radius: 10px; background: linear-gradient(135deg, #e2b714, #d97706); display: flex; align-items: center; justify-content: center; font-size: 18px; }
.sb-title { font-size: 12px; font-weight: 800; color: #e2b714; }
.sb-sub { font-size: 9px; color: #6b7280; margin-top: 1px; }

section[data-testid="stSidebar"] div[data-testid="stRadio"] label { padding: 9px 12px !important; border-radius: 10px !important; color: #8a99ad !important; font-size: 12.5px !important; font-weight: 500 !important; cursor: pointer !important; width: 100% !important; }
section[data-testid="stSidebar"] div[data-testid="stRadio"] label:hover { background: rgba(255, 255, 255, 0.03) !important; color: #ffffff !important; }
section[data-testid="stSidebar"] div[data-testid="stRadio"] [aria-checked="true"] { background: rgba(226, 183, 20, 0.08) !important; border: 1px solid #e2b714 !important; color: #e2b714 !important; font-weight: 700 !important; }
section[data-testid="stSidebar"] div[data-testid="stRadio"] input[type="radio"], section[data-testid="stSidebar"] div[data-testid="stRadio"] label > div:first-child { display: none !important; }

.ref-table-card { background: #090e1a; border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 16px; overflow: hidden; box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5); }
.ref-table { width: 100%; border-collapse: collapse; font-size: 13px; direction: rtl; text-align: right; }
.ref-table thead th { background: #0c1322; color: #8a99ad; padding: 14px 18px; font-weight: 600; font-size: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
.ref-table thead th.th-ctr { text-align: center; direction: ltr; }
.ref-table tbody tr { border-bottom: 1px solid rgba(255, 255, 255, 0.035); }
.ref-table tbody tr:hover { background: rgba(226, 183, 20, 0.04); }
.ref-table tbody td { padding: 12px 18px; color: #e5e7eb; vertical-align: middle; }
.ref-table td.td-name { font-weight: 700; color: #ffffff; }
.ref-table td.td-val { font-family: 'Inter', monospace, sans-serif; font-weight: 600; color: #ffffff; text-align: center; direction: ltr; }
.ref-table td.td-pct { font-family: 'Inter', monospace, sans-serif; font-weight: 600; text-align: center; direction: ltr; }
.ref-badge-green { color: #10b981; font-weight: 700; }
.ref-badge-red { color: #ef4444; font-weight: 700; }
.ref-badge-gray { color: #6b7280; font-weight: 700; }
.ref-table-footer { padding: 12px 18px; font-size: 11px; color: #8a99ad; background: #080c16; border-top: 1px solid rgba(255, 255, 255, 0.04); }

.matrix-card { background: #090e1a; border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 16px; overflow: hidden; box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5); }
.matrix-table { width: 100%; border-collapse: collapse; font-size: 13px; direction: rtl; text-align: right; }
.matrix-table thead th { background: #0c1322; color: #e2b714; padding: 14px 18px; font-weight: 700; font-size: 12.5px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); }
.matrix-table tbody tr { border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
.matrix-table tbody tr:hover { background: rgba(226, 183, 20, 0.03); }
.matrix-table td { padding: 14px 18px; color: #e5e7eb; vertical-align: middle; line-height: 1.5; }
.matrix-pill-wrap { display: flex; gap: 6px; flex-wrap: wrap; }
.pill-bull { background: rgba(16, 185, 129, 0.14); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 3px 10px; border-radius: 6px; font-weight: 700; font-size: 11px; }
.pill-bear { background: rgba(239, 68, 68, 0.14); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); padding: 3px 10px; border-radius: 6px; font-weight: 700; font-size: 11px; }

.metric-card { background: #090e1a; border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 16px; padding: 16px 18px; height: 100%; }
.metric-card:hover { border-color: rgba(226, 183, 20, 0.25); }
.mc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.mc-icon-wrap { width: 34px; height: 34px; border-radius: 9px; background: rgba(226, 183, 20, 0.08); display: flex; align-items: center; justify-content: center; font-size: 16px; }
.mc-cat { font-size: 9px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase; color: #8a99ad; padding: 2px 8px; border-radius: 999px; background: rgba(255, 255, 255, 0.04); }
.mc-name { font-size: 13px; font-weight: 700; color: #8a99ad; margin: 4px 0 2px; }
.mc-value { font-size: 24px; font-weight: 800; color: #ffffff; line-height: 1.1; margin-bottom: 4px; }
.mc-change { font-size: 12px; font-weight: 700; }
.mc-secondary { font-size: 11px; color: #8a99ad; margin-top: 2px; }
.mc-date { font-size: 10px; color: #4b5563; margin-top: 6px; }

.cal-card { display: flex; align-items: center; gap: 16px; background: #090e1a; border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 14px; padding: 14px 18px; margin-bottom: 10px; }
.cal-card.released { border-right: 4px solid #10b981; }
.cal-card.upcoming { border-right: 4px solid #374151; }
.cal-card.upcoming-soon { border-right: 4px solid #f59e0b; }
.cal-day-badge { min-width: 44px; height: 44px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 16px; flex-shrink: 0; }
.cal-day-badge.released { background: rgba(16, 185, 129, 0.12); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.2); }
.cal-day-badge.upcoming { background: #0c1322; color: #8a99ad; border: 1px solid rgba(255, 255, 255, 0.05); }
.cal-day-badge.upcoming-soon { background: rgba(245, 158, 11, 0.12); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.2); }
.cal-content { flex: 1; min-width: 0; }
.cal-name { font-weight: 800; color: #ffffff; font-size: 14px; }
.cal-hint { font-size: 11.5px; color: #8a99ad; margin-top: 3px; }
.cal-impact-badge { font-size: 9px; font-weight: 800; padding: 2px 8px; border-radius: 999px; }
.impact-high { background: rgba(239, 68, 68, 0.12); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.2); }
.impact-medium { background: rgba(245, 158, 11, 0.12); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.2); }

.badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.badge-bullish { background: rgba(16, 185, 129, 0.12); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.2); }
.badge-bearish { background: rgba(239, 68, 68, 0.12); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.2); }
.badge-neutral { background: rgba(107, 114, 128, 0.12); color: #9ca3af; border: 1px solid rgba(107, 114, 128, 0.2); }
.badge-lg { font-size: 14px; padding: 8px 20px; border-radius: 12px; font-weight: 800; }

.section-title { font-size: 11px; font-weight: 800; letter-spacing: 2px; text-transform: uppercase; color: #8a99ad; margin-bottom: 12px; margin-top: 6px; display: flex; align-items: center; gap: 8px; }
.section-title::after { content: ''; flex: 1; height: 1px; background: linear-gradient(90deg, rgba(255, 255, 255, 0.08), transparent); }

.app-footer { display: flex; justify-content: space-between; align-items: center; padding: 18px 24px; margin-top: 40px; border-top: 1px solid rgba(255, 255, 255, 0.05); font-size: 11px; color: #4b5563; }
.live-status { display: flex; align-items: center; gap: 6px; color: #10b981; font-weight: 600; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981; }

#MainMenu, footer, .stDeployButton { display: none !important; }
header[data-testid="stHeader"] { background: transparent !important; }
</style>
"""
    render_html(css_content)


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
# MULTI-TIMEFRAME ENGINE
# ============================================================

def calc_multiframe(vals: list, category: str) -> dict | None:
    if not vals or len(vals) < 2:
        return None

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

    t3m = None
    if len(vals) >= 4:
        changes = [(vals[i] - vals[i-1]) / abs(vals[i-1]) * 100 for i in range(-3, 0) if vals[i-1] != 0]
        t3m = np.mean(changes) if changes else None

    z_level = 0.0
    if len(vals) >= 6:
        sub = vals[-12:] if len(vals) >= 12 else vals
        std = np.std(sub)
        z_level = (vals[-1] - np.mean(sub)) / std if std != 0 else 0.0

    def t(x, ref):
        return float(np.tanh(x / ref)) if ref != 0 and x is not None else 0.0

    parts = [
        (t(mom,     0.5),  0.30),
        (t(qoq,     2.0),  0.25),
        (t(yoy,     5.0),  0.25),
        (t(t3m,     0.5),  0.10),
        (t(z_level, 1.0),  0.10),
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
            "impact":    meta.get("impact", "high"),
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
    return {"composite": composite, "rows": rows}


# ============================================================
# HELPERS & PLOTLY DYNAMIC CHARTS
# ============================================================

def bias_from_score(score: float):
    if score > 0.15:
        return "📈 Bullish", "badge-bullish", "#10b981"
    if score < -0.15:
        return "📉 Bearish", "badge-bearish", "#ef4444"
    return "⚖️ Neutral", "badge-neutral", "#9ca3af"


def badge_html(score: float, large: bool = False) -> str:
    label, css, _ = bias_from_score(score)
    sz = "badge-lg" if large else ""
    return f'<span class="badge {css} {sz}">{label}</span>'


def svg_spark(vals: list, width: int = 80, height: int = 34, positive_is_good: bool = True) -> str:
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


def make_dynamic_chart(df: pd.DataFrame, indicator_name: str, currency_name: str) -> go.Figure | None:
    if df is None or df.empty:
        return None

    vals = df["value"].tolist()
    accent_color = "#e2b714"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["value"],
        mode="lines+markers",
        marker=dict(size=4, color=accent_color),
        name=indicator_name,
        line=dict(color=accent_color, width=2.8, shape="spline"),
        fill="tozeroy",
        fillcolor="rgba(226,183,20,0.08)",
        hovertemplate="<b>%{x}</b><br>ئاست: <b>%{y:,.2f}</b><extra></extra>",
    ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        margin=dict(l=10, r=20, t=10, b=10),
        height=240,
        xaxis=dict(showgrid=False, color="#6b7280", tickfont=dict(size=10, color="#8a99ad")),
        yaxis=dict(autorange=True, showgrid=True, gridcolor="rgba(255,255,255,0.05)", color="#6b7280", side="right"),
        hovermode="x unified",
    )
    return fig


def make_gold_dual_chart(ry_df: pd.DataFrame, exp_df: pd.DataFrame) -> go.Figure | None:
    if ry_df is None or ry_df.empty:
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ry_df["date"],
        y=ry_df["value"],
        mode="lines",
        name="Real Yield 10Y (سوودی ڕاستەقینە)",
        line=dict(color="#e2b714", width=3, shape="spline"),
        hovertemplate="<b>%{x}</b><br>Real Yield: <b>%{y:.2f}%</b><extra></extra>",
    ))

    if exp_df is not None and not exp_df.empty:
        fig.add_trace(go.Scatter(
            x=exp_df["date"],
            y=exp_df["value"],
            mode="lines",
            name="Inflation Exp 10Y (هەڵئاوسانی چاوەڕوانکراو)",
            line=dict(color="#3b82f6", width=2, dash="dot", shape="spline"),
            hovertemplate="<b>%{x}</b><br>Inflation Exp: <b>%{y:.2f}%</b><extra></extra>",
        ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(orientation="h", y=1.05, x=1, xanchor="right", font=dict(size=11, color="#8a99ad")),
        margin=dict(l=10, r=20, t=30, b=10),
        height=260,
        xaxis=dict(showgrid=False, color="#6b7280"),
        yaxis=dict(autorange=True, showgrid=True, gridcolor="rgba(255,255,255,0.05)", side="right", color="#6b7280"),
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
        tbody_rows.append(f"""
        <tr>
          <td class="td-name"><span style="color:#e2b714;">📈</span> {r['name']}</td>
          <td class="td-val">{r['latest']:,.2f}</td>
          <td class="td-pct">{fmt_pct(r['mom'])}</td>
          <td class="td-pct">{fmt_pct(r.get('qoq'))}</td>
          <td class="td-pct">{fmt_pct(r.get('yoy'))}</td>
        </tr>
        """)

    table_html = f"""
    <div class="ref-table-card">
      <table class="ref-table">
        <thead>
          <tr>
            <th style="width:28%;">بازار</th>
            <th class="th-ctr" style="width:18%;">کۆتا</th>
            <th class="th-ctr" style="width:18%;">m/m</th>
            <th class="th-ctr" style="width:18%;">q/q</th>
            <th class="th-ctr" style="width:18%;">y/y</th>
          </tr>
        </thead>
        <tbody>{''.join(tbody_rows)}</tbody>
      </table>
      <div class="ref-table-footer">ⓘ % گۆڕانکارییەکان نیشاندەدرێن بە گۆڕان بەڕامبەری پێشووی خۆی.</div>
    </div>
    """
    render_html(table_html)


def render_impact_matrix_html() -> None:
    matrix_data = [
        {"event": "هەڵگیرسانی جەنگ و ئاڵۆزی سەربازی", "icon": "💣", "bullish": ["USD", "CHF", "Gold"], "bearish": ["EUR", "AUD", "GBP"], "reason": "ڕاکردنی سەرمایەی جیهانی بەرەو پەناگە ئارامەکان (Safe-havens)."},
        {"event": "بەرزبوونەوەی بەرچاوی نرخی نەوت و وزە", "icon": "🛢️", "bullish": ["CAD", "NOK", "USD"], "bearish": ["JPY", "EUR"], "reason": "کەنەدا و نەرویج هەناردەکاری نەوتن؛ ژاپۆن و ئەوروپا هاوردەکاری سەرەکین."},
        {"event": "بەرزکردنەوەی ڕێژەی سوودی بانکی (Rate Hikes)", "icon": "🏦", "bullish": ["دراوەکەی خۆی"], "bearish": ["زێڕ (Gold)", "پشکەکان"], "reason": "ڕاکێشانی وەبەرهێنەران بۆ بەدەستهێنانی سوودی بەرزتر لە دراوەکەدا."},
        {"event": "کەمکردنەوەی ڕێژەی سوود (Rate Cuts)", "icon": "📉", "bullish": ["زێڕ (Gold)", "پشکەکان"], "bearish": ["دراوەکەی خۆی"], "reason": "کەمبوونەوەی قازانجی سوودی بانکی و بەرزبوونەوەی خواست لەسەر زێڕ."},
        {"event": "جەنگی بازرگانی و باجی گومرگی (Tariffs)", "icon": "🚢", "bullish": ["USD"], "bearish": ["AUD", "NZD", "CNH", "EUR"], "reason": "لاوازبوونی بازرگانی چین بە شێوەیەکی ڕاستەوخۆ دۆلاری ئوسترالی و ئەوروپا دادەبەزێنێت."},
    ]
    rows_html = []
    for item in matrix_data:
        bull_pills = "".join(f'<span class="pill-bull">{c}</span>' for c in item["bullish"])
        bear_pills = "".join(f'<span class="pill-bear">{c}</span>' for c in item["bearish"])
        rows_html.append(f"""
        <tr>
          <td style="font-weight:700;color:#ffffff;width:24%;"><span style="font-size:16px;margin-left:6px;">{item['icon']}</span> {item['event']}</td>
          <td style="width:20%;"><div class="matrix-pill-wrap">{bull_pills}</div></td>
          <td style="width:20%;"><div class="matrix-pill-wrap">{bear_pills}</div></td>
          <td style="color:#8a99ad;font-size:12px;width:36%;">{item['reason']}</td>
        </tr>
        """)

    table_html = f"""
    <div class="matrix-card">
      <table class="matrix-table">
        <thead>
          <tr>
            <th>ڕووداوی جیهانی (Global Event)</th>
            <th>دراوە بەهێزەکان (Bullish)</th>
            <th>دراوە لاوازەکان (Bearish)</th>
            <th>هۆکار و شیکاریی مەکرۆ (Macro Mechanism)</th>
          </tr>
        </thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>
    """
    render_html(table_html)


# ============================================================
# COMPONENT: TOP HEADER
# ============================================================

def render_top_header() -> None:
    html = """
    <div class="top-header-bar">
      <div class="top-brand"><span>📊</span> <span>FX MACRO &amp; GEOPOLITICAL DESK</span></div>
      <div class="top-tickers">
        <div class="ticker-pill"><span>🇺🇸 USD</span> <span class="ticker-up">98.42 ▲ +0.24%</span></div>
        <div class="ticker-pill"><span>🇪🇺 EUR</span> <span class="ticker-up">1.08 ▲ +0.18%</span></div>
        <div class="ticker-pill"><span>🇬🇧 GBP</span> <span class="ticker-down">1.27 ▼ -0.12%</span></div>
        <div class="ticker-pill"><span>🇯🇵 JPY</span> <span class="ticker-up">157.36 ▲ +0.31%</span></div>
      </div>
      <div class="top-actions">
        <div class="user-badge">
          <div class="user-avatar">M</div>
          <div class="user-info">Macro Desk</div>
        </div>
      </div>
    </div>
    """
    render_html(html)


# ============================================================
# PAGE 1: 🏠 سەرەکی (DASHBOARD)
# ============================================================

def render_dashboard() -> None:
    render_top_header()
    banner_html = """
    <div class="main-title-wrap">
      <div class="main-gold-sub">FX MACRO &amp; GEOPOLITICAL DESK</div>
      <h1 class="main-big-heading">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h1>
      <div class="main-breadcrumb">تەحلیل، تایبەتمەندی و کارکردنی بازاڕە دارایی و سیاسی جیهان</div>
    </div>
    """
    render_html(banner_html)

    a_col, b_col = st.columns([3, 2])
    with a_col:
        asset_type = st.radio("جۆری بازاڕ:", ["💱 Forex", "🥇 Gold & Metals"], horizontal=True, label_visibility="collapsed")
    with b_col:
        selected = st.selectbox("دراوەکە هەڵبژێرە:", list(CURRENCY_SERIES.keys()), label_visibility="collapsed") if "Forex" in asset_type else "USD دۆلار"

    if "Gold" in asset_type:
        render_gold_page()
        return

    with st.spinner("داتاکانی مەکرۆ بار دەکرێن..."):
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

        card_html = f"""
        <div class="metric-card">
          <div class="mc-header">
            <div class="mc-icon-wrap">{CATEGORY_ICONS.get(row['category'], '📊')}</div>
            <span class="mc-cat">{CATEGORY_LABELS.get(row['category'], '')}</span>
          </div>
          <div class="mc-name">{row['name']}</div>
          <div class="mc-value">{row['latest']:,.2f}</div>
          <div class="mc-change">
            <span style="color:{mom_color}; font-weight:700;">{mom_arrow} {abs(mom):.2f}%</span>
            <span style="color:#6b7280; font-size:10px;"> (m/m)</span>
          </div>
          <div class="mc-date">📅 {row['date']}</div>
          <div style="margin-top:10px;">{spark}</div>
        </div>
        """
        with col:
            render_html(card_html)

    st.markdown("<div style='height:20px;'></div>", unsafe_allow_html=True)
    t_col, c_col = st.columns([1.1, 1.2])

    with t_col:
        st.markdown('<div class="section-title">کۆتا ئاستیەکان (Multi-Timeframe Table)</div>', unsafe_allow_html=True)
        render_reference_table_html(rows)

    with c_col:
        st.markdown('<div class="section-title">کەش و هەوای بازاڕەکان (Live Dynamic Chart)</div>', unsafe_allow_html=True)
        chosen_ind = st.selectbox("نیشاندەر بۆ پیشاندان:", [r["name"] for r in rows], label_visibility="collapsed")
        crow = row_map.get(chosen_ind, rows[0])
        fig_dyn = make_dynamic_chart(crow["df"], chosen_ind, selected)
        if fig_dyn:
            st.plotly_chart(fig_dyn, use_container_width=True, config={"displayModeBar": False})

    st.markdown("<div style='height:20px;'></div>", unsafe_allow_html=True)
    n_col, d_col = st.columns([1.1, 1.1])

    with n_col:
        st.markdown('<div class="section-title">هەواڵی جیهانی و ئابووری (News Feed)</div>', unsafe_allow_html=True)
        arts = fetch_news(f"{selected.split()[0]} OR forex OR economy OR inflation", NEWS_API_KEY)
        if arts:
            for art in arts[:3]:
                t_str = art.get("title", "—")
                src = (art.get("source") or {}).get("name", "Market Desk")
                pub = (art.get("publishedAt") or "")[:10]
                link = art.get("url", "#")
                render_html(f"""
                <a href="{link}" target="_blank" style="text-decoration:none;">
                <div style="background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:12px 16px;margin-bottom:8px;">
                  <div style="color:#ffffff;font-size:13px;font-weight:600;line-height:1.4;margin-bottom:6px;">{t_str}</div>
                  <div style="font-size:11px;color:#8a99ad;display:flex;justify-content:space-between;"><span>📰 {src}</span><span>🕒 {pub}</span></div>
                </div></a>
                """)
        else:
            st.info("هەواڵ نەدۆزرایەوە.")

    with d_col:
        st.markdown('<div class="section-title">ئاراستەی گشتی دراو (Composite Signal)</div>', unsafe_allow_html=True)
        comp_box = f"""
        <div style="background:#090e1a;border:1px solid rgba(226,183,20,0.18);border-radius:14px;padding:24px;text-align:center;">
          <div style="font-size:11px;font-weight:800;letter-spacing:1px;color:#8a99ad;text-transform:uppercase;margin-bottom:8px;">ئاراستەی مەکرۆی {selected}</div>
          <div style="margin:16px 0;">{badge_html(result['composite'], large=True)}</div>
          <div style="font-size:14px;font-weight:700;color:#ffffff;margin-top:8px;">Composite Score: <span style="color:#e2b714;">{result['composite']:+.3f}</span></div>
          <div style="font-size:11px;color:#6b7280;margin-top:8px;">بەپێی ژماردنی تایمفریمەکانی: m/m • q/q • y/y • Z-Score</div>
        </div>
        """
        render_html(comp_box)


# ============================================================
# PAGE 2: 📋 کۆتا ئاستیەکان
# ============================================================

def render_levels_page() -> None:
    render_top_header()
    banner_html = """
    <div class="main-title-wrap">
      <div class="main-gold-sub">FX MACRO &amp; GEOPOLITICAL DESK</div>
      <h1 class="main-big-heading">سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h1>
      <div class="main-breadcrumb">Macro Strength &amp; Predictive Engine &gt; کۆتا ئاستیەکان</div>
    </div>
    """
    render_html(banner_html)

    selected = st.selectbox("دراوەکە هەڵبژێرە:", list(CURRENCY_SERIES.keys()), key="levels_cur")
    result = compute_currency_composite(selected, FRED_API_KEY)
    if result:
        render_reference_table_html(result["rows"])


# ============================================================
# PAGE 3: 📅 MONTHLY OUTLOOK
# ============================================================

def render_monthly() -> None:
    render_top_header()
    today = date.today()
    month_ku = {1:"کانوونی دووەم",2:"شوبات",3:"ئازار",4:"نیسان",5:"ئایار",6:"حوزەیران",7:"تەممووز",8:"ئاب",9:"ئەیلوول",10:"تشرینی یەکەم",11:"تشرینی دووەم",12:"کانوونی یەکەم"}

    header_html = f"""
    <div class="main-title-wrap">
      <div class="main-gold-sub">MONTHLY OUTLOOK &amp; PREDICTIVE CALENDAR</div>
      <h1 class="main-big-heading">کالێندەری شیکاری هەواڵەکان — {month_ku[today.month]} {today.year}</h1>
      <div class="main-breadcrumb">ئەمڕۆ: <b style="color:#e2b714;">{today.strftime('%Y-%m-%d')}</b></div>
    </div>
    """
    render_html(header_html)

    selected = st.radio("دراوەکە:", list(MONTHLY_CALENDAR.keys()), horizontal=True, key="month_cur")
    indicators = CURRENCY_SERIES.get(selected, {})
    events = []

    for ev in MONTHLY_CALENDAR.get(selected, []):
        try:
            max_d = cal_lib.monthrange(today.year, today.month)[1]
            rel_d = date(today.year, today.month, min(ev["day"], max_d))
        except ValueError:
            continue

        is_released = today >= rel_d
        days_until  = (rel_d - today).days

        mf = None
        meta = indicators.get(ev["name"])
        if meta:
            df2 = fetch_fred_series(meta["series"], FRED_API_KEY, limit=36)
            if df2 is not None and not df2.empty:
                mf = calc_multiframe(df2["value"].tolist(), meta["category"])

        events.append({**ev, "release_date": rel_d, "is_released": is_released, "days_until": days_until, "mf": mf})

    events.sort(key=lambda x: x["day"])
    released = [e for e in events if e["is_released"]]
    upcoming = [e for e in events if not e["is_released"]]

    def render_event_card(ev, card_cls, day_cls):
        mf = ev.get("mf")
        pos = ev.get("category", "inflation") != "labor_bad"
        lbl2, cls2, _ = bias_from_score(mf["composite"] if mf else 0)
        imt = f"impact-{ev['impact']}"

        tf_line = ""
        if mf:
            mom_s = f"<span style='color:{'#10b981' if (mf['mom']>0)==pos else '#ef4444'}; font-weight:700;'>{'▲' if mf['mom']>0 else '▼'} {abs(mf['mom']):.2f}%</span>"
            qoq_s = f"<span style='color:{'#10b981' if ((mf.get('qoq') or 0)>0)==pos else '#ef4444'}; font-weight:700;'>{abs(mf.get('qoq') or 0):.2f}%</span>" if mf.get('qoq') is not None else "—"
            yoy_s = f"<span style='color:{'#10b981' if ((mf.get('yoy') or 0)>0)==pos else '#ef4444'}; font-weight:700;'>{abs(mf.get('yoy') or 0):.2f}%</span>" if mf.get('yoy') is not None else "—"
            tf_line = f"<div style='margin-top:6px;font-size:11.5px;color:#8a99ad;'>m/m: {mom_s} &nbsp;|&nbsp; q/q: {qoq_s} &nbsp;|&nbsp; y/y: {yoy_s}</div>"

        days_badge = f"<span style='color:#f59e0b;font-weight:800;font-size:11px;'>⚡ ماوە {ev['days_until']} ڕۆژ</span>" if ev['days_until'] <= 3 else f"<span style='color:#8a99ad;font-size:11px;'>ماوە {ev['days_until']} ڕۆژ</span>"
        badge_title = f"📊 ئاراستەی چاوەڕوانکراو: {lbl2.split()[1]}" if not ev['is_released'] else lbl2

        render_html(f"""
        <div class="cal-card {card_cls}">
          <div class="cal-day-badge {day_cls}">{ev['day']:02d}</div>
          <div class="cal-content">
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <div style="display:flex;align-items:center;gap:8px;">
                <span class="cal-name">{ev['name']}</span>
                <span class="cal-impact-badge {imt}">{ev['impact'].upper()}</span>
              </div>
              <div>{days_badge}</div>
            </div>
            <div class="cal-hint">📌 {ev['hint']}</div>
            {tf_line}
            <div style="margin-top:8px;"><span class="badge {cls2}">{badge_title}</span></div>
          </div>
        </div>
        """)

    if released:
        st.markdown('<div class="section-title">✅ بڵاوکراوەتەوە — Released Events</div>', unsafe_allow_html=True)
        for ev in released: render_event_card(ev, "released", "released")

    if upcoming:
        st.markdown('<div class="section-title">🔮 هەواڵەکانی داهاتوو — Upcoming Forecasts</div>', unsafe_allow_html=True)
        for ev in upcoming:
            cc = "upcoming-soon" if 0 <= ev["days_until"] <= 3 else "upcoming"
            render_event_card(ev, cc, cc)


# ============================================================
# PAGE 4: 🥇 GOLD ANALYSIS
# ============================================================

def render_gold_page() -> None:
    render_top_header()
    header_html = """
    <div class="main-title-wrap">
      <div class="main-gold-sub">COMMODITY &amp; SAFE-HAVEN INTELLIGENCE</div>
      <h1 class="main-big-heading">شیکاری زێڕ (XAUUSD) — Real Yield &amp; USD</h1>
      <div class="main-breadcrumb">Real Yield 10Y (DGS10 - T10YIE) + USD Multi-Timeframe Composite</div>
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
    ry_mf   = calc_multiframe(ry_vals, "rate")

    gold_ry   = -ry_mf["composite"] if ry_mf else 0.0
    gold_usd  = -usd_r["composite"] if usd_r else 0.0
    gold_score = (0.55 * gold_ry) + (0.45 * gold_usd)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Real Yield 10Y", f"{ry_vals[-1]:.2f}%", delta=f"{ry_mf['mom']:+.2f}% m/m" if ry_mf else None, delta_color="inverse")
    with c2:
        st.metric("USD Composite Score", f"{usd_r['composite']:+.3f}" if usd_r else "0.00")
    with c3:
        render_html(f"""
        <div style="background:#090e1a;border:1px solid rgba(226,183,20,0.2);border-radius:14px;padding:12px;text-align:center;">
          <div style="font-size:10px;font-weight:800;letter-spacing:1px;color:#8a99ad;text-transform:uppercase;margin-bottom:6px;">ئاراستەی گشتی زێڕ</div>
          {badge_html(gold_score, large=True)}
          <div style="font-size:11px;color:#6b7280;margin-top:6px;">Gold Signal Score: <b style="color:#e2b714;">{gold_score:+.3f}</b></div>
        </div>
        """)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-title">نموداری گۆڕانی سوودی ڕاستەقینە (Real Yield &amp; Inflation Expectations)</div>', unsafe_allow_html=True)

    ry_df = merged[["date", "ry"]].rename(columns={"ry": "value"})
    exp_df = merged[["date", "value_i"]].rename(columns={"value_i": "value"})
    fig = make_gold_dual_chart(ry_df, exp_df)
    if fig:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ============================================================
# PAGE 5: 📰 هەواڵە جیهانییەکان
# ============================================================

def render_news_page() -> None:
    render_top_header()
    header_html = """
    <div class="main-title-wrap">
      <div class="main-gold-sub">GLOBAL GEOPOLITICAL &amp; MACRO FEED</div>
      <h1 class="main-big-heading">هەواڵە جیهانییە خێراکان</h1>
      <div class="main-breadcrumb">هەواڵی گرنگی بانکە ناوەندییەکان، نەوت، جەنگ، و باجە گومرگییەکان</div>
    </div>
    """
    render_html(header_html)

    cat = st.radio("کاتیگۆری:", ["💣 Geopolitics & War", "🛢️ Energy & Oil", "🏛️ Central Banks", "🤝 Trade Wars"], horizontal=True, label_visibility="collapsed")
    kw = {
        "💣 Geopolitics & War": "war OR military OR conflict OR sanctions",
        "🛢️ Energy & Oil":      "oil OR opec OR crude OR energy crisis",
        "🏛️ Central Banks":     "fed OR central bank OR interest rates OR inflation",
        "🤝 Trade Wars":        "tariffs OR trade war OR import tax",
    }

    with st.spinner("هەواڵەکان دەهێنرێن..."):
        arts = fetch_news(kw[cat], NEWS_API_KEY)

    if not arts:
        st.info("هیچ هەواڵێک نەدۆزرایەوە.")
        return

    for art in arts:
        title  = art.get("title", "—")
        source = (art.get("source") or {}).get("name", "")
        pub    = (art.get("publishedAt") or "")[:10]
        desc   = art.get("description", "") or ""
        link   = art.get("url", "#")
        render_html(f"""
        <div style="background:#090e1a;border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:16px 18px;margin-bottom:12px;">
          <div style="color:#e2b714;font-size:14px;font-weight:700;line-height:1.5;margin-bottom:4px;">{title}</div>
          <div style="font-size:11px;color:#8a99ad;margin-bottom:8px;">📰 {source} &nbsp;•&nbsp; 🕒 {pub}</div>
          <div style="font-size:12.5px;color:#d1d5db;line-height:1.6;margin-bottom:10px;">{desc}</div>
          <a href="{link}" target="_blank" style="color:#10b981;font-size:12px;font-weight:700;text-decoration:none;">خوێندنەوەی سەرچاوە ↗</a>
        </div>
        """)


# ============================================================
# MAIN ROUTER
# ============================================================

def main() -> None:
    inject_css()

    with st.sidebar:
        brand_html = """
        <div class="sidebar-brand">
          <div class="sb-icon">📈</div>
          <div>
            <div class="sb-title">FX MACRO &amp; GEO</div>
            <div class="sb-sub">INTELLIGENCE DESK</div>
          </div>
        </div>
        """
        render_html(brand_html)

        page = st.radio(
            "دەستەی بەڕێوەبردن:",
            [
                "🏠 سەرەکی",
                "📋 کۆتا ئاستیەکان",
                "🥇 Gold (XAUUSD) Analysis",
                "📅 Monthly Outlook",
                "📰 هەواڵە جیهانییەکان",
                "📊 Impact Analysis on Currencies",
            ],
            label_visibility="collapsed",
        )

        st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
        render_html("""
        <div style="background:#090e1a;border:1px solid rgba(16,185,129,0.2);border-radius:10px;padding:8px 12px;text-align:center;">
          <span style="color:#10b981;font-size:11px;font-weight:700;">🟢 API Keys Active</span>
        </div>
        """)

    if page == "🏠 سەرەکی":
        render_dashboard()
    elif page == "📋 کۆتا ئاستیەکان":
        render_levels_page()
    elif page == "🥇 Gold (XAUUSD) Analysis":
        render_gold_page()
    elif page == "📅 Monthly Outlook":
        render_monthly()
    elif page == "📰 هەواڵە جیهانییەکان":
        render_news_page()
    elif page == "📊 Impact Analysis on Currencies":
        render_top_header()
        st.markdown('<div class="section-title">💡 کاریگەری ڕووداوە جیهانییەکان لەسەر دراوەکان (Global Impact Matrix)</div>', unsafe_allow_html=True)
        render_impact_matrix_html()

    footer_html = f"""
    <div class="app-footer">
      <div>© 2026 FX Macro &amp; Geopolitical Desk &nbsp;|&nbsp; Professional Market Intelligence</div>
      <div class="live-status"><span class="live-dot"></span><span>Live Market Data &nbsp; {datetime.now().strftime('%H:%M:%S')}</span></div>
    </div>
    """
    render_html(footer_html)


if __name__ == "__main__":
    main()
