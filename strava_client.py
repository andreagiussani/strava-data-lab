import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from stravalib import Client

# Default timezone for grouping
TZ_DEFAULT = "Europe/Zurich"


# ----------------------------
# Helpers: date ranges
# ----------------------------
def start_of_week(dt: datetime) -> datetime:
    # Monday as start (ISO week)
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def start_of_month(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def start_of_year(dt: datetime) -> datetime:
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def duration_seconds(d) -> int:
    """Convert Stravalib Duration / timedelta / int to seconds."""
    if d is None:
        return 0

    if hasattr(d, "total_seconds"):  # timedelta-like
        return int(d.total_seconds())

    if hasattr(d, "seconds"):  # some Duration objects
        return int(d.seconds)

    if hasattr(d, "to"):  # pint Quantity
        try:
            return int(d.to("second").magnitude)
        except Exception:
            pass

    try:
        return int(d)
    except Exception:
        return 0


def format_pace(p: float | None) -> str:
    """Format min/km as mm:ss."""
    if p is None or pd.isna(p):
        return "—"
    total_seconds = int(round(p * 60))
    mm, ss = divmod(total_seconds, 60)
    return f"{mm}:{ss:02d}"


# ----------------------------
# Auth / client
# ----------------------------
@st.cache_resource
def make_client() -> Client:
    missing = [
        k
        for k in ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"]
        if k not in os.environ
    ]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    client_id = int(os.environ["STRAVA_CLIENT_ID"])
    client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    refresh_token = os.environ["STRAVA_REFRESH_TOKEN"]

    c = Client()
    token = c.refresh_access_token(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    # IMPORTANT: Strava may rotate refresh tokens
    new_refresh = token.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        st.warning(
            "Strava rotated your refresh token. Update STRAVA_REFRESH_TOKEN in your .env "
            f"to: {new_refresh}"
        )

    return Client(access_token=token["access_token"])


@st.cache_data(ttl=24 * 60 * 60)
def get_activity_description(activity_id: int) -> str:
    """
    Strava summary activities often omit `description`.
    Fetch full activity detail (cached) to obtain it.
    """
    client = make_client()
    try:
        a = client.get_activity(activity_id)
        desc = getattr(a, "description", None)
        return str(desc).strip() if desc else ""
    except Exception:
        return ""


# ----------------------------
# Fetch activities and build dataframe
# ----------------------------
@st.cache_data(ttl=15 * 60)
def load_activities(
    after_utc: datetime,
    before_utc: datetime,
    *,
    include_description: bool = False,
    description_limit: int = 50,
) -> pd.DataFrame:
    client = make_client()
    acts = client.get_activities(after=after_utc, before=before_utc)

    rows = []
    for a in acts:
        rows.append(
            {
                "id": int(a.id),
                "name": str(a.name),
                "type": str(a.type),
                "start_date": pd.to_datetime(a.start_date, utc=True),
                "distance_m": float(a.distance) if a.distance is not None else 0.0,
                "moving_time_s": duration_seconds(a.moving_time),
                "elapsed_time_s": duration_seconds(a.elapsed_time),
                "elev_gain_m": float(a.total_elevation_gain) if a.total_elevation_gain is not None else 0.0,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["distance_km"] = df["distance_m"] / 1000.0
    df["moving_time_min"] = df["moving_time_s"] / 60.0

    # keep apply (your preference), but safe
    df["pace_min_km"] = df.apply(
        lambda r: (r["moving_time_s"] / 60.0) / r["distance_km"]
        if (r["distance_km"] and r["distance_km"] > 0)
        else None,
        axis=1,
    )
    df["pace_fmt"] = df["pace_min_km"].apply(format_pace)

    df = df.sort_values("start_date", ascending=False)

    # Optional: fetch descriptions only for latest N activities
    if include_description:
        df["description"] = ""
        n = int(max(0, description_limit))
        if n > 0:
            top_ids = df.head(n)["id"].astype(int).tolist()
            desc_map = {aid: get_activity_description(aid) for aid in top_ids}
            df.loc[df["id"].isin(top_ids), "description"] = df["id"].map(desc_map).fillna("")

    return df


# ----------------------------
# Aggregations
# ----------------------------
def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "Activities": 0,
            "Distance (km)": 0.0,
            "Moving time (min)": 0,
            "Elevation (m)": 0.0,
            "Avg pace (min/km)": None,
        }

    # pace: only activities with distance
    df_pace = df[df["distance_km"] > 0].copy()

    total_dist = float(df["distance_km"].sum())
    total_time_min = float(df["moving_time_min"].sum())
    total_elev = float(df["elev_gain_m"].sum())

    avg_pace = (
        float(df_pace["moving_time_min"].sum() / df_pace["distance_km"].sum())
        if not df_pace.empty and float(df_pace["distance_km"].sum()) > 0
        else None
    )

    return {
        "Activities": int(len(df)),
        "Distance (km)": total_dist,
        "Moving time (min)": int(round(total_time_min)),
        "Elevation (m)": total_elev,
        "Avg pace (min/km)": avg_pace,
    }


def daily_distance(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.set_index("start_date")["distance_km"]
        .resample("D")
        .sum()
        .to_frame(name="distance_km")
        .sort_index()
    )


def _ensure_local_index(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Work on local timezone to avoid boundary issues around midnight."""
    df2 = df.copy()
    if "start_date" not in df2.columns:
        raise ValueError("DataFrame must contain 'start_date'")
    if not pd.api.types.is_datetime64tz_dtype(df2["start_date"]):
        df2["start_date"] = pd.to_datetime(df2["start_date"], utc=True)
    df2["start_local"] = df2["start_date"].dt.tz_convert(tz)
    return df2.set_index("start_local")


def weekly_km(
    df: pd.DataFrame,
    *,
    tz: str = TZ_DEFAULT,
    by_sport: bool = False,
) -> pd.DataFrame:
    """
    Weekly distance.
    - Week = Mon->Sun
    - Index is the Monday (week start)
    - Continuous (fills missing weeks with 0)
    """
    df2 = df[df["distance_km"] > 0].copy()
    if df2.empty:
        return pd.DataFrame()

    df2 = _ensure_local_index(df2, tz)

    if by_sport:
        wk = (
            df2.groupby("type")["distance_km"]
            .resample("W-MON", label="left", closed="left")
            .sum()
            .reset_index()
        )
        wk = wk.pivot(index="start_local", columns="type", values="distance_km").sort_index()
        wk.index.name = "week_start"
        wk = wk.round(2)
    else:
        wk = (
            df2["distance_km"]
            .resample("W-MON", label="left", closed="left")
            .sum()
            .to_frame("km_week")
            .sort_index()
        )
        wk.index.name = "week_start"
        wk["km_week"] = wk["km_week"].round(2)

    # Make it continuous
    full_idx = pd.date_range(start=wk.index.min(), end=wk.index.max(), freq="W-MON", tz=wk.index.tz)
    wk = wk.reindex(full_idx, fill_value=0)
    wk.index.name = "week_start"
    return wk


def monthly_km(
    df: pd.DataFrame,
    *,
    tz: str = TZ_DEFAULT,
    by_sport: bool = False,
) -> pd.DataFrame:
    """
    Monthly distance (month start).
    Continuous (fills missing months with 0).
    """
    df2 = df[df["distance_km"] > 0].copy()
    if df2.empty:
        return pd.DataFrame()

    df2 = _ensure_local_index(df2, tz)

    if by_sport:
        mo = (
            df2.groupby("type")["distance_km"]
            .resample("MS")
            .sum()
            .reset_index()
        )
        mo = mo.pivot(index="start_local", columns="type", values="distance_km").sort_index()
        mo.index.name = "month_start"
        mo = mo.round(2)
    else:
        mo = (
            df2["distance_km"]
            .resample("MS")
            .sum()
            .to_frame("km_month")
            .sort_index()
        )
        mo.index.name = "month_start"
        mo["km_month"] = mo["km_month"].round(2)

    # Make it continuous
    full_idx = pd.date_range(start=mo.index.min(), end=mo.index.max(), freq="MS", tz=mo.index.tz)
    mo = mo.reindex(full_idx, fill_value=0)
    mo.index.name = "month_start"
    return mo
