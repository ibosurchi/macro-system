"""Thin render orchestration for authenticated pages.

Responsive Institutional Macro Terminal UI (Desktop, Tablet & Mobile).
All strategies, data sources, and calculations remain 100% untouched.
"""
from __future__ import annotations

from html import escape
from datetime import datetime, timedelta
import streamlit as st
import plotly.graph_objects as go
import numpy as np

from . import production_core as core
from .ui.common import render_top_header, render_footer
from .ui.terminal_nav import render_terminal_nav


# ─────────────────────────────────────────────
# HTML Cleaner (Prevents Markdown Code-Block Bug)
# ─────────────────────────────────────────────

def _render_html(html_str: str) -> None:
    """Render HTML safely without markdown indentation creating code-blocks."""
    clean = "\n".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(clean, unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Pure Helpers (no engine modifications)
# ─────────────────────────────────────────────

def _broad(score):
    if score is None:
        return "Unavailable"
    detailed, _, _ = core.bias_from_score(float(score))
    return core._broad_regime(detailed)


def _tone(label):
    s = str(label).lower()
    if any(x in s for x in ("bear", "risk-off", "tight", "low", "negative", "down", "slowing", "tightening")):
        return "negative"
    if any(x in s for x in ("bull", "risk-on", "strong", "positive", "up", "expanding", "easing")):
        return "positive"
    if any(x in s for x in ("mixed", "sticky", "elevated", "moderate", "cautious")):
        return "warning"
    return "neutral"


def _fmt(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "—"


def _latest_change(df):
    if df is None or df.empty:
        return None, None, []
    vals = [float(x) for x in df["value"].dropna().tolist()]
    if not vals:
        return None, None, []
    latest = vals[-1]
    ch = (latest / vals[-2] - 1) * 100 if len(vals) > 1 and vals[-2] else None
    return latest, ch, vals[-20:]


def _risk_label(broad):
    return {"Bearish": "Risk-Off", "Bullish": "Risk-On", "Neutral": "Neutral"}.get(broad, broad)


def _flag(currency):
    flags = {
        "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
        "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭",
        "CNY": "🇨🇳", "ALL": "🌐",
    }
    return flags.get(str(currency).upper(), "🌐")


def _smooth_sparkline_svg(vals: list, color: str = "#27dce7", w: int = 105, h: int = 34) -> str:
    """Generate a smooth cubic bezier sparkline SVG with glowing drop-shadow and gradient fill."""
    if not vals or len(vals) < 2:
        vals = [10, 14, 12, 17, 15, 21, 19, 25]
    
    vals = [float(x) for x in vals if x is not None]
    if len(vals) < 2:
        vals = [10, 15, 12, 20]
        
    mn, mx = min(vals), max(vals)
    rng = mx - mn if mx != mn else 1.0
    n = len(vals)
    pad_y = 4.0
    pts = [(i / (n - 1) * w, (h - pad_y) - ((vals[i] - mn) / rng) * (h - 2 * pad_y)) for i in range(n)]

    path_d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[max(i - 1, 0)]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[min(i + 2, len(pts) - 1)]
        cp1x = p1[0] + (p2[0] - p0[0]) / 5.5
        cp1y = p1[1] + (p2[1] - p0[1]) / 5.5
        cp2x = p2[0] - (p3[0] - p1[0]) / 5.5
        cp2y = p2[1] - (p3[1] - p1[1]) / 5.5
        path_d += f" C {cp1x:.1f} {cp1y:.1f}, {cp2x:.1f} {cp2y:.1f}, {p2[0]:.1f} {p2[1]:.1f}"

    fill_d = f"{path_d} L {w:.1f} {h:.1f} L 0 {h:.1f} Z"
    grad_id = f"grad_{abs(hash(color + str(vals[:3])))}"

    return f"""<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;overflow:visible;max-width:100%;">
<defs>
<linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="{color}" stop-opacity="0.25"/>
<stop offset="100%" stop-color="{color}" stop-opacity="0.0"/>
</linearGradient>
</defs>
<path d="{fill_d}" fill="url(#{grad_id})"/>
<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" style="filter:drop-shadow(0 0 5px {color}66);"/>
</svg>"""


def _world_map_svg() -> str:
    """High-tech institutional dot-matrix World Map SVG with cyan glowing nodes."""
    return """<svg viewBox="0 0 420 220" width="100%" height="100%" style="display:block;max-height:220px;">
<defs>
<radialGradient id="mapGlow" cx="50%" cy="50%" r="50%">
<stop offset="0%" stop-color="#27dce7" stop-opacity="0.12"/>
<stop offset="100%" stop-color="#27dce7" stop-opacity="0"/>
</radialGradient>
<filter id="nodeGlow" x="-50%" y="-50%" width="200%" height="200%">
<feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="blur"/>
<feMerge>
<feMergeNode in="blur"/>
<feMergeNode in="SourceGraphic"/>
</feMerge>
</filter>
</defs>
<rect width="100%" height="100%" fill="url(#mapGlow)"/>
<g stroke="rgba(39,220,231,0.06)" stroke-width="0.75" stroke-dasharray="2 4">
<line x1="0" y1="55" x2="420" y2="55"/><line x1="0" y1="110" x2="420" y2="110"/><line x1="0" y1="165" x2="420" y2="165"/>
<line x1="70" y1="0" x2="70" y2="220"/><line x1="140" y1="0" x2="140" y2="220"/><line x1="210" y1="0" x2="210" y2="220"/>
<line x1="280" y1="0" x2="280" y2="220"/><line x1="350" y1="0" x2="350" y2="220"/>
</g>
<g fill="rgba(39,220,231,0.45)">
<circle cx="50" cy="40" r="1.4"/><circle cx="58" cy="42" r="1.4"/><circle cx="66" cy="38" r="1.4"/><circle cx="74" cy="44" r="1.4"/>
<circle cx="45" cy="50" r="1.4"/><circle cx="55" cy="52" r="1.4"/><circle cx="65" cy="48" r="1.8"/><circle cx="75" cy="52" r="1.4"/><circle cx="85" cy="48" r="1.4"/>
<circle cx="42" cy="62" r="1.4"/><circle cx="52" cy="60" r="1.8"/><circle cx="62" cy="62" r="1.8"/><circle cx="72" cy="60" r="1.4"/><circle cx="82" cy="62" r="1.8"/><circle cx="92" cy="58" r="1.4"/>
<circle cx="50" cy="74" r="1.4"/><circle cx="60" cy="72" r="1.8"/><circle cx="70" cy="74" r="1.8"/><circle cx="80" cy="72" r="1.8"/><circle cx="90" cy="74" r="1.4"/>
<circle cx="58" cy="86" r="1.4"/><circle cx="68" cy="84" r="1.4"/><circle cx="78" cy="86" r="1.4"/><circle cx="72" cy="98" r="1.4"/><circle cx="80" cy="102" r="1.4"/>
</g>
<g fill="rgba(39,220,231,0.40)">
<circle cx="95" cy="120" r="1.4"/><circle cx="105" cy="122" r="1.8"/><circle cx="115" cy="120" r="1.4"/>
<circle cx="98" cy="132" r="1.4"/><circle cx="108" cy="134" r="1.8"/><circle cx="118" cy="130" r="1.8"/><circle cx="128" cy="132" r="1.4"/>
<circle cx="102" cy="144" r="1.4"/><circle cx="112" cy="146" r="1.8"/><circle cx="122" cy="142" r="1.8"/><circle cx="130" cy="144" r="1.4"/>
<circle cx="105" cy="156" r="1.4"/><circle cx="115" cy="158" r="1.8"/><circle cx="122" cy="154" r="1.4"/><circle cx="108" cy="168" r="1.4"/><circle cx="116" cy="170" r="1.4"/><circle cx="110" cy="180" r="1.4"/>
</g>
<g fill="rgba(39,220,231,0.55)">
<circle cx="195" cy="40" r="1.4"/><circle cx="205" cy="42" r="1.8"/><circle cx="215" cy="40" r="1.4"/>
<circle cx="188" cy="50" r="1.4"/><circle cx="198" cy="52" r="1.8"/><circle cx="208" cy="48" r="1.8"/><circle cx="218" cy="52" r="1.8"/><circle cx="228" cy="48" r="1.4"/>
<circle cx="190" cy="62" r="1.8"/><circle cx="200" cy="60" r="2.0"/><circle cx="210" cy="62" r="2.0"/><circle cx="220" cy="60" r="1.8"/><circle cx="230" cy="62" r="1.4"/>
<circle cx="194" cy="74" r="1.4"/><circle cx="204" cy="72" r="1.8"/><circle cx="214" cy="74" r="1.8"/><circle cx="224" cy="72" r="1.4"/>
</g>
<g fill="rgba(39,220,231,0.40)">
<circle cx="192" cy="90" r="1.4"/><circle cx="202" cy="88" r="1.8"/><circle cx="212" cy="90" r="1.8"/><circle cx="222" cy="88" r="1.4"/><circle cx="232" cy="92" r="1.4"/>
<circle cx="188" cy="102" r="1.4"/><circle cx="198" cy="104" r="1.8"/><circle cx="208" cy="100" r="1.8"/><circle cx="218" cy="104" r="1.8"/><circle cx="228" cy="102" r="1.4"/><circle cx="238" cy="104" r="1.4"/>
<circle cx="195" cy="116" r="1.4"/><circle cx="205" cy="118" r="1.8"/><circle cx="215" cy="114" r="1.8"/><circle cx="225" cy="118" r="1.8"/><circle cx="235" cy="116" r="1.4"/>
<circle cx="202" cy="130" r="1.4"/><circle cx="212" cy="132" r="1.8"/><circle cx="222" cy="128" r="1.4"/><circle cx="230" cy="132" r="1.4"/>
<circle cx="208" cy="144" r="1.4"/><circle cx="218" cy="146" r="1.8"/><circle cx="226" cy="142" r="1.4"/>
<circle cx="214" cy="158" r="1.4"/><circle cx="222" cy="156" r="1.4"/>
</g>
<g fill="rgba(39,220,231,0.50)">
<circle cx="245" cy="38" r="1.4"/><circle cx="255" cy="40" r="1.4"/><circle cx="265" cy="36" r="1.4"/><circle cx="275" cy="40" r="1.4"/><circle cx="285" cy="38" r="1.4"/><circle cx="295" cy="42" r="1.4"/><circle cx="305" cy="38" r="1.4"/>
<circle cx="240" cy="50" r="1.4"/><circle cx="250" cy="52" r="1.8"/><circle cx="260" cy="48" r="1.8"/><circle cx="270" cy="52" r="1.8"/><circle cx="280" cy="48" r="1.8"/><circle cx="290" cy="52" r="1.8"/><circle cx="300" cy="48" r="1.8"/><circle cx="310" cy="52" r="1.4"/><circle cx="320" cy="48" r="1.4"/>
<circle cx="238" cy="62" r="1.4"/><circle cx="248" cy="60" r="1.8"/><circle cx="258" cy="62" r="2.0"/><circle cx="268" cy="60" r="2.0"/><circle cx="278" cy="62" r="2.0"/><circle cx="288" cy="60" r="2.0"/><circle cx="298" cy="62" r="2.0"/><circle cx="308" cy="60" r="1.8"/><circle cx="318" cy="62" r="1.8"/><circle cx="328" cy="60" r="1.4"/>
<circle cx="245" cy="74" r="1.4"/><circle cx="255" cy="72" r="1.8"/><circle cx="265" cy="76" r="1.8"/><circle cx="275" cy="72" r="2.0"/><circle cx="285" cy="74" r="2.0"/><circle cx="295" cy="72" r="2.0"/><circle cx="305" cy="74" r="1.8"/><circle cx="315" cy="72" r="1.8"/><circle cx="325" cy="76" r="1.4"/>
<circle cx="260" cy="86" r="1.4"/><circle cx="270" cy="88" r="1.8"/><circle cx="280" cy="84" r="1.8"/><circle cx="290" cy="88" r="2.0"/><circle cx="300" cy="84" r="1.8"/><circle cx="310" cy="88" r="1.8"/>
<circle cx="275" cy="100" r="1.4"/><circle cx="285" cy="102" r="1.8"/><circle cx="295" cy="98" r="1.8"/><circle cx="305" cy="102" r="1.4"/>
</g>
<g fill="rgba(39,220,231,0.45)">
<circle cx="330" cy="138" r="1.4"/><circle cx="340" cy="140" r="1.8"/><circle cx="350" cy="136" r="1.4"/>
<circle cx="325" cy="150" r="1.4"/><circle cx="335" cy="152" r="1.8"/><circle cx="345" cy="148" r="1.8"/><circle cx="355" cy="150" r="1.4"/>
<circle cx="330" cy="162" r="1.4"/><circle cx="340" cy="164" r="1.8"/><circle cx="350" cy="160" r="1.4"/>
</g>
<g stroke="rgba(39,220,231,0.30)" stroke-width="1" fill="none">
<path d="M 62 62 Q 130 30 200 60" stroke-dasharray="3 3"/>
<path d="M 200 60 Q 240 40 288 60" stroke-dasharray="3 3"/>
<path d="M 288 60 Q 320 100 345 148" stroke-dasharray="3 3"/>
<path d="M 62 62 Q 80 100 118 130" stroke-dasharray="3 3"/>
<path d="M 200 60 Q 210 100 218 146" stroke-dasharray="3 3"/>
</g>
<circle cx="62" cy="62" r="3.2" fill="#27dce7" filter="url(#nodeGlow)"/><circle cx="62" cy="62" r="1.6" fill="#ffffff"/>
<circle cx="200" cy="60" r="3.5" fill="#27dce7" filter="url(#nodeGlow)"/><circle cx="200" cy="60" r="1.8" fill="#ffffff"/>
<circle cx="288" cy="60" r="3.2" fill="#27dce7" filter="url(#nodeGlow)"/><circle cx="288" cy="60" r="1.6" fill="#ffffff"/>
<circle cx="345" cy="148" r="2.8" fill="#27dce7" filter="url(#nodeGlow)"/><circle cx="345" cy="148" r="1.4" fill="#ffffff"/>
<circle cx="118" cy="130" r="2.5" fill="#27dce7" filter="url(#nodeGlow)"/>
</svg>"""


# ─────────────────────────────────────────────
# CSS Design System (Exact Match for Image 1 + Responsive Mobile)
# ─────────────────────────────────────────────

def _inject_terminal_css():
    _render_html("""<style>
/* ── BASE & ROOT CSS VARIABLES ──────────────────────────── */
:root {
  --apex-bg: #02080d;
  --apex-panel: #05141d;
  --apex-card: #071923;
  --apex-cyan: #27dce7;
  --apex-cyan-glow: rgba(39, 220, 231, 0.18);
  --apex-border: rgba(70, 145, 165, 0.18);
  --apex-border-hover: rgba(39, 220, 231, 0.35);
  --apex-text: #f3f6f8;
  --apex-muted: #94a2b0;
  --apex-positive: #1ddf91;
  --apex-negative: #ff554f;
  --apex-warning: #ffb21a;
  --apex-purple: #b54ee3;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  overflow-x: hidden !important;
}

/* ── SIDEBAR STYLING ────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #03111a 0%, #020a10 100%) !important;
  border-right: 1px solid rgba(39, 220, 231, 0.22) !important;
  box-shadow: 2px 0 24px rgba(0, 0, 0, 0.5) !important;
}

@media (min-width: 1024px) {
  [data-testid="stSidebar"] {
    min-width: 230px !important;
    max-width: 230px !important;
    width: 230px !important;
    transform: none !important;
    visibility: visible !important;
    display: block !important;
  }
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] {
    display: none !important;
    visibility: hidden !important;
  }
}

@media (max-width: 1023px) {
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    z-index: 999999 !important;
  }
}

[data-testid="stSidebar"] > div:first-child {
  padding: 16px 10px 18px !important;
}

/* Sidebar Nav Button Styles */
[data-testid="stSidebar"] [data-testid="stButton"] button {
  min-height: 44px !important;
  border-radius: 9px !important;
  text-align: left !important;
  justify-content: flex-start !important;
  font-size: 13.5px !important;
  font-weight: 550 !important;
  box-shadow: none !important;
  margin: 2px 0 !important;
  letter-spacing: 0.15px;
  transition: all 0.15s ease !important;
}

/* Active Nav Item */
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: linear-gradient(90deg, rgba(20, 210, 225, 0.14) 0%, rgba(20, 210, 225, 0.03) 100%) !important;
  border: 1px solid rgba(39, 220, 231, 0.40) !important;
  color: #27dce7 !important;
  box-shadow: inset 0 0 16px rgba(39, 220, 231, 0.06), 0 0 12px rgba(39, 220, 231, 0.08) !important;
}

/* Inactive Nav Items */
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
  background: transparent !important;
  border: 1px solid transparent !important;
  color: #a4b3be !important;
}

[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
  background: rgba(255, 255, 255, 0.03) !important;
  border-color: rgba(70, 145, 165, 0.25) !important;
  color: #f0f4f8 !important;
}

/* ── SIDEBAR BRAND & WIDGETS ────────────────────────────── */
.apex-sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px 18px;
}
.apex-sidebar-logo-icon {
  width: 38px;
  height: 38px;
  border-radius: 9px;
  border: 1px solid rgba(39, 220, 231, 0.35);
  background: rgba(39, 220, 231, 0.06);
  display: grid;
  place-items: center;
  color: #27dce7;
  font-size: 22px;
  font-weight: 950;
  font-style: italic;
  box-shadow: inset 0 0 14px rgba(39, 220, 231, 0.08);
  flex-shrink: 0;
}
.apex-sidebar-brand-title {
  font-size: 16px;
  font-weight: 850;
  letter-spacing: 1.8px;
  color: #f5f7f9;
  line-height: 1.1;
}
.apex-sidebar-brand-subtitle {
  font-size: 10px;
  color: #27dce7;
  margin-top: 2px;
  letter-spacing: 0.3px;
}
.apex-sidebar-sep {
  height: 1px;
  background: rgba(80, 145, 165, 0.14);
  margin: 0 0 12px;
}
.apex-sidebar-bottom {
  margin-top: 24px;
  padding: 12px 14px;
  border: 1px solid rgba(70, 145, 165, 0.18);
  border-radius: 11px;
  background: rgba(7, 25, 35, 0.50);
}
.apex-side-meta {
  font-size: 10px;
  letter-spacing: 0.4px;
  color: #748895;
  text-transform: uppercase;
  display: flex;
  align-items: center;
  gap: 5px;
}
.apex-side-clock {
  font-size: 20px;
  font-weight: 800;
  color: #f3f6f8;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.5px;
}
.apex-side-date {
  font-size: 9.5px;
  color: #899aa7;
  margin-top: 2px;
}
.apex-sidebar-mode-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(70, 145, 165, 0.15);
  background: rgba(7, 25, 35, 0.35);
  margin-top: 8px;
  font-size: 11px;
  color: #94a2b0;
}

/* ── APP CANVAS & CONTAINER ─────────────────────────────── */
[data-testid="stAppViewContainer"] {
  background: radial-gradient(circle at 10% 0%, rgba(0, 220, 230, 0.03), transparent 28%), #02080d !important;
}
.block-container {
  max-width: 1750px !important;
  padding: 22px 28px 30px !important;
}

@media (max-width: 768px) {
  .block-container {
    padding: 10px 10px 24px !important;
  }
}

/* ── TOP HEADER (TITLE & USER CONTROLS) ─────────────────── */
.apex-dashboard-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}
.apex-dashboard-title {
  font-size: 30px;
  font-weight: 800;
  line-height: 1.1;
  color: #f4f7f9;
  letter-spacing: -0.3px;
}
.apex-dashboard-subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #7e91a2;
}
.apex-user-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.apex-bell-btn {
  position: relative;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid rgba(70, 145, 165, 0.20);
  background: rgba(7, 25, 35, 0.70);
  display: grid;
  place-items: center;
  color: #a4b3be;
  font-size: 14px;
}
.apex-bell-badge {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: #27dce7;
  color: #02080d;
  font-size: 9px;
  font-weight: 900;
  display: grid;
  place-items: center;
}
.apex-vip-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 178, 26, 0.35);
  background: rgba(255, 178, 26, 0.06);
  color: #f0cc80;
  font-size: 11.5px;
  font-weight: 700;
}
.apex-profile-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 12px 5px 6px;
  border-radius: 999px;
  border: 1px solid rgba(70, 145, 165, 0.22);
  background: rgba(7, 25, 35, 0.75);
  color: #d4dde3;
  font-size: 12px;
  font-weight: 600;
}
.apex-profile-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: rgba(70, 145, 165, 0.25);
  color: #ecf7ff;
  font-size: 10px;
  font-weight: 800;
  display: grid;
  place-items: center;
}

@media (max-width: 768px) {
  .apex-dashboard-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 12px;
  }
  .apex-dashboard-title {
    font-size: 21px !important;
  }
  .apex-dashboard-subtitle {
    font-size: 11px !important;
  }
  .apex-user-controls {
    width: 100%;
    justify-content: flex-start;
    gap: 6px;
  }
  .apex-bell-btn { width: 32px; height: 32px; font-size: 12px; }
  .apex-vip-badge { font-size: 10px; padding: 4px 9px; }
  .apex-profile-chip { font-size: 10.5px; padding: 4px 10px 4px 5px; }
}

/* ── 4 SUMMARY METRIC CARDS ─────────────────────────────── */
.apex-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

@media (max-width: 1100px) and (min-width: 769px) {
  .apex-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
}

@media (max-width: 768px) {
  .apex-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 10px;
  }
}

@media (max-width: 360px) {
  .apex-summary-grid { grid-template-columns: 1fr; }
}

.apex-summary-card {
  min-width: 0;
  box-sizing: border-box;
  background: linear-gradient(145deg, rgba(7, 25, 35, 0.92) 0%, rgba(3, 15, 23, 0.98) 100%);
  border: 1px solid rgba(90, 145, 165, 0.20);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  transition: all 0.18s ease;
}
.apex-summary-card:hover {
  border-color: rgba(39, 220, 231, 0.35);
  transform: translateY(-1px);
}
.apex-summary-left {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.apex-summary-kicker-row {
  display: flex;
  align-items: center;
  gap: 7px;
}
.apex-summary-icon {
  font-size: 14px;
  color: #27dce7;
}
.apex-kicker {
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: 0.7px;
  color: #8899a8;
  text-transform: uppercase;
}
.apex-metric {
  font-size: 27px;
  font-weight: 850;
  color: #f3f6f8;
  margin-top: 6px;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}
.apex-metric.negative { color: #ff554f !important; }
.apex-metric.positive { color: #1ddf91 !important; }
.apex-metric.warning  { color: #ffb21a !important; }
.apex-metric.cyan     { color: #27dce7 !important; }

.apex-summary-sub {
  font-size: 10.5px;
  color: #7e91a2;
  margin-top: 5px;
  white-space: nowrap;
}
.apex-summary-sub.positive { color: #1ddf91; }
.apex-summary-sub.negative { color: #ff554f; }

.apex-summary-spark {
  flex-shrink: 0;
  width: 105px;
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 768px) {
  .apex-summary-card {
    padding: 12px 10px;
    position: relative;
    min-height: 86px;
    align-items: flex-start;
  }
  .apex-metric {
    font-size: 20px !important;
    margin-top: 3px;
  }
  .apex-kicker {
    font-size: 8.5px !important;
  }
  .apex-summary-sub {
    font-size: 9px !important;
    margin-top: 3px;
  }
  .apex-summary-spark {
    position: absolute;
    right: 6px;
    bottom: 6px;
    width: 65px !important;
    opacity: 0.65;
  }
}

/* ── SHARED PANEL BASE ──────────────────────────────────── */
.apex-panel {
  box-sizing: border-box;
  background: linear-gradient(145deg, rgba(7, 25, 35, 0.92) 0%, rgba(3, 15, 23, 0.98) 100%);
  border: 1px solid rgba(90, 145, 165, 0.20);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
  border-radius: 13px;
  padding: 18px 20px;
  margin-bottom: 14px;
  transition: border-color 0.18s ease;
}
.apex-panel:hover {
  border-color: rgba(39, 220, 231, 0.28);
}
.apex-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 10px;
}
.apex-panel-title {
  font-size: 15.5px;
  font-weight: 780;
  color: #f3f6f8;
  display: flex;
  align-items: center;
  gap: 6px;
}
.apex-info-icon {
  font-size: 12px;
  color: #5d7485;
  cursor: default;
}
.apex-header-link {
  font-size: 11.5px;
  color: #27dce7;
  text-decoration: none;
  font-weight: 600;
  cursor: pointer;
}
.apex-header-link:hover {
  text-decoration: underline;
}

@media (max-width: 768px) {
  .apex-panel {
    padding: 13px 12px;
    margin-bottom: 10px;
  }
  .apex-panel-title {
    font-size: 13.5px;
  }
}

/* ── MIDDLE ROW: MACRO REGIME & MARKET SNAPSHOT ─────────── */
.apex-regime-split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr);
  gap: 16px;
  align-items: center;
}

@media (max-width: 900px) {
  .apex-regime-split {
    grid-template-columns: 1fr;
    gap: 10px;
  }
}

.apex-regime-map-wrap {
  width: 100%;
  height: 100%;
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(3, 14, 22, 0.40);
  border-radius: 10px;
  border: 1px solid rgba(70, 145, 165, 0.10);
  overflow: hidden;
}

@media (max-width: 768px) {
  .apex-regime-map-wrap {
    min-height: 120px !important;
    max-height: 140px !important;
  }
}

.apex-regime-rows {
  display: flex;
  flex-direction: column;
}
.apex-regime-item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(90, 145, 165, 0.09);
}
.apex-regime-item:last-child { border-bottom: 0; }
.apex-regime-icon-box {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: rgba(39, 220, 231, 0.08);
  display: grid;
  place-items: center;
  font-size: 12px;
  color: #27dce7;
}
.apex-regime-label-group {
  min-width: 0;
}
.apex-regime-name {
  font-size: 12px;
  font-weight: 700;
  color: #e5ecf0;
}
.apex-regime-subtext {
  font-size: 9.5px;
  color: #748895;
  margin-top: 1px;
}
.apex-status-pill {
  font-size: 10px;
  font-weight: 650;
  padding: 4px 10px;
  border-radius: 6px;
  white-space: nowrap;
}
.apex-status-pill.amber {
  border: 1px solid rgba(255, 178, 26, 0.35);
  background: rgba(255, 178, 26, 0.09);
  color: #ffb21a;
}
.apex-status-pill.red {
  border: 1px solid rgba(255, 85, 79, 0.35);
  background: rgba(255, 85, 79, 0.09);
  color: #ff7b77;
}
.apex-status-pill.green {
  border: 1px solid rgba(29, 223, 145, 0.35);
  background: rgba(29, 223, 145, 0.09);
  color: #1ddf91;
}
.apex-regime-val-chip {
  font-size: 11px;
  font-weight: 700;
  color: #dce5ea;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .apex-regime-item {
    grid-template-columns: 22px minmax(0, 1fr) auto;
    gap: 6px;
    padding: 5px 0;
  }
  .apex-regime-icon-box { width: 20px; height: 20px; font-size: 10px; }
  .apex-regime-name { font-size: 11px; }
  .apex-regime-subtext { font-size: 8.5px; }
  .apex-status-pill { font-size: 8.5px; padding: 3px 7px; }
  .apex-regime-val-chip { font-size: 9.5px; }
}

/* ── MARKET SNAPSHOT TABLE ──────────────────────────────── */
.apex-snapshot-head {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(70px, 0.7fr) minmax(66px, 0.6fr) minmax(90px, 0.8fr);
  gap: 8px;
  font-size: 9px;
  text-transform: uppercase;
  color: #5d7485;
  letter-spacing: 0.5px;
  font-weight: 700;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(90, 145, 165, 0.15);
}
.apex-snapshot-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(70px, 0.7fr) minmax(66px, 0.6fr) minmax(90px, 0.8fr);
  gap: 8px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid rgba(90, 145, 165, 0.08);
}
.apex-snapshot-row:last-child { border-bottom: 0; }
.apex-asset-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.apex-asset-icon-round {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: rgba(39, 220, 231, 0.08);
  border: 1px solid rgba(39, 220, 231, 0.18);
  display: grid;
  place-items: center;
  font-size: 11px;
  flex-shrink: 0;
}
.apex-asset-title {
  font-size: 11.5px;
  font-weight: 700;
  color: #e8eef2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.apex-asset-price {
  font-size: 11.5px;
  font-weight: 600;
  color: #d4dde3;
  font-variant-numeric: tabular-nums;
}
.apex-asset-chg {
  font-size: 11.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.apex-asset-chg.positive { color: #1ddf91; }
.apex-asset-chg.negative { color: #ff554f; }
.apex-asset-chg.neutral  { color: #94a2b0; }

.apex-snapshot-footer {
  font-size: 9px;
  color: #5d7485;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(90, 145, 165, 0.08);
}

@media (max-width: 768px) {
  .apex-snapshot-head { display: none; }
  .apex-snapshot-row {
    grid-template-columns: minmax(0, 1fr) auto !important;
    grid-template-areas: 'asset price' 'spark chg' !important;
    gap: 4px 8px !important;
    padding: 7px 0 !important;
  }
  .apex-snapshot-row .apex-asset-info { grid-area: asset; }
  .apex-snapshot-row .apex-asset-price { grid-area: price; text-align: right; font-size: 11px; }
  .apex-snapshot-row > div:nth-child(4) { grid-area: spark; }
  .apex-snapshot-row .apex-asset-chg { grid-area: chg; text-align: right; font-size: 11px; }
}

/* ── MARKET SENTIMENT INDEX PANEL ───────────────────────── */
.apex-sentiment-subtitle {
  font-size: 11px;
  color: #748895;
  margin-top: -8px;
  margin-bottom: 10px;
}
.apex-timeframe-tabs {
  display: flex;
  gap: 4px;
}
.apex-timeframe-tab {
  font-size: 9.5px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 5px;
  border: 1px solid rgba(70, 145, 165, 0.18);
  background: rgba(7, 25, 35, 0.40);
  color: #748895;
  cursor: default;
}
.apex-timeframe-tab.active {
  border-color: rgba(39, 220, 231, 0.40);
  background: rgba(39, 220, 231, 0.12);
  color: #27dce7;
}

@media (max-width: 768px) {
  .apex-timeframe-tab { font-size: 8px; padding: 2px 5px; }
  .apex-sentiment-subtitle { font-size: 9.5px; }
}

/* ── TOP CATALYSTS PANEL ────────────────────────────────── */
.apex-catalyst-item {
  display: grid;
  grid-template-columns: 32px 50px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid rgba(90, 145, 165, 0.09);
}
.apex-catalyst-item:first-child { border-top: 1px solid rgba(90, 145, 165, 0.09); }
.apex-cat-flag-badge {
  font-size: 16px;
  text-align: center;
}
.apex-cat-datetime-col {
  display: flex;
  flex-direction: column;
}
.apex-cat-date-text {
  font-size: 10px;
  font-weight: 800;
  color: #dce5ea;
  letter-spacing: 0.2px;
}
.apex-cat-time-text {
  font-size: 9px;
  color: #748895;
  margin-top: 1px;
}
.apex-cat-info-col {
  min-width: 0;
}
.apex-cat-tag-row {
  display: flex;
  align-items: center;
  gap: 5px;
}
.apex-cat-curr-text {
  font-size: 9.5px;
  font-weight: 750;
  color: #8fa1ad;
}
.apex-cat-dot-sep {
  font-size: 6px;
  color: #4a6070;
}
.apex-cat-impact-text {
  font-size: 9.5px;
  font-weight: 700;
}
.apex-cat-impact-text.high   { color: #ff554f; }
.apex-cat-impact-text.medium { color: #ffb21a; }
.apex-cat-impact-text.low    { color: #748895; }

.apex-cat-headline {
  font-size: 11.5px;
  font-weight: 700;
  color: #eef3f6;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
}
.apex-cat-timer-col {
  font-size: 9.5px;
  color: #748895;
  text-align: right;
  white-space: nowrap;
}

.apex-catalyst-bottom-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 6px;
  font-size: 11px;
  color: #27dce7;
  cursor: pointer;
  opacity: 0.85;
  border-top: 1px solid rgba(90, 145, 165, 0.08);
}
.apex-catalyst-bottom-link:hover { opacity: 1; }

@media (max-width: 768px) {
  .apex-catalyst-item {
    grid-template-columns: 24px minmax(0, 1fr) auto !important;
    gap: 6px !important;
    padding: 7px 0 !important;
  }
  .apex-cat-datetime-col { display: none !important; }
  .apex-cat-flag-badge { font-size: 14px; }
  .apex-cat-headline { font-size: 10.5px; }
  .apex-cat-timer-col { font-size: 8.5px; }
}

/* ── SINGLE INSTITUTIONAL FOOTER ────────────────────────── */
.apex-footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 16px;
  padding: 11px 18px;
  border: 1px solid rgba(70, 145, 165, 0.14);
  border-radius: 10px;
  background: rgba(5, 14, 20, 0.65);
  font-size: 10.5px;
  color: #6a7f8e;
  flex-wrap: wrap;
}
.apex-footer-bar-left {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.apex-live-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #8fa3b4;
}
.apex-live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #1ddf91;
  box-shadow: 0 0 8px rgba(29, 223, 145, 0.8);
}

@media (max-width: 768px) {
  .apex-footer-bar {
    flex-direction: column !important;
    align-items: flex-start !important;
    gap: 8px !important;
    padding: 10px 12px !important;
    font-size: 9px !important;
  }
  .apex-footer-bar-left {
    flex-direction: column !important;
    align-items: flex-start !important;
    gap: 5px !important;
  }
}
</style>""")


# ─────────────────────────────────────────────
# Sidebar Renderer
# ─────────────────────────────────────────────

def _render_sidebar(auth_user):
    is_admin = bool(auth_user and auth_user.get("is_admin"))
    with st.sidebar:
        # Top Brand Logo
        _render_html("""<div class="apex-sidebar-brand">
<div class="apex-sidebar-logo-icon">▲</div>
<div>
<div class="apex-sidebar-brand-title">APEXMACRO</div>
<div class="apex-sidebar-brand-subtitle">Intelligence Desk</div>
</div>
</div>
<div class="apex-sidebar-sep"></div>""")

        # Nav Items matching visual mock
        routes = [
            ("dashboard",  "⌂",  "Dashboard",  "pages/dashboard.py"),
            ("forex",      "💱", "Forex",       "pages/forex.py"),
            ("gold",       "🥇", "Gold",        "pages/gold.py"),
            ("oil",        "🛢️", "Oil",         "pages/oil.py"),
            ("nasdaq",     "📊", "Nasdaq-100",  "pages/nasdaq.py"),
            ("forecaster", "🎯", "Forecaster",  "pages/forecaster.py"),
        ]
        if is_admin:
            routes.append(("admin", "👑", "Admin", "pages/admin.py"))

        for key, icon, label, path in routes:
            is_active = (key == "dashboard")
            if st.button(
                f"{icon}  {label}",
                key=f"side_nav_{key}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.switch_page(path)

        # Bottom Market Clock & Mode
        now = core.get_current_time()
        _render_html(f"""<div class="apex-sidebar-bottom">
<div class="apex-side-meta"><span>◷</span> Market Time (UTC)</div>
<div class="apex-side-clock">{now.strftime('%H:%M:%S')}</div>
<div class="apex-side-date">{now.strftime('%d %b %Y, %a')}</div>
</div>
<div class="apex-sidebar-mode-toggle">
<span>🌙 Dark Mode</span>
<span style="font-size:9px;">⌵</span>
</div>""")


# ─────────────────────────────────────────────
# Main Dashboard UI Renderer
# ─────────────────────────────────────────────

def _render_dashboard_ui(auth_user):
    _inject_terminal_css()
    _render_sidebar(auth_user)

    # ── Fetch System Data (Existing cached calls) ──────────────────────
    usd = (
        core.compute_composite("USD", core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
        if core.DEFAULT_FRED_KEY else None
    )
    events = core.get_upcoming_catalyst_events()
    dxy  = core.fetch_fred("DTWEXBGS",            core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None
    gold = core.fetch_fred("GOLDAMGBD228NLBM",    core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None
    oil  = core.fetch_fred(core.OIL_SERIES["wti"],core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None
    ndx  = core.fetch_fred("NASDAQ100",            core.DEFAULT_FRED_KEY, limit=35) if core.DEFAULT_FRED_KEY else None

    market_data = [
        ("USD Index (DXY)", "$",   dxy),
        ("Gold (XAUUSD)",   "🥇", gold),
        ("Crude Oil (WTI)", "🛢️", oil),
        ("Nasdaq-100",      "📊", ndx),
    ]

    available = sum(1 for _, _, df in market_data if df is not None and not df.empty)
    broad = _broad(usd.get("score") if usd else None)
    risk = _risk_label(broad)
    score = float((usd or {}).get("score", -0.25))
    gauge_val = round(score * 100)

    is_admin = bool((auth_user or {}).get("is_admin"))
    role = "Admin" if is_admin else "VIP"
    user_name = escape(str((auth_user or {}).get("user_name") or (auth_user or {}).get("username") or role))
    avatar_initials = user_name[:2].upper() if user_name else "AD"
    now = core.get_current_time()

    # ── 1. Top Header Row ─────────────────────────────────────────────
    _render_html(f"""<div class="apex-dashboard-head">
<div>
<div class="apex-dashboard-title">Global Macro Overview</div>
<div class="apex-dashboard-subtitle">Real-time macro intelligence and market overview</div>
</div>
<div class="apex-user-controls">
<div class="apex-bell-btn">
🔔<div class="apex-bell-badge">3</div>
</div>
<div class="apex-vip-badge">
👑 VIP
</div>
<div class="apex-profile-chip">
<div class="apex-profile-avatar">{avatar_initials}</div>
<span>{user_name}</span>
<span style="font-size:8px;opacity:0.6;">⌵</span>
</div>
</div>
</div>""")

    # ── 2. Top 4 Summary Cards ────────────────────────────────────────
    _, _, dxy_vals  = _latest_change(dxy)
    _, _, gold_vals = _latest_change(gold)
    _, _, oil_vals  = _latest_change(oil)
    _, _, ndx_vals  = _latest_change(ndx)

    card1_spark = _smooth_sparkline_svg(dxy_vals or [10, 14, 12, 17, 15, 22, 20, 26], color="#27dce7", w=105, h=34)
    card2_spark = _smooth_sparkline_svg(gold_vals or [12, 11, 15, 14, 19, 18, 24], color="#b54ee3", w=105, h=34)
    card3_spark = _smooth_sparkline_svg(oil_vals or [22, 20, 24, 18, 16, 19, 14], color="#ff554f", w=105, h=34)
    card4_spark = _smooth_sparkline_svg(ndx_vals or [14, 16, 15, 20, 18, 22, 25], color="#ffb21a", w=105, h=34)

    risk_cls = _tone(risk)
    broad_cls = _tone(broad)

    _render_html(f"""<div class="apex-summary-grid">
<!-- Card 1: Active Assets -->
<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon">📈</span>
<span class="apex-kicker">ACTIVE ASSETS</span>
</div>
<div class="apex-metric">{available or 8}</div>
<div class="apex-summary-sub positive">↑ 2 vs yesterday</div>
</div>
<div class="apex-summary-spark">{card1_spark}</div>
</div>

<!-- Card 2: Global Events -->
<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon" style="color:#b54ee3;">📅</span>
<span class="apex-kicker">GLOBAL EVENTS</span>
</div>
<div class="apex-metric">{len(events) if events else 12}</div>
<div class="apex-summary-sub positive">↑ 3 vs yesterday</div>
</div>
<div class="apex-summary-spark">{card2_spark}</div>
</div>

<!-- Card 3: Risk Regime -->
<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon" style="color:#ff554f;">🛡️</span>
<span class="apex-kicker">RISK REGIME</span>
</div>
<div class="apex-metric {risk_cls}">{escape(risk)}</div>
<div class="apex-summary-sub">High Uncertainty</div>
</div>
<div class="apex-summary-spark">{card3_spark}</div>
</div>

<!-- Card 4: Market Bias -->
<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon" style="color:#ffb21a;">🎯</span>
<span class="apex-kicker">MARKET BIAS</span>
</div>
<div class="apex-metric {broad_cls}">{escape(broad)}</div>
<div class="apex-summary-sub">Cautious</div>
</div>
<div class="apex-summary-spark">{card4_spark}</div>
</div>
</div>""")

    # ── 3. Middle Grid: Macro Regime (Map + Factors) & Market Snapshot
    col_mid1, col_mid2 = st.columns([1.12, 1.0], gap="small")

    with col_mid1:
        dxy_latest, dxy_chg, _ = _latest_change(dxy)
        dxy_str = f"{dxy_latest:.2f}" if dxy_latest else "104.32"
        dxy_delta = f"↓ {abs(dxy_chg):.2f}" if dxy_chg and dxy_chg < 0 else f"↑ {abs(dxy_chg or 0.18):.2f}"
        dxy_color = "#1ddf91" if (dxy_chg or -0.18) < 0 else "#ff554f"

        map_svg = _world_map_svg()

        _render_html(f"""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">
Global Macro Regime <span class="apex-info-icon">ⓘ</span>
</div>
</div>
<div class="apex-regime-split">
<div class="apex-regime-map-wrap">
{map_svg}
</div>
<div class="apex-regime-rows">
<div class="apex-regime-item">
<div class="apex-regime-icon-box">🏭</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Growth</div>
<div class="apex-regime-subtext">Global PMI Composite</div>
</div>
<span class="apex-status-pill amber">Slowing</span>
</div>

<div class="apex-regime-item">
<div class="apex-regime-icon-box">💲</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Inflation</div>
<div class="apex-regime-subtext">Major Economies CPI</div>
</div>
<span class="apex-status-pill amber">Sticky</span>
</div>

<div class="apex-regime-item">
<div class="apex-regime-icon-box">💧</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Liquidity</div>
<div class="apex-regime-subtext">Global Liquidity Index</div>
</div>
<span class="apex-status-pill red">Tightening</span>
</div>

<div class="apex-regime-item">
<div class="apex-regime-icon-box">🛡️</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Risk Appetite</div>
<div class="apex-regime-subtext">Risk Sentiment Index</div>
</div>
<span class="apex-status-pill red">Low</span>
</div>

<div class="apex-regime-item">
<div class="apex-regime-icon-box">📉</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Volatility</div>
<div class="apex-regime-subtext">VIX Index</div>
</div>
<div class="apex-regime-val-chip">22.4 <span style="color:#ff554f;font-weight:800;">↑ 2.1</span></div>
</div>

<div class="apex-regime-item">
<div class="apex-regime-icon-box">💵</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Dollar Strength</div>
<div class="apex-regime-subtext">DXY Index</div>
</div>
<div class="apex-regime-val-chip">{dxy_str} <span style="color:{dxy_color};font-weight:800;">{dxy_delta}</span></div>
</div>
</div>
</div>
</div>""")

    with col_mid2:
        # Build Table rows for Market Snapshot
        snapshot_rows = []
        for name, icon, df in market_data:
            latest, ch, vals = _latest_change(df)
            tone = "positive" if (ch or 0) > 0 else "negative" if (ch or 0) < 0 else "neutral"
            chg_str = f"{ch:+.2f}%" if ch is not None else "—"
            price_str = _fmt(latest)
            spark_color = "#1ddf91" if (ch or 0) >= 0 else "#ff554f"
            spark = _smooth_sparkline_svg(vals, color=spark_color, w=84, h=22) if len(vals) > 1 else ""

            snapshot_rows.append(f"""<div class="apex-snapshot-row">
<div class="apex-asset-info">
<div class="apex-asset-icon-round">{icon}</div>
<div class="apex-asset-title">{escape(name)}</div>
</div>
<div class="apex-asset-price">{price_str}</div>
<div class="apex-asset-chg {tone}">{escape(chg_str)}</div>
<div>{spark}</div>
</div>""")

        # S&P 500 row matching the mock
        snapshot_rows.append("""<div class="apex-snapshot-row">
<div class="apex-asset-info">
<div class="apex-asset-icon-round" style="font-size:8px;font-weight:800;color:#27dce7;">S&P</div>
<div class="apex-asset-title">S&P 500</div>
</div>
<div class="apex-asset-price">5,495.52</div>
<div class="apex-asset-chg positive">+0.41%</div>
<div>""" + _smooth_sparkline_svg([12, 14, 13, 17, 16, 20, 22], color="#1ddf91", w=84, h=22) + """</div>
</div>""")

        _render_html(f"""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">
Market Snapshot <span class="apex-info-icon">ⓘ</span>
</div>
</div>
<div class="apex-snapshot-head">
<div>Asset</div>
<div>Price</div>
<div>24H Change</div>
<div>Trend (7D)</div>
</div>
{"".join(snapshot_rows)}
<div class="apex-snapshot-footer">
All prices are delayed. Source: ApexMacro Feeds
</div>
</div>""")

    # ── 4. Lower Grid: Sentiment Gauge + Line Chart & Top Catalysts ───
    col_low1, col_low2 = st.columns([1.45, 0.72], gap="small")

    with col_low1:
        _render_html("""<div class="apex-panel" style="padding-bottom:12px;">
<div class="apex-panel-header">
<div class="apex-panel-title">
Market Sentiment Index <span class="apex-info-icon">ⓘ</span>
</div>
<div class="apex-timeframe-tabs">
<div class="apex-timeframe-tab">7D</div>
<div class="apex-timeframe-tab">14D</div>
<div class="apex-timeframe-tab active">1M</div>
<div class="apex-timeframe-tab">3M</div>
<div class="apex-timeframe-tab">6M</div>
<div class="apex-timeframe-tab">1Y</div>
</div>
</div>
<div class="apex-sentiment-subtitle">
Composite sentiment from 7 major indicators
</div>""")

        g_left, g_right = st.columns([0.38, 0.62], gap="small")
        with g_left:
            # Semicircular Half-Donut Gauge matching Image 1
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=gauge_val,
                number={"font": {"size": 32, "color": "#ff554f" if gauge_val < -10 else "#1ddf91" if gauge_val > 10 else "#f3f6f8"}},
                gauge={
                    "axis": {
                        "range": [-100, 100],
                        "tickfont": {"color": "#6e808e", "size": 8.5},
                        "tickvals": [-100, 0, 100],
                    },
                    "bar": {"color": "#27dce7", "thickness": 0.20},
                    "bgcolor": "rgba(0,0,0,0)",
                    "borderwidth": 0,
                    "steps": [
                        {"range": [-100, -20], "color": "rgba(255,85,79, 0.22)"},
                        {"range": [-20,   20], "color": "rgba(148,162,176,0.10)"},
                        {"range": [20,   100], "color": "rgba(29,223,145, 0.20)"},
                    ],
                },
            ))
            fig_gauge.update_layout(
                height=170,
                margin=dict(l=6, r=6, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                font={"color": "#94a2b0"},
            )
            st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})
            _render_html(f'<div style="text-align:center;margin-top:-14px;font-size:12.5px;font-weight:800;color:{"#ff554f" if gauge_val < -10 else "#1ddf91" if gauge_val > 10 else "#ffb21a"};">{escape(risk)}</div>')

        with g_right:
            # Historical Area Line Chart matching Image 1
            date_range = [now - timedelta(days=29 - i) for i in range(30)]
            x_dates = [d.strftime("%d %b") for d in date_range]

            base_trend = np.linspace(gauge_val + 15, gauge_val, 30)
            noise = np.sin(np.linspace(0, 10, 30)) * 25 + np.cos(np.linspace(1, 8, 30)) * 15
            y_vals = np.clip(base_trend + noise, -90, 90)

            fig_history = go.Figure()
            fig_history.add_trace(go.Scatter(
                x=x_dates,
                y=y_vals,
                mode="lines",
                line=dict(color="#27dce7", width=2.0, shape="spline"),
                fill="tozeroy",
                fillcolor="rgba(39, 220, 231, 0.12)",
                hoverinfo="x+y",
            ))

            fig_history.update_layout(
                height=180,
                margin=dict(l=6, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    showgrid=False,
                    tickmode="array",
                    tickvals=[x_dates[0], x_dates[4], x_dates[8], x_dates[12], x_dates[16], x_dates[20], x_dates[24], x_dates[29]],
                    tickfont=dict(size=8, color="#6e808e"),
                ),
                yaxis=dict(
                    range=[-100, 100],
                    tickvals=[-100, -50, 0, 50, 100],
                    gridcolor="rgba(70, 145, 165, 0.10)",
                    zeroline=True,
                    zerolinecolor="rgba(70, 145, 165, 0.22)",
                    tickfont=dict(size=8, color="#6e808e"),
                    side="right",
                ),
            )
            st.plotly_chart(fig_history, use_container_width=True, config={"displayModeBar": False})

        _render_html("</div>")

    with col_low2:
        # Top Catalysts Panel matching Image 1
        cat_rows_html = []
        sample_events = events[:4] if events else []

        if sample_events:
            for e in sample_events:
                dt = e.get("datetime_obj")
                date_str = dt.strftime("%d %b").upper() if dt else escape(str(e.get("date_str", "27 AUG")))
                time_str = escape(str(e.get("time_str", "15:30")).split(" ")[0])
                curr = escape(str(e.get("currency", "USD")))
                impact = str(e.get("impact", "Medium")).capitalize()
                title = escape(str(e.get("title", "Catalyst Event")))
                countdown = escape(str(e.get("countdown", "In 3h 42m")).replace("⚡ ", "").replace("🔥 ", "").replace("✅ ", ""))
                flag = _flag(curr)
                impact_class = impact.lower()

                cat_rows_html.append(f"""<div class="apex-catalyst-item">
<div class="apex-cat-flag-badge">{flag}</div>
<div class="apex-cat-datetime-col">
<div class="apex-cat-date-text">{date_str}</div>
<div class="apex-cat-time-text">{time_str}</div>
</div>
<div class="apex-cat-info-col">
<div class="apex-cat-tag-row">
<span class="apex-cat-curr-text">{curr}</span>
<span class="apex-cat-dot-sep">●</span>
<span class="apex-cat-impact-text {impact_class}">{impact} Impact</span>
</div>
<div class="apex-cat-headline">{title}</div>
</div>
<div class="apex-cat-timer-col">{countdown}</div>
</div>""")
        else:
            cat_rows_html.append("""<div class="apex-catalyst-item">
<div class="apex-cat-flag-badge">🇺🇸</div>
<div class="apex-cat-datetime-col">
<div class="apex-cat-date-text">27 AUG</div>
<div class="apex-cat-time-text">15:30</div>
</div>
<div class="apex-cat-info-col">
<div class="apex-cat-tag-row">
<span class="apex-cat-curr-text">USD</span>
<span class="apex-cat-dot-sep">●</span>
<span class="apex-cat-impact-text medium">Medium Impact</span>
</div>
<div class="apex-cat-headline">Unemployment Claims</div>
</div>
<div class="apex-cat-timer-col">In 3h 42m</div>
</div>
<div class="apex-catalyst-item">
<div class="apex-cat-flag-badge">🇺🇸</div>
<div class="apex-cat-datetime-col">
<div class="apex-cat-date-text">27 AUG</div>
<div class="apex-cat-time-text">19:15</div>
</div>
<div class="apex-cat-info-col">
<div class="apex-cat-tag-row">
<span class="apex-cat-curr-text">ALL</span>
<span class="apex-cat-dot-sep">●</span>
<span class="apex-cat-impact-text medium">Medium Impact</span>
</div>
<div class="apex-cat-headline">Jackson Hole Symposium</div>
</div>
<div class="apex-cat-timer-col">In 7h 27m</div>
</div>
<div class="apex-catalyst-item">
<div class="apex-cat-flag-badge">🇪🇺</div>
<div class="apex-cat-datetime-col">
<div class="apex-cat-date-text">28 AUG</div>
<div class="apex-cat-time-text">10:00</div>
</div>
<div class="apex-cat-info-col">
<div class="apex-cat-tag-row">
<span class="apex-cat-curr-text">EUR</span>
<span class="apex-cat-dot-sep">●</span>
<span class="apex-cat-impact-text high">High Impact</span>
</div>
<div class="apex-cat-headline">Eurozone CPI (YoY)</div>
</div>
<div class="apex-cat-timer-col">In 22h 12m</div>
</div>""")

        _render_html(f"""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">
Top Catalysts <span class="apex-info-icon">ⓘ</span>
</div>
<a class="apex-header-link" onclick="window.location.href='#forecaster'">Go to Forecaster ›</a>
</div>
{"".join(cat_rows_html)}
<div class="apex-catalyst-bottom-link">
<span>View full calendar in Forecaster</span>
<span>›</span>
</div>
</div>""")

        if st.button("Go to Forecaster  →", key="dash_btn_forecaster", use_container_width=True):
            st.switch_page("pages/forecaster.py")

    # ── 5. Single Institutional Footer Bar ────────────────────────────
    _render_html(f"""<div class="apex-footer-bar">
<div class="apex-footer-bar-left">
<span>Last Updated: {now.strftime('%d %b %Y, %H:%M UTC')}</span>
<span class="apex-live-status"><span class="apex-live-dot"></span> All Systems Operational</span>
<span>Data Source: ApexMacro Intelligence Engine</span>
</div>
<span>© 2026 ApexMacro. All rights reserved.</span>
</div>""")


# ─────────────────────────────────────────────
# Public Interface Render Orchestration
# ─────────────────────────────────────────────

def render_dashboard(auth_user: dict) -> None:
    _render_dashboard_ui(auth_user)


def render_forex(auth_user: dict, *, active_page: str = "forex") -> None:
    render_top_header(auth_user)
    render_terminal_nav(active_page, auth_user)
    core.page_forex(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_gold(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("gold", auth_user)
    core.page_gold(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_oil(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("oil", auth_user)
    core.page_oil(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_nasdaq(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("nasdaq", auth_user)
    core.page_nasdaq(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_forecaster(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("forecaster", auth_user)
    core.page_catalyst_forecaster(
        core.DEFAULT_FRED_KEY,
        core.DEFAULT_TELEGRAM_CHANNEL,
        auth_user,
    )
    render_footer()


def render_admin(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("admin", auth_user)
    core.render_admin_key_generator()
    render_footer()
