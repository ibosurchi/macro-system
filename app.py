import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime

# ڕێکخستنی لاپەڕە
st.set_page_config(page_title="FX Macro & News Intelligence Desk", layout="wide")

# CSS بۆ ڕێکخستنی دیزاینی تاریک و ڕوونکردنەوەی تێکست و خشتەکان
st.markdown("""
    <style>
    .stApp { background-color: #0b0f19; color: #ffffff; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        background-color: #151c2c;
        border-radius: 6px;
        color: #9ca3af;
        padding: 10px 20px;
        font-weight: bold;
    }
    .stTabs [aria-selected="true"] {
        background-color: #e2b714 !important;
        color: #000000 !important;
    }
    .metric-card {
        background-color: #151c2c;
        border: 1px solid #2a3447;
        border-radius: 10px;
        padding: 15px;
        margin-bottom: 15px;
    }
    .reasoning-box {
        background-color: #0f1623;
        border-right: 4px solid #e2b714;
        padding: 12px;
        margin-top: 10px;
        border-radius: 4px;
        font-size: 14px;
    }
    th { color: #e2b714 !important; background-color: #1f293d !important; }
    td { color: #f3f4f6 !important; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<h4 style='text-align: center; color: #e2b714; margin-bottom: 0;'>FX MACRO & GEOPOLITICAL DESK</h4>", unsafe_allow_html=True)
st.markdown("<h2 style='text-align: center; margin-top: 5px; color: #ffffff;'>سیستەمی پێشبینیکردن و شیکاری هەواڵەکان</h2>", unsafe_allow_html=True)

# وەرگرتنی API Key
api_key = st.sidebar.text_input("FRED API Key (ئۆتۆماتیکی):", type="password")
news_api_key = st.sidebar.text_input("NewsAPI Key (بۆ هەواڵەکان):", type="password")

# فەنکشنی وەرگرتنی داتای FRED بە بەروارەوە
def fetch_fred_data_with_dates(series_id, key):
    if not key:
        return None, None
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json"
    try:
        res = requests.get(url)
        if res.status_code == 200:
            obs = res.json()['observations']
            df = pd.DataFrame(obs)
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            df = df.dropna(subset=['value'])
            
            latest_date = df['date'].iloc[-1]
            latest_values = df['value'].tail(12).tolist()
            return latest_values, latest_date
    except:
        return None, None
    return None, None

def calc_z_score(vals):
    if not vals or len(vals) < 2:
        return 0.0
    mean = np.mean(vals)
    std = np.std(vals)
    return (vals[-1] - mean) / std if std != 0 else 0.0

# دروستکردنی TABS لە سەرەوەی ئەپەکە
tab1, tab2, tab3 = st.tabs([
    "📊 Macro Strength & Predictive Engine", 
    "📰 Live World News & Categories", 
    "💡 Impact Analysis on Currencies"
])

# ==========================================
# TAB 1: پێشبینی ئابووری و داتاکان
# ==========================================
with tab1:
    selected_currency = st.radio("دراوەکە هەڵبژێرە:", ["USD دۆلار", "GBP پاوەند", "CAD کەنەدی", "JPY یەن"], horizontal=True)
    
    currency_series = {
        "USD دۆلار": {"CPI": "CPIAUCSL", "NFP": "PAYEMS", "PPI": "PPIACO", "Unemployment": "UNRATE"},
        "GBP پاوەند": {"CPI": "GBRCPIALLMINMEI", "Production": "GBRPROINDMISMEI", "Unemployment": "LRUN64TTGBM156S"},
        "CAD کەنەدی": {"CPI": "CANCPIALLMINMEI", "Employment": "LFEMTTTTCAM647S", "Unemployment": "LRUN64TTCAM156S"},
        "JPY یەن": {"CPI": "JPNCPIALLMINMEI", "Production": "JPNPROINDMISMEI", "Unemployment": "LRUN64TTJPM156S"}
    }

    if api_key:
        indicators = currency_series[selected_currency]
        results = []
        raw_values = {}
        
        with st.spinner("کۆتا داتاکان و بەروارەکانیان ڕادەکێشرێن..."):
            for name, series_id in indicators.items():
                vals, date = fetch_fred_data_with_dates(series_id, api_key)
                if vals:
                    z = calc_z_score(vals)
                    score = -z if "Unemployment" in name else z
                    raw_values[name] = vals
                    results.append({"نیشاندەر": name, "کۆتا داتا": vals[-1], "Z-Score": round(score, 2), "کۆتا بەرواری دەرچوون": date})

        if results:
            df_res = pd.DataFrame(results)
            
            # نیشاندانی خشتەی داتاکان بە بەروارەوە
            st.subheader("📋 کۆتا داتا ڕاستەقینەکان بە بەروارەوە")
            st.table(df_res)
            
            # ----------------------------------------------------
            # مۆدێلی پێشبینیکردنی زنجیرەیی (Leading Indicators Logic)
            # ----------------------------------------------------
            st.markdown("---")
            st.subheader("🔮 پێشبینیکردنی هەواڵی داهاتوو (پشتنەبەستن بە Forecast)")
            
            if selected_currency == "USD دۆلار" and "CPI" in raw_values and "PPI" in raw_values:
                ppi_trend = raw_values["PPI"][-1] - raw_values["PPI"][-2]
                unemp_trend = raw_values["Unemployment"][-1] - raw_values["Unemployment"][-2] if "Unemployment" in raw_values else 0
                
                # پێشبینی CPI لە ڕێگەی PPI و بێکارییەوە
                if ppi_trend > 0 and unemp_trend <= 0:
                    cpi_pred = "📈 بەرزبوونەوەی CPI (هەڵکشان)"
                    reason = "چونکە شاخصی PPI (تێچووی بەرهەمهێنان) بەرزبووەتەوە و بێکاری کەمیکردووە. تێچووی بەرزی دروستکردنی کەلوپەل بە شێوەیەکی ڕاستەوخۆ دەگوازرێتەوە بۆ بەکارهێنەر، کە دەبێتە هۆی بەرزبوونەوەی CPIی مانگی داهاتوو."
                elif ppi_trend < 0 and unemp_trend > 0:
                    cpi_pred = "📉 دابەزینی CPI (سستبوون)"
                    reason = "چونکە PPI دابەزیوە و ڕێژەی بێکاری زیادی کردووە. ئەمەش داواکاریی بازار کەم دەکاتەوە و فشار لەسەر بەرزبوونەوەی نرخەکان کەم دەکاتەوە."
                else:
                    cpi_pred = "⚖️ سەقامگیر / Neutral"
                    reason = "داتاکانی PPI و بێکاری ئاراستەیەکی دژبەیەکیان نیشانداوە، بۆیە CPI بە جێگیری دەمێنێتەوە."

                col1, col2 = st.columns([1, 2])
                with col1:
                    st.markdown(f"**پێشبینی بۆ CPIی داهاتوو:**")
                    st.info(cpi_pred)
                with col2:
                    st.markdown("**هۆکاری پێشبینییەکە (Logical Reasoning):**")
                    st.markdown(f"<div class='reasoning-box'>{reason}</div>", unsafe_allow_html=True)
            else:
                st.write("پێشبینی تەنها کاتێک چالاک دەبێت کە هەردوو داتای CPI و PPI بارکرابن.")
    else:
        st.info("تکایە FRED API Key لە لای ڕاست بنووسە بۆ بارکردنی داتاکان.")

# ==========================================
# TAB 2: هەواڵە جیهانییەکان بەپێی بەشەکان
# ==========================================
with tab2:
    st.subheader("📰 هەواڵە جیهانییە خێراکان بەپێی بەشەکان")
    
    category = st.radio("بەشی هەواڵەکە هەڵبژێرە:", [
        "💣 Geopolitics & War (جەنگ)", 
        "🛢️ Energy & Oil (نەوت)", 
        "🏛️ Central Banks (بانکی ناوەندی)", 
        "🤝 Trade Wars & Tariffs (جەنگی بازرگانی)"
    ], horizontal=True)

    keywords = {
        "💣 Geopolitics & War (جەنگ)": "war OR military OR conflict OR sanctions",
        "🛢️ Energy & Oil (نەوت)": "oil OR opec OR crude OR energy crisis",
        "🏛️ Central Banks (بانکی ناوەندی)": "fed OR central bank OR interest rates OR inflation",
        "🤝 Trade Wars & Tariffs (جەنگی بازرگانی)": "tariffs OR trade war OR import tax"
    }

    if news_api_key:
        query = keywords[category]
        url = f"https://newsapi.org/v2/everything?q={query}&sortBy=publishedAt&apiKey={news_api_key}"
        try:
            res = requests.get(url)
            if res.status_code == 200:
                articles = res.json().get('articles', [])[:5]
                for art in articles:
                    st.markdown(f"""
                    <div class="metric-card">
                        <h4 style="color: #e2b714; margin:0;">{art['title']}</h4>
                        <p style="font-size: 12px; color: #9ca3af;">سەرچاوە: {art['source']['name']} | بەروار: {art['publishedAt'][:10]}</p>
                        <p style="font-size: 14px;">{art['description']}</p>
                        <a href="{art['url']}" target="_blank" style="color: #10b981;">خوێندنەوەی زیاتر...</a>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.error("کێشەیەک لە بارکردنی هەواڵەکان هەیە.")
        except:
            st.error("پەیوەندی لەگەڵ NewsAPI ڕوویدا.")
    else:
        st.warning("تکایە NewsAPI Keyی خۆڕایی لە لای ڕاست بنووسە بۆ بارکردنی هەواڵە ڕاستەوخۆکان (دەتوانیت لە newsapi.org وەریبگریت).")

# ==========================================
# TAB 3: کاریگەری هەواڵ لەسەر دراوەکان
# ==========================================
with tab3:
    st.subheader("💡 شیکاری کاریگەری رووداوە جیهانییەکان لەسەر دراوەکان")
    
    st.markdown("""
    | ڕووداوی جیهانی (Event) | دراوە بەهێزەکان (Bullish) | دراوە لاوازەکان (Bearish) | هۆکارەکە |
    | :--- | :--- | :--- | :--- |
    | **هەڵگیرسانی جەنگ یان ئاڵۆزی سەربازی** | **USD, CHF, Gold** | **EUR, AUD** | ڕاکردنی سەرمایە بۆ ناو دراوە ئەمنەکان (Safe-havens). |
    | **بەرزبوونەوەی بەرچاوی نرخی نەوت** | **CAD, NOK** | **JPY, EUR** | کەنەدا و نەرویج نەوت دەنێرنە دەرەوە؛ ژاپۆن و ئەوروپا هاوردەی دەکەن. |
    | **بەرزکردنەوەی ڕێژەی سوود (Rate Hikes)** | **دراوەکەی خۆی (واتە USD/GBP)** | **زێڕ (Gold)** | ڕاکێشانی وەبەرهێنەران بۆ بەدەستهێنانی سوودی بەرزتر. |
    | **جەنگی بازرگانی و باجی گومرگی** | **USD** | **AUD, NZD, CNH** | لاوازبوونی بازرگانی چین بە شێوەیەکی ڕاستەوخۆ دۆلاری ئوسترالی دادەبەزێنێت. |
    """)
