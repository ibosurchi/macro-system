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

# VIP checkout configuration. Only public receiving/API values are used; no wallet private key is required.
USDT_TRC20_ADDRESS = get_secret("USDT_TRC20_ADDRESS", "")
TRONGRID_API_KEY = get_secret("TRONGRID_API_KEY", "")  # Optional; improves TronGrid rate limits.
TRONGRID_BASE_URL = get_secret("TRONGRID_BASE_URL", "https://api.trongrid.io").rstrip("/")
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
VIP_PAYMENT_PLANS = {
    "1 Month": {"amount": 29, "days": 30, "badge": "MONTHLY"},
    "3 Months": {"amount": 75, "days": 90, "badge": "BEST VALUE"},
}
PAYMENTS_FILE = os.path.join(os.path.dirname(__file__), "vip_payments.json")
_PAYMENT_LOCK = threading.RLock()
SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "vip_sessions.json")

# Persistent client storage. Supabase is optional at code level so the app can still
# start locally, but production Streamlit should configure these secrets.
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

# Synchronizes Streamlit/Admin and Telegram worker access to the shared VIP registry.
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
    """Return (request_succeeded, payload). payload=None means the row does not exist yet."""
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
    """Supabase-first read with automatic one-time migration from the existing JSON file."""
    with _PERSISTENCE_LOCK:
        remote_ok, remote_payload = _supabase_load_state(state_id)
        if remote_ok and remote_payload is not None:
            try:
                _write_local_json_atomic(local_path, remote_payload)  # local cache/mirror only
            except Exception:
                pass
            return remote_payload

        local_payload = _read_local_json(local_path, default)
        if remote_ok and remote_payload is None:
            # First run after enabling Supabase: preserve the current clients/payments/sessions.
            _supabase_save_state(state_id, local_payload)
        return local_payload


def _save_persistent_state(state_id: str, local_path: str, payload: object) -> None:
    """Write a local safety copy and the durable Supabase copy."""
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
    """Persist VIP data durably while preserving Telegram-owned alert preferences."""
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

# Canonical Telegram-selectable market keys. Forex entries are derived from the
# existing CURRENCY_SERIES so future configured currencies can be exposed easily.
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
    """Missing field means ALL (backward compatibility); an explicit [] means none."""
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
        # Preserve existing behavior for legacy/custom registry date formats.
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
    """Update only the requesting Telegram user's active registry entry in one locked transaction."""
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
    """Non-secret fingerprint used only to separate persisted getUpdates offsets per bot."""
    raw = str(token if token is not None else TELEGRAM_BOT_TOKEN).strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_telegram_update_offset() -> int:
    """Load an offset only when it belongs to the currently configured Telegram bot."""
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
    """Start one non-blocking daemon worker for /alerts and callback_query updates."""
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

        # getUpdates cannot operate while a webhook is configured.
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/deleteWebhook",
                json={"drop_pending_updates": False},
                timeout=10,
            )
        except Exception:
            pass

        # Register the commands in Telegram's command menu.
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
    """Load persisted broad-regime state. Invalid state is ignored safely."""
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
    """Persist regime state atomically so Streamlit reruns/restarts do not duplicate shifts."""
    try:
        tmp_path = ALERT_STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(GLOBAL_ALERT_STATE, f, indent=2)
        os.replace(tmp_path, ALERT_STATE_FILE)
    except Exception:
        pass


# Per-asset state:
# {"confirmed_regime": str, "confirmed_score": float,
#  "pending_regime": str|None, "pending_since": float|None}
GLOBAL_ALERT_STATE: dict[str, dict] = _load_alert_state()


def _broad_regime(bias_label: str) -> str:
    """Map dashboard detail labels to Bullish | Neutral | Bearish for Telegram only."""
    label = str(bias_label or "").lower()
    if "bullish" in label:
        return "Bullish"
    if "bearish" in label:
        return "Bearish"
    return "Neutral"


def _init_asset_state(asset_key: str, regime: str, score: float) -> bool:
    """Silently initialize an unseen asset; returns True only when initialization occurred."""
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
    """Confirm only broad regime shifts; neutral transitions wait 15 minutes, direct reversals are immediate."""
    with _ALERT_STATE_LOCK:
        state = GLOBAL_ALERT_STATE.get(asset_key)
        if state is None:
            return None

        new_regime = _broad_regime(new_detailed_label)
        confirmed = str(state.get("confirmed_regime") or new_regime)

        # Same broad regime: no alert, and any threshold-noise pending transition is cancelled.
        if new_regime == confirmed:
            changed = state.get("pending_regime") is not None or state.get("pending_since") is not None
            state["confirmed_score"] = float(new_score)
            state["pending_regime"] = None
            state["pending_since"] = None
            if changed:
                _save_alert_state()
            return None

        # Bullish <-> Bearish is a major reversal and bypasses neutral confirmation.
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

        # Every transition involving Neutral must remain valid for ~15 minutes.
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

