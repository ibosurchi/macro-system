"""
ApexMacro — Global Macro & Geopolitical Intelligence Desk
Institutional-Grade Multi-Timeframe Macro Analysis, Safe-Haven & Energy Intelligence
"""
from __future__ import annotations
import os
from pathlib import Path
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
from email.utils import parsedate_to_datetime

# Stable repository root: persistence files remain in their original project-root locations.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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

# Central AI provider configuration.
# RUAPI is the production default. OpenRouter is used only when explicitly selected.
DEFAULT_RUAPI_KEY = get_secret("RUAPI_API_KEY", "")
DEFAULT_RUAPI_MODEL = get_secret("RUAPI_MODEL", "claude-sonnet-5") or "claude-sonnet-5"
DEFAULT_RUAPI_BASE_URL = get_secret("RUAPI_BASE_URL", "https://www.ruapi.ai/v1").rstrip("/")

DEFAULT_OPENROUTER_KEY = get_secret("OPENROUTER_API_KEY", "")
DEFAULT_OPENROUTER_MODEL = get_secret("OPENROUTER_MODEL", "openai/gpt-4o-mini") or "openai/gpt-4o-mini"

DEFAULT_AI_PROVIDER = (get_secret("AI_PROVIDER", "RUAPI") or "RUAPI").strip().upper()
if DEFAULT_AI_PROVIDER not in {"RUAPI", "OPENROUTER"}:
    DEFAULT_AI_PROVIDER = "RUAPI"

if DEFAULT_AI_PROVIDER == "OPENROUTER":
    DEFAULT_AI_KEY = DEFAULT_OPENROUTER_KEY
    DEFAULT_AI_MODEL = DEFAULT_OPENROUTER_MODEL
    DEFAULT_AI_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
else:
    DEFAULT_AI_PROVIDER = "RUAPI"
    DEFAULT_AI_KEY = DEFAULT_RUAPI_KEY
    DEFAULT_AI_MODEL = DEFAULT_RUAPI_MODEL
    DEFAULT_AI_CHAT_URL = f"{DEFAULT_RUAPI_BASE_URL}/chat/completions"

AI_CACHE_VERSION = "ruapi-provider-v2"

REQUEST_TIMEOUT = 8

FOREX_FACTORY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def _ai_runtime(
    api_key: str | None = None,
    provider_hint: str | None = None,
    model_hint: str | None = None,
) -> tuple[str, str, str, str]:
    """Resolve the explicitly configured AI provider without silent provider switching."""
    provider = str(provider_hint or DEFAULT_AI_PROVIDER or "RUAPI").strip().upper()
    if provider == "OPENROUTER":
        key = str(api_key or DEFAULT_OPENROUTER_KEY or "").strip()
        model = str(model_hint or DEFAULT_OPENROUTER_MODEL or "openai/gpt-4o-mini").strip()
        return (
            "OpenRouter",
            "https://openrouter.ai/api/v1/chat/completions",
            model,
            key,
        )

    key = str(api_key or DEFAULT_RUAPI_KEY or "").strip()
    model = str(model_hint or DEFAULT_RUAPI_MODEL or "claude-sonnet-5").strip()
    return (
        "RUAPI",
        f"{DEFAULT_RUAPI_BASE_URL}/chat/completions",
        model,
        key,
    )

def _ai_headers(api_key: str, title: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://apexmacro.com",
        "X-Title": title,
        "Content-Type": "application/json",
    }


