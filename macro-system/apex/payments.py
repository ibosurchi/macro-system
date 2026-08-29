"""USDT/TRC20 payment facade."""
from .production_core import (
    _tron_headers,
    _normalize_txid,
    _load_payment_records_unlocked,
    load_payment_records,
    _write_payment_records_unlocked,
    _payment_record_for_txid,
    _fetch_confirmed_usdt_transfer,
    verify_usdt_payment,
    _make_key_for_expiry,
    _activate_verified_payment,
    _send_vip_activation_telegram,
    _login_paid_client,
    render_payment_admin_summary,
    render_vip_checkout,
)
