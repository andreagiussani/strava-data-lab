# pages/trends.py
import truststore
truststore.inject_into_ssl()

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from strava_client import load_activities

load_dotenv()

TZ = "Europe/Zurich"


def normalize_type(t: str) -> str:
    """
    Stravalib sometimes returns strings like "root='Run'".
    Normalize to a clean label like "Run".
    """
    t = str(t)
    if "root=" in t and "'" in t:
        # root='Run' -> Run
        try:
            return t.split("'")[1]
        except Exception:
            return t
    return t


def weekly_monthly_km_by_sport(
    df: pd.DataFrame,
    sports: list[str],
    tz: str = TZ,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    # only activities with distance > 0 (exclude WeightTraining etc.)
    d = df[df["distance_km"] > 0].copy()
    d["sport"] = d["type"].astype(str).map(normalize_type)

    if sports:
        d = d[d["sport"].isin(sports)]

    # local timezone to avoid midnight edge cases
    d["start_local"] = d["start_date"].dt.tz_convert(tz)
    d = d.set_index("start_local")

    # ---- WEEKLY: Mon->Sun, label on Monday, bucket [Mon, next Mon) ----
    wk = (
        d.groupby(["sport", pd.Grouper(freq="W-MON", label="left", closed="left")])["distance_km"]
        .sum()
        .unstack("sport")
        .sort_index()
    )
    wk.index.name = "week_start"

    # complete weekly index => continuous lines
    if len(wk.index) > 0:
        wk_full_idx = pd.date_range(wk.index.min(), wk.index.max(), freq="W-MON", tz=wk.index.tz)
        wk = wk.reindex(wk_full_idx)

    # ---- MONTHLY: month start ----
    mo = (
        d.groupby(["sport", pd.Grouper(freq="MS")])["distance_km"]
        .sum()
        .unstack("sport")
        .sort_index()
    )
    mo.index.name = "month_start"

    # complete monthly index => continuous lines
    if len(mo.index) > 0:
        mo_full_idx = pd.date_range(mo.index.min(), mo.index.max(), freq="MS", tz=mo.index.tz)
        mo = mo.reindex(mo_full_idx)

    # continuous lines: missing periods -> 0
    wk = wk.fillna(0).round(2)
    mo = mo.fillna(0).round(2)

    return wk, mo


# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Trends", layout="wide")
st.title("Strava Trends (weekly + monthly)")

missing = [k for k in ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"] if k not in os.environ]
if missing:
    st.error(f"Missing env vars: {', '.join(missing)}")
    st.stop()

months = st.sidebar.radio("Window", [12, 24], index=0, format_func=lambda x: f"Last {x} months")

now = datetime.now(timezone.utc)
start = (now - relativedelta(months=months)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

with st.spinner("Loading activities from Strava..."):
    df = load_activities(after_utc=start, before_utc=now)

if df.empty:
    st.info("No activities found in this period.")
    st.stop()

# Available sports (normalized)
df["sport"] = df["type"].astype(str).map(normalize_type)
available_sports = sorted(df["sport"].dropna().unique().tolist())

# Default: Run only if present, else first available
default_sports = ["Run"] if "Run" in available_sports else (available_sports[:1] if available_sports else [])

sports = st.sidebar.multiselect("Sport", available_sports, default=default_sports)
if not sports:
    st.warning("Select at least one sport.")
    st.stop()

wk_df, mo_df = weekly_monthly_km_by_sport(df, sports=sports, tz=TZ)

if wk_df.empty and mo_df.empty:
    st.info("No distance activities in this period for the selected sport(s).")
    st.stop()

st.subheader("Weekly distance (km)")
st.line_chart(wk_df)

st.subheader("Monthly distance (km)")
st.line_chart(mo_df)

st.subheader("Tables")
c1, c2 = st.columns(2)
with c1:
    st.write("Weekly (latest 52) — newest first")
    st.dataframe(wk_df.sort_index(ascending=False).head(52), use_container_width=True)
with c2:
    st.write("Monthly (latest 24) — newest first")
    st.dataframe(mo_df.sort_index(ascending=False).head(24), use_container_width=True)