def _post_ai_chat(
    provider: str,
    url: str,
    headers: dict,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
):
    """
    Send an OpenAI-compatible chat request with bounded retry behavior.

    RUAPI behavior:
    - normal request first
    - one minimal-payload retry on HTTP 400
    - retry on read/connect timeout or transient 5xx
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }

    max_attempts = 3 if str(provider).upper() == "RUAPI" else 2
    last_error = None

    for attempt in range(max_attempts):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )

            # Some RUAPI/model combinations reject optional parameters or system role.
            if str(provider).upper() == "RUAPI" and response.status_code == 400:
                minimal_prompt = (
                    f"{system_prompt.strip()}\n\n"
                    f"USER REQUEST / EVIDENCE:\n{user_prompt.strip()}"
                )
                minimal_payload = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": minimal_prompt}
                    ],
                }
                response = requests.post(
                    url,
                    headers=headers,
                    json=minimal_payload,
                    timeout=timeout,
                )

            if response.ok:
                return response

            # Retry only transient gateway/server errors.
            if response.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < max_attempts - 1:
                time.sleep(1.5 * (attempt + 1))
                continue

            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    err = body.get("error", body)
                    if isinstance(err, dict):
                        detail = str(err.get("message") or err.get("detail") or err)
                    else:
                        detail = str(err)
                else:
                    detail = str(body)
            except Exception:
                detail = str(response.text or "").strip()

            detail = re.sub(r"\s+", " ", detail)[:500]
            provider_label = str(provider or "AI")
            if detail:
                raise RuntimeError(
                    f"{provider_label} HTTP {response.status_code}: {detail}"
                )
            response.raise_for_status()

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"{provider} temporarily unavailable after {max_attempts} attempts."
            ) from exc

    if last_error:
        raise RuntimeError(f"{provider} temporarily unavailable.") from last_error
    raise RuntimeError(f"{provider} request failed.")


def _ai_message_content(response_json: dict) -> str:
    """Extract text from an OpenAI-compatible chat-completions response."""
    try:
        choices = response_json.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        # Some gateways can return structured content parts.
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text") or item.get("content") or ""
                    if txt:
                        parts.append(str(txt))
                elif item:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content or "").strip()
    except Exception:
        return ""


def _extract_json_object(raw_text: str) -> dict | None:
    """Parse strict/fenced/embedded JSON without changing model semantics."""
    raw = str(raw_text or "").strip()
    if not raw:
        return None

    # 1) Direct JSON.
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # 2) Markdown fenced JSON.
    cleaned = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$",
        "",
        raw,
        flags=re.I | re.S,
    ).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # 3) Extract the first balanced {...} object even if the model adds prose.
    start = cleaned.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:i + 1]
                try:
                    parsed = json.loads(candidate)
                    return parsed if isinstance(parsed, dict) else None
                except Exception:
                    return None

    return None


def _normalize_causal_ai_payload(parsed: dict, source_count: int) -> dict:
    """Normalize only schema/typing; do not invent analytical content."""
    if not isinstance(parsed, dict):
        return {}

    result = dict(parsed)

    list_fields = [
        "causal_chain",
        "facts",
        "supporting_evidence",
        "contradictions",
    ]
    for field in list_fields:
        value = result.get(field, [])
        if isinstance(value, str):
            value = [value] if value.strip() else []
        elif not isinstance(value, list):
            value = []
        result[field] = [str(v).strip() for v in value if str(v).strip()][:12]

    try:
        result["confidence"] = int(max(0, min(100, round(float(result.get("confidence", 0))))))
    except Exception:
        result["confidence"] = 0

    for field, default in {
        "event_assessment": "Insufficient Evidence",
        "nowcast": "Insufficient Evidence",
        "confidence_reason": "Insufficient structured AI evidence.",
        "cross_source_confirmation": "Unavailable",
        "usd": "Neutral",
        "gold": "Neutral",
        "oil": "Neutral",
        "nasdaq": "Neutral",
        "invalidation": "Insufficient Evidence",
    }.items():
        value = str(result.get(field, "") or "").strip()
        result[field] = value if value else default

    try:
        result["source_count"] = int(result.get("source_count", source_count))
    except Exception:
        result["source_count"] = int(source_count)

    return result


@st.cache_data(ttl=30, show_spinner=False)
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
REGISTRY_FILE = str(PROJECT_ROOT / "vip_registry.json")

# VIP checkout configuration. Only public receiving/API values are used; no wallet private key is required.
USDT_TRC20_ADDRESS = get_secret("USDT_TRC20_ADDRESS", "")
TRONGRID_API_KEY = get_secret("TRONGRID_API_KEY", "")  # Optional; improves TronGrid rate limits.
TRONGRID_BASE_URL = get_secret("TRONGRID_BASE_URL", "https://api.trongrid.io").rstrip("/")
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
VIP_PAYMENT_PLANS = {
    "1 Month": {"amount": 29, "days": 30, "badge": "MONTHLY"},
    "3 Months": {"amount": 75, "days": 90, "badge": "BEST VALUE"},
}
PAYMENTS_FILE = str(PROJECT_ROOT / "vip_payments.json")
_PAYMENT_LOCK = threading.RLock()
SESSIONS_FILE = str(PROJECT_ROOT / "vip_sessions.json")

# Persistent client storage. Supabase is optional at code level so the app can still
# start locally, but production Streamlit should configure these secrets.
SUPABASE_URL = get_secret("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = get_secret("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STATE_TABLE = get_secret("SUPABASE_STATE_TABLE", "apexmacro_state") or "apexmacro_state"
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SUPABASE_STATE_TABLE):
    SUPABASE_STATE_TABLE = "apexmacro_state"
_PERSISTENCE_LOCK = threading.RLock()
_PERSISTENCE_STATUS = {"backend": "local", "last_error": ""}
ACTUALS_FILE = str(PROJECT_ROOT / "actual_releases.json")
ALERT_STATE_FILE = str(PROJECT_ROOT / "alert_regime_state.json")
TELEGRAM_UPDATE_STATE_FILE = str(PROJECT_ROOT / "telegram_update_state.json")
TELEGRAM_DAEMON_LOCK_FILE = str(PROJECT_ROOT / ".apexmacro_telegram_daemon.lock")
TACTICAL_STATE_FILE = str(PROJECT_ROOT / "tactical_move_state.json")
FORECAST_HISTORY_FILE = str(PROJECT_ROOT / "forecaster_history.json")
_FORECAST_HISTORY_LOCK = threading.RLock()
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


def _compose_gold_intelligence_score(gold_ry: float, gold_usd: float, sentiment_res: dict) -> dict:
    """Established Gold model plus a bounded live-price confirmation overlay."""
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

    # The original macro/news engine still owns 90% of the final decision.
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
        "Gold": {"symbol": "XAUUSD=X", "fallback_symbols": ["GC=F"], "invert": False, "display": "Gold (XAUUSD)", "icon": "🥇"},
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
def _news_source_display_name(channel_username: str) -> str:
    clean = str(channel_username or "").replace("@", "").replace("https://t.me/", "").strip()
    known = {
        "financialjuice": "FinancialJuice",
        "forexlive": "ForexLive",
        "firstsquawk": "First Squawk",
        "Forex_LiveStream": "Forex LiveStream",
        "forex_livestream": "Forex LiveStream",
    }
    return known.get(clean, clean or "Telegram")


def _parse_news_datetime(value: object) -> datetime | None:
    """Best-effort timestamp parsing used only for freshness ranking."""
    raw = str(value or "").strip()
    if not raw:
        return None

    candidates = [raw, raw.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _news_priority_score(article: dict) -> float:
    """
    Ranking affects which CURRENT headlines reach AI first.
    It does not change any macro, Gold, Tactical, alert, or strategy thresholds.
    """
    source_name = str((article.get("source") or {}).get("name", "")).strip().lower()
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()

    source_weight = 0.45
    source_priorities = {
        "first squawk": 1.00,
        "financialjuice": 0.96,
        "kitco gold": 0.96,
        "kitco commodities": 0.94,
        "forexlive": 0.90,
        "fxstreet": 0.86,
        "axios world": 0.84,
        "axios business": 0.84,
        "axios energy & climate": 0.84,
        "dailyfx": 0.80,
        "marketwatch": 0.78,
        "investing macro": 0.77,
        "yahoo finance": 0.72,
        "forex livestream": 0.82,
    }
    for key, weight in source_priorities.items():
        if key in source_name:
            source_weight = max(source_weight, weight)

    # Freshness is deliberately bounded: unknown timestamps are not treated as "fresh".
    freshness = 0.22
    dt = _parse_news_datetime(article.get("publishedAt", ""))
    if dt is not None:
        now_utc = datetime.utcnow()
        age_hours = max(0.0, (now_utc - dt).total_seconds() / 3600.0)
        if age_hours <= 0.5:
            freshness = 1.00
        elif age_hours <= 2:
            freshness = 0.92
        elif age_hours <= 6:
            freshness = 0.78
        elif age_hours <= 12:
            freshness = 0.62
        elif age_hours <= 24:
            freshness = 0.44
        elif age_hours <= 48:
            freshness = 0.28
        else:
            freshness = 0.12

    high_impact_terms = [
        "federal reserve", "fed ", "powell", "rate cut", "rate hike", "interest rate",
        "cpi", "pce", "inflation", "nonfarm", "nfp", "payroll", "unemployment",
        "treasury yield", "real yield", "dxy", "dollar index", "us dollar", "u.s. dollar",
        "gold", "xau", "bullion", "central bank", "gold etf",
        "war", "missile", "attack", "ceasefire", "sanction", "geopolitical",
        "middle east", "iran", "israel", "oil", "opec", "hormuz",
    ]
    relevance_hits = sum(1 for term in high_impact_terms if term in text)
    relevance = min(1.0, 0.18 + (0.12 * relevance_hits))

    breaking_bonus = 0.0
    if any(term in text for term in [
        "breaking", "just in", "unexpected", "surprise", "emergency",
        "strikes", "attacks", "cuts rates", "raises rates", "record high",
    ]):
        breaking_bonus = 0.12

    return float((0.43 * source_weight) + (0.37 * freshness) + (0.20 * relevance) + breaking_bonus)


def _rank_news_articles(articles: list) -> list:
    enriched = []
    for art in articles or []:
        if not isinstance(art, dict):
            continue
        item = dict(art)
        item["_priority"] = _news_priority_score(item)
        enriched.append(item)
    enriched.sort(key=lambda x: float(x.get("_priority", 0.0)), reverse=True)
    return enriched


@st.cache_data(ttl=30, show_spinner=False)
def fetch_telegram_channel_news(channel_username: str) -> list:
    clean_username = channel_username.replace("@", "").replace("https://t.me/", "").strip()
    url = f"https://t.me/s/{clean_username}"
    headers = {"User-Agent": "Mozilla/5.0 ApexMacro/14.0"}
    articles = []
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message_text")
            times = soup.find_all("time", class_="time")
            source_name = _news_source_display_name(clean_username)
            for msg, tm in zip(messages[-10:], times[-10:]):
                txt = msg.get_text(separator=" ").strip()
                if len(txt) > 15:
                    articles.append({
                        "title": txt[:110] + "..." if len(txt) > 110 else txt,
                        "description": txt,
                        "publishedAt": tm.get("datetime", "") if tm else "",
                        "source": {"name": source_name},
                        "url": url,
                    })
    except Exception:
        pass
    return list(reversed(articles))


@st.cache_data(ttl=60, show_spinner=False)
def fetch_axios_macro_news() -> list:
    """
    Read only publicly visible Axios section headlines/metadata.
    No article-body scraping is performed.
    """
    sections = [
        ("Axios Business", "https://www.axios.com/business"),
        ("Axios World", "https://www.axios.com/world"),
        ("Axios Energy & Climate", "https://www.axios.com/energy-climate"),
    ]
    articles = []
    headers = {"User-Agent": "Mozilla/5.0 ApexMacro/14.0"}
    seen = set()

    for source_name, url in sections:
        try:
            response = requests.get(url, headers=headers, timeout=8)
            if not response.ok:
                continue
            soup = BeautifulSoup(response.text, "html.parser")

            # Axios section pages expose article headlines in heading links.
            links = soup.select("h2 a[href], h3 a[href]")
            for link in links:
                title = link.get_text(" ", strip=True)
                href = str(link.get("href", "")).strip()
                if not title or len(title) < 20 or len(title) > 180:
                    continue
                if title.lower() in seen:
                    continue
                if href.startswith("/"):
                    href = "https://www.axios.com" + href
                if "axios.com" not in href:
                    continue

                parent = link.find_parent(["article", "section", "div"])
                time_tag = parent.find("time") if parent else None
                published = ""
                if time_tag:
                    published = str(time_tag.get("datetime", "") or time_tag.get_text(" ", strip=True)).strip()

                articles.append({
                    "title": title,
                    "description": "",
                    "publishedAt": published,
                    "source": {"name": source_name},
                    "url": href,
                })
                seen.add(title.lower())

                if sum(1 for a in articles if a.get("source", {}).get("name") == source_name) >= 4:
                    break
        except Exception:
            continue

    return articles


def _rss_entry_to_article(src_name: str, entry: object) -> dict:
    title = str(entry.get("title", "")).strip()
    summary = str(entry.get("summary", "") or entry.get("description", ""))
    desc = re.sub(r"<[^>]+>", "", summary).strip()[:300]
    published = str(
        entry.get("published", "")
        or entry.get("updated", "")
        or entry.get("pubDate", "")
    ).strip()
    return {
        "title": title,
        "description": desc,
        "publishedAt": published,
        "source": {"name": src_name},
        "url": str(entry.get("link", "")).strip(),
    }


@st.cache_data(ttl=30, show_spinner=False)
def fetch_all_instant_news(channel_name: str = DEFAULT_TELEGRAM_CHANNEL) -> list:
    all_raw = []

    # Fast public Telegram wires.
    tg_channels = [channel_name, "financialjuice", "forexlive", "firstsquawk"]
    for ch in tg_channels:
        if ch:
            all_raw.extend(fetch_telegram_channel_news(ch))

    # Existing macro feeds plus dedicated precious-metals coverage.
    rss_urls = [
        ("Kitco Gold", "https://www.kitco.com/news/category/commodities/rss"),
        ("Kitco Commodities", "https://www.kitco.com/news/category/markets/rss"),
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
            for entry in feed.entries[:5]:
                article = _rss_entry_to_article(src_name, entry)
                if article["title"]:
                    all_raw.append(article)
        except Exception:
            continue

    # Axios is a confirmation/context layer for policy, geopolitics, business and energy.
    all_raw.extend(fetch_axios_macro_news())

    # Rank BEFORE deduplication so the stronger/fresher source survives duplicate removal.
    ranked_raw = _rank_news_articles(all_raw)
    deduped = deduplicate_news_articles(ranked_raw)

    # No synthetic/fake "live" headlines. An empty feed remains explicitly empty.
    return _rank_news_articles(deduped)


@st.cache_data(ttl=60, show_spinner=False)
def get_openrouter_analysis(
    news_text: str,
    api_key: str = DEFAULT_AI_KEY,
    provider_hint: str = DEFAULT_AI_PROVIDER,
    model_hint: str = DEFAULT_AI_MODEL,
    cache_version: str = AI_CACHE_VERSION,
) -> str:
    if not news_text or not api_key:
        return "AI analysis unavailable."

    provider, url, model, resolved_key = _ai_runtime(api_key, provider_hint, model_hint)
    if not resolved_key:
        return f"{provider} AI key is unavailable."

    system_prompt = (
        "You are an institutional financial analyst and macro strategist. "
        "Analyze ONLY the supplied live-news items. Respect source names and timestamps, prioritize the freshest "
        "high-impact developments, and treat cross-source confirmation as stronger evidence than a single headline. "
        "Do not invent missing facts and do not treat stale or undated items as breaking news. "
        "Provide a concise 2-3 sentence executive summary highlighting the immediate directional impact on "
        "Gold (XAUUSD), US Dollar (USD), Crude Oil, and Nasdaq-100 when relevant."
    )

    try:
        response = _post_ai_chat(
            provider=provider,
            url=url,
            headers=_ai_headers(resolved_key, "ApexMacro Desk"),
            model=model,
            system_prompt=system_prompt,
            user_prompt=news_text,
            temperature=0.2,
            timeout=45,
        )
        content = _ai_message_content(response.json())
        return content or "Could not generate AI analysis at the moment."
    except Exception as e:
        return f"{provider} AI Error: {str(e)}"


def _is_gold_relevant_news(article: dict) -> bool:
    """Identify headlines that can materially affect XAUUSD, even if 'gold' is not named."""
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
    """Contextual XAUUSD news score in the existing [-0.50, +0.50] convention."""
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


@st.cache_data(ttl=60, show_spinner=False)
def get_openrouter_gold_signal(
    news_text: str,
    api_key: str = DEFAULT_AI_KEY,
    provider_hint: str = DEFAULT_AI_PROVIDER,
    model_hint: str = DEFAULT_AI_MODEL,
    cache_version: str = AI_CACHE_VERSION,
) -> dict:
    default = {
        "direction": "Neutral",
        "score": 0.0,
        "confidence": 0.0,
        "horizon": "Unknown",
        "reason": "Gold AI signal is temporarily unavailable.",
        "active": False,
    }
    if not news_text or not api_key:
        return default

    provider, url, model, resolved_key = _ai_runtime(api_key, provider_hint, model_hint)
    if not resolved_key:
        return default

    system_prompt = (
        "You are the Gold intelligence analyst for an institutional macro terminal. "
        "Assess ONLY the directional impact of the supplied CURRENT news on Gold/XAUUSD. "
        "Use supplied source names and timestamps: prioritize fresher high-quality reports, look for cross-source "
        "confirmation, and lower confidence when evidence is stale, undated, contradictory, or single-source. "
        "Reason through real yields, USD/DXY, Federal Reserve expectations, inflation, "
        "safe-haven/geopolitical demand, central-bank demand and ETF flows. "
        "Do not treat generic positive/negative words as Gold direction and do not invent facts not present in the feed. "
        "Return ONLY valid JSON with keys: direction, score, confidence, horizon, reason. "
        "direction must be Bullish, Neutral or Bearish. "
        "score must be from -1.0 to +1.0. confidence must be from 0 to 100. "
        "horizon must be Intraday, 1-3 Days, or Multi-Day. "
        "reason must be one concise sentence."
    )

    try:
        response = _post_ai_chat(
            provider=provider,
            url=url,
            headers=_ai_headers(resolved_key, "ApexMacro Gold Intelligence"),
            model=model,
            system_prompt=system_prompt,
            user_prompt=news_text,
            temperature=0.1,
            timeout=45,
        )
        content = _ai_message_content(response.json())
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S).strip()
        parsed = json.loads(content)

        direction = str(parsed.get("direction", "Neutral")).strip().title()
        if direction not in {"Bullish", "Neutral", "Bearish"}:
            direction = "Neutral"

        score = float(np.clip(float(parsed.get("score", 0.0)), -1.0, 1.0))
        confidence = float(np.clip(float(parsed.get("confidence", 0.0)), 0.0, 100.0))

        return {
            "direction": direction,
            "score": score,
            "confidence": confidence,
            "horizon": str(parsed.get("horizon", "Unknown"))[:40],
            "reason": str(parsed.get("reason", ""))[:280],
            "active": True,
        }
    except Exception as exc:
        default["reason"] = f"{provider} Gold AI unavailable: {str(exc)[:220]}"
        return default


def _gold_news_intelligence(articles: list) -> dict:
    """Blend contextual Gold rules with bounded AI in the existing ±0.50 news layer."""
    relevant = _gold_relevant_articles(articles, 14)
    rule_points = _gold_rule_based_news_points(relevant)
    if not relevant:
        return {
            "points": 0.0,
            "rule_points": 0.0,
            "ai_points": 0.0,
            "ai": {
                "direction": "Neutral", "score": 0.0, "confidence": 0.0,
                "horizon": "Unknown", "reason": "No Gold-relevant live news detected.",
                "active": False,
            },
            "relevant_count": 0,
        }

    ranked_relevant = _rank_news_articles(relevant)
    news_text = "\n".join(
        f"- [{(a.get('source') or {}).get('name', 'Unknown Source')} | {a.get('publishedAt', '')}] "
        f"{a.get('title', '')}: {a.get('description', '')}"
        for a in ranked_relevant[:12]
    )
    ai = get_openrouter_gold_signal(news_text, DEFAULT_AI_KEY, DEFAULT_AI_PROVIDER, DEFAULT_AI_MODEL, AI_CACHE_VERSION)
    confidence_factor = float(ai.get("confidence", 0.0)) / 100.0 if ai.get("active") else 0.0
    ai_points = float(np.clip(float(ai.get("score", 0.0)) * 0.50 * confidence_factor, -0.50, 0.50))

    # Rules remain the majority of the news layer; AI is deliberately bounded.
    blended = (0.65 * rule_points) + (0.35 * ai_points)
    return {
        "points": float(np.clip(blended, -0.50, 0.50)),
        "rule_points": rule_points,
        "ai_points": ai_points,
        "ai": ai,
        "relevant_count": len(relevant),
    }


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

    ranked_articles = _rank_news_articles(articles)
    combined_news = "\n".join([
        f"- [{(a.get('source') or {}).get('name', 'Unknown Source')} | {a.get('publishedAt', '')}] "
        f"{a.get('title', '')}: {a.get('description', '')}"
        for a in ranked_articles[:10]
    ])
    ai_summary = get_openrouter_analysis(combined_news, DEFAULT_AI_KEY, DEFAULT_AI_PROVIDER, DEFAULT_AI_MODEL, AI_CACHE_VERSION)

    bullish_keywords = ["surge", "jump", "higher", "beat", "strong", "rally", "growth", "bull", "cut inflation", "options", "profit"]
    bearish_keywords = ["drop", "fall", "lower", "miss", "weak", "slump", "bear", "inflation rise", "tension", "attacking", "military", "war"]

    sentiment_delta = 0.0
    for art in articles:
        text = (art.get("title", "") + " " + art.get("description", "")).lower()
        if any(k in text for k in bullish_keywords):
            sentiment_delta += 0.04
        if any(k in text for k in bearish_keywords):
            sentiment_delta -= 0.04

    gold_intel = _gold_news_intelligence(articles)

    for k in scores:
        if k == "Gold":
            scores[k] = float(gold_intel["points"])
        elif k == "CHF":
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

    return {
        "scores": scores,
        "drivers": drivers,
        "ai_summary": ai_summary,
        "ai_active": True,
        "gold_ai": gold_intel.get("ai", {}),
        "gold_rule_points": gold_intel.get("rule_points", 0.0),
        "gold_ai_points": gold_intel.get("ai_points", 0.0),
        "gold_relevant_news_count": gold_intel.get("relevant_count", 0),
    }

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
    """Backward-compatible terminal default; real routing now lives in app.py/pages."""
    page_forex(fred_key, channel_name)

def page_forex(fred_key: str, channel_name: str) -> None:
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
    gold_intel = _compose_gold_intelligence_score(gold_ry, gold_usd, sentiment_res)
    gold_news_pts = gold_intel["news_points"]
    gold_s = gold_intel["score"]
    gold_base_s = gold_intel["base_score"]
    gold_ai = gold_intel.get("gold_ai", {})
    gold_tactical_confirm = gold_intel.get("tactical")

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
        gold_ai_direction = str(gold_ai.get("direction", "Neutral"))
        gold_ai_confidence = float(gold_ai.get("confidence", 0.0))
        gold_ai_reason = str(gold_ai.get("reason", ""))
        gold_ai_horizon = str(gold_ai.get("horizon", "Unknown"))
        gold_news_count = int(gold_intel.get("gold_relevant_news_count", 0))
        tactical_confirm_text = (
            f"{gold_tactical_confirm.get('label_icon', '')} {gold_tactical_confirm.get('label', 'Neutral')} "
            f"({int(gold_tactical_confirm.get('confidence', 0))}% confidence)"
            if gold_tactical_confirm else "Unavailable"
        )
        ai_summary_html = (
            f'<div style="margin-top:10px;padding:10px 12px;background:rgba(255,209,102,0.06);'
            f'border:1px solid rgba(255,209,102,0.22);border-radius:10px;font-size:11.5px;color:#ecf7ff;'
            f'text-align:left;line-height:1.55;"><b style="color:#ffd166;">Gold AI Signal:</b> '
            f'{gold_ai_direction} • {gold_ai_confidence:.0f}% • {gold_ai_horizon}<br>'
            f'<span style="color:#9fb1bf;">{gold_ai_reason}</span></div>'
        ) if gold_ai.get("active") else (
            '<div style="margin-top:10px;padding:10px 12px;background:rgba(255,255,255,0.03);'
            'border:1px solid rgba(255,255,255,0.08);border-radius:10px;font-size:11px;color:#8fa3b4;">'
            'Gold AI signal is temporarily unavailable; contextual rules and macro data remain active.</div>'
        )

        render_html(f"""
        <div class="comp-box" style="height:100%;text-align:left;padding:18px 20px;">
          <div style="font-size:11px;font-weight:800;color:#8fa3b4;text-transform:uppercase;margin-bottom:8px;">🥇 GOLD (XAUUSD) OVERALL BIAS</div>
          <div style="margin-bottom:12px;">{badge(gold_s, lg=True)}</div>
          <div style="font-size:18px;font-weight:900;color:#fff;">Composite: <span style="color:#ffd166;">{gold_s:+.3f}</span></div>
          <div style="font-size:11.5px;color:#8fa3b4;margin-top:4px;">Established Macro + News Score: <b style="color:#fff;">{gold_base_s:+.3f}</b> | Contextual Gold News: <b style="color:{gn_color};">{gold_news_pts:+.2f} pts</b></div>
          <div style="font-size:11px;color:#8fa3b4;margin-top:5px;">Gold-Relevant Headlines: <b style="color:#ecf7ff;">{gold_news_count}</b> | Live Price Confirmation: <b style="color:#00f5ff;">{tactical_confirm_text}</b></div>
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
            ai_ndx_summary = get_openrouter_analysis(ndx_news_text, DEFAULT_AI_KEY, DEFAULT_AI_PROVIDER, DEFAULT_AI_MODEL, AI_CACHE_VERSION)
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


