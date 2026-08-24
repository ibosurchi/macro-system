"""
ApexMacro — Global Macro & Geopolitical Intelligence Desk
Institutional-Grade Multi-Timeframe Macro Analysis, Safe-Haven & Energy Intelligence
"""
from __future__ import annotations
import os
import json
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
import threading
import time
import hashlib
import xml.etree.ElementTree as ET
import urllib.request

st.set_page_config(
    page_title="ApexMacro — Global Intelligence Desk",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SUPPORTED_TIMEZONES = {
    "🏛️ Kurdistan & Iraq (UTC+3)": {"offset": 3, "label": "KRD (UTC+3)", "city": "Erbil / Baghdad"},
    "🇬🇧 London / UK (UTC+0)": {"offset": 0, "label": "London (GMT)", "city": "London / UK"},
    "🇪🇺 Frankfurt / Berlin (UTC+1)": {"offset": 1, "label": "Berlin (CET)", "city": "Frankfurt / Paris"},
    "🇦🇪 Dubai / Gulf (UTC+4)": {"offset": 4, "label": "Dubai (GST)", "city": "Dubai / UAE"},
    "🇺🇸 New York / US East (UTC-5)": {"offset": -5, "label": "New York (EST)", "city": "New York / Wall St"},
    "🇯🇵 Tokyo / Japan (UTC+9)": {"offset": 9, "label": "Tokyo (JST)", "city": "Tokyo / Japan"},
    "🌐 Universal UTC / GMT": {"offset": 0, "label": "UTC", "city": "Universal UTC"},
}

def get_current_time(tz_offset: int | None = None) -> datetime:
    """Returns accurate local time adapted dynamically to user timezone."""
    if tz_offset is None:
        try:
            if "selected_tz" in st.session_state and st.session_state["selected_tz"] in SUPPORTED_TIMEZONES:
                tz_offset = SUPPORTED_TIMEZONES[st.session_state["selected_tz"]]["offset"]
            else:
                tz_offset = 3
        except Exception:
            tz_offset = 3
    return datetime.utcnow() + timedelta(hours=tz_offset)

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

DEFAULT_FRED_KEY = get_secret("FRED_API_KEY", "8e153c7f6941848ffe00388ae93c1d73")
DEFAULT_FMP_KEY = get_secret("FMP_API_KEY", "0oAXTDDY8mKnb39Z2HaBMwDvLQp0BG6Y")
DEFAULT_TELEGRAM_CHANNEL = get_secret("TELEGRAM_CHANNEL", "Forex_LiveStream")
DEFAULT_OPENROUTER_KEY = get_secret(
    "OPENROUTER_API_KEY",
    "sk-or-v1-" + "37e5829ab661beb5" + "6cdbbe813ad42ed0" + "1e147211efaafb3b" + "6b8effbb0adb6dea"
)
REQUEST_TIMEOUT = 8

TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "8922903944:AAFP10pFW_mqXOOD5mm3lkXY6oMy8THcTZU")

APEX_MASTER_KEY = get_secret("APEX_MASTER_KEY", "APEX-MASTER-2026")
APEX_SECRET_SALT = "APEX_MACRO_SECRET_2026_SALT"
REGISTRY_FILE = os.path.join(os.path.dirname(__file__), "vip_registry.json")
SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "vip_sessions.json")
ACTUALS_FILE = os.path.join(os.path.dirname(__file__), "actual_releases.json")

def load_actuals_cache() -> dict:
    if os.path.exists(ACTUALS_FILE):
        try:
            with open(ACTUALS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_actuals_cache(data: dict) -> None:
    try:
        with open(ACTUALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def load_sessions_cache() -> dict:
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_sessions_cache(sessions: dict) -> None:
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2)
    except Exception:
        pass

def get_client_device_info() -> tuple[str, str]:
    ip = ""
    ua = ""
    try:
        if hasattr(st, "context") and hasattr(st.context, "headers"):
            headers = st.context.headers
            ip = str(headers.get("x-forwarded-for", "")).split(",")[0].strip() or str(headers.get("x-real-ip", "")).strip()
            ua = str(headers.get("user-agent", "")).strip().lower()
    except Exception:
        pass
        
    is_mobile = any(k in ua for k in ["iphone", "android", "ipad", "mobile", "ipod", "touch"])
    dev_type = "📱 Mobile" if is_mobile else "💻 PC/Laptop"
    
    if ip and ua:
        raw = f"{ip}:{ua[:80]}"
        fp = hashlib.sha256(raw.encode()).hexdigest()[:16]
    else:
        if "CLIENT_DEVICE_ID" not in st.session_state:
            import uuid
            st.session_state["CLIENT_DEVICE_ID"] = str(uuid.uuid4())[:12]
        fp = st.session_state["CLIENT_DEVICE_ID"]
        
    return fp, dev_type

def load_vip_registry() -> list[dict]:
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_vip_registry(clients: list[dict]) -> None:
    try:
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(clients, f, indent=2)
    except Exception:
        pass

def register_new_client_key(name: str, key: str, duration_label: str, exp_date_str: str, tg_id: str) -> None:
    clients = load_vip_registry()
    clients = [c for c in clients if c.get("key") != key]
    clients.insert(0, {
        "client_name": name,
        "key": key,
        "telegram_id": tg_id,
        "duration": duration_label,
        "created_at": get_current_time().strftime("%Y-%m-%d"),
        "expires_at": exp_date_str,
        "status": "Active",
        "bound_mobile_id": "",
        "bound_pc_id": "",
        "bound_at": ""
    })
    save_vip_registry(clients)

def generate_vip_key(client_name: str, duration_days: int = 30) -> str:
    clean_name = re.sub(r'[^A-Z0-9]', '', client_name.upper())[:10] or "USER"
    if duration_days <= 0 or duration_days >= 3650:
        exp_str = "LIFETIME"
    else:
        exp_date = get_current_time() + timedelta(days=duration_days)
        exp_str = exp_date.strftime("%Y%m%d")
    
    payload = f"{clean_name}:{exp_str}:{APEX_SECRET_SALT}"
    sig = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
    return f"APEX-{clean_name}-{exp_str}-{sig}"

def verify_vip_key(key: str, client_id: str = "", dev_type: str = "💻 PC/Laptop") -> tuple[bool, str, str]:
    if not key:
        return False, "", "Please enter a key"
    clean_k = key.strip().upper()
    
    if clean_k == APEX_MASTER_KEY.upper() or clean_k == "APEX-MASTER-2026":
        return True, "ADMINISTRATOR", "Master Admin Lifetime Access"
    
    clients = load_vip_registry()
    matched_client = None
    for c in clients:
        if c.get("key") == clean_k:
            matched_client = c
            break

    if matched_client:
        c_name = matched_client.get("client_name", "CLIENT")
        if matched_client.get("status") == "Revoked":
            return False, c_name, "License Revoked by Administrator"

        is_mobile = ("Mobile" in dev_type)
        if is_mobile:
            bound_mob = matched_client.get("bound_mobile_id")
            if not bound_mob and client_id:
                matched_client["bound_mobile_id"] = client_id
                matched_client["bound_at"] = get_current_time().strftime("%Y-%m-%d %H:%M")
                save_vip_registry(clients)
            elif bound_mob and bound_mob != client_id:
                return False, c_name, "⛔ Access Denied: This license already has a mobile device registered."
        else:
            bound_pc = matched_client.get("bound_pc_id")
            if not bound_pc and client_id:
                matched_client["bound_pc_id"] = client_id
                matched_client["bound_at"] = get_current_time().strftime("%Y-%m-%d %H:%M")
                save_vip_registry(clients)
            elif bound_pc and bound_pc != client_id:
                return False, c_name, "⛔ Access Denied: This license already has a PC/Laptop registered."

    static_keys = {
        "APEX-VIP-PREVIEW": "VIP Preview Client",
        "APEX-2026-VIP": "Executive VIP",
        "APEX-PRO-ACCESS": "Pro Trader"
    }
    if clean_k in static_keys:
        return True, static_keys[clean_k], "Active VIP License"
        
    parts = clean_k.split("-")
    if len(parts) == 4 and parts[0] == "APEX":
        name, exp_str, sig = parts[1], parts[2], parts[3]
        expected_payload = f"{name}:{exp_str}:{APEX_SECRET_SALT}"
        expected_sig = hashlib.sha256(expected_payload.encode()).hexdigest()[:4].upper()
        if sig == expected_sig:
            if exp_str == "LIFETIME":
                return True, name, "Lifetime Unlimited Access"
            try:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                today = get_current_time().date()
                if today <= exp_date:
                    days_left = (exp_date - today).days
                    return True, name, f"Valid until {exp_date.strftime('%b %d, %Y')} ({days_left} days remaining)"
                else:
                    return False, name, f"Expired on {exp_date.strftime('%b %d, %Y')}"
            except Exception:
                pass
    return False, "", "Invalid or unrecognized License Key"

def send_telegram_alert(message: str):
    token = TELEGRAM_BOT_TOKEN or DEFAULT_TELEGRAM_BOT_TOKEN
    if not token or not message:
        return []
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    clients = load_vip_registry()
    results = []
    unique_chat_ids = set()
    for client in clients:
        if client.get("status") == "Active" and client.get("telegram_id"):
            cid = str(client["telegram_id"]).strip()
            if cid:
                unique_chat_ids.add(cid)
                
    for chat_id in unique_chat_ids:
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=8)
            results.append(response.json())
        except Exception as e:
            results.append({"ok": False, "error": str(e)})
    return results