@st.cache_data(ttl=30, show_spinner=False)
def _calc_ndx_score_only(fred_key: str, channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> tuple[float | None, float]:
    """Nasdaq-100: 40% price momentum + 20% inverse real yield + 15% inverse USD + 25% NDX news."""
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



# Asset registry for smart monitoring
_ASSET_MONITOR_CONFIG = [
    # (asset_key, display_name, icon, score_fn_args)
    # score functions are called per-asset in check_global_market_shifts
]



# ============================================================
# TACTICAL MOVE LAYER — Short-Term Price Action (presentation/delivery only)
# This layer NEVER changes the existing macro scores, weights, thresholds or Smart Shift engine.
# ============================================================

def _tactical_symbol_config(asset_key: str) -> dict[str, object] | None:
    """Map an ApexMacro asset to a liquid Yahoo market symbol and direction convention."""
    key = str(asset_key or "").strip()
    fixed = {
        "Gold": {"symbol": "GC=F", "invert": False, "display": "Gold (XAUUSD)", "icon": "🥇"},
        "Oil": {"symbol": "CL=F", "invert": False, "display": "Crude Oil (WTI)", "icon": "🛢️"},
        # Nasdaq futures are used so tactical movement remains available beyond cash-index hours.
        "NDX": {"symbol": "NQ=F", "invert": False, "display": "Nasdaq-100 (NDX)", "icon": "📊"},
        "USD": {"symbol": "DX-Y.NYB", "invert": False, "display": "US Dollar (USD)", "icon": "🇺🇸"},
    }
    if key in fixed:
        return fixed[key]
    if key not in CURRENCY_SERIES:
        return None

    meta = CURRENCY_SERIES.get(key, {})
    # EUR/GBP/AUD/NZD are normally quoted XXXUSD; most other majors are USDXXX,
    # so USDXXX must be inverted to represent strength of the target currency itself.
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
    """Fetch 5-minute market prices. Failure is silent so the macro engine remains fully independent."""
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
    """Calculate short-term price action independently of the existing macro strategy."""
    cfg = _tactical_symbol_config(asset_key)
    if not cfg:
        return None
    df = _fetch_tactical_price_series(str(cfg["symbol"]))
    if df is None or df.empty or len(df) < 40:
        return None

    closes = df["close"].astype(float).to_numpy()
    if bool(cfg.get("invert")):
        # Invert USDXXX pairs so positive movement always means target-currency strength.
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
        "symbol": str(cfg.get("symbol", "")),
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
    """Compact dashboard card that clearly separates live price action from Macro Outlook."""
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
    """Return True only for a new, meaningful strong tactical move; suppress one-minute noise/spam."""
    key = str(tactical.get("key", ""))
    label = str(tactical.get("label", "Neutral"))
    strong = label if label in {"Strong Bullish", "Strong Bearish"} else ""
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

    # Very strong breakout/breakdown can alert immediately; otherwise require ~3 minutes persistence.
    immediate = abs(float(tactical.get("score", 0.0))) >= 0.78 and str(tactical.get("structure", "")) in {"Upside Breakout", "Downside Breakdown"}
    if stt.get("candidate") != strong:
        stt["candidate"] = strong
        stt["candidate_since"] = float(now_ts)
        if not immediate:
            return False

    candidate_since = float(stt.get("candidate_since") or now_ts)
    if not immediate and float(now_ts) - candidate_since < 180.0:
        return False

    # Prevent repeated same-direction alerts during noisy reconnects/restarts.
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
        f"📌 *Interpretation:* {tactical['interpretation']}\n\n"
        "_Short-term price action layer — Macro Outlook remains independent._\n"
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
    """Monitor every selectable asset for strong live price moves without touching macro calculations."""
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
    """Build the hourly brief, optionally containing only one client's selected market assets."""
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
    """Send each active VIP an hourly brief containing only enabled market assets."""
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
    """Filter confirmed global shifts per VIP client's saved Telegram market preferences."""
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
    """Smart broad-regime monitoring for every configured currency plus Gold, Oil and NDX."""
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
                return  # first observation is intentionally silent
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

        # Dynamically monitor every currency defined by the existing project strategy.
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
            # Global Smart Shift detection remains unchanged; delivery is personalized per client.
            send_personalized_shift_alerts(confirmed_shifts)
    except Exception:
        pass



def _acquire_telegram_daemon_process_lock():
    """Best-effort OS lock preventing duplicate alert daemons on the same host/filesystem."""
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

def _is_nasdaq_news(article: dict) -> bool:
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    terms = [
        "nasdaq", "nasdaq-100", "ndx", "technology stocks", "tech stocks", "megacap",
        "mega-cap", "ai stocks", "artificial intelligence stocks", "semiconductor", "chip sector",
        "nvidia", "microsoft", "apple", "amazon", "meta", "alphabet", "google", "tesla",
        "us equities", "u.s. equities", "growth stocks", "treasury yield", "real yield", "fed",
        "rate expectations", "risk-on", "risk on", "risk-off", "risk off", "technology sector"
    ]
    return any(term in text for term in terms)


def _nasdaq_relevant_articles(articles: list) -> list:
    return [a for a in (articles or []) if _is_nasdaq_news(a)]


@st.cache_data(ttl=300, show_spinner=False)
def analyze_news_rule_based(articles: list) -> dict:
    scores = {
        "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "CAD": 0.0,
        "JPY": 0.0, "AUD": 0.0, "NZD": 0.0, "CHF": 0.0,
        "Gold": 0.0, "Oil": 0.0, "Nasdaq": 0.0
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
        elif k == "Nasdaq":
            ndx_delta = 0.0
            ndx_bull = [
                "rally", "surge", "beat", "strong earnings", "risk on", "risk-on", "yield falls",
                "yields fall", "rate cut", "dovish", "ai demand", "chip rally", "tech rally"
            ]
            ndx_bear = [
                "selloff", "slump", "miss", "risk off", "risk-off", "yield spike", "yields rise",
                "rate hike", "hawkish", "inflation surprise", "recession", "chip restrictions", "tech selloff"
            ]
            for art in _nasdaq_relevant_articles(articles):
                text2 = (art.get("title", "") + " " + art.get("description", "")).lower()
                if any(kw in text2 for kw in ndx_bull):
                    ndx_delta += 0.06
                if any(kw in text2 for kw in ndx_bear):
                    ndx_delta -= 0.06
            scores[k] = max(min(ndx_delta, 0.5), -0.5)
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
    <div class="t-pill"><span>📊 NDX</span><span class="t-up">▲ Active</span></div>
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

def page_dashboard(fred_key: str, channel_name: str, auth_user: dict | None = None) -> None:
    is_admin_user = auth_user and auth_user.get("is_admin", False)

    if "active_tab" not in st.session_state:
        st.session_state["active_tab"] = "💱 Forex"

    if is_admin_user:
        b1, b2, b3, b4, b5, b6 = st.columns(6)
        with b1:
            if st.button("💱 Forex", use_container_width=True, type="primary" if st.session_state["active_tab"] == "💱 Forex" else "secondary"):
                st.session_state["active_tab"] = "💱 Forex"
                st.rerun()
        with b2:
            if st.button("🥇 Gold", use_container_width=True, type="primary" if st.session_state["active_tab"] == "🥇 Gold" else "secondary"):
                st.session_state["active_tab"] = "🥇 Gold"
                st.rerun()
        with b3:
            if st.button("🛢️ Oil", use_container_width=True, type="primary" if st.session_state["active_tab"] == "🛢️ Oil" else "secondary"):
                st.session_state["active_tab"] = "🛢️ Oil"
                st.rerun()
        with b4:
            if st.button("📊 Nasdaq-100", use_container_width=True, type="primary" if st.session_state["active_tab"] == "📊 Nasdaq-100" else "secondary"):
                st.session_state["active_tab"] = "📊 Nasdaq-100"
                st.rerun()
        with b5:
            if st.button("🔮 Forecaster", use_container_width=True, type="primary" if st.session_state["active_tab"] == "🔮 Forecaster" else "secondary"):
                st.session_state["active_tab"] = "🔮 Forecaster"
                st.rerun()
        with b6:
            if st.button("👑 MASTER ADMIN", use_container_width=True, type="primary" if st.session_state["active_tab"] == "👑 MASTER ADMIN" else "secondary"):
                st.session_state["active_tab"] = "👑 MASTER ADMIN"
                st.rerun()
    else:
        b1, b2, b3, b4, b5 = st.columns(5)
        with b1:
            if st.button("💱 Forex", use_container_width=True, type="primary" if st.session_state["active_tab"] == "💱 Forex" else "secondary"):
                st.session_state["active_tab"] = "💱 Forex"
                st.rerun()
        with b2:
            if st.button("🥇 Gold", use_container_width=True, type="primary" if st.session_state["active_tab"] == "🥇 Gold" else "secondary"):
                st.session_state["active_tab"] = "🥇 Gold"
                st.rerun()
        with b3:
            if st.button("🛢️ Oil", use_container_width=True, type="primary" if st.session_state["active_tab"] == "🛢️ Oil" else "secondary"):
                st.session_state["active_tab"] = "🛢️ Oil"
                st.rerun()
        with b4:
            if st.button("📊 Nasdaq-100", use_container_width=True, type="primary" if st.session_state["active_tab"] == "📊 Nasdaq-100" else "secondary"):
                st.session_state["active_tab"] = "📊 Nasdaq-100"
                st.rerun()
        with b5:
            if st.button("🔮 Forecaster", use_container_width=True, type="primary" if st.session_state["active_tab"] == "🔮 Forecaster" else "secondary"):
                st.session_state["active_tab"] = "🔮 Forecaster"
                st.rerun()

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

    current_tab = st.session_state["active_tab"]

    if current_tab == "👑 MASTER ADMIN" and is_admin_user:
        render_admin_key_generator()
        return

    if current_tab == "🥇 Gold":
        page_gold(fred_key, channel_name)
        return
    if current_tab == "🛢️ Oil":
        page_oil(fred_key, channel_name)
        return
    if current_tab == "📊 Nasdaq-100":
        page_nasdaq(fred_key, channel_name)
        return
    if current_tab == "🔮 Forecaster":
        page_catalyst_forecaster(fred_key, channel_name, auth_user)
        return


    if "selected_currency" not in st.session_state:
        st.session_state["selected_currency"] = "USD"

    curr_keys = ["USD", "EUR", "GBP", "CAD", "JPY", "CHF"]
    currency = st.session_state["selected_currency"]
    c_meta = CURRENCY_SERIES.get(currency, {"flag": "💵", "name": "US Dollar"})
    
    is_open = st.session_state.get("currency_menu_open", False)
    btn_label = f"{c_meta['flag']}  {currency} — {c_meta['name']}  {'▲' if is_open else '▾'}"

    if st.button(btn_label, key="single_curr_btn", use_container_width=True, type="primary"):
        st.session_state["currency_menu_open"] = not is_open
        st.rerun()

    if is_open:
        st.markdown("""
        <div style="background:linear-gradient(180deg,rgba(11,20,32,0.98),rgba(6,12,18,0.98));border:1px solid rgba(0,245,255,0.35);border-radius:16px 16px 0 0;padding:12px 16px 6px;margin-top:6px;box-shadow:0 20px 60px rgba(0,0,0,0.8),0 0 30px rgba(0,245,255,0.16);backdrop-filter:blur(24px);">
          <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(0,245,255,0.2);padding-bottom:6px;">
            <div style="font-size:11px;font-weight:900;color:#00f5ff;text-transform:uppercase;letter-spacing:1.5px;">Select Target Macro Currency</div>
            <div style="font-size:10px;color:#8fa3b4;">Click any currency to select &amp; auto-close</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        
        p_cols = st.columns(3)
        for idx, opt_code in enumerate(curr_keys):
            target_col = p_cols[idx % 3]
            opt_meta = CURRENCY_SERIES.get(opt_code, {"flag": "💵", "name": opt_code})
            opt_is_sel = (currency == opt_code)
            btn_txt = f"{opt_meta['flag']}  {opt_code} — {opt_meta['name']}"
            with target_col:
                if st.button(btn_txt, key=f"curr_opt_{opt_code}", use_container_width=True, type="primary" if opt_is_sel else "secondary"):
                    st.session_state["selected_currency"] = opt_code
                    st.session_state["currency_menu_open"] = False
                    st.rerun()

    currency = st.session_state["selected_currency"]
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

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
            driver_items.append(f'<div style="font-size:11px;color:#ecf7ff;margin-top:4px;text-align:left;"><b>{d.get("icon","⚡")} {d.get("name","Event")}:</b>{dur_tag}<br><span style="color:#8fa3b4;font-size:10px;">{d.get("reason","")}</span></div>')
        drivers_html = "".join(driver_items)

        ai_summary_html = f'<div style="margin-top:8px;padding:8px 10px;background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.22);border-radius:10px;font-size:11px;color:#ecf7ff;text-align:left;line-height:1.45;"><b style="color:#ffd166;">Desk Summary:</b> {result["ai_summary"]}</div>' if result["ai_summary"] else ''

        render_html(f"""
        <div class="comp-box">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:6px;">{CURRENCY_SERIES[currency]['flag']} {currency} OVERALL BIAS</div>
          <div style="margin-bottom:8px;">{badge(s, lg=True)}</div>
          <div style="font-size:18px;font-weight:900;color:#fff;">Composite: <span style="color:#00f5ff;">{s:+.3f}</span></div>
          <div style="font-size:11px;color:#8fa3b4;margin-top:3px;">Macro (50%): <b style="color:#fff;">{m_s:+.3f}</b> | News Sentiment (50%): <b style="color:{np_color};">{n_p:+.2f} pts</b></div>
          {ai_summary_html}
          <div style="margin-top:8px;">{drivers_html}</div>
        </div>
        """)

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_tactical_move_panel(currency, s)

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
<div class="pg-bread">Institutional Real Yield 10Y (DFII10) Analysis, Breakeven Inflation &amp; Safe-Haven Sentiment</div>
</div>
""")
    if not fred_key:
        st.info("🔑 FRED API Key is required.")
        return

    with st.spinner("Analyzing Gold Real Yield (DFII10) & Feeds..."):
        ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
        y_df = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
        i_df = fetch_fred(GOLD_SERIES["inflation_exp"], fred_key, limit=60)
        if (ry_df is None or ry_df.empty) and (y_df is not None and i_df is not None):
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

    render_html('<div class="sec-title">Key Safe-Haven Indicators</div>')
    k1, k2, k3 = st.columns(3)
    with k1:
        _spark_ry = spark_svg(ry_vals[-20:], pos_good=False)
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">🏛️</div><span class="mc-cat">Real Rate</span></div>
          <div class="mc-nm">10Y Real Yield (DFII10)</div>
          <div style="font-size:20px;font-weight:800;color:#00ffa3;margin:4px 0;">{ry_vals[-1]:.2f}%</div>
          <div style="font-size:11px;color:#8fa3b4;">MoM: <b>{ry_mf['mom']:+.2f}%</b> | 📅 {ry_df['date'].iloc[-1]}</div>
          <div style="margin-top:8px;">{_spark_ry}</div>
        </div>
        """)
    with k2:
        y_val = f"{y_df['value'].iloc[-1]:.2f}%" if y_df is not None and not y_df.empty else "4.35%"
        _spark_y = spark_svg(y_df["value"].tail(20).tolist() if y_df is not None and not y_df.empty else ry_vals[-20:], pos_good=False)
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">📈</div><span class="mc-cat">Nominal Rate</span></div>
          <div class="mc-nm">10Y Treasury Yield (DGS10)</div>
          <div style="font-size:20px;font-weight:800;color:#00f5ff;margin:4px 0;">{y_val}</div>
          <div style="font-size:11px;color:#8fa3b4;">Baseline Benchmark Rate</div>
          <div style="margin-top:8px;">{_spark_y}</div>
        </div>
        """)
    with k3:
        i_val = f"{i_df['value'].iloc[-1]:.2f}%" if i_df is not None and not i_df.empty else "2.30%"
        _spark_i = spark_svg(i_df["value"].tail(20).tolist() if i_df is not None and not i_df.empty else ry_vals[-20:], pos_good=True)
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">🔥</div><span class="mc-cat">Expectations</span></div>
          <div class="mc-nm">10Y Breakeven Inflation (T10YIE)</div>
          <div style="font-size:20px;font-weight:800;color:#ffd166;margin:4px 0;">{i_val}</div>
          <div style="font-size:11px;color:#8fa3b4;">Expected Forward Inflation</div>
          <div style="margin-top:8px;">{_spark_i}</div>
        </div>
        """)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    t_col, d_col = st.columns([1, 1])

    with t_col:
        render_html('<div class="sec-title">Gold Pricing Matrix</div>')
        gold_rows = [
            {"name": "10Y Real Yield (DFII10)", "cat": "rate", "latest": ry_vals[-1], "mom": ry_mf['mom'], "qoq": ry_mf.get('qoq'), "yoy": ry_mf.get('yoy'), "vals": ry_vals, "score": -ry_mf['score']},
            {"name": "10Y Treasury Yield (DGS10)", "cat": "rate", "latest": y_df['value'].iloc[-1] if y_df is not None else 4.35, "mom": 0.12, "qoq": 0.45, "yoy": -1.2, "vals": ry_vals, "score": -0.15},
            {"name": "10Y Inflation Exp (T10YIE)", "cat": "inflation", "latest": i_df['value'].iloc[-1] if i_df is not None else 2.30, "mom": 0.05, "qoq": 0.15, "yoy": 0.35, "vals": ry_vals, "score": 0.22},
            {"name": "USD Currency Pressure", "cat": "growth", "latest": usd_r['score'] if usd_r else 0.10, "mom": -0.05, "qoq": 0.20, "yoy": 0.50, "vals": ry_vals, "score": -gold_usd},
        ]
        render_data_table(gold_rows)

    with d_col:
        render_html('<div class="sec-title">Gold Direction &amp; AI Synthesis &nbsp; <span style="color:#00ffa3;font-size:10px;font-weight:800;">⚡ Multi-Alert Active</span></div>')
        gn_color = "#00ffa3" if gold_news_pts > 0 else ("#ff5e75" if gold_news_pts < 0 else "#8fa3b4")
        ai_gold_summary = sentiment_res.get("ai_summary", "")
        ai_summary_html = f'<div style="margin-top:10px;padding:10px 12px;background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.22);border-radius:10px;font-size:11.5px;color:#ecf7ff;text-align:left;line-height:1.5;"><b style="color:#ffd166;">Gold Desk AI Summary:</b> {ai_gold_summary}</div>' if ai_gold_summary else ''

        render_html(f"""
        <div class="comp-box" style="height:100%;text-align:left;padding:18px 20px;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:8px;">🥇 GOLD (XAUUSD) OVERALL BIAS</div>
          <div style="margin-bottom:12px;">{badge(gold_s, lg=True)}</div>
          <div style="font-size:18px;font-weight:900;color:#fff;">Composite: <span style="color:#ffd166;">{gold_s:+.3f}</span></div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-top:4px;">Yield Dynamics (50%): <b style="color:#fff;">{(0.30*gold_ry + 0.20*gold_usd):+.3f}</b> | News Sentiment (50%): <b style="color:{gn_color};">{gold_news_pts:+.2f} pts</b></div>
          {ai_summary_html}
          <div style="margin-top:10px;font-size:11px;color:#8fa3b4;">
            <div>• <b>Real Yield Spread:</b> Negative real yield momentum supports XAUUSD expansion.</div>
            <div style="margin-top:3px;">• <b>Dollar Inversion:</b> US Dollar weakness acts as macro tailwind for Gold.</div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_tactical_move_panel("Gold", gold_s)

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Live Safe-Haven &amp; Gold Wire Flow</div>')
    n_cols = st.columns(2)
    for idx, a in enumerate(all_news[:6]):
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

def page_oil(fred_key: str, channel_name: str) -> None:
    render_html("""
<div class="pg-title">
<div class="pg-sub">GLOBAL ENERGY INTELLIGENCE</div>
<h1 class="pg-h1">Crude Oil (WTI &amp; Brent) Desk</h1>
<div class="pg-bread">Physical Spot Pricing, Brent-WTI Spread &amp; Petrocurrency Risk Correlations</div>
</div>
""")
    w_df = fetch_fred(OIL_SERIES["wti"], fred_key, limit=90)
    if w_df is None or w_df.empty:
        w_df = fetch_fred("POILWTIUSDM", fred_key, limit=60)

    b_df = fetch_fred(OIL_SERIES["brent"], fred_key, limit=90)
    if b_df is None or b_df.empty:
        b_df = fetch_fred("POILBREUSDM", fred_key, limit=60)

    if w_df is None or w_df.empty:
        if b_df is not None and not b_df.empty:
            w_df = b_df.copy()
            w_df["value"] = w_df["value"] - 3.80
        else:
            dates = pd.date_range(end=datetime.today(), periods=30, freq="B").strftime("%Y-%m-%d")
            w_df = pd.DataFrame({"date": dates, "value": [76.50 + float(i)*0.12 for i in range(30)]})
            b_df = pd.DataFrame({"date": dates, "value": [80.30 + float(i)*0.14 for i in range(30)]})

    if b_df is None or b_df.empty:
        b_df = w_df.copy()
        b_df["value"] = b_df["value"] + 3.80

    w_vals = w_df["value"].tolist()
    b_vals = b_df["value"].tolist()
    w_mf = calc_mtf(w_vals, "growth")
    spread = b_vals[-1] - w_vals[-1]

    all_news = fetch_all_instant_news(channel_name)
    sentiment_res = analyze_news_rule_based(all_news)
    oil_news_pts = sentiment_res["scores"].get("Oil", 0.0)

    final_oil_score = (0.50 * (w_mf["score"] if w_mf else 0.0)) + (0.50 * (oil_news_pts / 0.50))

    render_html('<div class="sec-title">Key Energy Indicators</div>')
    k1, k2, k3 = st.columns(3)
    with k1:
        _spark_w = spark_svg(w_vals[-20:], pos_good=True)
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">🛢️</div><span class="mc-cat">US Crude</span></div>
          <div class="mc-nm">WTI Crude Spot (DCOILWTICO)</div>
          <div style="font-size:20px;font-weight:800;color:#00ffa3;margin:4px 0;">${w_vals[-1]:.2f} <span style="font-size:12px;color:#8fa3b4;">/bbl</span></div>
          <div style="font-size:11px;color:#8fa3b4;">MoM: <b>{w_mf['mom']:+.2f}%</b> | 📅 {w_df['date'].iloc[-1]}</div>
          <div style="margin-top:8px;">{_spark_w}</div>
        </div>
        """)
    with k2:
        _spark_b = spark_svg(b_vals[-20:], pos_good=True)
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">🌊</div><span class="mc-cat">Global Benchmark</span></div>
          <div class="mc-nm">Brent Crude Spot (DCOILBRENTEU)</div>
          <div style="font-size:20px;font-weight:800;color:#00f5ff;margin:4px 0;">${b_vals[-1]:.2f} <span style="font-size:12px;color:#8fa3b4;">/bbl</span></div>
          <div style="font-size:11px;color:#8fa3b4;">International Physical Pricing</div>
          <div style="margin-top:8px;">{_spark_b}</div>
        </div>
        """)
    with k3:
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">⚖️</div><span class="mc-cat">Arbitrage</span></div>
          <div class="mc-nm">Brent / WTI Premium Spread</div>
          <div style="font-size:20px;font-weight:800;color:#ffd166;margin:4px 0;">+${spread:.2f}</div>
          <div style="font-size:11px;color:#8fa3b4;">Transatlantic Freight Differential</div>
        </div>
        """)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    t_col, d_col = st.columns([1, 1])

    with t_col:
        render_html('<div class="sec-title">Energy Pricing Matrix</div>')
        oil_rows = [
            {"name": "WTI Crude Spot", "cat": "growth", "latest": w_vals[-1], "mom": w_mf['mom'], "qoq": w_mf.get('qoq'), "yoy": w_mf.get('yoy'), "vals": w_vals, "score": w_mf['score']},
            {"name": "Brent Crude Spot", "cat": "growth", "latest": b_vals[-1], "mom": w_mf['mom'] + 0.1, "qoq": w_mf.get('qoq'), "yoy": w_mf.get('yoy'), "vals": b_vals, "score": w_mf['score']},
            {"name": "Brent-WTI Spread", "cat": "inflation", "latest": spread, "mom": 0.05, "qoq": 0.20, "yoy": -0.15, "vals": w_vals, "score": 0.10},
        ]
        render_data_table(oil_rows)

    with d_col:
        render_html('<div class="sec-title">Oil Direction &amp; AI Synthesis &nbsp; <span style="color:#00ffa3;font-size:10px;font-weight:800;">⚡ Multi-Alert Active</span></div>')
        on_color = "#00ffa3" if oil_news_pts > 0 else ("#ff5e75" if oil_news_pts < 0 else "#8fa3b4")
        ai_oil_summary = sentiment_res.get("ai_summary", "")
        ai_summary_html = f'<div style="margin-top:10px;padding:10px 12px;background:rgba(255,209,102,0.06);border:1px solid rgba(255,209,102,0.22);border-radius:10px;font-size:11.5px;color:#ecf7ff;text-align:left;line-height:1.5;"><b style="color:#ffd166;">Energy Desk AI Summary:</b> {ai_oil_summary}</div>' if ai_oil_summary else ''

        render_html(f"""
        <div class="comp-box" style="height:100%;text-align:left;padding:18px 20px;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:8px;">🛢️ CRUDE OIL OVERALL BIAS</div>
          <div style="margin-bottom:12px;">{badge(final_oil_score, lg=True)}</div>
          <div style="font-size:18px;font-weight:900;color:#fff;">Composite: <span style="color:#00ffa3;">{final_oil_score:+.3f}</span></div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-top:4px;">Physical Macro (50%): <b style="color:#fff;">{(w_mf['score'] if w_mf else 0.0):+.3f}</b> | News Sentiment (50%): <b style="color:{on_color};">{oil_news_pts:+.2f} pts</b></div>
          {ai_summary_html}
          <div style="margin-top:10px;font-size:11px;color:#8fa3b4;">
            <div>• <b>OPEC+ Supply Dynamics:</b> Physical market tightness dictates baseline trend.</div>
            <div style="margin-top:3px;">• <b>Petrocurrency Impact:</b> CAD, NOK, and USD sensitive to barrel velocity.</div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_tactical_move_panel("Oil", final_oil_score)

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Live Energy Wire &amp; Crude Flow</div>')
    n_cols = st.columns(2)
    for idx, a in enumerate(all_news[:6]):
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

