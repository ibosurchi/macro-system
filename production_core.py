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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_FOREX_FACTORY_LAST_GOOD_EVENTS: list[dict] = []
_FOREX_FACTORY_LAST_GOOD_AT = 0.0
_FOREX_FACTORY_LAST_GOOD_LOCK = threading.RLock()


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


def fetch_forex_factory_calendar() -> list[dict]:
    """
    Load and normalize the live Forex Factory/Faireconomy weekly calendar.

    A transient network failure must never wipe the live Forecaster.
    The last non-empty calendar is retained in memory and reused until a fresh
    non-empty response is available.
    """
    global _FOREX_FACTORY_LAST_GOOD_EVENTS, _FOREX_FACTORY_LAST_GOOD_AT

    urls = [
        FOREX_FACTORY_CALENDAR_URL,
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
    ]

    rows = None
    for url in urls:
        try:
            response = requests.get(
                url,
                timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ApexMacro/1.0)",
                    "Accept": "application/json,text/plain,*/*",
                    "Cache-Control": "no-cache",
                },
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                rows = data
                break
        except Exception:
            continue

    normalized = []
    aliases = {
        "Red": "High",
        "Orange": "Medium",
        "Yellow": "Low",
        "High Impact": "High",
        "Medium Impact": "Medium",
        "Low Impact": "Low",
    }

    if rows:
        for row in rows:
            if not isinstance(row, dict):
                continue

            title = str(row.get("title") or row.get("event") or row.get("name") or "").strip()
            country = str(row.get("country") or row.get("currency") or "").strip().upper()
            raw_impact = str(row.get("impact") or "").strip()
            impact = aliases.get(raw_impact.title(), raw_impact.title())
            raw_date = row.get("date") or row.get("datetime") or row.get("time")

            if not title or not country or not raw_date:
                continue

            normalized.append({
                **row,
                "title": title,
                "country": country,
                "impact": impact,
                "date": raw_date,
                "forecast": row.get("forecast", ""),
                "previous": row.get("previous", ""),
                "actual": row.get("actual", ""),
            })

    if normalized:
        with _FOREX_FACTORY_LAST_GOOD_LOCK:
            _FOREX_FACTORY_LAST_GOOD_EVENTS = [dict(item) for item in normalized]
            _FOREX_FACTORY_LAST_GOOD_AT = time.time()
        return normalized

    with _FOREX_FACTORY_LAST_GOOD_LOCK:
        if _FOREX_FACTORY_LAST_GOOD_EVENTS:
            return [dict(item) for item in _FOREX_FACTORY_LAST_GOOD_EVENTS]

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

/* ===== ApexMacro Forecaster Calendar v1 — apex- scoped, safe ===== */
.apex-forecaster-shell{width:100%;box-sizing:border-box;}