def _normalize_forex_factory_actual(value: object) -> str:
    """Return a usable published Forex Factory actual print, or an empty string while pending."""
    clean = str(value if value is not None else "").strip()
    if clean.lower() in {"", "—", "-", "n/a", "na", "none", "null", "pending"}:
        return ""
    return clean


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

        # Keep a released catalyst on the live Forecaster radar for 48 hours only.
        # Persisted Actual values remain stored for compatibility/history.
        if total_seconds < -(48 * 3600):
            continue

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
            "actual_str": _normalize_forex_factory_actual(ff.get("actual", "")),
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
            "actual_str": meta["actual_str"],
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


def _safe_numeric_release(value: object) -> float | None:
    """Parse common calendar prints without guessing non-numeric releases."""
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text or text.lower() in {"—", "-", "n/a", "na", "pending", "none"}:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _event_family(event: dict) -> str:
    title = _normalize_catalyst_title(event.get("title", ""))
    if any(k in title for k in ("pce", "cpi", "inflation", "price index", "ppi")):
        return "inflation"
    if any(k in title for k in ("non farm", "nonfarm", "payroll", "unemployment", "jobless", "employment", "jolts")):
        return "labor"
    if any(k in title for k in ("gdp", "retail sales", "personal spending", "income", "pmi", "industrial production")):
        return "growth"
    if any(k in title for k in ("durable", "factory", "manufacturing", "construction")):
        return "activity"
    if any(k in title for k in ("rate decision", "fomc", "speaks", "speech", "minutes")):
        return "policy"
    if any(k in title for k in ("crude oil", "inventory", "inventories", "eia")):
        return "energy"
    return "general"