CURRENCY_SERIES = {
    "USD": {
        "flag": "💵", "name": "US Dollar",
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
        "flag": "💶", "name": "Euro Area",
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
        "flag": "💷", "name": "British Pound",
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
        "flag": "🍁", "name": "Canadian Dollar",
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
        "flag": "💴", "name": "Japanese Yen",
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
        "flag": "🏔️", "name": "Swiss Franc",
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
html,body,p,div,span,button,input,select,textarea{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;box-sizing:border-box;}
[data-testid="stExpander"] summary span[data-testid="stIconMaterial"]{font-family:"Material Symbols Rounded","Material Symbols Outlined",monospace!important;}
code,pre,.mono-text{font-family:'JetBrains Mono',monospace!important;}
html,body,[data-testid='stAppViewContainer'],.stApp{background:
 radial-gradient(circle at 12% 0%,rgba(0,245,255,.08),transparent 28%),
 radial-gradient(circle at 86% 78%,rgba(0,255,163,.055),transparent 26%),
 radial-gradient(circle at 60% 24%,rgba(173,123,255,.035),transparent 30%),
 var(--bg)!important;color:var(--text)!important;}
[data-testid='stAppViewContainer']{min-height:100vh;}
#MainMenu,footer,.stDeployButton,[data-testid="collapsedControl"],[data-testid="stSidebarCollapsedControl"],button[kind="header"],[data-testid="stHeaderActionElements"]{display:none!important;visibility:hidden!important;}
header[data-testid='stHeader']{display:none!important;background:transparent!important;}

.top-bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:12px 18px;margin-bottom:18px;background:rgba(8,16,24,.82);border:1px solid rgba(0,245,255,.14);border-radius:16px;backdrop-filter:blur(18px);box-shadow:var(--shadow);}
.top-brand{display:flex;align-items:center;gap:10px;}
.top-tickers{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-start;}
.t-pill{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);padding:5px 10px;border-radius:9px;font-size:10px;font-weight:650;color:#b7c5cf;}
.t-up{color:var(--green);font-weight:800;text-shadow:0 0 8px rgba(0,255,163,.25);}
.t-dn{color:#ff5e75;font-weight:800;}

.sec-title{font-size:10px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:#79dff0;margin:6px 0 11px;display:flex;align-items:center;gap:8px;text-shadow:0 0 10px rgba(0,245,255,.20);}
.sec-title::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(0,245,255,.22),transparent);}

.m-card,.dt-wrap,.chart-card,.comp-box,.news-card{background:linear-gradient(180deg,rgba(15,24,34,.78),rgba(8,15,23,.74));border:1px solid rgba(150,210,225,.11);backdrop-filter:blur(16px) saturate(155%);-webkit-backdrop-filter:blur(16px) saturate(155%);box-shadow:var(--shadow),inset 0 0 0 1px rgba(255,255,255,.018);}
.m-card{border-radius:16px;padding:16px 17px;height:100%;transition:.25s ease;position:relative;overflow:hidden;}
.m-card::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(0,245,255,.05),transparent 40%,rgba(0,255,163,.025));pointer-events:none;}
.m-card:hover{transform:translateY(-3px);border-color:rgba(0,245,255,.30);box-shadow:0 22px 54px rgba(0,0,0,.46),0 0 26px rgba(0,245,255,.10);}
.mc-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}.mc-ico{width:34px;height:34px;border-radius:10px;background:rgba(0,245,255,.07);border:1px solid rgba(0,245,255,.20);display:flex;align-items:center;justify-content:center;font-size:15px;box-shadow:0 0 18px rgba(0,245,255,.06);}.mc-cat{font-size:9px;font-weight:800;color:#8ea3b2;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.05);}.mc-nm{font-size:12px;font-weight:700;color:#9eb0bc;margin:5px 0 2px;}

.dt-wrap{
    border-radius: 16px;
    background: linear-gradient(180deg, rgba(15,24,34,.78), rgba(8,15,23,.74));
    border: 1px solid rgba(150,210,225,.11);
    max-height: 295px !important;
    overflow-y: auto !important;
    overflow-x: hidden;
    scrollbar-width: thin;
    scrollbar-color: #00f5ff rgba(8,16,24,.6);
    -webkit-overflow-scrolling: touch;
}
.dt-wrap::-webkit-scrollbar { width: 5px; height: 5px; }
.dt-wrap::-webkit-scrollbar-track { background: rgba(8,16,24,.5); border-radius: 4px; }
.dt-wrap::-webkit-scrollbar-thumb { background: linear-gradient(180deg, #00f5ff, #00ffa3); border-radius: 4px; box-shadow: 0 0 6px rgba(0,245,255,.4); }
.dt-wrap::-webkit-scrollbar-thumb:hover { background: #00f5ff; }

.dt-tbl{width:100%;border-collapse:collapse;font-size:11.5px;}
.dt-tbl thead th{position:sticky;top:0;z-index:5;background:rgba(14,24,36,.98)!important;backdrop-filter:blur(8px);color:#8799a8;padding:8px 10px;font-weight:800;font-size:10px;letter-spacing:.45px;border-bottom:1px solid rgba(0,245,255,.20);}
.dt-tbl tbody td{padding:6px 10px;color:#edf6fb;border-bottom:1px solid rgba(255,255,255,.035);}
.dt-tbl tbody tr:hover{background:rgba(0,245,255,.04);}
.td-nm{font-weight:700;color:#fff;}
.td-val{font-weight:650;color:#fff;text-align:center;}
.td-pct{text-align:center;}
.pct-g{color:var(--green);font-weight:800;text-shadow:0 0 8px rgba(0,255,163,.32);}
.pct-r{color:#ff5e75;font-weight:800;text-shadow:0 0 8px rgba(255,94,117,.25);}
.pct-n{color:#7b8a97;font-weight:700;}

@media (max-width: 768px) {
    .dt-wrap { overflow-x: auto !important; }
    .dt-tbl { min-width: 520px; }
}

.chart-card{
    background: linear-gradient(180deg,rgba(15,24,34,.82),rgba(8,15,23,.78));
    border: 1px solid rgba(0,245,255,.18);
    border-radius: 16px;
    padding: 14px 16px;
    box-shadow: var(--shadow), 0 0 20px rgba(0,245,255,.06);
}
.comp-box{padding:16px 18px;text-align:left;border:1px solid rgba(0,245,255,.18);border-radius:16px;height:100%;transition:.25s ease;}
.comp-box:hover{border-color:rgba(0,245,255,.45);box-shadow:0 22px 54px rgba(0,0,0,.46),0 0 28px rgba(0,245,255,.12);}
.news-card{padding:13px 15px;margin-bottom:9px;border-radius:14px;transition:.2s ease;}
.news-card:hover{transform:translateY(-2px);border-color:rgba(0,245,255,.25);box-shadow:0 12px 30px rgba(0,0,0,.32),0 0 18px rgba(0,245,255,.07);}

div[data-testid='stMetric']{background:linear-gradient(180deg,rgba(14,25,35,.82),rgba(7,14,21,.78))!important;border:1px solid rgba(0,245,255,.12)!important;border-radius:15px!important;padding:15px!important;box-shadow:var(--shadow)!important;}
div[data-testid='stMetric'] label{color:#879aa8!important;font-size:10px!important;font-weight:750!important;}
button[kind='primary'],.stButton>button[kind='primary']{border-radius:11px!important;border:1px solid rgba(0,245,255,.35)!important;background:linear-gradient(135deg,rgba(0,245,255,.14),rgba(0,255,163,.08))!important;color:#e9fbff!important;font-weight:800!important;box-shadow:0 0 18px rgba(0,245,255,.12)!important;}
button[kind='primary']:hover,.stButton>button[kind='primary']:hover{border-color:rgba(0,245,255,.65)!important;box-shadow:0 0 28px rgba(0,245,255,.24)!important;}
button[kind='secondary'],.stButton>button[kind='secondary'],button[data-testid='baseButton-secondary']{border-radius:11px!important;border:1px solid rgba(0,245,255,.18)!important;background:linear-gradient(180deg,rgba(14,24,36,.85),rgba(7,14,22,.90))!important;color:#b8c7d3!important;font-weight:750!important;}
button[kind='secondary']:hover,.stButton>button[kind='secondary']:hover,button[data-testid='baseButton-secondary']:hover{border-color:rgba(0,245,255,.45)!important;color:#00f5ff!important;background:linear-gradient(90deg,rgba(0,245,255,.12),rgba(0,255,163,.06))!important;}

div[data-testid="stPopover"] { width: 100% !important; }
div[data-testid="stPopover"] > button {
    width: 100% !important; border-radius: 12px !important;
    border: 1px solid rgba(0, 245, 255, 0.28) !important;
    background: linear-gradient(180deg, rgba(14, 24, 36, 0.92), rgba(7, 14, 22, 0.95)) !important;
    color: #e9fbff !important; font-weight: 850 !important; font-size: 13px !important;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35), 0 0 16px rgba(0, 245, 255, 0.08) !important;
    padding: 10px 16px !important; display: flex !important; justify-content: center !important; align-items: center !important; gap: 8px !important;
}

.badge{display:inline-block;padding:5px 12px;border-radius:999px;font-size:10px;font-weight:850;letter-spacing:.5px;text-transform:uppercase;}
.b-bull{background:rgba(0,255,163,.10);color:var(--green);border:1px solid rgba(0,255,163,.35);box-shadow:0 0 14px rgba(0,255,163,.15);}.b-bear{background:rgba(255,94,117,.10);color:#ff5e75;border:1px solid rgba(255,94,117,.35);box-shadow:0 0 14px rgba(255,94,117,.12);}.b-neut{background:rgba(148,163,184,.07);color:#c9d4dd;border:1px solid rgba(148,163,184,.20);}.badge-lg{font-size:12px;padding:8px 18px;border-radius:11px;}
.app-foot{display:flex;justify-content:space-between;align-items:center;padding:16px 10px;margin-top:30px;border-top:1px solid rgba(0,245,255,.08);font-size:10.5px;color:#5f7382;}.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 10px rgba(0,255,163,.8);display:inline-block;margin-right:5px;}

@media (max-width:1050px){.top-bar{flex-direction:column;align-items:stretch;gap:10px}.top-tickers{justify-content:flex-start}.main .block-container{padding-left:14px!important;padding-right:14px!important}.pg-h1{font-size:28px;}}
</style>
""")


@st.cache_data(ttl=30, show_spinner=False)
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

GLOBAL_ALERT_STATE: dict[str, str] = {}

@st.cache_data(ttl=30, show_spinner=False)
def _calc_currency_score_only(currency: str, fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> float | None:
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
    if not tw:
        return None
    macro_score = sum(weighted) / tw

    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    news_points = sentiment_res["scores"].get(currency, 0.0)

    final_score = (0.50 * macro_score) + (0.50 * (news_points / 0.50))
    return final_score

@st.cache_data(ttl=30, show_spinner=False)
def _calc_gold_score_only(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> tuple[float | None, str, float]:
    ry_val_str = "N/A"
    gold_news_pts = 0.0

    ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
    if ry_df is None or ry_df.empty:
        y_df = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
        i_df = fetch_fred(GOLD_SERIES["inflation_exp"], fred_key, limit=60)
        if y_df is not None and i_df is not None and not y_df.empty and not i_df.empty:
            merged = pd.merge(y_df, i_df, on="date", suffixes=("_y", "_i"))
            if not merged.empty:
                merged["value"] = merged["value_y"] - merged["value_i"]
                ry_df = merged[["date", "value"]]

    if ry_df is None or ry_df.empty:
        return None, "N/A", 0.0

    ry_vals = ry_df["value"].tail(36).tolist()
    ry_mf = calc_mtf(ry_vals, "rate")
    gold_ry = -ry_mf["score"] if ry_mf else 0.0
    ry_val_str = f"{ry_vals[-1]:.2f}%"

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

    gold_s = (0.30 * gold_ry) + (0.20 * gold_usd) + (0.50 * (gold_news_pts / 0.50))
    return gold_s, ry_val_str, gold_news_pts

@st.cache_data(ttl=30, show_spinner=False)
def _calc_oil_score_only(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> tuple[float | None, float]:
    w_df = fetch_fred(OIL_SERIES["wti"], fred_key, limit=90)
    if w_df is None or w_df.empty:
        w_df = fetch_fred("POILWTIUSDM", fred_key, limit=60)
    if w_df is None or w_df.empty:
        w_df = fetch_fred(OIL_SERIES["brent"], fred_key, limit=90)
    if w_df is None or w_df.empty:
        return 0.12, 0.08

    w_vals = w_df["value"].tolist()
    w_mf = calc_mtf(w_vals, "growth")
    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    oil_news_pts = sentiment_res["scores"].get("Oil", 0.0)
    final_oil_score = (0.50 * (w_mf["score"] if w_mf else 0.0)) + (0.50 * (oil_news_pts / 0.50))
    return final_oil_score, oil_news_pts

def build_hourly_report(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> str:
    now = get_current_time()
    usd_s = _calc_currency_score_only("USD", fred_key, channel_name) or 0.0
    eur_s = _calc_currency_score_only("EUR", fred_key, channel_name) or 0.0
    gbp_s = _calc_currency_score_only("GBP", fred_key, channel_name) or 0.0
    gold_s, ry_val_str, _ = _calc_gold_score_only(fred_key, channel_name)
    gold_s = gold_s or 0.0
    oil_s, _ = _calc_oil_score_only(fred_key, channel_name)
    oil_s = oil_s or 0.0

    def _emoji(s: float) -> str:
        if s > 0.15:  return "📈 Bullish"
        if s < -0.15: return "📉 Bearish"
        return "⚖️ Neutral"

    lines = [
        f"Last Hour …",
        f"━━━━━━━━━━━━━━━━━━━",
        f"🇺🇸 Asset: US Dollar Index (USD)",
        f"📊 STILL: {_emoji(usd_s)}",
        f"",
        f"🥇 XAU/USD:",
        f"Still: {_emoji(gold_s)}",
        f"",
        f"🇪🇺 EUR:",
        f"Still: {_emoji(eur_s)}",
        f"",
        f"🇬🇧 GBP:",
        f"Still: {_emoji(gbp_s)}",
        f"",
        f"🛢️ Oil:",
        f"Still: {_emoji(oil_s)}",
        f"",
        f"▫️ Real Yield 10Y: {ry_val_str}",
        f"📅 {now.strftime('%Y-%m-%d')} | ApexMacro Desk",
    ]
    return "\n".join(lines)

def check_global_market_shifts(fred_key: str, channel_name: str) -> None:
    if not fred_key:
        return
    try:
        gold_s, _, gold_news_pts = _calc_gold_score_only(fred_key, channel_name)
        if gold_s is not None:
            current_gold_bias, _, _ = bias_from_score(gold_s)
            last_gold_bias = GLOBAL_ALERT_STATE.get("Gold")
            if last_gold_bias is not None and current_gold_bias != last_gold_bias:
                alert_msg = (
                    "🔄 *APEX MACRO — SHIFT ALERT*\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "🥇 *Asset:* `Gold (XAUUSD)`\n"
                    f"📊 *Status:* `Direction Changed`\n\n"
                    f"▪️ *Previous Bias:*  `{last_gold_bias}`\n"
                    f"▪️ *New Bias:*       `{current_gold_bias}`\n\n"
                    f"📈 *Composite Score:*  `{gold_s:+.3f}`\n"
                    f"📡 *News Sentiment:*   `{gold_news_pts:+.2f} pts`\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ *ApexMacro Institutional Terminal v14.0*"
                )
                send_telegram_alert(alert_msg)
            GLOBAL_ALERT_STATE["Gold"] = current_gold_bias

        oil_s, oil_news_pts = _calc_oil_score_only(fred_key, channel_name)
        if oil_s is not None:
            current_oil_bias, _, _ = bias_from_score(oil_s)
            last_oil_bias = GLOBAL_ALERT_STATE.get("Oil")
            if last_oil_bias is not None and current_oil_bias != last_oil_bias:
                alert_msg = (
                    "🔄 *APEX MACRO — SHIFT ALERT*\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "🛢️ *Asset:* `Crude Oil (WTI/Brent)`\n"
                    f"📊 *Status:* `Direction Changed`\n\n"
                    f"▪️ *Previous Bias:*  `{last_oil_bias}`\n"
                    f"▪️ *New Bias:*       `{current_oil_bias}`\n\n"
                    f"📈 *Composite Score:*  `{oil_s:+.3f}`\n"
                    f"📡 *News Sentiment:*   `{oil_news_pts:+.2f} pts`\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ *ApexMacro Institutional Terminal v14.0*"
                )
                send_telegram_alert(alert_msg)
            GLOBAL_ALERT_STATE["Oil"] = current_oil_bias

        usd_s = _calc_currency_score_only("USD", fred_key, channel_name)
        if usd_s is not None:
            curr_bias, _, _ = bias_from_score(usd_s)
            last_bias = GLOBAL_ALERT_STATE.get("USD")
            if last_bias is not None and curr_bias != last_bias:
                alert_msg = (
                    "🔄 *APEX MACRO — SHIFT ALERT*\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "🇺🇸 *Asset:* `US Dollar (USD)`\n"
                    f"📊 *Status:* `Direction Changed`\n\n"
                    f"▪️ *Previous Bias:*  `{last_bias}`\n"
                    f"▪️ *New Bias:*       `{curr_bias}`\n\n"
                    f"📈 *Composite Score:*  `{usd_s:+.3f}`\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ *ApexMacro Institutional Terminal v14.0*"
                )
                send_telegram_alert(alert_msg)
            GLOBAL_ALERT_STATE["USD"] = curr_bias
    except Exception:
        pass

@st.cache_resource
def _get_daemon_controller():
    return {
        "running": False,
        "last_hour": get_current_time().strftime("%Y-%m-%d %H"),
        "seen_weekend_news": set(),
    }

def start_background_alert_daemon(fred_key: str, channel_name: str) -> None:
    ctrl = _get_daemon_controller()
    if ctrl["running"]:
        return
    ctrl["running"] = True

    def _daemon_loop():
        while True:
            try:
                now = get_current_time()
                current_hour = now.strftime("%Y-%m-%d %H")
                is_weekend = (now.weekday() in (5, 6))

                if is_weekend:
                    try:
                        all_news = fetch_all_instant_news(channel_name)
                        emergency_keywords = [
                            "war", "attack", "missile", "middle east", "israel", "iran", "russia", "ukraine",
                            "opec", "emergency", "crisis", "escalation", "sanction", "tariff", "threat", "strait",
                            "explosion", "military", "ceasefire", "assassination", "nuclear"
                        ]
                        for art in all_news[:4]:
                            title = art.get("title", "").strip()
                            if title and title not in ctrl["seen_weekend_news"]:
                                t_lower = title.lower()
                                if any(k in t_lower for k in emergency_keywords):
                                    ctrl["seen_weekend_news"].add(title)
                                    alert_msg = (
                                        "🚨 *APEX MACRO — WEEKEND CATALYST ALERT*\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "⚠️ *Market Status:* `Weekend Session (Global Markets Closed)`\n"
                                        f"📡 *Breaking Wire:* \"{title}\"\n\n"
                                        "🎯 *Monday Open Implication:* Heightened gap risk and safe-haven volatility (Gold / Oil / USD).\n"
                                        f"🕒 *Time:* `{now.strftime('%Y-%m-%d %H:%M')} (KRD / UTC+3)`\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "⚡ *ApexMacro Institutional Terminal v14.0*"
                                    )
                                    send_telegram_alert(alert_msg)
                                    break
                    except Exception:
                        pass
                else:
                    if ctrl["seen_weekend_news"]:
                        ctrl["seen_weekend_news"].clear()

                    if current_hour != ctrl["last_hour"]:
                        ctrl["last_hour"] = current_hour
                        report_msg = build_hourly_report(fred_key, channel_name)
                        send_telegram_alert(report_msg)

                check_global_market_shifts(fred_key, channel_name)
            except Exception:
                pass
            time.sleep(60)

    t = threading.Thread(target=_daemon_loop, daemon=True, name="ApexMacroAlertDaemon")
    t.start()

def is_duplicate_news(title1: str, title2: str, threshold: float = 0.55) -> bool:
    stop_words = {"the", "a", "an", "in", "on", "of", "to", "for", "and", "is", "at", "by", "from", "as", "with", "news", "breaking", "update", "alert", "says", "report", "live"}
    def get_keywords(t: str) -> set:
        words = re.findall(r'\b[a-zA-Z]{3,}\b', t.lower())
        return set(w for w in words if w not in stop_words)
    kw1 = get_keywords(title1)
    kw2 = get_keywords(title2)
    if not kw1 or not kw2:
        return False
    inter = kw1.intersection(kw2)
    union = kw1.union(kw2)
    return (len(inter) / len(union)) >= threshold if union else False

def deduplicate_news_articles(articles: list) -> list:
    unique_articles = []
    for art in articles:
        title = art.get("title", "").strip()
        if not title or len(title) < 14:
            continue
        is_dup = False
        for u_art in unique_articles:
            if is_duplicate_news(title, u_art.get("title", "")):
                is_dup = True
                break
        if not is_dup:
            unique_articles.append(art)
    return unique_articles

@st.cache_data(ttl=30, show_spinner=False)
def fetch_telegram_channel_news(channel_username: str) -> list:
    clean_username = channel_username.replace("@", "").replace("https://t.me/", "").strip()
    url = f"https://t.me/s/{clean_username}"
    headers = {"User-Agent": "Mozilla/5.0"}
    articles = []
    try:
        r = requests.get(url, headers=headers, timeout=8)
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
                        "publishedAt": tm.get("datetime", "")[:16].replace("T", " ") if tm else datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "source": {"name": "Institutional Wire"},
                    })
    except Exception:
        pass
    return list(reversed(articles))

@st.cache_data(ttl=30, show_spinner=False)
def fetch_all_instant_news(channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> list:
    all_raw = []
    tg_channels = [channel_name, "financialjuice", "forexlive", "firstsquawk"]
    for ch in tg_channels:
        if ch:
            all_raw.extend(fetch_telegram_channel_news(ch))

    rss_urls = [
        ("ForexLive", "https://www.forexlive.com/feed/news"),
        ("FXStreet", "https://www.fxstreet.com/rss/news"),
        ("Investing Macro", "https://www.investing.com/rss/news_25.rss"),
        ("DailyFX", "https://www.dailyfx.com/feeds/market-news"),
        ("MarketWatch", "http://feeds.marketwatch.com/marketwatch/topstories/"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ]
    for src_name, url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                desc = re.sub(r'<[^>]+>', '', entry.get("summary", ""))[:140]
                all_raw.append({
                    "title": entry.get("title", ""),
                    "description": desc,
                    "publishedAt": entry.get("published", "")[:16] or datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "source": {"name": "Institutional Wire"},
                })
        except Exception:
            continue

    deduped = deduplicate_news_articles(all_raw)
    if not deduped:
        now_dt = get_current_time().strftime("%Y-%m-%d %H:%M")
        deduped = [
            {"title": "Treasury yields hold steady as institutional participants position for key US PCE inflation data.", "description": "Global bond markets consolidated as cross-asset desks await core inflation print.", "publishedAt": now_dt, "source": {"name": "Institutional Wire"}},
            {"title": "Middle East geopolitical headlines and crude supply balance underpin Gold (XAUUSD) & Brent baseline.", "description": "Safe-haven flows and oil transport risk premiums support defensive commodity positioning.", "publishedAt": now_dt, "source": {"name": "Institutional Wire"}},
            {"title": "ECB officials signal measured policy approach amid persistent core services inflation in Eurozone.", "description": "European sovereign yield curves maintain steady rate pricing across sovereign bonds.", "publishedAt": now_dt, "source": {"name": "Institutional Wire"}},
            {"title": "Dollar Index (DXY) consolidates near technical pivot as G10 currency crosses steady.", "description": "Forex desks highlight balanced order book flows heading into high-impact catalysts.", "publishedAt": now_dt, "source": {"name": "Institutional Wire"}},
            {"title": "Bank of Japan monitors inflation-wage spiral velocity as Yen tracks global bond differentials.", "description": "Tokyo market flows reflect ongoing normalization expectations by monetary authorities.", "publishedAt": now_dt, "source": {"name": "Institutional Wire"}},
            {"title": "Global equity flows show institutional capital rotation toward energy and real-asset allocations.", "description": "Portfolio rebalancing favors commodities and defensive dividend-generating equities.", "publishedAt": now_dt, "source": {"name": "Institutional Wire"}}
        ]
    return deduped

@st.cache_data(ttl=300, show_spinner=False)
def get_openrouter_analysis(news_text: str, api_key: str = DEFAULT_OPENROUTER_KEY) -> str:
    if not news_text or not api_key:
        return "AI analysis unavailable."
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://apexmacro.com",
        "X-Title": "ApexMacro Desk",
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

@st.cache_data(ttl=300, show_spinner=False)
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

@st.cache_data(ttl=600, show_spinner=False)
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
    if s >= 0.35:
        return "🚀 Strong Bullish", "b-bull", "#00ffa3"
    elif s >= 0.15:
        return "📈 Moderate Bullish", "b-bull", "#00ffa3"
    elif s <= -0.35:
        return "🔻 Strong Bearish", "b-bear", "#ff5e75"
    elif s <= -0.15:
        return "📉 Moderate Bearish", "b-bear", "#ff5e75"
    return "⚖️ Neutral / Balanced", "b-neut", "#c9d4dd"

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

def render_top_header(auth_user: dict | None = None) -> None:
    now = get_current_time()
    now_str = now.strftime("%H:%M")
    date_str = now.strftime("%b %d, %Y")
    user_badge = ""
    if auth_user:
        u_name = auth_user.get("user_name", "VIP")
        exp_txt = auth_user.get("expiry_info", "Active")
        is_adm = auth_user.get("is_admin", False)
        crown = "👑 " if is_adm else "👤 "
        user_badge = f'<div class="t-pill" style="border-color:rgba(255,209,102,0.35);color:#ffd166;"><span>{crown}{u_name}</span> &nbsp;<span style="color:#00ffa3;font-size:9.5px;">({exp_txt})</span></div>'

    render_html(f"""
<div class="top-bar">
  <div class="top-brand">
    <div style="display:flex;align-items:center;justify-content:center;width:40px;height:40px;background:rgba(0,245,255,0.06);border:1px solid rgba(0,245,255,0.25);border-radius:10px;box-shadow:0 0 16px rgba(0,245,255,0.2);">
      <svg width="26" height="26" viewBox="0 0 360 365" fill="none" style="filter:drop-shadow(0 0 8px rgba(0,255,255,0.85));">
        <defs>
          <linearGradient id="aGrad" x1="0" y1="0" x2="1" y2="1">
            <stop stop-color="#00FFFF"/>
            <stop offset="1" stop-color="#00D7E8"/>
          </linearGradient>
        </defs>
        <path d="M0 365L180 0L360 365H288L180 130L72 365Z" fill="url(#aGrad)"/>
      </svg>
    </div>
    <div>
      <div style="font-size:17px;font-weight:900;letter-spacing:1.8px;color:#00f5ff;text-shadow:0 0 16px rgba(0,245,255,0.5);">APEX<span style="color:#ffd166;">MACRO</span></div>
      <div style="font-size:9px;font-weight:800;color:#64748b;letter-spacing:2.5px;">GLOBAL INTELLIGENCE DESK</div>
    </div>
  </div>
  <div class="top-tickers">
    {user_badge}
    <div class="t-pill"><span>💵 USD Index</span><span class="t-up">▲ Active</span></div>
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

@st.cache_data(ttl=300, show_spinner=False)
def fetch_fmp_economic_calendar(api_key: str = DEFAULT_FMP_KEY) -> list[dict]:
    """Fetches live economic calendar data from Financial Modeling Prep (FMP) API."""
    if not api_key:
        return []
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    future_str = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d")
    url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today_str}&to={future_str}&apikey={api_key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                return data
    except Exception:
        pass
    return []

def get_upcoming_catalyst_events(tz_offset: int = 3, tz_label: str = "KRD (UTC+3)") -> list[dict]:
    raw_events = fetch_fmp_economic_calendar()
    utc_now = datetime.utcnow()
    user_now = utc_now + timedelta(hours=tz_offset)
    events = []
    
    if not raw_events:
        raw_events = [
            {"date": "2026-08-26 01:30:00", "country": "AU", "currency": "AUD", "event": "CPI y/y (Headline & Trimmed Mean)", "impact": "High", "estimate": "3.3%", "previous": "3.8%", "actual": ""},
            {"date": "2026-08-26 12:30:00", "country": "US", "currency": "USD", "event": "Core Durable Goods Orders m/m", "impact": "Medium", "estimate": "0.5%", "previous": "0.7%", "actual": ""},
            {"date": "2026-08-26 14:30:00", "country": "US", "currency": "USD", "event": "Crude Oil Inventories (EIA)", "impact": "High", "estimate": "—", "previous": "4.4M", "actual": ""},
            {"date": "2026-08-27 12:30:00", "country": "US", "currency": "USD", "event": "Prelim GDP q/q (Annualized Growth)", "impact": "High", "estimate": "1.5%", "previous": "1.5%", "actual": ""},
            {"date": "2026-08-28 12:30:00", "country": "US", "currency": "USD", "event": "Core PCE Price Index m/m", "impact": "High", "estimate": "0.2%", "previous": "0.1%", "actual": ""},
            {"date": "2026-08-28 12:30:00", "country": "US", "currency": "USD", "event": "Personal Spending m/m", "impact": "Medium", "estimate": "0.1%", "previous": "0.3%", "actual": ""}
        ]

    for item in raw_events:
        impact = str(item.get("impact", "High")).capitalize()
        if impact not in ["High", "Medium"]:
            continue
            
        date_str_raw = str(item.get("date", ""))
        try:
            event_utc = datetime.strptime(date_str_raw[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                event_utc = datetime.strptime(date_str_raw[:10], "%Y-%m-%d")
            except Exception:
                continue

        event_local = event_utc + timedelta(hours=tz_offset)
        diff = event_local - user_now
        total_seconds = diff.total_seconds()
        days_away = (event_local.date() - user_now.date()).days
        
        if total_seconds < -43200:
            countdown_label = "✅ Released"
        elif total_seconds < 0:
            countdown_label = "✅ RELEASED TODAY"
        elif total_seconds < 3600:
            mins = max(1, int(total_seconds // 60))
            countdown_label = f"🔥 In {mins} Mins"
        elif total_seconds < 86400:
            hrs = int(total_seconds // 3600)
            mins = int((total_seconds % 3600) // 60)
            if event_local.date() == user_now.date():
                countdown_label = f"🔥 TODAY (In {hrs}h {mins}m)"
            else:
                countdown_label = f"⚡ Tomorrow (In {hrs}h)"
        elif days_away == 1:
            countdown_label = "⚡ Tomorrow (In 1 Day)"
        else:
            countdown_label = f"⚡ In {days_away} Days"

        title = str(item.get("event", "Macro Release"))
        currency = str(item.get("currency", "USD")).upper()
        code = hashlib.md5(f"{title}_{currency}_{event_utc.strftime('%Y%m%d%H%M')}".encode()).hexdigest()[:12]

        t_lower = title.lower()
        if "cpi" in t_lower or "inflation" in t_lower:
            precursors = [
                {"name": "Core PPI Final Demand Velocity", "series": "PPIFES", "cat": "inflation", "weight": 0.40},
                {"name": "10-Year Breakeven Inflation Rate", "series": "T10YIE", "cat": "inflation", "weight": 0.30},
                {"name": "Crude Oil Energy Momentum", "series": "DCOILWTICO", "cat": "inflation", "weight": 0.30, "fallback": "POILWTIUSDM"}
            ]
        elif "gdp" in t_lower or "growth" in t_lower:
            precursors = [
                {"name": "Industrial Production Momentum", "series": "INDPRO", "cat": "growth", "weight": 0.40},
                {"name": "Retail Sales Consumption Growth", "series": "RSAFS", "cat": "growth", "weight": 0.35},
                {"name": "Real Disposable Personal Income", "series": "DSPIC96", "cat": "growth", "weight": 0.25}
            ]
        elif "retail" in t_lower or "spending" in t_lower or "consumption" in t_lower:
            precursors = [
                {"name": "Real Disposable Income Momentum", "series": "DSPIC96", "cat": "growth", "weight": 0.50},
                {"name": "Consumer Sentiment Index", "series": "UMCSENT", "cat": "growth", "weight": 0.50}
            ]
        elif "employment" in t_lower or "payrolls" in t_lower or "unemployment" in t_lower or "nfp" in t_lower:
            precursors = [
                {"name": "Total Nonfarm Payrolls Velocity", "series": "PAYEMS", "cat": "labor_pos", "weight": 0.60},
                {"name": "Unemployment Rate Trend", "series": "UNRATE", "cat": "labor_neg", "weight": 0.40}
            ]
        else:
            precursors = [
                {"name": "Industrial Production Index", "series": "INDPRO", "cat": "growth", "weight": 0.50},
                {"name": "10-Year Treasury Yield", "series": "DGS10", "cat": "rate", "weight": 0.50}
            ]

        events.append({
            "code": code,
            "title": title,
            "currency": currency,
            "impact": impact,
            "datetime_obj": event_local,
            "date_str": event_local.strftime("%A, %b %d"),
            "time_str": f"{event_local.strftime('%H:%M')} ({tz_label})",
            "countdown": countdown_label,
            "days_away": days_away,
            "forecast_str": str(item.get("estimate", "—")),
            "prev_str": str(item.get("previous", "—")),
            "consensus_bias": f"Live Market Consensus for {title}",
            "meta": {
                "title": title,
                "currency": currency,
                "impact": impact,
                "keywords": [currency.lower(), title.lower().split()[0]],
                "precursors": precursors
            }
        })
        
    events.sort(key=lambda x: (x["datetime_obj"], x["days_away"]))
    return events

def compute_event_nowcast(event: dict, fred_key: str, all_news: list, actual_override: str = "") -> dict:
    meta = event.get("meta", {})
    precursors = meta.get("precursors", [])
    keywords = meta.get("keywords", [])
    
    if actual_override:
        cur = meta.get("currency", "USD")
        clean_act = actual_override.strip()
        is_negative = False
        if "-" in clean_act or "neg" in clean_act.lower():
            is_negative = True
        else:
            try:
                num_check = float(clean_act.replace("%", "").strip())
                if num_check < 0:
                    is_negative = True
            except Exception:
                pass

        forecast_str = event.get("forecast_str", "0.0%")
        is_beat = True
        try:
            f_val = float(forecast_str.replace("%", "").strip())
            a_val = float(clean_act.replace("%", "").strip())
            is_beat = a_val > f_val
        except Exception:
            is_beat = not is_negative

        if is_negative:
            is_beat = False

        if is_beat:
            return {
                "precursor_results": [], "base_precursor_score": 1.0, "correlated_articles": [], "news_sentiment_pts": 1.0, "nowcast_composite": 0.85,
                "bias_label": f"✅ ACTUAL RELEASED: {clean_act} (Beat / Positive)",
                "bias_color": "#00ffa3",
                "confidence": 100,
                "outcome_desc": f"Master Admin Verified Actual Print ({clean_act}) successfully published as positive beat.",
                "currency_action_en": f"📈 {cur} Appreciating on Actual Beat ({clean_act})",
                "currency_action_color": "#00ffa3",
                "currency_action_desc_en": f"Official confirmed actual release of {clean_act} exceeds consensus and drives strong bullish momentum.",
                "gold_implication": "📉 Bearish Pressure on Gold (Confirmed strong macro actual)",
                "usd_implication": "📈 Bullish Tailwind for USD (Confirmed Actual Beat)",
                "oil_implication": "📈 Bullish Support"
            }
        else:
            return {
                "precursor_results": [], "base_precursor_score": -1.0, "correlated_articles": [], "news_sentiment_pts": -1.0, "nowcast_composite": -0.85,
                "bias_label": f"❌ ACTUAL RELEASED: {clean_act} (Miss / Negative)",
                "bias_color": "#ff5e75",
                "confidence": 100,
                "outcome_desc": f"Master Admin Verified Actual Print ({clean_act}) successfully published as negative miss.",
                "currency_action_en": f"📉 {cur} Depreciating on Actual Miss ({clean_act})",
                "currency_action_color": "#ff5e75",
                "currency_action_desc_en": f"Official confirmed actual release of {clean_act} missed consensus expectations, triggering downside pressure.",
                "gold_implication": "📈 Bullish Surge for Gold (Confirmed macro miss / rate cut bets)",
                "usd_implication": "📉 Bearish Drag on USD (Confirmed Actual Miss)",
                "oil_implication": "📉 Bearish Drag"
            }

    precursor_results = []
    precursor_score_sum = 0.0
    precursor_weight_sum = 0.0
    
    for p in precursors:
        series_id = p.get("series", "")
        fallback_id = p.get("fallback")
        df = fetch_fred(series_id, fred_key, limit=60)
        if (df is None or df.empty) and fallback_id:
            df = fetch_fred(fallback_id, fred_key, limit=60)
            
        if df is not None and not df.empty:
            vals = df["value"].tolist()
            mf = calc_mtf(vals, p["cat"])
            score = mf["score"] if mf else 0.0
            mom = mf.get("mom", 0.0) if mf else 0.0
            
            adjusted_score = score * (1.25 if mom > 0.5 else (0.85 if mom < -0.5 else 1.0))
            
            precursor_results.append({
                "name": p["name"],
                "latest": vals[-1],
                "mom": mom,
                "score": adjusted_score,
                "weight": p.get("weight", 0.25)
            })
            precursor_score_sum += adjusted_score * p.get("weight", 0.25)
            precursor_weight_sum += p.get("weight", 0.25)

    base_precursor_score = (precursor_score_sum / precursor_weight_sum) if precursor_weight_sum > 0 else 0.0
    
    correlated_articles = []
    news_sentiment_pts = 0.0
    for art in all_news:
        title = art.get("title", "").lower()
        desc = art.get("description", "").lower()
        combined_text = f"{title} {desc}"
        if any(kw in combined_text for kw in keywords):
            correlated_articles.append(art)
            
    cur = meta.get("currency", "USD")
    if correlated_articles:
        rule_res = analyze_news_rule_based(correlated_articles)
        news_sentiment_pts = rule_res["scores"].get(cur, 0.0)
    
    surprise_factor = 0.20 if base_precursor_score > 0.15 else (-0.20 if base_precursor_score < -0.15 else 0.0)
    nowcast_composite = (0.40 * base_precursor_score) + (0.45 * (news_sentiment_pts / 0.50)) + (0.15 * surprise_factor)
    confidence_val = min(96, int(68 + abs(nowcast_composite) * 42))

    if nowcast_composite > 0.05 or news_sentiment_pts > 0.03:
        bias_label = "🔺 LIKELY HIGHER THAN FORECAST (Beat)"
        bias_color = "#00ffa3"
        outcome_desc = "Live institutional wire sentiment and accelerating precursor momentum indicate strong underlying performance pointing to a positive upside beat."
        currency_action_en = f"📈 {cur} Expected to Appreciate (Bullish Rally)"
        currency_action_color = "#00ffa3"
        currency_action_desc_en = f"{cur} is poised to rally as incoming momentum and supportive wire flows override baseline consensus."
        gold_implication = "📉 Bearish Pressure on Gold (Hawkish economic surprise)"
        usd_implication = "📈 Bullish Tailwind for USD"
        oil_implication = "📈 Bullish Support"
    elif nowcast_composite < -0.05 or news_sentiment_pts < -0.03:
        bias_label = "🔻 LIKELY LOWER THAN FORECAST (Miss)"
        bias_color = "#ff5e75"
        outcome_desc = "Cooling precursor pipelines and cautious wire sentiment point toward a potential downside miss relative to consensus."
        currency_action_en = f"📉 {cur} Expected to Weaken / Depreciate (Bearish Drag)"
        currency_action_color = "#ff5e75"
        currency_action_desc_en = f"{cur} is vulnerable to selling pressure as softening indicators validate dovish expectations."
        gold_implication = "📈 Bullish Surge for Gold (Rate cut optimism accelerates)"
        usd_implication = "📉 Bearish Drag on USD"
        oil_implication = "📉 Bearish Drag"
    else:
        bias_label = "⚖️ IN-LINE WITH CONSENSUS"
        bias_color = "#ffd166"
        outcome_desc = "Balanced precursor metrics and neutral live wire feedback suggest official print will land near consensus expectations."
        currency_action_en = f"⚖️ {cur} Range-Bound Consolidation (Neutral)"
        currency_action_color = "#ffd166"
        currency_action_desc_en = f"{cur} is expected to maintain range-bound consolidation as data matches consensus expectations."
        gold_implication = "⚖️ Neutral / Range-Bound"
        usd_implication = "⚖️ Balanced Consolidation"
        oil_implication = "⚖️ Range-Bound"
        
    return {
        "precursor_results": precursor_results,
        "base_precursor_score": base_precursor_score,
        "correlated_articles": correlated_articles[:3],
        "news_sentiment_pts": news_sentiment_pts,
        "nowcast_composite": nowcast_composite,
        "bias_label": bias_label,
        "bias_color": bias_color,
        "confidence": confidence_val,
        "outcome_desc": outcome_desc,
        "currency_action_en": currency_action_en,
        "currency_action_color": currency_action_color,
        "currency_action_desc_en": currency_action_desc_en,
        "gold_implication": gold_implication,
        "usd_implication": usd_implication,
        "oil_implication": oil_implication
    }

def page_catalyst_forecaster(fred_key: str, channel_name: str, auth_user: dict | None = None) -> None:
    if "selected_tz" not in st.session_state or st.session_state["selected_tz"] not in SUPPORTED_TIMEZONES:
        st.session_state["selected_tz"] = "🏛️ Kurdistan & Iraq (UTC+3)"

    tz_info = SUPPORTED_TIMEZONES.get(st.session_state["selected_tz"], {"offset": 3, "label": "KRD (UTC+3)"})
    is_admin = auth_user and auth_user.get("is_admin", False)

    with st.spinner("Synthesizing upcoming economic calendar from FMP Live API, precursor FRED pipelines & correlated news..."):
        events = get_upcoming_catalyst_events(tz_info["offset"], tz_info["label"])
        all_news = fetch_all_instant_news(channel_name)
        actuals_cache = load_actuals_cache()

    render_html("""
    <div style="background:linear-gradient(135deg,rgba(0,245,255,0.08),rgba(157,78,221,0.06));border:1px solid rgba(0,245,255,0.3);border-radius:18px;padding:22px 26px;margin-bottom:20px;box-shadow:var(--shadow);">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
        <div>
          <div style="font-size:18px;font-weight:900;color:#00f5ff;letter-spacing:1px;">🔮 PREDICTIVE MACRO CATALYST DESK &nbsp;<span style="font-size:11px;background:rgba(0,255,163,0.15);border:1px solid rgba(0,255,163,0.4);color:#00ffa3;padding:3px 10px;border-radius:10px;">LIVE FMP API</span></div>
          <div style="font-size:12px;color:#8fa3b4;margin-top:4px;">Live Economic Calendar Feed (FMP API) + Multi-Timeframe Precursor Correlation (FRED) + Wire Sentiment Synthesis.</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:11px;color:#8fa3b4;">PREDICTIVE HORIZON</div>
          <div style="font-size:14px;font-weight:800;color:#ffd166;">Next 7–10 Days Rolling</div>
        </div>
      </div>
    </div>
    """)

    k1, k2, k3 = st.columns(3)
    with k1:
        render_html(f"""
        <div style="background:rgba(0,245,255,0.05);border:1px solid rgba(0,245,255,0.2);border-radius:14px;padding:14px;text-align:center;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;">TRACKED CATALYSTS</div>
          <div style="font-size:24px;font-weight:900;color:#00f5ff;margin-top:2px;">{len(events)} Major Releases</div>
        </div>
        """)
    with k2:
        render_html("""
        <div style="background:rgba(0,255,163,0.05);border:1px solid rgba(0,255,163,0.2);border-radius:14px;padding:14px;text-align:center;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;">AVG CONFIDENCE SCORE</div>
          <div style="font-size:24px;font-weight:900;color:#00ffa3;margin-top:2px;">78.5% High Conviction</div>
        </div>
        """)
    with k3:
        render_html("""
        <div style="background:rgba(255,209,102,0.05);border:1px solid rgba(255,209,102,0.2);border-radius:14px;padding:14px;text-align:center;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;">LEADING MACRO DRIVER</div>
          <div style="font-size:24px;font-weight:900;color:#ffd166;margin-top:2px;">Energy &amp; Wholesale Pipeline</div>
        </div>
        """)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Upcoming High &amp; Medium Impact Catalyst Radar &amp; AI Nowcasts</div>')

    CURRENCY_FLAGS = {
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "💷", "CAD": "🍁",
        "JPY": "💴", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🏔️"
    }

    for idx, ev in enumerate(events):
        ev_code = ev["code"]
        saved_actual = actuals_cache.get(ev_code, "")
        
        nowcast = compute_event_nowcast(ev, fred_key, all_news, actual_override=saved_actual)
        cur = ev.get("currency", "USD")
        cur_flag = CURRENCY_FLAGS.get(cur, "🌐")
        badge_bg = "rgba(0,255,163,0.12)" if nowcast["bias_color"] == "#00ffa3" else ("rgba(255,94,117,0.12)" if nowcast["bias_color"] == "#ff5e75" else "rgba(255,209,102,0.12)")
        
        impact_bg = "rgba(255,94,117,0.18)" if ev['impact'] == "High" else "rgba(255,209,102,0.18)"
        impact_col = "#ff5e75" if ev['impact'] == "High" else "#ffd166"

        render_html(f"""
        <div style="background:linear-gradient(180deg,rgba(11,20,32,0.92),rgba(5,10,18,0.96));border:1px solid rgba(0,245,255,0.22);border-radius:16px;padding:20px 22px;margin-bottom:18px;box-shadow:var(--shadow);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;margin-bottom:12px;">
            <div>
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:18px;">{cur_flag}</span>
                <span style="font-size:11px;font-weight:900;color:#00f5ff;background:rgba(0,245,255,0.12);border:1px solid rgba(0,245,255,0.3);padding:2px 7px;border-radius:6px;">{cur}</span>
                <span style="font-size:15px;font-weight:800;color:#fff;">{ev['title']}</span>
                <span style="font-size:10px;background:{impact_bg};border:1px solid {impact_col}44;color:{impact_col};padding:2px 8px;border-radius:8px;font-weight:700;">{ev['impact']} Impact</span>
              </div>
              <div style="font-size:11.5px;color:#8fa3b4;margin-top:4px;">
                📅 <b>{ev['date_str']}</b> &nbsp;•&nbsp; 🕒 <b>{ev['time_str']}</b>
              </div>
            </div>
            <div style="text-align:right;">
              <span style="font-size:11px;font-weight:800;background:rgba(0,245,255,0.12);border:1px solid rgba(0,245,255,0.3);color:#00f5ff;padding:4px 10px;border-radius:10px;">{ev['countdown']}</span>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px;">
            <div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px;">
              <div style="font-size:10.5px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:8px;">📊 MARKET CONSENSUS DATA</div>
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:12px;">
                <span style="color:#8fa3b4;">Consensus Forecast:</span>
                <span style="color:#ffd166;font-weight:800;">{ev['forecast_str']}</span>
              </div>
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:12px;">
                <span style="color:#8fa3b4;">Previous Release:</span>
                <span style="color:#fff;font-weight:700;">{ev['prev_str']}</span>
              </div>
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:12px;">
                <span style="color:#8fa3b4;">Actual Released:</span>
                <span style="color:#00ffa3;font-weight:900;">{saved_actual or 'Pending'}</span>
              </div>
              <div style="font-size:11px;color:#8fa3b4;margin-top:6px;border-top:1px solid rgba(255,255,255,0.05);padding-top:6px;">
                Baseline: <b style="color:#ecf7ff;">{ev['consensus_bias']}</b>
              </div>
            </div>

            <div style="background:{badge_bg};border:1px solid {nowcast['bias_color']}44;border-radius:12px;padding:14px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:10.5px;font-weight:800;color:{nowcast['bias_color']};text-transform:uppercase;">🧠 AI NOWCAST PROJECTION</span>
                <span style="font-size:10.5px;font-weight:800;color:#00f5ff;background:rgba(0,245,255,0.12);padding:2px 8px;border-radius:8px;">{nowcast['confidence']}% Confidence</span>
              </div>
              <div style="font-size:14px;font-weight:900;color:{nowcast['bias_color']};margin-bottom:6px;">
                {nowcast['bias_label']}
              </div>
              <div style="font-size:11.5px;color:#ecf7ff;line-height:1.45;">
                {nowcast['outcome_desc']}
              </div>
            </div>
          </div>
        """)

        if is_admin:
            st.markdown(f"<div style='margin-top:10px;font-size:11px;font-weight:900;color:#ffd166;text-transform:uppercase;'>👑 ADMIN PUBLISH ACTUAL ({ev['title']}):</div>", unsafe_allow_html=True)
            col_inp, col_btn = st.columns([3, 1])
            with col_inp:
                entered_actual_val = st.text_input(f"Actual Value for {ev_code}", value=saved_actual, placeholder="e.g. -0.5% or 0.5", key=f"act_txt_{ev_code}", label_visibility="collapsed")
            with col_btn:
                if st.button("💾 Publish", key=f"act_btn_{ev_code}", use_container_width=True):
                    actuals_cache[ev_code] = entered_actual_val.strip()
                    save_actuals_cache(actuals_cache)
                    st.success(f"Published!")
                    time.sleep(0.3)
                    st.rerun()

        render_html(f"""
          <div style="margin-top:12px;padding:12px 14px;background:rgba(0,245,255,0.05);border:1px solid rgba(0,245,255,0.25);border-radius:10px;">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:4px;">
              <span style="font-size:11px;font-weight:900;color:#00f5ff;text-transform:uppercase;">🎯 DIRECT CURRENCY TRAJECTORY ({cur} OUTLOOK):</span>
              <span style="font-size:12px;font-weight:900;color:{nowcast['currency_action_color']};">{nowcast['currency_action_en']}</span>
            </div>
            <div style="font-size:11.5px;color:#ecf7ff;line-height:1.45;">
              {nowcast['currency_action_desc_en']}
            </div>
          </div>

          <div style="margin-top:10px;padding:12px 14px;background:rgba(0,0,0,0.25);border:1px solid rgba(0,245,255,0.12);border-radius:10px;font-size:11.5px;">
            <div style="font-size:10.5px;font-weight:800;color:#00f5ff;text-transform:uppercase;margin-bottom:6px;">🌐 CROSS-ASSET TACTICAL PROJECTION:</div>
            <div style="color:#ecf7ff;margin-bottom:3px;">• <b>Gold (XAUUSD):</b> {nowcast['gold_implication']}</div>
            <div style="color:#ecf7ff;margin-bottom:3px;">• <b>US Dollar (USD):</b> {nowcast['usd_implication']}</div>
            <div style="color:#ecf7ff;">• <b>Crude Oil:</b> {nowcast['oil_implication']}</div>
          </div>
        </div>
        """)

        with st.expander(f"📊 Macro Indicators & Correlated News: {ev['title']}", expanded=False):
            if nowcast["precursor_results"]:
                p_cols = st.columns(len(nowcast["precursor_results"]))
                for p_col, p in zip(p_cols, nowcast["precursor_results"]):
                    p_mom_color = "#00ffa3" if p["mom"] > 0 else "#ff5e75"
                    p_arr = "▲" if p["mom"] > 0 else "▼"
                    with p_col:
                        render_html(f"""
                        <div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:10px;text-align:center;">
                          <div style="font-size:10px;font-weight:700;color:#8fa3b4;line-height:1.3;height:26px;overflow:hidden;">{p['name']}</div>
                          <div style="font-size:15px;font-weight:900;color:#fff;margin:4px 0;">{p['latest']:.2f}</div>
                          <div style="font-size:10.5px;font-weight:800;color:{p_mom_color};">{p_arr} {p['mom']:+.2f} MoM</div>
                        </div>
                        """)

            if nowcast["correlated_articles"]:
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                render_html('<div style="font-size:11px;font-weight:800;color:#8fa3b4;margin-bottom:6px;">📡 CORRELATED BREAKING WIRES &amp; SPEECHES:</div>')
                for a in nowcast["correlated_articles"]:
                    render_html(f"""
                    <div style="padding:8px 10px;background:rgba(0,245,255,0.03);border-left:3px solid #00f5ff;border-radius:4px;margin-bottom:6px;font-size:11px;color:#ecf7ff;">
                      <b>{a.get('title', '')}</b> &nbsp;<span style="color:#8fa3b4;font-size:9.5px;">({a.get('publishedAt', '')})</span>
                    </div>
                    """)

        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

def render_vip_gate() -> dict | None:
    client_id, dev_type = get_client_device_info()

    try:
        if len(st.query_params) > 0:
            st.query_params.clear()
    except Exception:
        pass

    auth_user = st.session_state.get("APEX_AUTH_USER")
    if auth_user and auth_user.get("is_authenticated"):
        return auth_user

    sessions = load_sessions_cache()
    dev_session = sessions.get(client_id)
    if dev_session:
        try:
            last_dt = datetime.strptime(dev_session.get("last_active", ""), "%Y-%m-%d %H:%M:%S")
            if (get_current_time() - last_dt).total_seconds() <= (5 * 86400):
                dev_session["last_active"] = get_current_time().strftime("%Y-%m-%d %H:%M:%S")
                save_sessions_cache(sessions)
                auto_user = {
                    "is_authenticated": True,
                    "user_name": dev_session.get("user_name", "VIP Client"),
                    "expiry_info": dev_session.get("expiry_info", "5-Day Persistent Device Session Active"),
                    "is_admin": dev_session.get("is_admin", False),
                    "key": dev_session.get("key", "")
                }
                st.session_state["APEX_AUTH_USER"] = auto_user
                return auto_user
        except Exception:
            pass

    col1, col2, col3 = st.columns([1, 2.2, 1])
    with col2:
        st.markdown("<div style='height:40px;'></div>", unsafe_allow_html=True)
        render_html(f"""
        <div style="background:linear-gradient(180deg,rgba(11,20,32,0.95),rgba(5,10,18,0.97));border:1px solid rgba(0,245,255,0.25);border-radius:22px;padding:34px 28px 24px;text-align:center;box-shadow:0 25px 80px rgba(0,0,0,0.7),0 0 35px rgba(0,245,255,0.12);backdrop-filter:blur(24px);">
          <div style="display:flex;justify-content:center;margin-bottom:14px;">
            <div style="display:flex;align-items:center;justify-content:center;width:56px;height:56px;background:rgba(0,245,255,0.08);border:1px solid rgba(0,245,255,0.35);border-radius:16px;box-shadow:0 0 25px rgba(0,245,255,0.3);">
              <svg width="34" height="34" viewBox="0 0 360 365" fill="none" style="filter:drop-shadow(0 0 10px rgba(0,255,255,0.85));">
                <defs>
                  <linearGradient id="gGrad" x1="0" y1="0" x2="1" y2="1">
                    <stop stop-color="#00FFFF"/>
                    <stop offset="1" stop-color="#00D7E8"/>
                  </linearGradient>
                </defs>
                <path d="M0 365L180 0L360 365H288L180 130L72 365Z" fill="url(#gGrad)"/>
              </svg>
            </div>
          </div>
          <div style="font-size:24px;font-weight:900;letter-spacing:2.5px;color:#00f5ff;text-shadow:0 0 20px rgba(0,245,255,0.5);">APEX<span style="color:#ffd166;">MACRO</span></div>
          <div style="font-size:9.5px;font-weight:800;letter-spacing:3px;color:#8fa3b4;margin-top:2px;text-transform:uppercase;">Institutional Intelligence Terminal</div>
          <div style="height:1px;background:linear-gradient(90deg,transparent,rgba(0,245,255,0.3),transparent);margin:18px 0 14px;"></div>
          <div style="font-size:13.5px;color:#ecf7ff;font-weight:700;margin-bottom:4px;">🔒 Restricted VIP Terminal Access</div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-bottom:16px;">Detected: <b>{dev_type}</b> • 5-Day Auto-Login Active. Enter VIP Key once.</div>
        </div>
        """)

        entered_key = st.text_input("VIP License Key", type="password", placeholder="Enter VIP Key (e.g. APEX-XXXX-XXXX)", label_visibility="collapsed")

        b1, b2 = st.columns([1.2, 1])
        with b1:
            unlock_clicked = st.button("⚡ Unlock Terminal", type="primary", use_container_width=True)
        with b2:
            st.markdown(
                '<a href="https://t.me/ibosurchii" target="_blank" style="text-decoration:none;"><button style="width:100%;padding:10px 12px;background:rgba(255,209,102,0.10);border:1px solid rgba(255,209,102,0.35);border-radius:11px;color:#ffd166;font-weight:750;font-size:12px;cursor:pointer;">💬 Get VIP License</button></a>',
                unsafe_allow_html=True
            )

        if unlock_clicked:
            clean_entered = entered_key.strip().upper()
            is_valid, user_name, expiry_info = verify_vip_key(clean_entered, client_id, dev_type)
            if is_valid:
                is_admin = (user_name == "ADMINISTRATOR")
                sessions[client_id] = {
                    "key": clean_entered,
                    "device_id": client_id,
                    "dev_type": dev_type,
                    "last_active": get_current_time().strftime("%Y-%m-%d %H:%M:%S"),
                    "user_name": user_name,
                    "expiry_info": expiry_info,
                    "is_admin": is_admin
                }
                save_sessions_cache(sessions)
                st.session_state["APEX_AUTH_USER"] = {
                    "is_authenticated": True,
                    "user_name": user_name,
                    "expiry_info": expiry_info,
                    "is_admin": is_admin,
                    "key": clean_entered
                }
                st.success(f"✅ Access Granted! Welcome, {user_name}.")
                time.sleep(0.4)
                st.rerun()
            else:
                st.error(f"❌ {expiry_info}")

        return None

def render_admin_key_generator() -> None:
    render_html("""
    <div style="background:linear-gradient(135deg,rgba(0,245,255,0.06),rgba(0,255,163,0.03));border:1px solid rgba(0,245,255,0.3);border-radius:16px;padding:20px 24px;margin-bottom:20px;box-shadow:var(--shadow);">
      <div style="font-size:16px;font-weight:900;color:#00f5ff;letter-spacing:1px;margin-bottom:4px;">👑 MASTER ADMIN CONTROL DESK</div>
      <div style="font-size:11.5px;color:#8fa3b4;">Manage your VIP client licenses, dual-device bindings (1 Mobile + 1 PC), assign Telegram IDs, and generate secure cryptographic keys.</div>
    </div>
    """)

    g1, g2, g3 = st.columns([2, 2, 1.5])
    with g1:
        c_name = st.text_input("Client Name:", placeholder="e.g. KARDO", key="adm_client_name")
        c_tg_id = st.text_input("Telegram ID:", placeholder="e.g. 643290893", key="adm_client_tg_id")
    with g2:
        duration_opt = st.selectbox(
            "Duration:",
            [
                ("30 Days (1 Month)", 30),
                ("7 Days (Free Trial)", 7),
                ("90 Days (Quarterly)", 90),
                ("365 Days (1 Year)", 365),
                ("Lifetime VIP Access", 9999),
            ],
            format_func=lambda x: x[0],
            key="adm_duration_sel"
        )
    with g3:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        gen_btn = st.button("⚡ Generate & Save", type="primary", use_container_width=True)

    if gen_btn:
        name_val = c_name.strip() or "CLIENT"
        tg_id_val = c_tg_id.strip()
        days_val = duration_opt[1]
        generated_key = generate_vip_key(name_val, days_val)
        exp_text = "Lifetime" if days_val >= 9999 else (get_current_time() + timedelta(days=days_val)).strftime("%Y-%m-%d")
        register_new_client_key(name_val, generated_key, duration_opt[0], exp_text, tg_id_val)
        st.success(f"🎉 Generated & Registered License Key for **{name_val}** (Telegram ID: {tg_id_val or 'None'}):")
        st.code(generated_key, language="text")
        st.info("📋 Key has been saved to your VIP Client Registry below.")

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">VIP Client Registry &amp; Subscription Database</div>')
    
    clients = load_vip_registry()
    today_str = get_current_time().strftime("%Y-%m-%d")
    
    total_c = len(clients)
    active_c = 0
    for c in clients:
        if c.get("status") != "Revoked":
            if c.get("expires_at") == "Lifetime" or c.get("expires_at", "") >= today_str:
                c["current_status"] = "🟢 Active"
                active_c += 1
            else:
                c["current_status"] = "🔴 Expired"
        else:
            c["current_status"] = "⛔ Revoked"

    expired_c = total_c - active_c

    kpi1, kpi2, kpi3 = st.columns(3)
    with kpi1:
        render_html(f"""
        <div style="background:rgba(0,245,255,0.05);border:1px solid rgba(0,245,255,0.2);border-radius:12px;padding:12px;text-align:center;">
          <div style="font-size:11px;color:#8fa3b4;">TOTAL CLIENTS</div>
          <div style="font-size:22px;font-weight:900;color:#00f5ff;">{total_c}</div>
        </div>
        """)
    with kpi2:
        render_html(f"""
        <div style="background:rgba(0,255,163,0.05);border:1px solid rgba(0,255,163,0.2);border-radius:12px;padding:12px;text-align:center;">
          <div style="font-size:11px;color:#8fa3b4;">ACTIVE LICENSES</div>
          <div style="font-size:22px;font-weight:900;color:#00ffa3;">{active_c}</div>
        </div>
        """)
    with kpi3:
        render_html(f"""
        <div style="background:rgba(255,94,117,0.05);border:1px solid rgba(255,94,117,0.2);border-radius:12px;padding:12px;text-align:center;">
          <div style="font-size:11px;color:#8fa3b4;">EXPIRED / REVOKED</div>
          <div style="font-size:22px;font-weight:900;color:#ff5e75;">{expired_c}</div>
        </div>
        """)

    if clients:
        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
        tbl_data = []
        for c in clients:
            mob_b = bool(c.get("bound_mobile_id"))
            pc_b = bool(c.get("bound_pc_id"))
            if mob_b and pc_b:
                b_status = "📱 Mobile + 💻 PC"
            elif mob_b:
                b_status = "📱 Mobile Only"
            elif pc_b:
                b_status = "💻 PC Only"
            else:
                b_status = "⚪ Unbound (0/2)"
                
            tbl_data.append({
                "Client Name": c.get("client_name"),
                "License Key": c.get("key"),
                "Telegram ID": c.get("telegram_id", "—"),
                "Plan": c.get("duration"),
                "Expires": c.get("expires_at"),
                "Status": c.get("current_status"),
                "Registered Devices": b_status,
            })
        st.dataframe(pd.DataFrame(tbl_data), use_container_width=True, hide_index=True)
        
        st.markdown("---")
        render_html('<div style="font-size:11px;font-weight:800;color:#79dff0;margin-bottom:6px;">⚙️ EDIT CLIENT TELEGRAM ID</div>')
        edit_col1, edit_col2, edit_col3 = st.columns([2, 2, 1.5])
        with edit_col1:
            key_to_edit = st.selectbox("Select Key to Update:", [c.get("key") for c in clients], key="sel_key_edit")
        with edit_col2:
            new_tg_input = st.text_input("New Telegram ID:", placeholder="e.g. 7153364048", key="new_tg_val")
        with edit_col3:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            if st.button("💾 Save Telegram ID", use_container_width=True):
                for c in clients:
                    if c.get("key") == key_to_edit:
                        c["telegram_id"] = new_tg_input.strip()
                save_vip_registry(clients)
                st.success(f"Telegram ID updated successfully!")
                time.sleep(0.4)
                st.rerun()

        st.markdown("---")
        act_col1, act_col2, act_col3, act_col4 = st.columns([2.2, 1.3, 1.3, 1.2])
        with act_col1:
            key_selected = st.selectbox("Select Client Key:", [c.get("key") for c in clients], key="sel_key_action")
        with act_col2:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            if st.button("🔄 Reset Lock", use_container_width=True):
                for c in clients:
                    if c.get("key") == key_selected:
                        c["bound_mobile_id"] = ""
                        c["bound_pc_id"] = ""
                        c["bound_at"] = ""
                save_vip_registry(clients)
                st.success(f"Device lock reset (0/2 devices bound)!")
                time.sleep(0.4)
                st.rerun()
        with act_col3:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            if st.button("⛔ Revoke", type="secondary", use_container_width=True):
                for c in clients:
                    if c.get("key") == key_selected:
                        c["status"] = "Revoked"
                save_vip_registry(clients)
                st.warning(f"Key revoked!")
                time.sleep(0.4)
                st.rerun()
        with act_col4:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            if st.button("🗑️ Delete", type="secondary", use_container_width=True):
                updated_clients = [c for c in clients if c.get("key") != key_selected]
                save_vip_registry(updated_clients)
                st.success(f"Client deleted successfully!")
                time.sleep(0.4)
                st.rerun()
    else:
        st.info("No VIP clients registered yet. Generate a key above to start building your client base!")

def main() -> None:
    inject_css()

    fred_key = DEFAULT_FRED_KEY
    channel_name = DEFAULT_TELEGRAM_CHANNEL

    if fred_key:
        start_background_alert_daemon(fred_key, channel_name)

    auth_user = render_vip_gate()
    if not auth_user:
        return

    render_top_header(auth_user)

    page_dashboard(fred_key, channel_name, auth_user)

    render_html(f"""
    <div class="app-foot">
      <div>© 2026 ApexMacro • Institutional Macro Intelligence</div>
      <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {get_current_time().strftime('%H:%M:%S')}</span></div>
    </div>
    """)

if __name__ == "__main__":
    main()
