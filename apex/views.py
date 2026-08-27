"""Thin render orchestration for authenticated pages."""
from . import production_core as core
from .ui.common import render_top_header, render_footer
from .ui.terminal_nav import render_terminal_nav
from html import escape
import streamlit as st
import plotly.graph_objects as go

def _broad(score):
    if score is None: return "Unavailable"
    detailed, _, _ = core.bias_from_score(float(score))
    return core._broad_regime(detailed)

def _tone(label):
    s=str(label).lower()
    if any(x in s for x in ("bear","risk-off","tight","low","negative","down")): return "negative"
    if any(x in s for x in ("bull","risk-on","strong","positive","up")): return "positive"
    if any(x in s for x in ("mixed","sticky","elevated","moderate")): return "warning"
    return "neutral"

def _fmt(v):
    try: return f"{float(v):,.2f}"
    except Exception: return "—"

def _latest_change(df):
    if df is None or df.empty: return None, None, []
    vals=[float(x) for x in df['value'].dropna().tolist()]
    if not vals: return None, None, []
    latest=vals[-1]; ch=(latest/vals[-2]-1)*100 if len(vals)>1 and vals[-2] else None
    return latest,ch,vals[-20:]

def _sidebar(auth_user):
    is_admin=bool(auth_user and auth_user.get('is_admin'))
    with st.sidebar:
        st.markdown('<div class="apex-sidebar-brand"><div class="apex-sidebar-logo">A</div><div><div class="apex-sidebar-brand-title">APEXMACRO</div><div class="apex-sidebar-brand-subtitle">Intelligence Desk</div></div></div>',unsafe_allow_html=True)
        routes=[('dashboard','⌂','Dashboard','pages/dashboard.py'),('forex','◉','Forex','pages/forex.py'),('gold','◆','Gold','pages/gold.py'),('oil','◔','Oil','pages/oil.py'),('nasdaq','▥','Nasdaq-100','pages/nasdaq.py'),('forecaster','▣','Forecaster','pages/forecaster.py')]
        if is_admin: routes.append(('admin','♛','Admin','pages/admin.py'))
        for key,icon,label,path in routes:
            if st.button(f"{icon}  {label}",key=f"dash_side_{key}",use_container_width=True,type='primary' if key=='dashboard' else 'secondary'):
                st.switch_page(path)
        now=core.get_current_time()
        st.markdown(f'<div class="apex-sidebar-bottom"><div class="apex-side-meta">Market Time</div><div class="apex-side-clock">{now.strftime("%H:%M:%S")}</div><div class="apex-side-date">{now.strftime("%d %b %Y")} · system timezone</div></div>',unsafe_allow_html=True)