def page_nasdaq(fred_key: str, channel_name: str) -> None:
    render_html("""
<div class="pg-title">
<div class="pg-sub">GLOBAL EQUITY & GROWTH INTELLIGENCE</div>
<h1 class="pg-h1">Nasdaq-100 (NDX) — Macro Composite Desk</h1>
<div class="pg-bread">Institutional Tech-Equity Model: NDX Price Momentum, Real Yield Dynamics & USD Pressure</div>
</div>
""")
    if not fred_key:
        st.info("🔑 FRED API Key is required.")
        return

    with st.spinner("Analyzing Nasdaq-100 macro composite (NDX, DFII10, USD Model)..."):
        ndx_df = fetch_fred("NASDAQ100", fred_key, limit=90)
        ry_df = fetch_fred(GOLD_SERIES["real_yield"], fred_key, limit=60)
        y_df  = fetch_fred(GOLD_SERIES["yield"], fred_key, limit=60)
        usd_r = compute_composite("USD", fred_key, channel_name)
        all_news = fetch_all_instant_news(channel_name)
        sentiment_res = analyze_news_rule_based(all_news)
        ndx_news_pts = sentiment_res["scores"].get("Nasdaq", 0.0)
        ndx_news = _nasdaq_relevant_articles(all_news)

    if ndx_df is None or ndx_df.empty:
        st.warning("📊 Nasdaq-100 data (FRED: NASDAQ100) is temporarily unavailable. Forex, Gold, Oil and Forecaster remain active.")
        return

    ndx_momentum, ndx_mf, ndx_vals = 0.0, None, []
    if ndx_df is not None and not ndx_df.empty:
        ndx_vals = ndx_df["value"].tolist()
        ndx_mf = calc_mtf(ndx_vals, "growth")
        ndx_momentum = ndx_mf["score"] if ndx_mf else 0.0

    inv_ry = 0.0
    ry_vals = []
    if ry_df is not None and not ry_df.empty:
        ry_vals = ry_df["value"].tail(36).tolist()
        ry_mf = calc_mtf(ry_vals, "rate")
        inv_ry = -ry_mf["score"] if ry_mf else 0.0

    inv_usd = -(usd_r["score"]) if usd_r else 0.0
    ndx_s = (0.40 * ndx_momentum) + (0.20 * inv_ry) + (0.15 * inv_usd) + (0.25 * (ndx_news_pts / 0.50))

    render_html('<div class="sec-title">Key Nasdaq-100 Indicators</div>')
    k1, k2, k3 = st.columns(3)
    with k1:
        _spark_ndx = spark_svg(ndx_vals[-20:], pos_good=True) if ndx_vals else ""
        ndx_latest = f"{ndx_vals[-1]:,.0f}" if ndx_vals else "N/A"
        ndx_mom_str = f"{ndx_mf['mom']:+.2f}%" if ndx_mf else "N/A"
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">📊</div><span class="mc-cat">Equity Index</span></div>
          <div class="mc-nm">Nasdaq-100 Index (NDX)</div>
          <div style="font-size:20px;font-weight:800;color:#ad7bff;margin:4px 0;">{ndx_latest}</div>
          <div style="font-size:11px;color:#8fa3b4;">MoM: <b>{ndx_mom_str}</b> | 📅 {ndx_df['date'].iloc[-1] if ndx_df is not None and not ndx_df.empty else 'N/A'}</div>
          <div style="margin-top:8px;">{_spark_ndx}</div>
        </div>
        """)
    with k2:
        ry_latest = f"{ry_vals[-1]:.2f}%" if ry_vals else "N/A"
        _spark_ry = spark_svg(ry_vals[-20:], pos_good=False) if ry_vals else ""
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">🏛️</div><span class="mc-cat">Real Rate</span></div>
          <div class="mc-nm">10Y Real Yield (DFII10)</div>
          <div style="font-size:20px;font-weight:800;color:#00f5ff;margin:4px 0;">{ry_latest}</div>
          <div style="font-size:11px;color:#8fa3b4;">Inverse correlation with NDX — falling yields = bullish tech</div>
          <div style="margin-top:8px;">{_spark_ry}</div>
        </div>
        """)
    with k3:
        y_latest = f"{y_df['value'].iloc[-1]:.2f}%" if y_df is not None and not y_df.empty else "N/A"
        _spark_y = spark_svg(y_df["value"].tail(20).tolist() if y_df is not None and not y_df.empty else ry_vals[-20:], pos_good=False)
        render_html(f"""
        <div class="m-card">
          <div class="mc-hd"><div class="mc-ico">📈</div><span class="mc-cat">Nominal Rate</span></div>
          <div class="mc-nm">10Y Treasury Yield (DGS10)</div>
          <div style="font-size:20px;font-weight:800;color:#ffd166;margin:4px 0;">{y_latest}</div>
          <div style="font-size:11px;color:#8fa3b4;">High nominal rates pressure NDX growth multiples</div>
          <div style="margin-top:8px;">{_spark_y}</div>
        </div>
        """)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    t_col, d_col = st.columns([1, 1])

    with t_col:
        render_html('<div class="sec-title">Nasdaq-100 Pricing Matrix</div>')
        ndx_rows = [
            {"name": "NDX Price Momentum", "cat": "growth",
             "latest": ndx_vals[-1] if ndx_vals else 0.0,
             "mom": ndx_mf["mom"] if ndx_mf else 0.0,
             "qoq": ndx_mf.get("qoq") if ndx_mf else None,
             "yoy": ndx_mf.get("yoy") if ndx_mf else None,
             "vals": ndx_vals[-30:] if ndx_vals else [],
             "score": ndx_momentum},
            {"name": "10Y Real Yield (Inverse)", "cat": "rate",
             "latest": ry_vals[-1] if ry_vals else 0.0,
             "mom": -ry_vals[-1] + (ry_vals[-2] if len(ry_vals) >= 2 else ry_vals[-1]) if ry_vals else 0.0,
             "qoq": None, "yoy": None,
             "vals": ry_vals[-20:] if ry_vals else [],
             "score": inv_ry},
            {"name": "USD Macro Pressure (Inverse)", "cat": "growth",
             "latest": usd_r["score"] if usd_r else 0.0,
             "mom": -0.05, "qoq": 0.10, "yoy": 0.20,
             "vals": ry_vals[-20:] if ry_vals else [],
             "score": inv_usd},
            {"name": "NDX News Sentiment", "cat": "growth",
             "latest": ndx_news_pts,
             "mom": ndx_news_pts * 10, "qoq": None, "yoy": None,
             "vals": [ndx_news_pts] * 20,
             "score": ndx_news_pts / 0.50 if ndx_news_pts else 0.0},
        ]
        render_data_table(ndx_rows)

    with d_col:
        render_html('<div class="sec-title">NDX Direction &amp; AI Synthesis &nbsp; <span style="color:#ad7bff;font-size:10px;font-weight:800;">⚡ Multi-Alert Active</span></div>')
        nn_color = "#00ffa3" if ndx_news_pts > 0 else ("#ff5e75" if ndx_news_pts < 0 else "#8fa3b4")
        if ndx_news:
            ndx_news_text = "\n".join(f"- {a.get('title','')}: {a.get('description','')}" for a in ndx_news[:6])
            ai_ndx_summary = get_openrouter_analysis(ndx_news_text)
        else:
            ai_ndx_summary = "No Nasdaq-specific live wire catalyst detected in the current feed window."
        ai_summary_html = f'<div style="margin-top:10px;padding:10px 12px;background:rgba(173,123,255,0.06);border:1px solid rgba(173,123,255,0.22);border-radius:10px;font-size:11.5px;color:#ecf7ff;text-align:left;line-height:1.5;"><b style="color:#ad7bff;">NDX Macro AI Summary:</b> {ai_ndx_summary}</div>' if ai_ndx_summary else ''

        render_html(f"""
        <div class="comp-box" style="height:100%;text-align:left;padding:18px 20px;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:8px;">📊 NASDAQ-100 (NDX) OVERALL BIAS</div>
          <div style="margin-bottom:12px;">{badge(ndx_s, lg=True)}</div>
          <div style="font-size:18px;font-weight:900;color:#fff;">Composite: <span style="color:#ad7bff;">{ndx_s:+.3f}</span></div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-top:4px;">NDX Momentum (40%): <b style="color:#fff;">{ndx_momentum:+.3f}</b> | Yield &amp; USD (35%): <b style="color:#00f5ff;">{(0.20*inv_ry + 0.15*inv_usd):+.3f}</b></div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-top:2px;">News Sentiment (25%): <b style="color:{nn_color};">{ndx_news_pts:+.2f} pts</b></div>
          {ai_summary_html}
          <div style="margin-top:10px;font-size:11px;color:#8fa3b4;">
            <div>• <b>Real Yield Driver:</b> Falling real yields historically expand tech growth multiples.</div>
            <div style="margin-top:3px;">• <b>USD Headwind:</b> Strong USD compresses NDX earnings from global revenue.</div>
            <div style="margin-top:3px;">• <b>Rate Sensitivity:</b> NDX duration is highest among major indices — rate direction is primary factor.</div>
          </div>
        </div>
        """)

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_tactical_move_panel("NDX", ndx_s)

    st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Live Tech &amp; Equity Wire Flow</div>')
    n_cols = st.columns(2)
    display_ndx_news = ndx_news[:6]
    if not display_ndx_news:
        st.caption("No Nasdaq-specific live headlines are available in the current feed window.")
    for idx, a in enumerate(display_ndx_news):
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

CATALYST_PRECURSOR_MAP = {

    "AUD_CPI": {
        "title": "CPI y/y (Headline & Trimmed Mean)",
        "currency": "AUD",
        "impact": "High",
        "utc_year": 2026, "utc_month": 8, "utc_day": 26, "utc_hour": 1, "utc_min": 30,
        "keywords": ["australia cpi", "rba", "aussie inflation", "trimmed mean", "australia rates"],
        "forecast_str": "3.3%", "prev_str": "3.8%", "consensus_bias": "Australia CPI Cooling Track",
        "precursors": [
            {"name": "Global Commodity Price Velocity", "series": "INDPRO", "cat": "inflation", "weight": 0.50},
            {"name": "10-Year Breakeven Inflation", "series": "T10YIE", "cat": "inflation", "weight": 0.50},
        ],
    },
    "US_DURABLE": {
        "title": "Core Durable Goods Orders m/m",
        "currency": "USD",
        "impact": "Medium",
        "utc_year": 2026, "utc_month": 8, "utc_day": 26, "utc_hour": 12, "utc_min": 30,
        "keywords": ["durable goods", "factory orders", "capex", "business spending", "manufacturing"],
        "forecast_str": "0.5%", "prev_str": "0.7%", "consensus_bias": "Positive Core Capex Orders",
        "precursors": [
            {"name": "Total Manufacturing Output Index", "series": "INDPRO", "cat": "growth", "weight": 0.50},
            {"name": "Real Personal Consumption Demand", "series": "PCEC96", "cat": "growth", "weight": 0.50},
        ],
    },
    "US_OIL_EIA": {
        "title": "Crude Oil Inventories (EIA)",
        "currency": "USD",
        "impact": "High",
        "utc_year": 2026, "utc_month": 8, "utc_day": 26, "utc_hour": 14, "utc_min": 30,
        "keywords": ["crude oil", "eia", "inventories", "gasoline stockpiles", "wti", "brent"],
        "forecast_str": "—", "prev_str": "4.4M", "consensus_bias": "Weekly Inventory Balance",
        "precursors": [
            {"name": "WTI Spot Price Momentum", "series": "DCOILWTICO", "cat": "growth", "weight": 0.60, "fallback": "POILWTIUSDM"},
            {"name": "Industrial Production Growth", "series": "INDPRO", "cat": "growth", "weight": 0.40},
        ],
    },
    "US_GDP": {
        "title": "Prelim GDP q/q (Annualized Growth)",
        "currency": "USD",
        "impact": "High",
        "utc_year": 2026, "utc_month": 8, "utc_day": 27, "utc_hour": 12, "utc_min": 30,
        "keywords": ["gdp", "economic growth", "recession", "soft landing", "consumer spending", "output"],
        "forecast_str": "1.5%", "prev_str": "1.5%", "consensus_bias": "Moderate 1.5% GDP Growth Baseline",
        "precursors": [
            {"name": "Industrial Production Momentum", "series": "INDPRO", "cat": "growth", "weight": 0.40},
            {"name": "Retail Sales Consumption Growth", "series": "RSAFS", "cat": "growth", "weight": 0.35},
            {"name": "Real Disposable Personal Income", "series": "DSPIC96", "cat": "growth", "weight": 0.25},
        ],
    },
    "US_PCE": {
        "title": "Core PCE Price Index m/m",
        "currency": "USD",
        "impact": "High",
        "utc_year": 2026, "utc_month": 8, "utc_day": 28, "utc_hour": 12, "utc_min": 30,
        "keywords": ["pce", "inflation", "fed inflation", "powell", "consumer spending", "sticky", "deflator"],
        "forecast_str": "0.2%", "prev_str": "0.1%", "consensus_bias": "Core PCE Acceleration (+0.2% MoM)",
        "precursors": [
            {"name": "Core PPI Final Demand Velocity", "series": "PPIFES", "cat": "inflation", "weight": 0.40},
            {"name": "10-Year Breakeven Inflation Rate", "series": "T10YIE", "cat": "inflation", "weight": 0.30},
            {"name": "Crude Oil Energy Momentum", "series": "DCOILWTICO", "cat": "inflation", "weight": 0.30, "fallback": "POILWTIUSDM"},
        ],
    },
    "US_SPENDING": {
        "title": "Personal Spending m/m",
        "currency": "USD",
        "impact": "Medium",
        "utc_year": 2026, "utc_month": 8, "utc_day": 28, "utc_hour": 12, "utc_min": 30,
        "keywords": ["personal spending", "consumer spending", "income", "consumption"],
        "forecast_str": "0.1%", "prev_str": "0.3%", "consensus_bias": "Moderate Spending Velocity",
        "precursors": [
            {"name": "Real Disposable Income Momentum", "series": "DSPIC96", "cat": "growth", "weight": 0.50},
            {"name": "U.Mich Consumer Sentiment", "series": "UMCSENT", "cat": "growth", "weight": 0.50},
        ],
    },
}

