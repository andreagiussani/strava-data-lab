import truststore
truststore.inject_into_ssl()

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Strava Data Lab", layout="wide")

st.title("Strava Data Lab")
st.write("Choose what you want to view:")

c1, c2 = st.columns(2)

with c1:
    if st.button("🏃‍♂️ Athlete Stats (week/30d/month/YTD)", use_container_width=True):
        st.switch_page("pages/athlete_stats.py")

with c2:
    if st.button("📈 Trends (weekly + monthly, 12–24 months)", use_container_width=True):
        st.switch_page("pages/trends.py")