import streamlit as st
import pandas as pd
import numpy as np
import requests

# ڕێکخستنی لاپەڕە
st.set_page_config(page_title="FX Strength Desk", layout="centered")

# CSSی نوێکراوە بۆ تەواو ڕوونبوونی نووسینەکان و خشتەکان
st.markdown("""
    <style>
    .stApp { background-color: #0b0f19; color: #ffffff; }
    
    /* ڕوون کردنی نووسینی ڕادیۆ دوگمەکان */
    div[data-aria-selected="true"] { color: #e2b714 !important; }
    label p { color: #f3f4f6 !important; font-weight: bold !important; font-size: 15px !important; }
    
    /* کارتەکە */
    .currency-card {
        background-color: #151c2c;
        border: 2px solid #2a3447;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        margin-bottom: 25px;
    }
    .score-title { color: #d1d5db; font-size: 16px; font-weight: 500; }
    .score-number { font-size: 48px; font-weight: bold; margin: 10px 0; }
    
    /* ڕوون کردنی خشتەکە */
    .stTable, div[data-testid="stTable"] {
        background-color: #151c2c !important;
        color: #ffffff !important;
        border-radius: 8px;
    }
    th { color: #e2b714 !important; font-size: 15px !important; background-color: #1f293d !important; }
    td { color: #f3f4f6 !important; font-size: 14px !important; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<h5 style='text-align: center; color: #e2b714; letter-spacing: 2px;'>FX STRENGTH DESK</h5>", unsafe_allow_html=True)
st.markdown("<h2 style='text-align: center; margin-top: 0; color: #ffffff;'>سیستەمی هێزی دراو</h2>", unsafe_allow_html=True)

# وەرگرتنی API Key
api_key = st.text_input("FRED API Key بنووسە بۆ بارکردنی ئۆتۆماتیکی:", type="password")

# هەڵبژاردنی دراوەکان
selected_currency = st.radio("دراوەکە هەڵبژێرە:", ["USD دۆلار", "GBP پاوەند", "CAD کەنەدی", "JPY یەن"], horizontal=True)

# بنکەی داتای جیاواز بۆ هر دراوێک لە FRED
currency_series = {
    "USD دۆلار": {
        "CPI (هەڵکشانی نرخ)": "CPIAUCSL",
        "NFP / Employment (داهات/کار)": "PAYEMS",
        "PPI (نرخی بەرهەمهێنەر)": "PPIACO",
        "Unemployment Rate (بێکاری)": "UNRATE"
    },
    "GBP پاوەند": {
        "CPI (هەڵکشان)": "GBRCPIALLMINMEI",
        "Industrial Production (بەرهەمهێنان)": "GBRPROINDMISMEI",
        "Unemployment Rate (بێکاری)": "LRUN64TTGBM156S"
    },
    "CAD کەنەدی": {
        "CPI (هەڵکشان)": "CANCPIALLMINMEI",
        "Employment (کار)": "LFEMTTTTCAM647S",
        "Unemployment Rate (بێکاری)": "LRUN64TTCAM156S"
    },
    "JPY یەن": {
        "CPI (هەڵکشان)": "JPNCPIALLMINMEI",
        "Industrial Production (بەرهەمهێنان)": "JPNPROINDMISMEI",
        "Unemployment Rate (بێکاری)": "LRUN64TTJPM156S"
    }
}

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

if api_key:
    scores = {}
    current_indicators = currency_series[selected_currency]
    
    with st.spinner(f"داتاکانی {selected_currency} ڕادەکێشرێن..."):
        for name, series_id in current_indicators.items():
            vals = fetch_fred_series(series_id, api_key)
            if vals:
                z = calc_z_score(vals)
                # پێچەوانەکردنەوەی نمرە بۆ بێکاری
                scores[name] = -z if "Unemployment" in name else z

    if scores:
        total_score = sum(scores.values()) / len(scores)
        
        if total_score > 0.3:
            color = "#10b981"
            status = "ئاراستەی بەهێز (Bullish)"
        elif total_score < -0.3:
            color = "#ef4444"
            status = "ئاراستەی لاواز (Bearish)"
        else:
            color = "#d1d5db"
            status = "بێلایەن / Neutral"

        st.markdown(f"""
            <div class="currency-card">
                <div class="score-title">خاڵی هێزی گشتی {selected_currency[:3]}</div>
                <div class="score-number" style="color: {color};">{total_score:.2f}</div>
                <span style="color: {color}; font-weight: bold; font-size: 16px;">{status}</span>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("<h3 style='color: #ffffff;'>📋 وردەکاری Z-Scoreی نیشاندەرەکان</h3>", unsafe_allow_html=True)
        
        df_display = pd.DataFrame([
            {"نیشاندەر": k, "Z-Score": f"{v:.2f}", "ئاراستە": "📈 بەرزبوونەوە" if v > 0 else "📉 دابەزین"} 
            for k, v in scores.items()
        ])
        st.table(df_display)
    else:
        st.error("داتاکان لە FRED بەردەست نەبوون بۆ ئەم دراوە، تکایە لە دروستی API Key دڵنیا ببەوە.")
else:
    st.info("تکایە API Keyی FRED لە سندوقی سەرەوە بنووسە.")
