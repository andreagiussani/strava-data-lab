import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from stravalib import Client

TZ_DEFAULT = os.getenv("STRAVA_TZ", "Europe/Zurich")


# ----------------------------
# Helpers: date ranges
# ----------------------------
def start_of_week(dt: datetime) -> datetime:
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
    if d is None:
        return 0
    if hasattr(d, "total_seconds"):
        return int(d.total_seconds())
    if hasattr(d, "seconds"):
        return int(d.seconds)
    if hasattr(d, "to"):
        try:
            return int(d.to("second").magnitude)
        except Exception:
            pass
    try:
        return int(d)
    except Exception:
        return 0


def format_pace(p: float | None) -> str:
    if p is None or pd.isna(p):
        return "—"
    total_seconds = int(round(p * 60))
    mm, ss = divmod(total_seconds, 60)
    return f"{mm}:{ss:02d}"


def _localize_start_date_local(a) -> pd.Timestamp:
    """
    Strava UI buckets by local start time. stravalib often returns naive start_date_local.
    """
    sdl = getattr(a, "start_date_local", None)
    if sdl is None:
        return pd.to_datetime(a.start_date, utc=True).tz_convert(TZ_DEFAULT)

    ts = pd.to_datetime(sdl)
    if ts.tzinfo is None:
        return ts.tz_localize(TZ_DEFAULT)
    return ts.tz_convert(TZ_DEFAULT)


# ----------------------------
# Auth / client
# ----------------------------
@st.cache_resource
def make_client() -> Client:
    missing = [k for k in ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"] if k not in os.environ]
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

    new_refresh = token.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        st.warning(
            "Strava rotated your refresh token. Update STRAVA_REFRESH_TOKEN in your .env "
            f"to: {new_refresh}"
        )

    return Client(access_token=token["access_token"])


# ----------------------------
# Gear lookup (cached)
# ----------------------------
@st.cache_data(ttl=24 * 3600)
def gear_name_from_id(gear_id: str) -> str:
    """Resolve Strava gear_id -> gear name (cached)."""
    if not gear_id:
        return ""

    client = make_client()
    try:
        g = client.get_gear(gear_id)
        return str(getattr(g, "name", "") or "")
    except Exception:
        return ""


# ----------------------------
# Fetch activities and build dataframe
# ----------------------------
@st.cache_data(ttl=15 * 60)
def load_activities(
    after_utc: datetime,
    before_utc: datetime,
    include_description: bool = False,
    description_limit: int = 50,
    include_biometrics: bool = False,
    biometrics_limit: int = 50,
    include_gear: bool = True,   # ✅ NEW
) -> pd.DataFrame:
    """
    Fetch activities between after_utc and before_utc.

    Notes:
    - Many fields (HR, watts, cadence, calories, suffer_score) are reliably available
      only from the detailed activity endpoint.
    - To avoid being super slow, we fetch details only for the latest N activities.
    - Gear name requires an extra call per distinct gear_id; we cache it for 24h.
    """
    client = make_client()
    acts = list(client.get_activities(after=after_utc, before=before_utc))

    rows = []
    for i, a in enumerate(acts):
        want_desc = include_description and (i < int(description_limit))
        want_bio = include_biometrics and (i < int(biometrics_limit))

        detailed = None
        if want_desc or want_bio:
            try:
                detailed = client.get_activity(a.id)
            except Exception:
                detailed = None

        src = detailed or a

        # description
        desc_val = ""
        if include_description:
            desc_val = str(getattr(src, "description", "") or "")

        # biometrics (may be missing/None)
        avg_hr = getattr(src, "average_heartrate", None)
        max_hr = getattr(src, "max_heartrate", None)
        avg_watts = getattr(src, "average_watts", None)
        weighted_watts = getattr(src, "weighted_average_watts", None)
        avg_cad = getattr(src, "average_cadence", None)
        calories = getattr(src, "calories", None)
        suffer = getattr(src, "suffer_score", None)

        # ✅ gear
        gear_id = ""
        gear_name = ""
        if include_gear:
            gear_id = str(getattr(src, "gear_id", "") or "")
            gear_name = gear_name_from_id(gear_id) if gear_id else ""

        rows.append(
            {
                "id": int(a.id),
                "name": str(a.name),
                "type": str(a.type),
                "start_date": pd.to_datetime(a.start_date, utc=True),
                "start_date_local": _localize_start_date_local(a),
                "distance_m": float(a.distance) if a.distance is not None else 0.0,
                "moving_time_s": duration_seconds(a.moving_time),
                "elapsed_time_s": duration_seconds(a.elapsed_time),
                "elev_gain_m": float(a.total_elevation_gain) if a.total_elevation_gain is not None else 0.0,
                # optional fields
                **({"description": desc_val} if include_description else {}),
                **(
                    {
                        "avg_hr": avg_hr,
                        "max_hr": max_hr,
                        "avg_watts": avg_watts,
                        "weighted_watts": weighted_watts,
                        "avg_cadence": avg_cad,
                        "calories": calories,
                        "suffer_score": suffer,
                    }
                    if include_biometrics
                    else {}
                ),
                **(
                    {
                        "gear_id": gear_id,
                        "gear_name": gear_name,
                    }
                    if include_gear
                    else {}
                ),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["distance_km"] = df["distance_m"] / 1000.0
    df["moving_time_min"] = df["moving_time_s"] / 60.0

    df["pace_min_km"] = df.apply(
        lambda r: (r["moving_time_s"] / 60.0) / r["distance_km"]
        if (r["distance_km"] and r["distance_km"] > 0)
        else None,
        axis=1,
    )
    df["pace_fmt"] = df["pace_min_km"].apply(format_pace)

    return df.sort_values("start_date", ascending=False)


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
