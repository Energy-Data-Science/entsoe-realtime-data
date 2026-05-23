from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from entsoe_realtime.config import VARIABLES, load_settings
from entsoe_realtime.jobs import run_refresh
from entsoe_realtime.storage import collect_file_summary, collect_snapshot_summary

DEFAULT_REMOTE_DATA_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "Energy-Data-Science/entsoe-realtime-data/data/"
)
DATA_SOURCE = os.getenv("ENTSOE_DASHBOARD_DATA_SOURCE", "remote").strip().lower()
REMOTE_DATA_BASE_URL = os.getenv(
    "ENTSOE_DASHBOARD_DATA_BASE_URL", DEFAULT_REMOTE_DATA_BASE_URL
).rstrip("/")


def resolve_data_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def remote_url(path_value: str) -> str:
    return f"{REMOTE_DATA_BASE_URL}/{path_value.lstrip('/')}"


@st.cache_data(ttl=60, show_spinner=False)
def read_remote_csv(path_value: str) -> pd.DataFrame:
    return pd.read_csv(remote_url(path_value))


@st.cache_data(ttl=60, show_spinner=False)
def read_remote_json(path_value: str) -> dict:
    with urlopen(remote_url(path_value), timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def load_snapshot_summary_frame(settings) -> pd.DataFrame:
    if DATA_SOURCE == "remote":
        try:
            return read_remote_csv("data/update_manifest.csv")
        except Exception as exc:
            st.warning(f"Could not read remote snapshot manifest: {exc}")
            return pd.DataFrame()
    return collect_snapshot_summary(settings.update_dir, settings.update_manifest)


def read_snapshot_csv(path_value: str) -> pd.DataFrame:
    if DATA_SOURCE == "remote":
        return read_remote_csv(path_value)
    return pd.read_csv(resolve_data_path(path_value))


st.set_page_config(page_title="ENTSO-E Fetch Monitor", layout="wide")
components.html(
    """
    <script>
      setTimeout(function () {
        window.parent.location.reload();
      }, 30000);
    </script>
    """,
    height=0,
)
st.title("ENTSO-E 15-Minute Collection Monitor")

settings = load_settings(require_api_key=False)
if DATA_SOURCE == "remote":
    st.caption(f"Update snapshots: {REMOTE_DATA_BASE_URL}")
else:
    st.caption(f"Update snapshots: {settings.update_dir}")

with st.sidebar:
    st.header("Controls")
    st.caption(f"Fetch: {settings.fetch_mode} | Storage: {settings.storage_mode}")
    st.caption(f"Dashboard data source: {DATA_SOURCE}")
    st.caption(f"Window: latest {settings.recent_days} days")
    selected_country = st.selectbox("Country", settings.countries)
    selected_variable = st.selectbox("Variable", list(VARIABLES))
    if settings.api_key and st.button("Fetch now", type="primary"):
        with st.spinner("Fetching ENTSO-E data..."):
            run_refresh(countries=(selected_country,), variables=(selected_variable,), settings=settings)
        st.success("Fetch complete")
    elif not settings.api_key:
        st.caption("Public dashboard mode: data is read from committed snapshots.")

status_path = settings.status_file
status = {}
if DATA_SOURCE == "remote":
    try:
        status = read_remote_json("data/status.json")
    except Exception:
        status = {}
elif status_path.exists():
    status = json.loads(status_path.read_text(encoding="utf-8"))

progress = {}
if DATA_SOURCE == "remote":
    try:
        progress = read_remote_json("data/progress.json")
    except Exception:
        progress = {}
elif settings.progress_file.exists():
    progress = json.loads(settings.progress_file.read_text(encoding="utf-8"))

snapshot_summary = load_snapshot_summary_frame(settings)
historical_summary = collect_file_summary(settings.data_dir) if DATA_SOURCE != "remote" else pd.DataFrame()

top = st.columns(6)
top[0].metric("Last collection", status.get("collection_time_utc", "No run yet"))
top[1].metric("OK", status.get("ok_items", 0))
top[2].metric("Warnings", status.get("warning_items", 0))
top[3].metric("Errors", status.get("error_items", 0))
top[4].metric("Snapshot files", f"{len(snapshot_summary):,}")
top[5].metric("Snapshot rows", f"{int(snapshot_summary['rows'].sum()) if not snapshot_summary.empty else 0:,}")

if progress:
    if progress.get("status") == "run_complete":
        st.success(
            "Latest run complete: "
            f"{progress.get('ok_items', 0)} ok, "
            f"{progress.get('warning_items', 0)} warnings, "
            f"{progress.get('error_items', 0)} errors "
            f"at {progress.get('updated_at_utc')}"
        )
    else:
        st.info(
            "Current fetch: "
            f"{progress.get('country')} / {progress.get('variable')} "
            f"({progress.get('chunk_start')} to {progress.get('chunk_end')}) - "
            f"{progress.get('status')} at {progress.get('updated_at_utc')}"
        )

st.subheader("Recent Snapshot Collections")
if snapshot_summary.empty:
    st.info("No update snapshots have been written yet. Use Fetch now or run the scheduler.")
else:
    latest_collection = snapshot_summary["collection_time_utc"].max()
    latest_files = snapshot_summary[snapshot_summary["collection_time_utc"] == latest_collection]
    st.markdown(f"Latest collection: `{latest_collection}`")
    st.dataframe(
        latest_files[["country", "variable", "rows", "window_start_utc", "window_end_utc", "path"]]
        .sort_values(["country", "variable"]),
        use_container_width=True,
        hide_index=True,
    )
    st.divider()
    recent = snapshot_summary.sort_values("collection_time_utc", ascending=False).head(200)
    st.dataframe(
        recent[
            [
                "collection_time_utc",
                "country",
                "variable",
                "rows",
                "window_start_utc",
                "window_end_utc",
                "path",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Snapshot Coverage")
if not snapshot_summary.empty:
    coverage = (
        snapshot_summary.groupby(["country", "variable"], as_index=False)
        .agg(
            collections=("collection_time_utc", "nunique"),
            rows=("rows", "sum"),
            latest_collection_utc=("collection_time_utc", "max"),
            latest_window_end_utc=("window_end_utc", "max"),
        )
        .sort_values(["country", "variable"])
    )
    st.dataframe(coverage, use_container_width=True, hide_index=True)

st.subheader("Preview Latest Collection")
filtered = snapshot_summary[
    (snapshot_summary.get("country") == selected_country)
    & (snapshot_summary.get("variable") == selected_variable)
] if not snapshot_summary.empty else pd.DataFrame()

if filtered.empty:
    st.info("No snapshot is available for the selected country and variable yet.")
else:
    options = filtered.sort_values("collection_time_utc", ascending=False)
    selected_collection = st.selectbox("Collection time", options["collection_time_utc"].tolist())
    preview_path = options.loc[options["collection_time_utc"] == selected_collection, "path"].iloc[0]
    frame = read_snapshot_csv(preview_path)
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    source_options = sorted(frame["source"].dropna().unique())
    selected_sources = st.multiselect("Source columns", source_options, default=source_options[:3])
    plot_frame = frame[frame["source"].isin(selected_sources)] if selected_sources else frame
    fig = px.line(
        plot_frame.tail(3000),
        x="timestamp_utc",
        y="value",
        color="source",
        title=f"{selected_country} - {selected_variable} - collected {selected_collection}",
    )
    st.plotly_chart(fig, use_container_width=True)

with st.expander("Historical Backfill Files"):
    if historical_summary.empty:
        st.info("No historical raw files found.")
    else:
        st.dataframe(
            historical_summary.sort_values(["country", "variable", "year"]),
            use_container_width=True,
            hide_index=True,
        )

st.subheader("Recent Run Events")
if DATA_SOURCE == "remote":
    try:
        history = read_remote_csv("data/run_history.csv")
    except Exception:
        history = pd.DataFrame()
    if history.empty:
        st.info("No run history yet.")
    else:
        st.dataframe(history.tail(100).iloc[::-1], use_container_width=True, hide_index=True)
elif settings.run_history.exists():
    history = pd.read_csv(settings.run_history)
    st.dataframe(history.tail(100).iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("No run history yet.")