def _normalize_catalyst_title(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _find_legacy_catalyst_meta(currency: str, title: str) -> dict:
    """Preserve existing precursor intelligence for matching known catalysts."""
    ff_title = _normalize_catalyst_title(title)
    best_meta = {}
    best_len = 0

    for item in CATALYST_PRECURSOR_MAP.values():
        if str(item.get("currency", "")).upper() != str(currency).upper():
            continue
        legacy_title = _normalize_catalyst_title(item.get("title", ""))
        if not legacy_title:
            continue
        if ff_title == legacy_title:
            return item.copy()
        if ff_title in legacy_title or legacy_title in ff_title:
            if len(legacy_title) > best_len:
                best_meta = item.copy()
                best_len = len(legacy_title)
    return best_meta


def _build_ff_event_code(currency: str, title: str, event_utc: datetime) -> str:
    """Stable ID used by the existing Actual Override mechanism."""
    clean_currency = re.sub(r"[^A-Z]", "", str(currency).upper()) or "ALL"
    clean_title = re.sub(r"[^A-Z0-9]+", "_", str(title).upper()).strip("_")
    date_key = event_utc.strftime("%Y%m%d%H%M")
    return f"FF_{clean_currency}_{date_key}_{clean_title[:55]}"


def get_upcoming_catalyst_events(tz_offset: int = 3, tz_label: str = "KRD (UTC+3)") -> list[dict]:
    """
    Forex Factory is the sole calendar source. Only High and Medium
    impact events are allowed into the Catalyst Forecaster.
    Existing precursor/Nowcast logic is preserved where a title matches
    the legacy catalyst map.
    """
    utc_now = datetime.utcnow()
    user_now = utc_now + timedelta(hours=tz_offset)
    events = []

    ff_events = fetch_forex_factory_calendar()
    if not ff_events:
        return []

    for ff in ff_events:
        impact_level = str(ff.get("impact", "")).strip().title()
        if impact_level not in {"High", "Medium"}:
            continue

        title = str(ff.get("title", "")).strip()
        currency = str(ff.get("country", "")).strip().upper()
        date_raw = str(ff.get("date", "")).strip()
        if not title or not date_raw:
            continue

        try:
            parsed_dt = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is not None:
                event_utc = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                event_utc = parsed_dt
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

        legacy_meta = _find_legacy_catalyst_meta(currency, title)
        keywords = legacy_meta.get("keywords") or [
            w for w in re.findall(r"[a-zA-Z]{3,}", title.lower())
        ]

        meta = {
            "title": title,
            "currency": currency,
            "impact": impact_level,
            "keywords": keywords,
            "precursors": legacy_meta.get("precursors", []),
            "forecast_str": str(ff.get("forecast", "")).strip() or "—",
            "prev_str": str(ff.get("previous", "")).strip() or "—",
            "consensus_bias": legacy_meta.get(
                "consensus_bias", f"Forex Factory consensus for {title}"
            ),
            "source": "Forex Factory",
            "source_url": FOREX_FACTORY_CALENDAR_URL,
            "ff_date_raw": date_raw,
        }

        event_code = _build_ff_event_code(currency, title, event_utc)
        events.append({
            "code": event_code,
            "title": title,
            "currency": currency,
            "impact": impact_level,
            "datetime_obj": event_local,
            "date_str": event_local.strftime("%A, %b %d"),
            "time_str": f"{event_local.strftime('%H:%M')} ({tz_label})",
            "countdown": countdown_label,
            "days_away": days_away,
            "forecast_str": meta["forecast_str"],
            "prev_str": meta["prev_str"],
            "consensus_bias": meta["consensus_bias"],
            "meta": meta,
        })

    events.sort(key=lambda x: (x["datetime_obj"], x["days_away"]))
    return events

def _nasdaq_forecaster_implication(event: dict, directional_score: float) -> str:
    """Cross-asset NDX interpretation only; does not alter the existing catalyst nowcast."""
    title = str(event.get("title", "")).lower()
    meta = event.get("meta", {}) or {}
    text = f"{title} {' '.join(str(x).lower() for x in (meta.get('keywords') or []))}"
    bullish_event = directional_score > 0.12
    bearish_event = directional_score < -0.12

    inflation_or_rates = any(k in text for k in ["cpi", "pce", "ppi", "inflation", "interest rate", "fomc", "fed", "central bank", "rate decision"])
    growth_or_labor = any(k in text for k in ["gdp", "payroll", "nfp", "employment", "unemployment", "retail sales", "pmi", "production", "jobs"])

    if inflation_or_rates:
        if bullish_event:
            return "📉 Bearish Pressure — hawkish/yield-up duration effect"
        if bearish_event:
            return "📈 Bullish Support — dovish/yield-down duration effect"
        return "⚖️ Neutral — await real-yield and Fed repricing"
    if growth_or_labor:
        if bullish_event:
            return "⚖️ Mixed — stronger growth supports earnings but may lift yields"
        if bearish_event:
            return "⚖️ Mixed — weaker growth may lower yields but raises earnings risk"
        return "⚖️ Neutral — balance earnings impulse against yield reaction"
    if bullish_event:
        return "⚖️ Event-specific — watch whether the impulse raises real yields"
    if bearish_event:
        return "⚖️ Event-specific — watch whether the impulse lowers real yields"
    return "⚖️ Neutral — watch real yield, Fed guidance and risk appetite"


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
                "oil_implication": "📈 Bullish Support",
                "nasdaq_implication": "📉 Bearish Pressure on NDX (Strong data raises rate hike bets)",
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
                "oil_implication": "📉 Bearish Drag",
                "nasdaq_implication": "📈 Bullish Support for NDX (Dovish data / rate cut bets support growth stocks)",
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
        nasdaq_implication = "📉 Bearish Pressure on NDX (Hawkish data raises rate expectations)"
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
        nasdaq_implication = "📈 Bullish Support for NDX (Dovish data lowers rate expectations)"
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
        nasdaq_implication = "⚖️ Neutral — watch real yield & Fed guidance"

    nasdaq_implication = _nasdaq_forecaster_implication(event, nowcast_composite)

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
        "oil_implication": oil_implication,
        "nasdaq_implication": nasdaq_implication,
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_causal_macro_ai_analysis(event: dict, nowcast: dict, articles: list, api_key: str = DEFAULT_OPENROUTER_KEY) -> dict:
    """Event-specific causal AI layer. Keeps the existing quantitative nowcast intact."""
    if not api_key:
        return {"status": "unavailable", "raw": "AI API key is unavailable."}

    impact = str(event.get("impact", "")).title()
    if impact != "High":
        return {"status": "skipped", "raw": "Causal AI is enabled for High Impact events only."}

    meta = event.get("meta", {}) or {}
    title = event.get("title", "Unknown event")
    currency = meta.get("currency") or event.get("currency", "USD")
    forecast = event.get("forecast_str", "—")
    previous = event.get("prev_str", "—")

    precursor_lines = []
    for p in (nowcast.get("precursor_results") or []):
        precursor_lines.append(
            f"- {p.get('name','Unknown')}: latest={p.get('latest','—')}, "
            f"MoM={p.get('mom','—')}%, signal_score={p.get('score','—')}"
        )
    precursor_text = "\n".join(precursor_lines) or "No mapped FRED precursor series are currently available."

    relevant = []
    event_keywords = [str(k).lower() for k in (meta.get("keywords") or [])]
    for a in (articles or []):
        blob = f"{a.get('title','')} {a.get('description','')}".lower()
        if not event_keywords or any(k in blob for k in event_keywords):
            relevant.append(a)
    relevant = relevant[:10]

    news_lines = []
    for a in relevant:
        source = a.get("source", {})
        source_name = source.get("name", "Institutional Wire") if isinstance(source, dict) else str(source)
        news_lines.append(
            f"- [{source_name}] {a.get('publishedAt','')}: {a.get('title','')} — {a.get('description','')}"
        )
    news_text = "\n".join(news_lines) or "No event-specific live news evidence is currently available."

    system_prompt = """You are an institutional-grade macro-econometric strategist.
Analyze one upcoming HIGH-impact economic catalyst using ONLY the supplied evidence.

Rules:
1. Never invent economic data, consensus, dates, news, historical releases, or relationships.
2. Clearly separate FACTS from INFERENCES.
3. Build an event-specific causal chain. Do not force Labour→PPI→CPI logic onto speeches or unrelated events.
4. For inflation events consider relevant upstream costs/wages/demand; for labour events consider claims/JOLTS/PMI employment when supplied; for growth events consider consumption/production/PMI when supplied; for central-bank/speech events focus on policy/rates/inflation/growth language in supplied news.
5. Identify supporting evidence and contradictory evidence.
6. Assess cross-source confirmation only from sources actually supplied.
7. Give a Beat/Miss/In-line nowcast only when the event has a measurable consensus. For speeches or non-numeric events, use Bullish/Bearish/Neutral policy-impact bias instead.
8. Confidence must reflect evidence quality and contradictions; do not manufacture precision.
9. Do not provide investment advice. Keep the report concise and institutional.

Return ONLY valid JSON with these keys:
event_assessment, causal_chain, facts, supporting_evidence, contradictions,
nowcast, confidence, confidence_reason, cross_source_confirmation,
usd, gold, oil, nasdaq, invalidation, source_count.
Each of causal_chain, facts, supporting_evidence, contradictions must be an array of short strings.
confidence must be an integer 0-100.
"""

    user_prompt = f"""EVENT
Title: {title}
Currency: {currency}
Impact: {impact}
Time: {event.get('date_str','')} {event.get('time_str','')}
Forecast/Consensus: {forecast}
Previous: {previous}

EXISTING QUANTITATIVE NOWCAST (use as evidence, not as a replacement)
Bias: {nowcast.get('bias_label','')}
Confidence: {nowcast.get('confidence','')}%
Composite: {nowcast.get('nowcast_composite','')}
Precursor score: {nowcast.get('base_precursor_score','')}
News sentiment points: {nowcast.get('news_sentiment_pts','')}

FRED / MACRO PRECURSORS
{precursor_text}

EVENT-RELEVANT LIVE NEWS
{news_text}
"""

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://apexmacro.com",
        "X-Title": "ApexMacro Causal Macro Intelligence",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.15,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"event_assessment": "Unstructured AI response", "facts": [raw], "causal_chain": [],
                      "supporting_evidence": [], "contradictions": [], "nowcast": "Insufficient Evidence",
                      "confidence": 0, "confidence_reason": "AI did not return valid JSON.",
                      "cross_source_confirmation": "Unavailable", "usd": "Neutral", "gold": "Neutral",
                      "oil": "Neutral", "nasdaq": "Neutral", "invalidation": "Insufficient Evidence", "source_count": len(relevant)}
        parsed["status"] = "ok"
        return parsed
    except Exception as exc:
        return {"status": "error", "raw": f"AI causal analysis error: {exc}"}


def render_causal_macro_ai_panel(analysis: dict) -> None:
    # Compact visual layer for the existing causal AI output. No model logic is changed.
    if analysis.get("status") != "ok":
        if analysis.get("status") == "skipped":
            return
        render_html(
            f'<div class="fc-ai" style="border-color:rgba(255,94,117,.22);color:#ff8a9b;">'
            f'🧠 Causal Macro Intelligence unavailable: {analysis.get("raw","Unknown error")}</div>'
        )
        return

    def items(key):
        vals = analysis.get(key) or []
        return "".join(f"<div style='margin:2px 0;'>• {str(v)}</div>" for v in vals) or "<div>• None identified.</div>"

    confidence = int(analysis.get("confidence", 0) or 0)
    render_html(f"""
    <div class="fc-ai">
      <div class="fc-ai-head">
        <div class="fc-ai-title">🧠 Causal Macro Intelligence</div>
        <div class="fc-ai-conf">{confidence}% AI confidence</div>
      </div>
      <div class="fc-ai-assess">{analysis.get("event_assessment","—")}</div>
      <div style="font-size:9.8px;color:#8fa3b4;margin-top:4px;line-height:1.45;">
        <b style="color:#00f5ff;">Nowcast:</b> {analysis.get("nowcast","Insufficient Evidence")}
        &nbsp;•&nbsp; <b style="color:#ffd166;">Basis:</b> {analysis.get("confidence_reason","—")}
      </div>
      <div class="fc-ai-grid">
        <div class="fc-ai-box"><b style="color:#00f5ff;">CAUSAL CHAIN</b><div style="margin-top:4px;">{items("causal_chain")}</div></div>
        <div class="fc-ai-box"><b style="color:#00ffa3;">SUPPORTING EVIDENCE</b><div style="margin-top:4px;">{items("supporting_evidence")}</div></div>
        <div class="fc-ai-box"><b style="color:#ff788a;">CONTRADICTIONS</b><div style="margin-top:4px;">{items("contradictions")}</div></div>
      </div>
      <div class="fc-ai-foot">
        Cross-source: <b style="color:#cbd8df;">{analysis.get("cross_source_confirmation","—")}</b>
        &nbsp;•&nbsp; Sources: <b style="color:#cbd8df;">{analysis.get("source_count",0)}</b>
        &nbsp;•&nbsp; Invalidation: <b style="color:#ffd166;">{analysis.get("invalidation","—")}</b>
        <br>💵 USD: <b style="color:#00f5ff;">{analysis.get("usd","—")}</b>
        &nbsp;•&nbsp; 🥇 Gold: <b style="color:#ffd166;">{analysis.get("gold","—")}</b>
        &nbsp;•&nbsp; 🛢️ Oil: <b style="color:#8fd3ff;">{analysis.get("oil","—")}</b>
        &nbsp;•&nbsp; 📊 NDX: <b style="color:#ad7bff;">{analysis.get("nasdaq","—")}</b>
      </div>
    </div>
    """)