def _css():
    st.markdown('''<style>
:root{--adb:#02080d;--cyan:#27dce7;--border:rgba(70,145,165,.20);--text:#f3f6f8;--muted:#94a2b0;--pos:#1ddf91;--neg:#ff625e;--warn:#ffb21a}
[data-testid="stAppViewContainer"]{background:radial-gradient(circle at 18% 0%,rgba(39,220,231,.035),transparent 28%),var(--adb)!important}
[data-testid="stSidebar"]{background:linear-gradient(180deg,rgba(3,17,26,.99),rgba(2,10,16,1))!important;border-right:1px solid rgba(30,200,215,.20)!important}
[data-testid="stSidebar"]>div:first-child{padding-top:18px!important}[data-testid="stSidebar"] [data-testid="stButton"] button{min-height:46px!important;border-radius:10px!important;text-align:left!important;justify-content:flex-start!important;font-size:14px!important;box-shadow:none!important;margin:2px 0!important}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"]{background:linear-gradient(90deg,rgba(20,210,225,.14),rgba(20,210,225,.04))!important;border:1px solid rgba(25,210,225,.35)!important;color:#28dfe8!important}
.apex-sidebar-brand{display:flex;align-items:center;gap:11px;padding:4px 6px 20px}.apex-sidebar-logo{width:42px;height:42px;display:grid;place-items:center;color:#27dce7;font-size:29px;font-weight:950;font-style:italic;text-shadow:0 0 12px rgba(39,220,231,.35)}.apex-sidebar-brand-title{font-size:17px;font-weight:850;letter-spacing:2px;color:#f5f7f9}.apex-sidebar-brand-subtitle{font-size:11px;color:#27dce7;margin-top:2px}.apex-sidebar-bottom{margin-top:24px;padding:14px;border:1px solid var(--border);border-radius:12px;background:rgba(7,25,35,.55)}.apex-side-meta{font-size:11px;color:var(--muted)}.apex-side-clock{font-size:20px;font-weight:800;color:var(--text);margin-top:5px}.apex-side-date{font-size:10px;color:var(--muted);margin-top:3px}
.block-container{max-width:1800px!important;padding:24px 28px 32px!important}.apex-dashboard-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}.apex-dashboard-title{font-size:31px;font-weight:800;line-height:1.08;color:var(--text)}.apex-dashboard-subtitle{margin-top:6px;font-size:14px;color:var(--muted)}.apex-user-chip{padding:9px 12px;border:1px solid var(--border);border-radius:999px;color:#cbd5dc;font-size:12px;background:rgba(7,25,35,.65)}
.apex-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:14px}.apex-summary-card,.apex-panel{min-width:0;box-sizing:border-box;background:linear-gradient(145deg,rgba(7,25,35,.92),rgba(3,15,23,.97));border:1px solid var(--border);box-shadow:inset 0 1px 0 rgba(255,255,255,.018);border-radius:12px}.apex-summary-card{min-height:108px;padding:15px 16px}.apex-kicker{font-size:10px;font-weight:800;letter-spacing:.6px;color:#a9b4bd;text-transform:uppercase}.apex-metric{font-size:25px;font-weight:850;color:var(--text);margin-top:7px}.negative{color:var(--neg)!important}.positive{color:var(--pos)!important}.warning{color:var(--warn)!important}.apex-meta{font-size:11px;color:var(--muted);margin-top:4px;line-height:1.35}
.apex-panel{padding:17px 18px;margin-bottom:14px}.apex-panel-title{font-size:16px;font-weight:780;color:var(--text);margin-bottom:12px}.apex-regime-row{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:10px;min-height:49px;border-bottom:1px solid rgba(90,145,165,.10)}.apex-regime-row:last-child{border-bottom:0}.apex-regime-icon{color:var(--cyan);font-size:15px}.apex-regime-name{font-size:12px;font-weight:700;color:#e7edf1}.apex-regime-sub{font-size:10px;color:var(--muted);margin-top:2px}.apex-pill{font-size:10px;padding:5px 9px;border-radius:7px;border:1px solid var(--border);color:#b8c3cb;background:rgba(255,255,255,.025)}.apex-pill.negative{border-color:rgba(255,98,94,.24);background:rgba(255,98,94,.07)}.apex-pill.positive{border-color:rgba(29,223,145,.24);background:rgba(29,223,145,.07)}.apex-pill.warning{border-color:rgba(255,178,26,.22);background:rgba(255,178,26,.07)}
.apex-market-head,.apex-market-row{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(76px,.72fr) minmax(68px,.58fr) minmax(90px,.8fr);gap:8px;align-items:center}.apex-market-head{font-size:9px;text-transform:uppercase;color:#758694;padding:0 0 7px}.apex-market-row{min-height:48px;border-top:1px solid rgba(90,145,165,.10);font-size:11px}.apex-asset{font-weight:700;color:#e8eef2}.apex-spark{overflow:hidden}.apex-catalyst{padding:12px 0;border-top:1px solid rgba(90,145,165,.10);display:grid;grid-template-columns:64px 48px minmax(0,1fr);gap:10px;align-items:center}.apex-cat-date{font-size:10px;font-weight:800;color:#dce5ea}.apex-cat-time,.apex-cat-meta{font-size:9px;color:var(--muted)}.apex-cat-title{font-size:11px;font-weight:700;color:#eef3f6;line-height:1.3}.apex-impact{font-size:9px;color:var(--warn)}.apex-sent-copy{font-size:12px;color:var(--muted);line-height:1.6}.apex-sent-big{font-size:26px;font-weight:850;margin-bottom:5px}.apex-sent-note{margin-top:8px;font-size:10px;color:#738694}
@media(min-width:1024px){[data-testid="stSidebar"]{min-width:240px!important;max-width:240px!important;width:240px!important}.apex-summary-card:hover,.apex-panel:hover{border-color:rgba(35,210,220,.30)}}
@media(min-width:769px) and (max-width:1100px){[data-testid="stSidebar"]{min-width:200px!important;max-width:200px!important;width:200px!important}.block-container{padding:20px!important}.apex-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:768px){.block-container{padding:16px 14px 26px!important}.apex-dashboard-head{display:block}.apex-user-chip{display:inline-block;margin-top:12px}.apex-dashboard-title{font-size:25px}.apex-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.apex-summary-card{min-height:96px;padding:13px}.apex-metric{font-size:21px}.apex-panel{padding:14px}.apex-market-head{display:none}.apex-market-row{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:'asset price' 'change spark';padding:10px 0;gap:5px 10px}.apex-market-row .apex-asset{grid-area:asset}.apex-market-row .apex-price{grid-area:price;text-align:right}.apex-market-row .apex-change{grid-area:change}.apex-market-row .apex-spark{grid-area:spark;text-align:right}.apex-catalyst{grid-template-columns:58px minmax(0,1fr)}.apex-catalyst>div:nth-child(2){display:none}}
@media(max-width:370px){.apex-summary-grid{grid-template-columns:1fr}}
</style>''',unsafe_allow_html=True)

