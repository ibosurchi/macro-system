import streamlit as st
import pandas as pd
import numpy as np
import requests

st.set_page_config(page_title="FX Macro Strength Desk", layout="centered")

st.title("📊 FX Macro Strength Desk")
st.caption("سیستەمی ئۆتۆماتیکی دیاری کردنی هێزی دراو بەپێی Z-Score و Trend")

# وەرگرتنی API Key لە بەکارهێنەر یان بەکارهێنانی Keyی گشتی
api_key = st.text_input("Enter FRED API Key (ئەگەر هەتە):", type="password")

def get_fred_data(series_id, key):
    if not key:
        return None
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json"
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()['observations']
        df = pd.DataFrame(data)
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df.dropna()['value'].tail(12).tolist()
    return None

def calc_z(vals):
    if not vals or len(vals) < 2:
        return 0
    mean = np.mean(vals)
    std = np.std(vals)
    if std == 0:
        return 0
    return (vals[-1] - mean) / std

st.subheader("🇺🇸 USD Macro Indicators")

if api_key:
    cpi_data = get_fred_data("CPIAUCSL", api_key)
    nfp_data = get_fred_data("PAYEMS", api_key)
    
    if cpi_data and nfp_data:
        cpi_z = calc_z(cpi_data)
        nfp_z = calc_z(nfp_data)
        
        total_score = (cpi_z * 0.6) + (nfp_z * 0.4)
        
        st.metric(label="USD Strength Score", value=f"{total_score:.2f}")
        if total_score > 0.5:
            st.success("Bullish USD (ئاراستەی بەهێز)")
        elif total_score < -0.5:
            st.error("Bearish USD (ئاراستەی لاواز)")
        else:
            st.info("Neutral (بێلایەن)")
    else:
        st.warning("کێشەیەک لە ڕاکێشانی داتا هەیە، تکایە لە دروستی API Key دڵنیا ببەوە.")
else:
    st.info("تکایە API Keyی خۆڕایی FRED بنووسە تا داتاکان بە ئۆتۆماتیکی باربکرێن.")
