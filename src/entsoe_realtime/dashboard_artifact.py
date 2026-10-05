from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


DASHBOARD_TARGETS = {
    "actual_load": "load",
    "actual_solar_generation": "solar",
    "actual_onshore_wind_generation": "wind_onshore",
    "actual_offshore_wind_generation": "wind_offshore",
}


def build_dashboard_actuals(
    hourly_dir: Path,
    output_dir: Path,
    *,
    countries: tuple[str, ...] = ("BE", "FR", "DE"),
    lookback_days: int = 90,
    generated_at: pd.Timestamp | str | None = None,
) -> dict:
    """Publish a compact actuals artifact for latency-sensitive dashboards."""
    generated_at = pd.Timestamp(generated_at or pd.Timestamp.utcnow())
    if generated_at.tzinfo is None:
        generated_at = generated_at.tz_localize("UTC")
    else:
        generated_at = generated_at.tz_convert("UTC")
    cutoff = generated_at - pd.Timedelta(days=lookback_days)

    parts: list[pd.DataFrame] = []
    for country in countries:
        for variable, target in DASHBOARD_TARGETS.items():
            for path in sorted((hourly_dir / country / variable).glob("*.parquet")):
                frame = pd.read_parquet(
                    path,
                    columns=["timestamp_utc", "value", "collection_time_utc"],
                )
                frame["timestamp_utc"] = pd.to_datetime(
                    frame["timestamp_utc"], utc=True, errors="coerce"
                )
                frame["collection_time_utc"] = pd.to_datetime(
                    frame["collection_time_utc"], utc=True, errors="coerce"
                )
                frame["actual_mw"] = pd.to_numeric(frame["value"], errors="coerce")
                frame = frame[
                    frame["timestamp_utc"].between(cutoff, generated_at, inclusive="both")
                ].dropna(subset=["timestamp_utc", "actual_mw"])
                if frame.empty:
                    continue
                frame["zone"] = country
                frame["target"] = target
                parts.append(
                    frame[
                        [
                            "timestamp_utc",
                            "collection_time_utc",
                            "zone",
                            "target",
                            "actual_mw",
                        ]
                    ]
                )

    columns = ["timestamp_utc", "collection_time_utc", "zone", "target", "actual_mw"]
    actuals = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=columns)
    if not actuals.empty:
        actuals = actuals.sort_values(
            ["zone", "target", "timestamp_utc", "collection_time_utc"]
        ).drop_duplicates(["zone", "target", "timestamp_utc"], keep="last")
        actuals = actuals.sort_values(["zone", "target", "timestamp_utc"]).reset_index(
            drop=True
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "actuals_recent.parquet"
    status_path = output_dir / "actuals_status.json"
    actuals.to_parquet(parquet_path, index=False, compression="zstd")

    series = []
    if not actuals.empty:
        summary = (
            actuals.groupby(["zone", "target"], as_index=False)
            .agg(
                actual_through_utc=("timestamp_utc", "max"),
                source_collection_through_utc=("collection_time_utc", "max"),
                rows=("actual_mw", "size"),
            )
            .sort_values(["zone", "target"])
        )
        for row in summary.to_dict("records"):
            series.append(
                {
                    "zone": row["zone"],
                    "target": row["target"],
                    "actual_through_utc": _iso_utc(row["actual_through_utc"]),
                    "source_collection_through_utc": _iso_utc(
                        row["source_collection_through_utc"]
                    ),
                    "rows": int(row["rows"]),
                }
            )

    status = {
        "schema_version": 1,
        "generated_at_utc": _iso_utc(generated_at),
        "lookback_days": lookback_days,
        "rows": int(len(actuals)),
        "series": series,
    }
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status


def _iso_utc(value: pd.Timestamp | str | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