def _render_dashboard_ui(auth_user):
    _css(); _sidebar(auth_user)
    usd=core.compute_composite('USD',core.DEFAULT_FRED_KEY,core.DEFAULT_TELEGRAM_CHANNEL) if core.DEFAULT_FRED_KEY else None
    events=core.get_upcoming_catalyst_events()
    dxy=core.fetch_fred('DTWEXBGS',core.DEFAULT_FRED_KEY,limit=35) if core.DEFAULT_FRED_KEY else None
    gold=core.fetch_fred('GOLDAMGBD228NLBM',core.DEFAULT_FRED_KEY,limit=35) if core.DEFAULT_FRED_KEY else None
    oil=core.fetch_fred(core.OIL_SERIES['wti'],core.DEFAULT_FRED_KEY,limit=35) if core.DEFAULT_FRED_KEY else None
    ndx=core.fetch_fred('NASDAQ100',core.DEFAULT_FRED_KEY,limit=35) if core.DEFAULT_FRED_KEY else None
    market=[('USD Index',dxy),('Gold (XAUUSD)',gold),('Crude Oil (WTI)',oil),('Nasdaq-100',ndx)]
    available=sum(1 for _,df in market if df is not None and not df.empty)
    broad=_broad(usd.get('score') if usd else None); risk={'Bearish':'Risk-Off','Bullish':'Risk-On','Neutral':'Neutral'}.get(broad,broad)
    user_name=escape(str((auth_user or {}).get('user_name','VIP'))); role='Admin' if (auth_user or {}).get('is_admin') else 'VIP'
    st.markdown(f'<div class="apex-dashboard-head"><div><div class="apex-dashboard-title">Global Macro Overview</div><div class="apex-dashboard-subtitle">Real-time macro intelligence and market overview</div></div><div class="apex-user-chip">{"♛" if role=="Admin" else "♢"} {role} · {user_name}</div></div>',unsafe_allow_html=True)
    st.markdown(f'''<div class="apex-summary-grid"><div class="apex-summary-card"><div class="apex-kicker">Active Market Feeds</div><div class="apex-metric">{available}/4</div><div class="apex-meta">Current dashboard market sources available</div></div><div class="apex-summary-card"><div class="apex-kicker">Global Events</div><div class="apex-metric">{len(events)}</div><div class="apex-meta">Upcoming Catalyst Forecaster events</div></div><div class="apex-summary-card"><div class="apex-kicker">Risk Regime</div><div class="apex-metric {_tone(risk)}">{escape(risk)}</div><div class="apex-meta">Existing USD composite regime proxy</div></div><div class="apex-summary-card"><div class="apex-kicker">Market Bias</div><div class="apex-metric {_tone(broad)}">{escape(broad)}</div><div class="apex-meta">Existing broad composite state</div></div></div>''',unsafe_allow_html=True)
    left,right=st.columns([1.08,1],gap='small')
    regime=[]
    for r in (usd or {}).get('rows',[])[:6]: regime.append((core.CAT_ICONS.get(r.get('cat'),'◌'),r.get('name','Macro Factor'),r.get('date',''),_broad(r.get('score'))))
    with left:
        html=''.join(f'<div class="apex-regime-row"><div class="apex-regime-icon">{escape(str(i))}</div><div><div class="apex-regime-name">{escape(str(n))}</div><div class="apex-regime-sub">Latest: {escape(str(d))}</div></div><span class="apex-pill {_tone(l)}">{escape(l)}</span></div>' for i,n,d,l in regime) or '<div class="apex-meta">Macro regime data is temporarily unavailable.</div>'
        st.markdown(f'<div class="apex-panel"><div class="apex-panel-title">Global Macro Regime</div>{html}</div>',unsafe_allow_html=True)
    with right:
        mrows=[]
        for name,df in market:
            latest,ch,vals=_latest_change(df); tone='positive' if (ch or 0)>0 else 'negative' if (ch or 0)<0 else 'neutral'; c='—' if ch is None else f'{ch:+.2f}%'; spark=core.spark_svg(vals,w=92,h=28,pos_good=True) if len(vals)>1 else ''
            mrows.append(f'<div class="apex-market-row"><div class="apex-asset">{escape(name)}</div><div class="apex-price">{_fmt(latest)}</div><div class="apex-change {tone}">{c}</div><div class="apex-spark">{spark}</div></div>')
        st.markdown(f'<div class="apex-panel"><div class="apex-panel-title">Market Snapshot</div><div class="apex-market-head"><div>Asset</div><div>Latest</div><div>Change</div><div>Trend</div></div>{"".join(mrows)}</div>',unsafe_allow_html=True)
    sleft,sright=st.columns([1.45,.7],gap='small')
    score=float((usd or {}).get('score',0.0)); gauge=max(-100,min(100,score*100)); glabel=_broad(score)
    with sleft:
        st.markdown('<div class="apex-panel"><div class="apex-panel-title">Market Sentiment Index</div>',unsafe_allow_html=True)
        g1,g2=st.columns([.42,.58])
        with g1:
            fig=go.Figure(go.Indicator(mode='gauge+number',value=gauge,number={'font':{'size':28,'color':'#f3f6f8'}},gauge={'axis':{'range':[-100,100],'tickfont':{'color':'#7f919f','size':9}},'bar':{'color':'#27dce7'},'bgcolor':'rgba(0,0,0,0)','borderwidth':0,'steps':[{'range':[-100,-15],'color':'rgba(255,98,94,.16)'},{'range':[-15,15],'color':'rgba(148,162,176,.08)'},{'range':[15,100],'color':'rgba(29,223,145,.14)'}]})); fig.update_layout(height=205,margin=dict(l=18,r=18,t=18,b=8),paper_bgcolor='rgba(0,0,0,0)',font={'color':'#94a2b0'}); st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False})
        with g2: st.markdown(f'<div style="padding:28px 6px 10px"><div class="apex-sent-big {_tone(glabel)}">{escape(glabel)}</div><div class="apex-sent-copy">Visualization of the existing USD composite score. Macro and news weights remain exactly as defined by the ApexMacro engine.</div><div class="apex-sent-note">Historical composite sentiment is not fabricated; no synthetic history is shown.</div></div>',unsafe_allow_html=True)
        st.markdown('</div>',unsafe_allow_html=True)
    with sright:
        cats=[]
        for e in events[:5]:
            dt=e.get('datetime_obj'); date=dt.strftime('%d %b').upper() if dt else e.get('date_str','—'); time=e.get('time_str','—').split(' ')[0]
            cats.append(f'<div class="apex-catalyst"><div><div class="apex-cat-date">{escape(str(date))}</div><div class="apex-cat-time">{escape(str(time))}</div></div><div><div class="apex-cat-date">{escape(str(e.get("currency","—")))}</div><div class="apex-impact">{escape(str(e.get("impact","—")))}</div></div><div><div class="apex-cat-title">{escape(str(e.get("title","Event")))}</div><div class="apex-cat-meta">{escape(str(e.get("countdown","")))}</div></div></div>')
        body=''.join(cats) if cats else '<div class="apex-meta">No upcoming events available.</div>'
        st.markdown(f'<div class="apex-panel"><div class="apex-panel-title">Top Catalysts</div>{body}</div>',unsafe_allow_html=True)
        if st.button('Go to Forecaster  →',key='dash_go_forecaster',use_container_width=True): st.switch_page('pages/forecaster.py')

def render_dashboard(auth_user: dict) -> None:
    _render_dashboard_ui(auth_user)
    render_footer()

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
