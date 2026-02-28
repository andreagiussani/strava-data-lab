# pages/trends.py
import truststore
truststore.inject_into_ssl()

import os
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from strava_client import load_activities

load_dotenv()

TZ = os.getenv("STRAVA_TZ", "Europe/Zurich")


def normalize_type(t: str) -> str:
    """Stravalib sometimes returns strings like root='Run'. Normalize -> Run."""
    t = str(t)
    if "root=" in t and "'" in t:
        try:
            return t.split("'")[1]
        except Exception:
            return t
    return t


def _get_start_local(df: pd.DataFrame, tz: str) -> pd.Series:
    """
    Prefer Strava local timestamp if available (matches Strava UI bucketing).
    Fallback to converting UTC start_date to tz.
    """
    if "start_date_local" in df.columns:
        s = pd.to_datetime(df["start_date_local"])
        # ensure tz-aware
        if getattr(s.dt, "tz", None) is None:
            s = s.dt.tz_localize(tz)
        else:
            s = s.dt.tz_convert(tz)
        return s

    # fallback
    st.warning(
        "⚠️ `start_date_local` not found in the dataframe. "
        "Monthly/weekly bucketing will use `start_date` converted to local TZ. "
        "If you travel across timezones, some activities can be bucketed into the wrong month/week. "
        "Fix: update `strava_client.load_activities` to always return `start_date_local`."
    )
    return pd.to_datetime(df["start_date"], utc=True).dt.tz_convert(tz)


def weekly_monthly_km_by_sport(
    df: pd.DataFrame,
    sports: list[str],
    tz: str = TZ,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    # only activities with distance > 0 (exclude WeightTraining etc.)
    d = df[df["distance_km"] > 0].copy()
    if d.empty:
        return pd.DataFrame(), pd.DataFrame()

    d["sport"] = d["type"].astype(str).map(normalize_type)

    if sports:
        d = d[d["sport"].isin(sports)].copy()

    if d.empty:
        return pd.DataFrame(), pd.DataFrame()

    d["start_local"] = _get_start_local(d, tz)
    d = d.set_index("start_local")

    # ---- WEEKLY: Mon->Sun, bucket [Mon, next Mon), label on Monday ----
    wk = (
        d.groupby(["sport", pd.Grouper(freq="W-MON", label="left", closed="left")])["distance_km"]
        .sum()
        .unstack("sport")
        .sort_index()
    )
    wk.index.name = "week_start"

    if len(wk.index) > 0:
        wk_full = pd.date_range(wk.index.min(), wk.index.max(), freq="W-MON", tz=wk.index.tz)
        wk = wk.reindex(wk_full)

    # ---- MONTHLY: month start ----
    mo = (
        d.groupby(["sport", pd.Grouper(freq="MS")])["distance_km"]
        .sum()
        .unstack("sport")
        .sort_index()
    )
    mo.index.name = "month_start"

    if len(mo.index) > 0:
        mo_full = pd.date_range(mo.index.min(), mo.index.max(), freq="MS", tz=mo.index.tz)
        mo = mo.reindex(mo_full)

    # continuous lines/bars: missing periods -> 0
    wk = wk.fillna(0).round(2)
    mo = mo.fillna(0).round(2)

    return wk, mo


def monthly_bar_with_line(mo_df: pd.DataFrame) -> alt.Chart:
    """
    Bar chart for monthly km.
    - If 1 sport: bars + line overlay (same values; NOT rolling)
    - If multiple sports: stacked bars
    """
    if mo_df is None or mo_df.empty:
        return alt.Chart(pd.DataFrame({"month_start": [], "sport": [], "km_month": []}))

    base = mo_df.copy().reset_index()

    # the first column after reset_index is the old index (month start)
    idx_col = base.columns[0]
    base = base.rename(columns={idx_col: "month_start"})

    long = base.melt(id_vars=["month_start"], var_name="sport", value_name="km_month")
    long["month_label"] = pd.to_datetime(long["month_start"]).dt.strftime("%b %Y")

    sports = [c for c in mo_df.columns.tolist() if c is not None]

    if len(sports) <= 1:
        bar = (
            alt.Chart(long)
            .mark_bar()
            .encode(
                x=alt.X("month_start:T", title="", axis=alt.Axis(format="%b %Y", labelAngle=0)),
                y=alt.Y("km_month:Q", title="km"),
                tooltip=[
                    alt.Tooltip("month_label:N", title="Month"),
                    alt.Tooltip("km_month:Q", title="km", format=".2f"),
                ],
            )
        )
        # line = (
        #     alt.Chart(long)
        #     .mark_line(point=True)
        #     .encode(
        #         x=alt.X("month_start:T"),
        #         y=alt.Y("km_month:Q"),
        #         tooltip=[
        #             alt.Tooltip("month_label:N", title="Month"),
        #             alt.Tooltip("km_month:Q", title="km", format=".2f"),
        #         ],
        #     )
        # )
        return (bar).properties(height=260)

    # multiple sports -> stacked bars
    return (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("month_start:T", title="", axis=alt.Axis(format="%b %Y", labelAngle=0)),
            y=alt.Y("sum(km_month):Q", title="km"),
            color=alt.Color("sport:N", title="Sport"),
            tooltip=[
                alt.Tooltip("month_label:N", title="Month"),
                alt.Tooltip("sport:N", title="Sport"),
                alt.Tooltip("km_month:Q", title="km", format=".2f"),
            ],
        )
        .properties(height=260)
    )


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

df["sport"] = df["type"].astype(str).map(normalize_type)
available_sports = sorted(df["sport"].dropna().unique().tolist())

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

st.subheader("Monthly distance (km) — bars + line")
st.altair_chart(monthly_bar_with_line(mo_df), use_container_width=True)

st.subheader("Tables")
c1, c2 = st.columns(2)
with c1:
    st.write("Weekly (latest 52) — newest first")
    st.dataframe(wk_df.sort_index(ascending=False).head(52), use_container_width=True)
with c2:
    st.write("Monthly (latest 24) — newest first")
    st.dataframe(mo_df.sort_index(ascending=False).head(24), use_container_width=True)