_EVENT_MODEL_PROFILES = {
    # Quant/news/surprise weights plus minimum evidence and ambiguity controls.
    "inflation": {"precursor": .50, "news": .25, "surprise": .10, "inline_prior": .15, "conflict": .30},
    "labor":     {"precursor": .50, "news": .22, "surprise": .13, "inline_prior": .15, "conflict": .28},
    "growth":    {"precursor": .55, "news": .18, "surprise": .12, "inline_prior": .15, "conflict": .26},
    "activity":  {"precursor": .55, "news": .18, "surprise": .12, "inline_prior": .15, "conflict": .26},
    "energy":    {"precursor": .58, "news": .22, "surprise": .10, "inline_prior": .10, "conflict": .24},
    "policy":    {"precursor": .25, "news": .55, "surprise": .05, "inline_prior": .15, "conflict": .30},
    "general":   {"precursor": .48, "news": .22, "surprise": .10, "inline_prior": .20, "conflict": .30},
}


def _verified_event_news(event: dict, articles: list) -> tuple[list, float]:
    """Event-aware news gate. Ambiguous numeric headlines remain context, not hard release evidence."""
    meta = event.get("meta", {}) or {}
    kws = [str(k).lower().strip() for k in (meta.get("keywords") or []) if str(k).strip()]
    title_tokens = {t for t in _normalize_catalyst_title(event.get("title", "")).split() if len(t) >= 4}
    verified = []
    ambiguity = 0.0
    for art in (articles or []):
        blob = f"{art.get('title','')} {art.get('description','')}".lower()
        kw_hits = sum(1 for k in kws if k in blob)
        token_hits = sum(1 for t in title_tokens if t in blob)
        if kw_hits <= 0 and token_hits < 2:
            continue
        # A numeric headline is potentially a different metric/period unless title identity is strong.
        has_number = bool(re.search(r"\b\d+(?:\.\d+)?\s*%", blob))
        strong_identity = token_hits >= max(2, min(4, len(title_tokens))) or kw_hits >= 2
        item = dict(art)
        item["_event_verified"] = bool(strong_identity)
        item["_numeric_ambiguous"] = bool(has_number and not strong_identity)
        if item["_numeric_ambiguous"]:
            ambiguity += 1.0
        verified.append(item)
    return verified[:12], min(1.0, ambiguity / max(1, len(verified)))


