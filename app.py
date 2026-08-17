import streamlit as st
import pandas as pd
import numpy as np
import requests

# ڕێکخستنی لاپەڕە
st.set_page_config(page_title="FX Strength Desk", layout="centered")

# CSS بۆ دیزاینی تاریک (Dark Theme) ڕێک وەک وێنەکە
st.markdown("""
    <style>
    .stApp { background-color: #0b0f19; color: #f3f4f6; }
    .stTextInput input { background-color: #151c2c; color: #fff; border-radius: 8px; border: 1px solid #2a3447; }
    div[data-baseweb="select"] > div { background-color: #151c2c; color: #fff; }
    .currency-card {
        background-color: #151c2c;
        border: 1px solid #2a3447;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        margin-bottom: 20px;
    }
    .score-title { color: #9ca3af; font-size: 14px; }
    .score-number { font-size: 42px; font-weight: bold; margin: 10px 0; }
    .status-badge { font-weight: bold; padding: 4px 12px; border-radius: 6px; font-size: 14px; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<h5 style='text-align: center; color: #e2b714; letter-spacing: 2px;'>FX STRENGTH DESK</h5>", unsafe_allow_html=True)
st.markdown("<h2 style='text-align: center; margin-top: 0;'>سیستەمی هێزی دراو</h2>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #9ca3af; font-size: 13px;'>داتای مانگانەی ئیندیپێندنت تۆمار بکە، سیستەمەکە ئۆتۆماتیکی ئاراستە (Trend) و Z-Score حیساب دەکات.</p>", unsafe_allow_html=True)

# وەرگرتنی API Key
api_key = st.text_input("FRED API Key بنووسە بۆ بارکردنی ئۆتۆماتیکی:", type="password")

# هەڵبژاردنی دراوەکان (شێوازی دوگمەی تابەکان)
selected_currency = st.radio("دراوەکە هەڵبژێرە:", ["USD دۆلار", "GBP پاوەند", "CAD کەنەدی", "JPY یەن"], horizontal=True)

# فەنکشنی وەرگرتنی داتا لە FRED
def fetch_fred_series(series_id, key):
    if not key:
        return None
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json"
    try:
        res = requests.get(url)
        if res.status_code == 200:
            obs = res.json()['observations']
            df = pd.DataFrame(obs)
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            return df.dropna()['value'].tail(12).tolist()
    except:
        return None
    return None

def calc_z_score(vals):
    if not vals or len(vals) < 2:
        return 0.0
    mean = np.mean(vals)
    std = np.std(vals)
    return (vals[-1] - mean) / std if std != 0 else 0.0

# شاخصەکانی USD
indicators = {
    "CPI (هەڵکشانی نرخ)": "CPIAUCSL",
    "NFP (هەلی کار)": "PAYEMS",
    "PPI (نرخی بەرهەمهێنەر)": "PPIACO",
    "Unemployment Rate (بێکاری)": "UNRATE"
}

if api_key:
    scores = {}
    st.markdown("<br>", unsafe_allow_html=True)
    
    with st.spinner("داتاکان لە FRED ڕادەکێشرێن..."):
        for name, series_id in indicators.items():
            vals = fetch_fred_series(series_id, api_key)
            if vals:
                z = calc_z_score(vals)
                # لە بێکاریدا نمرەکە پێچەوانە دەکرێتەوە
                scores[name] = -z if "Unemployment" in name else z

    if scores:
        # حیسابکردنی نمرەی گشتی
        total_score = sum(scores.values()) / len(scores)
        
        # ڕەنگی کارتەکە
        if total_score > 0.3:
            color = "#10b981"
            status = "ئاراستەی بەهێز (Bullish)"
        elif total_score < -0.3:
            color = "#ef4444"
            status = "ئاراستەی لاواز (Bearish)"
        else:
            color = "#9ca3af"
            status = "بێلایەن / Neutral"

        # نیشاندانی خاڵی هێزەکە
        st.markdown(f"""
            <div class="currency-card">
                <div class="score-title">خاڵی هێزی گشتی {selected_currency[:3]}</div>
                <div class="score-number" style="color: {color};">{total_score:.2f}</div>
                <span class="status-badge" style="color: {color};">{status}</span>
            </div>
        """, unsafe_allow_html=True)

        # خشتەی وردەکاریی نیشاندەرەکان
        st.subheader("📋 وردەکاری Z-Scoreی نیشاندەرەکان")
        df_display = pd.DataFrame([
            {"نیشاندەر": k, "Z-Score": f"{v:.2f}", "ئاراستە": "📈 بەرزبوونەوە" if v > 0 else "📉 دابەزین"} 
            for k, v in scores.items()
        ])
        st.table(df_display)
    else:
        st.error("کێشەیەک لە ڕاکێشانی داتا هەبوو. دڵنیابەوە لە دروستی API Keyیەکەت.")
else:
    st.info("تکایە API Keyی FRED لە سندوقی سەرەوە بنووسە بۆ نمایشکردنی سیستەمەکە.")
