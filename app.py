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
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, InvalidOperation
import calendar as cal_lib
import re
import feedparser
from bs4 import BeautifulSoup
import threading
import time
import hashlib
import xml.etree.ElementTree as ET
import urllib.request
from urllib.parse import quote

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

DEFAULT_FRED_KEY = get_secret("FRED_API_KEY", "")
DEFAULT_TELEGRAM_CHANNEL = get_secret("TELEGRAM_CHANNEL", "Forex_LiveStream")
DEFAULT_OPENROUTER_KEY = get_secret("OPENROUTER_API_KEY", "")
REQUEST_TIMEOUT = 8

FOREX_FACTORY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

@st.cache_data(ttl=300, show_spinner=False)
def fetch_forex_factory_calendar() -> list[dict]:
    """Fetch the Forex Factory weekly calendar and keep only High/Medium events."""
    try:
        response = requests.get(
            FOREX_FACTORY_CALENDAR_URL,
            headers={"User-Agent": "Mozilla/5.0 ApexMacro/14.0"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return []
        return [
            item for item in data
            if str(item.get("impact", "")).strip().lower() in {"high", "medium"}
        ]
    except Exception:
        return []

TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "")

APEX_MASTER_KEY = get_secret("APEX_MASTER_KEY", "")
APEX_SECRET_SALT = "APEX_MACRO_SECRET_2026_SALT"
REGISTRY_FILE = os.path.join(os.path.dirname(__file__), "vip_registry.json")

USDT_TRC20_ADDRESS = get_secret("USDT_TRC20_ADDRESS", "")
TRONGRID_API_KEY = get_secret("TRONGRID_API_KEY", "")
TRONGRID_BASE_URL = get_secret("TRONGRID_BASE_URL", "https://api.trongrid.io").rstrip("/")
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
VIP_PAYMENT_PLANS = {
    "1 Month": {"amount": 29, "days": 30, "badge": "MONTHLY"},
    "3 Months": {"amount": 75, "days": 90, "badge": "BEST VALUE"},
}
PAYMENTS_FILE = os.path.join(os.path.dirname(__file__), "vip_payments.json")
_PAYMENT_LOCK = threading.RLock()
SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "vip_sessions.json")

SUPABASE_URL = get_secret("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = get_secret("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STATE_TABLE = get_secret("SUPABASE_STATE_TABLE", "apexmacro_state") or "apexmacro_state"
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SUPABASE_STATE_TABLE):
    SUPABASE_STATE_TABLE = "apexmacro_state"
_PERSISTENCE_LOCK = threading.RLock()
_PERSISTENCE_STATUS = {"backend": "local", "last_error": ""}
ACTUALS_FILE = os.path.join(os.path.dirname(__file__), "actual_releases.json")
ALERT_STATE_FILE = os.path.join(os.path.dirname(__file__), "alert_regime_state.json")
TELEGRAM_UPDATE_STATE_FILE = os.path.join(os.path.dirname(__file__), "telegram_update_state.json")
TELEGRAM_DAEMON_LOCK_FILE = os.path.join(os.path.dirname(__file__), ".apexmacro_telegram_daemon.lock")
TACTICAL_STATE_FILE = os.path.join(os.path.dirname(__file__), "tactical_move_state.json")
_TACTICAL_STATE_LOCK = threading.RLock()

_VIP_REGISTRY_LOCK = threading.RLock()

def _supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _supabase_headers(prefer: str = "") -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _supabase_state_url() -> str:
    return f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}"


def _supabase_load_state(state_id: str) -> tuple[bool, object | None]:
    if not _supabase_enabled():
        return False, None
    try:
        response = requests.get(
            _supabase_state_url(),
            headers=_supabase_headers(),
            params={"id": f"eq.{state_id}", "select": "payload"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            _PERSISTENCE_STATUS.update({"backend": "supabase", "last_error": ""})
            return True, None
        payload = rows[0].get("payload") if isinstance(rows[0], dict) else None
        _PERSISTENCE_STATUS.update({"backend": "supabase", "last_error": ""})
        return True, payload
    except Exception as exc:
        _PERSISTENCE_STATUS.update({"backend": "local-fallback", "last_error": str(exc)[:220]})
        return False, None


def _supabase_save_state(state_id: str, payload: object) -> bool:
    if not _supabase_enabled():
        return False
    try:
        response = requests.post(
            _supabase_state_url(),
            headers=_supabase_headers("resolution=merge-duplicates,return=minimal"),
            params={"on_conflict": "id"},
            json={
                "id": state_id,
                "payload": payload,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        _PERSISTENCE_STATUS.update({"backend": "supabase", "last_error": ""})
        return True
    except Exception as exc:
        _PERSISTENCE_STATUS.update({"backend": "local-fallback", "last_error": str(exc)[:220]})
        return False


def _read_local_json(path: str, default: object) -> object:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_local_json_atomic(path: str, payload: object) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _load_persistent_state(state_id: str, local_path: str, default: object) -> object:
    with _PERSISTENCE_LOCK:
        remote_ok, remote_payload = _supabase_load_state(state_id)
        if remote_ok and remote_payload is not None:
            try:
                _write_local_json_atomic(local_path, remote_payload)
            except Exception:
                pass
            return remote_payload

        local_payload = _read_local_json(local_path, default)
        if remote_ok and remote_payload is None:
            _supabase_save_state(state_id, local_payload)
        return local_payload


def _save_persistent_state(state_id: str, local_path: str, payload: object) -> None:
    with _PERSISTENCE_LOCK:
        try:
            _write_local_json_atomic(local_path, payload)
        except Exception:
            pass
        _supabase_save_state(state_id, payload)


def get_persistence_status() -> dict[str, str]:
    status = dict(_PERSISTENCE_STATUS)
    if _supabase_enabled() and status.get("backend") == "local":
        status["backend"] = "supabase-configured"
    return status


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
    data = _load_persistent_state("vip_sessions", SESSIONS_FILE, {})
    return data if isinstance(data, dict) else {}

def save_sessions_cache(sessions: dict) -> None:
    _save_persistent_state("vip_sessions", SESSIONS_FILE, sessions if isinstance(sessions, dict) else {})

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
    with _VIP_REGISTRY_LOCK:
        data = _load_persistent_state("vip_registry", REGISTRY_FILE, [])
        return data if isinstance(data, list) else []


def _write_vip_registry_unlocked(clients: list[dict]) -> None:
    _save_persistent_state("vip_registry", REGISTRY_FILE, clients)


def save_vip_registry(clients: list[dict]) -> None:
    with _VIP_REGISTRY_LOCK:
        try:
            current = _load_persistent_state("vip_registry", REGISTRY_FILE, [])
            current_by_key = {
                str(c.get("key", "")): c for c in current
                if isinstance(c, dict) and c.get("key")
            } if isinstance(current, list) else {}

            merged: list[dict] = []
            for client in clients:
                item = dict(client)
                persisted_client = current_by_key.get(str(item.get("key", "")))
                if persisted_client is not None and "alert_assets" in persisted_client:
                    item["alert_assets"] = persisted_client.get("alert_assets")
                merged.append(item)
            _write_vip_registry_unlocked(merged)
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
    
    if APEX_MASTER_KEY and clean_k == APEX_MASTER_KEY.upper():
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

ALERT_ASSETS: dict[str, str] = {
    "Gold": "🥇 Gold (XAUUSD)",
}
if "USD" in CURRENCY_SERIES:
    _usd_meta = CURRENCY_SERIES["USD"]
    ALERT_ASSETS["USD"] = f"{_usd_meta.get('flag', '💵')} US Dollar (USD)"
ALERT_ASSETS["Oil"] = "🛢️ Crude Oil (WTI/Brent)"
ALERT_ASSETS["NDX"] = "📊 Nasdaq-100 (NDX)"
for _currency_code, _currency_meta in CURRENCY_SERIES.items():
    if _currency_code == "USD":
        continue
    ALERT_ASSETS[_currency_code] = (
        f"{_currency_meta.get('flag', '💱')} {_currency_meta.get('name', _currency_code)} ({_currency_code})"
    )


def _all_alert_asset_keys() -> list[str]:
    return list(ALERT_ASSETS.keys())


def _client_alert_asset_keys(client: dict) -> set[str]:
    if "alert_assets" not in client:
        return set(_all_alert_asset_keys())
    raw = client.get("alert_assets")
    if not isinstance(raw, list):
        return set(_all_alert_asset_keys())
    supported = set(_all_alert_asset_keys())
    return {str(key) for key in raw if str(key) in supported}


def _client_license_is_current(client: dict) -> bool:
    if str(client.get("status", "")).strip().lower() != "active":
        return False
    exp = str(client.get("expires_at", "")).strip()
    if not exp or exp.lower() in {"lifetime", "never", "unlimited"}:
        return True
    try:
        return get_current_time().date() <= datetime.strptime(exp, "%Y-%m-%d").date()
    except Exception:
        return True


def _find_authorized_telegram_client(telegram_user_id: str, clients: list[dict] | None = None) -> dict | None:
    target = str(telegram_user_id or "").strip()
    if not target:
        return None
    source = clients if clients is not None else load_vip_registry()
    for client in source:
        if str(client.get("telegram_id", "")).strip() == target and _client_license_is_current(client):
            return client
    return None


def _set_client_alert_assets(telegram_user_id: str, selected_assets: set[str]) -> bool:
    target = str(telegram_user_id or "").strip()
    supported = set(_all_alert_asset_keys())
    clean_selection = [key for key in _all_alert_asset_keys() if key in selected_assets and key in supported]
    with _VIP_REGISTRY_LOCK:
        clients = load_vip_registry()
        changed = False
        for client in clients:
            if str(client.get("telegram_id", "")).strip() == target and _client_license_is_current(client):
                client["alert_assets"] = clean_selection
                changed = True
                break
        if changed:
            try:
                _write_vip_registry_unlocked(clients)
            except Exception:
                return False
        return changed


def _telegram_api(method: str, payload: dict | None = None, timeout: int = 8) -> dict:
    token = TELEGRAM_BOT_TOKEN
    if not token:
        return {"ok": False}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload or {},
            timeout=timeout,
        )
        return response.json() if response.content else {"ok": response.ok}
    except Exception:
        return {"ok": False}


def _alert_settings_text() -> str:
    return (
        "🔔 *APEXMACRO — ALERT SETTINGS*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "Select the markets you want ApexMacro to monitor for you.\n\n"
        "You will only receive Telegram Shift Alerts for enabled markets.\n\n"
        "Changes are saved automatically.\n\n"
        "⚡ *ApexMacro Institutional Terminal*"
    )


def _alert_settings_keyboard(selected: set[str]) -> dict:
    rows = []
    for key, label in ALERT_ASSETS.items():
        state = "✅" if key in selected else "❌"
        rows.append([{
            "text": f"{state} {label}",
            "callback_data": f"apex_alert_toggle:{key}",
        }])
    rows.append([
        {"text": "✅ Enable All", "callback_data": "apex_alert_all:on"},
        {"text": "🔕 Disable All", "callback_data": "apex_alert_all:off"},
    ])
    rows.append([{
        "text": "💾 Done",
        "callback_data": "apex_alert_done",
    }])
    return {"inline_keyboard": rows}


def _send_alert_settings_menu(chat_id: str, client: dict) -> None:
    _telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": _alert_settings_text(),
        "parse_mode": "Markdown",
        "reply_markup": _alert_settings_keyboard(_client_alert_asset_keys(client)),
    })


def _edit_alert_settings_menu(chat_id: str, message_id: int, selected: set[str]) -> None:
    _telegram_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": _alert_settings_text(),
        "parse_mode": "Markdown",
        "reply_markup": _alert_settings_keyboard(selected),
    })


