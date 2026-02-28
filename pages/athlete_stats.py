# pages/athlete_stats.py
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

TZ = os.getenv("STRAVA_TZ", "Europe/Zurich")

st.set_page_config(page_title="Athlete Stats", layout="wide")
st.title("Strava Athlete Stats")

# --------------------------------------------------
# ENV CHECK
# --------------------------------------------------
missing = [k for k in ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"] if k not in os.environ]
if missing:
    st.error(f"Missing env vars: {', '.join(missing)}")
    st.stop()

now = datetime.now(timezone.utc)

w_start = to_utc(start_of_week(now))
m_start = to_utc(start_of_month(now))
y_start = to_utc(start_of_year(now))
d30_start = to_utc((now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0))

# --------------------------------------------------
# SIDEBAR OPTIONS
# --------------------------------------------------
st.sidebar.header("Options")

include_desc = st.sidebar.checkbox("Include description (slower)", value=False)
desc_limit = st.sidebar.slider(
    "Descriptions for latest N activities",
    10, 200, 50, step=10,
    disabled=not include_desc,
)

include_bio = st.sidebar.checkbox("Include biometrics (HR/watts/cadence/calories) (slower)", value=False)
bio_limit = st.sidebar.slider(
    "Biometrics for latest N activities",
    10, 200, 50, step=10,
    disabled=not include_bio,
)

# --------------------------------------------------
# LOAD DATA
# --------------------------------------------------
with st.spinner("Loading activities from Strava..."):
    df_all = load_activities(
        after_utc=y_start,
        before_utc=now,
        include_description=include_desc,
        description_limit=desc_limit,
        include_biometrics=include_bio,
        biometrics_limit=bio_limit,
    )

if df_all.empty:
    st.info("No activities found in the selected period.")
    st.stop()

# --------------------------------------------------
# DEDUPE (same local start minute + sport + distance)
# --------------------------------------------------
df_all = df_all.copy()

# prefer start_date_local if present (Strava UI logic), else convert start_date
if "start_date_local" in df_all.columns:
    start_local = pd.to_datetime(df_all["start_date_local"])
else:
    start_local = pd.to_datetime(df_all["start_date"], utc=True).dt.tz_convert(TZ)

df_all["start_local"] = start_local
df_all["start_minute"] = df_all["start_local"].dt.floor("min")

# normalize sport key: prefer sport_type if you have it, else type
sport_col = "sport_type" if "sport_type" in df_all.columns else "type"
df_all["sport_norm"] = df_all[sport_col].astype(str)

# distance bucket to avoid dropping two different activities in same minute
df_all["distance_round"] = pd.to_numeric(df_all["distance_km"], errors="coerce").round(2)

# keep the "best" row per duplicate group: prefer description/biometrics, then higher id
sort_cols = ["start_local"]
asc = [False]

if "description" in df_all.columns:
    df_all["has_desc"] = df_all["description"].astype(str).str.len().fillna(0) > 0
    sort_cols.append("has_desc")
    asc.append(False)

bio_cols = [c for c in ["avg_hr", "avg_watts", "avg_cadence", "calories", "suffer_score"] if c in df_all.columns]
if bio_cols:
    df_all["bio_score"] = df_all[bio_cols].notna().sum(axis=1)
    sort_cols.append("bio_score")
    asc.append(False)

if "id" in df_all.columns:
    sort_cols.append("id")
    asc.append(False)

df_all = df_all.sort_values(sort_cols, ascending=asc)
df_all = df_all.drop_duplicates(subset=["start_minute", "sport_norm", "distance_round"], keep="first")

# cleanup helper cols
df_all = df_all.drop(columns=["start_local", "start_minute", "sport_norm", "distance_round", "has_desc", "bio_score"], errors="ignore")

# --------------------------------------------------
# SPORT FILTER
# --------------------------------------------------
all_types = sorted(df_all["type"].astype(str).unique().tolist())
default_sport = ["Run"] if "Run" in all_types else all_types
sport = st.sidebar.multiselect("Sport", all_types, default=default_sport)

if not sport:
    st.warning("Select at least one sport.")
    st.stop()

df_all = df_all[df_all["type"].astype(str).isin(sport)]
if df_all.empty:
    st.info("No activities match the selected sport filter.")
    st.stop()

# --------------------------------------------------
# TIME WINDOWS
# --------------------------------------------------
df_week = df_all[df_all["start_date"] >= w_start]
df_30d = df_all[df_all["start_date"] >= d30_start]
df_month = df_all[df_all["start_date"] >= m_start]
df_year = df_all

tabs = st.tabs(["Current week", "Last 30 days", "Month", "Current year"])

# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def safe_mean(df: pd.DataFrame, col: str) -> float | None:
    if col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return None
    return float(s.mean())


def safe_sum(df: pd.DataFrame, col: str) -> float | None:
    if col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return None
    return float(s.sum())


def has_any(df: pd.DataFrame, cols: list[str]) -> bool:
    return any(c in df.columns for c in cols)


# --------------------------------------------------
# RENDER TAB
# --------------------------------------------------
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

    # ----------------------------
    # BIOMETRICS SUMMARY (only if present)
    # ----------------------------
    bio_cols_present = has_any(
        df,
        ["avg_hr", "max_hr", "avg_watts", "weighted_watts", "avg_cadence", "calories", "suffer_score"],
    )

    if bio_cols_present:
        st.divider()
        b1, b2, b3, b4 = st.columns(4)

        with b1:
            v = safe_mean(df, "avg_hr")
            st.metric("Avg HR", f"{v:.0f} bpm" if v is not None else "—")

        with b2:
            v = safe_mean(df, "avg_watts")
            st.metric("Avg Watts", f"{v:.0f} W" if v is not None else "—")

        with b3:
            v = safe_mean(df, "avg_cadence")
            st.metric("Avg Cadence", f"{v:.0f} rpm" if v is not None else "—")

        with b4:
            v = safe_sum(df, "calories")
            st.metric("Calories", f"{v:.0f}" if v is not None else "—")

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

    # Gear always (if present)
    if "gear_name" in df.columns:
        cols.append("gear_name")
    elif "gear_id" in df.columns:
        cols.append("gear_id")

    # Optional description
    if include_desc and "description" in df.columns:
        cols.append("description")

    # Optional biometrics
    for extra in ["avg_hr", "max_hr", "avg_watts", "weighted_watts", "avg_cadence", "calories", "suffer_score"]:
        if extra in df.columns:
            cols.append(extra)

    latest = df[cols].head(30).copy()

    # formatting
    latest["distance_km"] = latest["distance_km"].round(2)
    latest["moving_time_min"] = latest["moving_time_min"].round().astype("int64")
    latest["elev_gain_m"] = latest["elev_gain_m"].round().astype("int64")

    # biometrics formatting
    for c in ["avg_hr", "max_hr", "avg_watts", "weighted_watts", "avg_cadence", "calories", "suffer_score"]:
        if c in latest.columns:
            latest[c] = pd.to_numeric(latest[c], errors="coerce").round().astype("Int64")

    st.dataframe(latest, use_container_width=True)


with tabs[0]:
    render_tab(df_week)
with tabs[1]:
    render_tab(df_30d)
with tabs[2]:
    render_tab(df_month)
with tabs[3]:
    render_tab(df_year)