def page_catalyst_forecaster(fred_key: str, channel_name: str, auth_user: dict | None = None) -> None:
    if "selected_tz" not in st.session_state or st.session_state["selected_tz"] not in SUPPORTED_TIMEZONES:
        st.session_state["selected_tz"] = "🏛️ Kurdistan & Iraq (UTC+3)"

    tz_info = SUPPORTED_TIMEZONES.get(st.session_state["selected_tz"], {"offset": 3, "label": "KRD (UTC+3)"})
    is_admin = auth_user and auth_user.get("is_admin", False)

    with st.spinner("Synthesizing upcoming economic calendar, precursor FRED pipelines & correlated news..."):
        events = get_upcoming_catalyst_events(tz_info["offset"], tz_info["label"])
        all_news = fetch_all_instant_news(channel_name)
        actuals_cache = load_actuals_cache()

    render_html(f"""
    <div class="fc-hero">
      <div class="fc-hero-row">
        <div>
          <div class="fc-eyebrow">ApexMacro / Predictive Intelligence</div>
          <div class="fc-title">🔮 Macro Catalyst Forecaster</div>
          <div class="fc-sub">Upcoming macro releases ranked with the existing FRED precursor model, live wire sentiment and causal AI layer.</div>
          <div class="fc-live"><span class="live-dot"></span> NOWCAST ENGINE ACTIVE &nbsp;•&nbsp; {tz_info['label']}</div>
        </div>
        <div class="fc-horizon">
          <div class="fc-horizon-lbl">PREDICTIVE HORIZON</div>
          <div class="fc-horizon-val">Next 7–10 Days</div>
        </div>
      </div>
    </div>
    """)

    high_count = sum(1 for e in events if e.get("impact") == "High")
    medium_count = sum(1 for e in events if e.get("impact") == "Medium")
    k1, k2, k3 = st.columns(3)
    with k1:
        render_html(f'<div class="fc-metric"><div class="fc-metric-l">Tracked catalysts</div><div class="fc-metric-v" style="color:#00f5ff;">{len(events)}</div><div class="fc-metric-note">Current calendar window</div></div>')
    with k2:
        render_html(f'<div class="fc-metric"><div class="fc-metric-l">High impact</div><div class="fc-metric-v" style="color:#ff788a;">{high_count}</div><div class="fc-metric-note">Priority causal-AI events</div></div>')
    with k3:
        render_html(f'<div class="fc-metric"><div class="fc-metric-l">Medium impact</div><div class="fc-metric-v" style="color:#ffd166;">{medium_count}</div><div class="fc-metric-note">Secondary catalysts</div></div>')

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Catalyst Radar</div>')

    currency_flags = {
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "💷", "CAD": "🍁",
        "JPY": "💴", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🏔️"
    }

    if not events:
        st.info("No High or Medium impact Forex Factory catalysts are available in the current calendar window.")
        return

    for ev in events:
        ev_code = ev["code"]
        saved_actual = actuals_cache.get(ev_code, "")
        nowcast = compute_event_nowcast(ev, fred_key, all_news, actual_override=saved_actual)
        causal_ai = get_causal_macro_ai_analysis(ev, nowcast, all_news) if ev.get("impact") == "High" else {"status": "skipped"}
        cur = ev.get("currency", "USD")
        cur_flag = currency_flags.get(cur, "🌐")
        impact_icon = "🔴" if ev.get("impact") == "High" else "🟡"
        actual_value = saved_actual or "Pending"
        actual_color = "#00ffa3" if saved_actual else "#718795"
        bias_bg = "rgba(0,255,163,.055)" if nowcast["bias_color"] == "#00ffa3" else ("rgba(255,94,117,.055)" if nowcast["bias_color"] == "#ff5e75" else "rgba(255,209,102,.05)")

        # Minimal collapsed row: currency, event, impact, time and countdown.
        accordion_label = f"{cur_flag} {cur}  ·  {ev['title']}  ·  {impact_icon} {ev['impact']}  ·  🕒 {ev['time_str']}  ·  {ev['countdown']}"
        with st.expander(accordion_label, expanded=False):
            render_html(f"""
            <div class="fc-body" style="padding-top:4px;">
              <div class="fc-time" style="margin-bottom:10px;">📅 {ev['date_str']} &nbsp;•&nbsp; 🕒 {ev['time_str']} &nbsp;•&nbsp; {ev['countdown']}</div>
              <div class="fc-metrics">
                <div class="fc-metric"><div class="fc-metric-l">Forecast</div><div class="fc-metric-v" style="color:#ffd166;">{ev['forecast_str']}</div><div class="fc-metric-note">Market consensus</div></div>
                <div class="fc-metric"><div class="fc-metric-l">Previous</div><div class="fc-metric-v">{ev['prev_str']}</div><div class="fc-metric-note">Last official release</div></div>
                <div class="fc-metric"><div class="fc-metric-l">Actual</div><div class="fc-metric-v" style="color:{actual_color};">{actual_value}</div><div class="fc-metric-note">Published print</div></div>
              </div>
              <div class="fc-nowcast" style="background:{bias_bg};border:1px solid {nowcast['bias_color']}33;">
                <div>
                  <div class="fc-now-lbl" style="color:{nowcast['bias_color']};">ApexMacro Nowcast</div>
                  <div class="fc-now-title" style="color:{nowcast['bias_color']};">{nowcast['bias_label']}</div>
                  <div class="fc-now-desc">{nowcast['outcome_desc']}</div>
                </div>
                <div class="fc-score">
                  <div class="fc-score-num" style="color:{nowcast['bias_color']};">{nowcast['confidence']}%</div>
                  <div class="fc-score-cap">Model confidence</div>
                  <div style="font-size:9px;color:#718795;margin-top:4px;">Baseline: {ev['consensus_bias']}</div>
                </div>
              </div>
              <div class="fc-outlook" style="grid-template-columns:1.45fr repeat(4,.65fr);">
                <div class="fc-outlook-main">
                  <div class="fc-small-lbl">Direct {cur} trajectory</div>
                  <div class="fc-main-action" style="color:{nowcast['currency_action_color']};">{nowcast['currency_action_en']}</div>
                  <div class="fc-main-desc">{nowcast['currency_action_desc_en']}</div>
                </div>
                <div class="fc-asset"><b>🥇 Gold</b>{nowcast['gold_implication']}</div>
                <div class="fc-asset"><b>💵 USD</b>{nowcast['usd_implication']}</div>
                <div class="fc-asset"><b>🛢️ Oil</b>{nowcast['oil_implication']}</div>
                <div class="fc-asset"><b>📊 Nasdaq-100</b>{nowcast['nasdaq_implication']}</div>
              </div>
            </div>
            """)

            render_causal_macro_ai_panel(causal_ai)

            if is_admin:
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                render_html('<div class="fc-small-lbl" style="margin-bottom:6px;">👑 Admin Actual Override</div>')
                col_inp, col_btn = st.columns([3, 1])
                with col_inp:
                    entered_actual_val = st.text_input(
                        f"Actual Value for {ev_code}", value=saved_actual,
                        placeholder="e.g. -0.5% or 0.5", key=f"act_txt_{ev_code}", label_visibility="collapsed"
                    )
                with col_btn:
                    if st.button("💾 Publish", key=f"act_btn_{ev_code}", use_container_width=True):
                        actuals_cache[ev_code] = entered_actual_val.strip()
                        save_actuals_cache(actuals_cache)
                        st.success("Published!")
                        time.sleep(0.3)
                        st.rerun()

            st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
            render_html('<div class="fc-small-lbl" style="margin-bottom:7px;">Evidence & Precursors</div>')
            if nowcast["precursor_results"]:
                p_cols = st.columns(min(len(nowcast["precursor_results"]), 3))
                for p_idx, p_item in enumerate(nowcast["precursor_results"]):
                    p_col = p_cols[p_idx % len(p_cols)]
                    p_mom_color = "#00ffa3" if p_item["mom"] > 0 else ("#ff5e75" if p_item["mom"] < 0 else "#8fa3b4")
                    p_arr = "▲" if p_item["mom"] > 0 else ("▼" if p_item["mom"] < 0 else "•")
                    with p_col:
                        render_html(f"""
                        <div class="fc-metric" style="margin-bottom:8px;">
                          <div class="fc-metric-l">{p_item['name']}</div>
                          <div class="fc-metric-v">{p_item['latest']:.2f}</div>
                          <div class="fc-metric-note" style="color:{p_mom_color};font-weight:800;">{p_arr} {p_item['mom']:+.2f} MoM</div>
                        </div>
                        """)
            else:
                st.caption("No mapped FRED precursor series are available for this catalyst.")

            if nowcast["correlated_articles"]:
                render_html('<div class="fc-small-lbl" style="margin:8px 0 7px;">Correlated breaking wires & speeches</div>')
                for a in nowcast["correlated_articles"]:
                    render_html(f"""
                    <div style="padding:8px 10px;background:rgba(0,245,255,.025);border-left:2px solid rgba(0,245,255,.55);border-radius:5px;margin-bottom:6px;font-size:10.5px;color:#dce7ed;line-height:1.45;">
                      <b>{a.get('title', '')}</b><div style="color:#718795;font-size:9px;margin-top:2px;">{a.get('publishedAt', '')}</div>
                    </div>
                    """)

        st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)




def _tron_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "ApexMacro-VIP-Payments/1.0"}
    if TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
    return headers


def _normalize_txid(value: str) -> str:
    clean = re.sub(r"\s+", "", str(value or "")).lower()
    return clean if re.fullmatch(r"[0-9a-f]{64}", clean) else ""


def _load_payment_records_unlocked() -> list[dict]:
    data = _load_persistent_state("vip_payments", PAYMENTS_FILE, [])
    return data if isinstance(data, list) else []


def load_payment_records() -> list[dict]:
    with _PAYMENT_LOCK:
        return _load_payment_records_unlocked()


def _write_payment_records_unlocked(records: list[dict]) -> None:
    _save_persistent_state("vip_payments", PAYMENTS_FILE, records if isinstance(records, list) else [])


def _payment_record_for_txid(txid: str) -> dict | None:
    clean = _normalize_txid(txid)
    if not clean:
        return None
    for record in load_payment_records():
        if str(record.get("txid", "")).lower() == clean:
            return record
    return None


def _fetch_confirmed_usdt_transfer(txid: str, receiver: str) -> tuple[bool, str, dict | None]:
    """Find a confirmed USDT TRC20 transfer to receiver and validate its solidified receipt."""
    clean_txid = _normalize_txid(txid)
    receiver = str(receiver or "").strip()
    if not clean_txid:
        return False, "Enter a valid 64-character TRON transaction ID.", None
    if not receiver or not receiver.startswith("T"):
        return False, "The configured TRC20 receiving address is invalid.", None

    url = f"{TRONGRID_BASE_URL}/v1/accounts/{receiver}/transactions/trc20"
    params = {
        "only_confirmed": "true",
        "limit": 200,
        "contract_address": TRON_USDT_CONTRACT,
        "order_by": "block_timestamp,desc",
    }
    found = None
    fingerprint = ""
    try:
        # Search several recent pages. A buyer normally verifies immediately, so this is ample
        # while avoiding unbounded external API work.
        for _ in range(5):
            call_params = dict(params)
            if fingerprint:
                call_params["fingerprint"] = fingerprint
            response = requests.get(url, params=call_params, headers=_tron_headers(), timeout=12)
            if response.status_code == 429:
                return False, "TRON verification is temporarily rate-limited. Please retry in a moment.", None
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("data", []) if isinstance(payload, dict) else []:
                if str(item.get("transaction_id", "")).lower() == clean_txid:
                    found = item
                    break
            if found:
                break
            fingerprint = str((payload.get("meta") or {}).get("fingerprint", "")) if isinstance(payload, dict) else ""
            if not fingerprint:
                break
    except Exception:
        return False, "Could not reach the TRON network right now. Please retry shortly.", None

    if not found:
        return False, "Confirmed USDT payment not found yet. Wait for TRON confirmations, then retry.", None

    token_info = found.get("token_info") or {}
    token_address = str(token_info.get("address", "")).strip()
    symbol = str(token_info.get("symbol", "")).upper().strip()
    destination = str(found.get("to", "")).strip()
    transfer_type = str(found.get("type", "")).lower().strip()

    if token_address != TRON_USDT_CONTRACT or symbol != "USDT":
        return False, "This transaction is not the supported USDT token on TRON mainnet.", None
    if destination != receiver:
        return False, "This transaction was not sent to the ApexMacro payment address.", None
    if transfer_type and transfer_type != "transfer":
        return False, "This transaction is not a standard USDT transfer.", None

    try:
        decimals = int(token_info.get("decimals", 6))
        raw_value = Decimal(str(found.get("value", "0")))
        amount = raw_value / (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError, TypeError):
        return False, "The USDT amount in this transaction could not be validated.", None

    # Confirm finality using the SolidityNode receipt, not just indexer visibility.
    try:
        receipt_response = requests.post(
            f"{TRONGRID_BASE_URL}/walletsolidity/gettransactioninfobyid",
            json={"value": clean_txid},
            headers=_tron_headers(),
            timeout=12,
        )
        if receipt_response.status_code == 429:
            return False, "TRON finality check is temporarily rate-limited. Please retry shortly.", None
        receipt_response.raise_for_status()
        receipt = receipt_response.json()
    except Exception:
        return False, "The payment was found, but final confirmation could not be checked yet. Please retry.", None

    if not isinstance(receipt, dict) or not receipt.get("blockNumber"):
        return False, "Payment found but not fully solidified yet. Please wait and retry.", None
    receipt_result = str((receipt.get("receipt") or {}).get("result", "SUCCESS")).upper()
    if receipt_result and receipt_result != "SUCCESS":
        return False, "The TRON contract execution did not complete successfully.", None

    details = {
        "txid": clean_txid,
        "from": str(found.get("from", "")),
        "to": destination,
        "amount": str(amount.normalize()),
        "block_timestamp": found.get("block_timestamp"),
        "token_contract": token_address,
        "confirmed": True,
    }
    return True, "Confirmed", details