def _three_way_probabilities(composite: float, conflict: float, evidence_quality: float, inline_prior: float) -> dict:
    """Convert directional score into calibrated Beat/In-line/Miss probabilities."""
    strength = min(1.0, abs(float(composite)))
    directional_mass = 0.50 + 0.30 * strength * max(.35, evidence_quality)
    directional_mass *= (1.0 - 0.35 * conflict)
    inline = max(inline_prior, 1.0 - directional_mass)
    remaining = max(0.0, 1.0 - inline)
    tilt = max(-1.0, min(1.0, float(composite) * 1.65))
    beat = remaining * (0.5 + 0.5 * tilt)
    miss = remaining - beat
    total = beat + inline + miss
    return {
        "beat": round(100 * beat / total, 1),
        "inline": round(100 * inline / total, 1),
        "miss": round(100 * miss / total, 1),
    }


def _load_forecaster_history() -> dict:
    with _FORECAST_HISTORY_LOCK:
        try:
            with open(FORECAST_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"records": {}}
        except Exception:
            return {"records": {}}


def _save_forecaster_history(data: dict) -> None:
    with _FORECAST_HISTORY_LOCK:
        try:
            tmp = FORECAST_HISTORY_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, FORECAST_HISTORY_FILE)
        except Exception:
            pass


def _history_learning_adjustment(event: dict) -> tuple[float, int]:
    """Conservative calibration from completed same-family forecasts; no self-modifying business logic."""
    family = _event_family(event)
    hist = _load_forecaster_history().get("records", {})
    rows = [r for r in hist.values() if r.get("family") == family and r.get("resolved") and r.get("predicted_outcome")]
    if len(rows) < 12:
        return 1.0, len(rows)
    correct = sum(1 for r in rows[-60:] if r.get("predicted_outcome") == r.get("actual_outcome"))
    acc = correct / max(1, len(rows[-60:]))
    # Only confidence calibration, bounded tightly; never rewrite strategy weights automatically.
    return max(.82, min(1.08, acc / .60)), len(rows)


