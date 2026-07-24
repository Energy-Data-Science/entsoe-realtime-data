from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import pandas as pd
import plotly.express as px
import streamlit as st

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

COUNTRY_LABELS = {
    "BE": "Belgium",
    "FR": "France",
    "DE": "Germany / Luxembourg",
    "NL": "Netherlands",
    "DK1": "Denmark DK1",
    "DK2": "Denmark DK2",
}


def country_label(code: str) -> str:
    return f"{code} - {COUNTRY_LABELS.get(code, code)}"


def parse_utc(value: object) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def format_time(value: object) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        return "No run yet"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def format_age(value: object) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        return "-"
    seconds = max(0, int((datetime.now(timezone.utc) - parsed.to_pydatetime()).total_seconds()))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def format_bytes(size_bytes: float | int) -> str:
    size = float(size_bytes or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{size:,.0f} {unit}"
        size /= 1024


def status_badge(error_items: int, warning_items: int) -> str:
    if error_items:
        return "Errors"
    if warning_items:
        return "Warnings"
    return "Healthy"


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
st.title("ENTSO-E Collection Monitor")

settings = load_settings(require_api_key=False)

if st.session_state.pop("refresh_data", False):
    st.cache_data.clear()

if DATA_SOURCE == "remote":
    st.caption(f"Update snapshots: {REMOTE_DATA_BASE_URL}")
else:
    st.caption(f"Update snapshots: {settings.update_dir}")

with st.sidebar:
    st.header("Controls")
    st.caption(f"Fetch: {settings.fetch_mode} | Storage: {settings.storage_mode}")
    st.caption(f"Dashboard data source: {DATA_SOURCE}")
    st.caption(f"Window: latest {settings.recent_days} days")
    if st.button("Refresh data"):
        st.session_state["refresh_data"] = True
        st.rerun()
    selected_country = st.selectbox(
        "Country / bidding zone",
        settings.countries,
        format_func=country_label,
    )
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

if not snapshot_summary.empty:
    snapshot_summary["country_label"] = snapshot_summary["country"].map(
        lambda code: COUNTRY_LABELS.get(str(code), str(code))
    )
    if "size_bytes" not in snapshot_summary.columns:
        snapshot_summary["size_bytes"] = 0

st.subheader("Latest Run")
latest_cols = st.columns(4)
ok_items = int(status.get("ok_items", 0) or 0)
warning_items = int(status.get("warning_items", 0) or 0)
error_items = int(status.get("error_items", 0) or 0)
latest_cols[0].metric("Collection time", format_time(status.get("collection_time_utc")))
latest_cols[1].metric("Age", format_age(status.get("collection_time_utc")))
latest_cols[2].metric("Fetch tasks", f"{ok_items:,} succeeded")
latest_cols[3].metric("Status", status_badge(error_items, warning_items))

run_cols = st.columns(3)
run_cols[0].metric("Warnings", f"{warning_items:,}")
run_cols[1].metric("Errors", f"{error_items:,}")
run_cols[2].metric("Countries configured", f"{len(settings.countries):,}")

st.subheader("Snapshot Inventory")
inventory_cols = st.columns(4)
snapshot_rows = int(snapshot_summary["rows"].sum()) if not snapshot_summary.empty else 0
snapshot_size = int(snapshot_summary["size_bytes"].sum()) if not snapshot_summary.empty else 0
available_countries = sorted(snapshot_summary["country"].dropna().astype(str).unique()) if not snapshot_summary.empty else []
inventory_cols[0].metric("Snapshot files", f"{len(snapshot_summary):,}")
inventory_cols[1].metric("Rows", f"{snapshot_rows:,}")
inventory_cols[2].metric("Stored size", format_bytes(snapshot_size))
inventory_cols[3].metric("Countries present", f"{len(available_countries):,} / {len(settings.countries):,}")

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

missing_countries = sorted(set(settings.countries) - set(available_countries))
if missing_countries:
    st.info(
        "No committed snapshots yet for: "
        + ", ".join(country_label(country) for country in missing_countries)
        + ". They will appear after the GitHub collection workflow stores the first snapshots."
    )

st.subheader("Recent Snapshot Collections")
if snapshot_summary.empty:
    st.info("No update snapshots have been written yet. Use Fetch now or run the scheduler.")
else:
    latest_collection = snapshot_summary["collection_time_utc"].max()
    latest_files = snapshot_summary[snapshot_summary["collection_time_utc"] == latest_collection]
    st.markdown(f"Latest collection: `{latest_collection}`")
    st.dataframe(
        latest_files[["country", "country_label", "variable", "rows", "window_start_utc", "window_end_utc", "path"]]
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
                "country_label",
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
            size_bytes=("size_bytes", "sum"),
            latest_collection_utc=("collection_time_utc", "max"),
            latest_window_end_utc=("window_end_utc", "max"),
        )
        .sort_values(["country", "variable"])
    )
    coverage["country_label"] = coverage["country"].map(lambda code: COUNTRY_LABELS.get(str(code), str(code)))
    coverage["stored_size"] = coverage["size_bytes"].map(format_bytes)
    coverage = coverage[
        [
            "country",
            "country_label",
            "variable",
            "collections",
            "rows",
            "stored_size",
            "latest_collection_utc",
            "latest_window_end_utc",
        ]
    ]
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