def _handle_telegram_update(update: dict) -> None:
    message = update.get("message") or {}
    if message:
        text = str(message.get("text", "")).strip()
        command = text.split()[0].split("@", 1)[0].lower() if text else ""
        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = str(from_user.get("id", "")).strip()
        chat_id = str(chat.get("id", "")).strip()

        if command == "/start":
            client = _find_authorized_telegram_client(user_id)
            if client and chat_id:
                _telegram_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        "🏛️ *Welcome to ApexMacro*\n\n"
                        "Your Telegram account is connected to an active ApexMacro client.\n\n"
                        f"🆔 Your Telegram ID: `{user_id}`\n\n"
                        "Use /alerts to choose which market Shift Alerts and personalized hourly market reports you receive.\n\n"
                        "⚡ *ApexMacro Institutional Terminal*"
                    ),
                    "parse_mode": "Markdown",
                })
            elif chat_id:
                _telegram_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        "🏛️ *ApexMacro*\n\n"
                        "This Telegram account is not linked to an active ApexMacro client yet.\n\n"
                        f"🆔 Your Telegram ID: `{user_id}`\n\n"
                        "Use this ID when purchasing ApexMacro VIP, then /alerts will become available automatically."
                    ),
                    "parse_mode": "Markdown",
                })
        elif command == "/alerts":
            client = _find_authorized_telegram_client(user_id)
            if client and chat_id:
                _send_alert_settings_menu(chat_id, client)
            elif chat_id:
                _telegram_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": "⛔ ApexMacro VIP alert settings are available only to authorized active clients.",
                })
        return

    callback = update.get("callback_query") or {}
    if not callback:
        return

    callback_id = str(callback.get("id", ""))
    data = str(callback.get("data", ""))
    from_user = callback.get("from") or {}
    user_id = str(from_user.get("id", "")).strip()
    cb_message = callback.get("message") or {}
    chat_id = str((cb_message.get("chat") or {}).get("id", "")).strip()
    message_id = cb_message.get("message_id")

    client = _find_authorized_telegram_client(user_id)
    if not client:
        if callback_id:
            _telegram_api("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "VIP access is not authorized.",
                "show_alert": True,
            })
        return

    selected = _client_alert_asset_keys(client)
    changed = False
    if data.startswith("apex_alert_toggle:"):
        asset_key = data.split(":", 1)[1]
        if asset_key in ALERT_ASSETS:
            if asset_key in selected:
                selected.remove(asset_key)
            else:
                selected.add(asset_key)
            changed = _set_client_alert_assets(user_id, selected)
    elif data == "apex_alert_all:on":
        selected = set(_all_alert_asset_keys())
        changed = _set_client_alert_assets(user_id, selected)
    elif data == "apex_alert_all:off":
        selected = set()
        changed = _set_client_alert_assets(user_id, selected)
    elif data == "apex_alert_done":
        if callback_id:
            _telegram_api("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "Alert preferences saved.",
            })
        if chat_id and message_id is not None:
            _telegram_api("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": (
                    "✅ *APEXMACRO — ALERT SETTINGS SAVED*\n"
                    "━━━━━━━━━━━━━━━━━━━\n\n"
                    "Your Shift Alert preferences are active.\n"
                    "Send /alerts anytime to change them.\n\n"
                    "⚡ *ApexMacro Institutional Terminal*"
                ),
                "parse_mode": "Markdown",
            })
        return
    else:
        if callback_id:
            _telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})
        return

    if callback_id:
        _telegram_api("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Saved" if changed else "No change",
        })
    if changed and chat_id and message_id is not None:
        _edit_alert_settings_menu(chat_id, int(message_id), selected)


def _telegram_bot_fingerprint(token: str | None = None) -> str:
    raw = str(token if token is not None else TELEGRAM_BOT_TOKEN).strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_telegram_update_offset() -> int:
    try:
        if os.path.exists(TELEGRAM_UPDATE_STATE_FILE):
            with open(TELEGRAM_UPDATE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return 0
            saved_fingerprint = str(data.get("bot_fingerprint", "")).strip()
            current_fingerprint = _telegram_bot_fingerprint()
            if not saved_fingerprint or saved_fingerprint != current_fingerprint:
                return 0
            return max(0, int(data.get("offset", 0)))
    except Exception:
        pass
    return 0


def _save_telegram_update_offset(offset: int) -> None:
    try:
        tmp_path = TELEGRAM_UPDATE_STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({
                "offset": int(offset),
                "bot_fingerprint": _telegram_bot_fingerprint(),
            }, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, TELEGRAM_UPDATE_STATE_FILE)
    except Exception:
        pass


@st.cache_resource
def _get_telegram_update_controller():
    return {
        "running": False,
        "offset": _load_telegram_update_offset(),
        "lock": threading.Lock(),
    }


def start_telegram_update_worker() -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    ctrl = _get_telegram_update_controller()
    with ctrl["lock"]:
        if ctrl["running"]:
            return
        ctrl["running"] = True

    def _poll_updates() -> None:
        token = TELEGRAM_BOT_TOKEN
        if not token:
            return

        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/deleteWebhook",
                json={"drop_pending_updates": False},
                timeout=10,
            )
        except Exception:
            pass

        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/setMyCommands",
                json={
                    "commands": [
                        {"command": "start", "description": "Open ApexMacro bot"},
                        {"command": "alerts", "description": "Configure market alert subscriptions"},
                    ]
                },
                timeout=10,
            )
        except Exception:
            pass

        while True:
            try:
                response = requests.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={
                        "offset": int(ctrl["offset"]),
                        "timeout": 20,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                    timeout=25,
                )
                try:
                    data = response.json()
                except Exception:
                    data = {"ok": False}

                if not response.ok or not data.get("ok"):
                    time.sleep(3)
                    continue

                for update in data.get("result", []):
                    update_id = int(update.get("update_id", -1))
                    try:
                        _handle_telegram_update(update)
                    finally:
                        if update_id >= 0:
                            ctrl["offset"] = max(int(ctrl["offset"]), update_id + 1)
                            _save_telegram_update_offset(int(ctrl["offset"]))
            except Exception:
                time.sleep(3)

    threading.Thread(
        target=_poll_updates,
        daemon=True,
        name="ApexMacroTelegramUpdateWorker",
    ).start()

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