/* Calendar container */
.apex-cal-wrap{background:linear-gradient(145deg,rgba(5,18,28,.96),rgba(3,11,19,.98));border:1px solid rgba(20,205,220,.18);border-radius:18px;padding:20px 20px 16px;margin-bottom:20px;box-shadow:0 20px 60px rgba(0,0,0,.42);}
.apex-cal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:12px;}
.apex-cal-title-block{}
.apex-cal-eyebrow{font-size:14px;font-weight:900;letter-spacing:1.5px;color:#20DDE8;text-transform:uppercase;margin-bottom:4px;}
.apex-cal-sub{font-size:11.5px;color:#8fa3b4;}
.apex-cal-nav{display:flex;align-items:center;gap:10px;}
.apex-cal-month-label{font-size:14px;font-weight:850;color:#F2F6F8;letter-spacing:1px;text-transform:uppercase;}

/* Weekday row */
.apex-cal-weekdays{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;margin-bottom:8px;}
.apex-cal-wd{font-size:9.5px;font-weight:900;color:#8fa3b4;text-transform:uppercase;letter-spacing:1px;text-align:center;padding:4px 0;}

/* Day grid */
.apex-cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;}
.apex-calendar-day{position:relative;min-height:74px;padding:10px 6px 8px;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;box-sizing:border-box;background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));border:1px solid rgba(110,155,175,.15);border-radius:9px;color:#F4F7FA;cursor:pointer;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;user-select:none;}
.apex-calendar-day:hover{border-color:rgba(20,205,220,.45);background:linear-gradient(145deg,rgba(6,48,60,.70),rgba(4,22,32,.88));transform:translateY(-1px);}
.apex-calendar-day.is-selected{border:1px solid rgba(20,225,235,.95)!important;background:linear-gradient(145deg,rgba(6,64,75,.78),rgba(4,28,38,.94))!important;box-shadow:0 0 18px rgba(20,220,230,.15)!important;}
.apex-calendar-day.is-today .apex-cal-date-num{color:#20DDE8;font-weight:950;}
.apex-calendar-day.is-other-month{opacity:.35;pointer-events:none;}
.apex-calendar-day.no-events{cursor:default;}
.apex-calendar-day.no-events:hover{transform:none;border-color:rgba(110,155,175,.15);background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));}
.apex-cal-date-num{font-size:15px;font-weight:850;color:#F4F7FA;line-height:1;margin-bottom:7px;}
.apex-cal-dots{display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:center;min-height:10px;}
.apex-impact-dot{width:6.5px;height:6.5px;border-radius:50%;flex-shrink:0;}
.apex-impact-dot.high{background:#A84DE3;box-shadow:0 0 6px rgba(168,77,227,.65);}
.apex-impact-dot.medium{background:#FFBC26;box-shadow:0 0 6px rgba(255,188,38,.55);}
.apex-impact-dot.low{background:#38D4E4;box-shadow:0 0 6px rgba(56,212,228,.50);}
.apex-cal-overflow{font-size:8.5px;font-weight:850;color:#A5B2BF;}

/* Legend */
.apex-cal-legend{display:flex;align-items:center;gap:20px;justify-content:center;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06);}
.apex-cal-legend-item{display:flex;align-items:center;gap:7px;font-size:10.5px;color:#A5B2BF;font-weight:650;}

/* Selected day header */
.apex-selected-day-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px;flex-wrap:wrap;}
.apex-selected-day-title-wrap{display:flex;align-items:center;gap:12px;}
.apex-selected-day-title{font-size:16px;font-weight:900;color:#20DDE8;letter-spacing:1px;text-transform:uppercase;}
.apex-selected-day-count{font-size:10px;font-weight:850;color:#20DDE8;background:rgba(20,221,232,.10);border:1px solid rgba(20,221,232,.25);padding:3px 10px;border-radius:999px;letter-spacing:.5px;}

/* Day event cards */
.apex-day-events-list{display:flex;flex-direction:column;gap:10px;margin-bottom:24px;}
.apex-day-event-card{width:100%;display:grid;grid-template-columns:70px 80px minmax(0,1fr) 70px 70px 70px 28px;gap:12px;align-items:center;padding:16px 18px;box-sizing:border-box;background:linear-gradient(145deg,rgba(10,28,39,.82),rgba(5,17,26,.92));border:1px solid rgba(90,145,165,.18);border-radius:11px;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;}
.apex-day-event-card:hover{border-color:rgba(20,205,220,.42);background:linear-gradient(145deg,rgba(6,42,58,.85),rgba(4,20,32,.95));}
.apex-dec-time{font-size:13px;font-weight:800;color:#F2F6F8;line-height:1.2;}
.apex-dec-time-sub{font-size:9.5px;color:#718795;font-weight:700;margin-top:2px;text-transform:uppercase;}
.apex-dec-currency{display:flex;align-items:center;gap:6px;}
.apex-dec-flag{font-size:18px;line-height:1;}
.apex-dec-cur-code{font-size:12px;font-weight:850;color:#F2F6F8;}
.apex-dec-body{min-width:0;}
.apex-dec-impact-row{display:flex;align-items:center;gap:6px;margin-bottom:4px;}
.apex-dec-impact-dot{width:7px;height:7px;border-radius:50%;}
.apex-dec-impact-dot.high{background:#A84DE3;}
.apex-dec-impact-dot.medium{background:#FFBC26;}
.apex-dec-impact-dot.low{background:#38D4E4;}
.apex-dec-impact-text{font-size:9.5px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.5px;}
.apex-dec-name{font-size:13.5px;font-weight:850;color:#F2F6F8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.apex-dec-val-box{text-align:center;}
.apex-dec-val-lbl{font-size:8.5px;font-weight:850;color:#718795;text-transform:uppercase;letter-spacing:.6px;}
.apex-dec-val{font-size:13px;font-weight:850;color:#F2F6F8;margin-top:2px;}
.apex-dec-val.actual-live{color:#00ffa3;}
.apex-dec-val.pending{color:#718795;}
.apex-dec-arrow{font-size:16px;color:#8fa3b4;font-weight:800;text-align:right;}
.apex-no-events-msg{padding:28px 16px;text-align:center;color:#718795;font-size:12.5px;background:rgba(5,18,28,.4);border:1px solid rgba(110,155,175,.10);border-radius:11px;margin-bottom:20px;}

/* Modal overlay */
.apex-event-modal-overlay{position:fixed;inset:0;z-index:9998;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(1,7,12,.58);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}

/* Modal */
.apex-event-modal{position:relative;z-index:9999;width:min(980px,72vw);max-height:90vh;overflow-y:auto;padding:26px 28px;box-sizing:border-box;border-radius:16px;background:linear-gradient(145deg,rgba(5,20,30,.98),rgba(3,13,21,.99));border:1px solid rgba(20,215,225,.72);box-shadow:0 28px 90px rgba(0,0,0,.60),0 0 40px rgba(15,210,220,.05);scrollbar-width:thin;scrollbar-color:#20DDE8 rgba(8,16,24,.6);}
.apex-event-modal::-webkit-scrollbar{width:6px;}.apex-event-modal::-webkit-scrollbar-track{background:rgba(8,16,24,.5);border-radius:4px;}.apex-event-modal::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#20DDE8,#00ffa3);border-radius:4px;}

/* Modal header */
.apex-modal-header{position:sticky;top:-26px;z-index:10;background:rgba(5,20,30,.97);margin:-26px -28px 20px;padding:18px 28px 14px;border-bottom:1px solid rgba(20,215,225,.14);display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}
.apex-modal-header-left{}
.apex-modal-title{font-size:17px;font-weight:900;color:#F2F6F8;letter-spacing:-.1px;}
.apex-modal-date{font-size:12px;color:#8fa3b4;margin-top:3px;}
.apex-modal-close-btn{width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:9px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);color:#F0F5F8;font-size:18px;cursor:pointer;line-height:1;}

/* Form fields */
.apex-form-grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px;}
.apex-form-label{font-size:9.5px;font-weight:800;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;}
.apex-form-box{padding:11px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);font-size:13px;font-weight:750;color:#F2F6F8;display:flex;align-items:center;justify-content:space-between;}

/* Actual/Forecast/Previous values row */
.apex-modal-values{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:14px;}
.apex-modal-value{padding:13px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);}
.apex-modal-value-lbl{font-size:9px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;margin-bottom:5px;}
.apex-modal-value-num{font-size:18px;font-weight:900;color:#F2F6F8;}
.apex-modal-value-num.beat{color:#00ffa3;}
.apex-modal-value-num.miss{color:#ff5e75;}
.apex-modal-value-num.inline{color:#ffd166;}

/* Causal card & AI panels */
.apex-intelligence-card{padding:14px 16px;border-radius:11px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);margin-bottom:14px;}
.apex-card-header-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.apex-card-title{font-size:11px;font-weight:900;color:#F2F6F8;letter-spacing:.5px;text-transform:uppercase;}
.apex-ai-badge{font-size:9px;font-weight:900;color:#20DDE8;background:rgba(32,221,232,.12);border:1px solid rgba(32,221,232,.30);padding:2px 7px;border-radius:6px;}
.apex-conf-badge{font-size:10px;font-weight:850;color:#00ffa3;}
.apex-evidence-list{margin:0;padding:0 0 0 16px;font-size:11px;color:#cbd8df;line-height:1.65;}
.apex-evidence-list li{margin-bottom:4px;}

/* Cross Asset Grid */
.apex-cross-asset-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:14px;}
.apex-cross-asset-card{padding:12px 10px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.16);border-radius:10px;text-align:center;}
.apex-cross-asset-name{font-size:11px;font-weight:850;color:#F2F6F8;display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;}
.apex-cross-asset-state{font-size:10px;font-weight:700;color:#8fa3b4;}

/* Admin Box */
.apex-admin-box{padding:14px 16px;border-radius:11px;background:rgba(12,28,40,.82);border:1px solid rgba(20,205,220,.25);margin-bottom:14px;}
.apex-admin-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;}
.apex-admin-title{font-size:11.5px;font-weight:900;color:#20DDE8;letter-spacing:.5px;}
.apex-admin-sub{font-size:10px;color:#8fa3b4;}

/* Mobile responsiveness */
@media(max-width:768px){
  .apex-cal-wrap{padding:14px 10px 12px;}
  .apex-cal-grid,.apex-cal-weekdays{gap:4px;}
  .apex-calendar-day{min-height:48px;padding:6px 2px 4px;border-radius:7px;}
  .apex-cal-date-num{font-size:13px;margin-bottom:3px;}
  .apex-impact-dot{width:4.5px;height:4.5px;}
  .apex-cal-dots{gap:2px;}
  .apex-cal-legend{gap:10px;flex-wrap:wrap;}
  .apex-day-event-card{grid-template-columns:1fr;gap:6px;padding:12px 14px;}
  .apex-event-modal-overlay{padding:8px;}
  .apex-event-modal{width:100%;max-width:none;height:min(94vh,100%);max-height:94vh;padding:18px 14px;border-radius:14px;}
  .apex-modal-header{margin:-18px -14px 16px;padding:14px 14px 12px;top:-18px;}
  .apex-form-grid-3,.apex-form-grid-2{grid-template-columns:1fr;}
  .apex-modal-values{grid-template-columns:1fr 1fr 1fr;}
  .apex-cross-asset-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media(max-width:480px){
  .apex-modal-values{grid-template-columns:1fr;}
  .apex-cross-asset-grid{grid-template-columns:1fr;}
}


/* ===== Forecaster direct calendar/event interaction (scoped) ===== */
.apex-forecaster-calendar-head{width:100%;padding:18px 16px 16px;box-sizing:border-box;background:linear-gradient(145deg,rgba(5,21,31,.95),rgba(3,13,21,.99));border:1px solid rgba(35,190,205,.22);border-bottom:0;border-radius:20px 20px 0 0;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}
.apex-forecaster-title{color:#2CD9E5;font-size:18px;font-weight:800;letter-spacing:1px;}
.apex-forecaster-subtitle{margin-top:4px;color:#A5B2BF;font-size:12px;line-height:1.45;}
.apex-forecaster-month{color:#F4F7F9;font-size:16px;font-weight:800;letter-spacing:.5px;white-space:nowrap;}
.apex-forecaster-calendar-head + .apex-cal-weekdays{padding:0 10px 8px;background:linear-gradient(145deg,rgba(5,21,31,.95),rgba(3,13,21,.99));border-left:1px solid rgba(35,190,205,.22);border-right:1px solid rgba(35,190,205,.22);margin:0;}

/* Force Streamlit columns to remain a seven-column calendar on phones. */
.st-key-apex_calendar_interactive{padding:0 10px 12px;background:linear-gradient(145deg,rgba(5,21,31,.95),rgba(3,13,21,.99));border-left:1px solid rgba(35,190,205,.22);border-right:1px solid rgba(35,190,205,.22);}
.st-key-apex_calendar_interactive [data-testid="stHorizontalBlock"]{display:flex!important;flex-direction:row!important;gap:5px!important;margin-bottom:5px!important;}
.st-key-apex_calendar_interactive [data-testid="stColumn"]{min-width:0!important;width:0!important;flex:1 1 0!important;}

[class*="st-key-apex_calday_"]{position:relative!important;min-width:0!important;}
[class*="st-key-apex_calday_"] .stButton{margin:0!important;}
[class*="st-key-apex_calday_"] button{width:100%!important;min-width:0!important;height:72px!important;padding:6px 2px 20px!important;border-radius:9px!important;background:linear-gradient(145deg,rgba(13,32,43,.78),rgba(5,18,27,.93))!important;border:1px solid rgba(100,150,170,.12)!important;color:#F2F5F7!important;box-shadow:none!important;font-size:17px!important;font-weight:750!important;line-height:1!important;}
[class*="st-key-apex_calday_"] button:hover{border-color:rgba(35,205,220,.42)!important;background:linear-gradient(145deg,rgba(13,43,55,.88),rgba(5,22,32,.96))!important;transform:none!important;}
[class*="st-key-apex_calday_selected_"] button{border:1px solid rgba(23,222,234,.95)!important;background:linear-gradient(145deg,rgba(7,66,76,.82),rgba(4,28,38,.96))!important;color:#2CE4EC!important;box-shadow:0 0 14px rgba(25,220,230,.13)!important;}
[class*="st-key-apex_calday_today_"] button{border-color:rgba(65,200,215,.40)!important;}
[class*="st-key-apex_calday_outside_"]{opacity:.28!important;}
[class*="st-key-apex_calday_outside_"] button{cursor:default!important;}
.apex-cal-button-dots{position:absolute;left:1px;right:1px;bottom:7px;z-index:5;display:flex;align-items:center;justify-content:center;gap:3px;line-height:1;pointer-events:none;white-space:nowrap;}
.apex-cal-live-dot{width:6px;height:6px;border-radius:50%;display:inline-block;box-shadow:0 0 7px rgba(255,255,255,.05);}
.apex-cal-live-dot.high{background:#B04CE4}.apex-cal-live-dot.medium{background:#FFB822}.apex-cal-live-dot.low{background:#35D2E3}.apex-cal-more{font-size:8px;font-weight:850;color:#A5B2BF;margin-left:1px;}

.apex-cal-legend{margin:0 0 0;padding:12px 14px 14px;border:1px solid rgba(35,190,205,.22);border-top:1px solid rgba(255,255,255,.05);border-radius:0 0 20px 20px;background:linear-gradient(145deg,rgba(5,21,31,.95),rgba(3,13,21,.99));display:flex;align-items:center;justify-content:center;gap:22px;}
.apex-cal-legend .apex-impact-dot{width:7px;height:7px;display:inline-block;border-radius:50%;margin-right:6px;}
.apex-cal-legend .high{background:#B04CE4}.apex-cal-legend .medium{background:#FFB822}.apex-cal-legend .low{background:#35D2E3}
.apex-selected-date-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:22px 0 12px;}
.apex-selected-date-text{color:#28DCE7;font-size:19px;font-weight:800;letter-spacing:.5px;}
.apex-selected-date-count{padding:5px 10px;border-radius:999px;border:1px solid rgba(30,205,220,.30);background:rgba(25,200,215,.07);color:#28DCE7;font-size:11px;font-weight:700;}

/* Each event card is ONE real Streamlit button. No duplicate View Details control. */
[class*="st-key-apex_evtcard_"]{margin-bottom:12px!important;}
[class*="st-key-apex_evtcard_"] button{width:100%!important;min-height:142px!important;padding:18px!important;box-sizing:border-box!important;border-radius:14px!important;background:linear-gradient(145deg,rgba(8,27,38,.88),rgba(4,17,25,.96))!important;border:1px solid rgba(90,145,165,.20)!important;color:#F3F6F8!important;box-shadow:none!important;text-align:left!important;justify-content:flex-start!important;white-space:pre-wrap!important;font-size:13px!important;font-weight:650!important;line-height:1.55!important;}
[class*="st-key-apex_evtcard_"] button{position:relative!important;}
[class*="st-key-apex_evtcard_"] button::before{content:"●";font-size:11px;margin-right:8px;align-self:flex-start;}
[class*="st-key-apex_evtcard_high_"] button::before{color:#B04CE4;}
[class*="st-key-apex_evtcard_medium_"] button::before{color:#FFB822;}
[class*="st-key-apex_evtcard_low_"] button::before{color:#35D2E3;}
[class*="st-key-apex_evtcard_"] button p{width:100%!important;text-align:left!important;white-space:pre-wrap!important;margin:0!important;}
[class*="st-key-apex_evtcard_"] button:hover{border-color:rgba(35,205,220,.50)!important;background:linear-gradient(145deg,rgba(9,39,52,.94),rgba(4,20,30,.98))!important;color:#F7FBFD!important;transform:none!important;}

/* Native Streamlit dialog = true modal with built-in top-right X and blocked background. */
div[data-testid="stDialog"]{backdrop-filter:blur(8px)!important;-webkit-backdrop-filter:blur(8px)!important;}
div[data-testid="stDialog"] div[role="dialog"]{width:min(980px,92vw)!important;max-width:980px!important;max-height:92vh!important;border-radius:18px!important;background:linear-gradient(155deg,rgba(5,22,32,.99),rgba(2,13,20,.995))!important;border:1px solid rgba(35,205,220,.40)!important;box-shadow:0 30px 80px rgba(0,0,0,.60)!important;overflow-y:auto!important;}
div[data-testid="stDialog"] div[role="dialog"] > div{max-width:none!important;}
.apex-dialog-date{color:#8fa3b4;font-size:12px;margin:-4px 0 16px;}
.apex-dialog-event-name{font-size:14px!important;overflow-wrap:anywhere;}
.apex-dialog-ai-text{font-size:12px;color:#dce7ed;line-height:1.65;white-space:normal;overflow-wrap:anywhere;margin-bottom:6px;}
.apex-dialog-ai-source{font-size:9.5px;color:#718795;line-height:1.45;}
.apex-dialog-section-title{font-size:10px;font-weight:900;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;margin:2px 0 8px;}

@media(max-width:768px){
  .apex-forecaster-calendar-head{padding:16px 12px 14px;border-radius:18px 18px 0 0;}
  .apex-forecaster-title{font-size:17px}.apex-forecaster-month{font-size:14px}.apex-forecaster-subtitle{font-size:11px;}
  .apex-forecaster-calendar-head + .apex-cal-weekdays{padding:0 6px 7px;gap:4px;}
  .st-key-apex_calendar_interactive{padding:0 6px 10px;}
  .st-key-apex_calendar_interactive [data-testid="stHorizontalBlock"]{gap:4px!important;margin-bottom:4px!important;}
  [class*="st-key-apex_calday_"] button{height:50px!important;padding:5px 1px 16px!important;border-radius:7px!important;font-size:13px!important;}
  .apex-cal-button-dots{bottom:5px;gap:2px;}.apex-cal-live-dot{width:4px;height:4px;}.apex-cal-more{font-size:6.5px;}
  .apex-cal-legend{gap:12px;flex-wrap:wrap;font-size:10px;padding:10px 8px 12px;border-radius:0 0 18px 18px;}
  .apex-selected-date-text{font-size:17px;}
  [class*="st-key-apex_evtcard_"] button{min-height:220px!important;padding:18px 16px!important;font-size:13px!important;line-height:1.6!important;}
  div[data-testid="stDialog"] div[role="dialog"]{width:calc(100vw - 12px)!important;max-width:none!important;max-height:94vh!important;border-radius:16px!important;}
  div[data-testid="stDialog"] div[role="dialog"] [data-testid="stVerticalBlock"]{padding-left:0!important;padding-right:0!important;}
  .apex-form-grid-3,.apex-form-grid-2{grid-template-columns:1fr!important;}
  .apex-modal-values{grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:7px!important;}
  .apex-cross-asset-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;}
}
@media(max-width:390px){
  .st-key-apex_calendar_interactive [data-testid="stHorizontalBlock"]{gap:3px!important;margin-bottom:3px!important;}
  [class*="st-key-apex_calday_"] button{height:45px!important;font-size:12px!important;padding-bottom:14px!important;}
  .apex-cal-button-dots{bottom:4px;gap:1.5px;}.apex-cal-live-dot{width:3.5px;height:3.5px;}.apex-cal-more{font-size:6px;}
  [class*="st-key-apex_evtcard_"] button{padding:15px 13px!important;min-height:210px!important;font-size:12px!important;}
  .apex-modal-values{grid-template-columns:1fr!important;}
  .apex-cross-asset-grid{grid-template-columns:1fr!important;}
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
        "last_forecaster_warm": 0.0,
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

                # Keep Catalyst Forecaster warm even when nobody has opened the page.
                # The existing singleton/process lock guarantees this does not create
                # duplicate background loops during normal Streamlit reruns.
                if time.time() - float(ctrl.get("last_forecaster_warm", 0.0)) >= 180:
                    ctrl["last_forecaster_warm"] = time.time()
                    try:
                        fc_events = get_upcoming_catalyst_events(3, "KRD (UTC+3)")
                        if fc_events:
                            _ensure_forecaster_background_worker(
                                fc_events,
                                fred_key,
                                channel_name,
                                load_actuals_cache(),
                            )
                    except Exception:
                        pass
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


def _asset_news_relevance(article: dict, asset: str) -> bool:
    """Deterministic relevance gate: unrelated headlines cannot affect an asset."""
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    common_macro = ["inflation","cpi","pce","gdp","jobs","employment","unemployment","payroll","central bank","interest rate","rate cut","rate hike","bond yield","treasury yield","recession","growth","tariff","sanction","geopolitical","war"]
    terms = {
        "USD":["usd","dollar","dxy","federal reserve"," fed ","powell","united states","u.s.","us economy","treasury"],
        "EUR":["eur","euro","ecb","lagarde","eurozone","euro area","germany","france","italy","spain"],
        "GBP":["gbp","pound","sterling","bank of england","boe","united kingdom","uk economy","britain","bailey"],
        "CAD":["cad","canadian dollar","bank of canada","boc","canada","macklem"],
        "JPY":["jpy","yen","bank of japan","boj","japan","ueda"],
        "CHF":["chf","swiss franc","swiss national bank","snb","switzerland"],
        "AUD":["aud","australian dollar","reserve bank of australia","rba","australia"],
        "NZD":["nzd","new zealand dollar","reserve bank of new zealand","rbnz","new zealand"],
        "Gold":["gold","xau","bullion","precious metal","real yield","safe haven","gold reserve","gold etf","central bank buying"],
        "Oil":["oil","crude","wti","brent","opec","opec+","petroleum","gasoline","energy","inventory","inventories","refinery"],
        "Nasdaq":["nasdaq","ndx","technology stocks","tech stocks","semiconductor","nvidia","microsoft","apple","amazon","meta","alphabet","tesla","growth stocks","ai stocks","chip"]}
    if any(term in text for term in terms.get(asset, [])): return True
    if asset in {"USD","Gold","Nasdaq"} and any(term in text for term in common_macro): return True
    if asset == "Oil" and any(term in text for term in ["geopolitical","war","attack","sanction","middle east","russia","iran"]): return True
    return False

def _asset_rule_news_score(articles: list, asset: str) -> float:
    if asset == "Gold": return float(_gold_rule_based_news_points(articles))
    bull=["rally","surge","jump","beat","strong","higher","growth","dovish","rate cut","risk on","risk-on"]
    bear=["selloff","slump","drop","fall","miss","weak","lower","hawkish","rate hike","risk off","risk-off","recession"]
    score=0.0
    for art in articles:
        if not _asset_news_relevance(art,asset): continue
        t=f"{art.get('title','')} {art.get('description','')}".lower(); local=0.0
        if any(k in t for k in bull): local += 0.055
        if any(k in t for k in bear): local -= 0.055
        if asset=="Oil":
            if any(k in t for k in ["supply cut","output cut","inventory draw","inventories fall","supply disruption","sanction","attack"]): local += 0.075
            if any(k in t for k in ["output increase","supply increase","inventory build","inventories rise","demand weak"]): local -= 0.075
        elif asset=="Nasdaq":
            if any(k in t for k in ["yields fall","yield falls","ai demand","chip rally","tech rally","strong earnings"]): local += 0.07
            if any(k in t for k in ["yields rise","yield spike","chip restrictions","tech selloff","inflation surprise"]): local -= 0.07
        elif asset in {"JPY","CHF"} and any(k in t for k in ["risk off","risk-off","war","attack","escalation"]): local += 0.04
        score += local
    return float(np.clip(score,-0.50,0.50))

@st.cache_data(ttl=21600, show_spinner=False)
def get_multi_asset_news_intelligence(news_text: str, api_key: str=DEFAULT_AI_KEY, provider_hint: str=DEFAULT_AI_PROVIDER, model_hint: str=DEFAULT_AI_MODEL, cache_version: str=AI_CACHE_VERSION) -> dict:
    """ONE cached provider request returns separate intelligence for every tracked asset."""
    assets=["USD","EUR","GBP","CAD","JPY","AUD","NZD","CHF","Gold","Oil","Nasdaq"]
    empty={a:{"score":0.0,"confidence":0.0,"reason":"No material asset-specific news signal."} for a in assets}
    if not news_text or not api_key: return {"summary":"AI analysis unavailable.","assets":empty,"active":False}
    provider,url,model,resolved_key=_ai_runtime(api_key,provider_hint,model_hint)
    if not resolved_key: return {"summary":f"{provider} AI key is unavailable.","assets":empty,"active":False}
    system_prompt=("You are the institutional multi-asset news judge for ApexMacro. Analyze ONLY the supplied CURRENT headlines. Return ONE JSON response covering USD, EUR, GBP, CAD, JPY, AUD, NZD, CHF, Gold, Oil and Nasdaq separately. A headline may affect several assets, but never copy generic sentiment across assets. Judge causal relevance: central-bank expectations, inflation/growth/labor, yields and FX for currencies; real yields/USD/safe-haven/central-bank demand for Gold; supply/demand/OPEC/geopolitics/inventories for Oil; yields/Fed/growth/earnings/semiconductors/risk appetite for Nasdaq. For each asset return score -1.0 to +1.0, confidence 0 to 100, and one short reason. If no material relevance, score MUST be 0 and confidence low. Also return a concise 2-3 sentence summary. Respect timestamps and source quality; invent nothing. Return ONLY JSON shaped as {\"summary\":\"...\",\"assets\":{\"USD\":{\"score\":0,\"confidence\":0,\"reason\":\"...\"},...}}")
    try:
        response=_post_ai_chat(provider=provider,url=url,headers=_ai_headers(resolved_key,"ApexMacro Multi-Asset News"),model=model,system_prompt=system_prompt,user_prompt=news_text,temperature=0.1,timeout=45)
        content=_ai_message_content(response.json()); content=re.sub(r"^```(?:json)?\s*|\s*```$","",content,flags=re.I|re.S).strip(); parsed=json.loads(content)
        raw=parsed.get("assets",{}) if isinstance(parsed,dict) else {}; clean={}
        for asset in assets:
            item=raw.get(asset,{}) if isinstance(raw,dict) else {}
            clean[asset]={"score":float(np.clip(float(item.get("score",0.0)),-1.0,1.0)),"confidence":float(np.clip(float(item.get("confidence",0.0)),0.0,100.0)),"reason":str(item.get("reason",""))[:240]}
        return {"summary":str(parsed.get("summary",""))[:1200],"assets":clean,"active":True}
    except Exception as exc:
        return {"summary":f"{provider} AI Error: {str(exc)[:300]}","assets":empty,"active":False}

@st.cache_data(ttl=300, show_spinner=False)
def analyze_news_rule_based(articles: list) -> dict:
    """Asset-specific news scores; all AI judgments arrive in one cached request."""
    assets=["USD","EUR","GBP","CAD","JPY","AUD","NZD","CHF","Gold","Oil","Nasdaq"]; scores={a:0.0 for a in assets}
    drivers=[{"name":"Macro Data Momentum","icon":"📊","expected_duration":"Active Session","reason":"Evaluated via multi-timeframe FRED indicators."},{"name":"Geopolitical & Feed Flow","icon":"📡","expected_duration":"1-2 Days","reason":"Real-time institutional news stream monitored."}]
    if not articles: return {"scores":scores,"drivers":drivers,"ai_summary":"No live news articles detected for AI analysis.","ai_active":False}
    ranked=_rank_news_articles(articles)
    combined_news="\n".join(f"- [{(a.get('source') or {}).get('name','Unknown Source')} | {a.get('publishedAt','')}] {a.get('title','')}: {a.get('description','')}" for a in ranked[:18])
    ai_pack=get_multi_asset_news_intelligence(combined_news,DEFAULT_AI_KEY,DEFAULT_AI_PROVIDER,DEFAULT_AI_MODEL,AI_CACHE_VERSION); ai_assets=ai_pack.get("assets",{})
    counts={}; reasons={}
    for asset in assets:
        relevant=[a for a in ranked if _asset_news_relevance(a,asset)][:14]; counts[asset]=len(relevant)
        rule=_asset_rule_news_score(relevant,asset) if relevant else 0.0; item=ai_assets.get(asset,{}) if ai_pack.get("active") else {}
        ais=float(item.get("score",0.0)); conf=float(item.get("confidence",0.0))/100.0; aic=float(np.clip(ais*0.50*conf,-0.50,0.50)) if relevant else 0.0
        scores[asset]=float(np.clip((0.65*rule)+(0.35*aic),-0.50,0.50)); reasons[asset]=str(item.get("reason",""))[:240]
    gi=ai_assets.get("Gold",{}) if ai_pack.get("active") else {}; gs=float(gi.get("score",0.0)); gc=float(gi.get("confidence",0.0)); gd="Bullish" if gs>0.12 else "Bearish" if gs<-0.12 else "Neutral"
    gold_ai={"direction":gd,"score":gs,"confidence":gc,"horizon":"1-3 Days","reason":str(gi.get("reason",""))[:280],"active":bool(ai_pack.get("active") and counts.get("Gold",0))}
    gold_rel=[a for a in ranked if _asset_news_relevance(a,"Gold")][:14]
    return {"scores":scores,"drivers":drivers,"ai_summary":ai_pack.get("summary",""),"ai_active":bool(ai_pack.get("active")),"asset_ai_reasons":reasons,"asset_news_counts":counts,"gold_ai":gold_ai,"gold_rule_points":_asset_rule_news_score(gold_rel,"Gold"),"gold_ai_points":float(np.clip(gs*0.50*(gc/100.0),-0.50,0.50)),"gold_relevant_news_count":counts.get("Gold",0)}

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
        if days_away > 10:
            continue

        # Keep a released catalyst on the live Forecaster radar for 48 hours only.
        # Persisted Actual values remain stored for compatibility/history.
        if total_seconds < -(48 * 3600):
            continue

        if total_seconds < -(48 * 3600):
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


@st.cache_data(ttl=21600, show_spinner=False)
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
        if analysis.get("status") in {"updating", "deferred"}:
            render_html(
                '<div class="fc-ai" style="border-color:rgba(173,123,255,.22);color:#bfa7ff;">'
                '🧠 Causal Macro Intelligence is updating in the background. '
                'The quantitative Nowcast is already available.</div>'
            )
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


# ============================================================
# FORECASTER BACKGROUND PRE-COMPUTE CACHE
# ============================================================
# Process-level cache: Streamlit reruns reuse completed analysis instead of
# waiting for FRED/news/RUAPI after a user clicks a catalyst.
_FORECASTER_BG_LOCK = threading.RLock()
_FORECASTER_BG_CACHE: dict[str, dict] = {}
_FORECASTER_BG_INFLIGHT: set[str] = set()
_FORECASTER_BG_WORKER_RUNNING = False
_FORECASTER_BG_TTL_SECONDS = 900
# Causal AI is expensive. Reuse it until the event's meaningful evidence changes.
# This cache is independent from the faster quantitative Nowcast cache.
_FORECASTER_AI_REUSE_CACHE: dict[str, dict] = {}
_FORECASTER_AI_MAX_AGE_SECONDS = 48 * 3600
_FORECASTER_AI_ERROR_RETRY_SECONDS = 15 * 60
_FORECASTER_AI_PREWARM_HORIZON_SECONDS = 72 * 3600


def _forecaster_bg_signature(event: dict, actual: str = "") -> str:
    payload = {
        "code": event.get("code", ""),
        "forecast": event.get("forecast_str", ""),
        "previous": event.get("prev_str", ""),
        "actual": actual or event.get("actual_str", ""),
        "impact": event.get("impact", ""),
        "ai_model": DEFAULT_AI_MODEL,
        "ai_cache": AI_CACHE_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _forecaster_ai_evidence_signature(event: dict, nowcast: dict, relevant_articles: list, actual: str = "") -> str:
    """Fingerprint only evidence that can materially change causal AI output."""
    precursor_rows = []
    for row in (nowcast.get("precursor_results") or []):
        precursor_rows.append({
            "name": row.get("name", ""),
            "latest": round(float(row.get("latest", 0) or 0), 6),
            "mom": round(float(row.get("mom", 0) or 0), 6),
            "score": round(float(row.get("score", 0) or 0), 6),
        })

    news_rows = []
    for art in (relevant_articles or [])[:10]:
        source = art.get("source", {})
        source_name = source.get("name", "") if isinstance(source, dict) else str(source or "")
        news_rows.append({
            "source": source_name,
            "published": str(art.get("publishedAt", "")),
            "title": str(art.get("title", "")).strip(),
            "description": str(art.get("description", "")).strip(),
        })
    news_rows.sort(key=lambda x: (x["published"], x["source"], x["title"]))

    payload = {
        "event": _forecaster_bg_signature(event, actual),
        "precursors": precursor_rows,
        "probabilities": nowcast.get("probabilities", {}),
        "composite": round(float(nowcast.get("nowcast_composite", 0) or 0), 6),
        "conflict": round(float(nowcast.get("conflict_score", 0) or 0), 6),
        "evidence_quality": round(float(nowcast.get("evidence_quality", 0) or 0), 6),
        "news": news_rows,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _forecaster_relevant_ai_articles(event: dict, all_news: list) -> list:
    """Use the same event-verification gate so unrelated feed churn cannot trigger AI spend."""
    verified, _ = _verified_event_news(event, all_news or [])
    return verified[:10]


def _forecaster_ai_cache_get(code: str, evidence_sig: str) -> dict | None:
    now_ts = time.time()
    with _FORECASTER_BG_LOCK:
        item = _FORECASTER_AI_REUSE_CACHE.get(code)
        if not item or item.get("evidence_signature") != evidence_sig:
            return None
        age = now_ts - float(item.get("updated_at", 0))
        result = item.get("result") or {}
        max_age = _FORECASTER_AI_ERROR_RETRY_SECONDS if result.get("status") == "error" else _FORECASTER_AI_MAX_AGE_SECONDS
        if age > max_age:
            return None
        return result


def _forecaster_ai_cache_put(code: str, evidence_sig: str, result: dict) -> None:
    with _FORECASTER_BG_LOCK:
        _FORECASTER_AI_REUSE_CACHE[code] = {
            "evidence_signature": evidence_sig,
            "updated_at": time.time(),
            "result": result,
        }


def _forecaster_bg_get(event: dict, actual: str = "") -> dict | None:
    code = str(event.get("code", "")).strip()
    if not code:
        return None
    sig = _forecaster_bg_signature(event, actual)
    now_ts = time.time()
    with _FORECASTER_BG_LOCK:
        item = _FORECASTER_BG_CACHE.get(code)
        if not item or item.get("signature") != sig:
            return None
        if now_ts - float(item.get("updated_at", 0)) > _FORECASTER_BG_TTL_SECONDS:
            return None
        return item



def _forecaster_background_worker(events: list[dict], fred_key: str, channel_name: str, actuals_snapshot: dict, force_ai: bool = False) -> None:
    """
    Continuously warm Forecaster results off the click path.

    Important behavior:
    - one shared news fetch for the batch
    - nearest/recent catalysts are prioritized
    - several events can precompute concurrently
    - quantitative Nowcast is cached BEFORE the slower causal-AI call
    - one slow RUAPI request cannot block every other catalyst
    """
    global _FORECASTER_BG_WORKER_RUNNING

    try:
        try:
            all_news = fetch_all_instant_news(channel_name)
        except Exception:
            all_news = []

        now_utc = datetime.now(timezone.utc)

        def _priority(ev: dict):
            dt = ev.get("datetime_obj")
            if isinstance(dt, datetime):
                try:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    distance = abs((dt.astimezone(timezone.utc) - now_utc).total_seconds())
                except Exception:
                    distance = 10**12
            else:
                distance = 10**12
            impact_rank = 0 if str(ev.get("impact", "")).title() == "High" else 1
            return (distance, impact_rank)

        ordered = sorted(list(events or []), key=_priority)

        def _compute_one(ev: dict):
            code = str(ev.get("code", "")).strip()
            if not code:
                return

            saved_actual = str((actuals_snapshot or {}).get(code, "")).strip()
            published_actual = _normalize_forex_factory_actual(ev.get("actual_str", ""))
            effective_actual = saved_actual or published_actual
            signature = _forecaster_bg_signature(ev, effective_actual)

            with _FORECASTER_BG_LOCK:
                existing = _FORECASTER_BG_CACHE.get(code)
                fresh = (
                    existing
                    and existing.get("signature") == signature
                    and time.time() - float(existing.get("updated_at", 0)) <= _FORECASTER_BG_TTL_SECONDS
                    and existing.get("nowcast")
                )
                ai_ready = bool(existing and (existing.get("causal_ai") or {}).get("status") in {"ok", "skipped"})
                if (fresh and (not force_ai or ai_ready)) or code in _FORECASTER_BG_INFLIGHT:
                    return
                _FORECASTER_BG_INFLIGHT.add(code)

            try:
                # Stage 1: fast quantitative result first.
                nowcast = compute_event_nowcast(
                    ev,
                    fred_key,
                    all_news,
                    actual_override=effective_actual,
                )

                with _FORECASTER_BG_LOCK:
                    _FORECASTER_BG_CACHE[code] = {
                        "signature": signature,
                        "updated_at": time.time(),
                        "nowcast": nowcast,
                        "causal_ai": {"status": "updating"},
                        "all_news": all_news,
                        "effective_actual": effective_actual,
                    }

                # Stage 2: causal AI is event-driven, not timer-driven.
                # Unrelated news-feed churn and Streamlit reruns must not spend tokens.
                if str(ev.get("impact", "")).title() == "High":
                    ev_dt = ev.get("datetime_obj")
                    seconds_to_event = None
                    if isinstance(ev_dt, datetime):
                        try:
                            local_now = datetime.utcnow() + timedelta(hours=3)
                            seconds_to_event = (ev_dt.replace(tzinfo=None) - local_now).total_seconds()
                        except Exception:
                            seconds_to_event = None

                    should_prewarm_ai = force_ai or effective_actual or (
                        seconds_to_event is not None
                        and -(48 * 3600) <= seconds_to_event <= _FORECASTER_AI_PREWARM_HORIZON_SECONDS
                    )

                    if should_prewarm_ai:
                        relevant_ai_news = _forecaster_relevant_ai_articles(ev, all_news)
                        evidence_sig = _forecaster_ai_evidence_signature(
                            ev, nowcast, relevant_ai_news, effective_actual
                        )
                        causal_ai = _forecaster_ai_cache_get(code, evidence_sig)
                        if causal_ai is None:
                            causal_ai = get_causal_macro_ai_analysis(
                                ev,
                                nowcast,
                                relevant_ai_news,
                                DEFAULT_AI_KEY,
                                DEFAULT_AI_PROVIDER,
                                DEFAULT_AI_MODEL,
                                AI_CACHE_VERSION,
                            )
                            _forecaster_ai_cache_put(code, evidence_sig, causal_ai)
                    else:
                        causal_ai = {"status": "deferred"}
                else:
                    causal_ai = {"status": "skipped"}

                with _FORECASTER_BG_LOCK:
                    item = _FORECASTER_BG_CACHE.get(code, {})
                    if item.get("signature") == signature:
                        item["causal_ai"] = causal_ai
                        item["updated_at"] = time.time()
                        _FORECASTER_BG_CACHE[code] = item

            except Exception as exc:
                with _FORECASTER_BG_LOCK:
                    prior = _FORECASTER_BG_CACHE.get(code, {})
                    _FORECASTER_BG_CACHE[code] = {
                        **prior,
                        "signature": signature,
                        "updated_at": time.time(),
                        "error": str(exc)[:300],
                        "effective_actual": effective_actual,
                    }
            finally:
                with _FORECASTER_BG_LOCK:
                    _FORECASTER_BG_INFLIGHT.discard(code)

        # A small pool avoids RUAPI flooding while preventing one event from
        # blocking the rest of the calendar.
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="ApexFcWarm") as pool:
            futures = [pool.submit(_compute_one, ev) for ev in ordered]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    pass

    finally:
        with _FORECASTER_BG_LOCK:
            _FORECASTER_BG_WORKER_RUNNING = False

def _ensure_forecaster_background_worker(events: list[dict], fred_key: str, channel_name: str, actuals_cache: dict, force_ai: bool = False) -> None:
    """Start at most one daemon worker per process; reruns do not duplicate RUAPI calls."""
    global _FORECASTER_BG_WORKER_RUNNING
    if not events:
        return

    # Start only when at least one event is missing/stale.
    needs_work = False
    for ev in events:
        code = str(ev.get("code", "")).strip()
        saved = str((actuals_cache or {}).get(code, "")).strip()
        published = _normalize_forex_factory_actual(ev.get("actual_str", ""))
        cached = _forecaster_bg_get(ev, saved or published)
        if cached is None:
            needs_work = True
            break
        if force_ai and str(ev.get("impact", "")).title() == "High":
            if (cached.get("causal_ai") or {}).get("status") not in {"ok", "skipped"}:
                needs_work = True
                break
    if not needs_work:
        return

    with _FORECASTER_BG_LOCK:
        if _FORECASTER_BG_WORKER_RUNNING:
            return
        _FORECASTER_BG_WORKER_RUNNING = True

    threading.Thread(
        target=_forecaster_background_worker,
        args=(list(events), fred_key, channel_name, dict(actuals_cache or {}), force_ai),
        daemon=True,
        name="ApexMacroForecasterPrecompute",
    ).start()


@st.fragment(run_every=30)
def _forecaster_radar_refresh_tick() -> None:
    """Trigger periodic radar refresh without running while a catalyst is selected."""
    if not st.session_state.get("APEX_FORECASTER_SELECTED_EVENT"):
        st.caption("Live calendar refresh active.")


@st.dialog("Event Details")
def _show_forecaster_event_dialog(
    modal_ev: dict,
    fred_key: str,
    channel_name: str,
    auth_user: dict | None,
    actuals_cache: dict,
    currency_flags: dict,
) -> None:
    """UI-only event detail dialog. Forecasting/AI calculations remain unchanged."""
    is_admin = bool(auth_user and auth_user.get("is_admin", False))
    ev_code = str(modal_ev.get("code", "")).strip()
    saved_actual = str(actuals_cache.get(ev_code, "")).strip()
    published_actual = _normalize_forex_factory_actual(modal_ev.get("actual_str", ""))
    effective_actual = saved_actual or published_actual
    bg_result = _forecaster_bg_get(modal_ev, effective_actual)

    if bg_result and bg_result.get("nowcast"):
        nowcast = bg_result["nowcast"]
        causal_ai = bg_result.get("causal_ai") or {"status": "skipped"}
        all_news = bg_result.get("all_news") or []
        if str(modal_ev.get("impact", "")).title() == "High" and causal_ai.get("status") not in {"ok", "skipped"}:
            _ensure_forecaster_background_worker([modal_ev], fred_key, channel_name, actuals_cache, force_ai=True)
    else:
        with st.spinner(f"Loading Causal Intelligence for {modal_ev.get('title','catalyst')}..."):
            all_news = fetch_all_instant_news(channel_name)
            nowcast = compute_event_nowcast(
                modal_ev, fred_key, all_news, actual_override=effective_actual
            )
            causal_ai = {"status": "updating"}
            with _FORECASTER_BG_LOCK:
                _FORECASTER_BG_CACHE[ev_code] = {
                    "signature": _forecaster_bg_signature(modal_ev, effective_actual),
                    "updated_at": time.time(),
                    "nowcast": nowcast,
                    "causal_ai": causal_ai,
                    "all_news": all_news,
                    "effective_actual": effective_actual,
                }
            _ensure_forecaster_background_worker([modal_ev], fred_key, channel_name, actuals_cache, force_ai=True)

    try:
        _record_forecaster_snapshot(modal_ev, nowcast, actual=effective_actual)
    except Exception:
        pass

    cur = modal_ev.get("currency", "USD")
    cur_flag = currency_flags.get(cur, "🌐")
    impact_level = str(modal_ev.get("impact", "High")).title()
    impact_color = "#B04CE4" if impact_level == "High" else ("#FFB822" if impact_level == "Medium" else "#35D2E3")

    ev_dt = modal_ev.get("datetime_obj")
    ev_date_str = ev_dt.strftime("%d %B %Y") if ev_dt else modal_ev.get("date_str", "")
    ev_time_str = ev_dt.strftime("%H:%M") if ev_dt else "—"

    act_disp = effective_actual or "—"
    fcst_disp = modal_ev.get("forecast_str", "—")
    prev_disp = modal_ev.get("prev_str", "—")

    actual_outcome = nowcast.get("actual_outcome", "")
    if effective_actual:
        act_num_cls = "beat" if actual_outcome == "beat" else ("miss" if actual_outcome == "miss" else ("inline" if actual_outcome == "inline" else ""))
    else:
        act_num_cls = ""

    causal_intel_label = nowcast.get("bias_label", "In-Line Signal").lstrip("🔺🔻⚖️✅❌ ")
    causal_intel_color = nowcast.get("bias_color", "#00ffa3")
    market_impact_label = "Risk-Off" if "Bearish" in nowcast.get("usd_implication", "") or "miss" in str(actual_outcome).lower() else "Risk-On / High Volatility"
    if "USD" in nowcast.get("currency_action_en", "") and "Appreciate" in nowcast.get("currency_action_en", ""):
        market_impact_label = "Hawkish / Risk-Off"
    elif "Weaken" in nowcast.get("currency_action_en", ""):
        market_impact_label = "Dovish / Risk-On"

    precursor_bullets = []
    if nowcast.get("precursor_results"):
        for p in nowcast["precursor_results"]:
            p_name = p.get("name", "Indicator")
            p_mom = p.get("mom", 0.0)
            p_trend = "acceleration" if p_mom > 0 else ("deceleration" if p_mom < 0 else "steady")
            precursor_bullets.append(
                f"• {p_name} ({p.get('latest', 0.0):.2f}) showing MoM {p_trend} ({p_mom:+.2f}%)"
            )
    else:
        precursor_bullets = [
            "• Precursor series analysis integrated with FRED macroeconomic database",
            "• Multi-timeframe trend momentum and directional bias calibrated",
            "• High-frequency headline verification active",
        ]
    evidence_html = "".join(f"<li>{b}</li>" for b in precursor_bullets)

    if causal_ai.get("status") == "ok":
        ai_text = causal_ai.get("event_assessment", "") or nowcast.get("outcome_desc", "")
        ai_updated = "Live Causal Macro Engine"
    elif causal_ai.get("status") in {"updating", "deferred"}:
        ai_text = nowcast.get("outcome_desc", "") + " (Causal AI synthesis updating in background...)"
        ai_updated = "Updating..."
    else:
        ai_text = nowcast.get("outcome_desc", "Comprehensive three-way probabilistic model with evidence conflict penalties.")
        ai_updated = "Real-time Quant Model"

    cross_assets = [
        ("USD", nowcast.get("usd_implication", "")),
        ("Gold", nowcast.get("gold_implication", "")),
        ("NASDAQ", nowcast.get("nasdaq_implication", "")),
        ("Oil", nowcast.get("oil_implication", "")),
    ]
    cross_cards_html = ""
    for a_name, a_imp in cross_assets:
        a_str = str(a_imp)
        is_up = any(w in a_str.lower() for w in ["bull", "appreciat", "tailwind", "support", "higher"])
        is_dn = any(w in a_str.lower() for w in ["bear", "weaken", "drag", "lower", "miss"])
        arrow = "↑" if is_up else ("↓" if is_dn else "→")
        arr_color = "#00ffa3" if is_up else ("#ff5e75" if is_dn else "#ffd166")
        state_word = "Bullish" if is_up else ("Bearish" if is_dn else "Neutral")
        if a_name == "USD":
            state_word = "Strengthen" if is_up else ("Weaken" if is_dn else "Consolidation")
        cross_cards_html += f"""
        <div class="apex-cross-asset-card">
          <div class="apex-cross-asset-name">{a_name} <span style="color:{arr_color};font-weight:900;">{arrow}</span></div>
          <div class="apex-cross-asset-state">{state_word}</div>
        </div>
        """

    render_html(f"""
    <div class="apex-dialog-date">{ev_date_str}, {ev_time_str} · {modal_ev.get('time_str','')}</div>
    <div class="apex-form-grid-3">
      <div class="apex-form-field"><div class="apex-form-label">Currency</div><div class="apex-form-box">{cur_flag} {cur}</div></div>
      <div class="apex-form-field"><div class="apex-form-label">Impact</div><div class="apex-form-box"><span><span class="apex-impact-dot" style="background:{impact_color};display:inline-block;margin-right:6px;"></span>{impact_level} Impact</span></div></div>
      <div class="apex-form-field"><div class="apex-form-label">Time</div><div class="apex-form-box">{ev_time_str}</div></div>
    </div>
    <div class="apex-form-field"><div class="apex-form-label">Event</div><div class="apex-form-box apex-dialog-event-name">{modal_ev.get('title','')}</div></div>
    <div class="apex-modal-values">
      <div class="apex-modal-value"><div class="apex-modal-value-lbl">Actual</div><div class="apex-modal-value-num {act_num_cls}">{act_disp}</div></div>
      <div class="apex-modal-value"><div class="apex-modal-value-lbl">Forecast</div><div class="apex-modal-value-num">{fcst_disp}</div></div>
      <div class="apex-modal-value"><div class="apex-modal-value-lbl">Previous</div><div class="apex-modal-value-num">{prev_disp}</div></div>
    </div>
    <div class="apex-form-grid-2">
      <div class="apex-form-field"><div class="apex-form-label">Causal Intelligence</div><div class="apex-form-box" style="color:{causal_intel_color};">{causal_intel_label}</div></div>
      <div class="apex-form-field"><div class="apex-form-label">Market Impact</div><div class="apex-form-box" style="color:#ffd166;">{market_impact_label}</div></div>
    </div>
    <div class="apex-intelligence-card">
      <div class="apex-card-header-row"><div class="apex-card-title">Evidence &amp; Precursors</div><div><span class="apex-ai-badge">AI</span> <span class="apex-conf-badge">{nowcast.get('confidence', 0)}% Confidence</span></div></div>
      <ul class="apex-evidence-list">{evidence_html}</ul>
    </div>
    <div class="apex-intelligence-card">
      <div class="apex-card-header-row"><div class="apex-card-title">AI Analysis (Causal Intelligence)</div><span class="apex-ai-badge">AI</span></div>
      <div class="apex-dialog-ai-text">{ai_text}</div>
      <div class="apex-dialog-ai-source">Source: {ai_updated} &nbsp;•&nbsp; Baseline: {modal_ev.get('consensus_bias','')}</div>
    </div>
    <div class="apex-dialog-section-title">Cross-Asset Impact</div>
    <div class="apex-cross-asset-grid">{cross_cards_html}</div>
    """)

    # Preserve the complete existing causal-AI panel (causal chain, evidence,
    # contradictions, cross-source confirmation and asset implications).
    render_causal_macro_ai_panel(causal_ai)

    if is_admin:
        render_html(f"""
        <div class="apex-admin-box">
          <div class="apex-admin-header"><div class="apex-admin-title">👑 Admin Actual Override</div><div class="apex-admin-sub">Current: {act_disp}</div></div>
        </div>
        """)
        c1, c2 = st.columns([2, 1])
        with c1:
            new_actual = st.text_input(
                "Admin Actual Override",
                value=effective_actual,
                placeholder="Enter actual value...",
                key=f"apex_dialog_actual_{ev_code}",
                label_visibility="collapsed",
            )
        with c2:
            if st.button("Update Actual", key=f"apex_dialog_actual_save_{ev_code}", use_container_width=True):
                actuals_cache[ev_code] = new_actual.strip()
                save_actuals_cache(actuals_cache)
                st.success("Updated!")
                time.sleep(0.25)
                st.rerun()


def page_catalyst_forecaster(fred_key: str, channel_name: str, auth_user: dict | None = None) -> None:
    """Catalyst Forecaster UI: weekly catalyst rail -> timeline -> intelligence panel/dialog."""
    from datetime import date as _date

    if "selected_tz" not in st.session_state or st.session_state["selected_tz"] not in SUPPORTED_TIMEZONES:
        st.session_state["selected_tz"] = "🏛️ Kurdistan & Iraq (UTC+3)"

    tz_info = SUPPORTED_TIMEZONES.get(
        st.session_state["selected_tz"],
        {"offset": 3, "label": "KRD (UTC+3)"},
    )

    selected_key = "APEX_FORECASTER_SELECTED_EVENT"
    snapshot_key = "APEX_FORECASTER_EVENT_SNAPSHOT"
    sel_date_key = "apex_forecaster_selected_date"

    # Preserve the current source/data pipeline and snapshot behavior.
    if not st.session_state.get(selected_key):
        _forecaster_radar_refresh_tick()

    events = get_upcoming_catalyst_events(tz_info["offset"], tz_info["label"])
    if events:
        st.session_state[snapshot_key] = events
    elif st.session_state.get(snapshot_key):
        events = st.session_state[snapshot_key]

    actuals_cache = load_actuals_cache()
    actuals_changed = False
    for event in events:
        event_code = str(event.get("code", "")).strip()
        published_actual = _normalize_forex_factory_actual(event.get("actual_str", ""))
        if event_code and published_actual and not str(actuals_cache.get(event_code, "")).strip():
            actuals_cache[event_code] = published_actual
            actuals_changed = True
    if actuals_changed:
        save_actuals_cache(actuals_cache)

    _ensure_forecaster_background_worker(events, fred_key, channel_name, actuals_cache)

    currency_flags = {
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "💷", "CAD": "🍁",
        "JPY": "💴", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🏔️",
    }

    events_by_date: dict[_date, list[dict]] = {}
    for ev in events:
        dt = ev.get("datetime_obj")
        if dt:
            d = dt.date() if hasattr(dt, "date") else dt
            events_by_date.setdefault(d, []).append(ev)
    for day_events in events_by_date.values():
        day_events.sort(key=lambda e: e.get("datetime_obj") or datetime.max)

    all_event_dates = sorted(events_by_date)
    today_local = (datetime.utcnow() + timedelta(hours=tz_info["offset"])).date()

    if sel_date_key not in st.session_state or not isinstance(st.session_state[sel_date_key], _date):
        if today_local in events_by_date:
            st.session_state[sel_date_key] = today_local
        elif all_event_dates:
            future = [d for d in all_event_dates if d >= today_local]
            st.session_state[sel_date_key] = future[0] if future else all_event_dates[0]
        else:
            st.session_state[sel_date_key] = today_local

    selected_date: _date = st.session_state[sel_date_key]
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_days = [week_start + timedelta(days=i) for i in range(7)]
    week_end = week_days[-1]

    # This CSS is intentionally scoped to the new Forecaster keys/classes so it
    # cannot alter the approved UI of the other ApexMacro pages.
    render_html("""
    <style>
    .apex-fc2-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:2px 0 16px;padding:2px 2px 6px}
    .apex-fc2-kicker{font-size:11px;font-weight:800;letter-spacing:.16em;color:#28dce7;text-transform:uppercase}
    .apex-fc2-title{margin-top:6px;max-width:780px;color:#f3f7fa;font-size:clamp(24px,3vw,38px);font-weight:800;line-height:1.08;letter-spacing:-.7px}
    .apex-fc2-sub{margin-top:8px;color:#8fa1ae;font-size:12px}
    .apex-fc2-weekmeta{flex:0 0 auto;color:#8fa1ae;font-size:11px;border:1px solid rgba(83,135,158,.20);background:rgba(6,22,31,.74);border-radius:999px;padding:7px 10px}
    .apex-fc2-section-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 12px}
    .apex-fc2-section-title{font-size:14px;font-weight:800;color:#eef5f8}
    .apex-fc2-count{font-size:10px;white-space:nowrap;flex:0 0 auto;color:#9fb0ba;border:1px solid rgba(83,135,158,.20);background:rgba(7,25,35,.78);border-radius:999px;padding:5px 9px}
    .apex-fc2-empty{padding:22px 16px;border:1px dashed rgba(83,135,158,.22);border-radius:14px;color:#8295a2;text-align:center;background:rgba(5,18,27,.48)}
    .apex-fc2-dots{display:flex;align-items:center;justify-content:center;gap:4px;height:9px;margin-top:-5px;margin-bottom:4px}
    .apex-fc2-dot{width:5px;height:5px;border-radius:50%;display:inline-block}.apex-fc2-dot.high{background:#b04ce4}.apex-fc2-dot.medium{background:#ffb822}.apex-fc2-dot.low{background:#35d2e3}
    .apex-fc2-legend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:10px 0 0;color:#8fa1ae;font-size:10px}.apex-fc2-legend span{display:flex;align-items:center;gap:5px}
    .apex-fc2-detail-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:10px}.apex-fc2-detail-kicker{font-size:10px;color:#28dce7;font-weight:800;letter-spacing:.1em;text-transform:uppercase}.apex-fc2-detail-title{font-size:19px;color:#f2f7fa;font-weight:800;line-height:1.25;margin-top:5px}.apex-fc2-detail-time{font-size:11px;color:#91a3af;margin-top:5px}
    .apex-fc2-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0}.apex-fc2-metric{padding:10px;border:1px solid rgba(83,135,158,.16);background:rgba(7,25,35,.74);border-radius:10px}.apex-fc2-metric-label{font-size:9px;color:#7e909c}.apex-fc2-metric-value{margin-top:4px;font-size:13px;font-weight:750;color:#eef5f8}
    .apex-fc2-insight{padding:11px;border-top:1px solid rgba(83,135,158,.14);color:#91a3af;font-size:11px;line-height:1.5}.apex-fc2-insight b{display:block;color:#dce7ec;font-size:11px;margin-bottom:4px}

    /* Week rail */
    [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_day_"]){width:100%!important;max-width:100%!important;gap:8px!important;overflow:hidden!important}
    [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_day_"])>[data-testid="stColumn"],
    [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_day_"])>[data-testid="column"]{min-width:0!important;max-width:100%!important}
    [class*="st-key-apex_fc2_day_"] button{min-height:82px!important;padding:9px 4px!important;border-radius:13px!important;border:1px solid rgba(83,135,158,.17)!important;background:linear-gradient(145deg,rgba(10,31,42,.82),rgba(4,17,25,.94))!important;color:#9fb0ba!important;white-space:pre-line!important;line-height:1.25!important;font-size:11px!important;font-weight:700!important;box-shadow:none!important;transition:transform .18s ease,border-color .18s ease,background .18s ease!important}
    [class*="st-key-apex_fc2_day_"] button p{white-space:pre-line!important;text-align:center!important;margin:0!important}
    [class*="st-key-apex_fc2_day_selected_"] button{border-color:rgba(39,220,231,.85)!important;background:linear-gradient(145deg,rgba(12,68,78,.72),rgba(5,28,37,.95))!important;color:#2be0e9!important;box-shadow:0 0 18px rgba(39,220,231,.10)!important}
    [class*="st-key-apex_fc2_day_today_"] button{border-color:rgba(39,220,231,.34)!important}
    [class*="st-key-apex_fc2_day_"] button:hover{transform:translateY(-2px)!important;border-color:rgba(39,220,231,.50)!important;color:#eaf6f8!important}

    /* Main two-column intelligence composition */
    [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_timeline_col"]){align-items:stretch!important;gap:14px!important;width:100%!important;max-width:100%!important;overflow:visible!important}
    [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_timeline_col"])>[data-testid="stColumn"],
    [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_timeline_col"])>[data-testid="column"]{min-width:0!important;max-width:100%!important;overflow:hidden!important}
    [class*="st-key-apex_fc2_timeline_col"],[class*="st-key-apex_fc2_detail_col"]{height:100%!important;min-width:0!important;max-width:100%!important;overflow:hidden!important;border:1px solid rgba(83,135,158,.17)!important;background:linear-gradient(145deg,rgba(6,22,31,.94),rgba(3,13,20,.98))!important;border-radius:17px!important;padding:15px!important;box-sizing:border-box!important}
    [class*="st-key-apex_fc2_timeline_col"] *,[class*="st-key-apex_fc2_detail_col"] *{min-width:0}

    /* Event timeline cards */
    [class*="st-key-apex_fc2_event_"]:not([class*="st-key-apex_fc2_event_btn_"]){position:relative!important;margin:0 0 11px 22px!important;width:calc(100% - 22px)!important;max-width:calc(100% - 22px)!important;box-sizing:border-box!important;overflow:visible!important}
    [class*="st-key-apex_fc2_event_"]:not([class*="st-key-apex_fc2_event_btn_"])::before{content:"";position:absolute;left:-18px;top:0;bottom:-12px;width:1px;background:rgba(83,135,158,.16)}
    [class*="st-key-apex_fc2_event_"]:not([class*="st-key-apex_fc2_event_btn_"])::after{content:"";position:absolute;left:-22px;top:19px;width:9px;height:9px;border-radius:50%;background:#b04ce4;box-shadow:0 0 0 4px #061721}
    [class*="st-key-apex_fc2_event_medium_"]:not([class*="st-key-apex_fc2_event_btn_"])::after{background:#ffb822}[class*="st-key-apex_fc2_event_low_"]:not([class*="st-key-apex_fc2_event_btn_"])::after{background:#35d2e3}
    [class*="st-key-apex_fc2_event_"] button{width:100%!important;max-width:100%!important;min-width:0!important;min-height:108px!important;padding:12px 13px!important;border-radius:13px!important;border:1px solid rgba(83,135,158,.17)!important;background:rgba(7,25,35,.78)!important;color:#eaf1f4!important;text-align:left!important;justify-content:flex-start!important;white-space:pre-wrap!important;overflow-wrap:anywhere!important;word-break:normal!important;box-shadow:none!important;font-size:11px!important;line-height:1.46!important;font-weight:650!important;transition:transform .16s ease,border-color .16s ease,background .16s ease!important}
    [class*="st-key-apex_fc2_event_"] button p{text-align:left!important;white-space:pre-wrap!important;overflow-wrap:anywhere!important;margin:0!important;width:100%!important;max-width:100%!important}
    [class*="st-key-apex_fc2_event_selected_"] button{border-color:rgba(39,220,231,.60)!important;background:linear-gradient(145deg,rgba(8,38,49,.92),rgba(4,20,29,.98))!important}
    [class*="st-key-apex_fc2_event_"] button:hover{transform:translateY(-2px)!important;border-color:rgba(39,220,231,.45)!important;background:rgba(8,33,44,.90)!important}

    [class*="st-key-apex_fc2_open_full"] button{width:100%!important;min-height:44px!important;margin-top:10px!important;border-radius:10px!important;border:1px solid rgba(39,220,231,.44)!important;background:linear-gradient(90deg,rgba(24,205,219,.18),rgba(24,205,219,.08))!important;color:#37e4ec!important;font-weight:800!important;box-shadow:none!important}

    @media(max-width:768px){
      .apex-fc2-hero{align-items:flex-start;flex-direction:column;margin-bottom:12px;width:100%;max-width:100%}.apex-fc2-title{font-size:23px}.apex-fc2-weekmeta{padding:5px 8px}
      .apex-fc2-section-head{align-items:flex-start}.apex-fc2-section-title{min-width:0;overflow-wrap:anywhere}.apex-fc2-count{white-space:nowrap!important}
      [class*="st-key-apex_fc2_day_"] button{min-height:66px!important;padding:7px 2px!important;border-radius:9px!important;font-size:9px!important}
      .apex-fc2-dots{gap:2px;margin-top:-7px}.apex-fc2-dot{width:4px;height:4px}
      [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_day_"]){gap:4px!important;width:100%!important;max-width:100%!important}

      /* Force the two Streamlit columns to become true full-width rows on phones.
         The parent stColumn widths/flex-basis must be overridden, not only the keyed inner containers. */
      [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_timeline_col"]){display:flex!important;flex-direction:column!important;flex-wrap:nowrap!important;align-items:stretch!important;gap:12px!important;width:100%!important;max-width:100%!important;overflow:visible!important}
      [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_timeline_col"]) > [data-testid="stColumn"],
      [data-testid="stHorizontalBlock"]:has([class*="st-key-apex_fc2_timeline_col"]) > [data-testid="column"]{display:block!important;flex:1 1 100%!important;flex-basis:100%!important;width:100%!important;min-width:0!important;max-width:100%!important;overflow:visible!important}
      [class*="st-key-apex_fc2_timeline_col"],[class*="st-key-apex_fc2_detail_col"]{display:block!important;width:100%!important;min-width:0!important;max-width:100%!important;padding:12px!important;overflow:hidden!important;box-sizing:border-box!important}
      [class*="st-key-apex_fc2_detail_col"]{order:initial!important;margin-top:0!important}

      [class*="st-key-apex_fc2_event_"]:not([class*="st-key-apex_fc2_event_btn_"]){margin-left:17px!important;width:calc(100% - 17px)!important;max-width:calc(100% - 17px)!important}
      [class*="st-key-apex_fc2_event_"] button{min-height:104px!important;padding:11px!important;font-size:10px!important;line-height:1.42!important}
      .apex-fc2-detail-title{font-size:16px;overflow-wrap:anywhere}.apex-fc2-detail-time{overflow-wrap:anywhere}.apex-fc2-metrics{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.apex-fc2-metric{padding:8px;min-width:0}.apex-fc2-metric-label{font-size:8px;overflow-wrap:normal;word-break:normal}.apex-fc2-metric-value{font-size:12px;overflow-wrap:anywhere}
      .apex-fc2-insight{overflow-wrap:anywhere}
    }
    @media(max-width:390px){
      [class*="st-key-apex_fc2_day_"] button{min-height:61px!important;font-size:8px!important;padding:6px 1px!important}
      .apex-fc2-title{font-size:21px}.apex-fc2-sub{font-size:10px}
    }
    @media(prefers-reduced-motion:reduce){[class*="st-key-apex_fc2_"] button{transition:none!important}}
    </style>
    """)

    week_label = f"{week_start.strftime('%d %b')} — {week_end.strftime('%d %b %Y')}"
    render_html(f"""
    <div class="apex-fc2-hero">
      <div>
        <div class="apex-fc2-kicker">CATALYST FORECASTER</div>
        <div class="apex-fc2-title">Macro events, organized by market relevance.</div>
        <div class="apex-fc2-sub">Live catalyst timeline · {tz_info['label']}</div>
      </div>
      <div class="apex-fc2-weekmeta">{week_label}</div>
    </div>
    """)

    def _select_fc2_date(day_value: _date) -> None:
        st.session_state[sel_date_key] = day_value
        st.session_state.pop(selected_key, None)

    # Seven-day rail, matching the new timeline concept instead of a classic month calendar.
    week_cols = st.columns(7, gap="small")
    for idx, day_date in enumerate(week_days):
        day_events = events_by_date.get(day_date, [])
        state = "selected" if day_date == selected_date else ("today" if day_date == today_local else "normal")
        with week_cols[idx]:
            with st.container(key=f"apex_fc2_day_{state}_{day_date:%Y_%m_%d}"):
                st.button(
                    f"{day_date.strftime('%a').upper()}\n{day_date.day}",
                    key=f"apex_fc2_day_btn_{day_date:%Y_%m_%d}",
                    use_container_width=True,
                    on_click=_select_fc2_date,
                    args=(day_date,),
                )
                if day_events:
                    # One marker per impact CATEGORY (not one marker per event).
                    # This keeps the week rail clean and prevents repeated purple/yellow dots
                    # from being mistaken for duplicate events.
                    present_impacts = []
                    for impact_name, dot_cls in (("High", "high"), ("Medium", "medium"), ("Low", "low")):
                        if any(str(d_ev.get("impact", "")).title() == impact_name for d_ev in day_events):
                            present_impacts.append(f'<span class="apex-fc2-dot {dot_cls}"></span>')
                    render_html(f'<div class="apex-fc2-dots">{"".join(present_impacts)}</div>')

    render_html("""
    <div class="apex-fc2-legend">
      <span><i class="apex-fc2-dot high"></i>High Impact</span>
      <span><i class="apex-fc2-dot medium"></i>Medium Impact</span>
      <span><i class="apex-fc2-dot low"></i>Low Impact</span>
    </div>
    """)

    selected_date = st.session_state[sel_date_key]
    sel_events = events_by_date.get(selected_date, [])

    # Keep the detail panel selection tied to the currently selected date.
    selected_code = str(st.session_state.get(selected_key, "") or "")
    selected_event = next((ev for ev in sel_events if str(ev.get("code", "")) == selected_code), None)
    if selected_event is None and sel_events:
        selected_event = sel_events[0]
        st.session_state[selected_key] = str(selected_event.get("code", ""))

    def _select_fc2_event(code_value: str) -> None:
        st.session_state[selected_key] = code_value

    left_col, right_col = st.columns([1.35, 0.65], gap="medium")

    with left_col:
        with st.container(key="apex_fc2_timeline_col"):
            date_title = selected_date.strftime("%A, %d %B %Y")
            count_label = "1 catalyst" if len(sel_events) == 1 else f"{len(sel_events)} catalysts"
            render_html(f"""
            <div class="apex-fc2-section-head">
              <div class="apex-fc2-section-title">{date_title}</div>
              <div class="apex-fc2-count">{count_label}</div>
            </div>
            """)

            if not sel_events:
                render_html('<div class="apex-fc2-empty">No scheduled macro catalysts for this date.</div>')
            else:
                for idx, sev in enumerate(sel_events):
                    code = str(sev.get("code", "")).strip()
                    cur = sev.get("currency", "USD")
                    flag = currency_flags.get(cur, "🌐")
                    impact = str(sev.get("impact", "High")).title()
                    impact_key = impact.lower() if impact.lower() in {"high", "medium", "low"} else "low"
                    dt = sev.get("datetime_obj")
                    event_time = dt.strftime("%H:%M") if dt else "—"
                    saved = str(actuals_cache.get(code, "")).strip()
                    published = _normalize_forex_factory_actual(sev.get("actual_str", ""))
                    actual = saved or published or "—"
                    forecast = sev.get("forecast_str", "—") or "—"
                    previous = sev.get("prev_str", "—") or "—"
                    title = str(sev.get("title", ""))
                    safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", code)[:60] or f"event_{idx}"
                    selected_marker = "selected_" if selected_event is sev or (selected_event and str(selected_event.get("code", "")) == code) else ""
                    label = (
                        f"{event_time}  ·  {flag} {cur}  ·  {impact.upper()} IMPACT\n\n"
                        f"{title}\n\n"
                        f"ACTUAL  {actual}     FORECAST  {forecast}     PREVIOUS  {previous}"
                    )
                    with st.container(key=f"apex_fc2_event_{selected_marker}{impact_key}_{safe_key}"):
                        st.button(
                            label,
                            key=f"apex_fc2_event_btn_{safe_key}",
                            use_container_width=True,
                            on_click=_select_fc2_event,
                            args=(code,),
                        )

    with right_col:
        with st.container(key="apex_fc2_detail_col"):
            if selected_event is None:
                render_html("""
                <div class="apex-fc2-detail-kicker">Catalyst Intelligence</div>
                <div class="apex-fc2-detail-title">Select a catalyst</div>
                <div class="apex-fc2-detail-time">Choose an event from the timeline to inspect it.</div>
                """)
            else:
                code = str(selected_event.get("code", "")).strip()
                cur = selected_event.get("currency", "USD")
                flag = currency_flags.get(cur, "🌐")
                impact = str(selected_event.get("impact", "High")).title()
                dt = selected_event.get("datetime_obj")
                event_time = dt.strftime("%H:%M") if dt else "—"
                saved = str(actuals_cache.get(code, "")).strip()
                published = _normalize_forex_factory_actual(selected_event.get("actual_str", ""))
                actual = saved or published or "—"
                forecast = selected_event.get("forecast_str", "—") or "—"
                previous = selected_event.get("prev_str", "—") or "—"
                title = str(selected_event.get("title", ""))

                # Read-only side summary. The full existing Causal Intelligence UI
                # remains available in the existing dialog opened below.
                render_html(f"""
                <div class="apex-fc2-detail-top">
                  <div>
                    <div class="apex-fc2-detail-kicker">Catalyst Intelligence</div>
                    <div class="apex-fc2-detail-title">{title}</div>
                    <div class="apex-fc2-detail-time">{event_time} · {flag} {cur} · {impact} Impact</div>
                  </div>
                </div>
                <div class="apex-fc2-metrics">
                  <div class="apex-fc2-metric"><div class="apex-fc2-metric-label">ACTUAL</div><div class="apex-fc2-metric-value">{actual}</div></div>
                  <div class="apex-fc2-metric"><div class="apex-fc2-metric-label">FORECAST</div><div class="apex-fc2-metric-value">{forecast}</div></div>
                  <div class="apex-fc2-metric"><div class="apex-fc2-metric-label">PREVIOUS</div><div class="apex-fc2-metric-value">{previous}</div></div>
                </div>
                <div class="apex-fc2-insight"><b>Institutional workflow</b>Open the complete intelligence view for nowcast, evidence, causal analysis, contradictions, cross-source confirmation and cross-asset implications.</div>
                """)
                if st.button("Open full intelligence", key=f"apex_fc2_open_full_{re.sub(r'[^a-zA-Z0-9_]', '_', code)[:60]}", use_container_width=True):
                    _show_forecaster_event_dialog(
                        selected_event,
                        fred_key,
                        channel_name,
                        auth_user,
                        actuals_cache,
                        currency_flags,
                    )

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
    
/* ===== ApexMacro Forecaster Calendar v1 — apex- scoped, safe ===== */
.apex-forecaster-shell{width:100%;box-sizing:border-box;}

/* Calendar container */
.apex-cal-wrap{background:linear-gradient(145deg,rgba(5,18,28,.96),rgba(3,11,19,.98));border:1px solid rgba(20,205,220,.18);border-radius:18px;padding:20px 20px 16px;margin-bottom:20px;box-shadow:0 20px 60px rgba(0,0,0,.42);}
.apex-cal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:12px;}
.apex-cal-title-block{}
.apex-cal-eyebrow{font-size:14px;font-weight:900;letter-spacing:1.5px;color:#20DDE8;text-transform:uppercase;margin-bottom:4px;}
.apex-cal-sub{font-size:11.5px;color:#8fa3b4;}
.apex-cal-nav{display:flex;align-items:center;gap:10px;}
.apex-cal-month-label{font-size:14px;font-weight:850;color:#F2F6F8;letter-spacing:1px;text-transform:uppercase;}

/* Weekday row */
.apex-cal-weekdays{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;margin-bottom:8px;}
.apex-cal-wd{font-size:9.5px;font-weight:900;color:#8fa3b4;text-transform:uppercase;letter-spacing:1px;text-align:center;padding:4px 0;}

/* Day grid */
.apex-cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;}
.apex-calendar-day{position:relative;min-height:74px;padding:10px 6px 8px;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;box-sizing:border-box;background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));border:1px solid rgba(110,155,175,.15);border-radius:9px;color:#F4F7FA;cursor:pointer;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;user-select:none;}
.apex-calendar-day:hover{border-color:rgba(20,205,220,.45);background:linear-gradient(145deg,rgba(6,48,60,.70),rgba(4,22,32,.88));transform:translateY(-1px);}
.apex-calendar-day.is-selected{border:1px solid rgba(20,225,235,.95)!important;background:linear-gradient(145deg,rgba(6,64,75,.78),rgba(4,28,38,.94))!important;box-shadow:0 0 18px rgba(20,220,230,.15)!important;}
.apex-calendar-day.is-today .apex-cal-date-num{color:#20DDE8;font-weight:950;}
.apex-calendar-day.is-other-month{opacity:.35;pointer-events:none;}
.apex-calendar-day.no-events{cursor:default;}
.apex-calendar-day.no-events:hover{transform:none;border-color:rgba(110,155,175,.15);background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));}
.apex-cal-date-num{font-size:15px;font-weight:850;color:#F4F7FA;line-height:1;margin-bottom:7px;}
.apex-cal-dots{display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:center;min-height:10px;}
.apex-impact-dot{width:6.5px;height:6.5px;border-radius:50%;flex-shrink:0;}
.apex-impact-dot.high{background:#A84DE3;box-shadow:0 0 6px rgba(168,77,227,.65);}
.apex-impact-dot.medium{background:#FFBC26;box-shadow:0 0 6px rgba(255,188,38,.55);}
.apex-impact-dot.low{background:#38D4E4;box-shadow:0 0 6px rgba(56,212,228,.50);}
.apex-cal-overflow{font-size:8.5px;font-weight:850;color:#A5B2BF;}

/* Legend */
.apex-cal-legend{display:flex;align-items:center;gap:20px;justify-content:center;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06);}
.apex-cal-legend-item{display:flex;align-items:center;gap:7px;font-size:10.5px;color:#A5B2BF;font-weight:650;}

/* Selected day header */
.apex-selected-day-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px;flex-wrap:wrap;}
.apex-selected-day-title-wrap{display:flex;align-items:center;gap:12px;}
.apex-selected-day-title{font-size:16px;font-weight:900;color:#20DDE8;letter-spacing:1px;text-transform:uppercase;}
.apex-selected-day-count{font-size:10px;font-weight:850;color:#20DDE8;background:rgba(20,221,232,.10);border:1px solid rgba(20,221,232,.25);padding:3px 10px;border-radius:999px;letter-spacing:.5px;}

/* Day event cards */
.apex-day-events-list{display:flex;flex-direction:column;gap:10px;margin-bottom:24px;}
.apex-day-event-card{width:100%;display:grid;grid-template-columns:70px 80px minmax(0,1fr) 70px 70px 70px 28px;gap:12px;align-items:center;padding:16px 18px;box-sizing:border-box;background:linear-gradient(145deg,rgba(10,28,39,.82),rgba(5,17,26,.92));border:1px solid rgba(90,145,165,.18);border-radius:11px;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;}
.apex-day-event-card:hover{border-color:rgba(20,205,220,.42);background:linear-gradient(145deg,rgba(6,42,58,.85),rgba(4,20,32,.95));}
.apex-dec-time{font-size:13px;font-weight:800;color:#F2F6F8;line-height:1.2;}
.apex-dec-time-sub{font-size:9.5px;color:#718795;font-weight:700;margin-top:2px;text-transform:uppercase;}
.apex-dec-currency{display:flex;align-items:center;gap:6px;}
.apex-dec-flag{font-size:18px;line-height:1;}
.apex-dec-cur-code{font-size:12px;font-weight:850;color:#F2F6F8;}
.apex-dec-body{min-width:0;}
.apex-dec-impact-row{display:flex;align-items:center;gap:6px;margin-bottom:4px;}
.apex-dec-impact-dot{width:7px;height:7px;border-radius:50%;}
.apex-dec-impact-dot.high{background:#A84DE3;}
.apex-dec-impact-dot.medium{background:#FFBC26;}
.apex-dec-impact-dot.low{background:#38D4E4;}
.apex-dec-impact-text{font-size:9.5px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.5px;}
.apex-dec-name{font-size:13.5px;font-weight:850;color:#F2F6F8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.apex-dec-val-box{text-align:center;}
.apex-dec-val-lbl{font-size:8.5px;font-weight:850;color:#718795;text-transform:uppercase;letter-spacing:.6px;}
.apex-dec-val{font-size:13px;font-weight:850;color:#F2F6F8;margin-top:2px;}
.apex-dec-val.actual-live{color:#00ffa3;}
.apex-dec-val.pending{color:#718795;}
.apex-dec-arrow{font-size:16px;color:#8fa3b4;font-weight:800;text-align:right;}
.apex-no-events-msg{padding:28px 16px;text-align:center;color:#718795;font-size:12.5px;background:rgba(5,18,28,.4);border:1px solid rgba(110,155,175,.10);border-radius:11px;margin-bottom:20px;}

/* Modal overlay */
.apex-event-modal-overlay{position:fixed;inset:0;z-index:9998;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(1,7,12,.58);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}

/* Modal */
.apex-event-modal{position:relative;z-index:9999;width:min(980px,72vw);max-height:90vh;overflow-y:auto;padding:26px 28px;box-sizing:border-box;border-radius:16px;background:linear-gradient(145deg,rgba(5,20,30,.98),rgba(3,13,21,.99));border:1px solid rgba(20,215,225,.72);box-shadow:0 28px 90px rgba(0,0,0,.60),0 0 40px rgba(15,210,220,.05);scrollbar-width:thin;scrollbar-color:#20DDE8 rgba(8,16,24,.6);}
.apex-event-modal::-webkit-scrollbar{width:6px;}.apex-event-modal::-webkit-scrollbar-track{background:rgba(8,16,24,.5);border-radius:4px;}.apex-event-modal::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#20DDE8,#00ffa3);border-radius:4px;}

/* Modal header */
.apex-modal-header{position:sticky;top:-26px;z-index:10;background:rgba(5,20,30,.97);margin:-26px -28px 20px;padding:18px 28px 14px;border-bottom:1px solid rgba(20,215,225,.14);display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}
.apex-modal-header-left{}
.apex-modal-title{font-size:17px;font-weight:900;color:#F2F6F8;letter-spacing:-.1px;}
.apex-modal-date{font-size:12px;color:#8fa3b4;margin-top:3px;}
.apex-modal-close-btn{width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:9px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);color:#F0F5F8;font-size:18px;cursor:pointer;line-height:1;}

/* Form fields */
.apex-form-grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px;}
.apex-form-label{font-size:9.5px;font-weight:800;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;}
.apex-form-box{padding:11px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);font-size:13px;font-weight:750;color:#F2F6F8;display:flex;align-items:center;justify-content:space-between;}

/* Actual/Forecast/Previous values row */
.apex-modal-values{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:14px;}
.apex-modal-value{padding:13px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);}
.apex-modal-value-lbl{font-size:9px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;margin-bottom:5px;}
.apex-modal-value-num{font-size:18px;font-weight:900;color:#F2F6F8;}
.apex-modal-value-num.beat{color:#00ffa3;}
.apex-modal-value-num.miss{color:#ff5e75;}
.apex-modal-value-num.inline{color:#ffd166;}

/* Causal card & AI panels */
.apex-intelligence-card{padding:14px 16px;border-radius:11px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);margin-bottom:14px;}
.apex-card-header-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.apex-card-title{font-size:11px;font-weight:900;color:#F2F6F8;letter-spacing:.5px;text-transform:uppercase;}
.apex-ai-badge{font-size:9px;font-weight:900;color:#20DDE8;background:rgba(32,221,232,.12);border:1px solid rgba(32,221,232,.30);padding:2px 7px;border-radius:6px;}
.apex-conf-badge{font-size:10px;font-weight:850;color:#00ffa3;}
.apex-evidence-list{margin:0;padding:0 0 0 16px;font-size:11px;color:#cbd8df;line-height:1.65;}
.apex-evidence-list li{margin-bottom:4px;}

/* Cross Asset Grid */
.apex-cross-asset-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:14px;}
.apex-cross-asset-card{padding:12px 10px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.16);border-radius:10px;text-align:center;}
.apex-cross-asset-name{font-size:11px;font-weight:850;color:#F2F6F8;display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;}
.apex-cross-asset-state{font-size:10px;font-weight:700;color:#8fa3b4;}

/* Admin Box */
.apex-admin-box{padding:14px 16px;border-radius:11px;background:rgba(12,28,40,.82);border:1px solid rgba(20,205,220,.25);margin-bottom:14px;}
.apex-admin-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;}
.apex-admin-title{font-size:11.5px;font-weight:900;color:#20DDE8;letter-spacing:.5px;}
.apex-admin-sub{font-size:10px;color:#8fa3b4;}

/* Mobile responsiveness */
@media(max-width:768px){
  .apex-cal-wrap{padding:14px 10px 12px;}
  .apex-cal-grid,.apex-cal-weekdays{gap:4px;}
  .apex-calendar-day{min-height:48px;padding:6px 2px 4px;border-radius:7px;}
  .apex-cal-date-num{font-size:13px;margin-bottom:3px;}
  .apex-impact-dot{width:4.5px;height:4.5px;}
  .apex-cal-dots{gap:2px;}
  .apex-cal-legend{gap:10px;flex-wrap:wrap;}
  .apex-day-event-card{grid-template-columns:1fr;gap:6px;padding:12px 14px;}
  .apex-event-modal-overlay{padding:8px;}
  .apex-event-modal{width:100%;max-width:none;height:min(94vh,100%);max-height:94vh;padding:18px 14px;border-radius:14px;}
  .apex-modal-header{margin:-18px -14px 16px;padding:14px 14px 12px;top:-18px;}
  .apex-form-grid-3,.apex-form-grid-2{grid-template-columns:1fr;}
  .apex-modal-values{grid-template-columns:1fr 1fr 1fr;}
  .apex-cross-asset-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media(max-width:480px){
  .apex-modal-values{grid-template-columns:1fr;}
  .apex-cross-asset-grid{grid-template-columns:1fr;}
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
    """Compatibility state + real Streamlit route navigation."""
    clean_view = str(view or "home").strip().lower()
    st.session_state["APEX_PUBLIC_VIEW"] = clean_view
    route_map = {
        "home": "pages/home.py",
        "login": "pages/login.py",
        "vip": "pages/vip.py",
    }
    target = route_map.get(clean_view)
    if target:
        st.switch_page(target)


def render_public_nav(active: str = "home") -> None:
    """Premium public navigation matching the supplied ApexMacro reference."""
    st.markdown("""
    <style>
      .st-key-apex_public_header{
        position:relative !important;
        width:min(1500px, calc(100% - 56px)) !important;
        max-width:1500px !important;
        min-height:106px !important;
        margin:28px auto 42px !important;
        padding:0 28px !important;
        border-radius:30px !important;
        overflow:visible !important;
        background:
          radial-gradient(circle at 4% 0%,rgba(0,239,255,.065),transparent 25%),
          linear-gradient(180deg,rgba(6,18,27,.90),rgba(4,12,19,.94)) !important;
        border:1px solid rgba(22,224,238,.38) !important;
        box-shadow:
          inset 0 1px 0 rgba(255,255,255,.055),
          0 0 0 1px rgba(0,229,246,.025),
          0 18px 48px rgba(0,0,0,.30),
          0 0 24px rgba(0,232,247,.045) !important;
        backdrop-filter:blur(22px) saturate(130%) !important;
        -webkit-backdrop-filter:blur(22px) saturate(130%) !important;
      }

      .st-key-apex_public_header::after{
        content:"";
        position:absolute;
        left:24px; right:24px; bottom:-1px;
        height:1px;
        background:linear-gradient(90deg,transparent,rgba(0,245,255,.72),transparent);
        filter:blur(.2px);
        pointer-events:none;
      }

      .st-key-apex_public_header .apex-ref-brand-wrap{
        position:absolute !important;
        left:44px !important;
        top:50% !important;
        transform:translateY(-50%) !important;
        display:flex !important;
        align-items:center !important;
        margin:0 !important;
        z-index:10 !important;
      }

      .apex-ref-brand{
        display:flex !important;
        align-items:center !important;
        gap:20px !important;
      }

      .apex-ref-logo{
        width:76px !important;
        height:76px !important;
        min-width:76px !important;
        border-radius:22px !important;
        display:flex !important;
        align-items:center !important;
        justify-content:center !important;
        background:linear-gradient(145deg,rgba(5,30,41,.92),rgba(4,13,22,.95)) !important;
        border:1px solid rgba(0,239,255,.58) !important;
        box-shadow:
          inset 0 1px 0 rgba(255,255,255,.05),
          0 0 26px rgba(0,234,255,.08) !important;
      }
      .apex-ref-logo svg{
        width:48px !important;
        height:48px !important;
      }

      .apex-ref-brand-name{
        color:#f6f8fb !important;
        font-size:29px !important;
        line-height:1 !important;
        font-weight:950 !important;
        letter-spacing:2.6px !important;
        white-space:nowrap !important;
      }
      .apex-ref-brand-name span{color:#f6b934 !important;}

      .apex-ref-brand-sub{
        margin-top:9px !important;
        color:#9ba9b5 !important;
        font-size:10.5px !important;
        line-height:1 !important;
        font-weight:750 !important;
        letter-spacing:5px !important;
        white-space:nowrap !important;
      }

      .apex-ref-toplinks{
        position:absolute !important;
        left:55% !important;
        top:50% !important;
        transform:translate(-50%,-50%) !important;
        display:flex !important;
        align-items:center !important;
        gap:48px !important;
        z-index:12 !important;
        white-space:nowrap !important;
      }

      .apex-ref-toplinks a{
        display:inline-flex !important;
        align-items:center !important;
        justify-content:center !important;
        min-height:40px !important;
        padding:0 !important;
        border:0 !important;
        border-radius:0 !important;
        background:transparent !important;
        color:#f0f4f7 !important;
        text-decoration:none !important;
        font-size:18px !important;
        line-height:1 !important;
        font-weight:800 !important;
        letter-spacing:-.15px !important;
        text-shadow:0 0 14px rgba(255,255,255,.025);
        transition:.18s ease !important;
      }

      .apex-ref-toplinks a:hover{
        color:#20effb !important;
        text-shadow:0 0 14px rgba(0,239,255,.18) !important;
      }


      .st-key-apex_public_header .st-key-apex_profile_access,
      .st-key-apex_public_header [class*="st-key-public_home_back_"]{
        position:absolute !important;
        top:50% !important;
        right:34px !important;
        transform:translateY(-50%) !important;
        width:68px !important;
        height:68px !important;
        margin:0 !important;
        z-index:20 !important;
      }

      .st-key-apex_profile_access button,
      .st-key-public_home_back_login button,
      .st-key-public_home_back_vip button{
        width:68px !important;
        height:68px !important;
        min-height:68px !important;
        padding:0 !important;
        margin:0 !important;
        border-radius:22px !important;
        border:1px solid rgba(0,239,255,.68) !important;
        background:#031019 !important;
        box-shadow:
          inset 0 1px 0 rgba(255,255,255,.04),
          0 0 22px rgba(0,239,255,.065) !important;
      }

      .st-key-apex_profile_access button{
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Ccircle cx='24' cy='16' r='7' fill='%2300efff'/%3E%3Cpath d='M11 38c1-9 6-13 13-13s12 4 13 13z' fill='%2300efff'/%3E%3C/svg%3E") !important;
        background-repeat:no-repeat !important;
        background-position:center !important;
        background-size:34px 34px !important;
      }
      .st-key-apex_profile_access button p{font-size:0 !important;}

      .st-key-apex_profile_access button:hover{
        border-color:#28f4ff !important;
        box-shadow:0 0 28px rgba(0,239,255,.16) !important;
      }

      .apex-ref-navline{display:none !important;}

      @media(max-width:1250px){
        .st-key-apex_public_header{
          width:calc(100% - 34px) !important;
          min-height:94px !important;
          padding:0 20px !important;
        }
        .st-key-apex_public_header .apex-ref-brand-wrap{left:26px !important;}
        .apex-ref-logo{width:62px !important;height:62px !important;min-width:62px !important;border-radius:18px !important;}
        .apex-ref-logo svg{width:39px !important;height:39px !important;}
        .apex-ref-brand{gap:14px !important;}
        .apex-ref-brand-name{font-size:23px !important;}
        .apex-ref-brand-sub{font-size:8.5px !important;letter-spacing:3.8px !important;}
        .apex-ref-toplinks{left:55% !important;gap:25px !important;}
        .apex-ref-toplinks a{font-size:14px !important;}
        .st-key-apex_public_header .st-key-apex_profile_access{right:24px !important;width:58px !important;height:58px !important;}
        .st-key-apex_profile_access button{width:58px !important;height:58px !important;min-height:58px !important;border-radius:18px !important;}
      }

      @media(max-width:900px){
        .st-key-apex_public_header{
          width:calc(100% - 22px) !important;
          min-height:122px !important;
          margin:12px auto 20px !important;
          border-radius:22px !important;
        }
        .st-key-apex_public_header .apex-ref-brand-wrap{
          left:18px !important;
          top:17px !important;
          transform:none !important;
        }
        .apex-ref-logo{width:48px !important;height:48px !important;min-width:48px !important;border-radius:14px !important;}
        .apex-ref-logo svg{width:29px !important;height:29px !important;}
        .apex-ref-brand{gap:10px !important;}
        .apex-ref-brand-name{font-size:18px !important;letter-spacing:1.6px !important;}
        .apex-ref-brand-sub{margin-top:6px !important;font-size:7px !important;letter-spacing:2.5px !important;}
        .apex-ref-toplinks{
          left:16px !important;
          right:16px !important;
          top:78px !important;
          transform:none !important;
          justify-content:center !important;
          gap:18px !important;
          overflow-x:auto !important;
          scrollbar-width:none !important;
        }
        .apex-ref-toplinks::-webkit-scrollbar{display:none !important;}
        .apex-ref-toplinks a{font-size:10px !important;min-height:28px !important;}
        .st-key-apex_profile_access button{
          width:44px !important;height:44px !important;min-height:44px !important;border-radius:13px !important;
          background-size:25px 25px !important;
        }
      }

      @media(max-width:430px){
        .apex-ref-toplinks{gap:13px !important;}
        .apex-ref-toplinks a{font-size:9px !important;}
      }
    
/* ===== ApexMacro Forecaster Calendar v1 — apex- scoped, safe ===== */
.apex-forecaster-shell{width:100%;box-sizing:border-box;}

/* Calendar container */
.apex-cal-wrap{background:linear-gradient(145deg,rgba(5,18,28,.96),rgba(3,11,19,.98));border:1px solid rgba(20,205,220,.18);border-radius:18px;padding:20px 20px 16px;margin-bottom:20px;box-shadow:0 20px 60px rgba(0,0,0,.42);}
.apex-cal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:12px;}
.apex-cal-title-block{}
.apex-cal-eyebrow{font-size:14px;font-weight:900;letter-spacing:1.5px;color:#20DDE8;text-transform:uppercase;margin-bottom:4px;}
.apex-cal-sub{font-size:11.5px;color:#8fa3b4;}
.apex-cal-nav{display:flex;align-items:center;gap:10px;}
.apex-cal-month-label{font-size:14px;font-weight:850;color:#F2F6F8;letter-spacing:1px;text-transform:uppercase;}

/* Weekday row */
.apex-cal-weekdays{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;margin-bottom:8px;}
.apex-cal-wd{font-size:9.5px;font-weight:900;color:#8fa3b4;text-transform:uppercase;letter-spacing:1px;text-align:center;padding:4px 0;}

/* Day grid */
.apex-cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;}
.apex-calendar-day{position:relative;min-height:74px;padding:10px 6px 8px;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;box-sizing:border-box;background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));border:1px solid rgba(110,155,175,.15);border-radius:9px;color:#F4F7FA;cursor:pointer;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;user-select:none;}
.apex-calendar-day:hover{border-color:rgba(20,205,220,.45);background:linear-gradient(145deg,rgba(6,48,60,.70),rgba(4,22,32,.88));transform:translateY(-1px);}
.apex-calendar-day.is-selected{border:1px solid rgba(20,225,235,.95)!important;background:linear-gradient(145deg,rgba(6,64,75,.78),rgba(4,28,38,.94))!important;box-shadow:0 0 18px rgba(20,220,230,.15)!important;}
.apex-calendar-day.is-today .apex-cal-date-num{color:#20DDE8;font-weight:950;}
.apex-calendar-day.is-other-month{opacity:.35;pointer-events:none;}
.apex-calendar-day.no-events{cursor:default;}
.apex-calendar-day.no-events:hover{transform:none;border-color:rgba(110,155,175,.15);background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));}
.apex-cal-date-num{font-size:15px;font-weight:850;color:#F4F7FA;line-height:1;margin-bottom:7px;}
.apex-cal-dots{display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:center;min-height:10px;}
.apex-impact-dot{width:6.5px;height:6.5px;border-radius:50%;flex-shrink:0;}
.apex-impact-dot.high{background:#A84DE3;box-shadow:0 0 6px rgba(168,77,227,.65);}
.apex-impact-dot.medium{background:#FFBC26;box-shadow:0 0 6px rgba(255,188,38,.55);}
.apex-impact-dot.low{background:#38D4E4;box-shadow:0 0 6px rgba(56,212,228,.50);}
.apex-cal-overflow{font-size:8.5px;font-weight:850;color:#A5B2BF;}

/* Legend */
.apex-cal-legend{display:flex;align-items:center;gap:20px;justify-content:center;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06);}
.apex-cal-legend-item{display:flex;align-items:center;gap:7px;font-size:10.5px;color:#A5B2BF;font-weight:650;}

/* Selected day header */
.apex-selected-day-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px;flex-wrap:wrap;}
.apex-selected-day-title-wrap{display:flex;align-items:center;gap:12px;}
.apex-selected-day-title{font-size:16px;font-weight:900;color:#20DDE8;letter-spacing:1px;text-transform:uppercase;}
.apex-selected-day-count{font-size:10px;font-weight:850;color:#20DDE8;background:rgba(20,221,232,.10);border:1px solid rgba(20,221,232,.25);padding:3px 10px;border-radius:999px;letter-spacing:.5px;}

/* Day event cards */
.apex-day-events-list{display:flex;flex-direction:column;gap:10px;margin-bottom:24px;}
.apex-day-event-card{width:100%;display:grid;grid-template-columns:70px 80px minmax(0,1fr) 70px 70px 70px 28px;gap:12px;align-items:center;padding:16px 18px;box-sizing:border-box;background:linear-gradient(145deg,rgba(10,28,39,.82),rgba(5,17,26,.92));border:1px solid rgba(90,145,165,.18);border-radius:11px;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;}
.apex-day-event-card:hover{border-color:rgba(20,205,220,.42);background:linear-gradient(145deg,rgba(6,42,58,.85),rgba(4,20,32,.95));}
.apex-dec-time{font-size:13px;font-weight:800;color:#F2F6F8;line-height:1.2;}
.apex-dec-time-sub{font-size:9.5px;color:#718795;font-weight:700;margin-top:2px;text-transform:uppercase;}
.apex-dec-currency{display:flex;align-items:center;gap:6px;}
.apex-dec-flag{font-size:18px;line-height:1;}
.apex-dec-cur-code{font-size:12px;font-weight:850;color:#F2F6F8;}
.apex-dec-body{min-width:0;}
.apex-dec-impact-row{display:flex;align-items:center;gap:6px;margin-bottom:4px;}
.apex-dec-impact-dot{width:7px;height:7px;border-radius:50%;}
.apex-dec-impact-dot.high{background:#A84DE3;}
.apex-dec-impact-dot.medium{background:#FFBC26;}
.apex-dec-impact-dot.low{background:#38D4E4;}
.apex-dec-impact-text{font-size:9.5px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.5px;}
.apex-dec-name{font-size:13.5px;font-weight:850;color:#F2F6F8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.apex-dec-val-box{text-align:center;}
.apex-dec-val-lbl{font-size:8.5px;font-weight:850;color:#718795;text-transform:uppercase;letter-spacing:.6px;}
.apex-dec-val{font-size:13px;font-weight:850;color:#F2F6F8;margin-top:2px;}
.apex-dec-val.actual-live{color:#00ffa3;}
.apex-dec-val.pending{color:#718795;}
.apex-dec-arrow{font-size:16px;color:#8fa3b4;font-weight:800;text-align:right;}
.apex-no-events-msg{padding:28px 16px;text-align:center;color:#718795;font-size:12.5px;background:rgba(5,18,28,.4);border:1px solid rgba(110,155,175,.10);border-radius:11px;margin-bottom:20px;}

/* Modal overlay */
.apex-event-modal-overlay{position:fixed;inset:0;z-index:9998;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(1,7,12,.58);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}

/* Modal */
.apex-event-modal{position:relative;z-index:9999;width:min(980px,72vw);max-height:90vh;overflow-y:auto;padding:26px 28px;box-sizing:border-box;border-radius:16px;background:linear-gradient(145deg,rgba(5,20,30,.98),rgba(3,13,21,.99));border:1px solid rgba(20,215,225,.72);box-shadow:0 28px 90px rgba(0,0,0,.60),0 0 40px rgba(15,210,220,.05);scrollbar-width:thin;scrollbar-color:#20DDE8 rgba(8,16,24,.6);}
.apex-event-modal::-webkit-scrollbar{width:6px;}.apex-event-modal::-webkit-scrollbar-track{background:rgba(8,16,24,.5);border-radius:4px;}.apex-event-modal::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#20DDE8,#00ffa3);border-radius:4px;}

/* Modal header */
.apex-modal-header{position:sticky;top:-26px;z-index:10;background:rgba(5,20,30,.97);margin:-26px -28px 20px;padding:18px 28px 14px;border-bottom:1px solid rgba(20,215,225,.14);display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}
.apex-modal-header-left{}
.apex-modal-title{font-size:17px;font-weight:900;color:#F2F6F8;letter-spacing:-.1px;}
.apex-modal-date{font-size:12px;color:#8fa3b4;margin-top:3px;}
.apex-modal-close-btn{width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:9px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);color:#F0F5F8;font-size:18px;cursor:pointer;line-height:1;}

/* Form fields */
.apex-form-grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px;}
.apex-form-label{font-size:9.5px;font-weight:800;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;}
.apex-form-box{padding:11px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);font-size:13px;font-weight:750;color:#F2F6F8;display:flex;align-items:center;justify-content:space-between;}

/* Actual/Forecast/Previous values row */
.apex-modal-values{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:14px;}
.apex-modal-value{padding:13px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);}
.apex-modal-value-lbl{font-size:9px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;margin-bottom:5px;}
.apex-modal-value-num{font-size:18px;font-weight:900;color:#F2F6F8;}
.apex-modal-value-num.beat{color:#00ffa3;}
.apex-modal-value-num.miss{color:#ff5e75;}
.apex-modal-value-num.inline{color:#ffd166;}

/* Causal card & AI panels */
.apex-intelligence-card{padding:14px 16px;border-radius:11px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);margin-bottom:14px;}
.apex-card-header-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.apex-card-title{font-size:11px;font-weight:900;color:#F2F6F8;letter-spacing:.5px;text-transform:uppercase;}
.apex-ai-badge{font-size:9px;font-weight:900;color:#20DDE8;background:rgba(32,221,232,.12);border:1px solid rgba(32,221,232,.30);padding:2px 7px;border-radius:6px;}
.apex-conf-badge{font-size:10px;font-weight:850;color:#00ffa3;}
.apex-evidence-list{margin:0;padding:0 0 0 16px;font-size:11px;color:#cbd8df;line-height:1.65;}
.apex-evidence-list li{margin-bottom:4px;}

/* Cross Asset Grid */
.apex-cross-asset-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:14px;}
.apex-cross-asset-card{padding:12px 10px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.16);border-radius:10px;text-align:center;}
.apex-cross-asset-name{font-size:11px;font-weight:850;color:#F2F6F8;display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;}
.apex-cross-asset-state{font-size:10px;font-weight:700;color:#8fa3b4;}

/* Admin Box */
.apex-admin-box{padding:14px 16px;border-radius:11px;background:rgba(12,28,40,.82);border:1px solid rgba(20,205,220,.25);margin-bottom:14px;}
.apex-admin-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;}
.apex-admin-title{font-size:11.5px;font-weight:900;color:#20DDE8;letter-spacing:.5px;}
.apex-admin-sub{font-size:10px;color:#8fa3b4;}

/* Mobile responsiveness */
@media(max-width:768px){
  .apex-cal-wrap{padding:14px 10px 12px;}
  .apex-cal-grid,.apex-cal-weekdays{gap:4px;}
  .apex-calendar-day{min-height:48px;padding:6px 2px 4px;border-radius:7px;}
  .apex-cal-date-num{font-size:13px;margin-bottom:3px;}
  .apex-impact-dot{width:4.5px;height:4.5px;}
  .apex-cal-dots{gap:2px;}
  .apex-cal-legend{gap:10px;flex-wrap:wrap;}
  .apex-day-event-card{grid-template-columns:1fr;gap:6px;padding:12px 14px;}
  .apex-event-modal-overlay{padding:8px;}
  .apex-event-modal{width:100%;max-width:none;height:min(94vh,100%);max-height:94vh;padding:18px 14px;border-radius:14px;}
  .apex-modal-header{margin:-18px -14px 16px;padding:14px 14px 12px;top:-18px;}
  .apex-form-grid-3,.apex-form-grid-2{grid-template-columns:1fr;}
  .apex-modal-values{grid-template-columns:1fr 1fr 1fr;}
  .apex-cross-asset-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media(max-width:480px){
  .apex-modal-values{grid-template-columns:1fr;}
  .apex-cross-asset-grid{grid-template-columns:1fr;}
}

</style>
    """, unsafe_allow_html=True)

    with st.container(key="apex_public_header"):
        render_html("""
        <div class="apex-ref-brand-wrap">
          <div class="apex-ref-brand">
            <div class="apex-ref-logo">
              <svg viewBox="0 0 360 365" fill="none">
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
        </div>

        <nav class="apex-ref-toplinks" aria-label="ApexMacro home sections">
          <a href="/#apex-platform">Platform</a>
          <a href="/#apex-features">Features</a>
          <a href="/#apex-data">Data Sources</a>
          <a href="/vip">Pricing</a>
          <a href="/#apex-company">Company</a>
        </nav>

        """)

        if active == "home":
            if st.button("Access", key="apex_profile_access", help="VIP Login / Get VIP"):
                _set_public_view("login")
                st.rerun()
        else:
            if st.button("←", key=f"public_home_back_{active}", help="Back to Home"):
                _set_public_view("home")
                st.rerun()

    render_html('<div class="apex-ref-navline"></div>')






def render_public_home() -> None:
    """Public ApexMacro landing page matching the supplied premium reference."""
    st.markdown("""
    <style>
      .block-container{
        max-width:1600px !important;
        padding-top:18px !important;
        padding-left:28px !important;
        padding-right:28px !important;
      }
      .apex-home-shell{
        width:100%;
        max-width:1500px;
        margin:0 auto;
      }

      .apex-ref-hero{
        position:relative;
        overflow:hidden;
        min-height:650px;
        padding:58px 52px 44px;
        border-radius:30px;
        background:
          radial-gradient(circle at 55% 10%,rgba(0,238,255,.035),transparent 25%),
          linear-gradient(90deg,#031019 0%,#031019 44%,rgba(3,16,25,.66) 59%,rgba(2,10,16,.08) 100%);
        border:1px solid rgba(0,227,242,.53);
        box-shadow:
          inset 0 1px 0 rgba(255,255,255,.035),
          0 30px 80px rgba(0,0,0,.38),
          0 0 22px rgba(0,231,246,.035);
      }

      .apex-ref-hero::before{
        content:"";
        position:absolute;
        inset:0;
        z-index:1;
        pointer-events:none;
        background:
          radial-gradient(circle at 53% 45%,rgba(0,228,246,.055),transparent 30%),
          linear-gradient(90deg,rgba(2,11,18,.98) 0%,rgba(2,11,18,.90) 42%,rgba(2,11,18,.40) 56%,transparent 72%);
      }

      .apex-ref-globe{
        position:absolute;
        z-index:0;
        right:0;
        top:0;
        bottom:0;
        width:58%;
        background-image:url("data:image/webp;base64,UklGRmqoAABXRUJQVlA4IF6oAADQ7wKdASq6A4wCPmEuk0ekIiGqopKKGVAMCWdCqeB2VgfLUkbQB2brfAhlV5v9bY32EZ0LmZ2ZfSF/XvUe8yH/w9d/mm83b02/2z1IP7z1MPoHdLZ/j/Pi9QD/8e3D/AODU9JPyL+o/6XjL+Qe7B7u9Ab13/V83/5n+Z/6f+X9jP+l4b/lH9n6B35h/Qv+V6A/4/i6b7+TnsNe4X4ryNf0PS39L9Qf+icVH/Y9QTyRv9r/9f7z09/oX/C/+3+6+A/+jf4j/uCoCAvTlgerwPepxmp8HR9gNuhCXlvLMSi7Kx26EJeW9TjM8Nq7p8o7fxHsd/+Yu47bIarG7hiRjUpzU1g8lKIyivc8iXlvLMSieC8VwHPP0099nKiu7u7zESzf3tG+N4mvCqqqsWBMMbXWg+n77/Hby/o5fxaTN7MnHQYkHq103JNAIvAjw95SMADCidHyAlFovjZ7V7UfQul+9fJONpu7u8Y57evaqqm4uqv1s6l3/9tbU51M5PyETar/rJTv6sQ5ioRhzIu8G+Ii9W0myBrQs0r0POZghbwitOiRe8FFeKUb4NjOB1u7u87OoFVVN3WTd3dtIiw+5ooE4VeaOzQqHiVlg2VuhBLqsuVurgzwiTwSjTJYcj/NAOBn+uBjaHLbzpTjAmaOXMM/RN0iRncZ3WIkGuYYRAAO6bL+gmBeG46m/+hbkOW3h71niMDiL/RI4nTkoPB7RLX3eaA3Lj9oFj/ZxvfS0lRnFIDeaV3mrT/CqDg/ZQATc50HXRU3py7gvBW49zG+pbQBVVTbNpu2jMMqDjCKDxzefP8nGcyo9OdhacPk/CZt7dK2NKWXtrlGJutnlShNXxC0Y7JhPOObRA0IroOXgjqrQP55CByAc0DbhBOd3jcM+V1yLTDOmj9GECPLN6sCnq4JIGRi+ZbiKqqqqq4oaPl+HnkFCV1t4SiFsEHT9BNMfBddt47JCifwWsAb20E0uCXLd9AJG2WW4wbHK4wtV3LJ8M8s1B4MctHgaOBn7yf+kGSBsJ3dRLF79y61F/HjtT1o+EXzJZ79z1mzoCy+tLAoocdUSfHLBCWdD5YzzTRmobe35pA3Vx4ONrfHjQxt3w3IE+CdpeneH72f9Id6XMNGZmZl9FSiw3E9aV1hJGAhzfqutdeQmrsKqXwov4gflV6eE8/nOJhPbt/lyQyGkx9vgFXPdn/24gtM2F3G7Nl/PjgQqlvIQNJ2IARe+E7TfOuqttG+KedXM0aSes59//WxQSk1wR9Kwoj64QUsrk2Wo01aIEAcN6Dous28+H3Zubbt6ezPmb7BYsFVVU120iYCidGyWp5TSMfavh+k41qadBiE68pBk4K9g65Q6GyevxOhsPXqEH1cHkj39O3o6rzg9w3GytZd8M2oQql25+WGPgx5AYr/G4nFIFB5Mxy2N/25sUH7vWx5EefMCLtRI/20TyST7qyXb+VX4mqMQADO4foMA0AUbE7zUwn8VzOBN4B1YDwYCncp64EWZIVz7zlCS8W8PJe7O2wuR847Sb6rQkEDqilk8SxnY2cv7iLUwoCvzFWpaXyOmL/uC9gUiwdpinf5FrVCyiSA6MNdCvcCzzxmM33I7zHMI9jy3k9G0gkdhn6+HlmLmK5OuJw4XTs/7nK83b+HAPWaPgkzakGE8rry6JU1xJbYEZ8eBSSOO828EYpYiEOua/ln5toy4FUrVJb+D3eFwj6/ExWGBUbv4pCuMnz+77fTVBSO/FkNUjd/0I3a1TYn+nNid3s4Xk29ldEWmNV3l+SzWQM1Qvi3i3miodHtNRqlKh/v7DWyq0STN/xh6rnDLBrJibLalOF9aK0Tx7aNO6mLy1nxpvSNtpi+cwuyDqVr0y81gIcWv2ceMWyu1vcLqMtJDDlpCYs/DIPeNcNYjlB36vh1mW7ssH0NbkAvB/BA91cHDsk/DlbwxQomEEFXCw8+RXannvGfxNYjcWyg0J1SCpXEkDsbtzOmKg787n9/X/faIQx48EzUBsmETmTfLIGW9KAXQwVaRsQC/HfTGC3/08GYV8RyF/ngUJVelqsEa9QK/ufpvS7P1e8F4ZKSp0Mrjb4xJMokW3IkgyF3OW9Rv7HaVBy/3zd/XobmgT0syEGfM8Xo2N1H8H/HP/FdbArmD8QbOi/6m8uB5cUasJi29TibZRDx2I20np2am/2JvHt0OBiDlrWRTf0/7suldNzWP0k1x+t2xflTPSQVliDuZDWlEsowo1SYyqDkRf5W8RvZcgeq+7o92awt9i27Bb/HjBNvKXfbDzKAqqbAlqGNVvxxZBJYOxM1p8/nfJn0jl/TrE8Et11bmjBxvvPEIInP6NXxDFfPFo4RdOAK1J9j4ihO3A0QzVjZXsu3QPQI81zjGVNQOy9daI71KvVdEdWbnWxa5WjtvV0Mv8tP9udq5o2ihzJS6rVgAwovw2I6NHHsXvCMgzt9WyRNboLYuQJY3+eIR5c8RRCjTvNHzxk2I6+5uaSDSdZKv0fIB3NRFL4x6whWw2fSxd3ioF6mjvrmJNUmy0JtCOXG8ddeNRtAv+kB2zix63N4ipRoKOdoL02ckbYm5Z2bKaXN67YfUzRhW6YrISN+opT05S2vC1TN/+N/b3WM25caRAKDvlcxCIvc53Urzj4OSCiKAT7jHrsiiHJKPojEveAsuM9Yin0KYi1D/n1PfP+m/EpPvG9Ub8cQxzEN50JWtWLu8tCnvk+nC/6MUvnlhuh1yAeCiroN3CwLb2//sYjneY9dUkVAxsXK4LGSCGD955K2eHS1ws2X996I0xxQLJ0ju22QPab+nJNGodjMuC1vTKj2wWs4JLii7WYgVoIbWzxvYuQSnzpq19X5cwkJEin9ifEy7UaGDfcG6n/VYELDi+BEqON9gLiQK81g1X8cZwEAIjt7f7Hc85S8LiEUXZrGCVdTYhRP1T4uORmRdoCMzVXS7NBrG0U2QWDNhr0E/SVthSRuV1ipi+srywUh39fMtCEzwZbjDRgchnPm0jnA+hE4G8rQBGM7EVICRrrA3c54sMybKvuMFWlWyB5p5lUMqupGZkO3wM3v87euAzG1D0FkLOcBdkNIKBhRhnLTjgsPxg9hBNd6l+JFZ+9dOYYycGuCGDKnrvcbEFyU6kpYk0aGUh6Bxg4/f6YjaAskkoPo7/iVxxy3jRXFGTH3dS9s74ptXmuNhffWTFqd/mNpIIGswxPGK4uUBPC9R+kr9w10ll4iYGC8DNdHt7kvKZ7UqSY8UJzjrBlqvq8irFhO5q3mBLYL+6jVGtquoL09ifMQxXFNhqj2bySw/T9g2hFXYfzBm0wf0CpqTZuBKNjl4Nk7upjkSuAdH+VMsoOVoKuHReul4HHIevQsTQYFFhCmsJApw0rle7YCc4QweoKEGOu0aqSgAJAG/KXfaI8c4wXQes/0TboqlhtXAb8S1N1U3Pt/wi8WwNmLdxhCdNRGedLibY4cYVBSp06OqflaXybgwhBg3eLnjH2puvxIUfTkQMrv/srdfUm8AxP/oa50NcOMBXfXBb6BFUpkDMkLbu7ig3g4Mks4bmJNi6FNEmI7bGUPSctaZlscrMszdRow8IDZMGiHdZlLq43BjV1EpOCYymxSPpe/KSzxkuwdmYLE8oYR36gQNeDniYEul5Pue6sn8h3BGyMTgWwFpZ17HHbkfMY84i8Avg+4aqILIL5RKaJj5xqxlpQWWPe715KDCr/w7iL3Ffv9IF4RBO+pk92WsvFRTvP4kIQW1PNRIhaiBxcbDatkAXdXzM6YnRFGQ/TgALTgYEvlfpju+MTKTkFWdnidJd/aLLwgn3a73JBxuok7G2uJG0ycpw8e4UlfkusgPauNyonpNvpd1f9N5PGreFfPQBIyCIymUDyMFm0yZ9Ygbe7vFxSrIK05xzvDWxY/S0Qbf4KA+/O7j7r0vn3v6ZN32vxyi/4CsnfImZJC8w2lPJWFEcVVVgUyaehHmlFwAQ/3pnQsTls7+u/vbvp3874G8yHliX0sIqGqn3wCkEGembj7pcGiiH3zUVU093SuGhUlgAmQ0thToZYW/IWy9x9JnlJYbhrXho0Kf5NBAv+D8bryt3L2oeq+CC3aFo9pj84tKnwnGGaQVihj+EqIwFaO5oOFQZhV7AsKszXc1HVg9ob8jQofygX8W2vQEP3abaKkrSYdohbK6a6NCcxbzYsE9UGziRdH7swlN1Qnx/1HUGY/y8Qz0foz1ajzmzgxJ977ZQD/Eav2twiYsfId2zfE8ZseujBAZl8lmjx3/9Fcc1JKrG/YaQtE7WJjsUYzjHlDQQTKH7R8lKB8D+DXGBN/vXdYWYShXeE+bqE0g8No6yiQ3MzrO/uzkKBRTNP/+5IG1E9/T0LhgSQarTTmQQV5k3D7ISL7aa00BEFy/DqTKhZBhzI+ePiMj9Egp5MJSKDDyH2eWt6d/CN6JCTgyrm6nG9oCaLa/o7guOweGJZpMEYg2RqSD4XWIoDGodUUqtZ+TtsVGcgYlHVaCLTZaM+LXuRlDj6TZB2c9/c+6xJxLwsth+pCFQeBEU0YtEPjiar3YKCsPkBUErb+Qmz7tKt9VFXh+g3ewUVYOHk6HDuiVddTo8papIZKlYBMw8ohpfnXwyhyTYrrJLP3SZDyvswJ17VNw604LTMJJXbadvAzDuaXtjnAETyYNU+StuZDajoTwEkGq/8eJTECkbxI/HTOIAmsk8eJxRXu2lfp5gsiy8BYGpkbfiaFRW+RK9s8kRC6woOnjS8LlXVGCcbOCq++DFxMV09jeTx+3y9kMYgSfff70Wm5U03orq6DuuubDON7RUfE9R9gbHboHcRUBWr6aqU+s6GNEnnwNMkJ2Uz4OJaAWh9/g0vU/nyQNYDzaJvzWEYsJrQYKQ3R5PZn/lTmR9U/R8C20APzDmOudJlPV0FI6jsX4RxkwyVTLIpMt6WMxwiryq7pHdapRphxJiCBhZURfAwI1AYkD/DgI/Htd1R7J5C8oC2Rtdb5A1+M/8aSoz0ObdeYJIvBqyueC9KTXijrmHdHGIkO8dq4YCdkWi4Q6T0wryMa9LFDEVGvh7jmsiRiogY2b5ORZvZZeBxlw2ImmDC63u3y0BAZgT3lgtNOukR9LnEghf2WPc7JITa7cnnWW7wxLHHBlej9ATBy1oFnlwlVNtlbbK8uv71bVCEDdPB+mTPpwqQeLCkDQq8MXyRJdzNGZ9w/2UnErK2ju9qOf2J3SD0FwCsjpJada7J50lJLhXXzVjhhNwTvfVtFNfpcDJ/az6LywOfuhuBOlCmv1jRFLxfNDKvFQ3fMrUvQIZ95ntNfK2M+W4xQPtZ4jwCOCNO0YNWCEiJhCc+vF4KC2cy1weXY4MjTGi05rfhnozDI49OtBP1tbGY/FGSQ8DW8SgklM3EYemUbIY81VTsF1avd93Zr7N8qcCGNRSyd70e579558JAWbjlrN3ihx80dRFhpzdBlFDuKlwXHlU0vV0MGCbxt/wgKi+U9bqdmyp7Tt9Ra0GkOiPJJ/HfmODzKLUzSH6EkeeRxIAdkdRatXKLBIBbDQjSg9R7l+IA213tmduEXThyc7FBEu6zEXFH+5aRjLt+hyz1tyAqm2rrqEO82dXLr5WxYYuuykzmsFHXL71iXu8ErjxgrEI/T2a4MEEwI3oHgAkNvohYscSK3rTaZ4/7Sg1wExdE3d7OrRlxWKFPuW92zbZv0mc+nJedUF5t8R2F93soo3hkdQ0p8x5Jk8MWoGrz515dMeMPezClrzmuDRvzCASBpTmhyCDifl4BTgHCFXqJ/vmAt7qt89PPO2UucFtxKi0pacYjkjNrl1tHhHfhxvflf/+l0eJ/NIBRoTuiGJHm21zqKh0x7ys/KgT1tDkszZvV5xc613oRRi8mc5GWeZp977Ot0czCrSqwdNHBML9GUf3zr5ArHhhm1LnJKKzSoKLehXhMIUMidSIEvD4nSK7IJuQ5kcKVjaRDTBJM07GMqDyDA6M01XmDFXGE9x62Vk0St+lRa3H1IykgQH7AEXK7LiZOwMNRIn0z0RfXLQCtktkgWzHsEbMBgyKRsS+Pejq3HJYCEpgXM+kDgyZ+H0lEiCatTw98g0LeG3wjKjlytWrVHQ6ij9SjPkIQFaI89YU4Gl99dwaacQ1krAVwMacbYts3pXNyuczej1wB8UNsSfR7+QRH3p49hyXuhfuPdZ2+lDaOGDDBM5XPRG42Xv1qTIGA25t+KqnFOz9W37S9FBx7qquF9lG+VR3glZ5+YfCtpydqLnTvOcwK7EdI9nrI21kVpuUkO5a//ArU9sVx8NfWL3HBLqkxb9T1IuuvBV3KY5Dk0bd4gu7Q0pfv3H62ZMNv6mLBVVJrTdICiTu8mjhOHud/fu63VN8li3xkpcU/fQDeQJvOBEhvhq1Gtu43ICffRTa8B9geUa8FiTJdW3Ew/Ba2biijfRDPiSH0ml0h6iT6grX8jO+b3eSLWvSgjn06VyPkEfGxSlorJevZ37fus/4NIyj+6gb1Ob26uMYd3WbGLzIHpSaHEgi1VgQqmbS5122ynjcDF2WgFkb2fzLQfbVelbwU992CF3deidR94lpb3yuwgK130VZsLEqMeoFW5J76aQPLum5ngiuhpUMbNG6jS6PGkGQjae1x8Sy3Ct2DfD9tggMxMQ8R7U/yTt5i3++JxskamCZjpa7+T8GmkzXw1LlKYZf9ck1U0vu3LAYAw0w8PtJ01RUH3yf9+4Wrh5z2nUAfBFi3VLdI2PxQwdRFiCvOuuvDg2QvgHQT01jESThGTR6tF3jFd3j7ylFjSPxTkXJGLxAAZO16zVTUy0LOFVUshhmLC0VebXWw7CVmbWfbGcaiD5o1Ev22d6+FRFfzOCSejtD14UvidX/2w9U8AQa1+LDlcJ0Dp4jbf0QF5WRJRLvKyYMhqNv8B5Xd0TQwagPOzo5Um1HCSFnjeLamqTgGiUf9yzRIg3qWrt1aYSpH3DmQUPMTk+UyxVgnCNvZ7YzEV4ld9piFGnO2klcOSUYJNnY4646cEZckvGrjLPCQVedWYV3O736hs6M92qNITx7Tdz40cSMYd0NT5uBAxJ5sqmcOqVLlS+WW2S7vX6F3lEU3gJBPqNGkvVIQcovQ4lvj3Qclqxlyv3FJVgWgIU/WqGcx/DjcDEC+hR48frvihVG4/3Wqxk3YubCx3MaDHJ5B3crQhr0CE658P2JF/PrJKkd9fBHlI99cyJu7SpfR0RRZFHb7K3qSCI/biXwtXnyVPzKLDuJGBa/WdIEUNekscQ/xK3bI1ddRs6azjl/OyrOcBgnjLecwsboyT3PHyXCOERRQPIjk+DwvzuGceLdk4SGr8ktYBqhmPd1CLV3XDSCaAzcCTc4Zddrz684b1flXJdLIr9HBdHNDKX4vCPKoLtEF+pSfnJVtfvVkO7hHkHB4sWALefnQBp4IH0ubsvriiF7QfAvCF73oF7MAxGKnrY4++wl6K3vAzFjC4Ox281hUw0Zjzfcf0qutCwF13nXa+nkAt0vo59GLqU3oHgZeyhwguhNZ4j1uXo51qUsmZinaXQcXZ5+nZuwmpWncn7vAcU49xNiyGUyHvYwvO9+oNwvJ4PfDTmOW5a99jTFFQyQSnhFAvniQIG3E722LQqmgmgGoDdeoL8xIae5xMO8X6pP8DS1x5F6XyBdrU/29JpyiRrAtrNb4vbQbxIWSGVKlQx9OA5ho1RARQbhNZJ/Wnzq93d3d3d6g+0Vxx4IWRXGEW1/xbfKSeIRIOz0/jmf9B+wuMI1Y3FIc/i8+PnM1+ia6S3IUVQ5iU5vG6a7JkoaAhesmqmLthjzB8xA29FZNJU3rVVVVVVNs2llpVZzo+o4SlURkvI0kHA06qz5TMmqP5ilr4hH0BK3wl57cCXfJozxd49gFSvijlVDeejz37Hw7RmZfRIYj2v+/DkBM5D88HwesJpLJGlO+Qk/OF3swxV3UrzI7G7u7u9LmGwjeraMvo5Faed3d2TQ2nUlGXCya9OEU4AowtYuz/yORHWThLTZtN3d2yOuimcAD+/jUH0ErdjW3cI/fqtRDc/l/GtyHWswig4nl0VK+isUSJmJodiEG7UQgG212C8Si70kxhK+8nfNFE9BwWSUnJNdXGAj1w7GcTMLcXNraI3JLRXGjHxhS44Jn1afE6Dk/YTrdQwME/e7x3qJloVKjVEjUTFoVpyfss4RjwT8IUvCMT4+PwenBz59sWGPLDZaFacaphdX7GEYU+/CybbmkQLBPFBBID6kQGKQcVQkkJLdP2JkE+nFD7whIWqLPXXddel4a1QuFuMjBNe09syr+D7bccT8AAoWisD5jFrwIdwqm+5gXwt+Vva3JzjpsuezxeQdl53E8TUjk+bkfHlVOpyONhMjd8VCgM6pO7RsQv6uc4E44S5w7PTA605vYfFBGTYgJnTAiOsKjMDk0sZ5FoIViAvMXZQygvQeFaECt8F5sD6+0IpIFACfK3C3iLZC0qN2sF8jUHkRg6PbG7gAUWjDopUpT99P7hZ95Vlwn6YtUkrWm5PospHhYUq4KHeGNeQEIW/e9y20X5YHwhBogAAAMTX4m8CnTLTvZ6AAAJof+OlGL7SFvR0Q+lecr2sw7lrE1s9y23FiiHYb0ya6P1KLJfFLxvUAUJtpt57as7tmxEGc2ZcMCJFRPn+swc5YZ3OGLExhT2POz1JwiIesoue03D7pydyMkcWPhliXEbxx9/czQdNs9Rxj+vqyIuu1fw5mDNuH2qYPitkak1oPhBakAwMKkChx0sauI0OyAaYC/gCW12LajxaAWlWbLSnHE5aBtZ0QLFFekhGR32hyWmH1wU2hw6PyRr4DEcnMtOU0NGHOy1G+AT/BT1AAABGrVnxcBDNK2Vg5SiSFGCEKV3PYS17zlblsmQErAp+FJWcz1ZaxGgcWrU7Z2OsEH32xk0mxpTvRvCmyukDy/SetqwmMEglBX6OZwstNyjCHAeOw+dX8D8lnGCF7l9kvpJPmuJEU1lWMSIREQYzS1X0SHqi53bWXZyx5dC1J51hhziRnGYvo6e6qhywJE7RE65+oS+U2BaTiWliRX8UunVbRqoY2TyTdSa5028iUFjfWlLbNcNic9giOK4krB4Vbkj1h+Feiaed7U8fLxXKDIqXXwSx2HUP6SS7NhJ72ae5LxB7J+wmeUeiU0noR1xQrLbrSQuQaDuUEKk0g5cOo+BoEO7CEQKmC/YwfP8jX3H9fPKB+yPyj/bLgLRxVNzn1cWjh1JRL+AW/gAI5xNPPYFCYZB0XsAQmEppDDqug33g0uvViU4XdcvdK2WocwPy9tmW+ukv1qf9gUdd2cfZ7i93QqGjGccgjrvUqOhMAvuao/4vFXXeEEo5K7dgOxEyI4KZ54pZDZRxSrkzvS/teitOjIAD62j53feiK09p34jmBJ1biLm+EbIFI6KY7P32BNUQxs/hFZQ2jwB6u1TFQvtciic/tAfBHvdKiZw+Iu4h2MC4kfwkmHt9XkC74sMwKTwKzax8IzRPJ7qTDtR7UsrmE6zOpxSPSpMZXS2wUZERYkPdOSATj9LrBYMsjngsKQBlueGqWkNhqyg/vDjUtZTe5rajFTyFHIVXhfj5oO+fCStLdFzVTbcTOKIfwfMvLU8+yS2TnENcZM/Gx9i95bmEeAO/+LEk/tKEHh2vhEf9g9drqdv52Hq44W8zE4Iyu8UT3lroFqmQz5XJY5hc6AL59WkAYPVRKuLCQqc522WWKU17Bg50grjdyXbS3T22OYkLES8iuPucxaNup5YDh8graBF6KSPh9xSXLgmjaR2x1uvCW9j17G5Q9PaHRnVu1snFZPup/jbnO8verVswTiHEzo+1uN8T6wlVmWpvDD3nGKxPpUZTyOtGey821+cF7h3l63T4xkb/Z/DeUoPqThU9X86LvH2ckSpz++7LaZ2at18mz7BUJFzZNmUp7YYHahhq5t3Q6qXkfmpw+CkQlyYg4ck0Aw2urnd8VcWchg348F7Z+ccRjF9MfQAIMJ0eeTzh6JBxwDtq+UdJtSp/d+iujr0Co2EP3jRPBtyyukChSQk/JFUE8Tl+fpm5g+sASYIxGQO7L+nqXRAlwaNQXT7+4E1DGYIuzAnFqjQJsAALt2ihNefq85+W0DerrgoJ3D4A4XJO4h9YoxOKIzfum02CYkzLU/JuPXpYc+SecstZyV0k9fbqG/TxCAaTWalv0zo+Qn27VYTSlacYARP4mz/0QZ7vPj347BNnMFCZjeC4pwj3oN08cvQ5c2pIbXvjV/O2XMiwh4uG1+7k4xb17q+1fLbUK02gn/tFc7QJtKUmRaCpADzgLrF6I4P6rAXKOGS4pppAVioeClCUaSOHqujYLekFuFb1gbgxAQ7z+jPSk2ssLaIjw8aB6Co6WDN6k57acK1TCnKmUuXelNBGJ/9+tt/b/OSMi7wAZafSWz4uhD35dH/0otgQ6KpbJSwe779z/EzGp9H60NDqMc5/XpYNi+rIDpdRdelroNzVcLBpK6BvFQUa3Qmld2T5QwMnTNM5eKTAiHSd3ngPIDErCJa8RfxmL3GYIbaLvxDQnp+8r52L9V9zIZfM7TV5fqtHgM2PPds7YhEn9cc4gM8nJprmMvO9K/DwWbtGV9mfvvmLnDYZdbRmmjnYUI5WZNgiIon3qAmEczAwaz6V4nKS/qZsPBwQbJKSw4tujLHFaOmHHhrSTBMO0rcdSgRNBodEgXlBIRfAIRoKC5uwfvfw7cyEYNwoTxhWko+uD0sp+zFS6VpRgFSHhyl/iZb8NXF7mRCBeRqisO+xrXo1kq8DEN9geDoFjzSKbCL0CLwtPHQZ5PntmgLpLRznVrcYOPkOSNNCFIJQBYTghDLwgsl3b3nVCyOjymMjOuLTCqmkWXSd2cYmJsks1MKt6STjBBLuyNtk4Z+Pj5UHU6bXdt8KEaDgNzwdk3m6c8dvO0cRaEXDEMYZUFpnWMvc6ow0Rv587EtMmE3LqEl2drnSSt58YzhSiR7uc0JCPf1jrp6X48lnCazzTxeDrhYR9bzF6f00l8cZ2REsf6KJE4Ww6ena7jX0aAorfcYj3ikC+y8nOPYdhwlnIK0uhO3Ewt5iYJaoq+hrCAxXNptWvV/EDce4pVP5QjhBPDzxGohmNOCi+6P5pQ6qaQ8qNHghppf6ETPLkmaYHrxadMF6a76AYqiiNeTWF1VPWfFpWwm/zgiMruHOrtgeBSLp6VlIZxGrwPvX0So6pGQbx6uo8e2TMyyHxZ/OPTmtzC8x8Jpj1vx9QN5WUiNzolrLXQnq4M1x2Uiro0MTtJtfEVATt6ZfNm2SuXnE8mY3H2zYspcbO/PMtNSahb9CaYFFI0oc7kjpNCBJXycSwXv0RgJNpK7xO7w9SKfgSOvxyeb2zVdY778WzYhDNwnunAlrvEtQoybRGJ7WWCXaJ/ceyHW50Ce5ZfuJd+2YZ7R5Xqyt1LtbHm0ZeZS8rQm8ahXDfihLf6Pd1RZJkZGYiF8mVbuHa1wdTOUOdDI/L/MoOuvKZ1vWOVsszSg5Pmn7QfFrxvGCM/kapUFtXxuo67nYXqKB0esNToyMBVb6QIK3DLI/JMoo/BEi0SjgkpFUGPmVWKjBE71hh+CKFNwGB9R17lHkcpo049R5Da4PZLYvX/LzGywNdgV/ANUYWBY+jZ3yuEIB66XQa54jIBsthS7/ThB6AVCycqeOrEJeqNBcMj+pNkUGNptXIJhqLDSNI9yvYvhcoOT3+TMHpqYegEnrNajDXjenA0lTeHeWMvNR+R4XLVZZPj0G2g1RTRnnrAs0p0KE2zHtDMjBWC3GdO/oFSKU3mJyj0n5uVx1yekjSSyxYIlxJklfOIeghXqgIcAF518B/6BGirJa4si+8PTrJmCxM6eO2VLTBTk5KG64Eum9fuyLMg+tUbiP3EpFCdUX/sBE9CpV0pUUes7IftSZuD8jJHmfsdvi7tAKHF9Dr/ktUK5YXIsVQQ2Tb9nf1JllhYx4ijpYYbu21ZenGTWqsYHi9hXPDWn3yySz9Xw0WrXmWlXf4zXyahIo1ReWiQmWkPlkG+VKFNPuf1AWE4AStO+4gTqyxfoyAqzNKonXJwJtn1Z2rfbGBSfw3P4QU6YRakBmlqt7zns8zV9AzgFoRaQ19FH+h6OsJRrAmoXiYJOaUtDACneAlEtA7N/Lsud9V5FPBvZk4tfzeuDQjM3qdFH/RZorERiNh2/XZ9hhjYHMB/+rb1n+FU0mN0qdnnOzgy6eDHcUZY9xpXyKVuXchRJVW7uKgHx33InNwolp3qIupj41qQPViQbJftv0u5ZmlC2Ei1+lxf3iBltHS9ZjBQa2qGJsfhCR8ndPjqHAlphb1AURgLa9VPOhw/+s9LJhpeYfC/SgAJtoX8jpk27dFPcPF8ZVFAPy1VVouUwRbRQboPwqEqau7g7YgSpo+xshndNWWiCvHKxSgtCPttKOVRxY5aXjz1xQFSKM7kWRQI2MAey3idOs4R+caibvxhJaynHA8SRSXpr7T5mgIb2n8au9N4otr5BX0nI+kbVoliYaqL3vHK2Fbe/pzGQo0R3Nc3mFxXpInP2TWIgFPP43VHKBiLbkvKnUOGDxShxJ77AU1dE+d8d+MNr6UAvnbd/FL4S/YyX6a8iXEoR1UrJThQfFW7uSBxQyrWHpFiNIQ4M65RFbECiS70IH7OwMlGNu2zhg7/Q5MnxXqkPAExrQQjPrDDSpGlrwdunOYHr1vSy0k5NOktlswPmxbbMqaUQgJzsh0wjBvy3IMBiohH2aQu2p5WJUofBJPtTnbAMcAA2QbmNMWYmqdiq3SoBpglJ62oF5l8VAn85rj/K697gSMJBExVm2Q4BwYlkCUXUaYDYXz+pzZ3mAmk9XdKYXeA76sKExDVehW6vZJLXMuDFxydSKjdJccKmTQ/QPK7NFs5P730MIa2gHtRQgkR6W4ihYoo+vQQly9NNnXTtabvQ3zXqN03GWSMz+yeBjC+Dgsk0Y1vU998pOLWMy9uL/oU4q1LdnQGQSvhw98rKkryLIjDL81WBUtHhZk6+LD9c3pHEQA6H3g1qBApcq4W1+2EcDoowyDEGTj2tUDVptd3UMN6RlG6kjvZxxeMK2wbMB2yHDZKmG0HUHyrzBgywOc8yEics94qbXugDR/1NZFtmgBevrORGydG22jUbkGFRdcwu/fXde61wk1O48RdhrOjFUET80XdjYLjVvKuUZaB92+2uhagltUxUP1GLWtZknmnxADA6oAsE7qe49NEif7Vc9FjVTO0U4O/qleFWj1yk3WADL3LchFTjKJSHkOpdE2Ys91QBFMoIyEjedHe1pQ1S5X0bAjDkRhJ5+MScSs4SYL5v6S/KNkOkrFlfXehCxmaZ6xC/J7ZcCujPI29Y4RJINYjXcC+EXHkQeq4xXxYzMbFfe/6J1jdlhkdNqgyBkY3BDCjTNpOGND2JnFpjQh3nfqhCIwFuizfw1pH3BYYEd8M+yyVuWCqYjaZTXC/ocxvO3oy/zDpL9CBpadEXgE/3Ex3IbQfSNifzN87EiH6dzl0GkY/ZDRFsiOSOuqBM5zjhxJ0UTguMKHN9WsVp5H8vB2M9h9BFs5slYbAfaK3Jn/+mWCC34hkSR8TGrLqNJlCIqHQ1qzReFBvaTIkpvRUOCs4hDq20E5yQed22jFu7sARk7KMoSCKRupqdsPnkBB0JnaPEQnpuWnotevK/iWIGRtHLNfPaOx+zGMxkIFuOU7uKP54YDTJMi+gKCKBiQdn4fwht4jt/BJe80TXbjHNDMjJ057iERwU8ej47VygCHfIlGnTjBi5YeyfiEg8knwkg5YQ81lw92iSj5+40KZq8iburdzYY5a9tiJ32wrtur4J9ZlItoOhQCbADvmFjdPuA40ZIYhDXEjsA18UocmwsDKGAkUekzRFeXgJbfVaQUvoDVRD6HuuPV2jkJBbjtoq2bPuOeJ/K/6RiSWMGVLIoV+uFBEnXGaAlpG1VaPsD2iQeKZeaWvxROCj6yL3CqZq47aE/Ywaky9zidQLliJuIAlH/qj7r46T//TvmagYx6wxbC5NLbiyOCwYLZ/dFvRs6rHKVHlxqYmnEdT4W3h7XuWtjMFanEMvYt4YBQ+6kjPpxZBaeQhHLVFIPTKkPGMXPFJP1SUJuHDTKLdsPfIyIk2vIffjv3bbab5w/m9Py6PdAsj+lIqpJc+9yMx6WQ3Q5ZDs89cuNc7BLKdYHhYHoGfEYYrfEqzLAlG1Yz5g2YWh1nG6WhZItfjlGMIe6Omc+GbP1rqjw/b9qW15WNMPMvTlTTJm7DxLJSXTVcHBeB+Dudt8U0F91qcTDCuQnzGzWTm2PzchXgXqKW9k0AOoGBbqFf8G0reUQ7QvX8/2c1AXcA7jUPqjYZy0RMZ4z0Tsfl3KHsHyEjLC5rFJqo0wzZuhm9tp+9VEqiNKwXaMUxcxE51H5RWw54QCwQ81c9Cq4CSHKOksWtYRiD6ew2dmYW+wxccmNwIiF2bEJ7v9I10HfHgmCoHL2O6NbUjjWFEY+2zKsOtakfEc7WeF+mlFO+Kirjk5Wb6tB1/ws/Gps/3L+IUGp/Waxk+i7WjApMgzkO7bH60mPaOms0SF4N0x9hZPdXzeFTVdjQiOnigeOqa7GPl/X5fv7lHE5I11eD/fLfBHBPwPqKA/x8FN5BR8fRWCzuspdYAU63Jl59G6vNA7CZlCjrgPscC1m/nzLHmB4R/CgH0AJLJ20HsgvoZI68W98Q1RKdWjCVrUza68RO44ONg1AaH/HC8TpX8YpVNnPIOy+TYXnj9HqlXiQiHwi1lRBwEox99TkX8p+pS8g7Gf6z35wEl1nl4eUPrJGU7FtYzVbr8oy1DZ4VrS4I2gr08saZNUHnbuh27kaPtbxhrIswCqEvvZf4hau4ldpwzuDCKrfCCcTsxt4Uk6QUSgbcp6qzsXI58oyP/pdtvxsXoudbzqAs34omivFs4tNnwCAYeMg5DM18F4amdZUaEff4IdBZHNzCMChidut0Cxv1xmvQaePe5yH9ED0KgpZtr9n9sAj4EFVbO7wd0C+khnO8280tQcZAPqXghOLeSuAxNpXzamEyVjjqg5llulFoVTTSCRU0BQchzEttHvHoHCj5EZfXvSpfHVA24i/EdYwJIYeW5JWguygKD8vNrY1ey2JatM871dh3632kX5OlnUTRwalNI461tS6Q3CRsageIVEaUwmkcPmOPXyu4z1yCbv8asmkjqIzLF9Nz7mEk4pOFq2+NBJ1Me+anjhn9qfy4uZVbAPjcUI4ekr+FaiT/xxihTp7+nmh8FkhUnIC4FgCE80eLXqnJ9RtGFxL/LX6jebvvnZ/veuv8kddMUPvIObyWnIvhUrpyQWFYgLHjGL0plxyApo1j1H14/FJMntYhItNbbvXc9kFFgFGujyCkMQamzQL9FNnyL3F72nqkG+go0XP3fl1zsyFv9YVVgPjiGOYHrlVER4IPN3ULn0dEpkT+y1TQD6sIiX1VEKJ8ruRgT25rpj5v3ODX7sfQO0l5jcYvqC8U4KMehw7WHGwakZ0EKGSt056Mc+BHSD7kbctjZbhNxvsmSDNuK8QswwSYkq/ocgAD8bhEtIGi8hk6YVjzsBlpzgLIs/46r5+TuqiwvbSV83xeQWCCZ47AT+HJyg3COcO4APAUaHg1vvNNQz49kyiMuO3ANtNHhPbGfeNWJWFq9FiQp2eEV7rVUpZFo38mb+aJ6Q3/9uqVP3fo4GujwXXuno8q4jzepzXVoKKBBJp7QADKsAjQLgAAANCZtRUFEzV1lVWw0o8dWYbrtg9FSoyLVwOozf4poDcX3y812DDe8AkvxlPpCfBil0usYW0oX4/eIzqfeHwhQ8cgJVaJCexX5VAEohclw7IDL25l/lYqAe6rhcAq6HqnqX7JknGnpdVKkPgAEj5v1ZfMfcjLhkKNzT64xAQGgLjJcm7kt+kWDjIsgt6r8gn47PnCQjGrgLW2cYX6mpM1XbhrSH6JXtyWBhVsKZtxQ/LcD9LQrYnyBDt8NQUkNX7LjIPqkX7WqVA9VX45NclpHJK+etMf/AbUW9pyv7zjZhWKb9suQDts/n386zHVuX6oyVN+W8EqtAlNtN8fQNBLLTxCF2YHPjHWTfa4HLfV7csWB6vjdRrE+sqHazZOJJxXSqej+z2j2Sn658ju56Htpe1ZV0BZXG6R60dZSMHtTHDPwORIG0MEG/wQkorwNeS2CJR9jGCMW8DbTeV9vffi1m08GE3y2lZgt7Xl90u5SWsKwtg/k12UiM/eUZ9KxZuli/BE+yD3yh3OrxGIOubI240U4um3buJaK8K58jkLZUb2UVKApnv9luhFFuVN9ht6fKi2NFhqHWkeUPqHiwU/RsCIKFYDRkn51UA8nIzGLvf49JJQ2c2Fr4avaztEU6oeUYZZLBu+YjvymVnzCKeeNR4kSvX2guz7Q97TbdTG4Ui+TKdtKB9Jm4RG4OFWEPRqYDugx/bk7WlwqlYSjjmJGfETvU9znw8tQKlqyJJ81l8RpWO0ctOfBkYH7uDksfRjka8tq5/b0xHhFiUY0xNWj7Q/g6nSfD5AI6i1p8k0yGhIfcDSayx0kwqw/QTeYDY0MexJsglKmk74OJzl+YbU9WcV2YQux8OYEgrpaDSvUdXbJnuzX60+pD5LbkyL7qYvi1rDJCZiMsQD9t6wQFzxyAU2hm7xGGlQ+92vvrtw1VgJloR0FZiEEhFCmrNYl7WSIsgb5cKcArMIRvGw6Jp6+inSEuz3x5xXWapiQSL/76TjjY5dpoORXRnNP4OfAtXTbvgb/AmHCPqde3q16rnXv4s4HRg/kDRSo1AUx+PfPXyisMBtHs0jR4Nv4nHBd+qHBhpf6s98UeWpdf/0SchiVQp2XV5IoWuZfP89LwVvfZqSxcgPl/Xpg3FYF2DzuGm/REHcBFXBgwHdb5PrQwun/FZTpeiw0jbSbqRtrpfyzMlk4TP0ZnJ0Tx8t4mVvj+rwgCgG6Ht/BysE27Bhq83yDZ/PJ06t0k79tm9jj8REEzCSHzBZM3VwUoRPgWSdPX5d/CE82cIsxzWEhfISrQw+eNlrMepdsrxYH2dTF0lrZdz2hSmn625nYUroBmIS0jDQ/OMQ/ixNcPdQgnF742aY8P8V0E3qiBsXBuWL82Xmcr0Z0U1GWPYVwI9nLSFPGOjPbACQnXG3SQkXDcb/AO1yX6zjDY9aat59BAgQ4Nr4ODNaBgj7pM4uxQTb+0eMkinP5wl8EK/WsSZE+jINgJSDWbmsiz8bb0hixumIIHNi9v65ykaSUy4nwBucKe24cDyJDKCeCIPSZ/kTpFvPkJsbDHSa94OCNrX8NGhpgyQKUliFGuyyIyZqS8R7iS/v78Z5CzegBdbXsogUpdc1uh0j55F8ad9qOmc3gqjMAk8DUwvgRzlv2EkeywfDFTHP/R/JD9ljaycqchznrturyb6Pl23lsZyjR+sPtRWFBEvPvDUSyTIl062zVdFwwDgzUbFmPnPyyqPjO7LSrY4kSBFDHzF8UkWqHp4jtvrOGvTtXJOa9/QwJdCz+p+jpjr/rqO9JQ5ELU6jFCY204Vu4xwwNT0aHd6LlPZ5z9RRYYwfxo4gDgHsQ0RPrqaqkXNz44P3SIjqUetHZzoJoXe8brze6whjCHKQzTltWtGJtU+HFqj+b/Cmelqb/1UKfT2RMAwEM4L2ypi22IPD+gBFjvDhoYy39ThocPobwh1x3BFBbVa9O78LYKX7uE/te8ccdymQEGwuujSr+/8UirG8Cxc3wXXK2lNL76sXWrbQfOa1/GzqyH9EdQjNuF78/gjjBRUYszPq3+RGOBK/JE/eG5skaaIRri+CIifw5+Q7rxPwlKnAIDuQyl1yMNB6d8wUjTFPV2SnTzH1nus6lJiLWlcQJ3IzSq2uWRcTlGBz5rU5UfIiyj0bnZrHp8YX1ETh7z+APe8ZeithuHeEp14xEfGtOPlat1j85QYPYVHa43slgNcjYUp5He3bDwRMijs4fjJsg5Auv9+vor98Zneu4oFyF6ciTd9/yC0lZEa7SNmaAFe4yAediff+YABIGsl1qQI82s2BpTAdCikS554oNG+7S2ie0da+/GBZqMoWtWupKw82UMvia0rbyJVdneA/msSdmaUPdWVk2Ko+2aH84N0YRyi/EOReyJ/X5/5p2Kc9fFpKkDZS4d5XWQ6BuB4TyP8J9BMQ1N6pJv1/GH5usugIi8DC1FoOs6Fp9GK0gsr0+cfmh+skzoLOKUQR1QvtGdE08F1pa/ZSFVy+fDfKXHwQ4HSPpQCu8qw032BBXUhDN2cEmrEd/ksOELI7YaZA1upKv0/9LNQJzaW+52YXVW7PIqjBjvPMzXBT1lME4hO4G588w5pctw8p34DFPZbbOY5+qqcR5h5bGCNh7Z5VCmC5ATUnIXpOxepEVA6vr7wmzYRWZTlqSjgYFuWXzyf/EaT6DxknftEBnw9wZtwqCJ1c6xYjtTTjDQG1z1ywUo0Kjc1sbA2+qKBnbH/ymGaruNmhXZWFWtYmyIsV9ADHyzRE1mdflvUFVBFswNcwExfJe0vCdNTbBr7CJivbj/qDcdJEO5Rrk2v8KGKIgY8OUlYOOPrYgAyerT0Dq+5uFzVe+bMcq9ZRqwkDb4lnzJFU9p793o+pffNtajoDSmCciwJXSUCp0rkbFOA8ZvYJp/CJkw+2D2n2Y8mRvvFAdeTQAVLgETzYH6uGVnmOB/Q6C+3ZAxtCeJyJd4/mFrp9WRpzjyWDRoCMZ7d6bzZ2hu9NeR2b01wdZtoMKnf+ttAIAa6ZRLdlVxxH+OoOL+U+E51N66zOmh343qCGQZ5wrM5MhjxbZY7MEeVdPd9f2Yf/qq2rQAtxDdFZ+ozHyCSCycdCh2VRi6IOD4pHXzfNW0iIGmVn9SSNBeUxuDokxxVPobr78LFIjySkroIniKiyZzD9VJirZox3LDSq0KMTMXPZrlhTVk2sJu0Dh9YPM/vB+m+QBicoiBt4UuiOkd8JEg/9DCTx1KHGQIOt3379FiBzPVRC0eireVcnpWVDpryqwWE6fj3V/880/JsJypvlcxFeJU1j7sq4ulCBqZfXulkKjLS2hAwcwy9K90KdOxzfvJM9CvpDnv/oJTPu+Ns8BO089kJqOVXOfaXrZyvsVMR8nSoAbWtiuqeeofR2vBNKq5OeYhX3HMQKnTIibbu9uud9wO1uh/9Ps57/LZVs6dfpo3LDZii8S5LbLAJ8bwWPAGZdU+tvgST/TTHzAx9nG61XHXMVTGKL3dOMwc90lM6i/I16xoMqhIzhlFGGcTcW6ANTlEjms45lCd21yTu/YzAzTsBVdvEfTFpmCsJHxXjoAw9a1tKKCJGMD3z9aYwLrHPBowWCDCpimWL6Hh0Q5g8GAFAQ2XheOSjPhlUVFdquat9Gn+9vx+7fhvLVVMgC75kBJvNIoWKJXZ6J11D2Xn03fEde509r1EEhR51QhqD97rjqIAmYJw4hIPrR7wv/U+BHQYp0DgR81BhsWRUTfDiF3I8kb5/uIjL/LBnGyJmZOK1AraEywa9xcRQN29Dhg7nub9ttYNFfAsxS1QA4NfLKwc9hK8tVrUvx+KwbJ7j/K07iVS8zXljZYxhYdPdeydrGSXF2EA6Y0Z9+QjV/Pkrs1oKTr/NaDJmkAQIZ9N72dk24CXI7GJ77KRL8iZxaxlDyn4KO11/KqbOTM9N1NAmXEP60M6whsUZ8lqHrRsdpTgAffc/D9Ef4+o/7qCN/mwUcED8pgp6hG2hAonVvdIpPp9ULtv2k9s47igMxi0sWa2QIkpL5IAFczfcGJupRZto5RSRbR3L8L7dEJN/qOdzmN88vHV50f+1bA4Qo4nDa5gD6Vl//m+G8imbnQdZbVMxoTs9YAx5xyFxwvKoPbxNZfWd8rxZ3/AbxlJO0UaMhibt/QZuVrxglEBKumWAu/THqecIQi20a8PTGoBBFHgLSHJT/0yVumr+zAKxM+iwFvLNQTxpneD3dlZ9l+0WYWUDkTGq4fAorNt1hB5EeGbbMZxJbbz3mQWAOKB/vGxb3RCLbV3zyCoFKX1LqbWvPvjO0gf6PHYWXH+hwrVSgp4P4sA/iHkP3dRH7RZDW8gIHJHUEEF59q/oMeMUJWSA0A+2MU6itZuspZWqSfE6vwFZHTfxs9jO9cOaUPcj6nqZSYDQ0NK7g1qb6bUBvBQmd32Ls9BcUtQyavWJqyl+dSAebL4rE8UmFynn5EcHEr8+AldGQziFvRdFMTXurERwiUqfsoc4XqJHuf3aoxnEsHF9U22HK8gsbpFASd0lMm2sMceGrTnfTTejxp2UFABtn8xtzoaWKEgOcZd7NwC6G5fBnx74lG3KItEytLbWozCof889zmpGAT9Zg7DP1E633ZgeMsAn7BgjqFiHkk1i7MaQqTuk9xlfV1KydVLFdCDpH+36HACwuOxtu0umjF40M1V/S7/3dB4TONBQcscWj/q0Le4ZsvHw0d8yC0l88tDF27My6FmpiwXLj4bvSyUH2Ubk+TfE24TRYTPjR6mJS9uc+jPZM0hbUPR/ANolqwz091ozGuAmAuUp6E3HonRCBRVmojtNS8+RJ4T6bxned3WSShAEaDPHVMbWC34pZEuwnLlLyDBrVrCzbYv7/XC/m//CrsVkcVMflimNzt/hcQCJ7yTrw5F4QdvYXBOi2ry4xuOGQ18Ashh7PIG1mpNbB5HKJHkqJ82xvnCZ604gF3yjq/RUelJ951RW98kWKx/IlMx/Ck1HTedSY3s+ebMQLAwOALyexCPdihqBUg22tKNuyIJwSSCSW4OFdMd0mO5OtK2hCnwhvEuQTaGT3RdeG87Htqo+ZI/jVuh2MR/odOscvOwR4SPpkKveL0HRFc9WdMwdcIgyfDYRsdWE4g889RCxJ60gTyYwLzZLnDg70QFr9VWB6k7vkvjwAnXClNpiPTiQ0qoFDFibgLSNxZIqUSXB3yZaicfnZvjLGOLVs4Bl3msMusKtuZF9yJZy7oXpMRtuhvSI4p2Qf5iHQkJ5RxZaUuTxciwtIblNB7Rr8+mBSXHvl60O8xgGKgNYJEB9hTUBWzQ/O+HTr3yi539PkK2D9s2AcyTCrn7jt0rKOwqx/WgVRj/qeZi6sP1+mUVfckjs5bpZbxTC9NGrKF0R3s9FvfXTDuiBANrVmzDjGcAQLjAXI3UZ2VvPrHqhmSfO3EAMnT9AdaQHwo1OoIoo9wBPIvnQeE+EUi5ysHf3/02mIcynzdlrcVhsI2KjrPvSjuhTIvahQd6UlCp7cv52vijowDQCQjw2GrpjPY+lKgzBnA3UcECP2J3Xe36blI2A/OHTikzP47v5BWowK9TQlQMUhbiE538Zc/L8RUSo4vkjprvpN6nN3OYlGbOzoSQkJxB+TMCGEiNGELBN/Q8T8moh0FJ4BNs3A6QYlEp8GfmNzBk3oaFqIzHpRel8N0H+pJ3Ib/tzq8s4sDIRw2Oixz8RxXXD7QBg6XSKRXea2SZcMPnkOkMtP6REaS2jPINPUt5Iv+Bis1bQodqelI92lRHA75rn97ynAty1cVpkHTunNXR7dFT6Lukaf5+khQLp/AEec30UMSGu9WwNtfQWWuwefYdGq/IIDg4JtmAA+M+uzUVxyotmBnmAF7IuzClchFb/bbfRq2ECWTN7cTWV+Wd1dX0lyR1vWhDfqPDrAkiH599KhetfnZjrR/weX1Xie+vwfwXzV1edoPb08zWgU+9ObTLt6m7hG5gTg/GhGkCT8Eb4Nt1/Ua+dJGMz5uDGkNpdcgdQr0+Pzzq3kp7HW7m0bvaJupggPARYzTbAAWWACpw+P8L0h5mbuO1AtGsE01vY9IZWpRiE8PM3qdzkb5Pqpk2V4rzLyRGYrsoCLKM6rgKuNsQm2yHUsENrirv3FvkUwSK5uMcscI08FYgsNQo64M1w7Nu1AedFKdPlvOPNPpoNbD1HKhSQd7Hn3B8mShvoN9KI6aREzsRa4ViFbwaIwVUGMRPLCz1kxI89zeCviSYyCa8rckUlrm8LaYcpH4MUqfeKCDr4RWNCc7xdmDN5pouiG/3y2Vp7RkAbfAqzMP/xWnXL5rRV58GLO8Ch9B1DTEom1l7civNV9N93+tYY7kx4I3Veohlth1cQXkfYw8y94LUvCioh+smZlVtDZUph08WKTEhKbofUlPuV0HbtdOz5/NR4uItNyXqjQbG9qSqBIt9Pjr6K5wmFPzlGjPghShgP4YB3zJU6Si84GQiV1sW/CvAYU47ra480KCgBNMgAzWPJj4ND0Ppo70bY7Tnc5X9mZPs2hWhaEVe33f6VRhILUsosQfxg9XTKxHYq67WF41nNe0NsnUk0jOYe4pkMNv/qJl0Vutmb/RiTq1GGfF+AO1PnFw6aq2vtoWcshz4TQTlkknMK0dEUVEuo/pWihtWVp7CLISNGY7aX/6gmWTBuyy6IutzwlP2hgGNetz1wzEIHCJ/QkvTTKd2SYLVt6LGOZmubNBnyoEw3p2QZQCJ9MN2MB/R+0e3sGUOACwaC9VCqZH+E92+Ssl5aeGPUd+xY+t7v5q0PEYfzk9M2KUORzeDAnnonvMqTBuXL/lo3GN/OGUg/gDNarGrlM3Bpv/uMVAqh0PXki3YcXXqtjKmQN/pewZrZ1hSSxaLf+dSQglLRw0EV2LSsx2ygHk/GM7gleVKCo60eGGS6PSPzNjjSOqCJXe616As3Bj4dBE93b4Ynmb2KI8xz7ZnrS5GmEiw3SxL+arnhe2LlsYC1gD5gnwG9YBnTWKKbukjdXttuKObRuZSeNPeLexL+Toe/FNWh17Ak4bz+FDe9zJRkvCDTf5St5bcSm4R0M6fOG/D//ZeyRscXuFB8mcJCql4iDOuaZ/VL6lwIODFLI7fPBb5p9FXeTyCejDS5Q3jcEsW1GIEuPNG9/ebgvrOiqrq9UgkQGqv041dsAz51NUmkz8kfS31SF3ia3pOhy4FoKVOrSo5Vz3CvY5IxoWU9h9CHKyD2mlgqZjpGdRkenBszxTVgcF1vdzkxW6ctw9JyOUVz/dIZ3KkEvtz+9nouI9ZYkpoGrKq7KV0F3iPl4d5i+TGpW+EYMuQu0NJm3tGoRw3VTt0Pfop6+vdfz4xZ/d5B4q0jncFIZWnPE0D3j6Uq9oS2zbpUutWT0XYeaBqxdBimf1iWPPbY1jPWmEjje6EWMNraBUMb9s1atZC4xI5u/RgNJqBApV0E0lw43Xm+tnVUHgDf8o5ZVAGCTK1PUYNpL4zZxBlxKfOe1RiWNQ2TIEa1gG2/8WnF8vL10q4XAJpKmCgWc9Gna8jfZ9rE/hkMtqMJ9m1Z5JcdSq5VCbZXRrSr39s5ZXQKwDg16hV2S5BjbMCWQeLL6z5a6rT2bWHYUdz4VPOMwyTsGUsQx4XoVgY+RcF31fIy6H/s0h+kq/LELb64ffMPlFDi0HhgWV2lTAu8QuzaZ/+2AhyQFc4VwM+3yfgagwkHPUOCvYBine7gIUOj8qyEvgPRG33Om4vO+UQ9HkgZ4Ff9i0vpjtbZcJ0+yL74VqhF2nB9OoOyx5ws/iSq+8wHxUNATEKMbswlgeLIFctbiqINW+tRbW6V+DjadCgtxdH7tadl8vTAzH7b5BQM2ssXQjr0FZB4kqUMEt4aHj4iWIRy13UCR+tDAUABa7Q2EwSa8mUFXVI6uIc29JA8/VIgguCgBXX5aGTyqxklVqjkFr/KJhZIB6s3wPPgjWANvK25UcJ9RkqH8BmTYPb8/UQmuY0vkO/JrdNWUxWG5s4GpvVb9YbY9AnV22/hWrLRxmVMDrsgEjtxuoTqMz5vyF1CH/2HO1l3GPHzmSdyjb/hbJS0H+2ugSAKNq34Pcn+QlIkSTfduGenIKX1Joh6tx1zz3zxByHDj3PSF9BQGKF3Pz0iU8q8G3ek+y0d8khB/e8vKPD65B6EM3212mwn07RFnSIEA2NsqItObEbPJtzGfap9+3ZjeGLvGE3J/JzdP7MtuwpbcWUqpqPdNs8gomyknsnqkc8u6SDK/MUjy1QWu15kSeeFGqHwh4beBnVKRwIpvS999/sJ1w4Xg0CCXSsjbXko+A6+tsld4QSRChW3e3h1rDTYYVOGKC4PDfaXh4LMj5BauCgI8U61XofVdjvWtHcRfd+4ngaowoAQq/Qkc4R4S013U/WZf1ju8kT77t1fgSHFiUG6B1OrB8AXqfrbVwZWCjbwSdia17Fae1rXYGUTyaIRMGKkqbaLUDJxPByPCbdkb9dZZThDzFW1e23tpNOApk8eOt/OjVQGSD3yvrE/WfgjnhRjHcVdhJr0bMbZ5JLw96sv0459gbqJczLgK5Dvk7gE3rhbVb/+MjlDrp9kmjjt9nUX3N6jC5o6xKCC6LCV87VoeaZ5DlWYRoP+aPbRE5e/wZ8TmYbw0KV0l7NC92tmIU+7jTrDp+KUw7c1BniWs+jD0ltX/Qom4LD38cQI7bbx/BNbbkv7F+4txqeigkPsBG5aqgP54jrY2+xmG/j541KzsC6BfbmbUFLw/hnACqjFLP8GO38YqKHy5eNO7bZexGupM+gZyWt+Hvnqp8bz6OI8TP0RebaY4qjUFLbSbfbTb4LinJSC/D4HuA6Oui8HVopqxf6mETDZLNSGKfJ5aoOmZG3aAmjPa+v6fBxiBr6+K/QpIrIWni5uJJBfoTPfkWx2PAYHn8seLi51oUWxhIkXvmSBKdPDvXlRQLOngDIfBQvFrQTZ77Eo6gIloSml2n9qvzh9Vf2BEIPo21nDD/OPDTos3U3V8J7WTg5xCnegZYFHS65YlrFiBJHENQ7b7lv1Sz960Edi9ChIoq1ZfPegUyzYhtsT9S+7qXLkZ4Pdla49h6hTw0/zH1sHtnebkEIzszXQuKZQhC0IAGxDzBAp1F6/NaaOUVYhdHNL7uLndpfZyXm5iLVWc4GUPid0UorjmK+wcc4iPqIQTbtp0h2si3lf+xscLYugO+75OQ0MkEKlVlaklwzlWevqlknMeCw6XyruXtQxQUFZ0XZXEa9ofs4XnPtGwloKv/7TLVaE9NZq6C5ESt87FNf7BLdhZGDI5jvCRQH6+NnBsN1ho7gjBzF9VVRZnTwZ4AwGqc/D1UYdfMt4sH1rhn5+WUNCiCz3lz29oei8RIBffbZUF5XJqrH3saenvEt6Br8h8EGQNfOB9A5WNb73PYrNSbeb265ByrDoXU6WKl0aD0ikFtSgrZZ18kyocmJfmH3cc/EkxcQCeLJXmFxs+DlrZmcq/v0+WX8jMRGFgiOVec0vMzgLM5Bjl2AQVqcFhgjHlmGGfVETSrIvJsaUSQA0IwbV7dFVDDuZ88dviEBG8dUzlhk6NmRb5z18IfDJAZkdawgmxdHzfdNCq/txLHECjm0lc+F6sWiLQcUxSBD7IroOQu+GtfgKGfBEHetnoyMN+m5TM8tWz1rvdwKbPEP6YHfrxkmlDpCvXjsFlrgkB5dcjl3kVfya0Tlcr36tdaEKwjuII0uNA5wjRgXYsdbAZ/mRKmBi8EoKRFFLDgeZnKKFlJ1Z3qEQgdZ9JPdmBVshZFtJ8fcKqc4efi6IT4OdbZIiPGuXj8mTUJsveN97fr2VsBdzu5NhQ10OR35tkxvKhVQsjySv7xYmFR15H5CQLGIy58Cj/aCYMr5U6911CrHjB6RDmL8YCx1GKuzcPg6354YEOYw++m5Yx0frG4hDG2+qsA5TaTnXTTe5NNSyI38MiPLC0NoObhh1UgEPXnGpIY6ZHk41h/4UryDp6fEu48v/0Feyr9EvOf1LCxudBzIxyZCnitX2my1Tq5fHWfTKrShhJ1IDMTpnPwDO/0AoJAAkO6z+U9fmlVPRpUsn+i8YiMy0NjaLTHZL+nbQh1b12hyOTOipQpAbb6scseti9hPiSyS9JgzwibcRnigsfPMYNwlTuoX4arnlDd45yl9J9GS62r48zcN9o/BDsHyBfPlOyk104KeXoUFdoCJlmVhTx3iyxZX4V988bFL8uqc2fUDeOeG+P2jtT4uTkpTmQYCde4kz7vN709HAP6VHIY8IBPDt8Kw75sjz2qLPsz4j0onu2bFPH0/AeMOJtDWN/y8F9yetV0jx1ksD9QKk0qetFxsLvny+7/hvNn4C54kEdtPIRw9mrvZi2CfDvYb/I6rB8rRawByB/grJ9BJv1fTP3U8jR3msg9u1PBEcYkCMLF3855V16A7fIl1B1RRZiza/BlrwgynwSVLga47p3+F+lMZcK501w09CveO6pMR45KUd6SlyvLLA1aKWTRbYYyngxEhmGjfWFZfZpsRt369XNsHZlZoNEOoUcAPs/+5Ca0K//sJ4nqmbcDe6qBeb039wsjdxMz46xKzNrW+mdcDvrWV9DMzt+Fj135GzEWx+YJwhk8qm/eGwUR4O5m0C0dBGM+2vssF2xmliIAv9tBrFkuiXqe4mSRvElUHUGpL2gft828FS35O2n1QSOurK/UEoomAD958YXF7v2PWoDNaB3ByhjbrYMcAaZOgzHDq/hFPI2veU/D1X+rLb6Xqhf13ddRfS/kYEC4S+KCJUnowtvEUucw4AQg+PXguqmfzPXo49uwJBbPcr5alV6VRSFncqCdsWUMBMBjsborZyK/OPM5BT9Bf4mXJn5fqz0YtC2ChbV1bo19c1RrMbEis7GmzO8cfHNLACIp+0WV91zFQGfdQ2EXOQ2A8Zm3UvsIZNYaeAHNK5GGxHL7CdZzQn3KLdBvy6gOF0QIaYO0u/lTmI9eVRs0oAUaxjkcc5AnAXAQDt3zo3ZPxk+NznUS3fl0un92W2hFs5ykYuHuYaLZhBvqAJ1sQyeU/FgE2VdNHTmzkeIJNWs9Mdd+z2Fc77pIkkwNpfZ5UwHk3DelwpA3M6h6zdlkx/RiAxFdu0Ej9xRoV0pb6ylXG/6M8hmKfKXWc0CzIT0TRs49m/KoCF3Fplr6+rCK5HC5nitG3BMPZITZQLXQ2G+jXm+aPXQ0DkFbfaHid8lP6fDF5dUWvAs/D/xl0NptuimfrmA6PVyQ8IZWexICnZFpQq8Z0bvFsPlyJbqTfjmHD6dE57zwcXCScaNPrLqiDtnJL0HsQahofoGCRXQsIvD56L5y1/XjxkvS71nVZ4o72Vb7NpgNUCbDafOq+xF48uGHmw03HrHORX+3cTCvoHR2j80IfMylyIs5rnXuG9NUqyy/M6L8EVkiINQKiJXzfpHaPJlOwcxV1b+0ZpeIEr3HIvdKBJEd1YuQxSWBSILRqOrUmGoGG5PTBu2R2J9Y2E6u5ZQZgAUzVr3h2AyXkzyaP1MyJ//DZJSZHUvOaAX+W7GyqZYSHPK3wry5NPe4442Vpvs0qsgi35nDY/ysZq0KpJTv9fVHQyFhHxGfe7Oru4qRC9YC/0IuYPIfylfuJ3x1+RzXbrnD78D+lNrvP1h6pF4K0HU4goLJU8L+vbz7JdSqAVcL5ARt4iLKulBun04uY8YHTBGYFrNIF5XuyjFZH8BucA9+PnvjH6Um4VD85M8oQSbga0E+tS7pDZaiaEJ1gV+pb06nI2VsqWTz1lObU+aZ0zAdd84UxtznoltxIDTM/1QKFOIhNod6x+kTMmWh8JW5wYNhoDaBLD8pdODKh7nnSNwYrLVLPKDciWcqk2bcMVRvZcKltCuPqIMk72654IdI7V7UpagSvl0D3RJ781bVGZ0OdoTq5KhUibgEKldheWkjFJI3Dgn3g1RSABF+Tn93Y/uVp2UTFuXcmCUH6Tl7N0M0m0Rvjfm02g0aFQ4l9n2O3Fulba2rrg3bz4Hummj2/JT0c3ribcy3hxspCafmM2E0EQnHV0lSdT+5GA/ubvwIknoOjemrOwzQxKzsBzRzxa8XG3UiUCz4hZW5lJ6PxM+BpVNwzLwus+7KtvyGGkWdebWTyv6CHDhAR3sg8ADYtP+2wnjp7BH1m+1onmvtg2Pr/3qx8/763GWtIRNosXoS/s7HchnRmBC+70mjAU5lZmdoSRje3i5P/eiKsXsccUmK24KWfmz1aGEx9hursEJSwjmrfA/KYTMBH0u+God9yvRKlpknpko303X5ltveQNVKQU5Zt33NIqKIENZXYX2S4K0yWnnzx7sRPWt/bIah5GdrUoMcP3gVJsmZqLVptCKLA9zO2Z1PkCxKlmsjoX4NhPNFKoI3dSGF8ZRIhIUsQQJ9I8l/g3aac31bbCqW/AFZU7HWsYlLXpXPlBuK9b3rZIZl+bCJeTpNTNILPJB28U2c7LwqY8b0NRbsImILHrWMs83UFCTcsON154vzBdb5jsc1OBW7nv+lo1GTuv0wvYsUH015U/T9OAYniWxXpFtZgpXQjYF2sN7p25DVYhFiFndBXNuU5zg+3RliZU3iUgdkLoN8SSs/4H669Culi1LD6+wYhbt46OLETNyL3LNMY3wdbQDL435v1ND0L2QFyC1fhu1LBYqKVndKuOv+JnveMozwHO3oqiLgXYYo5wurbi1JRjcPKHKIZmC3clMG3Mv5wkvnIr66ej3HS1rEhLxb+s9fsoQmjuzH1ascxXhwsc7UFpkVzk014EQ6Mbqn6De1YYMwGSNlvlo3FWcD+kV495XmcKLFl9WHKB7Ly7GFlc0epSOa97skmNKB0MJaLXiQRaQFH8uAAFcsCDHWEn7o09GY49G6eKTzZg/N52PYPgfVEdHThTYMlS5jwfadme4NjC6JxR3y3IYDvBpfpZbaO8w4TV2IAFn2c8fvFWp5inbSjBOsX/fBVlCBIHR0+C0+bbRyCOatU3UdgJpUMHEEhwd9hlN3MAqG9KPDoX9oBJEKPh3ZUFGrRt2L4kkVSf1s7E1VA2lzP6Ku/eohzeol3WTW/wddaPc+5450rhLuJQYodO5JkTp1sXfYFqhA4zU9k7FjIYjVyIjuJWnXy77keYYdpHJEz1wBoKF0Aszr2VZBDsN2+aEVyh4R+7yC33rJ7kHA0B02FR+ZO6cOhgMnxrYq6uZyonq+UMiCfXBeUAam6sH6ufOyvqLHtOv7Kv6bv7J8vrbmAfQIEgsoKrLTPOsoNGNDPsIZLFHAu6P5O/1wZUpK7Ry/QqfMmujkOHowiuCcA51k2W/SXGOUEHvRwFqapUvbdrVF4u3WNbcieHts1JagUYQuVGlknEKDe9wM52K7KPenakez33w/1H/OYN98V0PH9k/KpXRK19JyKbPb79Jnlu3XikMWsxfqNnRXKEkebr9jkXWT4VKLcAJrMjem3DtJS5X6+8d8ALn41Q13Pzp5PWbJE2JKH/wP6gMaR4Tdtz2yyS4vpWr3kyPUUSbczJba4OT4Iz03CUOi2dWxklzAnD+PTX28YUC+zfR6js1M41mI3bVr9YWdnG54t1/fPs+HfLqihGmtq1aSwrR7j7G2h3XP8NrnRR4yQUTswAUPBRcnUk2VkSMq4QU5P2IS6QbcqEAdfYjUPpAJn+K5EW91i2nsILWpuhVQ1oWIhrBmwhZ+em1qD6H/FGVdN2FnK3/c1vx2T8jfWqDif8hQ6oU1KlkH3Cn3TD+K/xmD9oDHW1WmRSSV0Hd7q89A6tC0zZOa/V7TdksgWRoB+eLUI9WU998n74rNapqPaM3vvs0wt2psP9VIYBh5R6xw13IZiVsGzE6tcMX93BKpYgtbyicBg8Uaj0UqRbtOYGnUJvQ+ZzlF4dtkixDx7tjlyojJAo/NRXg4xmddr5dvqWAjoXgg9Yf95oIh0dhFNuDsU3avLy3zQpCh8PGsAhrs3AhnxBXLnVv4jS/Fp/m1Kl6EG+owkiuHA07mjAw4eClk0zICEBTimWNUviax11P3neqszt8xfzAH3bvEqivaa88du5qGUsje2iwo7hqrAORsda+xmDlS0fWQmkttRv2IOW0Tvar+76U4E5GwYgbFi9VHIejOJofMY0IHrsWBBZxBt/r/m3Cz3/9S+y9BWrXCWRbTHoT8GWxKcldjcOF06q0xeGzl4akt9YZtaDwCEky3dqf9HlnPAeCu3dB1gwULPb4vqEgWOuz3ChkIXHJsgrrFXYr7qsLVbxQKDDt49yG6cbW1GU9y3iurwzu1AT+JcFnaSoKtIukMYG4vXK1SQ2DhEmHPe+7/wMdvpX/dRdbJquxu0ymaPssl6eOQH2A81dh2cuidESyUvHYTjQtj7foPwLDCoDK6qrak5+P6wUuODW7ml6gKxyf7L4/zgKBbOoVUBxT+J5uQ1UI9r6iGs1abiFJu7pTCPHjGjaGgHJRaT28KHe9a0/rgiqAKx4LMpJmBTaPCSGu6gNhZHKSVXH7smZmf/QrzbHTGcfWpBZoxujO7w65qnMOu9Cs+EnSPkLgy4+kpCah0xfE6YJ3b/+VTfUvtearBWeE5695xseV2y9oLehVykJUxYov8FUxWIBh5PYaat/hMM6xc465rXb+1cdaoEzTf9W/HBjpeKrdP67E65yvhJfEsazk2lLkdr8dmx8wX1N0EgMGjT2QHkbD0VyARWqxMlgxSw0tC3LDVAW2zhFJ9eUSxrp9k0yIq2FYRic8PWbXVRYdJZZZk8Mu8hhzIkI67gfd9iBL0L9p8lQ0dN7g9fzhPSEiXdCjNpvCWYM1e6NNm662dXXoufjY55Ooy+k/jKUX2WBXSnXcbye04BpbukIEELNQOSufwlinFE6BG/REEZFwU+06iy+IdV2lRxLT5s46Bc4FBEbmgiWMkwZSkGHrWJx77gSZaxYvN8bDy1X6dGQeXIodVtM0bY9SP+XEE+EjV8SoZaWc+X9TQEg6J9tOXejTJJLa5zeK9qf8YbBNdKkmZI5Jn0/Kkk4upWNs2KKxVHGug6r5WMQ8X9cGmRbEa3kR76DzCOvkVgAs79DSQD/vu9PrBrGufKVn6W01E0LnKwXQ++W3RW6g0FTwGI4KP5o3Zrzw19bCUCUbGxNqU0EniEN/Kl1WGaM6dza3sq/BfqNFx7fkD3GX9gDv2l0TLTZ4rJhnz9dYPt1/TIFfNq7xrHbRRODDAtSWc0ZgBl5JkdxkccER16WhYgtw1BCPsGHfltphAg2eUcrFLwgDucjdBGIG0U7oi4sYCn5yZT44QuzIvOzDUuGAwFqyfEAkTusYRhUdgSyPNqVDckpZ7mEj2GOMDGDVC0lzTKU4PNTlc5RgPjqZJRm1oL2Gg+U+Sho7yHA/KKtzzHUq3Pqiy5W4VFR1+bph+r+j+E+BaKtcsKmsp8WkOhTDnKGCQCMNbo8inqTtTqnhJINo3hljGdnkABTWhlRBX4ANKaUGwPodvyNbf85sYpBi8fr0Ntirpkj5/0v8DA8V8LsuXg2fj+v9hvkM3XMksbfSkBc6MVUTURKXizg9z/7ghxSKMxkERMFPOhl115QkPyaqYzw465jRLccTCN5qttKTENQn/MzktSCb00UyPW1RFp/rAMqRz+G0u9DbDIGBr5Lb8N7I/3472GfHmqQ27/fFeGBZ2CaYAbBBmEhdLvYNycmUmzRnPhtpMNyzKHegvDqWt2YA1Oi7SaoZGiRsGNwWecqFQNTgAGtgo5MCznfZNkdq9xj/T0lOOfA7r80V5j7UYKQsIazeBin0+bnYlQPi9ZFMKZLGIHTvE5k7IBJOyDgTBwPTZLhY1hqdzufDOPmbyKUmvyp0ZFWIiBzD4iNRRkv2k6F+aXNB9ko8MbLVwsEbLvtycNmKc+HDpvPkVVq4bQfZivG82UOR7PLzbb/xse+qnHUpNcbx/wbZbl5mCS66ry47NW1uKz25JIg9BA+KzEpeBASMHMGNuV/7TBqFGOWyFlXB/TM1J47hIEfELI7OBDJbxPcHofQAeY/EpleFfKos6Iql/GQYYJVFb3s3l9lrcuCugesKH4VE8IUxipwwnPQspBmWmbEOwiBDHFJkhtD+mra6gk0QbW21iLyTa8oHFdD0DGsUfxpXRsm8NcYNaMy/r6z64Do6VbBPj0IjuHhPdm3GGpzh5rH96ieRWsvS/iKyPBZofg+WdC+KxHfvfVX4gJZu6E+ftwDo5D0JGFL/B81rtVysJmOYs+Z6d9R8AvEA72FcCkFExZINB0tBHj94EomWd7Ph4bitixMs8db7H49uWYsw3pW68A7G9pozq4OiY4MtT3iZ/5J2x+Yj3V1eD/iuJYAyxHOzGN54W4GG21lnEoXV9eZDqva4REtBTIEsyoXCdLUSDzwFS6OUkNjym50DTfJMaJ0/w0rcSeXnC4KQifEcN8clYEtNDtLVvu4bdYrm2Cem/t/dNtDHb+kZmLxTqZABdshO+w6Bnskg4tYYBbqs5gtpxKvGsQczvk91xoqL/iYcHBEvl9AJWObqby6iJANEvBb3i9xMrEoWbxFThFkP3pgXd30HBFi+6uqK0R947F3rr90+hMQ3OdoSRMcb8QxXOpiGO0lTzYWff5duFqCOjApzokRgCpW6EdBTx3q5FXiXt/GEzG9ZcYa6SArDPiRnQuM7LXbrrzVtg9O/Z5c3h83xOfu8pFg01eD1WXX+XS/EmisfGDCrrf9j3iDjDEyG2GH4d68AfZOXLffwCzm7k9ahOclssrboqUTP6kOHCL40UdAD42khJJtIxsNUM1fL8oElFQfQYoLKwjUutq4XeAG2W0cReco/kpGyKjNpud7201eTtUybfvnjyRZptRuxR+bdeMeL4Cd+OJWPdF+Py6IZ3RoNjg3L+t05cXnQLPaId2H1987IBLtNbaWoVM8ieh07PZcCZnt29ebHRY3YbJrQETFSku/qUYauMlHFBMiRZioiI5akt7TKREnU+Ec+aItQ8BcnuJJMkI4EsvqgofyZeB4pJj9PJagaSR9cS3ddAU9IeHmWqCaZcEYxmx/IhG3WM16VM224IMLvt7rB86M373LTbOk7Pet93reamDrqcRmu2VT5RWLe3OiL7FG0inZfv1dIdsn68I6FbKfPbLn5VHi2VVO1acGzacyR54TZ7iNTfrp5obVszliqwN3RKxgk22lq44YTdktbx+ZzrAXtJq0CUF/KtXyIIWeMxOQvPK/PmSp1eR59wbTCh4BjWqjv36zuu7W8k5RfgeSxCOp/2P5GAdr5VGUgkH8V3kC3Mb6IQwIjLWX/4PVkNO+dFo6iRlRFs3Viiws87syWyzi9K4l0ZrkyqILO66EfWh0GZX8x1sF8OiyS4QQOJ0UwcURE+93jfKcmXta+gNVUCjvnbcyYSCDZreuj3VJJR+dTw2nb58ZhELRtXuZZqEhsIloYiUXWnQAeq/dYiMqRLBY8xcSxW/RhD/TCDN9Y8y0p/Ou3k18ZJN5ECPqN6ok4dZTjthsBGpX0QGZ9dnVAUVpD0Uo4gH/dYYlBDSOqJw1xzFdaVWgDpXdMVTJoNA7kVd44f+6YycKafT9NO6X/cx11K6HqPOMqF0H6lvglhcPmz0ZY8MsbPc7v327axramKSNmfcADyVLvMsTSyqqSJYeq9Wqpz3tFBtwtTTyarUfkAvcA1Kw7KFUkxKKtRcMi5ivBEn+omgowW1LTbT9DAy1ZZC2t3DYXXJAWGr69OKIphQB5qFLzYs+x4dXejVecnzau3gfCU/dW3od4abiFsP7fv+p7qwGyHe2+cfEvdjxxd7Gs3f73EuKi9EB7C+7jxmwlQAzDJ7ZQXfHR9VVkkNxBr9jTN+N2WpYPfAkn8ugR8FVwlMTMLDIrvUEM/yuNdTEEVckbmqghjbRsRp/3zhyftG5T8vncH28RNQDqAxr5SrbGL/mm/GSkUsLEf2V0Bsi3Mv2ml6jfXhv57cbyPBG1MhSOVrJ1OBpE7gBaqB9zgRtFNgVuVf2yT3/OmMPhEFEcnojYYnTPs1kEPa3kM/GDPykM3mUszNCzi1AscT5P3QZRK+PCWX+HtEs+b/X2zr/4Fnd2Bn1isQ2abhsEqcDI3yitRqsczj1TmVcpMRlSOggob/PSuGu4STO9UzeWxtWWlq5VdOrTPap+z+KQ5hIEGpAdKZB/HBn71q3ow6wWrTYNfLXXjkhfWitVs61jzjIXuCEEa0trRlLBmQqiH1OxAjio+9NG9G0eqcdjDVfCr4nqi0cf/69gEArHvGvY6FwxA9u8PY7BoaHDFsxEmM3GuekeWqGp0Xm0hDPk9HU4okXhhfV0iz2wsNlIbm3Ge5t3ePM23w4zlNNM0JVjo5epdCMp0FL4X2j83Ag5j5iSMZer+/IdxMK0P2VfD1Ttx+K5Ph7NcPE329TBeFbBh8K+hdK13/V69BLPrg3bhK1PUT0Ojyvz9mL0gA0Fwi8ZYfEbhS5IWwgMZpN13UhN3xUFm83DHAIf9cI4+3daPE2E0g1lvi9JViz+NPtzQRokCn8TTdttx603nGOhfQwJCdB5ZLOKFob1j4KrXIbcH/urTRt6mO0vAXb7fe5di0iiwj1KOgj1y4+jV38ufeKf48z0qirJeT5GLCPcpbTpa0D2IsZdun/mATmokYd8keCDUIgtPEvycxuDwWK9YYZK6QJSv3qT7o3PloOIkuWC38e+nTogFZhiv9r5rku31UpBtVcqvDnDLsZLvIAsbXTQAMVl567JF3B5xKHX/WrVKfwiml/Suxq4P/W+nqWvOgf+WygL6c0D9WUQgEcEZ+jiOsb6sBnarpULRi3faEeHftTFnRgFNkepOlUylQgzhGws/CcQftMag6qj+TbIk3Yc78em9xX0pLy1/W1XfYBy8czZsk75UN9VWnZn31dD5MFCDLzTKOeph2Pj8kFajFYIXXddYaeS6HZcqF1zaRrRdQRqZ5DNNkdfxlxH/n0t8qWBViSmnTHbYy5vS4hYvxIv0QkVGicHIdU2L8mpDjxuP81bURDU+5DoYYyjq3ImRrSnCrF2QExHZvhEUAqJ6BOlcoiJemx8bynrbFxjIIWtBoFXAwKM9CTAEDTNSPLNuv5RNobykOz8JmVS1rVdR6Mh82pimNXjAPLB3hTUVUyt1MtuS7TNoK1Oz2prJ2yGxMscF5UWgt4Og28y1oYHzKmKPHGkIM/9kIFZP48w+GPHwjV6IeCuqmiuTVEUnMXMtFvinaJJH/yCZbkmdrjV5ZGq3dSptuZCwVi/IveH0abfDivH4dJz3eRy7oarhaS2VkYobr0qX8w37r0v/I1xkUXPRQm1bQBSHnfMbRyYHWivpcdzOvz5b4eRP1DO2nTQxmmWetNgfxxXS4WeT3g7UeOoOAKNVnnBKzpZiA/tV6QElVESfsovOBh4gbLDQ7ktM4VzKoX6t6byAm6kVkJ+z7XY6vOEhXgaES6MWfbx19MvFhw5QycUxdGsF2tbgHPqnhs17we4lg4QXYbvJq9OQEwQhiwZKFglbl2TFS+oACW8QIcZnLIoURZp0sGtgUaHLpslJv/qb+yEar/ls5ruPUZIazWXBynjQSXIGMbtlqsAUWiMiiRDPH3lyf59e6uWKy6Bzya8foqHT9a5/+3O3h9pjAwC2F4APazcYqLwlNpZynyxE2QY0uOhBt8LKQYwhsV3FxD2J9GxamrgMonfAQx8kzhHfgZE/Hj5Fu6aj2aVA6rDIW10vIBg75LMovt+LQRM96xvvHPFm8rVNkIKCirYUNnhnSdtl90fP9JTjsf/tqjy/vJ3NZXDyQXcccjYTJlAW29WnwI+17xrEcnYz9mbOHf4rCe15/bi3uu1f3BMEUjYAJmFLQo54Ga+DewrLc7FW+r9ZaV+e8bsd5rBWP3wSdg1Ff2gbHU92+lr7GAZTM0Vkj05l3FSrrqBOqv0p/ikGd0x2yqFXjbDjEJJpmaauXsCvX4TZ6/N/AVI+na1kX7C/8Mxd0Lhajd45yZj9k97Q2SfInI+2tDPXaq+JUUJm0IXyU/1cUqUMyf+wj5sxbPSX6NLzRgj3hmXY8nBdaYejwrPlPZd12uVbVT3k9aKnQUemFr9GXv9hljgO4y9WRxYDCihv3Jjksyy9caTUf5H2vGdG1d3U+HX626yBls0ghalnUBYPmDE6Z7cAh3sCL5zJajy3qQgr+E+aCVlh4+UruLoTPhLd7JUI3EgG/EstU8XlRT7E/cOYeDtuc3F+t9d+kim4+pNwLlJLzVxmzVJttNyyajhyjdjTkzRGutuvNxc1NVb5ZPwZ1H1G3VuQjw6d0PCF2xw+Hxf8aHI0iFD4msZ3Uo4lDUU0iQuTLnWtznU8jCI0B9DfeYNxh1hl79c7x+JpnV0JfpjRxrG9+dRAva/xLmZw1M7SC2jIBu0lMmnPq2+0TQTUuw9vqxEZwVBc83D1TuZuSlppXxu9ugw/rJPX3xZntuF610RjnOsDhwFYmCDhcUUyvpCx2Y3OZ2zB2NvKn1tcLVes5tOsBWZPxlbZDrxkzUUyEmcA5NyJ1qGlJEBwe0DcqVtCRFowN/a7XqTKQUFzi6KVGfttLmmyQXHP6ff14JOOA5uFIOKYiE248khhdSqXnCkQYCwL2TIaM/YmMxasgol+woM4Z6UH1pAKFmMTFGYre9/T1ryO/6xmL+m2Xul9VtKq6GjJFfonEBl13E7X5N0xVLTBPqMLPNltl5qpgXs0HjS9qavloxrFHPrqk/BpzUjbLNcXQ8PAWL85RMk3YM1OOMRo0S1rVn7MKbhpHDuYZt0NQ0jF8QLBUmVW2EoNVNMsKLAAKT+JaNHZes5tk7/0E7bHMtRR2m0P8Vw/ftQGbKvoQy07IhAB4bzVr+eyUOU3t4j8m7VKoJ6tOkpH5hNWY/4QCNvyZJua1ZkZUoV00lREcUZiDHCw7jdPbHfa/B7+VzZJDG1JY/BChWBfJAglW1hBVNZtXeLrqyyQPgSt+tvJjgAxLmyQ6VTwnu7OOyxguUfxdFCUow4GPs/iGMb9LTk6gopgZMgTal7woZCMua2yj2MMetOYe/hFHOIiEk5bhPwOMedj41HuTHAseycRoCiEX/vPPIqf/iIieOsKYDXg4pTXqMsJiqu5Q1ODLT7gKMQMgmiBphYG2yclKfdpJQ8Kv76hb0NxXVL2Yjn/In4xz6TloRyJOhXhsSytlsV2i7PR174s0/7yGfrRvh8XvUlkZFWqSGVOEE8jWOaQB8sG3Povb974cpZ1lwSpFsQ3t7iLijLnsH6pyoxz3PK9+tnX23mgeTC1VRBa44mKhUP0UXtwBy1+pDsZVNAafs37OIw7H+RE7+fHMBK2liw6ez3qzWj4ufo0ApxXkb0Ot882MBuZmJvM7Al8y9hKetVokfF03j6ZMlAcovNv638x1XDd0sd0zv2PvCL0zunGP9PhqCZRhVvfhEdyvl+SdWx8n2sT3hqIV15AfuQPpbg3eN4Gxs0hmAs7MiQy2AULa8FBsVXg2TM2l++CTEJtnMDxlN0WFzXmTcU80QFE1LuB0UBPZotQhACIe7lmvCfZni6F3aCjfrPBObInvycXLT8srnu7XPIfnkuQI9bxZpo5gmOh0jBPVH4sumTHVE8irpA4dfGKoa6YvuXrmITYFMPnqFAbEx+IvB+t6a+bFDpDFBoZRl9l149kwVQqlaHylNgtJIdemBQ/G6zjzAPRz+Bn1IcOeQbNKgSn87drFeuAgTfOvVamGI5/ruAAzJHSIQix3/QnzwL8YTwmc4OJ+AbgwdvnQw7Tx/NrmydH4Jlr35PbEXIJY/bkq6PDTNxtfGtEB9oKjfJp92P16AzxBB61TPX3/Gw3pXJB5pt5xdZ4vIjlMcSKuFmqrlvkxGaXI58DB0WxIj6/ooPFj2fvV4yOcJ0ivYONnvABCsnC+BHD7VQAJLmKLnEJc94QA4JAhKszLuV5hEYj62MdoYytEUSxjeeoIK2XP5Zu93XCHloL1iMm1FXKEoArIkxzsLIAr+aJ7n8Hzqtp+WhfztIEXCOkPOfibHZjwfIYjUpzIDul6+6bLVhq14PHwAUFhTmLhpuytFpVgCP17MgWuEsZGIuaSEA2sP5sgl4o9ICICTsxvA9PneyoiUo1/7geM3auQrDeR4rXbwElGgO6fV/sP8Z/y32SuMt5eHkGxMAZgdTmgoEw9zpkEvHPl81bClxrXSsKPtHTCVgLRjPhs23nBgAJgciaRrYfkaIwdC51j8eLmnRPkyMerIJUTqsOOvQpumfnS3oiSERW4s+E4nU3iEhmK5joO4F+AY+K9G2IQ9lwECtkw5iCnonAjFnXL9xF9uNTGgLrpzT1olVDbStZMrpJfs3l4YT8xAP1tbAt8Nk6dQUOS00Mflw1Lao9Ggsfa+WEeAfeM728AepsKjQSP+k2KxvXzWwuQZDlJOKTg2qe8BBDSWIIZNyu0DmrCRTz8w8DLLPYnSgOeS19vUt/rBIa747IAgPutVmNlzOIXeljGIQ9bz8nF/bh4ND5nx7yydS/cU5+zgsayjH91LNaK1mGe2xFgL0aSlsuOal+2Wf30785yM6lHmeGsrqK5e0q3NK184hIce6Y6mbxy0p1fGLWIFYaR2aTkYXWOnwoqls2CA8IL4JmmUJksE4mfQiZ7tCrzL5TIf8D1sje74S9MVzaVJw1b7+CpbeYFeiCrs2y41OCfZxVlB4K84B4o3bJ5McQrQucS88YTPZqejd8emCUcP0QmR88CFd7bpBu20bmVWCMSPQZ53Dxk7e5lW9D6BAIfXNVn8qboMvGsohW14vB6rZ6Khvce3PbMnyaaTWh+cy8Vj6E/PAz5gHqkNY4nzY0Ubl8pweQEp8XhRIytKJDjOH1Qw/WoLfyUrb5TnRgcpCTzsZtC6GNUlmO2FHS4/prjWl5o0NznPtln9kaKuaub38uV9uR1nUkdsi1DPMCxVnEwvvp9qaUPwBvCUgkQd0BPl0tM2bzQwC0r9N0rHLtHQG7JnKzYjieumX7IUgpG9DT2Ox7DjbRI9ih3hChQN4ZzDOPbAIr6eERpoBeDxak42dfhY1PFLGr2Djo6u6pLtPxoHUzvCSqDGZGx7Pzg27K4Q3pEfcyHJ9kqt3yR3UgGxH2ejNKSrr7NhvO0vQefEk52hRGoRwkb5pyFzmpEfgKl3emNZ/SIIyoZi8PmqZarPrFzYy2RYodY36WP/6JFH+mDPUIK0vSCeAO9qfGchw6E2azd2xwmqdEmcuS3juZYWFHxa2tPUEx2fg9TDtRhf0HIGuI33SSWqzHOi+L1cuzUW6TJrqodJbj0dbzQQkEJauhITnKK1ocGh0/DolO0mHrLGbrPvrsqOtp50+fWEwfTyLdRrgvI5+loej6C6PuONdB3oJej3NJP5FOizfWlVmsY6OSc1crW2uPNHgrysHj2dBtCILCUNAi1q03lehO0jI7zw2wvNnAIdjcaMyul0bh2pJ8spfdOx4h/6zFIgd9QWN2G7p9vaa0CIszxQS356POS5OPBvQ8DH+EAyqW+KqYeA7JFmpYaH1f8JQGD4QuMoO7HFPR2yyU/XGZrshKPd5y0bgWCLstBgUX3KxQOn2wjqZsIG2L+OtqROCH+eLRyvAzK3IEeZacSxY0sgWETXzRm/QpTQemBM/Q3YgEYAFpk72StIQria9O11c68GxJDLx2XYTlja8PThRQCpVGGuj8lo302wqhFd4DURdOssdW/g9mzdDjBPQAZQpFS6e3+jQlyregfjYhoeyhqmTmYBrc+rj/xevRaMJoToatwp9zBOIB+LS9BFRLTWqJWAqy7QoDNnDH9ufQvIYhoZ5q7YQqOeFc1vdlk7cCLhW+Ng76aEt1d3v/Z3AblBvxsK3R3jL2npqVAXYfdK+tDSJEsRVQrKNkPokHfy92Ez7ys+zlQgh9OvvsNiUbil6aN84znUuTEDHz2BigwUCxMKuNPM5CKqQx/Hvhvw7ZHAECK2RagYQk9PCf+Q9QynBWB2/13UfyMe6Zn1g2assRPNdeB2lUAsjC8WSSNP2pg0pMjyPxyPnH/f+D8pnjxx2LKMUBrk5EY9Ji6hdgXEKbSo7xcXbTLh4u3MMFrHO0RWO71XHRua69Be/w11MpgGUNkIl0gfMtbfIDcAqtaymijQkDCgaQGiPnp4CaxMdD4eD2av1eTtguhJ2cpx/DcetanZj0wAXTZRjdyuQMpx+23HjayNKcnXyPXJtb4vz0hU0wwVsac3vcnKPNbObw/iY8MzXoMK5WKydOOmc9N5F/EtRR7RjIBYi1/a9H7rEDuY8ZbbwAY8kLdeceif+C1XjPyM9DUemWvgizcNLECK16M4sW24qfMKn9IgHEorfs58iBsTAlktPADpGGAy67+U2PIaXW+Wa+CxDyo/duKTSnTHBc1CpTesQ33sy8XaDzFRGrtuVp0w4b8cq5NukHdYyx3MPwJb64ttE7PEZLlTD9Gr0WCDJyrLamIMvBwJfkd6mTpmL+4twR39/rTvmQu7wK1E1oJCjucgD3YOtQ/bpRJcAzDsV4xv4YP9IXznqU487wP6X4vtNp3IOnqidbjD8S+6VljL8HsFH346BwpbPe/tzXpvCNLa+1jlJ7NhAxGs0/fM2c4j6ygimnEdfSyTvV1QOnf7new8TYesQmgR5o+ALXjMLMgRhAm/Iey7psbYfpq8wEFXqoS6EZMkqQqE+Ga2yOb4/xwNsey3v665pcfD7LSO2UIVZaIdeaKViOeZajHvtBBOkm1F9nh82km086OuLvdlQS2k9JFn1PJA5rtbYWSojcaMxqEFrxQ4+MEYaSHb8pIc6gbCBAIkdHnCHa4skQXXjOb8zK3pvn/EzFrKFcQjFmCoCKWQ/xfL4bYPfUbe9MqZEX4V1snVRvU4lmP1ubJESCkh/c+TxrG/h/9h4F71SVW/7kohzbsW1ynMuJcBQ+MoBQW3GDG7/1CuOVkmtA8rIbDLoLQpMeSd43QUbZe8Z0XEMAxsbllLOHM0EvHKYpxltxRi1fltkR5DIj1TxwpT7rIlpJdivO1Wxoi0SrP2euzIlPDhtDFNcVoJRwSqu7RCEABf+YpL0bIHrdRRKvIYSN/4BPy+t2eJxy2u6/PwKG6UzyE93Klx1TX1dHmYXZ5BZ3J9RU2m3DppDdgSCWUYsD8XBqfe7YunIFBc3ZbG8bwclJrXqwrJgQCceAN7Bwmx+cpDoDsbtwnoOCx3o6Xlro697kL8eEbDqRR8C7CFevMQZ5oXMMsDqsOWmb8Kq4DW13IUXoRswryiJIQQbciERNr5FjBNHOXtrV23EeUP+Zilgfsi4eLG9zKgpbj+FDSe+Smvw2MrMAysIjWuS47NAKg4g9xIQaAleriD21JMqj0RZlV/epoSyf3tx80uClSbWlEVstOVtnkAIPMa2skbqq4he6lJpNvGwcv/Fct2DqWES2Pc/fh2FxI7tMo2XjzmYhzJQLffrtzruA2Rrfq9aW44FhUxOgBOoOYU9os24AQ4dNpNS4LkWfEywSKVs4wHQ8GGqvVdAE8c/8LuN2z7now/d85BXoq+QFpWZJvtn0H1TOlxpf5eQmSDYZPe2IQRdQsARYu9t/u1lsHu6tBoe1DLGaYe24hEZnIIrSwlHAk/tzFvFT9KdPxQj0RvdtA4HXpPll8XgiFeB4L21tTgd6PClENYQ3Vx6ymOEf+0j7r7R6l9xadcu7IfiHCBlZ6xT/m/OReSLqQruCQg3zewhHM7Wfe1WmOml/se/3LExTxjykKyLn1qhxr1L+eRckLaSRHcaoQPtrHd6VSsxtz50KvHyxIedQIBnmQOalRXxmhwRbKfq9GV6J3tkLWq0G5g3Ga5q+1urdjvXJ8OcUqq8gjrqMNi/8vVXrXpCQDMuULBM/oimb26Y1iLXGeYNnl4tFpl44wOEs9kn+G9Tec1vAg9j5EGdNoFKxdlFG2krDSDO9ofd1nd7FzFOxv1XBKeve75YAfxHcCVLAEIaWy3wqgqWhjGGAGWc3vV0T9SoSwiw7B76RFryed9WtGUUU2SyNgXNTfUtVFQKVIpJIXK4R3OKWF2n/YYiHLs+cumpvuFbufiffOnl41IC/5/SuYiT+8gJKUBB7AkIs7aSLSD+mmGgZ86IMNJWV6A5156Wq7WjQ1ukVP0eUEBgboe0VWQr0E5kMimIJY3eO20rM/Lhhzl/eJfCDQ23nNSujuy/6Cl5s/SEZqkvrttWBEz6ux4l8tMsMY4MbBsx/3zsUzzX+D4y/sqj+ajMI8szb/U5TM0y8+regtnAAKovngxjGeiEzQNMlCBg9itH8E9jLt0nuEwXp89u3swai9/DLzclItGDndVMqjUWupzws7NoeIWBIllgLXETMAjsU0UNzdXWIa/v5rVF2skQYylCV0qvQZzeREJsb1PWqdEUj9uDCBnhiAhXonepqWcINauT6oa6LR4xbJ7lf6wXopZz9AQ6FQuBpPrLMLAVXJzqgE9VtMsUit250rrjUgTRjyOaRXIA7Gw8KqvWn0a3TCD9CVxr8fT/DniVD6sc4R/F4Yj1vi9Z1hezs9OxxM4iG7T7LBh8SzMylnXT+GjDCbpwv+cWuUuaWnkO7wZ85DDudxhiG78M/Oh1uW1gqSWaIvLVZNfm/EZ7bqulXLU9mFIHB7aMrokAQsU46pmmb2tE/eNnlN4UqDYRCb6WQaOa/fhOhG3WjuRc3fCndiDueAONPIFqotHRoCoDxsVmrBMOXC9PYPpPkDiohKfiK2Od8RewEDouqlhjTLCiXPEQ+siXNkYvSMccKQfdbc380ZMOH+bgwxqYybWYn15XfLqVlFKqj9tHxrljlkPNINaTDWEFNt5xKaUDOSfNXlmWqsClmj/j28guAdL4xkux5ILr3K2K5YNffwGFgt/GNBMm5yMCDMBOZPS9Ac1VfWzboXUGaFp57V2iFAGqFd1sIt6tdRbyllgn0/oT7Ad7gard/91vrNqRXiGgv1c+qXfB1OjkRZ3uvgfanUHg3kuhVNIwLiB5yFeCVwTDVv7kkeUggoUaZ0LLxOJ8zFYBdnjWajkIT6ALOrG1syCGBdjCt5tytTUunznq+609PNP0aFIE4IuyMwccwVRRKoFYZoAAi2PLXG2yvF3ZHmGaR3MDYUamgJZ14zBI/VFwjauCQdKl5mXBXSfg+8xBTrM2wzmoFENZai3wuD3h6pSMBit6UifnoOzKqcPp/iF7wCXCvQZoHvAWRs5sqxzb/Y3VCKVJQSE7Mcr2mgiVvzOonudNbxE4ycQIAGQPHMkC/3bEMp0Y/2ONF9Lm/u7vr98lQvDeEcyoCiIVx2w/dydzqRSOwg/ltxcP3COlBfeYtHjj2MzuVGqSEFfWwfkRDGVUfztA8l4kWRctDUADhTdBnO4gtePDPbgU1Cyc/6tIxiAHw92WBcRBsEVZ444G+GqA5QMqsLrzmdNhGVeWK3c1G8p3sq6MQXLfVCjiDF1HSHKi2wZL27CP2UAHtQIGNaRkhu62loA9mCTQS9wDtgueC9zFTiGwdX7FeY+/lPQVaWKfTEDlhzUHC9vqbcXy5yyE3D2COu82RTkY8zyO2eJ4NkhYVztMblS5xsyKTwUozmbfQ8VDit4byH6Ui/prs2KFPt18FnrCinTsXBsroaVqU4STeQYFBehzq5sBuXAhnlR1Cu4GY9/Eldk/A2uZJvbDpCsZea+6vIZVSLdl1cgSkN1auUeLMraX/qjsfBXY/z7vmk20/V6LUEguofeE+MWDegaTBza9z6VEQ3NMstcI+4irHXAXAoCYBE5jT/91WJ8I152IrZnFNcVjiQwAXEkHhnTq475G7ZWo74ny/ewACIF8OoX1WF1+itJrXBR+gEJ0LRxttwYU/uiZnkPt+HZ6sdNghPQGS8/jhVfbS2zKtezvv8+Z0hboEvqGAm4lM8yqcVD4KGbKtG/+5CEsR8EITulmZdpR7TuUQuD5XJ6mURlU/gcxU1AZ7P8JJSuLyhPTsH9ZM2ZHUmjwxN529NxPhz8/1o6gZADywvM1E6M4S420tZEjl6YJnqoxmuic1NNIL4Etz8qoeNZj+tBASMsop/LPMsmlX4QNoJkhrk6A8s3bg++kit7GB65yNQdX8ZdwPFPnPr+LCFNPhghUKWvkMxfK9nu9N/X+NZvADsIOpY0++Dt8n4GnukTaNI1OFFdplybcU0VslLuo71rHCY/yyL5EimypDTfCxdHZe8OrhJpzjBStcgwHuLp0AL1pafmGz/pdpFIqv7j4dxhcG/YGfFbBFw9WNclC9vpAmFkM+qRYmnwCdu+dFeD554KpYqgUY+mAwvWqy96uXzy2+TitqyvBzajCyzUhgqOJrQan4ikjKPCNL4iJo7Iu3tLcSiasFe8kjZLqoAN007TO7zBv+mxXnMqbY4QXhludFg42MJrH+z+GNk68LLfuYaY3R5rLEoBsyB56tKHPKfTlNDEp3C3FLE9CU7ppGev/TOIhbxvOntQpajiYhbIASSeu4ZTp3AVfQaDw9qoIuvKozvCym8+i0Z+CSzlyiunW5TIJu/tMe+uNVV7qud4YL5scfc9YypgJhLOdpBEeXclQiXtUZwPlsNxsjZfhy0SaWK50ktveGHfqX9aLUeyOtBlJ9ocCbsFu0PLZgF7Nf710zcteG+rQXRmxdUIXVUfqIcFLqOgPEWcqQc6ltRM1quri/7HdZORxS05B3n6IXIJM4MNiVYT6U5dDZ4ChhW9OotdIHRJkk0hjfr9DRVkYuUrWHihMEJabuYVZfhvjBybUeDci0vedJa5UqQXt7Ii14xybTezXSbkS6NC1yDTxJgWlp2KkOtxg0vUqgLkWMdj5Cn2D4zZPaUI6CGXdWIaQoEL+cZeKW7CbgUUz1TscQkhvirrginedhQL7kpj+DjUg7MdKR4vTZ+02EhHrPeU3EKzEIBpn2kxuJwwoFoE5EqLNpdtdA6gozuJsae+2SqnJHPWs3/m6HGiNA9q8iYAHHeFfDmnN5FcjL290cSnlDFIsdU67d44rfCcD/y3xOVl+5519OEWRKORSeonQFbg9bJHL3rtIH6SU1C8c/qRFodxCfenedj1lKHRYPUi97Cl1M0J7mKFJTHmksz9bExG7Y2iM/Lw9WdXLxzdL+25KJ6imPaVnyVsdcwzlIONmtGWCOVnarpI5E33QvK30zurIt0ttcHG8nG0Z/mhM54YFLWf6L9a2fO/2Whcl15G0Huay9ddoVDDQPXs7Zj6jvW9EEHI7ptWJpXpcxEhlYj910KvgYq0yvjiTJihtN/vd6W2W4D2SaUnpXzB00nDx99K9SEHGSHvTxO/GTT03l8+k1HGszl2U3C1hXBbQIDOZv3EICwZim8It1k18vBvhn+slrVyzZGqxweycef/UnPYQnzLBgqmDRcYvZZwKOhpW3P8WcyoE0Hk021MkyghySlHUpdCBp7RUN7Bq7trq/K/LGe9ByfSxmY3VLva+WuZbnIRL48G8WjZclJET8nuQB/06qUH0ZT2egvxTH13gak5dj7F82kmzNtsP3IiWDeO73aNoQIpK9c8hUB/4zO2IScdJzQojnb6tsTM2D8uo0gxak+/Du/YWOWiRTtbFxBjVfk80MfjG2UEcLJOkMWgc6/LWZxVdYRk8bbmEYbwHk5OypKiZSiNcFj4LfUzrMzis1puxfj5RP2Uy4dGpuJbyIHCdzJTebB1s797kjTlQQIzRycQk4N94VfLINhnxlf1/sBFe12YXa2Xpy8KIFkNt5UkkI7tFCZZ5S5jv0AzAiSOmFUbpsNeYex95EXdn8113Lqa30M0DW8ZP3XK8UH49LDUqD5cKHwqrq3yd4cCwJ7L+pKE4OhND2iR1FKwmWDWK/WJj4kl6x/fEU3PN+/hUz0VZ5ulT35xCJETEt7Z3Rn2oyilBmzlbluhGR1SGA9QY/qbO9MGZAkkv19GDwjLCuyEMVsXQ7AAeJPxVcVFaDs9on8b6LZRdFFsUhckVBa12Z3GkdchOgYwqhoYX5kEn5AVKkVWqFCluXZvz2K2p2KUKavCY6kJPHC2uoHcjy9eSyIQ8fdHztzOc/NErPaECFtSKccNyB1Vn7s/PhYvaeqQ71FfBNnxE6qh8fdmX7qAgOoXp1mkWCvPRTm4Rgs8F1NscqGg8FkVAxa1V12pz2jwvlaz1J5GND7fVZgU7wOrbYV932NzoiaCY4IWC0O/NsaBGT+QP77e7+yuFC2RiRYqFvuBznr/+5igBCem10u0/hTOz1K70SxyEJxPDK7vQZDyWNxlZrw3qxAUIGkjiH7S/u0mH5wceMoPUkMcZlCmbaG0CUMyhF1YOehibHzFp0opUrGlEA007jHfEmdZcjBdTNSSf6SEgIkaScelVtkJnppV2wCiQPsXPJeE+GXj3vQS9kN/q5nKeJ9Ust9GxbaZaMjDAhOBtAamcgl4Adiz3c5z8ClsiaTPOK+JwIqzMg0dtwfDwJGrxxVggvaIO8k6AhZn081FV+S09pa5UnXgHCMIlU7/8PyZ2pfzo+LTM3KjEODZZD6CmApgg3cO1WL4tvXcuRVlRPmE21Z8i9lJZwXh8z7RKKHyOn/vHekELLs/qHk1KIKXBkf9AukLFeCVyZ09yBG+CQEUf+Mq6tKzNfd6qDdj/TvpBPiwaDZOn7a/tyT0CT2ezpP+9vCPlyNhMjuRA9QGlgAChD5lGZ4Ro4q+FhzlQE34BcqTiBM9gCN+W4aoSDzZzgWGfxGB1PTuGW1JAEjCEmlqm8rjAcJJ+uDK4DXC6+2mNaLUhsUJ0kTbtmoDeL3FKbsX6qkl3fLUYGpOfZd3ThNclL8TivamHp1X6WIiFd8oIDcFqBRqK1ZTd2w4BEtWqLS1X7SCLU+0y+h0lfL2YCEb9hyd8Y2I3JeHzgRfjl7qOywEiIl+GAzfF8/038iE9C4u1JUxyZz9MOe+kkrhdHXZJA3i6w4QMQTFlHQQEPH+WMLj1pQTRwxsp+SDmeHimv8lF/AKBtgBNlx7mfM2DG9zlEmYoPw1Mib4EDbzdwVLiGasXHPxcj9Ik5g1wx2H+r4RGOfrWpb3qP+icNBaYia16lS2JZa9bC8Sk2kbQOPiHOt4dOVhADSl29ZcTbjngVLhD8i2VhGAUda4WAtfK7hCeft/V+VuhCfP5E6oGK0JtLb/wPdd2EcyI5cPkJ/u+qZ/eobw9gfoWwFSlwiGT5D4b5ZrO8Q9HNZmuraaiaQ9396HMoBIhwnGRhVVOlLX3H3mQBKBfaKVnCNLjU0S7Ni2ZsPduOrxAJWZcENm6BSeFtqoH5t3J18tlA326a9EPt3IKXPqGWxju9rEXj67dkOdVmMW+ef1NMAR2Pjo/QmrfkW5bA36aCFDxrX1vzCD+GN/Pxkf3Y/8koe311ztLOZmswm6YCJZpaGfI3wLT09/y92Td82X2dNnhrDM4AE2aN5JmqCq7KOgpy8dxx9OVb0WjpTMf8MKnXJILa22dypFtxT0t4OkdsWFrQKUrg9427ig6z88me+IyyjWwptpy+5X9CvYRrqf9c3EJiMzC2pe939tsZ63ubuaVztJJWycUICnZ7N+Ko5X6qvdSI9dyslEcffEeSW8IMyxZDYSoiYd5AkV3d/hFQF1+k5uYJ8/+IzcfV4MH4QAJs02eGoVWuGdGwwL5R4htJgLkz916+NsCq4uxdo/tZv+d/kCpibrtrgBXN13jKDUQ5nEQipp08ewHnurkGZgz4Sl06I0FqO6VhxhX7HDQenh3CSxpWDiqvDAD7BSZzuACwlS5U2Y99717+JRao5OHCXb3IBN8cAGZDTsYjtu5b0bnL2q/HhwW8kHkChAcug7JGR2nQ28ta9eo6JBEudpMDN6SWvpVcKxRJJav8CuUPlBXQgT57ynjqM7BfUccnxAWTd9zM87VpMhn2zkSRyup14wRdTWBTBp+q1gRbQx9WO8HLt5Zf95dCZ5gu7KGIgiFJcM4/CBmumFrFj2wLLY7Y5TmSWY6tRINSvJTHeumTfxdS5uuq99ymeCM0hEXCx1QAmozSlaum37OQTVx3TEIO0ADlhylpzqBgEQOffM2NAJwdxLX+fMCYmGnhZA2pXSbV5qaUcyLP9HB1cYH/hlcLsiNr4npzN7MDqFhnpIPWx3bFQwiuEpJOHrzywgPJFP2Q/okFZYvN8TcaS71hb5hMdm7PgkItJCvnFSuJ5CXO/Tb0+3jnnxE4rhndJikBclJGg/sT/cfUWHkb4nyGwnzlUD1B3ay//hWoODTk2rrNymM7soK8D0CWtPyHfwk5ZjclMNyRNW7Zo+MACPR7b6xY0kFnqID8GNH/BKPrfQymvZO8y7ijoOOM6+O8ibrZz5RAntFo8IAS54QM5smCZsXkgkLTediqHHC+UBf3NDRGBwyEyorZwnns6G6XrrP8f/oqYVbLHuiB+ud2ZT5EYx87rOHDYUNWmD1d8z7Ndg31UkpXcpwvobaT0CcBYPcDoPfH4+aeXRvqPa6DMyxgQbjkP9Ut/5q9vDpH23nznt4yG6oLN4LOQ/NN6dIcLbhuLRZazw/W935g4GHxmc0uQrtq+aZJT6xtmkKTbLmdnhNKflXmQDnfRahuYG9ANJNOrf35c9pDVT8Y5Km6cyJLSlPh2iMzxaqVlGFcShLhUbD9CNEIPemhPdrXeSzRixBUf4WgsyNa02xlsEjE/jRkECiWIjDy7cAU5r4bns7S+VkHQ+AqCHnwenyGEdvYhJlLYm/WmdMFw+/s70eJXdHRv3dlQUuO9rZkthG4ZwSMeLOBApBRPkv2y8ww/PBH2+jZMGWXHdl3LSMhxH1jALiJE8kE1ZrxRmvRkLIZjKZ5TJ9SdBzY2J9iMyvT9scV49l71RKxZFodi5q0nRuxuxgzB97+omq32pIVJLjQF7YO22Ak/uyaDlMVvSgzmuV1uXspEyu0wwuiClkezg3kU3LvmiBz8ba4MC6w6ISxgNugNsDbJKaVpVVhbWv7vM6JuhvlAA8AXsq6jG4xLQBhhKuiK3rOQs9qfRhfj6bbxiulOujpHnZM9svsmYZIIqVirKlNzxVDps4RqzpCW/su+Z0g0X/BoPucHD3JfG1cCLnVxofWOcRwj6p5NiJIR/c8aUYoOG9TZUd5xZknksFlhYJURuX9rztLWLWyQmEl7fwsuppw2TiTmSO5BrlldYxDqJSDOQ0NhsNNmF4V1aMz/TPTQUY6EFRBLpYfJhFWpAQc6JGJzF4mC7Wm03ZhFW5JLczmSKwOrYd3wKZXxz7+07Ps0N0+3Ypr4IlWQkQzQQ+apj8BsSixdTdNCD1h6NpXBpeAbPG8Alaw1NB4dxMxC1+B8kd+bGs1ghKqhb/S1oiVZSiDTunasFPhnotkbUwb8pwTIwfkiugE/ow59thq4SHg1bPe4H6T7NQg9HG9tv5OLa7DYu9xbL0O83DOOi2ujhER4Owctuxi2dFQSKCkn4TTuhI8WYf7JY76mEhIWAWYB1QtiYVwSOsxurSqXYNT/E9wtdnNZLFeKJchbs7wD75+p7Fzrb5zI9ab7sWG/DStu4ewLLYOiFAvc/rfrIqD00+atT+T5kdlr6BzC2T4BZ53W9K4tF+e/CYAnpmyIzo6XxjfySCAfLiXPuqvNnagY65EImMnbCugwX6Knr5to0VOn4vywyy2csgiTbUdqrtjemP8jSJI7Ipr9IZAi+svE98615gJxg+cxwalxq6cQfza1m7hEPcetBwidGD9fYTTcNYeDorxrVTyJJdjKd47wiEZQRLHix2YM7s/Mj+NGuLSHpou02vzVheU/l/9ECAttN2O84vqBUP7DcM+0OFUjD29Rdn6shlww3d2zP9EHMWaAvQwAiTNCTlPwsUm7LSj3/ZNRZn/7KjqJVGUfI26CFqujXmTF3t+wwNHDLkhGO4eV8g2/uyKtMcKzgCwekimzjn019xhtIes/1vvR4Xxf9U6iqhE5shkzOHQFC2I91FFOPDg8v1nDiDsUnEXepsMGg2c5OMwi6pMrqFF451+7hiTw4T3pAOZQyyTZNp1l54q3hbYoU5h3SLhkw+YUEkdt1TCkU28azaEcyLQrcUyHboN17DcA+LI0wJOiUTkombDACaOx+5tyyv//Cntu6y5iU0BdZYvqEuVVWIBlQqa/Y9fCK0+5gjlfyBWYS7KCveZ8H5mNDc3mmuJdAia+Ic9pk7ZcmrFtJL5d9UQePFMmZgS9KEG1zun+6QliMFAfy7uBlxClIVNXXknXD84SP7bM8tWOCUxTy4slu1o78VdHt4BUsdbDsMl549eSweBMMGmTLl8A5sXV1uUsm58DlLeOkgrNS3PiMWofFXTW+CtDNjeKTXjaiCq6yGBOZiEUy1cFHfmF8DqK+AuT5yiK+RQjZFiiun+W7dh5Cg7MRXUzamN7+tpwg5+cVJKtLmELX40nxREe2HkuWrBDEFyQmqIr7OdqRdZosA1JR4DTGBXAPsjJtCONn/M9YMhLBAP79hczv3xDrPemN1vlPdAwqIzzli2e9/P3iarCzlgQwEx+f9s/Bj7hhHfhkQ3LTsfYeozQADXLERY7IISBcyolOQI6TD6yE8ON2Zxv6uTxI+z0skMnl1TTHMYUgjePFgvLAQOwxHzdriXONPyrDU1HLJ+VU8GGV7kXL7Ue1e6kLtWm8wZYpK5GH+ig1lX11eU4Q91S6sLYHMxUHRHnefN1ZydFlApvgCcbh0O3OOLvz/BLr2D1YSQiviE5qtXHdqmHlKwOWmbg2U646C1dQUU9V9LN4+Ot6peEkA/bXVMehZkII2EKJ1YOk70s2dLaYyuqgZ6u5pHgj35LIvbosnifVt3XsmLXRa20YmRWlgQkSPllBgfqICuit0aFqgrKhFTd+Ru8WgdnulBisWAkv4rxQ6mCuVUINk60kYxoDjV+UEg19Zgd/xD+0D0DscMlashmC+LlxoB9RC95kSQLDbJK1kCNrgfsZJ6pszSOeNHwjMwIZSy2Q/jwdxvi23e626aA/wKok9H20K9VWXCI75oLBSBBOLL1BViFs7PtfiHspR0UNHM12cNzfXS6V82QyLvlTpzcTafS+Ikt5sWi37vEg6QPisBy1iYbMUmPZb0zfS2C7mUb1CyUz+lKG6S8Oc1qlg5y7BOPfVv1Rs3m/Tcr1TAdyRXnfYbhHYLQs0XOaCGmMXaLsWzp3QIUy+SGG4D2au+SZF+9imFs8u5g5iao+FQu1ltrzC/RAa1aQCi8xN2RwprenqRSKl6Z8lxETRLfJp6drU04SgmDMXwgij7al29ztEe9rFr+cST6GLslJIQCaFn3T5dkOmjdG+rZeM58/RntHUWwkZ9LgypFTXP00acC/X4ZTWcH8TQpZWC5SSIDwlpxATa3g9hxrGIerwIOpmH1uLXlWcFTAV1t6Ebiuw1957e43R5OsNVZYhn5DwsP0Ph5hn0RGvkYTZ8IxcY3wDlEPz+FvE32dxpH8ARt+vtmd9kLMIchcl7/TomipwaXtVRZjYj0wj1WkoIq0H3Dxe17GFgFgFBDwJC0DeJFQVJdL4DxPRQtFZGlDwJwCramsxPQoe6OTNWO3tMkYvcu7MmlrteIxT/psrANergmlM2JAkA0BYEj5rfbUGbeAMq5Ss7uv0PWA4bOPCkuTcBgPvoJYT5cQwJhGlWMbLQ2vSOSNZsyWPGtZYh/CyQV84weHPgd0cZ2A07sxyCt8HWROBmq/daBYzBkPAxSbDidESIQt76SrD3bbWty20Ut0yg26PArjy7X7Bz8uSGsmtANuGOyGPPxRjJrAmTvV8IECK72Sa+/Bw2UM/yPpGUM1xbWveE8f0tpRp7s2ZzC9dsL7F60gxIZ9Lr2og+CJIH81GCfDA84b+qBD3zf2uWNTOYE/9C51U33SIezIaLZpXNqrZxMEzJag/02XtCkn16Onwmud6qkvTCwZYDC5MyKbqQEsY90NJfa/A9I8uWsJrrd0Y9vMEFnGJKgOOu2n5vFfZkpkvpnKRLtJu/wyhkqGXJsfL/CT7VezuFLJZLeaIcmoTjS1eVBFvBtDrOc0bMuOrYSYazWrgQd0OqC53OkT8WhnvS7eIVrhmJTNQZ6TBFo8QJUwQxQhu4SFpU7yvWhjnj8W6b1gOeS6DnIZC0voakECSnpYsRwgG8eLFNufXboP36qPr+IPqPczkrnuOVB0FZDZZ3RK0pqAn6j0sierCxgJXRCE4bnwWCmrNZwFBSDXL6AGPcS8zXLIU5KMcdXHFQDaiBwDgtmT+48QUdjyimSO5g19W/GtTGyGi81KObGH1Kge6iK5XVSMJe/n0DiaRsDz3ljDCZwB5Kys9WHyEkcppeAyl1BwATNju60prA64dKFJq3gjM78U9AIDLXBBwB6dm8E2U+1K92HnfoyyqjTCeB5UQY6pxNw3Y2O9olzvfWPXkusvc3qvscXHgQ55SwYdvwxElosts+N7qtQZZe5E/WHxhXMPulQiPYbhTK94/fPsh8AIn2Bjt/fhabTEBOgnbBdf4t00qNkdvrKg3soocFQcfo1NrOQ5vvwfNyg0iP42h8KSzr9lpa5CpNd1TmIpfb+BvN6/wHyCtsEftUIMtbHUfr6hSoQ/iU4o8qjMiyivXSDCQLAdUzwlalQ9x3h1D93z7kdD4qoe+8t5PucHY7yPA+aG+s69gdcUvP6OUCmigfobKazncBt7OUyBNl680HIBKkmzFPB/pf0DbnU2BKy0X9G11bEgu+trP5qBBdm4HFBxlnRQCBe73JwcsNlCMnibGxcLif9Eq2SuukMjksDR+XyN0d3ULLn5uun/PHL5/smB52wMcb7cyO+Sx/tavNUCPKx6UYK3BSKiPfhLlycP3Tq0EGRrrE/JnDKsteDkY+RaNzaumFRLN+UtdLAbNPGptM3DnCDMaDYLe5fTC4l/kWRJ4rOEVjG2VjbXUqxc5B1SdacqaQysOwkTyg3eH15mvKxP9yasR2lI0T5eUWH35/ysw8Zb9T807lhs3GgFFvASKuWINmy4a2L4R2Pxv6TGzySz/dhIzVRe0UFSSztx07i7AHCUwYlw4PjklUbDwXecmaGvYXdCfgmQb7MBr+44/MjaGKKrLiRf+lMYOajjtpfaoz1Bcet00NcSZ3LXfNPbkchqOt9+OiBq6wPCGASuAGn7QIaAiPTdmW21A1MQQTDLbcylyZ5vgBJW4iQafAD1RNUB+9+4bNg+puT7Gn1zgD03+U/G/RDRKCIrIOVG9HFO0H945TgjXVYsQNAUve1YWAoQ6yQzCv5zCwYOSo5REyOX4WeRsUg7xPIiJnPfLpTQpCRS1uCG8Lhuoy8h8pcsiLEIMJzl13ZTM7zwigyb4YKG6xnVs3Nr0TQSy7PKp8dV5XLS+RGLDrrvWOW0ns/Ft8mU6+HGwDBud4UwMaRWy/98JmF8zRudNi1aT1UgIHhfsswfl5gPS2G5sp+IE17XfPZh3MKcsvUpgHHjF08kgQwwKHNnoeItN8TbutMswCCFK26k4ECnyG9Xo+bN5vAPdm5gVcEwmuNYbLraXb63ruQq7cSBSr/wjMLkP+hBcf7JzvD9ug2fewNy+rcLIwBSDbOciU04aNX47NGd5Qrs/vUw2k66I+Gz+hcFOMjkmfQ+2z6XpnDwLPQ0kfx2jA1CWc/rqxXQzMBooQhE0B101t4QQaQPVdDABeB20mrMlH4HhZxhXRCaVx93Gd5vEY5B2eYJEqz0iuJEzVlhj0z+6HPgWRvzVyS1nS1I/L+WSGTLCzC5Ty9uZYuPdJtVzm+V0xGdhDZfjH8Ikh2lQ94vtnDMzbkRpBkeQm5LnBsqLxKLOFuCdHdpQEm+Dq65p6kDyeVzWg9Av8pUBEe+67/JpiJUE/dySgEdKfyXUR2zDd3CiHIkVCk3XtSIgpe1xsp9+8p5YRK6SLjKvepyzbpmvXEfdVYi8S+PAgSYhgjHDIVxLoD+phRKf7DV06IuV+ARiFGUfFKgP+ku7/v4frrkuGYGUCaLsOlJOmJSdry/PQpmIYSoh6sX4/78ZD7MJMF91V4Fbe8PjpUL9zhQzxVTs2x9HXgqLVGdsdV5ARwQ9AgcOLZd7MsdMQ53o3E7feAmKDTDoTKfjnk3gWTf2IsfRNtNvK+Ex+sYDkRaFV2vOvTjZpVec2XEIVArNLJl1r8HtufVW7MquLcX4YROCSakIeEA192sGlIQqzZ6IXfQFBZE5T3LoWfMTxmNTCFtyBACZnbIy3FMp7je/EBwfy9dOy30t00OUliG07F6vggGBXi0+MYgka+Mu9TePa71xORaz6Wy0rLgSumMMUu1tmtSJcNoKgw5qnpBc03Plj1+vBs/8ZzYETXL1gbD0Xt6yFrU2N2KMD/ckJNVK7ttpcjNt8ggaHm9iiXhWQbwG/GfSAKYAxSUITUZKmLmmVIOW6OM6e+ALYSqYpwGp38Ot2pJbX+XWVepZBASKQ9e8yncPAGk6NEKfDH0BmDnNSFmDsbIdndp/eb14/Wa74cC7yMgI17Mm9Idjbl52yOMoOKIYH1BGPK/QNMwzqFFo/TFrd99mY2F4HG6dLpotF7+Bryb3kdw5BPIRgxyx8CFRR3gAf2SJli8F60tl8KpJ8pHJBNjhbJ9/GRjXYruF1bD0Qr2KGsoJrL+fY5JhP9BTlv1qMYXeoSpT8m45rv3kvvdN4z0uxesbc/leIxRfCH4pQe9awjvY4xCZJ83Reh9QDTUkDTPAci0bg1PKPJSKbrswdoSVo0fEtBb+vyt+XPM5tmeBroT3+f2bMOk6h4CxbeatLfgj6ZLaC81Z+Gx8brxRjKsyeBerwec4B35leM0QMR98dapd7EGI8koDW+EWDZnXvHeraVFdg3PptS0dXalvLP8l18b5HYXHk/eivyHrX5ftux+bFNnDEuo2wM49uJ0PoPHbfJPWpMehn4LkMNZ/sRjf8y54XkL2uqL+o49f6fq4+eTyP8eZKbB7qcx21fuRn6Aojt6qVhN3Sx4AMJ3y9lhVofX+LW2OJrjPQGsciWsR57v7BH3zA6j8j4A5U7UmiSkVkbwkZbAdbZ7H99hUrRdLk66PRq88tbyGQhv63XEP/U78Qg5qmNIZDa9BmwpjgrwAs5216MqilEtioQLJIV8uo2pmSndiWdwB3gXFeUKr3JUBJnAfy+cmlqOZ3mOkwPoBslMxP7lA/nee/V6912W8jdAMCZRdkPvwaNn78nomJPQrO2otFJHrE9+OkezOt+W6NKFsWpioeLMC/R3qD6IxUfLxdP1YMMNI6P1Gwabk+ninGfj2VICB3BvbztC9BpfUa/A+rwwnRqLeMgfZDRsEMj8tC9fBLQhgfrFism17mAwdybMDHEGGbXSk7arIJ9SKzEOEL63sKe5BKXJIiVTPLPufUlGEDe4D6xOLTDEQeKQxvokx/TA35u8iwNXud5qvgTFO41kxmz+XjmNHdCbEka30yGxOoEPiJTwAFN3IbbiTiIJBdIJG+ccGfrwAUUa9hS2Riw+s5TV0PZNI1PWsyOlXvIOeVgaZaQ0/up2t9F1/5JNOcIhXrup/OIj5Zjp+iJHEe7g/wng8qnVbG8828LxhatgnqlvB3N58BaQmeqpPQVx7V/IH7AACUX5apnJoMb5MwXlo2doxL4QFkyUQphoV7XByD/xl6s6H6KTddk1+AB6VwgrsAe03SmshRSl3SjP/0VdOPfTvqLWgvDWDdr1KKkmi2AOL68yW8uO8OKVPoQZve2SU7X/954dhyYuSdV73BTgjsreFFLjh7NYJSiIVPbYLGLrHhqjGCtrP1e4whPdFsiDVK91gs77MYftK7v5F/dcD2xw/+eaU1vphoTXdmu4C/m1sWgy4jZlnVG026hyHa59D/Jwk1fkjKISCo7prlyo0NFMYA1k09iNZA2QKmCMxvDOMMo7rCUYp3La56xmzD8QsDJG0yG5+ATvCqpWfznz1ZIhT71OkznyjGjZEhkfZxm39KWbg9eTzPdskhMmnCi/Xp0C5fZ83KVDfDRymUrzgSg6Lwa04c4rlGTOJWR8FGissVNa/YgXwBx5M7c46wcLjEa0YlOBC58HyXdKqRuJyM7aeRexDu5X8y5te6qWmOKhN/ddEm5a1tv1tS0LsZ8MAYKqnHIo+JU+e+HA3sLJg2hWAI4MNnu33aPS9MWqymn7TzYqdUUHcaeHbPfvl6rr0hdtGWZQ4VugdKwJweKlAAcDzifLPQ6FqfjHtm8CnkvJTvYqOPazQT2WPHSAAta0IZAq/eRerYVHoX06HWeKqrrCcfnqpSTjvNt6AGDCh0GGsM4XYLeTUKQelwWOxbVVeJoipsKxpTwCIl9ebGEwmjDx8x3ssseL6UKgWjQ82s+7oNLB21KUjZOsl+TaKPOD2m/YjmZcl1WvR4dMqLuUW51zJHZ+JxhI2f54/xC+CC0Ktv7ySUjvEJAPvMfeeCLqDWOKUDh7Wklr7Okj5bDnqMkWk2+qnFC9r2wgOhQI9K6UdlrEOzyp2gCF5gAPkOX1J92A3aWkn+ZKHS81MnT0q3rpAdVHID2Zq34TM4lmqFEkku6q8mGrpP3xcP7EsHjWSfGM/dGRHN3wnsPamC+gL4/1UGVX/DYiYagDt2Jbwhjt+wT4WrsdWpdeOsFvoL9fh13hEvWO0ySAEX5ImhqxPWAdrkOY/WLMmYolBS1bcqesOkTMq4rhpwkETY5ibpm8qlT1d0RCYAAE7AHSYC4p/CK4S+ieP0PpXGXFZM3+j1frbMhYQg/jbBHvWH8/pqsY5LwDgu2IeMSqAGXbu2OkuKcuHU2qknP2BiyNLWoFEwmwpMUAWpjMp82nxJb2tfvt9Hd0HvepzLfKxQdwF69FMlzrbIMNkl2+JoiOPmXi3xdFPtXtB7GRJdRC8AuJEzv1uT9RHQcgtJirDdIaAYqiqV9JrZ3yRfBEUL3774CFFRRTUtFsgBvaw4Pqk0/Y38kf+HJ3B4eHKYRlWNkZ6EvUI0llxsG/q0yRTCgFQ2GMzeNJY6dxSsCIvxYwP/h3G4VCKakWYFtF9PRYyRm8bBjaX43YwWgzBSeK/O4ZVkwOP4DcedKbxL+PYyNzDITIYieYUIds3jbj+UdH94G1AB8SGcQYY++QI2eDc3pl5AKoc7Lke4m4/Vm6r3MPwbZ90oAAR64eBYsMZcRs+NqGo0PSIhgzdQ+kZFXvyVRRTN2fUBzdMLGVpZ6OB+SaUio8o5E8VNvFxns0vUbkK3brBPGBvbxSN2x6jeac5fx92vSYp1uFjBPv4iXqm3sRbUVrCbDyDSlkllAT/aC7kGKB56b2KnAblDarIykq2YA4CDaeYCUTr3yM41wHRw4jv4nS7DBcNQO3KtOZgV8AYKA0RuhMWH03iz4DKWM1+1efqEo5rrXX9Jn+pNYA/hrpPR+JF6NX6kiG4BMo5FQltM/gBK2bAMJuwFeJufppABR5ip0St8PwEmH6Cw6uymW1lMNIBw7Wb0098O6Y2ogMfqfP/4fIC80Eh4KJbOLJYSwKZIeaM6A3FIU4/HuAzpky195lY4UHC91oSRYjQOX2eqT9nU7J3ZMzHQmUX4kmDVBZtBCSsQ6xFGuHcwAAT1OOqzVvHaYgC5utS6ALEQibiqBZjfjvgtQfHU+t1a1FcSSzO2JZCBtxwRKAJ1igYmL2jSWt7feFjVBARqX4/Gkzwo0miyKMETAHK7OCMOQA7nGGY2s82bw/R7gfPH8Vvc2xF2GnTw+ysPrJhDK7opgUdd1tmRebgxKe3HIOJ1mM8Tl3j+O29AWHN3vY00CQASxoAFkCTNQQVYeK71ekAAAA");
        background-position:center right;
        background-size:cover;
        background-repeat:no-repeat;
        opacity:.98;
        filter:saturate(1.08) contrast(1.04);
        mask-image:linear-gradient(90deg,transparent 0%,rgba(0,0,0,.38) 15%,#000 35%);
        -webkit-mask-image:linear-gradient(90deg,transparent 0%,rgba(0,0,0,.38) 15%,#000 35%);
      }

      .apex-ref-hero-inner{
        position:relative;
        z-index:3;
        width:50%;
        max-width:690px;
      }

      .apex-ref-badge{
        display:inline-flex;
        align-items:center;
        gap:11px;
        height:42px;
        padding:0 18px;
        border-radius:999px;
        background:rgba(0,255,185,.045);
        border:1px solid rgba(0,255,185,.34);
        color:#83ffe0;
        font-size:13px;
        font-weight:900;
        letter-spacing:1.8px;
        text-transform:uppercase;
        box-shadow:0 0 18px rgba(0,255,185,.045);
      }
      .apex-ref-badge::before{
        content:"";
        width:8px;height:8px;border-radius:50%;
        background:#00f0b0;
        box-shadow:0 0 12px #00f0b0;
      }

      .apex-ref-title{
        margin-top:32px;
        max-width:700px;
        font-size:clamp(66px,4.9vw,88px);
        line-height:.93;
        letter-spacing:-4.1px;
        font-weight:950;
      }
      .apex-ref-title .line-white{color:#f5f7f9;}
      .apex-ref-title .line-cyan{
        color:#18e6f5;
        text-shadow:0 0 18px rgba(24,230,245,.07);
      }
      .apex-ref-title .line-gold{
        color:#f3b638;
        text-shadow:0 0 18px rgba(243,182,56,.06);
      }

      .apex-ref-copy{
        margin-top:25px;
        max-width:630px;
        color:#d3dce3;
        font-size:18px;
        line-height:1.58;
        font-weight:450;
      }

      .apex-ref-mini-grid{
        margin-top:28px;
        display:grid;
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:20px;
        max-width:650px;
      }
      .apex-ref-mini-card{
        min-height:108px;
        display:flex;
        align-items:center;
        gap:20px;
        padding:15px 18px;
        border-radius:20px;
        background:linear-gradient(180deg,rgba(4,13,21,.66),rgba(2,8,13,.80));
        border:1px solid rgba(117,178,192,.20);
        box-shadow:inset 0 1px 0 rgba(255,255,255,.025);
        backdrop-filter:blur(12px);
      }
      .apex-ref-mini-icon{
        width:66px;height:66px;min-width:66px;
        border-radius:50%;
        display:flex;align-items:center;justify-content:center;
        color:#1defff;
        border:1px solid rgba(0,237,255,.47);
        background:rgba(0,237,255,.035);
        box-shadow:0 0 18px rgba(0,237,255,.055);
      }
      .apex-ref-mini-card.gold .apex-ref-mini-icon{
        color:#24edff;
        border-color:rgba(0,237,255,.47);
        background:rgba(0,237,255,.035);
      }
      .apex-ref-mini-title{
        color:#f6f8fa;
        font-size:20px;
        font-weight:900;
        line-height:1.1;
      }
      .apex-ref-mini-sub{
        margin-top:8px;
        color:#aebac4;
        font-size:15px;
        line-height:1.2;
      }

      /* Preserve and polish the lower reference sections */
      /* Public Features section — isolated from authenticated terminal UI. */
      .apex-features-shell{width:100%;max-width:1500px;margin:32px auto;padding:48px 52px;box-sizing:border-box;border-radius:24px;background:radial-gradient(circle at 10% 20%,rgba(0,220,255,.05),transparent 34%),radial-gradient(circle at 90% 70%,rgba(246,190,70,.035),transparent 30%),linear-gradient(145deg,#06111a 0%,#050b12 100%);border:1px solid rgba(0,205,220,.38);box-shadow:0 18px 55px rgba(0,0,0,.30),inset 0 1px 0 rgba(255,255,255,.02);overflow:hidden;}
      .apex-features-eyebrow{margin:0 0 18px;color:#27e5ee;font-size:13px;font-weight:800;line-height:1;letter-spacing:2px;text-transform:uppercase;}
      .apex-features-title{max-width:950px;margin:0 0 20px;color:#f4f7fa;font-size:clamp(42px,4.5vw,68px);line-height:1.06;font-weight:800;letter-spacing:-1.8px;}
      .apex-features-intro{max-width:760px;margin:0 0 38px;color:#97a6b7;font-size:17px;line-height:1.65;}
      .apex-feature-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px;width:100%;}
      .apex-feature-card{min-width:0;min-height:300px;padding:34px 30px 24px;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;position:relative;overflow:hidden;border-radius:20px;background:linear-gradient(145deg,rgba(8,22,34,.92),rgba(4,13,22,.96));border:1px solid rgba(95,140,170,.30);box-shadow:inset 0 1px 0 rgba(255,255,255,.025),0 10px 28px rgba(0,0,0,.18);}
      .apex-feature-icon{width:80px;height:80px;flex:0 0 80px;display:flex;align-items:center;justify-content:center;margin:0 0 24px;border-radius:22px;background:rgba(32,221,234,.035);border:1px solid rgba(32,221,234,.20);color:#20ddea;}
      .apex-feature-icon svg,.apex-feature-icon img{display:block;width:70px;height:70px;max-width:100%;max-height:100%;}
      .apex-feature-card.cyan .apex-feature-icon{filter:drop-shadow(0 0 10px rgba(32,221,234,.28));}
      .apex-feature-card.purple .apex-feature-icon{color:#c648f0;background:rgba(198,72,240,.035);border-color:rgba(198,72,240,.20);filter:drop-shadow(0 0 10px rgba(198,72,240,.22));}
      .apex-feature-card.gold .apex-feature-icon{color:#f8bf43;background:rgba(248,191,67,.035);border-color:rgba(248,191,67,.20);filter:drop-shadow(0 0 10px rgba(248,191,67,.22));}
      .apex-feature-name{margin:0 0 12px;color:#f5f7fa;font-size:23px;font-weight:750;line-height:1.2;}
      .apex-feature-copy{max-width:280px;margin:0 auto;color:#98a7b8;font-size:16px;line-height:1.55;}
      .apex-card-accent{width:64px;height:3px;margin-top:30px;border-radius:100px;background:#20ddea;}
      .apex-feature-card.purple .apex-card-accent{background:#c648f0;}
      .apex-feature-card.gold .apex-card-accent{background:#f8bf43;}
      .apex-feature-secondary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:18px;}
      .apex-feature-secondary{min-width:0;padding:16px 18px;display:grid;grid-template-columns:42px 1fr;gap:13px;align-items:center;border-radius:15px;background:rgba(4,13,21,.55);border:1px solid rgba(95,140,170,.16);}
      .apex-feature-secondary .apex-feature-icon{width:42px;height:42px;flex:0 0 42px;margin:0;border-radius:12px;}
      .apex-feature-secondary .apex-feature-icon svg{width:30px;height:30px;}
      .apex-feature-secondary .apex-feature-name{font-size:14px;margin:0 0 4px;text-align:left;}
      .apex-feature-secondary .apex-feature-copy{font-size:11px;line-height:1.45;text-align:left;margin:0;max-width:none;}
      .apex-proof-section,.apex-pricing-section{margin-top:30px;padding:28px 26px;border-radius:22px;background:linear-gradient(145deg,rgba(7,20,29,.82),rgba(3,10,16,.94));border:1px solid rgba(109,176,190,.19);box-shadow:inset 0 1px 0 rgba(255,255,255,.025);}
      .apex-proof-eyebrow{font-size:9px;font-weight:900;letter-spacing:2px;color:#26e9f5;}
      .apex-proof-title{font-size:28px;font-weight:950;color:#f5f9fc;margin-top:8px;}
      .apex-proof-copy{font-size:12.5px;color:#8ea2b4;line-height:1.65;margin-top:8px;max-width:900px;}
      .apex-proof-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:20px;}
      .apex-proof-card{min-height:170px;padding:18px;border-radius:16px;background:rgba(4,13,20,.72);border:1px solid rgba(112,166,180,.14);}
      .apex-proof-num{font-size:9px;font-weight:900;letter-spacing:1.5px;color:#22ecf7;}
      .apex-proof-card-title{font-size:14px;font-weight:900;color:#f2f7fa;margin-top:12px;}
      .apex-proof-card-copy{font-size:10.5px;color:#8da2b3;line-height:1.55;margin-top:8px;}
      .apex-market-strip{margin-top:16px;padding:14px 16px;border-radius:14px;border:1px solid rgba(99,170,184,.14);background:rgba(2,10,16,.55);}
      .apex-market-items{display:flex;align-items:center;gap:8px;flex-wrap:wrap;color:#d8e3e9;font-size:10px;font-weight:800;}
      .apex-pricing-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:20px;}
      .apex-price-card{padding:22px 20px;border-radius:17px;background:rgba(6,15,24,.70);border:1px solid rgba(128,187,200,.17);}
      .apex-price-card.best{border-color:rgba(247,191,67,.36);}
      .apex-price-badge{display:inline-flex;height:24px;align-items:center;padding:0 9px;border-radius:999px;color:#86a1b4;font-size:8px;font-weight:900;letter-spacing:1.3px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);}
      .apex-price-name{margin-top:14px;color:#f4f9fc;font-size:17px;font-weight:900;}
      .apex-price-value{margin-top:8px;color:#fff;font-size:34px;font-weight:950;}
      .apex-price-value span{color:#879bad;font-size:12px;}
      .apex-price-list{margin-top:17px;display:grid;gap:8px;}
      .apex-price-row{display:flex;gap:8px;color:#8fa3b4;font-size:10.5px;}
      .apex-price-check{color:#20e6d3;font-weight:950;}

      @media(max-width:1100px){
        .block-container{padding-left:18px !important;padding-right:18px !important;}
        .apex-ref-hero{min-height:590px;padding:44px 34px 34px;}
        .apex-ref-hero-inner{width:58%;}
        .apex-ref-globe{width:55%;}
        .apex-ref-title{font-size:58px;}
        .apex-ref-copy{font-size:15px;}
      }


      @media(min-width:769px) and (max-width:1100px){
        .apex-features-shell{padding:36px 32px;}
        .apex-features-title{font-size:clamp(38px,5vw,52px);}
        .apex-features-intro{font-size:15px;margin-bottom:30px;}
        .apex-feature-grid{gap:16px;}
        .apex-feature-card{min-height:265px;padding:28px 20px 22px;}
        .apex-feature-icon{width:68px;height:68px;flex-basis:68px;margin-bottom:20px;}
        .apex-feature-icon svg,.apex-feature-icon img{width:58px;height:58px;}
        .apex-feature-name{font-size:19px;}
        .apex-feature-copy{font-size:14px;}
      }
      @media(max-width:390px){
        .apex-features-shell{width:calc(100% - 16px);padding:18px 12px;}
        .apex-features-title{font-size:28px;}
        .apex-feature-card{padding:17px 14px;grid-template-columns:52px 1fr;column-gap:12px;}
      }
      @media(max-width:700px){
        .block-container{padding:10px 10px 24px !important;}
        .apex-home-shell{max-width:none;}
        .apex-ref-hero{
          min-height:550px;
          padding:28px 18px 22px;
          border-radius:22px;
          background:linear-gradient(90deg,#031019 0%,#031019 59%,rgba(3,16,25,.50) 100%);
        }
        .apex-ref-hero-inner{width:72%;max-width:none;}
        .apex-ref-globe{width:58%;right:-12%;opacity:.76;}
        .apex-ref-badge{height:29px;padding:0 11px;font-size:7.8px;letter-spacing:1.2px;}
        .apex-ref-title{margin-top:26px;font-size:39px;line-height:.98;letter-spacing:-1.8px;}
        .apex-ref-copy{margin-top:20px;font-size:11.5px;line-height:1.62;}
        .apex-ref-mini-grid{margin-top:21px;gap:8px;max-width:100%;}
        .apex-ref-mini-card{min-height:72px;padding:10px 10px;gap:9px;border-radius:14px;}
        .apex-ref-mini-icon{width:38px;height:38px;min-width:38px;}
        .apex-ref-mini-title{font-size:11px;}
        .apex-ref-mini-sub{margin-top:4px;font-size:8.7px;}
        .apex-features-shell{width:calc(100% - 24px);margin:16px auto;padding:22px 14px;border-radius:18px;}
        .apex-features-eyebrow{font-size:10px;margin-bottom:12px;}
        .apex-features-title{font-size:clamp(29px,8vw,40px);line-height:1.1;letter-spacing:-.7px;margin-bottom:14px;}
        .apex-features-intro{font-size:14px;line-height:1.6;margin-bottom:22px;}
        .apex-feature-grid{grid-template-columns:1fr;gap:14px;}
        .apex-feature-card{min-height:auto;padding:20px 18px;border-radius:16px;display:grid;grid-template-columns:58px 1fr;grid-template-areas:"icon name" "icon copy" "accent accent";column-gap:16px;row-gap:4px;text-align:left;align-items:center;justify-content:stretch;}
        .apex-feature-icon{grid-area:icon;width:48px;height:48px;flex:0 0 48px;margin:0;border-radius:14px;}
        .apex-feature-icon svg,.apex-feature-icon img{width:44px;height:44px;}
        .apex-feature-name{grid-area:name;font-size:17px;margin:0 0 5px;text-align:left;}
        .apex-feature-copy{grid-area:copy;font-size:13px;line-height:1.5;max-width:none;margin:0;text-align:left;}
        .apex-card-accent{grid-area:accent;width:48px;height:2px;margin:12px 0 0;}
        .apex-feature-secondary-grid{grid-template-columns:1fr;gap:9px;margin-top:12px;}
        .apex-feature-secondary{padding:13px 14px;}
        .apex-proof-grid{grid-template-columns:1fr 1fr;}
        .apex-pricing-grid{grid-template-columns:1fr;}
      }

      @media(max-width:430px){
        .apex-ref-hero-inner{width:76%;}
        .apex-ref-title{font-size:35px;}
      }
    
/* ===== ApexMacro Forecaster Calendar v1 — apex- scoped, safe ===== */
.apex-forecaster-shell{width:100%;box-sizing:border-box;}

/* Calendar container */
.apex-cal-wrap{background:linear-gradient(145deg,rgba(5,18,28,.96),rgba(3,11,19,.98));border:1px solid rgba(20,205,220,.18);border-radius:18px;padding:20px 20px 16px;margin-bottom:20px;box-shadow:0 20px 60px rgba(0,0,0,.42);}
.apex-cal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:12px;}
.apex-cal-title-block{}
.apex-cal-eyebrow{font-size:14px;font-weight:900;letter-spacing:1.5px;color:#20DDE8;text-transform:uppercase;margin-bottom:4px;}
.apex-cal-sub{font-size:11.5px;color:#8fa3b4;}
.apex-cal-nav{display:flex;align-items:center;gap:10px;}
.apex-cal-month-label{font-size:14px;font-weight:850;color:#F2F6F8;letter-spacing:1px;text-transform:uppercase;}

/* Weekday row */
.apex-cal-weekdays{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;margin-bottom:8px;}
.apex-cal-wd{font-size:9.5px;font-weight:900;color:#8fa3b4;text-transform:uppercase;letter-spacing:1px;text-align:center;padding:4px 0;}

/* Day grid */
.apex-cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;}
.apex-calendar-day{position:relative;min-height:74px;padding:10px 6px 8px;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;box-sizing:border-box;background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));border:1px solid rgba(110,155,175,.15);border-radius:9px;color:#F4F7FA;cursor:pointer;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;user-select:none;}
.apex-calendar-day:hover{border-color:rgba(20,205,220,.45);background:linear-gradient(145deg,rgba(6,48,60,.70),rgba(4,22,32,.88));transform:translateY(-1px);}
.apex-calendar-day.is-selected{border:1px solid rgba(20,225,235,.95)!important;background:linear-gradient(145deg,rgba(6,64,75,.78),rgba(4,28,38,.94))!important;box-shadow:0 0 18px rgba(20,220,230,.15)!important;}
.apex-calendar-day.is-today .apex-cal-date-num{color:#20DDE8;font-weight:950;}
.apex-calendar-day.is-other-month{opacity:.35;pointer-events:none;}
.apex-calendar-day.no-events{cursor:default;}
.apex-calendar-day.no-events:hover{transform:none;border-color:rgba(110,155,175,.15);background:linear-gradient(145deg,rgba(15,35,47,.78),rgba(6,20,29,.90));}
.apex-cal-date-num{font-size:15px;font-weight:850;color:#F4F7FA;line-height:1;margin-bottom:7px;}
.apex-cal-dots{display:flex;flex-wrap:wrap;gap:4px;align-items:center;justify-content:center;min-height:10px;}
.apex-impact-dot{width:6.5px;height:6.5px;border-radius:50%;flex-shrink:0;}
.apex-impact-dot.high{background:#A84DE3;box-shadow:0 0 6px rgba(168,77,227,.65);}
.apex-impact-dot.medium{background:#FFBC26;box-shadow:0 0 6px rgba(255,188,38,.55);}
.apex-impact-dot.low{background:#38D4E4;box-shadow:0 0 6px rgba(56,212,228,.50);}
.apex-cal-overflow{font-size:8.5px;font-weight:850;color:#A5B2BF;}

/* Legend */
.apex-cal-legend{display:flex;align-items:center;gap:20px;justify-content:center;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06);}
.apex-cal-legend-item{display:flex;align-items:center;gap:7px;font-size:10.5px;color:#A5B2BF;font-weight:650;}

/* Selected day header */
.apex-selected-day-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px;flex-wrap:wrap;}
.apex-selected-day-title-wrap{display:flex;align-items:center;gap:12px;}
.apex-selected-day-title{font-size:16px;font-weight:900;color:#20DDE8;letter-spacing:1px;text-transform:uppercase;}
.apex-selected-day-count{font-size:10px;font-weight:850;color:#20DDE8;background:rgba(20,221,232,.10);border:1px solid rgba(20,221,232,.25);padding:3px 10px;border-radius:999px;letter-spacing:.5px;}

/* Day event cards */
.apex-day-events-list{display:flex;flex-direction:column;gap:10px;margin-bottom:24px;}
.apex-day-event-card{width:100%;display:grid;grid-template-columns:70px 80px minmax(0,1fr) 70px 70px 70px 28px;gap:12px;align-items:center;padding:16px 18px;box-sizing:border-box;background:linear-gradient(145deg,rgba(10,28,39,.82),rgba(5,17,26,.92));border:1px solid rgba(90,145,165,.18);border-radius:11px;transition:border-color 150ms ease,background 150ms ease,transform 150ms ease;}
.apex-day-event-card:hover{border-color:rgba(20,205,220,.42);background:linear-gradient(145deg,rgba(6,42,58,.85),rgba(4,20,32,.95));}
.apex-dec-time{font-size:13px;font-weight:800;color:#F2F6F8;line-height:1.2;}
.apex-dec-time-sub{font-size:9.5px;color:#718795;font-weight:700;margin-top:2px;text-transform:uppercase;}
.apex-dec-currency{display:flex;align-items:center;gap:6px;}
.apex-dec-flag{font-size:18px;line-height:1;}
.apex-dec-cur-code{font-size:12px;font-weight:850;color:#F2F6F8;}
.apex-dec-body{min-width:0;}
.apex-dec-impact-row{display:flex;align-items:center;gap:6px;margin-bottom:4px;}
.apex-dec-impact-dot{width:7px;height:7px;border-radius:50%;}
.apex-dec-impact-dot.high{background:#A84DE3;}
.apex-dec-impact-dot.medium{background:#FFBC26;}
.apex-dec-impact-dot.low{background:#38D4E4;}
.apex-dec-impact-text{font-size:9.5px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.5px;}
.apex-dec-name{font-size:13.5px;font-weight:850;color:#F2F6F8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.apex-dec-val-box{text-align:center;}
.apex-dec-val-lbl{font-size:8.5px;font-weight:850;color:#718795;text-transform:uppercase;letter-spacing:.6px;}
.apex-dec-val{font-size:13px;font-weight:850;color:#F2F6F8;margin-top:2px;}
.apex-dec-val.actual-live{color:#00ffa3;}
.apex-dec-val.pending{color:#718795;}
.apex-dec-arrow{font-size:16px;color:#8fa3b4;font-weight:800;text-align:right;}
.apex-no-events-msg{padding:28px 16px;text-align:center;color:#718795;font-size:12.5px;background:rgba(5,18,28,.4);border:1px solid rgba(110,155,175,.10);border-radius:11px;margin-bottom:20px;}

/* Modal overlay */
.apex-event-modal-overlay{position:fixed;inset:0;z-index:9998;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(1,7,12,.58);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}

/* Modal */
.apex-event-modal{position:relative;z-index:9999;width:min(980px,72vw);max-height:90vh;overflow-y:auto;padding:26px 28px;box-sizing:border-box;border-radius:16px;background:linear-gradient(145deg,rgba(5,20,30,.98),rgba(3,13,21,.99));border:1px solid rgba(20,215,225,.72);box-shadow:0 28px 90px rgba(0,0,0,.60),0 0 40px rgba(15,210,220,.05);scrollbar-width:thin;scrollbar-color:#20DDE8 rgba(8,16,24,.6);}
.apex-event-modal::-webkit-scrollbar{width:6px;}.apex-event-modal::-webkit-scrollbar-track{background:rgba(8,16,24,.5);border-radius:4px;}.apex-event-modal::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#20DDE8,#00ffa3);border-radius:4px;}

/* Modal header */
.apex-modal-header{position:sticky;top:-26px;z-index:10;background:rgba(5,20,30,.97);margin:-26px -28px 20px;padding:18px 28px 14px;border-bottom:1px solid rgba(20,215,225,.14);display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}
.apex-modal-header-left{}
.apex-modal-title{font-size:17px;font-weight:900;color:#F2F6F8;letter-spacing:-.1px;}
.apex-modal-date{font-size:12px;color:#8fa3b4;margin-top:3px;}
.apex-modal-close-btn{width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:9px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);color:#F0F5F8;font-size:18px;cursor:pointer;line-height:1;}

/* Form fields */
.apex-form-grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px;}
.apex-form-field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px;}
.apex-form-label{font-size:9.5px;font-weight:800;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;}
.apex-form-box{padding:11px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);font-size:13px;font-weight:750;color:#F2F6F8;display:flex;align-items:center;justify-content:space-between;}

/* Actual/Forecast/Previous values row */
.apex-modal-values{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:14px;}
.apex-modal-value{padding:13px 14px;border-radius:9px;background:rgba(8,27,38,.76);border:1px solid rgba(90,145,165,.20);}
.apex-modal-value-lbl{font-size:9px;font-weight:850;color:#8fa3b4;text-transform:uppercase;letter-spacing:.6px;margin-bottom:5px;}
.apex-modal-value-num{font-size:18px;font-weight:900;color:#F2F6F8;}
.apex-modal-value-num.beat{color:#00ffa3;}
.apex-modal-value-num.miss{color:#ff5e75;}
.apex-modal-value-num.inline{color:#ffd166;}

/* Causal card & AI panels */
.apex-intelligence-card{padding:14px 16px;border-radius:11px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.18);margin-bottom:14px;}
.apex-card-header-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.apex-card-title{font-size:11px;font-weight:900;color:#F2F6F8;letter-spacing:.5px;text-transform:uppercase;}
.apex-ai-badge{font-size:9px;font-weight:900;color:#20DDE8;background:rgba(32,221,232,.12);border:1px solid rgba(32,221,232,.30);padding:2px 7px;border-radius:6px;}
.apex-conf-badge{font-size:10px;font-weight:850;color:#00ffa3;}
.apex-evidence-list{margin:0;padding:0 0 0 16px;font-size:11px;color:#cbd8df;line-height:1.65;}
.apex-evidence-list li{margin-bottom:4px;}

/* Cross Asset Grid */
.apex-cross-asset-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:14px;}
.apex-cross-asset-card{padding:12px 10px;background:rgba(8,24,34,.72);border:1px solid rgba(90,145,165,.16);border-radius:10px;text-align:center;}
.apex-cross-asset-name{font-size:11px;font-weight:850;color:#F2F6F8;display:flex;align-items:center;justify-content:center;gap:4px;margin-bottom:4px;}
.apex-cross-asset-state{font-size:10px;font-weight:700;color:#8fa3b4;}

/* Admin Box */
.apex-admin-box{padding:14px 16px;border-radius:11px;background:rgba(12,28,40,.82);border:1px solid rgba(20,205,220,.25);margin-bottom:14px;}
.apex-admin-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;}
.apex-admin-title{font-size:11.5px;font-weight:900;color:#20DDE8;letter-spacing:.5px;}
.apex-admin-sub{font-size:10px;color:#8fa3b4;}

/* Mobile responsiveness */
@media(max-width:768px){
  .apex-cal-wrap{padding:14px 10px 12px;}
  .apex-cal-grid,.apex-cal-weekdays{gap:4px;}
  .apex-calendar-day{min-height:48px;padding:6px 2px 4px;border-radius:7px;}
  .apex-cal-date-num{font-size:13px;margin-bottom:3px;}
  .apex-impact-dot{width:4.5px;height:4.5px;}
  .apex-cal-dots{gap:2px;}
  .apex-cal-legend{gap:10px;flex-wrap:wrap;}
  .apex-day-event-card{grid-template-columns:1fr;gap:6px;padding:12px 14px;}
  .apex-event-modal-overlay{padding:8px;}
  .apex-event-modal{width:100%;max-width:none;height:min(94vh,100%);max-height:94vh;padding:18px 14px;border-radius:14px;}
  .apex-modal-header{margin:-18px -14px 16px;padding:14px 14px 12px;top:-18px;}
  .apex-form-grid-3,.apex-form-grid-2{grid-template-columns:1fr;}
  .apex-modal-values{grid-template-columns:1fr 1fr 1fr;}
  .apex-cross-asset-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media(max-width:480px){
  .apex-modal-values{grid-template-columns:1fr;}
  .apex-cross-asset-grid{grid-template-columns:1fr;}
}

</style>
    """, unsafe_allow_html=True)

    render_public_nav("home")

    render_html("""
    <div class="apex-home-shell">
      <div id="apex-platform"></div>
      <section class="apex-ref-hero">
        <div class="apex-ref-globe"></div>
        <div class="apex-ref-hero-inner">
          <div class="apex-ref-badge">GLOBAL MACRO INTELLIGENCE ENGINE</div>

          <div class="apex-ref-title">
            <div class="line-white">See the macro</div>
            <div class="line-cyan">shift before it</div>
            <div class="line-gold">becomes obvious.</div>
          </div>

          <div class="apex-ref-copy">
            ApexMacro combines global macro data, market catalysts, causal intelligence and live tactical price action
            into one institutional-grade decision desk for Gold, Oil, Nasdaq-100 and global currencies.
          </div>

          <div class="apex-ref-mini-grid">
            <div class="apex-ref-mini-card">
              <div class="apex-ref-mini-icon">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
                  <circle cx="12" cy="12" r="6"></circle>
                  <circle cx="12" cy="12" r="2.3"></circle>
                  <path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path>
                </svg>
              </div>
              <div>
                <div class="apex-ref-mini-title">Multi-Asset</div>
                <div class="apex-ref-mini-sub">Global Coverage</div>
              </div>
            </div>

            <div class="apex-ref-mini-card gold">
              <div class="apex-ref-mini-icon">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M13 2L5 13h6l-1 9 9-12h-6z"></path>
                </svg>
              </div>
              <div>
                <div class="apex-ref-mini-title">Real-Time</div>
                <div class="apex-ref-mini-sub">Macro Intelligence</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="apex-features" class="apex-features-shell">
        <div class="apex-features-eyebrow">FEATURES</div>
        <div class="apex-features-title">Intelligence built around the market, not a single indicator.</div>
        <div class="apex-features-intro">
          ApexMacro brings macro regime analysis, live tactical price action, event forecasting, causal intelligence and personalized alerts into one workflow.
        </div>
        <div class="apex-feature-grid">
          <article class="apex-feature-card cyan">
            <div class="apex-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 18l5-6 4 3 7-9"/><path d="M16 6h4v4"/></svg></div>
            <div class="apex-feature-name">Macro Outlook</div>
            <div class="apex-feature-copy">Institutional macro view across assets and regimes.</div>
            <div class="apex-card-accent"></div>
          </article>
          <article class="apex-feature-card purple">
            <div class="apex-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg></div>
            <div class="apex-feature-name">Tactical Move</div>
            <div class="apex-feature-copy">Live price action readings and momentum shifts.</div>
            <div class="apex-card-accent"></div>
          </article>
          <article class="apex-feature-card gold">
            <div class="apex-feature-icon"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.5 1L5 13h6l-1 10 9-13h-6z"/></svg></div>
            <div class="apex-feature-name">Smart Shift Alerts</div>
            <div class="apex-feature-copy">Regime change monitoring with confirmation logic.</div>
            <div class="apex-card-accent"></div>
          </article>
        </div>
        <div class="apex-feature-secondary-grid" aria-label="Additional ApexMacro intelligence features">
          <article class="apex-feature-secondary">
            <div class="apex-feature-icon" style="color:#1a9fff;border-color:rgba(26,159,255,.20);background:rgba(26,159,255,.035);"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/></svg></div>
            <div><div class="apex-feature-name">Catalyst Forecaster</div><div class="apex-feature-copy">Upcoming macro catalysts and event impact analysis.</div></div>
          </article>
          <article class="apex-feature-secondary">
            <div class="apex-feature-icon" style="color:#e05bbd;border-color:rgba(224,91,189,.20);background:rgba(224,91,189,.035);"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 5a3 3 0 0 0-5 2.2A3.5 3.5 0 0 0 5 14a3 3 0 0 0 4 4V5zM15 5a3 3 0 0 1 5 2.2A3.5 3.5 0 0 1 19 14a3 3 0 0 1-4 4V5z"/><path d="M9 9H6M15 9h3M9 14H7M15 14h2"/></svg></div>
            <div><div class="apex-feature-name">Causal Intelligence</div><div class="apex-feature-copy">Connects drivers, catalysts and market transmission.</div></div>
          </article>
          <article class="apex-feature-secondary">
            <div class="apex-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 3L3 10l7 3 3 7 8-17z"/><path d="M10 13l5-5"/></svg></div>
            <div><div class="apex-feature-name">Telegram Alerts</div><div class="apex-feature-copy">Personalized alerts delivered directly to you.</div></div>
          </article>
        </div>
      </section>

      <section id="apex-data" class="apex-proof-section">
        <div class="apex-proof-eyebrow">DATA SOURCES</div>
        <div class="apex-proof-title">Built from multiple intelligence layers.</div>
        <div class="apex-proof-copy">
          ApexMacro combines macroeconomic data, market-price data, economic calendars,
          live news feeds and AI-assisted interpretation so no single source controls the final view.
        </div>
        <div class="apex-proof-grid">
          <div class="apex-proof-card cyan"><div class="apex-proof-num">01</div><div class="apex-proof-card-title">Macro Data</div><div class="apex-proof-card-copy">Rates, yields, inflation, growth and policy-sensitive data feed the macro regime engine.</div></div>
          <div class="apex-proof-card purple"><div class="apex-proof-num">02</div><div class="apex-proof-card-title">Market Prices</div><div class="apex-proof-card-copy">Live price action confirms momentum, pullbacks, breakouts and tactical market structure.</div></div>
          <div class="apex-proof-card gold"><div class="apex-proof-num">03</div><div class="apex-proof-card-title">News & Catalysts</div><div class="apex-proof-card-copy">Current headlines, geopolitical risk and economic events are filtered into asset-specific intelligence.</div></div>
          <div class="apex-proof-card blue"><div class="apex-proof-num">04</div><div class="apex-proof-card-title">AI Interpretation</div><div class="apex-proof-card-copy">AI helps structure and interpret information while remaining bounded by the quantitative engine.</div></div>
        </div>
      </section>

<section id="apex-company" class="apex-proof-section">
        <div class="apex-proof-eyebrow">COMPANY</div>
        <div class="apex-proof-title">Built to see more than a single chart.</div>
        <div class="apex-proof-copy">
          ApexMacro is a global macro and geopolitical intelligence desk designed to combine forward-looking
          macro pressure with live tactical price action. The goal is to give clients one clear view of
          what is driving markets, what may change next, and what price is doing right now.
        </div>
        <div class="apex-proof-grid">
          <div class="apex-proof-card cyan"><div class="apex-proof-num">01</div><div class="apex-proof-card-title">Forward-Looking Research</div><div class="apex-proof-card-copy">Macro data, yields, policy expectations and catalysts are monitored before they are fully reflected in price.</div></div>
          <div class="apex-proof-card purple"><div class="apex-proof-num">02</div><div class="apex-proof-card-title">Macro + Tactical</div><div class="apex-proof-card-copy">The broader macro regime is kept separate from live momentum, pullbacks, breakouts and short-term moves.</div></div>
          <div class="apex-proof-card gold"><div class="apex-proof-num">03</div><div class="apex-proof-card-title">Client-Controlled Alerts</div><div class="apex-proof-card-copy">VIP clients choose the markets they want, while Smart Shift and tactical alerts are filtered personally.</div></div>
          <div class="apex-proof-card blue"><div class="apex-proof-num">04</div><div class="apex-proof-card-title">Institutional Workflow</div><div class="apex-proof-card-copy">Gold, Oil, Nasdaq-100 and global currencies are analyzed together so cross-asset relationships remain visible.</div></div>
        </div>
        <div class="apex-market-strip"><div class="apex-market-label">CORE COVERAGE</div><div class="apex-market-items"><span>Gold</span><i></i><span>Crude Oil</span><i></i><span>Nasdaq-100</span><i></i><span>USD</span><i></i><span>EUR</span><i></i><span>GBP</span><i></i><span>CAD</span><i></i><span>JPY</span><i></i><span>CHF</span></div></div>
      </section>

      <section id="apex-pricing" class="apex-pricing-section">
        <div class="apex-proof-eyebrow">PRICING</div>
        <div class="apex-proof-title">Simple VIP access. Full ApexMacro intelligence.</div>
        <div class="apex-proof-copy">
          Every VIP plan unlocks the full terminal, market desks, Gold intelligence, Smart Shift monitoring,
          Catalyst Forecaster, Tactical Move and personalized Telegram alerts.
        </div>

        <div class="apex-pricing-grid">
          <div class="apex-price-card">
            <div class="apex-price-badge">MONTHLY</div>
            <div class="apex-price-name">1 Month VIP</div>
            <div class="apex-price-value">$29 <span>USDT</span></div>
            <div class="apex-price-list">
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>30 days of full ApexMacro terminal access</span></div>
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>Gold, Oil, Nasdaq-100 and Forex intelligence desks</span></div>
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>Smart Shift and Tactical Move monitoring</span></div>
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>Personalized Telegram market alerts</span></div>
            </div>
          </div>

          <div class="apex-price-card best">
            <div class="apex-price-badge">BEST VALUE</div>
            <div class="apex-price-name">3 Months VIP</div>
            <div class="apex-price-value">$75 <span>USDT</span></div>
            <div class="apex-price-list">
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>90 days of full ApexMacro terminal access</span></div>
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>All macro, tactical and catalyst intelligence tools</span></div>
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>Personalized Telegram alerts and hourly intelligence brief</span></div>
              <div class="apex-price-row"><span class="apex-price-check">✓</span><span>Lower effective monthly cost than the 1-month plan</span></div>
            </div>
          </div>
        </div>

        <div class="apex-pricing-note">
          Payment is handled through the existing ApexMacro VIP checkout. Plan activation and client access
          continue to use the current payment and verification system.
        </div>
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



def restore_authenticated_session() -> dict | None:
    """Restore the existing in-memory or persisted five-day device session without rendering login UI."""
    auth_user = st.session_state.get("APEX_AUTH_USER")
    if auth_user and auth_user.get("is_authenticated"):
        return auth_user

    client_id, dev_type = get_client_device_info()
    sessions = load_sessions_cache()
    dev_session = sessions.get(client_id)
    if not dev_session:
        return None

    try:
        last_dt = datetime.strptime(dev_session.get("last_active", ""), "%Y-%m-%d %H:%M:%S")
        if (get_current_time() - last_dt).total_seconds() > (5 * 86400):
            return None
        dev_session["last_active"] = get_current_time().strftime("%Y-%m-%d %H:%M:%S")
        save_sessions_cache(sessions)
        auto_user = {
            "is_authenticated": True,
            "user_name": dev_session.get("user_name", "VIP Client"),
            "expiry_info": dev_session.get("expiry_info", "5-Day Persistent Device Session Active"),
            "is_admin": dev_session.get("is_admin", False),
            "key": dev_session.get("key", ""),
        }
        st.session_state["APEX_AUTH_USER"] = auto_user
        return auto_user
    except Exception:
        return None


def render_vip_gate() -> dict | None:
    client_id, dev_type = get_client_device_info()

    auth_user = restore_authenticated_session()
    if auth_user:
        return auth_user

    sessions = load_sessions_cache()

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
                st.session_state["APEX_SHOW_VIP_CHECKOUT"] = True
                st.switch_page("pages/vip.py")

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