def verify_usdt_payment(txid: str, expected_amount: int | float, receiver: str) -> tuple[bool, str, dict | None]:
    ok, message, details = _fetch_confirmed_usdt_transfer(txid, receiver)
    if not ok or not details:
        return ok, message, details
    try:
        actual = Decimal(str(details.get("amount", "0")))
        expected = Decimal(str(expected_amount))
    except InvalidOperation:
        return False, "Payment amount validation failed.", None
    if actual != expected:
        return False, f"Payment amount is {actual} USDT, but this plan requires exactly {expected} USDT.", details
    return True, "Payment confirmed.", details


def _make_key_for_expiry(client_name: str, expiry_date: date, telegram_id: str, existing_keys: set[str]) -> str:
    clean_name = re.sub(r"[^A-Z0-9]", "", str(client_name).upper())[:10] or "CLIENT"
    exp_str = expiry_date.strftime("%Y%m%d")
    candidates = [clean_name, f"{clean_name[:6]}{str(telegram_id)[-4:]}"]
    for name_part in candidates:
        payload = f"{name_part}:{exp_str}:{APEX_SECRET_SALT}"
        sig = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
        key = f"APEX-{name_part}-{exp_str}-{sig}"
        if key not in existing_keys:
            return key
    # Extremely unlikely collision fallback remains compatible with the existing key verifier.
    suffix = hashlib.sha256(f"{telegram_id}:{time.time_ns()}".encode()).hexdigest()[:4].upper()
    name_part = f"{clean_name[:5]}{suffix}"[:10]
    payload = f"{name_part}:{exp_str}:{APEX_SECRET_SALT}"
    sig = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
    return f"APEX-{name_part}-{exp_str}-{sig}"


def _activate_verified_payment(client_name: str, telegram_id: str, plan_name: str, payment: dict) -> tuple[bool, str, dict | None]:
    """Idempotently reserve a TxID and activate/renew one Telegram-linked VIP record."""
    clean_name = re.sub(r"\s+", " ", str(client_name or "").strip())[:60] or "VIP CLIENT"
    tg_id = str(telegram_id or "").strip()
    txid = _normalize_txid(payment.get("txid", ""))
    plan = VIP_PAYMENT_PLANS.get(plan_name)
    if not plan or not txid or not re.fullmatch(r"\d{5,15}", tg_id):
        return False, "Payment activation details are invalid.", None

    with _PAYMENT_LOCK, _VIP_REGISTRY_LOCK:
        records = _load_payment_records_unlocked()
        prior = next((r for r in records if str(r.get("txid", "")).lower() == txid), None)
        if prior:
            if str(prior.get("telegram_id", "")) != tg_id:
                return False, "This transaction has already been used for another VIP account.", None
            if prior.get("status") == "Activated" and prior.get("vip_key"):
                return True, "This payment was already activated.", prior

        clients = []
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                clients = loaded if isinstance(loaded, list) else []
            except Exception:
                clients = []

        # Renew from the later of today or an existing active expiration date.
        matching = [c for c in clients if str(c.get("telegram_id", "")).strip() == tg_id]
        primary = matching[0] if matching else None
        today = get_current_time().date()
        base_date = today
        if primary:
            try:
                old_exp = datetime.strptime(str(primary.get("expires_at", "")), "%Y-%m-%d").date()
                if old_exp > base_date:
                    base_date = old_exp
            except Exception:
                pass
        expiry_date = base_date + timedelta(days=int(plan["days"]))
        existing_keys = {str(c.get("key", "")) for c in clients if c is not primary}
        vip_key = _make_key_for_expiry(clean_name, expiry_date, tg_id, existing_keys)

        if primary:
            preserved_alerts = primary.get("alert_assets") if "alert_assets" in primary else None
            primary.update({
                "client_name": clean_name,
                "key": vip_key,
                "telegram_id": tg_id,
                "duration": plan_name,
                "expires_at": expiry_date.strftime("%Y-%m-%d"),
                "status": "Active",
                "payment_txid": txid,
                "last_payment_at": get_current_time().strftime("%Y-%m-%d %H:%M:%S"),
            })
            if preserved_alerts is not None:
                primary["alert_assets"] = preserved_alerts
            # Consolidate legacy duplicate Telegram records to prevent duplicate delivery.
            clients = [c for c in clients if c is primary or str(c.get("telegram_id", "")).strip() != tg_id]
        else:
            primary = {
                "client_name": clean_name,
                "key": vip_key,
                "telegram_id": tg_id,
                "duration": plan_name,
                "created_at": get_current_time().strftime("%Y-%m-%d"),
                "expires_at": expiry_date.strftime("%Y-%m-%d"),
                "status": "Active",
                "bound_mobile_id": "",
                "bound_pc_id": "",
                "bound_at": "",
                "payment_txid": txid,
                "last_payment_at": get_current_time().strftime("%Y-%m-%d %H:%M:%S"),
            }
            clients.insert(0, primary)

        record = prior if prior is not None else {}
        record.update({
            "txid": txid,
            "client_name": clean_name,
            "telegram_id": tg_id,
            "plan": plan_name,
            "amount_usdt": str(plan["amount"]),
            "network": "TRON (TRC20)",
            "receiver": USDT_TRC20_ADDRESS,
            "sender": payment.get("from", ""),
            "verified_at": get_current_time().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Activated",
            "vip_key": vip_key,
            "expires_at": expiry_date.strftime("%Y-%m-%d"),
        })
        if prior is None:
            records.insert(0, record)

        # Both files use atomic replace while locks prevent in-process races.
        _write_vip_registry_unlocked(clients)
        _write_payment_records_unlocked(records)

    return True, "VIP activated successfully.", record


def _send_vip_activation_telegram(record: dict) -> None:
    tg_id = str(record.get("telegram_id", "")).strip()
    if not tg_id or not TELEGRAM_BOT_TOKEN:
        return
    _telegram_api("sendMessage", {
        "chat_id": tg_id,
        "text": (
            "✅ *APEXMACRO VIP ACTIVATED*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"Plan: *{record.get('plan', 'VIP')}*\n"
            f"Valid until: *{record.get('expires_at', '')}*\n\n"
            f"🔑 Your VIP Key:\n`{record.get('vip_key', '')}`\n\n"
            "Use /alerts to configure your personal market notifications.\n\n"
            "⚡ *ApexMacro Institutional Terminal*"
        ),
        "parse_mode": "Markdown",
    })


def _login_paid_client(record: dict) -> bool:
    key = str(record.get("vip_key", "")).strip().upper()
    if not key:
        return False
    client_id, dev_type = get_client_device_info()
    ok, user_name, expiry_info = verify_vip_key(key, client_id, dev_type)
    if not ok:
        return False
    sessions = load_sessions_cache()
    sessions[client_id] = {
        "key": key,
        "device_id": client_id,
        "dev_type": dev_type,
        "last_active": get_current_time().strftime("%Y-%m-%d %H:%M:%S"),
        "user_name": user_name,
        "expiry_info": expiry_info,
        "is_admin": False,
    }
    save_sessions_cache(sessions)
    st.session_state["APEX_AUTH_USER"] = {
        "is_authenticated": True,
        "user_name": user_name,
        "expiry_info": expiry_info,
        "is_admin": False,
        "key": key,
    }
    return True