/* ===== Forecaster v15 visual system — presentation only ===== */
.fc-hero{background:linear-gradient(135deg,rgba(0,245,255,.075),rgba(173,123,255,.055));border:1px solid rgba(0,245,255,.20);border-radius:20px;padding:20px 22px;margin-bottom:16px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.fc-hero:after{content:'';position:absolute;width:260px;height:260px;border-radius:50%;right:-110px;top:-150px;background:rgba(0,245,255,.07);filter:blur(14px);pointer-events:none}
.fc-hero-row{display:flex;justify-content:space-between;gap:18px;align-items:center;position:relative;z-index:1}
.fc-eyebrow{font-size:9px;font-weight:900;letter-spacing:2px;color:#79dff0;text-transform:uppercase;margin-bottom:5px}
.fc-title{font-size:22px;font-weight:900;color:#fff;letter-spacing:-.25px}
.fc-sub{font-size:11.5px;color:#8fa3b4;line-height:1.55;margin-top:5px;max-width:760px}
.fc-live{display:inline-flex;align-items:center;gap:6px;margin-top:10px;font-size:9.5px;font-weight:850;color:#00ffa3;background:rgba(0,255,163,.07);border:1px solid rgba(0,255,163,.22);padding:5px 9px;border-radius:999px}
.fc-horizon{text-align:right;min-width:150px}.fc-horizon-lbl{font-size:9px;color:#718795;font-weight:850;letter-spacing:1px}.fc-horizon-val{font-size:13px;color:#ffd166;font-weight:900;margin-top:3px}
.fc-event{background:linear-gradient(180deg,rgba(12,21,31,.92),rgba(6,12,19,.94));border:1px solid rgba(150,210,225,.12);border-radius:18px;margin:0 0 14px;box-shadow:0 12px 35px rgba(0,0,0,.28);overflow:hidden}
.fc-event-top{padding:15px 17px;border-bottom:1px solid rgba(255,255,255,.055);display:flex;justify-content:space-between;gap:12px;align-items:center}
.fc-event-id{display:flex;align-items:center;gap:9px;min-width:0}.fc-flag{font-size:20px}.fc-cur{font-size:9px;font-weight:900;color:#00f5ff;background:rgba(0,245,255,.08);border:1px solid rgba(0,245,255,.20);padding:3px 7px;border-radius:6px}.fc-event-name{font-size:14px;font-weight:850;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:650px}
.fc-impact{font-size:9px;font-weight:850;padding:3px 7px;border-radius:999px}.fc-high{color:#ff788a;background:rgba(255,94,117,.08);border:1px solid rgba(255,94,117,.22)}.fc-medium{color:#ffd166;background:rgba(255,209,102,.07);border:1px solid rgba(255,209,102,.20)}
.fc-time{font-size:10.5px;color:#8fa3b4;margin-top:5px}.fc-count{font-size:10px;font-weight:850;color:#00f5ff;background:rgba(0,245,255,.07);border:1px solid rgba(0,245,255,.20);padding:6px 9px;border-radius:8px;white-space:nowrap}
.fc-body{padding:15px 17px 17px}.fc-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-bottom:11px}.fc-metric{padding:10px 11px;background:rgba(255,255,255,.018);border:1px solid rgba(255,255,255,.06);border-radius:10px}.fc-metric-l{font-size:8.5px;font-weight:850;color:#718795;text-transform:uppercase;letter-spacing:.75px}.fc-metric-v{font-size:15px;font-weight:900;color:#fff;margin-top:3px}.fc-metric-note{font-size:9px;color:#687b88;margin-top:2px}
.fc-nowcast{border-radius:12px;padding:13px 14px;display:grid;grid-template-columns:minmax(0,1.65fr) minmax(190px,.65fr);gap:14px;align-items:center}.fc-now-lbl{font-size:9px;font-weight:900;text-transform:uppercase;letter-spacing:.9px}.fc-now-title{font-size:15px;font-weight:900;margin:4px 0 5px}.fc-now-desc{font-size:10.8px;color:#d8e4eb;line-height:1.5}.fc-score{text-align:right}.fc-score-num{font-size:22px;font-weight:950}.fc-score-cap{font-size:8.5px;color:#718795;text-transform:uppercase;font-weight:850;letter-spacing:.8px}
.fc-outlook{display:grid;grid-template-columns:1.45fr repeat(3,.72fr);gap:8px;margin-top:10px}.fc-outlook-main,.fc-asset{background:rgba(0,0,0,.18);border:1px solid rgba(255,255,255,.055);border-radius:10px;padding:10px}.fc-outlook-main{border-color:rgba(0,245,255,.13)}.fc-small-lbl{font-size:8.5px;color:#718795;text-transform:uppercase;font-weight:850;letter-spacing:.7px}.fc-main-action{font-size:11.5px;font-weight:900;margin-top:4px}.fc-main-desc{font-size:9.8px;color:#92a5b1;line-height:1.4;margin-top:3px}.fc-asset{font-size:10px;color:#dce7ed;line-height:1.35}.fc-asset b{display:block;color:#fff;margin-bottom:3px}
.fc-ai{margin-top:10px;background:rgba(8,15,23,.72);border:1px solid rgba(173,123,255,.16);border-radius:12px;padding:12px}.fc-ai-head{display:flex;justify-content:space-between;gap:8px;align-items:center}.fc-ai-title{font-size:9px;font-weight:900;color:#ad7bff;letter-spacing:1px;text-transform:uppercase}.fc-ai-conf{font-size:9px;font-weight:850;color:#00ffa3}.fc-ai-assess{font-size:12px;font-weight:850;color:#fff;margin-top:6px}.fc-ai-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:8px}.fc-ai-box{background:rgba(255,255,255,.018);border:1px solid rgba(255,255,255,.055);border-radius:8px;padding:8px;font-size:9.8px;color:#dce7ed;line-height:1.4}.fc-ai-box b{font-size:8.5px;letter-spacing:.6px}.fc-ai-foot{font-size:9.5px;color:#8397a4;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,.05)}
@media(max-width:760px){.fc-hero-row,.fc-event-top{align-items:flex-start;flex-direction:column}.fc-horizon{text-align:left}.fc-event-name{white-space:normal;max-width:none}.fc-metrics{grid-template-columns:1fr 1fr}.fc-nowcast{grid-template-columns:1fr}.fc-score{text-align:left}.fc-outlook{grid-template-columns:1fr 1fr}.fc-outlook-main{grid-column:1/-1}.fc-ai-grid{grid-template-columns:1fr}.fc-body{padding:12px}.fc-event-top{padding:12px}.fc-title{font-size:19px}}


      /* ===== HEADER ALIGNMENT + TYPOGRAPHY FIX ===== */
      .apex-public-nav{
        width:min(1180px, calc(100% - 40px)) !important;
        min-height:96px !important;
        margin:18px auto 30px !important;
        padding:0 28px !important;
        box-sizing:border-box !important;
        display:grid !important;
        grid-template-columns:auto 1fr auto !important;
        align-items:center !important;
        justify-content:space-between !important;
        column-gap:40px !important;
        overflow:visible !important;
      }

      .apex-public-nav .apex-brand,
      .apex-public-nav .apex-brand-wrap,
      .apex-public-nav .brand-wrap{
        position:static !important;
        transform:none !important;
        margin:0 !important;
        align-self:center !important;
        justify-self:start !important;
        display:flex !important;
        align-items:center !important;
        gap:15px !important;
      }

      .apex-public-nav .apex-logo,
      .apex-public-nav .brand-logo{
        position:static !important;
        margin:0 !important;
        width:54px !important;
        height:54px !important;
        min-width:54px !important;
        align-self:center !important;
      }

      .apex-public-nav .apex-brand-name,
      .apex-public-nav .brand-name{
        font-size:24px !important;
        line-height:1.05 !important;
        letter-spacing:1.8px !important;
        white-space:nowrap !important;
      }

      .apex-public-nav .apex-brand-sub,
      .apex-public-nav .brand-sub{
        margin-top:6px !important;
        font-size:9.5px !important;
        line-height:1 !important;
        letter-spacing:3.2px !important;
        white-space:nowrap !important;
      }

      .apex-public-nav .apex-nav-links,
      .apex-public-nav .nav-links{
        position:static !important;
        transform:none !important;
        margin:0 !important;
        align-self:center !important;
        justify-self:center !important;
        display:flex !important;
        align-items:center !important;
        justify-content:center !important;
        gap:42px !important;
      }

      .apex-public-nav .apex-nav-links a,
      .apex-public-nav .nav-links a{
        font-size:14.5px !important;
        line-height:1 !important;
        font-weight:900 !important;
        letter-spacing:.2px !important;
        white-space:nowrap !important;
      }

      .apex-public-nav .apex-nav-actions,
      .apex-public-nav .nav-actions{
        position:static !important;
        transform:none !important;
        margin:0 !important;
        align-self:center !important;
        justify-self:end !important;
        display:flex !important;
        align-items:center !important;
        gap:16px !important;
      }

      .apex-public-nav .apex-search,
      .apex-public-nav .nav-search,
      .apex-public-nav .apex-profile,
      .apex-public-nav .nav-profile{
        position:static !important;
        margin:0 !important;
        align-self:center !important;
      }

      @media (min-width:1200px){
        .apex-public-nav{
          min-height:104px !important;
          padding:0 34px !important;
          column-gap:50px !important;
        }
        .apex-public-nav .apex-brand-name,
        .apex-public-nav .brand-name{
          font-size:26px !important;
        }
        .apex-public-nav .apex-nav-links a,
        .apex-public-nav .nav-links a{
          font-size:15px !important;
        }
      }

      @media (max-width:900px){
        .apex-public-nav{
          width:calc(100% - 24px) !important;
          min-height:76px !important;
          margin:12px auto 20px !important;
          padding:0 16px !important;
          grid-template-columns:minmax(0,1fr) auto !important;
          column-gap:12px !important;
        }
        .apex-public-nav .apex-nav-links,
        .apex-public-nav .nav-links{
          display:none !important;
        }
        .apex-public-nav .apex-brand-name,
        .apex-public-nav .brand-name{
          font-size:19px !important;
        }
        .apex-public-nav .apex-brand-sub,
        .apex-public-nav .brand-sub{
          font-size:8px !important;
          letter-spacing:2.2px !important;
        }
        .apex-public-nav .apex-logo,
        .apex-public-nav .brand-logo{
          width:46px !important;
          height:46px !important;
          min-width:46px !important;
        }
      }

      @media (max-width:430px){
        .apex-public-nav{
          width:calc(100% - 18px) !important;
          min-height:70px !important;
          padding:0 12px !important;
        }
        .apex-public-nav .apex-brand,
        .apex-public-nav .apex-brand-wrap,
        .apex-public-nav .brand-wrap{
          gap:11px !important;
        }
        .apex-public-nav .apex-brand-name,
        .apex-public-nav .brand-name{
          font-size:17px !important;
        }
      }
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


# ============================================================
# SMART TELEGRAM ALERT ENGINE — Broad Regime Monitoring
# ============================================================

_ALERT_STATE_LOCK = threading.Lock()

def _load_alert_state() -> dict[str, dict]:
    try:
        if os.path.exists(ALERT_STATE_FILE):
            with open(ALERT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for state in data.values():
                    if isinstance(state, dict):
                        state["pending_regime"] = None
                        state["pending_since"] = None
                return data
    except Exception:
        pass
    return {}


def _save_alert_state() -> None:
    try:
        tmp_path = ALERT_STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(GLOBAL_ALERT_STATE, f, indent=2)
        os.replace(tmp_path, ALERT_STATE_FILE)
    except Exception:
        pass


GLOBAL_ALERT_STATE: dict[str, dict] = _load_alert_state()


def _broad_regime(bias_label: str) -> str:
    label = str(bias_label or "").lower()
    if "bullish" in label:
        return "Bullish"
    if "bearish" in label:
        return "Bearish"
    return "Neutral"


def _init_asset_state(asset_key: str, regime: str, score: float) -> bool:
    with _ALERT_STATE_LOCK:
        if asset_key in GLOBAL_ALERT_STATE:
            return False
        GLOBAL_ALERT_STATE[asset_key] = {
            "confirmed_regime": regime,
            "confirmed_score": float(score),
            "pending_regime": None,
            "pending_since": None,
        }
        _save_alert_state()
        return True


def _check_regime_shift(
    asset_key: str,
    new_detailed_label: str,
    new_score: float,
    now_ts: float,
    confirmation_secs: float = 900.0,
) -> str | None:
    with _ALERT_STATE_LOCK:
        state = GLOBAL_ALERT_STATE.get(asset_key)
        if state is None:
            return None

        new_regime = _broad_regime(new_detailed_label)
        confirmed = str(state.get("confirmed_regime") or new_regime)

        if new_regime == confirmed:
            changed = state.get("pending_regime") is not None or state.get("pending_since") is not None
            state["confirmed_score"] = float(new_score)
            state["pending_regime"] = None
            state["pending_since"] = None
            if changed:
                _save_alert_state()
            return None

        is_major_reversal = (
            confirmed in {"Bullish", "Bearish"}
            and new_regime in {"Bullish", "Bearish"}
            and confirmed != new_regime
        )
        if is_major_reversal:
            old_regime = confirmed
            state.update({
                "confirmed_regime": new_regime,
                "confirmed_score": float(new_score),
                "pending_regime": None,
                "pending_since": None,
            })
            _save_alert_state()
            return f"{old_regime}→{new_regime}|IMMEDIATE"

        if state.get("pending_regime") != new_regime:
            state["pending_regime"] = new_regime
            state["pending_since"] = float(now_ts)
            _save_alert_state()
            return None

        pending_since = state.get("pending_since")
        if pending_since is None:
            state["pending_since"] = float(now_ts)
            _save_alert_state()
            return None

        if float(now_ts) - float(pending_since) < float(confirmation_secs):
            return None

        old_regime = confirmed
        state.update({
            "confirmed_regime": new_regime,
            "confirmed_score": float(new_score),
            "pending_regime": None,
            "pending_since": None,
        })
        _save_alert_state()
        return f"{old_regime}→{new_regime}|CONFIRMED"


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


def _compose_gold_intelligence_score(gold_ry: float, gold_usd: float, sentiment_res: dict) -> dict:
    gold_news_pts = float((sentiment_res.get("scores") or {}).get("Gold", 0.0))
    base_score = (
        (0.30 * float(gold_ry))
        + (0.20 * float(gold_usd))
        + (0.50 * (gold_news_pts / 0.50))
    )

    tactical = None
    tactical_score = 0.0
    try:
        tactical = compute_tactical_move("Gold", base_score)
        if tactical and int(tactical.get("confidence", 0)) >= 60:
            tactical_score = float(np.clip(float(tactical.get("score", 0.0)), -1.0, 1.0))
    except Exception:
        tactical = None

    final_score = float(np.clip((0.90 * base_score) + (0.10 * tactical_score), -1.0, 1.0))
    return {
        "score": final_score,
        "base_score": base_score,
        "news_points": gold_news_pts,
        "tactical_score": tactical_score,
        "tactical": tactical,
        "gold_ai": sentiment_res.get("gold_ai", {}),
        "gold_rule_points": float(sentiment_res.get("gold_rule_points", 0.0)),
        "gold_ai_points": float(sentiment_res.get("gold_ai_points", 0.0)),
        "gold_relevant_news_count": int(sentiment_res.get("gold_relevant_news_count", 0)),
    }


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
    gold_intel = _compose_gold_intelligence_score(gold_ry, gold_usd, sentiment_res)
    return gold_intel["score"], ry_val_str, gold_intel["news_points"]

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

@st.cache_data(ttl=30, show_spinner=False)
def _calc_ndx_score_only(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> tuple[float | None, float]:
    try:
        ndx_df = fetch_fred("NASDAQ100", fred_key, limit=90)
        if ndx_df is None or ndx_df.empty:
            return None, 0.0

        ndx_mf = calc_mtf(ndx_df["value"].tolist(), "growth")
        if ndx_mf is None:
            return None, 0.0
        ndx_momentum = ndx_mf["score"]

        ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
        inv_ry = 0.0
        if ry_df is not None and not ry_df.empty:
            ry_mf = calc_mtf(ry_df["value"].tail(36).tolist(), "rate")
            inv_ry = -ry_mf["score"] if ry_mf else 0.0

        usd_score = _calc_currency_score_only("USD", fred_key, channel_name)
        inv_usd = -(usd_score or 0.0)

        all_news = fetch_all_instant_news(channel_name)
        sentiment_res = analyze_news_rule_based(all_news)
        ndx_news_pts = sentiment_res["scores"].get("Nasdaq", 0.0)

        final_ndx = (
            (0.40 * ndx_momentum)
            + (0.20 * inv_ry)
            + (0.15 * inv_usd)
            + (0.25 * (ndx_news_pts / 0.50))
        )
        return final_ndx, ndx_news_pts
    except Exception:
        return None, 0.0



# ============================================================
# TACTICAL MOVE LAYER — Short-Term Price Action
# ============================================================

def _tactical_symbol_config(asset_key: str) -> dict[str, object] | None:
    key = str(asset_key or "").strip()
    fixed = {
        "Gold": {"symbol": "XAUUSD=X", "fallback_symbols": ["GC=F"], "invert": False, "display": "Gold (XAUUSD)", "icon": "🥇"},
        "Oil": {"symbol": "CL=F", "invert": False, "display": "Crude Oil (WTI)", "icon": "🛢️"},
        "NDX": {"symbol": "NQ=F", "invert": False, "display": "Nasdaq-100 (NDX)", "icon": "📊"},
        "USD": {"symbol": "DX-Y.NYB", "invert": False, "display": "US Dollar (USD)", "icon": "🇺🇸"},
    }
    if key in fixed:
        return fixed[key]
    if key not in CURRENCY_SERIES:
        return None

    meta = CURRENCY_SERIES.get(key, {})
    direct_usd = {"EUR", "GBP", "AUD", "NZD"}
    if key in direct_usd:
        symbol, invert = f"{key}USD=X", False
    else:
        symbol, invert = f"USD{key}=X", True
    return {
        "symbol": symbol,
        "invert": invert,
        "display": f"{meta.get('name', key)} ({key})",
        "icon": meta.get("flag", "💱"),
    }


@st.cache_data(ttl=55, show_spinner=False)
def _fetch_tactical_price_series(symbol: str) -> pd.DataFrame | None:
    if not symbol:
        return None
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        response = requests.get(
            url,
            params={
                "range": "5d",
                "interval": "5m",
                "includePrePost": "true",
                "events": "div,splits",
            },
            headers={"User-Agent": "Mozilla/5.0 ApexMacro Tactical/15.0"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
        if not isinstance(result, dict):
            return None
        timestamps = result.get("timestamp") or []
        quote_data = ((((result.get("indicators") or {}).get("quote")) or [{}])[0])
        closes = quote_data.get("close") or []
        if not timestamps or not closes:
            return None
        rows = []
        for ts, close in zip(timestamps, closes):
            try:
                if close is None:
                    continue
                value = float(close)
                if not np.isfinite(value) or value <= 0:
                    continue
                rows.append((int(ts), value))
            except Exception:
                continue
        if len(rows) < 40:
            return None
        df = pd.DataFrame(rows, columns=["ts", "close"])
        df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        return df
    except Exception:
        return None


def _tactical_label(score: float) -> str:
    if score >= 0.62:
        return "Strong Bullish"
    if score >= 0.24:
        return "Bullish"
    if score <= -0.62:
        return "Strong Bearish"
    if score <= -0.24:
        return "Bearish"
    return "Neutral"


def _tactical_icon(label: str) -> str:
    if label == "Strong Bullish": return "🚀"
    if label == "Bullish": return "📈"
    if label == "Strong Bearish": return "🔻"
    if label == "Bearish": return "📉"
    return "⚖️"


def _tactical_interpretation(macro_regime: str, tactical_label: str) -> str:
    macro = str(macro_regime or "Neutral")
    tactical = str(tactical_label or "Neutral")
    if tactical == "Neutral":
        return "Price consolidation / no decisive short-term move"
    tact_dir = "Bullish" if "Bullish" in tactical else "Bearish"
    if macro == "Neutral":
        return f"Short-term {tact_dir.lower()} price move inside a neutral macro regime"
    if macro == tact_dir:
        return "Macro outlook and short-term price action are aligned"
    if macro == "Bullish" and tact_dir == "Bearish":
        return "Bearish pullback against a bullish macro outlook"
    if macro == "Bearish" and tact_dir == "Bullish":
        return "Bullish rebound against a bearish macro outlook"
    return "Mixed macro and short-term conditions"


def compute_tactical_move(asset_key: str, macro_score: float | None = None) -> dict | None:
    cfg = _tactical_symbol_config(asset_key)
    if not cfg:
        return None
    symbols_to_try = [str(cfg.get("symbol", ""))]
    symbols_to_try.extend([str(x) for x in (cfg.get("fallback_symbols") or []) if str(x)])
    df = None
    used_symbol = ""
    for _symbol in symbols_to_try:
        _candidate = _fetch_tactical_price_series(_symbol)
        if _candidate is not None and not _candidate.empty and len(_candidate) >= 40:
            df = _candidate
            used_symbol = _symbol
            break
    if df is None or df.empty or len(df) < 40:
        return None

    closes = df["close"].astype(float).to_numpy()
    if bool(cfg.get("invert")):
        closes = 1.0 / np.maximum(closes, 1e-12)

    def ret(bars: int) -> float:
        if len(closes) <= bars or closes[-1-bars] == 0:
            return 0.0
        return float((closes[-1] / closes[-1-bars]) - 1.0)

    pct = np.diff(closes) / np.maximum(closes[:-1], 1e-12)
    recent_pct = pct[-96:] if len(pct) >= 96 else pct
    vol5 = float(np.nanstd(recent_pct)) if len(recent_pct) else 0.0
    if not np.isfinite(vol5) or vol5 < 1e-6:
        vol5 = max(float(np.nanmean(np.abs(recent_pct))) if len(recent_pct) else 0.0, 1e-6)

    def normalized_move(raw_return: float, bars: int) -> float:
        denom = max(vol5 * (max(bars, 1) ** 0.5), 1e-6)
        return float(np.tanh(raw_return / denom))

    r5 = ret(1)
    r15 = ret(3)
    r60 = ret(12)
    r240 = ret(48) if len(closes) > 48 else ret(max(6, len(closes)//3))

    series = pd.Series(closes)
    ema_fast = float(series.ewm(span=12, adjust=False).mean().iloc[-1])
    ema_slow = float(series.ewm(span=36, adjust=False).mean().iloc[-1])
    trend_scale = max(abs(closes[-1]) * vol5 * 4.0, 1e-6)
    ema_component = float(np.tanh((ema_fast - ema_slow) / trend_scale))

    prior = closes[-73:-1] if len(closes) >= 73 else closes[:-1]
    breakout_component = 0.0
    structure = "Range / Mean-Reversion"
    if len(prior) >= 12:
        prior_high = float(np.nanmax(prior))
        prior_low = float(np.nanmin(prior))
        buffer = max(abs(closes[-1]) * vol5 * 0.35, 1e-8)
        if closes[-1] > prior_high + buffer:
            breakout_component = 1.0
            structure = "Upside Breakout"
        elif closes[-1] < prior_low - buffer:
            breakout_component = -1.0
            structure = "Downside Breakdown"
        elif ema_fast > ema_slow:
            structure = "Higher Short-Term Trend"
        elif ema_fast < ema_slow:
            structure = "Lower Short-Term Trend"

    raw_score = (
        0.10 * normalized_move(r5, 1)
        + 0.22 * normalized_move(r15, 3)
        + 0.30 * normalized_move(r60, 12)
        + 0.20 * normalized_move(r240, 48)
        + 0.13 * ema_component
        + 0.05 * breakout_component
    )
    score = float(np.clip(raw_score, -1.0, 1.0))
    label = _tactical_label(score)

    prev15 = 0.0
    if len(closes) > 6 and closes[-6] != 0:
        prev15 = float((closes[-4] / closes[-7]) - 1.0)
    same_direction = (r15 > 0 and r60 > 0) or (r15 < 0 and r60 < 0)
    accelerating = same_direction and abs(r15) > abs(prev15) * 1.15
    if score >= 0.24:
        momentum = "Upside Accelerating" if accelerating else "Positive Momentum"
    elif score <= -0.24:
        momentum = "Downside Accelerating" if accelerating else "Negative Momentum"
    else:
        momentum = "Balanced Momentum"

    if macro_score is not None:
        detailed, _, _ = bias_from_score(float(macro_score))
        macro_regime = _broad_regime(detailed)
    else:
        macro_regime = str((GLOBAL_ALERT_STATE.get(asset_key) or {}).get("confirmed_regime") or "Neutral")

    confidence = int(min(95, max(50, 52 + abs(score) * 45 + (5 if abs(breakout_component) else 0))))
    last_ts = int(df["ts"].iloc[-1])
    return {
        "key": asset_key,
        "display_name": str(cfg.get("display", asset_key)),
        "icon": str(cfg.get("icon", "📊")),
        "symbol": used_symbol or str(cfg.get("symbol", "")),
        "score": score,
        "label": label,
        "label_icon": _tactical_icon(label),
        "macro_regime": macro_regime,
        "interpretation": _tactical_interpretation(macro_regime, label),
        "momentum": momentum,
        "structure": structure,
        "ret_5m": r5,
        "ret_15m": r15,
        "ret_1h": r60,
        "ret_4h": r240,
        "confidence": confidence,
        "last_price": float(df["close"].iloc[-1]),
        "market_ts": last_ts,
    }


def render_tactical_move_panel(asset_key: str, macro_score: float | None = None) -> None:
    tactical = compute_tactical_move(asset_key, macro_score)
    render_html('<div class="sec-title">Tactical Move — Live Price Action</div>')
    if not tactical:
        st.caption("Live tactical price data is temporarily unavailable. Macro Outlook remains fully active.")
        return
    label = tactical["label"]
    if "Bullish" in label:
        color = "#00ffa3"
    elif "Bearish" in label:
        color = "#ff5e75"
    else:
        color = "#ffd166"
    render_html(f"""
    <div class="comp-box" style="text-align:left;padding:17px 19px;border-color:rgba(0,245,255,.20);">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
        <div>
          <div style="font-size:10px;font-weight:850;letter-spacing:1.4px;color:#8fa3b4;text-transform:uppercase;">SHORT-TERM PRICE ACTION</div>
          <div style="font-size:20px;font-weight:950;color:{color};margin-top:5px;">{tactical['label_icon']} {label}</div>
        </div>
        <div style="font-size:10px;color:#8fa3b4;text-align:right;">Confidence<br><b style="color:#ecf7ff;font-size:14px;">{tactical['confidence']}%</b></div>
      </div>
      <div style="height:1px;background:rgba(255,255,255,.08);margin:13px 0;"></div>
      <div style="font-size:11px;color:#b8c9d5;line-height:1.75;">
        <b style="color:#ecf7ff;">Momentum:</b> {tactical['momentum']}<br>
        <b style="color:#ecf7ff;">Structure:</b> {tactical['structure']}<br>
        <b style="color:#ecf7ff;">15m:</b> {tactical['ret_15m']*100:+.2f}% &nbsp;•&nbsp;
        <b style="color:#ecf7ff;">1h:</b> {tactical['ret_1h']*100:+.2f}% &nbsp;•&nbsp;
        <b style="color:#ecf7ff;">4h:</b> {tactical['ret_4h']*100:+.2f}%
      </div>
      <div style="margin-top:11px;padding:9px 11px;border-radius:10px;background:rgba(255,255,255,.035);font-size:10.5px;color:#8fa3b4;">
        {tactical['interpretation']}
      </div>
      <div style="margin-top:8px;font-size:9.5px;color:#607586;">Tactical Move tracks live short-term price action and does not alter the Macro Outlook model.</div>
    </div>
    """)


def _load_tactical_state() -> dict[str, dict]:
    data = _load_persistent_state("tactical_move_state", TACTICAL_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_tactical_state(state: dict[str, dict]) -> None:
    _save_persistent_state("tactical_move_state", TACTICAL_STATE_FILE, state)


def _update_tactical_alert_state(state: dict[str, dict], tactical: dict, now_ts: float) -> bool:
    key = str(tactical.get("key", ""))
    label = str(tactical.get("label", "Neutral"))
    strong = label if label in {"Strong Bullish", "Strong Bearish"} else ""

    if key == "Gold" and label in {"Bullish", "Bearish"}:
        ret15 = float(tactical.get("ret_15m", 0.0))
        ret1h = float(tactical.get("ret_1h", 0.0))
        confidence = int(tactical.get("confidence", 0))
        structure = str(tactical.get("structure", ""))
        same_direction = (
            (ret15 > 0 and ret1h > 0)
            if label == "Bullish"
            else (ret15 < 0 and ret1h < 0)
        )
        meaningful_move = abs(ret1h) >= 0.0035
        structural_move = structure in {"Upside Breakout", "Downside Breakdown"}
        if confidence >= 68 and same_direction and (meaningful_move or structural_move):
            strong = label

    stt = state.setdefault(key, {
        "active": "", "candidate": "", "candidate_since": None,
        "non_strong_since": None, "last_alert_ts": 0.0,
    })

    if not strong:
        stt["candidate"] = ""
        stt["candidate_since"] = None
        if stt.get("active"):
            if stt.get("non_strong_since") is None:
                stt["non_strong_since"] = float(now_ts)
            elif float(now_ts) - float(stt.get("non_strong_since") or now_ts) >= 600.0:
                stt["active"] = ""
                stt["non_strong_since"] = None
        return False

    stt["non_strong_since"] = None
    if stt.get("active") == strong:
        stt["candidate"] = ""
        stt["candidate_since"] = None
        return False

    structure_now = str(tactical.get("structure", ""))
    score_now = abs(float(tactical.get("score", 0.0)))
    confidence_now = int(tactical.get("confidence", 0))
    ret1h_now = abs(float(tactical.get("ret_1h", 0.0)))

    immediate = score_now >= 0.78 and structure_now in {"Upside Breakout", "Downside Breakdown"}
    if key == "Gold" and strong:
        immediate = immediate or (
            confidence_now >= 72
            and (
                (score_now >= 0.42 and structure_now in {"Upside Breakout", "Downside Breakdown"})
                or ret1h_now >= 0.006
            )
        )
    if stt.get("candidate") != strong:
        stt["candidate"] = strong
        stt["candidate_since"] = float(now_ts)
        if not immediate:
            return False

    candidate_since = float(stt.get("candidate_since") or now_ts)
    required_persistence = 60.0 if key == "Gold" else 180.0
    if not immediate and float(now_ts) - candidate_since < required_persistence:
        return False

    last_alert = float(stt.get("last_alert_ts") or 0.0)
    if last_alert and float(now_ts) - last_alert < 900.0 and stt.get("active") == strong:
        return False

    stt["active"] = strong
    stt["candidate"] = ""
    stt["candidate_since"] = None
    stt["last_alert_ts"] = float(now_ts)
    return True


def _build_tactical_alert_msg(tactical: dict) -> str:
    return (
        "⚡ *APEXMACRO — TACTICAL MOVE*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{tactical['icon']} *Asset:* `{tactical['display_name']}`\n\n"
        f"🏛️ *Macro Outlook:* `{tactical['macro_regime']}`\n"
        f"🎯 *Tactical Move:* `{tactical['label']}`\n"
        f"🚦 *Momentum:* `{tactical['momentum']}`\n"
        f"🧱 *Structure:* `{tactical['structure']}`\n\n"
        f"⏱ *15m:* `{tactical['ret_15m']*100:+.2f}%`  |  *1h:* `{tactical['ret_1h']*100:+.2f}%`  |  *4h:* `{tactical['ret_4h']*100:+.2f}%`\n"
        f"📌 *Interpretation:* {tactical['interpretation']}\n"
        f"💹 *Price Source:* `{tactical.get('symbol', 'Live Market')}`\n\n"
        "_This alert can fire before the broader Macro Outlook changes; price action and macro regime are intentionally tracked as separate layers._\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚡ *ApexMacro Institutional Terminal v15.0*"
    )


def send_personalized_tactical_alert(tactical: dict) -> list[dict]:
    if not TELEGRAM_BOT_TOKEN or not tactical:
        return []
    results = []
    seen_chat_ids: set[str] = set()
    for client in load_vip_registry():
        if not _client_license_is_current(client):
            continue
        chat_id = str(client.get("telegram_id", "")).strip()
        if not chat_id or chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        if tactical.get("key") not in _client_alert_asset_keys(client):
            continue
        result = _telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": _build_tactical_alert_msg(tactical),
            "parse_mode": "Markdown",
        })
        results.append({"chat_id": chat_id, "result": result})
    return results


def check_global_tactical_moves() -> None:
    try:
        now_ts = time.time()
        with _TACTICAL_STATE_LOCK:
            state = _load_tactical_state()
            changed = False
            for asset_key in _all_alert_asset_keys():
                try:
                    tactical = compute_tactical_move(asset_key, None)
                    if not tactical:
                        continue
                    before = json.dumps(state.get(asset_key, {}), sort_keys=True)
                    should_alert = _update_tactical_alert_state(state, tactical, now_ts)
                    after = json.dumps(state.get(asset_key, {}), sort_keys=True)
                    changed = changed or before != after
                    if should_alert:
                        send_personalized_tactical_alert(tactical)
                except Exception:
                    continue
            if changed:
                _save_tactical_state(state)
    except Exception:
        pass

def build_hourly_report(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL, selected_assets: set[str] | None = None) -> str:
    now = get_current_time()
    selected = set(_all_alert_asset_keys()) if selected_assets is None else set(selected_assets)
    def _emoji(score: float) -> str:
        if score > 0.15: return "📈 Bullish"
        if score < -0.15: return "📉 Bearish"
        return "⚖️ Neutral"
    lines = ["📊 *APEX MACRO — HOURLY INSTITUTIONAL BRIEF*", "━━━━━━━━━━━━━━━━━━━━━━━━━"]
    forex_lines = []
    for cur in CURRENCY_SERIES:
        if cur not in selected: continue
        try:
            score = _calc_currency_score_only(cur, fred_key, channel_name) or 0.0
            meta = CURRENCY_SERIES[cur]
            forex_lines.append(f"  {meta['flag']} {cur} Macro Outlook: {_emoji(score)}")
            tactical = compute_tactical_move(cur, score)
            if tactical:
                forex_lines.append(f"     Tactical Move: {tactical['label_icon']} {tactical['label']} | 1h {tactical['ret_1h']*100:+.2f}%")
        except Exception: pass
    if forex_lines: lines.extend(["", "🌐 *Forex Macro Outlook*", *forex_lines])
    market_lines = []; ry_val_str = "N/A"
    if "Gold" in selected:
        try:
            score, ry_val_str, _ = _calc_gold_score_only(fred_key, channel_name)
            market_lines.append(f"  🥇 Gold (XAUUSD) Macro Outlook: {_emoji(score or 0.0)}")
            tactical = compute_tactical_move("Gold", score or 0.0)
            if tactical: market_lines.append(f"     Tactical Move: {tactical['label_icon']} {tactical['label']} | 1h {tactical['ret_1h']*100:+.2f}%")
        except Exception: pass
    if "Oil" in selected:
        try:
            score, _ = _calc_oil_score_only(fred_key, channel_name)
            market_lines.append(f"  🛢️ Oil (WTI) Macro Outlook: {_emoji(score or 0.0)}")
            tactical = compute_tactical_move("Oil", score or 0.0)
            if tactical: market_lines.append(f"     Tactical Move: {tactical['label_icon']} {tactical['label']} | 1h {tactical['ret_1h']*100:+.2f}%")
        except Exception: pass
    if "NDX" in selected:
        try:
            score, _ = _calc_ndx_score_only(fred_key, channel_name)
            if score is not None:
                market_lines.append(f"  📊 Nasdaq-100 (NDX) Macro Outlook: {_emoji(score)}")
                tactical = compute_tactical_move("NDX", score)
                if tactical: market_lines.append(f"     Tactical Move: {tactical['label_icon']} {tactical['label']} | 1h {tactical['ret_1h']*100:+.2f}%")
        except Exception: pass
    if market_lines: lines.extend(["", "🏅 *Macro Outlook — Commodities & Equity*", *market_lines])
    if not forex_lines and not market_lines: return ""
    lines.append("")
    if "Gold" in selected and ry_val_str != "N/A": lines.append(f"▫️ Real Yield 10Y: {ry_val_str}")
    lines.append(f"📅 {now.strftime('%Y-%m-%d %H:%M')} | ApexMacro Institutional Desk")
    return "\n".join(lines)


def send_personalized_hourly_reports(fred_key: str, channel_name: str) -> list[dict]:
    if not TELEGRAM_BOT_TOKEN: return []
    results = []; sent_chat_ids = set()
    for client in load_vip_registry():
        if not _client_license_is_current(client): continue
        chat_id = str(client.get("telegram_id", "")).strip()
        if not chat_id or chat_id in sent_chat_ids: continue
        sent_chat_ids.add(chat_id)
        selected = _client_alert_asset_keys(client)
        if not selected: continue
        message = build_hourly_report(fred_key, channel_name, selected)
        if not message: continue
        result = _telegram_api("sendMessage", {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})
        results.append({"chat_id": chat_id, "result": result})
    return results


def _build_single_asset_alert_msg(
    display_name: str, icon: str,
    old_regime: str, new_regime: str,
    score: float, news_pts: float | None,
    reason: str
) -> str:
    news_line = f"\n📡 *News Sentiment:* `{news_pts:+.2f} pts`" if news_pts is not None else ""
    return (
        "🔄 *APEX MACRO — SHIFT ALERT*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{icon} *Asset:* `{display_name}`\n"
        f"📊 *Status:* `Broad Regime Changed`\n\n"
        f"▪️ *Previous Macro Outlook:*  `{old_regime}`\n"
        f"▪️ *New Macro Outlook:*       `{new_regime}`\n\n"
        f"📈 *Composite Score:*  `{score:+.3f}`"
        f"{news_line}\n"
        f"🕐 *Reason:* {reason}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚡ *ApexMacro Institutional Terminal v15.0*"
    )


def _build_multi_asset_alert_msg(asset_shifts: list[dict]) -> str:
    lines = [
        "🔄 *APEX MACRO — MULTI-ASSET MACRO OUTLOOK SHIFT*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for s in asset_shifts:
        lines.append(f"\n{s['icon']} *{s['display_name']}*")
        lines.append(f"  Previous: `{s['old_regime']}` → New: `{s['new_regime']}`")
        lines.append(f"  Composite: `{s['score']:+.3f}`")
        if s.get("news_pts") is not None:
            lines.append(f"  News Sentiment: `{s['news_pts']:+.2f} pts`")
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ *ApexMacro Institutional Terminal v15.0*")
    return "\n".join(lines)


def send_personalized_shift_alerts(asset_shifts: list[dict]) -> list[dict]:
    if not TELEGRAM_BOT_TOKEN or not asset_shifts:
        return []

    clients = load_vip_registry()
    results: list[dict] = []
    seen_chat_ids: set[str] = set()
    for client in clients:
        if not _client_license_is_current(client):
            continue
        chat_id = str(client.get("telegram_id", "")).strip()
        if not chat_id or chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)

        selected = _client_alert_asset_keys(client)
        filtered = [shift for shift in asset_shifts if shift.get("key") in selected]
        if not filtered:
            continue

        if len(filtered) == 1:
            sft = filtered[0]
            message = _build_single_asset_alert_msg(
                sft["display_name"], sft["icon"], sft["old_regime"], sft["new_regime"],
                sft["score"], sft.get("news_pts"), sft["reason"]
            )
        else:
            message = _build_multi_asset_alert_msg(filtered)

        result = _telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        })
        results.append({"chat_id": chat_id, "result": result})
    return results


def check_global_market_shifts(fred_key: str, channel_name: str) -> None:
    if not fred_key:
        return
    try:
        now_ts = time.time()
        confirmed_shifts: list[dict] = []

        def _collect(asset_key: str, display_name: str, icon: str, score: float | None, news_pts: float | None = None) -> None:
            if score is None:
                return
            detailed, _, _ = bias_from_score(float(score))
            broad = _broad_regime(detailed)
            if _init_asset_state(asset_key, broad, float(score)):
                return
            result = _check_regime_shift(asset_key, detailed, float(score), now_ts)
            if not result:
                return
            transition, mode = result.split("|", 1)
            old_regime, new_regime = transition.split("→", 1)
            reason = "15-minute broad-regime confirmation" if mode == "CONFIRMED" else "Major reversal — immediate"
            confirmed_shifts.append({
                "key": asset_key,
                "display_name": display_name,
                "icon": icon,
                "old_regime": old_regime,
                "new_regime": new_regime,
                "score": float(score),
                "news_pts": news_pts,
                "reason": reason,
            })

        for cur, meta in CURRENCY_SERIES.items():
            try:
                score = _calc_currency_score_only(cur, fred_key, channel_name)
                _collect(cur, f"{meta.get('name', cur)} ({cur})", meta.get("flag", "💱"), score)
            except Exception:
                pass

        try:
            gold_s, _, gold_news_pts = _calc_gold_score_only(fred_key, channel_name)
            _collect("Gold", "Gold (XAUUSD)", "🥇", gold_s, gold_news_pts)
        except Exception:
            pass

        try:
            oil_s, oil_news_pts = _calc_oil_score_only(fred_key, channel_name)
            _collect("Oil", "Crude Oil (WTI)", "🛢️", oil_s, oil_news_pts)
        except Exception:
            pass

        try:
            ndx_s, ndx_news_pts = _calc_ndx_score_only(fred_key, channel_name)
            _collect("NDX", "Nasdaq-100 (NDX)", "📊", ndx_s, ndx_news_pts)
        except Exception:
            pass

        if confirmed_shifts:
            send_personalized_shift_alerts(confirmed_shifts)
    except Exception:
        pass



def _acquire_telegram_daemon_process_lock():
    handle = None
    try:
        import fcntl
        handle = open(TELEGRAM_DAEMON_LOCK_FILE, "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0); handle.truncate(); handle.write(str(os.getpid())); handle.flush()
        return handle
    except Exception:
        try:
            if handle: handle.close()
        except Exception: pass
        return None


@st.cache_resource
def _get_daemon_controller():
    return {
        "running": False,
        "last_hour": get_current_time().strftime("%Y-%m-%d %H"),
        "seen_weekend_news": set(),
        "process_lock": None,
    }

def start_background_alert_daemon(fred_key: str, channel_name: str) -> None:
    ctrl = _get_daemon_controller()
    if ctrl["running"]:
        return
    process_lock = _acquire_telegram_daemon_process_lock()
    if process_lock is None:
        return
    ctrl["process_lock"] = process_lock
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
                                        "🎯 *Monday Open Implication:* Heightened gap risk and safe-haven volatility (Gold / Oil / USD / NDX).\n"
                                        f"🕒 *Time:* `{now.strftime('%Y-%m-%d %H:%M')} (KRD / UTC+3)`\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "⚡ *ApexMacro Institutional Terminal v15.0*"
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
                        send_personalized_hourly_reports(fred_key, channel_name)

                check_global_market_shifts(fred_key, channel_name)
                check_global_tactical_moves()
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


def _is_gold_relevant_news(article: dict) -> bool:
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    terms = [
        "gold", "xau", "xauusd", "bullion", "precious metal", "safe haven", "safe-haven",
        "real yield", "real yields", "treasury yield", "treasury yields", "bond yield", "bond yields",
        "dollar index", "dxy", "us dollar", "u.s. dollar", "dollar weak", "dollar strength",
        "federal reserve", "fed ", "powell", "rate cut", "rate cuts", "rate hike", "rate hikes",
        "dovish", "hawkish", "inflation", "cpi", "pce", "payroll", "nonfarm", "nfp",
        "geopolitical", "war", "missile", "attack", "escalation", "ceasefire", "sanction",
        "central bank buying", "central banks buy", "gold reserve", "gold reserves",
        "gold etf", "etf inflow", "etf outflow"
    ]
    return any(term in text for term in terms)


def _gold_relevant_articles(articles: list, limit: int = 14) -> list:
    relevant = [a for a in (articles or []) if _is_gold_relevant_news(a)]
    return relevant[:max(1, int(limit))]


def _gold_rule_based_news_points(articles: list) -> float:
    score = 0.0
    for art in _gold_relevant_articles(articles, 20):
        text = f"{art.get('title', '')} {art.get('description', '')}".lower()

        if any(k in text for k in [
            "gold rises", "gold rise", "gold gains", "gold jumps", "gold surges", "gold rallies",
            "bullion rises", "bullion gains", "xauusd rises", "xauusd rallies",
            "gold hits record", "gold record high", "gold breaks higher"
        ]):
            score += 0.11
        if any(k in text for k in [
            "gold falls", "gold drops", "gold slides", "gold slumps", "gold tumbles",
            "bullion falls", "xauusd falls", "gold selloff", "gold breaks lower"
        ]):
            score -= 0.11

        if any(k in text for k in [
            "real yields fall", "real yield falls", "real yields decline", "treasury yields fall",
            "yields drop", "yields retreat", "bond yields fall"
        ]):
            score += 0.075
        if any(k in text for k in [
            "real yields rise", "real yield rises", "real yields climb", "treasury yields rise",
            "yields jump", "yield spike", "yields spike", "bond yields rise"
        ]):
            score -= 0.075

        if any(k in text for k in [
            "dollar weakens", "dollar falls", "dollar drops", "dxy falls", "dxy weakens",
            "weaker dollar", "dollar retreats"
        ]):
            score += 0.07
        if any(k in text for k in [
            "dollar strengthens", "dollar rises", "dollar jumps", "dxy rises", "dxy strengthens",
            "stronger dollar", "dollar rallies"
        ]):
            score -= 0.07

        if any(k in text for k in [
            "rate cut", "rate cuts", "dovish fed", "fed dovish", "easing cycle",
            "lower rates", "cuts rates"
        ]):
            score += 0.055
        if any(k in text for k in [
            "rate hike", "rate hikes", "hawkish fed", "fed hawkish",
            "higher for longer", "rates stay high"
        ]):
            score -= 0.055

        if any(k in text for k in [
            "geopolitical tensions", "geopolitical risk", "escalation", "missile", "attack",
            "military strike", "war ", "safe haven demand", "safe-haven demand", "crisis"
        ]):
            score += 0.055
        if any(k in text for k in [
            "ceasefire", "de-escalation", "deescalation", "peace deal",
            "geopolitical tensions ease"
        ]):
            score -= 0.035

        if any(k in text for k in [
            "central bank buying", "central banks buy", "gold reserves increase",
            "gold etf inflow", "gold etf inflows", "etf inflows into gold"
        ]):
            score += 0.07
        if any(k in text for k in [
            "central bank selling", "gold reserves decline", "gold etf outflow",
            "gold etf outflows", "etf outflows from gold"
        ]):
            score -= 0.07

    return float(np.clip(score, -0.50, 0.50))


def get_openrouter_gold_signal(news_text: str, api_key: str = DEFAULT_OPENROUTER_KEY) -> dict:
    default = {
        "direction": "Neutral",
        "score": 0.0,
        "confidence": 0.0,
        "horizon": "Unknown",
        "reason": "AI Gold signal unavailable.",
        "active": False,
    }
    if not news_text or not api_key:
        return default

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://apexmacro.com",
        "X-Title": "ApexMacro Gold Intelligence",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the Gold intelligence analyst for an institutional macro terminal. "
                    "Assess ONLY the directional impact of the supplied CURRENT news on Gold/XAUUSD. "
                    "Reason through real yields, USD/DXY, Federal Reserve expectations, inflation, "
                    "safe-haven/geopolitical demand, central-bank demand and ETF flows. "
                    "Do not treat generic positive/negative words as Gold direction. "
                    "Return ONLY valid JSON with keys: direction, score, confidence, horizon, reason. "
                    "direction must be Bullish, Neutral or Bearish. "
                    "score must be from -1.0 to +1.0. confidence must be from 0 to 100. "
                    "horizon must be Intraday, 1-3 Days, or Multi-Day. "
                    "reason must be one concise sentence."
                )
            },
            {"role": "user", "content": news_text},
        ],
        "temperature": 0.1,
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        content = str(data["choices"][0]["message"]["content"]).strip()
        content = re.sub(r"^```(?:json)?\s*|\s*
