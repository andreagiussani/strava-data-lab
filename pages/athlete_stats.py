import truststore
truststore.inject_into_ssl()

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from strava_client import (
    start_of_week,
    start_of_month,
    start_of_year,
    to_utc,
    load_activities,
    summarize,
    daily_distance,
    format_pace,
)

load_dotenv()

st.set_page_config(page_title="Athlete Stats", layout="wide")
st.title("Strava Athlete Stats (simple)")

missing = [k for k in ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"] if k not in os.environ]
if missing:
    st.error(f"Missing env vars: {', '.join(missing)}")
    st.stop()

now = datetime.now(timezone.utc)

w_start = to_utc(start_of_week(now))
m_start = to_utc(start_of_month(now))
y_start = to_utc(start_of_year(now))
d30_start = to_utc((now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0))

# Sidebar options
st.sidebar.header("Options")
include_desc = st.sidebar.checkbox("Include description (slower)", value=False)
desc_limit = st.sidebar.slider("Descriptions for latest N activities", 10, 200, 50, step=10, disabled=not include_desc)

with st.spinner("Loading activities from Strava..."):
    df_all = load_activities(
        after_utc=y_start,
        before_utc=now,
        include_description=include_desc,
        description_limit=desc_limit,
    )

if df_all.empty:
    st.info("No activities found in the selected period.")
    st.stop()

# Dynamic sport filter
all_types = sorted(df_all["type"].astype(str).unique().tolist())
sport = st.sidebar.multiselect("Sport", all_types, default=all_types)
if not sport:
    st.warning("Select at least one sport.")
    st.stop()

df_all = df_all[df_all["type"].astype(str).isin(sport)]
if df_all.empty:
    st.info("No activities match the selected sport filter.")
    st.stop()

df_week = df_all[df_all["start_date"] >= w_start]
df_30d = df_all[df_all["start_date"] >= d30_start]
df_month = df_all[df_all["start_date"] >= m_start]
df_year = df_all

tabs = st.tabs(["Current week", "Last 30 days", "Month", "Current year"])


def render_tab(df: pd.DataFrame):
    s = summarize(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Activities", s["Activities"])
    with c2:
        st.metric("Distance (km)", f"{s['Distance (km)']:.2f}")
    with c3:
        st.metric("Moving time (min)", str(s["Moving time (min)"]))
    with c4:
        st.metric("Elevation (m)", f"{s['Elevation (m)']:.0f}")
    with c5:
        st.metric("Avg pace", f"{format_pace(s['Avg pace (min/km)'])} /km" if s["Avg pace (min/km)"] else "—")

    st.divider()

    st.subheader("Daily distance")
    st.line_chart(daily_distance(df))

    st.subheader("By activity type")
    by_type = (
        df.groupby("type")
        .agg(
            activities=("id", "count"),
            distance_km=("distance_km", lambda x: round(float(x.sum()), 2)),
            moving_time_min=("moving_time_min", lambda x: int(round(float(x.sum())))),
            elev_gain_m=("elev_gain_m", lambda x: int(round(float(x.sum())))),
        )
        .sort_values("distance_km", ascending=False)
    )
    st.dataframe(by_type, use_container_width=True)

    st.subheader("Latest activities")

    cols = ["start_date", "type", "name", "distance_km", "moving_time_min", "elev_gain_m", "pace_fmt"]
    if include_desc and "description" in df.columns:
        cols.append("description")

    latest = df[cols].head(30).copy()

    # display formatting
    latest["distance_km"] = latest["distance_km"].round(2)
    latest["moving_time_min"] = latest["moving_time_min"].round().astype("int64")
    latest["elev_gain_m"] = latest["elev_gain_m"].round().astype("int64")

    st.dataframe(latest, use_container_width=True)


with tabs[0]:
    render_tab(df_week)
with tabs[1]:
    render_tab(df_30d)
with tabs[2]:
    render_tab(df_month)
with tabs[3]:
    render_tab(df_year)
