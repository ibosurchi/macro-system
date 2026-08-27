"""Thin render orchestration for authenticated pages.

Responsive Institutional Macro Terminal UI (Desktop, Tablet & Mobile).
All strategies, data sources, and calculations remain 100% untouched.
"""
from __future__ import annotations

from html import escape
from datetime import datetime, timedelta, timezone
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


def _render_plotly(fig: go.Figure) -> None:
    """Render Plotly figure supporting latest Streamlit width='stretch' syntax."""
    try:
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    except (TypeError, ValueError):
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


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

*, *::before, *::after {
  box-sizing: border-box !important;
}

html, body {
  overflow-x: clip !important;
  max-width: 100vw !important;
  width: 100% !important;
  position: relative !important;
  touch-action: pan-y !important;
  margin: 0 !important;
  padding: 0 !important;
}

[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
.stApp,
.main,
.block-container {
  overflow-x: clip !important;
  max-width: 100vw !important;
  width: 100% !important;
  box-sizing: border-box !important;
}

/* Hide default streamlit headers and deploy buttons */
header[data-testid="stHeader"],
.stDeployButton,
[data-testid="stHeaderActionElements"],
[data-testid="stToolbar"] {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
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
  .apex-mobile-header-bar-container {
    display: none !important;
  }
}

@media (max-width: 1023px) {
  [data-testid="stSidebar"] {
    display: none !important;
  }
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] {
    display: none !important;
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

/* ── MOBILE HEADER (LOGO ON LEFT, COMPACT ☰ ON RIGHT) ───── */
.apex-mobile-header-bar-container,
.st-key-apex_mobile_header {
  display: block !important;
  margin: 0 0 12px !important;
  padding: 0 8px 0 2px !important;
  box-sizing: border-box !important;
}

.apex-mobile-header-bar-container [data-testid="stHorizontalBlock"],
.st-key-apex_mobile_header [data-testid="stHorizontalBlock"],
.apex-mobile-drawer-head [data-testid="stHorizontalBlock"] {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 8px !important;
  width: 100% !important;
}

/* First Column: Logo on Left */
.apex-mobile-header-bar-container [data-testid="column"]:first-child,
.st-key-apex_mobile_header [data-testid="column"]:first-child,
.apex-mobile-drawer-head [data-testid="column"]:first-child {
  flex: 1 1 auto !important;
  min-width: 0 !important;
}

/* Last Column: Compact ☰ Button on Right */
.apex-mobile-header-bar-container [data-testid="column"]:last-child,
.st-key-apex_mobile_header [data-testid="column"]:last-child,
.apex-mobile-drawer-head [data-testid="column"]:last-child {
  flex: 0 0 46px !important;
  min-width: 46px !important;
  width: 46px !important;
  max-width: 46px !important;
  display: flex !important;
  justify-content: flex-end !important;
  padding-right: 6px !important;
}

.apex-mobile-header-bar-container button,
.st-key-apex_mobile_header button,
.apex-mobile-drawer-head button {
  width: 40px !important;
  height: 40px !important;
  min-height: 40px !important;
  max-width: 40px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 10px !important;
  border: 1px solid rgba(39, 220, 231, 0.45) !important;
  background: rgba(6, 22, 31, 0.90) !important;
  color: #27dce7 !important;
  font-size: 19px !important;
  font-weight: 800 !important;
  padding: 0 !important;
  margin-right: 6px !important;
  box-shadow: 0 0 14px rgba(39, 220, 231, 0.15) !important;
  cursor: pointer !important;
  transition: transform 150ms ease !important;
}

/* ── MOBILE OPEN CONTAINER (LEFT 22% DIMMED + RIGHT 78% DRAWER) ── */
.apex-mobile-open-container {
  margin: 0 0 16px !important;
  padding: 0 !important;
}

.apex-mobile-open-container [data-testid="stHorizontalBlock"] {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  align-items: stretch !important;
  gap: 0 !important;
  width: 100% !important;
  min-height: 82vh !important;
}

/* Left Dimmed Backdrop Column (22%) */
.apex-mobile-open-container [data-testid="column"]:first-child {
  flex: 0 0 22% !important;
  width: 22% !important;
  max-width: 22% !important;
  min-width: 0 !important;
  background: rgba(1, 7, 12, 0.58) !important;
  backdrop-filter: blur(8px) !important;
  -webkit-backdrop-filter: blur(8px) !important;
  border-radius: 12px 0 0 12px !important;
  padding: 10px 4px !important;
}

.apex-mobile-dim-backdrop button {
  width: 36px !important;
  height: 36px !important;
  min-height: 36px !important;
  max-width: 36px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 8px !important;
  border: 1px solid rgba(39, 220, 231, 0.40) !important;
  background: rgba(7, 25, 35, 0.90) !important;
  color: #27dce7 !important;
  font-size: 16px !important;
  font-weight: 700 !important;
  padding: 0 !important;
  margin: 0 auto !important;
}

/* Right Glass Drawer Column (78%, max 320px) */
.apex-mobile-open-container [data-testid="column"]:last-child {
  flex: 0 0 78% !important;
  width: 78% !important;
  max-width: 320px !important;
  min-width: 0 !important;
  margin-left: auto !important;
  background: linear-gradient(160deg, rgba(5, 22, 32, 0.985), rgba(2, 12, 19, 0.995)) !important;
  border-left: 1px solid rgba(30, 205, 220, 0.32) !important;
  box-shadow: -14px 0 35px rgba(0, 0, 0, 0.45) !important;
  border-radius: 0 12px 12px 0 !important;
  padding: 12px 8px 18px !important;
}

.apex-mobile-drawer-brand {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  padding: 0 4px 10px !important;
  border-bottom: 1px solid rgba(70, 145, 165, 0.18) !important;
  margin-bottom: 8px !important;
}

/* ── COMPACT DRAWER MENU ITEMS ──────────────────────────── */
.apex-mobile-menu-list {
  display: flex !important;
  flex-direction: column !important;
  margin-top: 4px !important;
  margin-bottom: 12px !important;
}

.apex-mobile-menu-list button {
  min-height: 44px !important;
  height: 44px !important;
  padding: 8px 10px !important;
  margin-bottom: 6px !important;
  border-radius: 9px !important;
  font-size: 13px !important;
  font-weight: 550 !important;
  text-align: left !important;
  justify-content: flex-start !important;
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  letter-spacing: 0.15px !important;
}

.apex-mobile-menu-list button[kind="primary"] {
  color: #29E1E9 !important;
  background: linear-gradient(90deg, rgba(25, 215, 225, 0.14), rgba(25, 215, 225, 0.035)) !important;
  border: 1px solid rgba(25, 215, 225, 0.38) !important;
  box-shadow: inset 0 0 14px rgba(39, 220, 231, 0.06), 0 0 10px rgba(39, 220, 231, 0.08) !important;
}

.apex-mobile-menu-list button[kind="secondary"] {
  background: rgba(7, 25, 35, 0.50) !important;
  border: 1px solid rgba(70, 145, 165, 0.20) !important;
  color: #d8e5ee !important;
}

.apex-mobile-menu-list button[kind="secondary"]:hover {
  border-color: rgba(39, 220, 231, 0.38) !important;
  color: #ffffff !important;
  background: rgba(10, 32, 45, 0.70) !important;
}

/* ── ACCOUNT / ADMIN CARD INSIDE DRAWER ─────────────────── */
.apex-mobile-account-card {
  width: 100% !important;
  min-width: 0 !important;
  box-sizing: border-box !important;
  padding: 10px 8px !important;
  overflow: hidden !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  border-radius: 10px !important;
  background: rgba(7, 28, 39, 0.75) !important;
  border: 1px solid rgba(24, 205, 220, 0.28) !important;
  margin-top: 8px !important;
}

.apex-mobile-profile-left {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  min-width: 0 !important;
  overflow: hidden !important;
}

.apex-mobile-profile-avatar {
  border-radius: 50% !important;
  border: 1px solid rgba(39, 220, 231, 0.40) !important;
  background: rgba(39, 220, 231, 0.10) !important;
  color: #27dce7 !important;
  font-weight: 800 !important;
  display: grid !important;
  place-items: center !important;
  flex-shrink: 0 !important;
}

.apex-mobile-profile-name {
  font-weight: 800 !important;
  color: #f3f7f9 !important;
}

.apex-mobile-profile-role {
  color: #27dce7 !important;
  margin-top: 1px !important;
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

.apex-sidebar-brand.apex-brand-centered {
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  text-align: center !important;
  gap: 10px !important;
  padding: 0 !important;
  margin: 0 auto !important;
}

.apex-auth-desktop-strip {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin: 0 0 10px;
}

@media (max-width: 1023px) {
  .apex-auth-desktop-strip {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
  }
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
  .main .block-container,
  [data-testid="stAppViewBlockContainer"],
  .block-container {
    padding-top: 0 !important;
    padding-left: 10px !important;
    padding-right: 10px !important;
    padding-bottom: 24px !important;
    margin-top: -30px !important;
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
    margin-bottom: 8px;
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
# Main Dashboard UI Renderer
# ─────────────────────────────────────────────

def _render_dashboard_ui(auth_user):
    """Authenticated dashboard built only from real ApexMacro data.

    Presentation rules:
    - no demo/fallback numbers
    - no synthetic sentiment history
    - no fake operational status
    - live/near-live snapshot reuses ApexMacro's existing Yahoo tactical feed
    """
    _inject_terminal_css()

    if render_terminal_nav("dashboard", auth_user):
        return

    usd = (
        core.compute_composite("USD", core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
        if core.DEFAULT_FRED_KEY else None
    )
    events = core.get_upcoming_catalyst_events()
    tracked_assets = len(core.ALERT_ASSETS)

    usd_score = float(usd.get("score")) if usd and usd.get("score") is not None else None

    gold_score = None
    ndx_score = None
    oil_score = None
    if core.DEFAULT_FRED_KEY:
        try:
            gold_score = core._calc_gold_score_only(
                core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL
            )[0]
        except Exception:
            gold_score = None
        try:
            ndx_score = core._calc_ndx_score_only(
                core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL
            )[0]
        except Exception:
            ndx_score = None
        try:
            oil_check = core.fetch_fred(core.OIL_SERIES["wti"], core.DEFAULT_FRED_KEY, limit=30)
            if oil_check is not None and not oil_check.empty:
                oil_score = core._calc_oil_score_only(
                    core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL
                )[0]
        except Exception:
            oil_score = None

    model_scores = {"USD": usd_score, "Gold": gold_score, "Oil": oil_score, "Nasdaq": ndx_score}
    model_regimes = {k: _broad(v) for k, v in model_scores.items() if v is not None}

    bulls = sum(1 for x in model_regimes.values() if x == "Bullish")
    bears = sum(1 for x in model_regimes.values() if x == "Bearish")
    neutrals = sum(1 for x in model_regimes.values() if x == "Neutral")
    if not model_regimes:
        cross_asset_bias = "Unavailable"
    elif bulls > bears and bulls >= 2:
        cross_asset_bias = "Bullish"
    elif bears > bulls and bears >= 2:
        cross_asset_bias = "Bearish"
    elif neutrals == len(model_regimes):
        cross_asset_bias = "Neutral"
    else:
        cross_asset_bias = "Mixed"

    usd_regime = _broad(usd_score) if usd_score is not None else "Unavailable"

    is_admin = bool((auth_user or {}).get("is_admin"))
    role = "Admin" if is_admin else "VIP"
    user_name = escape(
        str((auth_user or {}).get("user_name")
            or (auth_user or {}).get("username")
            or role)
    )
    avatar_initials = user_name[:2].upper() if user_name else "AP"
    now = core.get_current_time()

    # Reuse existing near-live Yahoo tactical market feed (55-second cache).
    live_specs = [
        ("USD Index (DXY)", "$",   ["DX-Y.NYB"]),
        ("Gold (XAUUSD)",   "🥇", ["XAUUSD=X", "GC=F"]),
        ("Crude Oil (WTI)", "🛢️", ["CL=F"]),
        ("Nasdaq-100",      "📊", ["^NDX", "NQ=F"]),
    ]

    def _live_series(symbols):
        for symbol in symbols:
            try:
                df = core._fetch_tactical_price_series(symbol)
                if df is not None and not df.empty and len(df) >= 2:
                    return df, symbol
            except Exception:
                continue
        return None, ""

    def _live_stats(df):
        if df is None or df.empty:
            return None, None, [], None
        data = df.dropna(subset=["close"]).copy()
        if data.empty:
            return None, None, [], None
        latest = float(data["close"].iloc[-1])
        latest_ts = int(data["ts"].iloc[-1])
        target = latest_ts - 24 * 3600
        older = data[data["ts"] <= target]
        ch = None
        if not older.empty:
            prev = float(older["close"].iloc[-1])
            if prev:
                ch = ((latest / prev) - 1.0) * 100.0
        vals = [float(x) for x in data["close"].tail(180).tolist()]
        stamp = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
        return latest, ch, vals, stamp

    live_market = []
    for name, icon, symbols in live_specs:
        df, used_symbol = _live_series(symbols)
        latest, ch, vals, stamp = _live_stats(df)
        live_market.append({
            "name": name, "icon": icon, "symbol": used_symbol,
            "latest": latest, "change": ch, "vals": vals, "stamp": stamp,
            "live": latest is not None,
        })

    live_count = sum(1 for item in live_market if item["live"])
    latest_feed_stamp = max(
        (item["stamp"] for item in live_market if item["stamp"] is not None),
        default=None
    )

    _render_html(f"""<div class="apex-dashboard-head">
<div>
<div class="apex-dashboard-title">Global Macro Overview</div>
<div class="apex-dashboard-subtitle">Live market prices, existing macro models and upcoming catalysts</div>
</div>
<div class="apex-user-controls">
<div class="apex-vip-badge">{'♛ ADMIN' if is_admin else '♢ VIP'}</div>
<div class="apex-profile-chip">
<div class="apex-profile-avatar">{avatar_initials}</div>
<span>{user_name}</span>
</div>
</div>
</div>""")

    usd_cls = _tone(usd_regime)
    cross_cls = _tone(cross_asset_bias)

    _render_html(f"""<div class="apex-summary-grid">
<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon">◉</span>
<span class="apex-kicker">TRACKED ASSETS</span>
</div>
<div class="apex-metric">{tracked_assets}</div>
<div class="apex-summary-sub">Configured models and alert assets</div>
</div>
</div>

<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon" style="color:#b54ee3;">▣</span>
<span class="apex-kicker">UPCOMING EVENTS</span>
</div>
<div class="apex-metric">{len(events)}</div>
<div class="apex-summary-sub">Current Forecaster calendar window</div>
</div>
</div>

<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon">💵</span>
<span class="apex-kicker">USD MACRO REGIME</span>
</div>
<div class="apex-metric {usd_cls}">{escape(usd_regime)}</div>
<div class="apex-summary-sub">Existing USD macro + news composite</div>
</div>
</div>

<div class="apex-summary-card">
<div class="apex-summary-left">
<div class="apex-summary-kicker-row">
<span class="apex-summary-icon" style="color:#ffb21a;">◎</span>
<span class="apex-kicker">CROSS-ASSET BIAS</span>
</div>
<div class="apex-metric {cross_cls}">{escape(cross_asset_bias)}</div>
<div class="apex-summary-sub">{len(model_regimes)} model regimes currently available</div>
</div>
</div>
</div>""")

    # Real USD macro categories from the existing model rows.
    rows = (usd or {}).get("rows", [])
    category_scores = {}
    category_dates = {}
    category_counts = {}
    for row in rows:
        cat = str(row.get("cat", ""))
        group = "labor" if cat in {"labor_pos", "labor_neg"} else cat
        if group not in {"inflation", "growth", "labor", "rate"}:
            continue
        category_scores.setdefault(group, []).append(float(row.get("score", 0.0)))
        category_counts[group] = category_counts.get(group, 0) + 1
        d = str(row.get("date", ""))
        if d and d > category_dates.get(group, ""):
            category_dates[group] = d

    display_groups = [
        ("growth", "🏭", "Growth", "Activity and demand"),
        ("inflation", "📈", "Inflation", "Price pressure"),
        ("labor", "👥", "Labor Market", "Employment and unemployment"),
        ("rate", "🏦", "Policy Rates", "Central-bank policy"),
    ]
    regime_html = []
    for group, icon, label, sub in display_groups:
        vals = category_scores.get(group, [])
        avg = float(np.mean(vals)) if vals else None
        status = _broad(avg) if avg is not None else "Unavailable"
        date_txt = category_dates.get(group, "—")
        count_txt = category_counts.get(group, 0)
        score_txt = f"{avg:+.2f}" if avg is not None else "—"
        regime_html.append(f"""<div class="apex-regime-item">
<div class="apex-regime-icon-box">{icon}</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">{label}</div>
<div class="apex-regime-subtext">{sub} · {count_txt} indicators · latest {escape(date_txt)}</div>
</div>
<div class="apex-regime-val-chip">{score_txt} <span class="{_tone(status)}">{escape(status)}</span></div>
</div>""")

    dxy_item = next((x for x in live_market if x["name"].startswith("USD Index")), None)
    if dxy_item and dxy_item["live"]:
        dxy_ch = dxy_item["change"]
        dxy_tone = "positive" if (dxy_ch or 0) > 0 else "negative" if (dxy_ch or 0) < 0 else "neutral"
        dxy_ch_txt = f"{dxy_ch:+.2f}%" if dxy_ch is not None else "—"
        regime_html.append(f"""<div class="apex-regime-item">
<div class="apex-regime-icon-box">💲</div>
<div class="apex-regime-label-group">
<div class="apex-regime-name">Dollar Price Action</div>
<div class="apex-regime-subtext">DXY live/near-live market feed</div>
</div>
<div class="apex-regime-val-chip">{_fmt(dxy_item["latest"])} <span class="{dxy_tone}">{dxy_ch_txt}</span></div>
</div>""")

    snapshot_rows = []
    for item in live_market:
        latest = item["latest"]
        ch = item["change"]
        vals = item["vals"]
        if latest is None:
            price_str = "Unavailable"
            chg_str = "—"
            tone = "neutral"
            spark = ""
        else:
            price_str = _fmt(latest)
            tone = "positive" if (ch or 0) > 0 else "negative" if (ch or 0) < 0 else "neutral"
            chg_str = f"{ch:+.2f}%" if ch is not None else "—"
            spark_color = "#1ddf91" if (ch or 0) >= 0 else "#ff554f"
            spark = _smooth_sparkline_svg(vals, color=spark_color, w=84, h=22) if len(vals) > 1 else ""

        snapshot_rows.append(f"""<div class="apex-snapshot-row">
<div class="apex-asset-info">
<div class="apex-asset-icon-round">{item["icon"]}</div>
<div>
<div class="apex-asset-title">{escape(item["name"])}</div>
<div style="font-size:8.5px;color:#6f8493;margin-top:2px;">{escape(item["symbol"] or "feed unavailable")}</div>
</div>
</div>
<div class="apex-asset-price">{escape(price_str)}</div>
<div class="apex-asset-chg {tone}">{escape(chg_str)}</div>
<div>{spark}</div>
</div>""")

    feed_stamp_text = (
        latest_feed_stamp.strftime("%d %b %Y %H:%M UTC")
        if latest_feed_stamp is not None else "No live feed timestamp available"
    )

    col_mid1, col_mid2 = st.columns([1.05, 1.0], gap="small")
    with col_mid1:
        _render_html(f"""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">US Macro Regime <span class="apex-info-icon">ⓘ</span></div>
</div>
<div style="font-size:10px;color:#708493;margin:-4px 0 10px;">
This panel summarizes the same USD indicators already used by the ApexMacro macro model. It is not a separate strategy.
</div>
<div class="apex-regime-rows">
{"".join(regime_html) if regime_html else '<div class="apex-meta">USD macro data unavailable.</div>'}
</div>
</div>""")

    with col_mid2:
        _render_html(f"""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">Market Snapshot <span class="apex-info-icon">ⓘ</span></div>
</div>
<div class="apex-snapshot-head">
<div>Asset</div>
<div>Price</div>
<div>24H Change</div>
<div>Intraday Trend</div>
</div>
{"".join(snapshot_rows)}
<div class="apex-snapshot-footer">
Live/near-live Yahoo market feed already used by ApexMacro Tactical Move · cached about 55 seconds · latest feed: {escape(feed_stamp_text)}
</div>
</div>""")

    col_low1, col_low2 = st.columns([1.38, 0.74], gap="small")
    with col_low1:
        if usd_score is not None:
            gauge_val = max(-100, min(100, round(usd_score * 100)))
            gauge_color = "#ff554f" if gauge_val < -15 else "#1ddf91" if gauge_val > 15 else "#ffb21a"
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=gauge_val,
                number={"font": {"size": 31, "color": gauge_color}},
                gauge={
                    "axis": {"range": [-100, 100], "tickfont": {"color": "#6e808e", "size": 8.5},
                             "tickvals": [-100, -50, 0, 50, 100]},
                    "bar": {"color": gauge_color, "thickness": 0.20},
                    "bgcolor": "rgba(0,0,0,0)",
                    "borderwidth": 0,
                    "steps": [
                        {"range": [-100, -15], "color": "rgba(255,85,79,.18)"},
                        {"range": [-15, 15], "color": "rgba(148,162,176,.09)"},
                        {"range": [15, 100], "color": "rgba(29,223,145,.16)"},
                    ],
                },
            ))
            fig_gauge.update_layout(
                height=205, margin=dict(l=15, r=15, t=18, b=5),
                paper_bgcolor="rgba(0,0,0,0)", font={"color": "#94a2b0"}
            )
            _render_html("""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">USD Composite Score <span class="apex-info-icon">ⓘ</span></div>
</div>
<div class="apex-sentiment-subtitle">
Current score from the existing 50% macro-data + 50% asset-specific news model.
</div>""")
            g1, g2 = st.columns([0.42, 0.58], gap="small")
            with g1:
                _render_plotly(fig_gauge)
            with g2:
                macro_score = (usd or {}).get("macro_score")
                news_points = (usd or {}).get("news_points")
                macro_txt = f"{float(macro_score):+.3f}" if macro_score is not None else "—"
                news_txt = f"{float(news_points):+.3f}" if news_points is not None else "—"
                _render_html(f"""<div style="padding:22px 6px 8px;">
<div style="font-size:22px;font-weight:850;color:{gauge_color};margin-bottom:8px;">{escape(usd_regime)}</div>
<div style="font-size:11px;color:#94a2b0;line-height:1.65;">
<b style="color:#dfe7ec;">Final score:</b> {usd_score:+.3f}<br>
<b style="color:#dfe7ec;">Macro component:</b> {macro_txt}<br>
<b style="color:#dfe7ec;">News points:</b> {news_txt}<br><br>
Current-state gauge only. No synthetic historical sentiment series is generated.
</div>
</div>""")
            _render_html("</div>")
        else:
            _render_html("""<div class="apex-panel">
<div class="apex-panel-title">USD Composite Score</div>
<div class="apex-meta">Current USD composite is unavailable.</div>
</div>""")

    with col_low2:
        cat_rows_html = []
        for e in events[:5]:
            dt = e.get("datetime_obj")
            date_str = dt.strftime("%d %b").upper() if dt else escape(str(e.get("date_str", "—")))
            time_str = escape(str(e.get("time_str", "—")).split(" ")[0])
            curr = escape(str(e.get("currency", "ALL")))
            impact = str(e.get("impact", "Medium")).capitalize()
            title = escape(str(e.get("title", "Catalyst Event")))
            countdown = escape(str(e.get("countdown", "")).replace("⚡ ", "").replace("🔥 ", "").replace("✅ ", ""))
            cat_rows_html.append(f"""<div class="apex-catalyst-item">
<div class="apex-cat-flag-badge">{_flag(curr)}</div>
<div class="apex-cat-datetime-col">
<div class="apex-cat-date-text">{date_str}</div>
<div class="apex-cat-time-text">{time_str}</div>
</div>
<div class="apex-cat-info-col">
<div class="apex-cat-tag-row">
<span class="apex-cat-curr-text">{curr}</span>
<span class="apex-cat-dot-sep">●</span>
<span class="apex-cat-impact-text {impact.lower()}">{escape(impact)} Impact</span>
</div>
<div class="apex-cat-headline">{title}</div>
</div>
<div class="apex-cat-timer-col">{countdown}</div>
</div>""")

        _render_html(f"""<div class="apex-panel">
<div class="apex-panel-header">
<div class="apex-panel-title">Top Catalysts <span class="apex-info-icon">ⓘ</span></div>
</div>
{"".join(cat_rows_html) if cat_rows_html else '<div class="apex-meta">No upcoming Forecaster catalysts are available in the current calendar window.</div>'}
</div>""")
        if st.button("Go to Forecaster  →", key="dash_btn_forecaster", use_container_width=True):
            st.switch_page("pages/forecaster.py")

    _render_html(f"""<div class="apex-footer-bar">
<div class="apex-footer-bar-left">
<span>Dashboard rendered: {now.strftime('%d %b %Y, %H:%M')}</span>
<span>Live feeds available: {live_count}/4</span>
<span>Macro source: FRED + ApexMacro news intelligence</span>
</div>
<span>© 2026 ApexMacro</span>
</div>""")


# ─────────────────────────────────────────────
# Public Interface Render Orchestration
# ─────────────────────────────────────────────

def render_dashboard(auth_user: dict) -> None:
    _render_dashboard_ui(auth_user)


def render_forex(auth_user: dict, *, active_page: str = "forex") -> None:
    render_top_header(auth_user)
    if render_terminal_nav(active_page, auth_user):
        return
    core.page_forex(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_gold(auth_user: dict) -> None:
    render_top_header(auth_user)
    if render_terminal_nav("gold", auth_user):
        return
    core.page_gold(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_oil(auth_user: dict) -> None:
    render_top_header(auth_user)
    if render_terminal_nav("oil", auth_user):
        return
    core.page_oil(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_nasdaq(auth_user: dict) -> None:
    render_top_header(auth_user)
    if render_terminal_nav("nasdaq", auth_user):
        return
    core.page_nasdaq(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()


def render_forecaster(auth_user: dict) -> None:
    render_top_header(auth_user)
    if render_terminal_nav("forecaster", auth_user):
        return
    core.page_catalyst_forecaster(
        core.DEFAULT_FRED_KEY,
        core.DEFAULT_TELEGRAM_CHANNEL,
        auth_user,
    )
    render_footer()


def render_admin(auth_user: dict) -> None:
    render_top_header(auth_user)
    if render_terminal_nav("admin", auth_user):
        return
    core.render_admin_key_generator()
    render_footer()
