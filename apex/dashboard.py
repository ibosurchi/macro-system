"""Authenticated Dashboard — UI presentation only.

ENGINE / BUSINESS LOGIC IS READ-ONLY.
All values come from existing production_core calculations.
No new strategies, no hard-coded metrics, no fake data.
"""
from __future__ import annotations

from html import escape
import streamlit as st
import plotly.graph_objects as go
from .. import production_core as core


# ─────────────────────────────────────────────
# Pure helpers (no engine changes)
# ─────────────────────────────────────────────

def _broad(score) -> str:
    if score is None:
        return "Unavailable"
    detailed, _, _ = core.bias_from_score(float(score))
    return core._broad_regime(detailed)


def _tone(label: str) -> str:
    s = str(label).lower()
    if any(x in s for x in ("bear", "risk-off", "tight", "low", "negative", "down", "slowing", "tightening")):
        return "negative"
    if any(x in s for x in ("bull", "risk-on", "strong", "positive", "up", "expanding", "easing")):
        return "positive"
    if any(x in s for x in ("mixed", "sticky", "elevated", "moderate", "cautious", "warning")):
        return "warning"
    return "neutral"


def _fmt(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "—"


def _latest_change(df):
    """Return (latest_value, pct_change, last_20_vals) from a FRED dataframe."""
    if df is None or df.empty:
        return None, None, []
    vals = [float(x) for x in df["value"].dropna().tolist()]
    if not vals:
        return None, None, []
    latest = vals[-1]
    ch = (latest / vals[-2] - 1) * 100 if len(vals) > 1 and vals[-2] else None
    return latest, ch, vals[-20:]


def _risk_label(broad: str) -> str:
    mapping = {"Bearish": "Risk-Off", "Bullish": "Risk-On", "Neutral": "Neutral"}
    return mapping.get(broad, broad)


def _impact_class(impact: str) -> str:
    il = str(impact).lower()
    if "high" in il:
        return "apex-impact-high"
    if "medium" in il:
        return "apex-impact-med"
    return "apex-impact-low"


def _currency_flag(currency: str) -> str:
    flags = {
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
        "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭",
        "CNY": "🇨🇳", "ALL": "🌐",
    }
    return flags.get(str(currency).upper(), "🌐")


def _asset_icon(name: str) -> str:
    n = name.lower()
    if "usd" in n or "dxy" in n or "dollar" in n:
        return "💵"
    if "gold" in n or "xau" in n:
        return "🥇"
    if "oil" in n or "wti" in n or "crude" in n or "brent" in n:
        return "🛢️"
    if "nasdaq" in n or "ndx" in n:
        return "📊"
    return "📈"


# ─────────────────────────────────────────────
# CSS — scoped to .apex-* classes only
# ─────────────────────────────────────────────

def _inject_dashboard_css() -> None:
    st.markdown("""
<style>
/* ── APEX DASHBOARD — SCOPED CSS ONLY ─────────────────────────────── */

/* CSS custom properties */
.apex-root {
  --apex-bg: #02080d;
  --apex-panel: #05141d;
  --apex-card: #071923;
  --apex-cyan: #27dce7;
  --apex-cyan-soft: rgba(39,220,231,0.10);
  --apex-border: rgba(70,145,165,0.20);
  --apex-text: #f3f6f8;
  --apex-muted: #94a2b0;
  --apex-positive: #1ddf91;
  --apex-negative: #ff554f;
  --apex-warning: #ffb21a;
  --apex-purple: #b54ee3;
}

/* ── SIDEBAR OVERRIDE ──────────────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(3,17,26,0.99), rgba(2,10,16,1)) !important;
  border-right: 1px solid rgba(30,200,215,0.20) !important;
}
[data-testid="stSidebar"] > div:first-child {
  padding-top: 16px !important;
}

/* Sidebar buttons */
[data-testid="stSidebar"] [data-testid="stButton"] button {
  min-height: 46px !important;
  border-radius: 10px !important;
  text-align: left !important;
  justify-content: flex-start !important;
  font-size: 13.5px !important;
  font-weight: 600 !important;
  box-shadow: none !important;
  margin: 2px 0 !important;
  letter-spacing: 0.1px;
  transition: border-color 0.15s ease;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: linear-gradient(90deg, rgba(20,210,225,0.15), rgba(20,210,225,0.04)) !important;
  border: 1px solid rgba(25,210,225,0.36) !important;
  color: #28dfe8 !important;
  box-shadow: inset 0 0 18px rgba(25,210,225,0.05) !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
  border: 1px solid transparent !important;
  color: #c2ccd4 !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
  border-color: rgba(70,145,165,0.22) !important;
  color: #dce8ee !important;
}

/* Desktop sidebar width */
@media (min-width: 1024px) {
  [data-testid="stSidebar"] {
    min-width: 240px !important;
    max-width: 240px !important;
    width: 240px !important;
  }
}
@media (min-width: 769px) and (max-width: 1100px) {
  [data-testid="stSidebar"] {
    min-width: 200px !important;
    max-width: 200px !important;
    width: 200px !important;
  }
}

/* ── APP BACKGROUND ────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(circle at 10% 0%, rgba(0,220,230,0.025), transparent 28%),
    #02080d !important;
}

/* ── MAIN CONTENT PADDING ──────────────────────────────────────────── */
.block-container {
  max-width: 1800px !important;
  padding: 24px 28px 36px !important;
}
@media (min-width: 769px) and (max-width: 1100px) {
  .block-container { padding: 20px !important; }
}
@media (max-width: 768px) {
  .block-container { padding: 14px 12px 28px !important; }
}

/* ── SIDEBAR BRAND ─────────────────────────────────────────────────── */
.apex-sidebar-brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 2px 4px 18px;
}
.apex-sidebar-logo {
  width: 44px;
  height: 44px;
  border-radius: 11px;
  border: 1px solid rgba(39,220,231,0.30);
  background: rgba(39,220,231,0.04);
  display: grid;
  place-items: center;
  color: #27dce7;
  font-size: 27px;
  font-weight: 950;
  font-style: italic;
  box-shadow: inset 0 0 18px rgba(39,220,231,0.06);
  flex-shrink: 0;
}
.apex-sidebar-brand-title {
  font-size: 17px;
  font-weight: 850;
  letter-spacing: 2px;
  color: #f5f7f9;
  line-height: 1.1;
}
.apex-sidebar-brand-subtitle {
  font-size: 10.5px;
  color: #27dce7;
  margin-top: 2px;
  letter-spacing: 0.3px;
}
.apex-sidebar-sep {
  height: 1px;
  background: rgba(80,145,165,0.14);
  margin: 0 0 10px;
}
.apex-sidebar-nav-label {
  font-size: 9.5px;
  letter-spacing: 0.7px;
  text-transform: uppercase;
  color: #5d7485;
  padding: 0 4px 6px;
  font-weight: 700;
}

/* ── SIDEBAR BOTTOM ────────────────────────────────────────────────── */
.apex-sidebar-bottom {
  margin-top: 20px;
  padding: 13px 14px;
  border: 1px solid rgba(70,145,165,0.18);
  border-radius: 12px;
  background: rgba(7,25,35,0.52);
}
.apex-side-meta {
  font-size: 10.5px;
  letter-spacing: 0.4px;
  color: #748895;
  text-transform: uppercase;
}
.apex-side-clock {
  font-size: 21px;
  font-weight: 800;
  color: #eef4f6;
  margin-top: 5px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.5px;
}
.apex-side-date {
  font-size: 10px;
  color: #899aa7;
  margin-top: 3px;
}

/* ── DASHBOARD HEADER ──────────────────────────────────────────────── */
.apex-dashboard-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 18px;
}
.apex-dashboard-title {
  font-size: 32px;
  font-weight: 800;
  line-height: 1.08;
  color: #f4f7f9;
  letter-spacing: -0.3px;
}
.apex-dashboard-subtitle {
  margin-top: 5px;
  font-size: 13.5px;
  color: #95a3b1;
}
.apex-user-area {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  margin-top: 4px;
}
.apex-user-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid rgba(70,145,165,0.22);
  border-radius: 999px;
  font-size: 11.5px;
  color: #c4d0d7;
  background: rgba(7,25,35,0.72);
  white-space: nowrap;
}
.apex-user-chip.admin {
  border-color: rgba(181,78,227,0.35);
  color: #d4a0f0;
  background: rgba(181,78,227,0.06);
}
.apex-user-chip.vip {
  border-color: rgba(255,178,26,0.30);
  color: #f0cc80;
  background: rgba(255,178,26,0.05);
}

/* ── SUMMARY METRICS GRID ──────────────────────────────────────────── */
.apex-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}
@media (min-width: 769px) and (max-width: 1100px) {
  .apex-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 768px) {
  .apex-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 9px;
  }
}
@media (max-width: 370px) {
  .apex-summary-grid {
    grid-template-columns: 1fr;
  }
}

/* ── SHARED GLASS CARD / PANEL ─────────────────────────────────────── */
.apex-summary-card,
.apex-panel {
  min-width: 0;
  box-sizing: border-box;
  background: linear-gradient(145deg, rgba(7,25,35,0.92), rgba(3,15,23,0.97));
  border: 1px solid rgba(90,145,165,0.20);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.018);
  border-radius: 12px;
  transition: border-color 0.18s ease, transform 0.14s ease;
}
@media (min-width: 1024px) {
  .apex-summary-card:hover,
  .apex-panel:hover {
    border-color: rgba(35,210,220,0.30);
    transform: translateY(-1px);
  }
}

/* Summary card specifics */
.apex-summary-card {
  min-height: 112px;
  padding: 15px 16px;
  position: relative;
  overflow: hidden;
}
.apex-summary-card-icon {
  position: absolute;
  top: 14px;
  right: 14px;
  font-size: 20px;
  opacity: 0.45;
}
.apex-kicker {
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: 0.7px;
  color: #a9b4bd;
  text-transform: uppercase;
}
.apex-metric {
  font-size: 26px;
  font-weight: 850;
  color: #f3f6f8;
  margin-top: 8px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.apex-metric.negative { color: #ff554f !important; }
.apex-metric.positive { color: #1ddf91 !important; }
.apex-metric.warning  { color: #ffb21a !important; }
.apex-meta {
  font-size: 11px;
  color: #94a2b0;
  margin-top: 5px;
  line-height: 1.35;
}
.apex-meta.positive { color: #1ddf91; }
.apex-meta.negative { color: #ff554f; }

/* Summary card mini sparkline area */
.apex-card-spark {
  position: absolute;
  bottom: 10px;
  right: 10px;
  opacity: 0.55;
  pointer-events: none;
}

/* ── MIDDLE PANELS GRID ────────────────────────────────────────────── */
.apex-middle-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 14px;
  margin-bottom: 14px;
}
@media (max-width: 1023px) {
  .apex-middle-grid {
    grid-template-columns: 1fr;
  }
}

/* ── LOWER PANELS GRID ─────────────────────────────────────────────── */
.apex-lower-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.65fr);
  gap: 14px;
  margin-bottom: 14px;
}
@media (max-width: 1023px) {
  .apex-lower-grid {
    grid-template-columns: 1fr;
  }
}

/* ── PANEL INTERNALS ───────────────────────────────────────────────── */
.apex-panel {
  padding: 18px 19px;
}
.apex-panel-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 13px;
  gap: 10px;
}
.apex-panel-title {
  font-size: 15.5px;
  font-weight: 780;
  color: #f3f6f8;
  display: flex;
  align-items: center;
  gap: 7px;
}
.apex-panel-title-icon {
  font-size: 14px;
  opacity: 0.7;
}
.apex-panel-link {
  font-size: 11.5px;
  color: #27dce7;
  cursor: pointer;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 3px;
  opacity: 0.85;
  text-decoration: none;
}
.apex-panel-link:hover { opacity: 1; }

/* ── GLOBAL MACRO REGIME ───────────────────────────────────────────── */
.apex-regime-row {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 52px;
  border-bottom: 1px solid rgba(90,145,165,0.10);
}
.apex-regime-row:last-child { border-bottom: 0; }
.apex-regime-icon {
  font-size: 16px;
  text-align: center;
}
.apex-regime-name {
  font-size: 12.5px;
  font-weight: 700;
  color: #e7edf1;
  line-height: 1.2;
}
.apex-regime-sub {
  font-size: 10px;
  color: #94a2b0;
  margin-top: 2px;
}
.apex-pill {
  font-size: 10px;
  padding: 5px 10px;
  border-radius: 7px;
  border: 1px solid rgba(90,145,165,0.20);
  color: #b8c3cb;
  background: rgba(255,255,255,0.025);
  font-weight: 600;
  white-space: nowrap;
}
.apex-pill.negative {
  border-color: rgba(255,85,79,0.30);
  background: rgba(255,85,79,0.08);
  color: #ff7b77;
}
.apex-pill.positive {
  border-color: rgba(29,223,145,0.30);
  background: rgba(29,223,145,0.08);
  color: #1ddf91;
}
.apex-pill.warning {
  border-color: rgba(255,178,26,0.30);
  background: rgba(255,178,26,0.08);
  color: #ffb21a;
}

/* ── MARKET SNAPSHOT ───────────────────────────────────────────────── */
.apex-market-table { width: 100%; }
.apex-market-head {
  display: grid;
  grid-template-columns: minmax(0,1.6fr) minmax(72px,0.8fr) minmax(66px,0.6fr) minmax(86px,0.8fr);
  gap: 8px;
  align-items: center;
  font-size: 9px;
  text-transform: uppercase;
  color: #5d7485;
  letter-spacing: 0.5px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(90,145,165,0.15);
  font-weight: 700;
}
.apex-market-row {
  display: grid;
  grid-template-columns: minmax(0,1.6fr) minmax(72px,0.8fr) minmax(66px,0.6fr) minmax(86px,0.8fr);
  gap: 8px;
  align-items: center;
  min-height: 50px;
  border-bottom: 1px solid rgba(90,145,165,0.08);
  font-size: 12px;
}
.apex-market-row:last-child { border-bottom: 0; }
.apex-asset-cell { display: flex; align-items: center; gap: 8px; min-width: 0; }
.apex-asset-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(39,220,231,0.07);
  border: 1px solid rgba(39,220,231,0.14);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  flex-shrink: 0;
}
.apex-asset-name {
  font-weight: 700;
  color: #e8eef2;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.apex-price {
  font-weight: 600;
  color: #d4dde3;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}
.apex-change {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}
.apex-change.positive { color: #1ddf91; }
.apex-change.negative { color: #ff554f; }
.apex-change.neutral  { color: #94a2b0; }
.apex-spark { overflow: hidden; }
.apex-market-source {
  font-size: 9.5px;
  color: #5d7485;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(90,145,165,0.08);
}

/* Mobile: market rows become 2-col compact cards */
@media (max-width: 768px) {
  .apex-market-head { display: none; }
  .apex-market-row {
    grid-template-columns: minmax(0,1fr) auto;
    grid-template-areas: 'asset price' 'change spark';
    padding: 10px 0;
    gap: 5px 10px;
    min-height: auto;
  }
  .apex-market-row .apex-asset-cell { grid-area: asset; }
  .apex-market-row .apex-price      { grid-area: price; text-align: right; }
  .apex-market-row .apex-change     { grid-area: change; }
  .apex-market-row .apex-spark      { grid-area: spark; text-align: right; }
}

/* ── MARKET SENTIMENT ──────────────────────────────────────────────── */
.apex-sent-label-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 2px;
}
.apex-sent-big {
  font-size: 27px;
  font-weight: 850;
  color: #f3f6f8;
  line-height: 1;
}
.apex-sent-big.negative { color: #ff554f; }
.apex-sent-big.positive { color: #1ddf91; }
.apex-sent-big.warning  { color: #ffb21a; }
.apex-sent-copy {
  font-size: 11.5px;
  color: #94a2b0;
  line-height: 1.6;
  margin-top: 8px;
}
.apex-sent-note {
  margin-top: 9px;
  font-size: 10px;
  color: #5d7485;
  line-height: 1.4;
  padding: 7px 10px;
  border: 1px solid rgba(70,145,165,0.14);
  border-radius: 8px;
  background: rgba(7,25,35,0.40);
}

/* ── TOP CATALYSTS ─────────────────────────────────────────────────── */
.apex-catalyst {
  display: grid;
  grid-template-columns: 56px 44px minmax(0,1fr) 58px;
  gap: 8px;
  align-items: center;
  padding: 11px 0;
  border-bottom: 1px solid rgba(90,145,165,0.09);
}
.apex-catalyst:first-child { border-top: 1px solid rgba(90,145,165,0.09); }
.apex-cat-flag { font-size: 18px; text-align: center; }
.apex-cat-datecol { display: flex; flex-direction: column; align-items: flex-start; }
.apex-cat-date {
  font-size: 10.5px;
  font-weight: 800;
  color: #dce5ea;
  letter-spacing: 0.2px;
  line-height: 1.2;
}
.apex-cat-time { font-size: 9.5px; color: #748895; margin-top: 1px; }
.apex-cat-main { min-width: 0; }
.apex-cat-title {
  font-size: 11.5px;
  font-weight: 700;
  color: #eef3f6;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.apex-cat-meta-row {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 2px;
}
.apex-cat-curr {
  font-size: 9.5px;
  font-weight: 700;
  color: #748895;
}
.apex-cat-dot { font-size: 7px; color: #4a6070; }
.apex-impact-high  { font-size: 9.5px; color: #ff7b77; font-weight: 700; }
.apex-impact-med   { font-size: 9.5px; color: #ffb21a; font-weight: 700; }
.apex-impact-low   { font-size: 9.5px; color: #748895; font-weight: 700; }
.apex-cat-countdown {
  font-size: 9.5px;
  color: #748895;
  text-align: right;
  line-height: 1.3;
}
.apex-catalyst-footer {
  padding-top: 10px;
  font-size: 11px;
  color: #27dce7;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  opacity: 0.85;
}
.apex-catalyst-footer:hover { opacity: 1; }

/* Mobile catalysts: hide flag + countdown merges */
@media (max-width: 768px) {
  .apex-catalyst {
    grid-template-columns: 38px minmax(0,1fr) 52px;
  }
  .apex-catalyst .apex-cat-datecol { display: none; }
  .apex-cat-flag { font-size: 16px; }
  .apex-cat-title { font-size: 11px; }
}

/* ── FOOTER STATUS BAR ─────────────────────────────────────────────── */
.apex-footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 18px;
  padding: 10px 16px;
  border: 1px solid rgba(70,145,165,0.14);
  border-radius: 10px;
  background: rgba(5,14,20,0.60);
  font-size: 10.5px;
  color: #748895;
  flex-wrap: wrap;
}
.apex-footer-bar-left { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.apex-footer-live-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #1ddf91;
  box-shadow: 0 0 6px rgba(29,223,145,0.6);
  margin-right: 5px;
}

/* ── MOBILE HEADER STRIP ───────────────────────────────────────────── */
.apex-mobile-header {
  display: none;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  padding: 9px 12px;
  border: 1px solid rgba(55,150,170,0.18);
  border-radius: 11px;
  background: linear-gradient(145deg, rgba(6,21,30,0.92), rgba(3,13,20,0.97));
}
.apex-mobile-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}
.apex-mobile-mark {
  font-size: 18px;
  font-weight: 950;
  font-style: italic;
  color: #27dce7;
}
.apex-mobile-title {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.7px;
  color: #eef4f7;
}
@media (max-width: 768px) {
  .apex-mobile-header { display: flex; }
  .apex-dashboard-head { display: block; }
  .apex-user-area { margin-top: 10px; justify-content: flex-start; }
  .apex-dashboard-title { font-size: 25px; }
  .apex-summary-card { min-height: 96px; padding: 13px; }
  .apex-metric { font-size: 22px; }
  .apex-panel { padding: 14px 13px; }
}

/* ── PANEL CONTENT SPACING ─────────────────────────────────────────── */
.apex-no-data {
  font-size: 11.5px;
  color: #748895;
  padding: 14px 0;
  text-align: center;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────

def _render_sidebar(auth_user: dict) -> None:
    is_admin = bool(auth_user and auth_user.get("is_admin"))
    with st.sidebar:
        # Brand
        st.markdown(
            """<div class="apex-sidebar-brand">
                 <div class="apex-sidebar-logo">A</div>
                 <div>
                   <div class="apex-sidebar-brand-title">APEXMACRO</div>
                   <div class="apex-sidebar-brand-subtitle">Intelligence Desk</div>
                 </div>
               </div>
               <div class="apex-sidebar-sep"></div>""",
            unsafe_allow_html=True,
        )

        # Nav items — only real existing routes
        routes = [
            ("dashboard",  "⌂",  "Dashboard",  "pages/dashboard.py"),
            ("forex",      "◉",  "Forex",       "pages/forex.py"),
            ("gold",       "◆",  "Gold",        "pages/gold.py"),
            ("oil",        "◔",  "Oil",         "pages/oil.py"),
            ("nasdaq",     "▥",  "Nasdaq-100",  "pages/nasdaq.py"),
            ("forecaster", "▣",  "Forecaster",  "pages/forecaster.py"),
        ]
        if is_admin:
            routes.append(("admin", "♛", "Admin", "pages/admin.py"))

        for key, icon, label, path in routes:
            is_active = key == "dashboard"
            if st.button(
                f"{icon}  {label}",
                key=f"dash_side_{key}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.switch_page(path)

        # Market time clock
        now = core.get_current_time()
        st.markdown(
            f"""<div class="apex-sidebar-bottom">
                  <div class="apex-side-meta">Market Time</div>
                  <div class="apex-side-clock">{now.strftime('%H:%M:%S')}</div>
                  <div class="apex-side-date">{now.strftime('%d %b %Y, %a')}</div>
                </div>""",
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────
# HTML builders
# ─────────────────────────────────────────────

def _summary_card(icon: str, kicker: str, metric: str, metric_cls: str,
                   meta: str, meta_cls: str = "", spark_html: str = "") -> str:
    spark_block = f'<div class="apex-card-spark">{spark_html}</div>' if spark_html else ""
    return f"""<div class="apex-summary-card">
  <div class="apex-summary-card-icon">{icon}</div>
  <div class="apex-kicker">{kicker}</div>
  <div class="apex-metric {metric_cls}">{metric}</div>
  <div class="apex-meta {meta_cls}">{meta}</div>
  {spark_block}
</div>"""


def _regime_rows_html(usd_composite: dict | None) -> str:
    if not usd_composite:
        return '<div class="apex-no-data">Macro regime data is temporarily unavailable.</div>'

    rows = usd_composite.get("rows", [])[:6]
    if not rows:
        return '<div class="apex-no-data">No macro regime rows available.</div>'

    html_parts = []
    for r in rows:
        icon = core.CAT_ICONS.get(r.get("cat"), "◌")
        name = escape(str(r.get("name", "Macro Factor")))
        date_val = escape(str(r.get("date", "")))
        label = _broad(r.get("score"))
        pill_cls = _tone(label)
        html_parts.append(f"""<div class="apex-regime-row">
  <div class="apex-regime-icon">{escape(str(icon))}</div>
  <div>
    <div class="apex-regime-name">{name}</div>
    <div class="apex-regime-sub">Latest: {date_val}</div>
  </div>
  <span class="apex-pill {pill_cls}">{escape(label)}</span>
</div>""")
    return "".join(html_parts)


def _market_rows_html(market_data: list) -> str:
    head = """<div class="apex-market-head">
  <div>Asset</div><div>Price</div><div>24H Chg</div><div>Trend</div>
</div>"""
    rows = []
    for name, df in market_data:
        latest, ch, vals = _latest_change(df)
        tone = "positive" if (ch or 0) > 0 else "negative" if (ch or 0) < 0 else "neutral"
        change_str = "—" if ch is None else f"{ch:+.2f}%"
        spark = core.spark_svg(vals, w=88, h=26, pos_good=True) if len(vals) > 1 else ""
        icon = _asset_icon(name)
        rows.append(f"""<div class="apex-market-row">
  <div class="apex-asset-cell">
    <div class="apex-asset-icon">{icon}</div>
    <div class="apex-asset-name">{escape(name)}</div>
  </div>
  <div class="apex-price">{_fmt(latest)}</div>
  <div class="apex-change {tone}">{escape(change_str)}</div>
  <div class="apex-spark">{spark}</div>
</div>""")
    return head + "".join(rows)


def _catalyst_rows_html(events: list) -> str:
    if not events:
        return '<div class="apex-no-data">No upcoming catalyst events available.</div>'
    parts = []
    for e in events[:5]:
        dt = e.get("datetime_obj")
        date_str = dt.strftime("%d %b").upper() if dt else escape(str(e.get("date_str", "—")))
        time_str = escape(str(e.get("time_str", "—")).split(" ")[0])
        currency = escape(str(e.get("currency", "—")))
        impact = escape(str(e.get("impact", "—")))
        title = escape(str(e.get("title", "Event")))
        countdown = escape(str(e.get("countdown", "")))
        flag = _currency_flag(str(e.get("currency", "")))
        impact_cls = _impact_class(str(e.get("impact", "")))
        parts.append(f"""<div class="apex-catalyst">
  <div class="apex-cat-flag">{flag}</div>
  <div class="apex-cat-datecol">
    <div class="apex-cat-date">{date_str}</div>
    <div class="apex-cat-time">{time_str}</div>
  </div>
  <div class="apex-cat-main">
    <div class="apex-cat-title">{title}</div>
    <div class="apex-cat-meta-row">
      <span class="apex-cat-curr">{currency}</span>
      <span class="apex-cat-dot">●</span>
      <span class="{impact_cls}">{impact} Impact</span>
    </div>
  </div>
  <div class="apex-cat-countdown">{countdown}</div>
</div>""")
    return "".join(parts)


# ─────────────────────────────────────────────
# Main render entry point
# ─────────────────────────────────────────────

def render_dashboard(auth_user: dict) -> None:
    """Full authenticated dashboard UI. Engine/business logic untouched."""
    _inject_dashboard_css()
    _render_sidebar(auth_user)

    # ── Data fetching (existing cached calls) ──────────────────────────
    usd = (
        core.compute_composite("USD", core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
        if core.DEFAULT_FRED_KEY else None
    )
    events = core.get_upcoming_catalyst_events()
    dxy  = core.fetch_fred("DTWEXBGS",           core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None
    gold = core.fetch_fred("GOLDAMGBD228NLBM",    core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None
    oil  = core.fetch_fred(core.OIL_SERIES["wti"], core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None
    ndx  = core.fetch_fred("NASDAQ100",            core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None

    market_data = [
        ("USD Index (DXY)", dxy),
        ("Gold (XAUUSD)",   gold),
        ("Crude Oil (WTI)", oil),
        ("Nasdaq-100",      ndx),
    ]

    available    = sum(1 for _, df in market_data if df is not None and not df.empty)
    broad        = _broad(usd.get("score") if usd else None)
    risk         = _risk_label(broad)
    score        = float((usd or {}).get("score", 0.0))
    gauge_val    = max(-100, min(100, score * 100))
    glabel       = _broad(score)

    user_name    = escape(str((auth_user or {}).get("user_name") or (auth_user or {}).get("username") or "VIP"))
    is_admin     = bool((auth_user or {}).get("is_admin"))
    role         = "Admin" if is_admin else "VIP"
    now          = core.get_current_time()

    # ── Mobile header (hidden on desktop by CSS) ───────────────────────
    st.markdown(
        f"""<div class="apex-mobile-header">
              <div class="apex-mobile-brand">
                <span class="apex-mobile-mark">A</span>
                <span class="apex-mobile-title">APEXMACRO</span>
              </div>
              <div class="apex-user-chip {'admin' if is_admin else 'vip'}">
                {'♛' if is_admin else '♢'} {role}
              </div>
            </div>""",
        unsafe_allow_html=True,
    )

    # ── Top header ────────────────────────────────────────────────────
    role_chip_cls = "admin" if is_admin else "vip"
    role_icon     = "♛" if is_admin else "◇"
    st.markdown(
        f"""<div class="apex-dashboard-head">
              <div>
                <div class="apex-dashboard-title">Global Macro Overview</div>
                <div class="apex-dashboard-subtitle">Real-time macro intelligence and market overview</div>
              </div>
              <div class="apex-user-area">
                <div class="apex-user-chip {role_chip_cls}">{role_icon} {role}</div>
                <div class="apex-user-chip">{user_name}</div>
                <div class="apex-user-chip">◷ {now.strftime('%H:%M')}</div>
              </div>
            </div>""",
        unsafe_allow_html=True,
    )

    # ── 4 Summary metric cards ────────────────────────────────────────
    _, _, dxy_vals = _latest_change(dxy)
    dxy_spark  = core.spark_svg(dxy_vals,  w=80, h=24, pos_good=True) if len(dxy_vals) > 1 else ""

    _, _, gold_vals = _latest_change(gold)
    gold_spark = core.spark_svg(gold_vals, w=80, h=24, pos_good=True) if len(gold_vals) > 1 else ""

    risk_tone  = _tone(risk)
    broad_tone = _tone(broad)

    cards_html = (
        '<div class="apex-summary-grid">'
        + _summary_card(
            "📈", "Active Assets",
            str(available),
            "",
            f"Live FRED market feeds · {available} of {len(market_data)} available",
            "",
            dxy_spark,
        )
        + _summary_card(
            "📅", "Global Events",
            str(len(events)),
            "",
            "Upcoming High &amp; Medium impact catalyst events",
            "",
            gold_spark,
        )
        + _summary_card(
            "🛡️", "Risk Regime",
            escape(risk),
            risk_tone,
            "USD composite regime proxy",
        )
        + _summary_card(
            "🎯", "Market Bias",
            escape(broad),
            broad_tone,
            "Existing broad composite state",
        )
        + '</div>'
    )
    st.markdown(cards_html, unsafe_allow_html=True)

    # ── Middle: Global Macro Regime  |  Market Snapshot ───────────────
    col_left, col_right = st.columns([1.08, 1], gap="small")

    with col_left:
        regime_html = _regime_rows_html(usd)
        st.markdown(
            f"""<div class="apex-panel">
                  <div class="apex-panel-title-row">
                    <div class="apex-panel-title">
                      <span class="apex-panel-title-icon">🌐</span>
                      Global Macro Regime
                    </div>
                  </div>
                  {regime_html}
                </div>""",
            unsafe_allow_html=True,
        )

    with col_right:
        market_html = _market_rows_html(market_data)
        st.markdown(
            f"""<div class="apex-panel">
                  <div class="apex-panel-title-row">
                    <div class="apex-panel-title">
                      <span class="apex-panel-title-icon">📊</span>
                      Market Snapshot
                    </div>
                  </div>
                  <div class="apex-market-table">{market_html}</div>
                  <div class="apex-market-source">All prices sourced from FRED. Source: ApexMacro Feeds</div>
                </div>""",
            unsafe_allow_html=True,
        )

    # ── Lower: Market Sentiment Index  |  Top Catalysts ───────────────
    s_left, s_right = st.columns([1.45, 0.7], gap="small")

    with s_left:
        st.markdown(
            """<div class="apex-panel">
                 <div class="apex-panel-title-row">
                   <div class="apex-panel-title">
                     <span class="apex-panel-title-icon">📡</span>
                     Market Sentiment Index
                   </div>
                 </div>""",
            unsafe_allow_html=True,
        )
        g_col1, g_col2 = st.columns([0.44, 0.56])
        with g_col1:
            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=gauge_val,
                number={"font": {"size": 30, "color": "#f3f6f8"}, "suffix": ""},
                gauge={
                    "axis": {
                        "range": [-100, 100],
                        "tickfont": {"color": "#7f919f", "size": 9},
                        "tickvals": [-100, -50, 0, 50, 100],
                    },
                    "bar": {"color": "#27dce7", "thickness": 0.22},
                    "bgcolor": "rgba(0,0,0,0)",
                    "borderwidth": 0,
                    "steps": [
                        {"range": [-100, -20], "color": "rgba(255,85,79,0.16)"},
                        {"range": [-20,  20],  "color": "rgba(148,162,176,0.08)"},
                        {"range": [20,  100],  "color": "rgba(29,223,145,0.14)"},
                    ],
                    "threshold": {
                        "line": {"color": "#27dce7", "width": 2},
                        "thickness": 0.82,
                        "value": gauge_val,
                    },
                },
            ))
            fig.update_layout(
                height=210,
                margin=dict(l=20, r=20, t=22, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                font={"color": "#94a2b0"},
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with g_col2:
            st.markdown(
                f"""<div style="padding:24px 6px 10px">
                      <div class="apex-sent-big {_tone(glabel)}">{escape(glabel)}</div>
                      <div class="apex-sent-copy">
                        Visualization of the existing USD composite score.<br>
                        Macro and news weights remain exactly as defined by the ApexMacro engine.
                      </div>
                      <div class="apex-sent-note">
                        Composite score: <strong>{gauge_val:+.1f}</strong> &nbsp;·&nbsp;
                        No synthetic history fabricated.
                      </div>
                    </div>""",
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    with s_right:
        catalyst_html = _catalyst_rows_html(events)
        st.markdown(
            f"""<div class="apex-panel">
                  <div class="apex-panel-title-row">
                    <div class="apex-panel-title">
                      <span class="apex-panel-title-icon">⚡</span>
                      Top Catalysts
                    </div>
                  </div>
                  {catalyst_html}
                  <div class="apex-catalyst-footer">
                    <span>View full calendar in Forecaster</span>
                    <span>›</span>
                  </div>
                </div>""",
            unsafe_allow_html=True,
        )
        if st.button("Go to Forecaster  →", key="dash_go_forecaster", use_container_width=True):
            st.switch_page("pages/forecaster.py")

    # ── Footer status bar ──────────────────────────────────────────────
    st.markdown(
        f"""<div class="apex-footer-bar">
              <div class="apex-footer-bar-left">
                <span>Last Updated: {now.strftime('%d %b %Y, %H:%M')}</span>
                <span><span class="apex-footer-live-dot"></span>All Systems Operational</span>
                <span>Data Source: ApexMacro Intelligence Engine · FRED</span>
              </div>
              <span>© 2026 ApexMacro. All rights reserved.</span>
            </div>""",
        unsafe_allow_html=True,
    )
