from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from entsoe_realtime.config import Settings


BASE_COLUMNS = [
    "timestamp_utc",
    "timestamp_local",
    "value",
    "unit",
    "country",
    "entsoe_area",
    "variable",
    "source",
    "updated_at_utc",
]

SNAPSHOT_COLUMNS = [
    "collection_time_utc",
    "collection_time_local",
    "run_id",
    *BASE_COLUMNS,
]


def save_variable_frame(
    frame: pd.DataFrame,
    settings: Settings,
    collection_time_utc: pd.Timestamp | None = None,
    run_id: str | None = None,
) -> dict[str, int]:
    if frame.empty:
        return {}

    if settings.storage_mode == "snapshot":
        if collection_time_utc is None or run_id is None:
            raise ValueError("Snapshot storage requires collection_time_utc and run_id.")
        return save_collection_snapshot(frame, settings, collection_time_utc, run_id)

    if settings.storage_mode != "merge":
        raise ValueError("ENTSOE_STORAGE_MODE must be either 'snapshot' or 'merge'.")

    counts: dict[str, int] = {}
    frame = frame.copy()
    frame["year"] = pd.to_datetime(frame["timestamp_utc"], utc=True).dt.year

    for (country, variable, year), group in frame.groupby(["country", "variable", "year"]):
        path = settings.data_dir / country / variable / f"{year}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = merge_existing(path, group[BASE_COLUMNS])
        merged.to_csv(path, index=False)
        counts[str(path)] = len(merged)

    return counts


def save_collection_snapshot(
    frame: pd.DataFrame,
    settings: Settings,
    collection_time_utc: pd.Timestamp,
    run_id: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    frame = frame.copy()
    collection_time_utc = pd.Timestamp(collection_time_utc)
    if collection_time_utc.tzinfo is None:
        collection_time_utc = collection_time_utc.tz_localize("UTC")
    collection_time_utc = collection_time_utc.tz_convert("UTC")
    collection_time_local = collection_time_utc.tz_convert(settings.timezone)
    slug = collection_time_utc.strftime("%Y%m%dT%H%M%SZ")

    frame["collection_time_utc"] = collection_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    frame["collection_time_local"] = collection_time_local.strftime("%Y-%m-%dT%H:%M:%S%z")
    frame["run_id"] = run_id
    manifest_rows = []

    for (country, variable), group in frame.groupby(["country", "variable"]):
        path = (
            settings.update_dir
            / country
            / variable
            / collection_time_utc.strftime("%Y")
            / collection_time_utc.strftime("%m")
            / f"{slug}.csv"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        output = group[SNAPSHOT_COLUMNS].sort_values(["timestamp_utc", "source"])
        output.to_csv(path, index=False)
        counts[str(path)] = len(output)
        manifest_rows.append(
            {
                "collection_time_utc": collection_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "collection_time_local": collection_time_local.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "run_id": run_id,
                "country": country,
                "variable": variable,
                "rows": len(output),
                "window_start_utc": output["timestamp_utc"].min() if not output.empty else None,
                "window_end_utc": output["timestamp_utc"].max() if not output.empty else None,
                "path": portable_path(path, settings),
            }
        )

    append_update_manifest(manifest_rows, settings)

    return counts


def append_update_manifest(rows: list[dict], settings: Settings) -> None:
    if not rows:
        return
    settings.update_manifest.parent.mkdir(parents=True, exist_ok=True)
    incoming = pd.DataFrame(rows)
    if settings.update_manifest.exists():
        existing = pd.read_csv(settings.update_manifest)
        outgoing = pd.concat([existing, incoming], ignore_index=True)
    else:
        outgoing = incoming
    outgoing.to_csv(settings.update_manifest, index=False)


def portable_path(path: Path, settings: Settings) -> str:
    project_root = settings.update_dir.parent.parent
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def merge_existing(path: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    if path.exists():
        existing = pd.read_csv(path)
        combined = pd.concat([existing, incoming], ignore_index=True)
    else:
        combined = incoming.copy()

    combined = combined.drop_duplicates(
        subset=["timestamp_utc", "country", "variable", "source"], keep="last"
    )
    combined = combined.sort_values(["timestamp_utc", "source"]).reset_index(drop=True)
    return combined[BASE_COLUMNS]


def append_run_history(records: list[dict], settings: Settings) -> None:
    if not records:
        return
    settings.run_history.parent.mkdir(parents=True, exist_ok=True)
    incoming = pd.DataFrame(records)
    if settings.run_history.exists():
        existing = pd.read_csv(settings.run_history)
        outgoing = pd.concat([existing, incoming], ignore_index=True)
    else:
        outgoing = incoming
    outgoing.to_csv(settings.run_history, index=False)


def write_status(status: dict, settings: Settings) -> None:
    settings.status_file.parent.mkdir(parents=True, exist_ok=True)
    settings.status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")


def write_progress(progress: dict, settings: Settings) -> None:
    settings.progress_file.parent.mkdir(parents=True, exist_ok=True)
    settings.progress_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")


def collect_file_summary(data_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(data_dir.glob("*/*/*.csv")):
        try:
            frame = pd.read_csv(path, usecols=["timestamp_utc", "value"])
            latest_timestamp = frame["timestamp_utc"].max() if not frame.empty else None
            rows.append(
                {
                    "country": path.parts[-3],
                    "variable": path.parts[-2],
                    "year": path.stem,
                    "rows": len(frame),
                    "latest_timestamp_utc": latest_timestamp,
                    "path": str(path),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "country": path.parts[-3],
                    "variable": path.parts[-2],
                    "year": path.stem,
                    "rows": 0,
                    "latest_timestamp_utc": None,
                    "path": str(path),
                    "error": str(exc),
                }
            )
    return pd.DataFrame(rows)


def collect_snapshot_summary(update_dir: Path, manifest_path: Path | None = None) -> pd.DataFrame:
    if manifest_path is not None and manifest_path.exists():
        return pd.read_csv(manifest_path)

    rows = []
    for path in sorted(update_dir.glob("*/*/*/*/*.csv")):
        try:
            frame = pd.read_csv(
                path,
                usecols=["timestamp_utc", "value", "collection_time_utc", "run_id"],
            )
            rows.append(
                {
                    "country": path.parts[-5],
                    "variable": path.parts[-4],
                    "collection_time_utc": frame["collection_time_utc"].iloc[0] if not frame.empty else path.stem,
                    "run_id": frame["run_id"].iloc[0] if not frame.empty else None,
                    "rows": len(frame),
                    "window_start_utc": frame["timestamp_utc"].min() if not frame.empty else None,
                    "window_end_utc": frame["timestamp_utc"].max() if not frame.empty else None,
                    "path": str(path),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "country": path.parts[-5],
                    "variable": path.parts[-4],
                    "collection_time_utc": path.stem,
                    "run_id": None,
                    "rows": 0,
                    "window_start_utc": None,
                    "window_end_utc": None,
                    "path": str(path),
                    "error": str(exc),
                }
            )
    return pd.DataFrame(rows)