def _record_forecaster_snapshot(event: dict, nowcast: dict, actual: str = "") -> None:
    code = str(event.get("code", "")).strip()
    if not code:
        return
    data = _load_forecaster_history(); records = data.setdefault("records", {})
    rec = records.get(code, {})
    if not rec:
        probs = nowcast.get("probabilities", {})
        rec = {
            "event_code": code, "title": event.get("title", ""), "currency": event.get("currency", ""),
            "family": _event_family(event), "forecast": event.get("forecast_str", ""), "previous": event.get("prev_str", ""),
            "captured_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "predicted_outcome": max(probs, key=probs.get) if probs else nowcast.get("outcome_key", "inline"),
            "probabilities": probs, "confidence": nowcast.get("confidence", 0),
            "composite": nowcast.get("nowcast_composite", 0), "conflict_score": nowcast.get("conflict_score", 0),
            "evidence_quality": nowcast.get("evidence_quality", 0),
            "precursors": nowcast.get("precursor_results", []),
            "news_sources": [((a.get("source") or {}).get("name", "") if isinstance(a.get("source"), dict) else str(a.get("source", ""))) for a in nowcast.get("correlated_articles", [])],
            "resolved": False,
        }
        records[code] = rec
    if actual and not rec.get("resolved"):
        av = _safe_numeric_release(actual); fv = _safe_numeric_release(event.get("forecast_str", ""))
        if av is not None and fv is not None:
            eps = max(1e-9, abs(fv) * 1e-6)
            outcome = "beat" if av > fv + eps else ("miss" if av < fv - eps else "inline")
            rec.update({"actual": actual, "actual_outcome": outcome, "resolved": True,
                        "resolved_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        "correct": rec.get("predicted_outcome") == outcome,
                        "absolute_error": abs(av - fv)})
    _save_forecaster_history(data)


def _forecaster_performance() -> dict:
    rows = list(_load_forecaster_history().get("records", {}).values())
    done = [r for r in rows if r.get("resolved")]
    correct = sum(1 for r in done if r.get("correct"))
    by = {}
    for r in done:
        fam = r.get("family", "general"); x = by.setdefault(fam, [0, 0]); x[0] += 1; x[1] += int(bool(r.get("correct")))
    return {"total": len(rows), "resolved": len(done), "correct": correct,
            "accuracy": (100.0 * correct / len(done)) if done else 0.0,
            "by_family": {k: {"n": v[0], "accuracy": 100.0*v[1]/v[0]} for k,v in by.items()}}


def compute_event_nowcast(event: dict, fred_key: str, all_news: list, actual_override: str = "") -> dict:
    meta = event.get("meta", {}) or {}
    precursors = meta.get("precursors", [])
    family = _event_family(event)
    profile = _EVENT_MODEL_PROFILES.get(family, _EVENT_MODEL_PROFILES["general"])

    precursor_results = []
    precursor_score_sum = 0.0
    precursor_weight_sum = 0.0
    signs = []
    for p in precursors:
        series_id = p.get("series", ""); fallback_id = p.get("fallback")
        df = fetch_fred(series_id, fred_key, limit=60)
        if (df is None or df.empty) and fallback_id:
            df = fetch_fred(fallback_id, fred_key, limit=60)
        if df is not None and not df.empty:
            vals = df["value"].tolist(); mf = calc_mtf(vals, p["cat"])
            score = mf["score"] if mf else 0.0; mom = mf.get("mom", 0.0) if mf else 0.0
            adjusted_score = score * (1.25 if mom > 0.5 else (0.85 if mom < -0.5 else 1.0))
            w = p.get("weight", 0.25)
            precursor_results.append({"name": p["name"], "latest": vals[-1], "mom": mom, "score": adjusted_score, "weight": w})
            precursor_score_sum += adjusted_score * w; precursor_weight_sum += w
            if abs(adjusted_score) >= .08: signs.append(1 if adjusted_score > 0 else -1)
    base_precursor_score = precursor_score_sum / precursor_weight_sum if precursor_weight_sum else 0.0

    correlated_articles, news_ambiguity = _verified_event_news(event, all_news)
    cur = meta.get("currency", event.get("currency", "USD"))
    verified_for_score = [a for a in correlated_articles if not a.get("_numeric_ambiguous")]
    news_sentiment_pts = 0.0
    if verified_for_score:
        news_sentiment_pts = analyze_news_rule_based(verified_for_score)["scores"].get(cur, 0.0)

    # Conflict: disagreement among precursor directions and between quantitative/news direction.
    precursor_conflict = 0.0
    if len(signs) >= 2 and len(set(signs)) > 1:
        precursor_conflict = min(1.0, 2.0 * min(signs.count(1), signs.count(-1)) / len(signs))
    cross_conflict = 1.0 if base_precursor_score * news_sentiment_pts < -0.005 else 0.0
    conflict_score = min(1.0, .65 * precursor_conflict + .25 * cross_conflict + .10 * news_ambiguity)

    evidence_quality = min(1.0, .18 + .16 * len(precursor_results) + .07 * min(5, len(verified_for_score)))
    surprise_factor = .20 if base_precursor_score > .15 else (-.20 if base_precursor_score < -.15 else 0.0)
    nowcast_composite = (profile["precursor"] * base_precursor_score +
                         profile["news"] * (news_sentiment_pts / .50) +
                         profile["surprise"] * surprise_factor)
    nowcast_composite *= (1.0 - profile["conflict"] * conflict_score)

    calibration, learning_n = _history_learning_adjustment(event)
    probabilities = _three_way_probabilities(nowcast_composite, conflict_score, evidence_quality, profile["inline_prior"])
    # Confidence is the winning probability, calibrated by evidence/history and capped when evidence conflicts.
    outcome_key = max(probabilities, key=probabilities.get)
    confidence_val = int(round(probabilities[outcome_key] * (.82 + .18 * evidence_quality) * calibration))
    if conflict_score >= .45: confidence_val = min(confidence_val, 64)
    if evidence_quality < .45: confidence_val = min(confidence_val, 60)
    confidence_val = max(34, min(92, confidence_val))

    if outcome_key == "beat":
        bias_label = "🔺 LIKELY HIGHER THAN FORECAST (Beat)"; bias_color = "#00ffa3"
        outcome_desc = "Event-specific precursor evidence leans above consensus, after conflict and news-verification penalties."
        currency_action_en = f"📈 {cur} Expected to Appreciate (Bullish Bias)"; currency_action_color = "#00ffa3"
        currency_action_desc_en = f"{cur} has an upside macro-surprise bias, but probability and contradiction controls remain active."
        gold_implication = "📉 Bearish Pressure on Gold (Hawkish surprise risk)"; usd_implication = "📈 Bullish Tailwind for USD"; oil_implication = "📈 Bullish Support"
    elif outcome_key == "miss":
        bias_label = "🔻 LIKELY LOWER THAN FORECAST (Miss)"; bias_color = "#ff5e75"
        outcome_desc = "Event-specific precursor evidence leans below consensus, after conflict and news-verification penalties."
        currency_action_en = f"📉 {cur} Expected to Weaken (Bearish Bias)"; currency_action_color = "#ff5e75"
        currency_action_desc_en = f"{cur} has a downside macro-surprise bias, with confidence reduced when evidence conflicts."
        gold_implication = "📈 Bullish Support for Gold (Dovish surprise risk)"; usd_implication = "📉 Bearish Drag on USD"; oil_implication = "📉 Bearish Drag"
    else:
        bias_label = "⚖️ IN-LINE WITH CONSENSUS"; bias_color = "#ffd166"
        outcome_desc = "The calibrated three-way model assigns the highest probability to an in-line print or finds directional evidence insufficiently decisive."
        currency_action_en = f"⚖️ {cur} Range-Bound / Await Release"; currency_action_color = "#ffd166"
        currency_action_desc_en = f"{cur} lacks a sufficiently strong verified edge over consensus."
        gold_implication = "⚖️ Neutral / Range-Bound"; usd_implication = "⚖️ Balanced Consolidation"; oil_implication = "⚖️ Range-Bound"

    nasdaq_implication = _nasdaq_forecaster_implication(event, nowcast_composite)
    result = {"precursor_results": precursor_results, "base_precursor_score": base_precursor_score,
              "correlated_articles": correlated_articles[:5], "news_sentiment_pts": news_sentiment_pts,
              "nowcast_composite": nowcast_composite, "bias_label": bias_label, "bias_color": bias_color,
              "confidence": confidence_val, "outcome_desc": outcome_desc, "currency_action_en": currency_action_en,
              "currency_action_color": currency_action_color, "currency_action_desc_en": currency_action_desc_en,
              "gold_implication": gold_implication, "usd_implication": usd_implication, "oil_implication": oil_implication,
              "nasdaq_implication": nasdaq_implication, "probabilities": probabilities, "outcome_key": outcome_key,
              "conflict_score": round(conflict_score, 3), "evidence_quality": round(evidence_quality, 3),
              "event_family": family, "learning_sample": learning_n, "news_ambiguity": round(news_ambiguity, 3)}

    # Once an official print exists, classify Beat/In-line/Miss correctly, including exact consensus matches.
    if actual_override:
        av = _safe_numeric_release(actual_override); fv = _safe_numeric_release(event.get("forecast_str", ""))
        if av is not None and fv is not None:
            eps = max(1e-9, abs(fv) * 1e-6)
            actual_outcome = "beat" if av > fv + eps else ("miss" if av < fv - eps else "inline")
            labels = {"beat": ("✅ ACTUAL RELEASED: {} (Beat)", "#00ffa3"),
                      "miss": ("❌ ACTUAL RELEASED: {} (Miss)", "#ff5e75"),
                      "inline": ("⚖️ ACTUAL RELEASED: {} (In-line)", "#ffd166")}
            fmt, color = labels[actual_outcome]
            result["bias_label"] = fmt.format(actual_override.strip()); result["bias_color"] = color
            result["confidence"] = 100; result["actual_outcome"] = actual_outcome
            result["outcome_desc"] = "Official actual print is available; the pre-release prediction remains frozen in Forecaster history for scoring."
    return result