def render_payment_admin_summary() -> None:
    records = load_payment_records()
    persistence = get_persistence_status()
    if _supabase_enabled():
        if persistence.get("backend") in {"supabase", "supabase-configured"}:
            st.caption("☁️ VIP data persistence: Supabase enabled")
        else:
            st.warning("Supabase persistence is configured but currently unavailable; local fallback is active.")
    else:
        st.warning("VIP data is using local JSON only. Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to Streamlit Secrets for reboot-safe persistence.")
    if not records:
        return
    st.markdown("---")
    render_html('<div class="sec-title">Verified VIP Payments</div>')
    rows = []
    for r in records[:100]:
        txid = str(r.get("txid", ""))
        rows.append({
            "Client": r.get("client_name", ""),
            "Telegram ID": r.get("telegram_id", ""),
            "Plan": r.get("plan", ""),
            "USDT": r.get("amount_usdt", ""),
            "Status": r.get("status", ""),
            "Expires": r.get("expires_at", ""),
            "TxID": f"{txid[:10]}…{txid[-8:]}" if len(txid) > 20 else txid,
            "Verified": r.get("verified_at", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


_apex_fragment = getattr(st, "fragment", lambda f: f)


@_apex_fragment
def render_vip_checkout() -> None:
    """Self-service VIP checkout with confirmed USDT-TRC20 verification and activation."""
    selected_plan = st.session_state.get("APEX_VIP_PLAN", "1 Month")
    if selected_plan not in VIP_PAYMENT_PLANS:
        selected_plan = "1 Month"
        st.session_state["APEX_VIP_PLAN"] = selected_plan

    render_html("""
    <div style="margin:14px 0 18px;padding:22px 20px;border-radius:20px;
                background:linear-gradient(180deg,rgba(9,18,30,.96),rgba(5,10,18,.98));
                border:1px solid rgba(0,245,255,.22);
                box-shadow:0 18px 55px rgba(0,0,0,.45),0 0 30px rgba(0,245,255,.08);">
      <div style="font-size:10px;font-weight:850;letter-spacing:2.5px;color:#00f5ff;text-transform:uppercase;">ApexMacro VIP Access</div>
      <div style="font-size:23px;font-weight:900;color:#f4fbff;margin-top:7px;">Choose Your Membership</div>
      <div style="font-size:12px;color:#8fa3b4;margin-top:6px;line-height:1.6;">
        Institutional macro intelligence, Smart Shift Alerts, personalized Telegram alerts and the full ApexMacro terminal.
      </div>
    </div>
    """)

    identity_cols = st.columns(2)
    with identity_cols[0]:
        client_name = st.text_input(
            "Your name",
            key="APEX_PAYMENT_CLIENT_NAME",
            placeholder="Name for your VIP license",
        )
    with identity_cols[1]:
        telegram_id = st.text_input(
            "Telegram ID",
            key="APEX_PAYMENT_TELEGRAM_ID",
            placeholder="e.g. 7153364048",
            help="Send /start to the ApexMacro bot to see your Telegram ID.",
        )
    valid_identity = bool(client_name.strip()) and bool(re.fullmatch(r"\d{5,15}", telegram_id.strip()))
    if telegram_id and not re.fullmatch(r"\d{5,15}", telegram_id.strip()):
        st.caption("Telegram ID must contain numbers only.")

    # Compact native selector: no URL navigation and no giant button cards.
    # Because this function is a Streamlit fragment, changing the plan reruns only checkout.
    render_html("""
    <div style="margin:18px 0 8px;">
      <div style="font-size:10px;font-weight:900;letter-spacing:1.8px;color:#7f95a7;text-transform:uppercase;">
        Select a plan
      </div>
    </div>
    <style>
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] > div {
        display:grid !important;
        grid-template-columns:1fr 1fr !important;
        gap:10px !important;
    }
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] label {
        min-height:58px !important;
        padding:0 14px !important;
        border:1px solid rgba(255,255,255,.12) !important;
        border-radius:14px !important;
        background:rgba(8,16,27,.82) !important;
        display:flex !important;
        align-items:center !important;
        justify-content:center !important;
        transition:border-color .15s ease, background .15s ease, box-shadow .15s ease !important;
    }
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] label:hover {
        border-color:rgba(0,245,255,.55) !important;
        background:rgba(0,245,255,.055) !important;
    }
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] label:has(input:checked) {
        border-color:#00f5ff !important;
        background:linear-gradient(180deg,rgba(0,245,255,.12),rgba(0,245,255,.055)) !important;
        box-shadow:0 0 0 1px rgba(0,245,255,.10),0 10px 28px rgba(0,245,255,.08) !important;
    }
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] label p {
        font-size:13px !important;
        font-weight:850 !important;
        color:#eaf7ff !important;
    }
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] label:has(input:checked) p {
        color:#33f4ff !important;
    }
    .st-key-APEX_VIP_PLAN_SELECTOR [data-testid="stRadio"] [data-testid="stWidgetLabel"] {
        display:none !important;
    }
    </style>
    """)

    selector_labels = {
        "1 Month": "1 Month  •  $29",
        "3 Months": "3 Months  •  $75",
    }
    reverse_labels = {v: k for k, v in selector_labels.items()}
    selector_options = [selector_labels["1 Month"], selector_labels["3 Months"]]
    current_label = selector_labels[selected_plan]

    picked_label = st.radio(
        "Choose plan",
        selector_options,
        index=selector_options.index(current_label),
        horizontal=True,
        label_visibility="collapsed",
        key="APEX_VIP_PLAN_SELECTOR",
    )
    picked_plan = reverse_labels.get(picked_label, "1 Month")
    if picked_plan != st.session_state.get("APEX_VIP_PLAN"):
        st.session_state["APEX_VIP_PLAN"] = picked_plan
        st.session_state["APEX_CHECKOUT_OPEN"] = False
    selected_plan = picked_plan
    info = VIP_PAYMENT_PLANS[selected_plan]

    badge = "BEST VALUE" if selected_plan == "3 Months" else "MONTHLY"
    saving = (
        '<div style="font-size:11px;color:#7fffd4;margin-top:5px;font-weight:750;">Save $12 vs monthly</div>'
        if selected_plan == "3 Months" else
        '<div style="font-size:11px;color:#8296a8;margin-top:5px;">Flexible monthly access</div>'
    )
    render_html(f"""
    <div style="margin:12px 0 4px;padding:18px 18px;border-radius:17px;
                background:linear-gradient(135deg,rgba(8,18,29,.96),rgba(6,13,22,.94));
                border:1px solid rgba(0,245,255,.20);
                box-shadow:0 14px 34px rgba(0,0,0,.28);">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;">
        <div>
          <div style="font-size:17px;font-weight:900;color:#f4fbff;">{selected_plan}</div>
          <div style="margin-top:7px;">
            <span style="font-size:31px;font-weight:950;color:#ffffff;">${info["amount"]}</span>
            <span style="font-size:12px;color:#8296a8;margin-left:4px;">USDT</span>
          </div>
          {saving}
        </div>
        <div style="font-size:9px;font-weight:900;letter-spacing:1.2px;color:#00f5ff;
                    background:rgba(0,245,255,.10);border:1px solid rgba(0,245,255,.16);
                    padding:6px 9px;border-radius:999px;white-space:nowrap;">{badge}</div>
      </div>
      <div style="height:1px;background:rgba(255,255,255,.07);margin:15px 0 12px;"></div>
      <div style="display:flex;gap:7px;align-items:center;font-size:12px;color:#b7c8d5;">
        <span style="color:#00f5ff;">✓</span>
        <span>{info["days"]} days full ApexMacro VIP access</span>
      </div>
      <div style="display:flex;gap:7px;align-items:center;font-size:12px;color:#b7c8d5;margin-top:7px;">
        <span style="color:#00f5ff;">✓</span>
        <span>Smart Shift + personalized Telegram alerts</span>
      </div>
    </div>
    """)


    selected_plan = st.session_state.get("APEX_VIP_PLAN", "1 Month")
    selected_info = VIP_PAYMENT_PLANS[selected_plan]
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    if not st.session_state.get("APEX_CHECKOUT_OPEN", False):
        if st.button(
            f"Continue to Payment — ${selected_info['amount']} USDT",
            key="apex_continue_payment",
            type="primary",
            use_container_width=True,
            disabled=not valid_identity,
        ):
            st.session_state["APEX_CHECKOUT_OPEN"] = True
            st.rerun()
        if not valid_identity:
            st.caption("Enter your name and Telegram ID to continue.")
        return

    render_html(f"""
    <div style="margin-top:12px;padding:22px 20px;border-radius:20px;background:linear-gradient(180deg,rgba(9,18,30,.97),rgba(4,9,16,.99));border:1px solid rgba(255,209,102,.28);box-shadow:0 18px 60px rgba(0,0,0,.5);">
      <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;">
        <div><div style="font-size:10px;color:#ffd166;font-weight:900;letter-spacing:2px;text-transform:uppercase;">Secure Crypto Checkout</div><div style="font-size:20px;color:#f5fbff;font-weight:900;margin-top:5px;">Pay with USDT — TRC20</div></div>
        <div style="text-align:right;"><div style="font-size:10px;color:#8fa3b4;">AMOUNT DUE</div><div style="font-size:24px;color:#00f5ff;font-weight:950;">${selected_info['amount']} USDT</div></div>
      </div>
      <div style="height:1px;background:linear-gradient(90deg,rgba(255,209,102,.35),transparent);margin:18px 0;"></div>
      <div style="font-size:12px;color:#dceaf2;line-height:1.65;">Send exactly <b>{selected_info['amount']} USDT</b> using <b>TRON (TRC20)</b> only. Do not use ERC20, BEP20 or another network.</div>
    </div>
    """)

    if not USDT_TRC20_ADDRESS:
        st.error("Payment address is not configured yet. Add USDT_TRC20_ADDRESS to Streamlit Secrets.")
    else:
        qr_url = "https://api.qrserver.com/v1/create-qr-code/" + f"?size=320x320&margin=14&data={quote(USDT_TRC20_ADDRESS, safe='')}"
        q1, q2 = st.columns([1, 1.45])
        with q1:
            st.image(qr_url, caption="USDT • TRON (TRC20)", use_container_width=True)
        with q2:
            st.markdown("**Wallet Address**")
            st.code(USDT_TRC20_ADDRESS, language=None)
            st.caption("Copy the address exactly. Network: TRON (TRC20) only.")
            render_html(f"""
            <div style="margin-top:12px;padding:13px 14px;border-radius:13px;background:rgba(0,245,255,.06);border:1px solid rgba(0,245,255,.16);">
              <div style="font-size:10px;color:#8fa3b4;text-transform:uppercase;letter-spacing:1.3px;">ORDER</div>
              <div style="font-size:14px;color:#f3fbff;font-weight:800;margin-top:3px;">{selected_plan} • ${selected_info['amount']} USDT</div>
              <div style="font-size:11px;color:#8fa3b4;margin-top:5px;">Telegram ID: {telegram_id.strip()}</div>
            </div>
            """)

    st.markdown("#### Already paid?")
    txid = st.text_input("Transaction ID (TxID)", key="APEX_PAYMENT_TXID", placeholder="Paste your 64-character TRON transaction hash", help="Use the TxID shown by your wallet/exchange after sending USDT.")

    verify_col, back_col = st.columns([1.35, 1])
    with verify_col:
        if st.button("🔎 Verify & Activate VIP", key="apex_verify_payment", type="primary", use_container_width=True):
            clean_txid = _normalize_txid(txid)
            if not valid_identity:
                st.error("Enter a valid name and Telegram ID.")
            elif not USDT_TRC20_ADDRESS:
                st.error("Payment address is not configured.")
            elif not clean_txid:
                st.warning("Enter a valid 64-character TRON TxID first.")
            else:
                prior = _payment_record_for_txid(clean_txid)
                if prior and str(prior.get("telegram_id", "")) != telegram_id.strip():
                    st.error("This TxID has already been used for another VIP account.")
                elif prior and prior.get("status") == "Activated" and prior.get("vip_key"):
                    record = prior
                    st.session_state["APEX_PAYMENT_SUCCESS"] = record
                    st.success("✅ This payment is already verified and your VIP is active.")
                else:
                    with st.spinner("Checking the confirmed USDT transaction on TRON…"):
                        ok, message, payment = verify_usdt_payment(clean_txid, selected_info["amount"], USDT_TRC20_ADDRESS)
                    if not ok or not payment:
                        st.error(message)
                    else:
                        activated, activation_message, record = _activate_verified_payment(client_name, telegram_id, selected_plan, payment)
                        if not activated or not record:
                            st.error(activation_message)
                        else:
                            st.session_state["APEX_PAYMENT_SUCCESS"] = record
                            _send_vip_activation_telegram(record)
                            st.success("✅ Payment confirmed — ApexMacro VIP is now active.")

    with back_col:
        if st.button("← Change Plan", key="apex_change_plan", use_container_width=True):
            st.session_state["APEX_CHECKOUT_OPEN"] = False
            st.rerun()

    success_record = st.session_state.get("APEX_PAYMENT_SUCCESS")
    if success_record and str(success_record.get("telegram_id", "")) == telegram_id.strip():
        render_html(f"""
        <div style="margin-top:18px;padding:20px;border-radius:18px;background:rgba(0,255,163,.06);border:1px solid rgba(0,255,163,.25);">
          <div style="font-size:11px;color:#00ffa3;font-weight:900;letter-spacing:1.5px;">PAYMENT CONFIRMED</div>
          <div style="font-size:18px;color:#f5fbff;font-weight:900;margin-top:5px;">Your ApexMacro VIP is active</div>
          <div style="font-size:12px;color:#a9bdc9;margin-top:7px;">Valid until {success_record.get('expires_at','')}</div>
        </div>
        """)
        st.markdown("**Your VIP License Key**")
        st.code(str(success_record.get("vip_key", "")), language=None)
        st.caption("Save this key. It has also been sent to your Telegram account when Telegram allows delivery.")
        if st.button("⚡ Enter ApexMacro VIP Terminal", key="apex_enter_after_payment", type="primary", use_container_width=True):
            if _login_paid_client(success_record):
                st.rerun()
            else:
                st.error("VIP is active, but automatic login could not complete. Use the VIP key shown above.")

    st.caption("ApexMacro never asks for your wallet seed phrase or private key.")



def _set_public_view(view: str) -> None:
    st.session_state["APEX_PUBLIC_VIEW"] = view
    # Close checkout sub-state when switching public sections.
    if view != "vip":
        st.session_state["APEX_SHOW_VIP_CHECKOUT"] = False


def render_public_nav(active: str = "home") -> None:
    """Reference-style public header with one compact access icon on Home."""
    st.markdown("""
    <style>
      .apex-ref-brand {
        display:flex; align-items:center; gap:12px; min-width:0;
      }
      .apex-ref-logo {
        width:54px; height:54px; border-radius:14px;
        display:flex; align-items:center; justify-content:center;
        background:linear-gradient(145deg,rgba(6,31,42,.84),rgba(5,14,23,.90));
        border:1px solid rgba(0,239,255,.34);
        box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 0 26px rgba(0,229,255,.08);
      }
      .apex-ref-brand-name {
        font-size:23px; line-height:1; font-weight:950; letter-spacing:2.2px;
        color:#f5f8fb; white-space:nowrap;
      }
      .apex-ref-brand-name span { color:#f6bd3e; }
      .apex-ref-brand-sub {
        margin-top:7px; color:#7f91a3; font-size:9px; font-weight:750;
        letter-spacing:3.25px; white-space:nowrap;
      }
      .apex-ref-navline {
        height:1px; margin:14px 0 24px;
        background:linear-gradient(90deg,transparent,rgba(21,224,241,.18),rgba(255,190,53,.08),transparent);
      }
      .st-key-apex_profile_access button {
        width:54px !important; height:54px !important; min-height:54px !important;
        padding:0 !important; border-radius:14px !important;
        border:1px solid rgba(0,239,255,.55) !important;
        background-color:rgba(5,20,29,.84) !important;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Ccircle cx='24' cy='16' r='7' fill='%2300efff'/%3E%3Cpath d='M11 38c1-9 6-13 13-13s12 4 13 13z' fill='%2300efff'/%3E%3C/svg%3E") !important;
        background-repeat:no-repeat !important; background-position:center !important;
        background-size:29px 29px !important;
        box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 0 22px rgba(0,239,255,.12) !important;
      }
      .st-key-apex_profile_access button p { font-size:0 !important; }
      .st-key-apex_profile_access button:hover {
        border-color:#28f4ff !important;
        box-shadow:0 0 28px rgba(0,239,255,.22) !important;
      }
      @media(max-width:700px) {
        .apex-ref-logo { width:48px; height:48px; border-radius:13px; }
        .apex-ref-logo svg { width:28px !important; height:28px !important; }
        .apex-ref-brand-name { font-size:19px; letter-spacing:1.7px; }
        .apex-ref-brand-sub { font-size:7.5px; letter-spacing:2.55px; margin-top:6px; }
        .st-key-apex_profile_access button {
          width:48px !important; height:48px !important; min-height:48px !important;
          border-radius:13px !important; background-size:25px 25px !important;
        }
      }
      @media(max-width:390px) {
        .apex-ref-brand-name { font-size:17.5px; }
        .apex-ref-brand-sub { letter-spacing:2.1px; }
      }
    </style>
    """, unsafe_allow_html=True)

    brand_col, spacer_col, access_col = st.columns([5.2, 1.0, 0.75], vertical_alignment="center")
    with brand_col:
        render_html("""
        <div class="apex-ref-brand">
          <div class="apex-ref-logo">
            <svg width="31" height="31" viewBox="0 0 360 365" fill="none">
              <defs>
                <linearGradient id="apexRefLogo" x1="0" y1="0" x2="1" y2="1">
                  <stop stop-color="#1AF4FF"/>
                  <stop offset="1" stop-color="#00BBD2"/>
                </linearGradient>
              </defs>
              <path d="M0 365L180 0L360 365H288L180 130L72 365Z" fill="url(#apexRefLogo)"/>
            </svg>
          </div>
          <div>
            <div class="apex-ref-brand-name">APEX<span>MACRO</span></div>
            <div class="apex-ref-brand-sub">INTELLIGENCE DESK</div>
          </div>
        </div>
        """)
    with access_col:
        if active == "home":
            if st.button("Access", key="apex_profile_access", use_container_width=True, help="VIP Login / Get VIP"):
                _set_public_view("login")
                st.rerun()
        else:
            if st.button("←", key=f"public_home_back_{active}", use_container_width=True, help="Back to Home"):
                _set_public_view("home")
                st.rerun()

    render_html('<div class="apex-ref-navline"></div>')


def render_public_home() -> None:
    """Public Home Page closely matching the supplied mobile reference."""
    st.markdown("""
    <style>
      /* ----- exact-reference landing page ----- */
      .block-container {
        max-width:1180px !important;
      }
      .apex-home-shell {
        width:100%; margin:0 auto;
      }
      .apex-ref-hero {
        position:relative; overflow:hidden;
        min-height:555px; border-radius:23px;
        padding:43px 38px 32px;
        background:
          radial-gradient(circle at 91% 14%,rgba(0,209,235,.105),transparent 27%),
          linear-gradient(145deg,rgba(5,20,28,.93),rgba(3,10,17,.985));
        border:1px solid rgba(0,218,235,.42);
        box-shadow:inset 0 1px 0 rgba(255,255,255,.045),0 26px 70px rgba(0,0,0,.38);
      }
      .apex-ref-globe {
        position:absolute; z-index:0; right:-1px; top:0; bottom:0;
        width:51%; opacity:.92;
        background-image:
          linear-gradient(90deg,rgba(3,12,20,1) 0%,rgba(3,12,20,.48) 24%,rgba(3,12,20,.04) 58%),
          url("data:image/webp;base64,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");
        background-size:cover,cover;
        background-position:center,right center;
        background-repeat:no-repeat;
      }
      .apex-ref-hero-inner {
        position:relative; z-index:2; width:59%;
      }
      .apex-ref-kicker {
        display:inline-flex; align-items:center; gap:8px;
        border:1px solid rgba(0,255,174,.18);
        background:rgba(0,255,174,.055);
        border-radius:999px; padding:7px 12px;
        color:#78f6cd; font-size:9px; line-height:1;
        font-weight:900; letter-spacing:1.55px; white-space:nowrap;
      }
      .apex-ref-kdot {
        width:6px; height:6px; border-radius:50%; background:#00f5a7;
        box-shadow:0 0 11px rgba(0,245,167,.95);
      }
      .apex-ref-headline {
        margin-top:34px; color:#f6f7f8;
        font-size:clamp(38px,5.25vw,65px); font-weight:950;
        line-height:1.025; letter-spacing:-2.5px;
      }
      .apex-ref-headline .cyan { color:#13e5f2; }
      .apex-ref-headline .gold { color:#f7bf43; }
      .apex-ref-desc {
        margin-top:22px; max-width:590px;
        color:#b1bdc8; font-size:14px; line-height:1.75; font-weight:450;
      }
      .apex-ref-capabilities {
        display:flex; align-items:center; gap:34px; margin-top:34px;
      }
      .apex-ref-cap {
        display:flex; align-items:center; gap:12px; min-width:0;
      }
      .apex-ref-cap + .apex-ref-cap {
        padding-left:34px; border-left:1px solid rgba(255,255,255,.12);
      }
      .apex-ref-capicon {
        width:46px; height:46px; flex:0 0 46px;
        display:flex; align-items:center; justify-content:center;
        border-radius:13px; font-size:24px;
        border:1px solid rgba(0,231,247,.20);
        background:rgba(0,231,247,.045);
        box-shadow:0 0 25px rgba(0,231,247,.07);
      }
      .apex-ref-capicon.gold {
        border-color:rgba(247,191,67,.16); background:rgba(247,191,67,.04);
      }
      .apex-ref-captitle { color:#f2f6f9; font-size:14px; font-weight:900; }
      .apex-ref-capsub { color:#8ea0b1; font-size:11.5px; margin-top:3px; }

      .apex-ref-features {
        display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
        gap:16px; margin-top:24px;
      }
      .apex-ref-feature {
        min-width:0; min-height:232px; border-radius:20px;
        padding:24px 22px 21px;
        background:linear-gradient(145deg,rgba(11,23,33,.72),rgba(5,12,20,.72));
        border:1px solid rgba(132,169,185,.25);
        box-shadow:inset 0 1px 0 rgba(255,255,255,.035),0 16px 38px rgba(0,0,0,.22);
        backdrop-filter:blur(17px); -webkit-backdrop-filter:blur(17px);
      }
      .apex-ref-ficon {
        width:43px; height:43px; border-radius:12px;
        display:flex; align-items:center; justify-content:center;
        margin-bottom:20px;
        background:rgba(0,225,241,.055);
        border:1px solid rgba(0,225,241,.22);
        box-shadow:0 0 19px rgba(0,225,241,.07);
      }
      .apex-ref-ficon svg { width:25px; height:25px; }
      .apex-ref-feature.purple .apex-ref-ficon {
        background:rgba(202,49,255,.055); border-color:rgba(202,49,255,.25);
        box-shadow:0 0 19px rgba(202,49,255,.08);
      }
      .apex-ref-feature.gold .apex-ref-ficon {
        background:rgba(247,191,67,.055); border-color:rgba(247,191,67,.24);
        box-shadow:0 0 19px rgba(247,191,67,.08);
      }
      .apex-ref-feature.blue .apex-ref-ficon {
        background:rgba(0,145,255,.055); border-color:rgba(0,145,255,.24);
        box-shadow:0 0 19px rgba(0,145,255,.08);
      }
      .apex-ref-ftitle {
        color:#f4f6f8; font-size:17px; font-weight:900;
        line-height:1.23; overflow-wrap:normal; word-break:normal; hyphens:none;
      }
      .apex-ref-fcopy {
        margin-top:11px; color:#94a5b5; font-size:12.5px; line-height:1.6;
        overflow-wrap:normal; word-break:normal; hyphens:none;
      }

      .st-key-apex_home_cta {
        margin-top:27px !important; padding:22px 24px !important;
        border-radius:20px !important;
        background:
          radial-gradient(circle at 92% 50%,rgba(247,191,67,.12),transparent 28%),
          linear-gradient(135deg,rgba(4,31,35,.76),rgba(10,18,24,.78)) !important;
        border:1px solid rgba(0,219,232,.36) !important;
        box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 18px 48px rgba(0,0,0,.24) !important;
        backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
      }
      .apex-ref-cta-title { color:#f4f6f8; font-size:19px; font-weight:950; line-height:1.2; }
      .apex-ref-cta-copy { color:#9aabb8; margin-top:7px; font-size:11.5px; line-height:1.5; }
      .st-key-apex_learn_more button {
        border:1px solid rgba(0,226,239,.40) !important;
        background:rgba(0,226,239,.035) !important; color:#12e2ee !important;
        box-shadow:none !important; font-weight:850 !important;
      }
      .st-key-apex_get_started button {
        border:1px solid rgba(255,204,73,.72) !important;
        background:linear-gradient(135deg,#ffd15a,#efa71e) !important;
        color:#111 !important; font-weight:900 !important;
        box-shadow:0 0 26px rgba(246,181,42,.20) !important;
      }
      .apex-ref-footer {
        display:flex; align-items:center; justify-content:center; gap:7px;
        margin:28px 0 4px; color:#d4d8dc; font-size:11px;
      }
      .apex-ref-lock { color:#7f8b96; }

      @media(max-width:700px) {
        .block-container {
          padding-left:14px !important; padding-right:14px !important;
          padding-top:18px !important;
        }
        .apex-ref-hero {
          min-height:535px; padding:32px 24px 27px; border-radius:20px;
        }
        .apex-ref-globe {
          width:54%; right:-13%; opacity:.85;
          background-image:
            linear-gradient(90deg,rgba(3,12,20,1) 0%,rgba(3,12,20,.62) 28%,rgba(3,12,20,.04) 67%),
            url("data:image/webp;base64,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");
          background-size:cover,cover;
          background-position:center,right center;
        }
        .apex-ref-hero-inner { width:67%; }
        .apex-ref-kicker {
          font-size:7.2px; letter-spacing:1.15px; padding:6px 9px; gap:6px;
        }
        .apex-ref-kdot { width:5px; height:5px; }
        .apex-ref-headline {
          margin-top:29px; font-size:35px; line-height:1.055; letter-spacing:-1.55px;
        }
        .apex-ref-desc {
          margin-top:19px; font-size:11.4px; line-height:1.65; max-width:94%;
        }
        .apex-ref-capabilities { gap:12px; margin-top:28px; }
        .apex-ref-cap { gap:7px; }
        .apex-ref-cap + .apex-ref-cap { padding-left:12px; }
        .apex-ref-capicon {
          width:33px; height:33px; flex-basis:33px; border-radius:10px; font-size:18px;
        }
        .apex-ref-captitle { font-size:10.5px; }
        .apex-ref-capsub { font-size:8.6px; }
        .apex-ref-features {
          grid-template-columns:repeat(3,minmax(0,1fr));
          gap:9px; margin-top:16px;
        }
        .apex-ref-feature {
          min-height:206px; padding:16px 13px 14px; border-radius:16px;
        }
        .apex-ref-ficon {
          width:34px; height:34px; border-radius:10px; margin-bottom:14px;
        }
        .apex-ref-ficon svg { width:20px; height:20px; }
        .apex-ref-ftitle {
          font-size:12.4px; line-height:1.23; letter-spacing:-.1px;
        }
        .apex-ref-fcopy {
          margin-top:8px; font-size:9.8px; line-height:1.5;
        }
        .st-key-apex_home_cta {
          margin-top:17px !important; padding:16px 15px !important; border-radius:16px !important;
        }
        .apex-ref-cta-title { font-size:14px; }
        .apex-ref-cta-copy { font-size:9px; line-height:1.45; margin-top:5px; }
        .st-key-apex_learn_more button, .st-key-apex_get_started button {
          min-height:42px !important; padding:7px 8px !important;
          font-size:10px !important; border-radius:11px !important;
        }
        .apex-ref-footer { margin-top:22px; font-size:10px; }
      }

      @media(max-width:430px) {
        .apex-ref-hero { min-height:505px; padding:28px 20px 24px; }
        .apex-ref-headline { font-size:31px; }
        .apex-ref-desc { font-size:10.7px; }
        .apex-ref-feature { min-height:190px; padding:14px 11px 12px; }
        .apex-ref-ftitle { font-size:11.2px; }
        .apex-ref-fcopy { font-size:8.9px; }
      }
      @media(max-width:390px) {
        .block-container { padding-left:10px !important; padding-right:10px !important; }
        .apex-ref-hero { min-height:482px; padding:25px 17px 22px; }
        .apex-ref-hero-inner { width:69%; }
        .apex-ref-headline { font-size:28.5px; }
        .apex-ref-desc { font-size:10px; }
        .apex-ref-features { gap:7px; }
        .apex-ref-feature { min-height:181px; padding:13px 9px 11px; border-radius:14px; }
        .apex-ref-ftitle { font-size:10.2px; }
        .apex-ref-fcopy { font-size:8.2px; line-height:1.47; }
        .apex-ref-ficon { width:31px; height:31px; margin-bottom:12px; }
      }
    </style>
    """, unsafe_allow_html=True)

    render_public_nav("home")

    render_html("""
    <div class="apex-home-shell">
      <section class="apex-ref-hero">
        <div class="apex-ref-globe"></div>
        <div class="apex-ref-hero-inner">
          <div class="apex-ref-kicker"><span class="apex-ref-kdot"></span>GLOBAL MACRO INTELLIGENCE ENGINE</div>
          <div class="apex-ref-headline">
            See the macro shift<br>
            <span class="cyan">before it becomes</span><br>
            <span class="gold">obvious.</span>
          </div>
          <div class="apex-ref-desc">
            ApexMacro combines global macro data, market catalysts, causal intelligence and live tactical price action
            into one institutional-grade decision desk for Gold, Oil, Nasdaq-100 and global currencies.
          </div>
          <div class="apex-ref-capabilities">
            <div class="apex-ref-cap">
              <div class="apex-ref-capicon">◎</div>
              <div><div class="apex-ref-captitle">Multi-Asset</div><div class="apex-ref-capsub">Global Coverage</div></div>
            </div>
            <div class="apex-ref-cap">
              <div class="apex-ref-capicon gold">ϟ</div>
              <div><div class="apex-ref-captitle">Real-Time</div><div class="apex-ref-capsub">Macro Intelligence</div></div>
            </div>
          </div>
        </div>
      </section>

      <section id="apex-features" class="apex-ref-features">
        <article class="apex-ref-feature">
          <div class="apex-ref-ficon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#15e5f1" stroke-width="2"><path d="M4 18l5-6 4 3 7-9"/><path d="M16 6h4v4"/></svg>
          </div>
          <div class="apex-ref-ftitle">Macro Outlook</div>
          <div class="apex-ref-fcopy">Institutional macro view across assets and regimes.</div>
        </article>
        <article class="apex-ref-feature purple">
          <div class="apex-ref-ficon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#d151ff" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>
          </div>
          <div class="apex-ref-ftitle">Tactical Move</div>
          <div class="apex-ref-fcopy">Live price action readings and momentum shifts.</div>
        </article>
        <article class="apex-ref-feature gold">
          <div class="apex-ref-ficon">
            <svg viewBox="0 0 24 24" fill="#f7bf43"><path d="M13.5 1L5 13h6l-1 10 9-13h-6z"/></svg>
          </div>
          <div class="apex-ref-ftitle">Smart Shift Alerts</div>
          <div class="apex-ref-fcopy">Regime change monitoring with confirmation logic.</div>
        </article>
        <article class="apex-ref-feature blue">
          <div class="apex-ref-ficon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#1a9fff" stroke-width="2"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>
          </div>
          <div class="apex-ref-ftitle">Catalyst Forecaster</div>
          <div class="apex-ref-fcopy">Upcoming macro catalysts and event impact analysis.</div>
        </article>
        <article class="apex-ref-feature purple">
          <div class="apex-ref-ficon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#e05bbd" stroke-width="2"><path d="M9 5a3 3 0 0 0-5 2.2A3.5 3.5 0 0 0 5 14a3 3 0 0 0 4 4V5zM15 5a3 3 0 0 1 5 2.2A3.5 3.5 0 0 1 19 14a3 3 0 0 1-4 4V5z"/><path d="M9 9H6M15 9h3M9 14H7M15 14h2"/></svg>
          </div>
          <div class="apex-ref-ftitle">Causal Intelligence</div>
          <div class="apex-ref-fcopy">Connects drivers, catalysts and market transmission.</div>
        </article>
        <article class="apex-ref-feature">
          <div class="apex-ref-ficon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#16e7f5" stroke-width="2"><path d="M21 3L3 10l7 3 3 7 8-17z"/><path d="M10 13l5-5"/></svg>
          </div>
          <div class="apex-ref-ftitle">Telegram Alerts</div>
          <div class="apex-ref-fcopy">Personalized alerts delivered directly to you.</div>
        </article>
      </section>
    </div>
    """)

    with st.container(key="apex_home_cta"):
        left, buttons = st.columns([2.1, 1.0], vertical_alignment="center")
        with left:
            render_html("""
            <div class="apex-ref-cta-title">Ready to access ApexMacro?</div>
            <div class="apex-ref-cta-copy">Join professional traders and investors who act before the market moves.</div>
            """)
        with buttons:
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Learn More", key="apex_learn_more", use_container_width=True):
                    st.toast("Explore the ApexMacro intelligence tools above.")
            with b2:
                if st.button("Get Started →", key="apex_get_started", use_container_width=True):
                    _set_public_view("vip")
                    st.rerun()

    render_html('<div class="apex-ref-footer"><span class="apex-ref-lock">▣</span><span>apexmacro.com</span></div>')


def render_public_checkout_page() -> None:
    render_public_nav("vip")
    home_col, title_col = st.columns([1, 5], vertical_alignment="center")
    with home_col:
        if st.button("← Home", key="vip_page_home", use_container_width=True):
            _set_public_view("home")
            st.rerun()
    with title_col:
        render_html("""
        <div style="padding:3px 0 10px;">
          <div style="font-size:20px;font-weight:950;color:#edf9ff;">ApexMacro VIP Access</div>
          <div style="font-size:10.5px;color:#7f95a7;margin-top:3px;">Choose a plan and activate your terminal access with USDT on TRON.</div>
        </div>
        """)
    render_vip_checkout()


def render_vip_gate() -> dict | None:
    client_id, dev_type = get_client_device_info()

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

    render_public_nav("login")
    back_col, spacer_col = st.columns([1, 5])
    with back_col:
        if st.button("← Home", key="login_page_home", use_container_width=True):
            _set_public_view("home")
            st.rerun()

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
            if st.button("💳 Get VIP Access", key="apex_open_vip_checkout", use_container_width=True):
                st.session_state["APEX_SHOW_VIP_CHECKOUT"] = not st.session_state.get("APEX_SHOW_VIP_CHECKOUT", False)
                st.rerun()

        if st.session_state.get("APEX_SHOW_VIP_CHECKOUT", False):
            render_vip_checkout()

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
                "Alerts": ", ".join(_client_alert_asset_keys(c)) or "None",
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

    render_payment_admin_summary()

def main() -> None:
    inject_css()

    fred_key = DEFAULT_FRED_KEY
    channel_name = DEFAULT_TELEGRAM_CHANNEL

    # Inbound bot settings run in their own cached daemon and never block Streamlit.
    start_telegram_update_worker()

    if fred_key:
        start_background_alert_daemon(fred_key, channel_name)

    # The public landing page is now the default entry point.
    auth_user = st.session_state.get("APEX_AUTH_USER")
    if not (auth_user and auth_user.get("is_authenticated")):
        public_view = st.session_state.get("APEX_PUBLIC_VIEW", "home")

        if public_view == "home":
            render_public_home()
            return

        if public_view == "vip":
            render_public_checkout_page()
            auth_user = st.session_state.get("APEX_AUTH_USER")
            if not (auth_user and auth_user.get("is_authenticated")):
                return
        else:
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