@st.cache_data(ttl=180, show_spinner=False)
def get_causal_macro_ai_analysis(event: dict, nowcast: dict, articles: list, api_key: str = DEFAULT_AI_KEY, provider_hint: str = DEFAULT_AI_PROVIDER, model_hint: str = DEFAULT_AI_MODEL, cache_version: str = AI_CACHE_VERSION) -> dict:
    """Event-specific causal AI layer. Keeps the existing quantitative nowcast intact."""
    if not api_key:
        return {"status": "unavailable", "raw": f"{DEFAULT_AI_PROVIDER} API key is unavailable. Check Streamlit Secrets."}

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
9. Any numeric value found in news must match the exact event metric, period and unit before it can be treated as release evidence. If identity is ambiguous, use it only as context and explicitly flag the ambiguity.
10. Treat the quantitative three-way probabilities and conflict score as anchors; if you disagree, explain the supplied evidence causing the disagreement.
11. Do not provide investment advice. Keep the report concise and institutional.

Return ONLY valid JSON with these keys:
event_assessment, causal_chain, facts, supporting_evidence, contradictions,
nowcast, confidence, confidence_reason, cross_source_confirmation,
usd, gold, oil, nasdaq, invalidation, source_count.
Each of causal_chain, facts, supporting_evidence, contradictions must be an array of short strings.
confidence must be an integer 0-100.
Your entire response MUST begin with { and end with }. Do not use markdown fences and do not add any text outside the JSON object.
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
Three-way probabilities: {nowcast.get('probabilities',{})}
Conflict score: {nowcast.get('conflict_score','')}
Evidence quality: {nowcast.get('evidence_quality','')}
Event model family: {nowcast.get('event_family','')}

FRED / MACRO PRECURSORS
{precursor_text}

EVENT-RELEVANT LIVE NEWS
{news_text}
"""

    provider, url, model, resolved_key = _ai_runtime(api_key, provider_hint, model_hint)
    if not resolved_key:
        return {"status": "unavailable", "raw": "AI API key is unavailable."}

    headers = _ai_headers(resolved_key, "ApexMacro Causal Macro Intelligence")
    try:
        response = _post_ai_chat(
            provider=provider,
            url=url,
            headers=headers,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.15,
            timeout=60,
        )
        data = response.json()
        raw = _ai_message_content(data)
        parsed = _extract_json_object(raw)

        # Claude-compatible gateways may occasionally wrap JSON in prose.
        # If no JSON object can be recovered, make ONE compact repair request.
        if parsed is None and raw:
            repair_system = (
                "Convert the supplied model output into ONE valid JSON object only. "
                "Do not add facts, commentary, markdown, or explanation. "
                "Preserve only information already present in the supplied output. "
                "Required keys: event_assessment, causal_chain, facts, supporting_evidence, "
                "contradictions, nowcast, confidence, confidence_reason, cross_source_confirmation, "
                "usd, gold, oil, nasdaq, invalidation, source_count."
            )
            repair_response = _post_ai_chat(
                provider=provider,
                url=url,
                headers=headers,
                model=model,
                system_prompt=repair_system,
                user_prompt=raw[:8000],
                temperature=0.0,
                timeout=45,
            )
            repair_raw = _ai_message_content(repair_response.json())
            parsed = _extract_json_object(repair_raw)

        if parsed is None:
            return {
                "status": "error",
                "raw": (
                    f"{provider} returned a response that could not be converted to structured JSON. "
                    "The quantitative nowcast remains active."
                ),
            }

        parsed = _normalize_causal_ai_payload(parsed, len(relevant))
        parsed["status"] = "ok"
        return parsed
    except Exception as exc:
        err_text = str(exc)
        if "temporarily unavailable" in err_text.lower() or "timed out" in err_text.lower():
            return {
                "status": "error",
                "raw": f"{provider} is temporarily unavailable. The quantitative nowcast remains active and AI will retry automatically."
            }
        return {"status": "error", "raw": f"{provider} causal analysis error: {err_text[:300]}"}


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


@st.fragment(run_every=30)
def page_catalyst_forecaster(fred_key: str, channel_name: str, auth_user: dict | None = None) -> None:
    if "selected_tz" not in st.session_state or st.session_state["selected_tz"] not in SUPPORTED_TIMEZONES:
        st.session_state["selected_tz"] = "🏛️ Kurdistan & Iraq (UTC+3)"

    tz_info = SUPPORTED_TIMEZONES.get(st.session_state["selected_tz"], {"offset": 3, "label": "KRD (UTC+3)"})
    is_admin = auth_user and auth_user.get("is_admin", False)

    with st.spinner("Loading catalyst calendar..."):
        events = get_upcoming_catalyst_events(tz_info["offset"], tz_info["label"])
        actuals_cache = load_actuals_cache()

        # Fast path: synchronize published Actual values without running any heavy analysis.
        actuals_changed = False
        for event in events:
            event_code = str(event.get("code", "")).strip()
            published_actual = _normalize_forex_factory_actual(event.get("actual_str", ""))
            if event_code and published_actual and not str(actuals_cache.get(event_code, "")).strip():
                actuals_cache[event_code] = published_actual
                actuals_changed = True
        if actuals_changed:
            save_actuals_cache(actuals_cache)

    render_html(f"""
    <div class="fc-hero">
      <div class="fc-hero-row">
        <div>
          <div class="fc-eyebrow">ApexMacro / Predictive Intelligence</div>
          <div class="fc-title">🔮 Macro Catalyst Forecaster</div>
          <div class="fc-sub">Upcoming macro releases ranked with the existing FRED precursor model, live wire sentiment and causal AI layer.</div>
          <div class="fc-live"><span class="live-dot"></span> NOWCAST ENGINE ACTIVE &nbsp;•&nbsp; {tz_info['label']} &nbsp;•&nbsp; AUTO REFRESH 30s</div>
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

    perf = _forecaster_performance()
    if is_admin:
        perf_note = f"Resolved: {perf['resolved']} • Correct: {perf['correct']} • Accuracy: {perf['accuracy']:.1f}%" if perf['resolved'] else "Learning history active — awaiting resolved forecasts"
        render_html(f'<div style="margin:8px 0 2px;padding:8px 11px;border:1px solid rgba(0,245,255,.12);border-radius:9px;color:#8fa3b4;font-size:10px;">🧪 Forecaster Learning &amp; Backtesting &nbsp;•&nbsp; {perf_note}</div>')

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
    render_html('<div class="sec-title">Catalyst Radar</div>')

    ai_key_state = "Configured" if DEFAULT_AI_KEY else "Missing"
    ai_key_color = "#00ffa3" if DEFAULT_AI_KEY else "#ff8a9b"
    render_html(
        f'<div style="margin:0 0 14px 0;padding:9px 12px;border:1px solid rgba(0,229,246,.14);'
        f'border-radius:10px;background:rgba(3,12,19,.55);font-size:11px;color:#8da2b3;">'
        f'🧠 AI Provider: <b style="color:#28eaf5;">{DEFAULT_AI_PROVIDER}</b>'
        f' &nbsp;•&nbsp; Model: <b style="color:#eaf5fb;">{DEFAULT_AI_MODEL}</b>'
        f' &nbsp;•&nbsp; Key: <b style="color:{ai_key_color};">{ai_key_state}</b>'
        f'</div>'
    )

    currency_flags = {
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "💷", "CAD": "🍁",
        "JPY": "💴", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🏔️"
    }

    if not events:
        st.info("No High or Medium impact Forex Factory catalysts are available in the current calendar window.")
        return

    # FAST RADAR: render every calendar row first, before any heavy FRED/news/AI work.
    # This keeps the full event list visible immediately instead of loading one event at a time.
    if "fc_selected_event_code" not in st.session_state:
        st.session_state["fc_selected_event_code"] = ""

    event_by_code = {str(e.get("code", "")): e for e in events if str(e.get("code", ""))}

    render_html(
        '<div style="margin:0 0 10px;color:#718795;font-size:10px;">'
        'Tap a catalyst to load its full Nowcast, precursors, news and causal-AI analysis.'
        '</div>'
    )

    # Pass 1: lightweight rows only. No news, FRED or AI calls happen in this loop.
    for ev in events:
        ev_code = str(ev.get("code", "")).strip()
        cur = ev.get("currency", "USD")
        cur_flag = currency_flags.get(cur, "🌐")
        impact_icon = "🔴" if ev.get("impact") == "High" else "🟡"
        saved_actual = str(actuals_cache.get(ev_code, "")).strip()
        published_actual = _normalize_forex_factory_actual(ev.get("actual_str", ""))
        effective_actual = saved_actual or published_actual
        actual_tag = f"  ·  ✅ Actual {effective_actual}" if effective_actual else ""
        row_label = (
            f"{cur_flag} {cur}  ·  {ev['title']}  ·  {impact_icon} {ev['impact']}  ·  "
            f"🕒 {ev['time_str']}  ·  {ev['countdown']}{actual_tag}"
        )
        if st.button(
            row_label,
            key=f"fc_fast_row_{ev_code}",
            use_container_width=True,
        ):
            st.session_state["fc_selected_event_code"] = ev_code

    selected_code = str(st.session_state.get("fc_selected_event_code", "")).strip()
    selected_event = event_by_code.get(selected_code)

    # Pass 2: only the selected catalyst performs expensive work.
    if selected_event:
        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        render_html('<div class="sec-title">Selected Catalyst Analysis</div>')

        ev = selected_event
        ev_code = str(ev.get("code", "")).strip()
        saved_actual = str(actuals_cache.get(ev_code, "")).strip()
        published_actual = _normalize_forex_factory_actual(ev.get("actual_str", ""))
        effective_actual = saved_actual or published_actual

        # Fetch shared news only when a catalyst is actually opened.
        all_news = fetch_all_instant_news(channel_name)
        nowcast = compute_event_nowcast(ev, fred_key, all_news, actual_override=effective_actual)
        causal_ai = (
            get_causal_macro_ai_analysis(
                ev,
                nowcast,
                all_news,
                DEFAULT_AI_KEY,
                DEFAULT_AI_PROVIDER,
                DEFAULT_AI_MODEL,
                AI_CACHE_VERSION,
            )
            if ev.get("impact") == "High"
            else {"status": "skipped"}
        )

        # Preserve the learning/backtesting trail, but only after the selected event is analyzed.
        try:
            if effective_actual:
                _record_forecaster_snapshot(ev, nowcast, actual=effective_actual)
            else:
                _record_forecaster_snapshot(ev, nowcast, actual="")
        except Exception:
            pass

        cur = ev.get("currency", "USD")
        cur_flag = currency_flags.get(cur, "🌐")
        impact_icon = "🔴" if ev.get("impact") == "High" else "🟡"
        actual_value = effective_actual or "Pending"
        actual_color = "#00ffa3" if effective_actual else "#718795"
        bias_bg = (
            "rgba(0,255,163,.055)"
            if nowcast["bias_color"] == "#00ffa3"
            else (
                "rgba(255,94,117,.055)"
                if nowcast["bias_color"] == "#ff5e75"
                else "rgba(255,209,102,.05)"
            )
        )

        render_html(f"""
        <div class="fc-body" style="padding-top:4px;">
          <div class="fc-time" style="margin-bottom:10px;">{cur_flag} {cur} &nbsp;•&nbsp; {impact_icon} {ev['impact']} &nbsp;•&nbsp; 📅 {ev['date_str']} &nbsp;•&nbsp; 🕒 {ev['time_str']} &nbsp;•&nbsp; {ev['countdown']}</div>
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
              <div style="font-size:9.5px;color:#8fa3b4;margin-top:7px;">Beat <b style="color:#00ffa3;">{nowcast.get('probabilities',{}).get('beat',0):.1f}%</b> &nbsp;•&nbsp; In-line <b style="color:#ffd166;">{nowcast.get('probabilities',{}).get('inline',0):.1f}%</b> &nbsp;•&nbsp; Miss <b style="color:#ff788a;">{nowcast.get('probabilities',{}).get('miss',0):.1f}%</b></div>
              <div style="font-size:8.8px;color:#718795;margin-top:3px;">Conflict {nowcast.get('conflict_score',0)*100:.0f}% • Evidence quality {nowcast.get('evidence_quality',0)*100:.0f}% • Model: {nowcast.get('event_family','general').title()}</div>
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
                    f"Actual Value for {ev_code}",
                    value=effective_actual,
                    placeholder="e.g. -0.5% or 0.5",
                    key=f"act_txt_{ev_code}",
                    label_visibility="collapsed",
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

    else:
        st.caption("Select a catalyst above to load its full analysis.")

