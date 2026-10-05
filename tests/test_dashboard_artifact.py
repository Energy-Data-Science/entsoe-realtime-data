from __future__ import annotations

import json

import pandas as pd

from entsoe_realtime.dashboard_artifact import build_dashboard_actuals


def test_build_dashboard_actuals_keeps_recent_latest_rows(tmp_path) -> None:
    hourly = tmp_path / "hourly" / "BE" / "actual_load"
    hourly.mkdir(parents=True)
    pd.DataFrame(
        {
            "timestamp_utc": [
                "2026-01-01T00:00:00Z",
                "2026-04-01T10:00:00Z",
                "2026-04-01T10:00:00Z",
                "2026-04-01T11:00:00Z",
            ],
            "value": [1.0, 100.0, 101.0, 102.0],
            "collection_time_utc": [
                "2026-01-01T01:00:00Z",
                "2026-04-01T10:10:00Z",
                "2026-04-01T10:20:00Z",
                "2026-04-01T11:20:00Z",
            ],
        }
    ).to_parquet(hourly / "2026.parquet", index=False)

    output = tmp_path / "dashboard"
    status = build_dashboard_actuals(
        tmp_path / "hourly",
        output,
        countries=("BE",),
        lookback_days=7,
        generated_at="2026-04-01T12:00:00Z",
    )

    artifact = pd.read_parquet(output / "actuals_recent.parquet")
    assert artifact["actual_mw"].tolist() == [101.0, 102.0]
    assert artifact["target"].unique().tolist() == ["load"]
    assert status["rows"] == 2
    saved_status = json.loads((output / "actuals_status.json").read_text())
    assert saved_status["generated_at_utc"] == "2026-04-01T12:00:00Z"
    assert saved_status["series"][0]["actual_through_utc"] == "2026-04-01T11:00:00Z"


def test_dashboard_actuals_excludes_unavailable_es_offshore(tmp_path) -> None:
    hourly = tmp_path / "hourly" / "ES" / "actual_offshore_wind_generation"
    hourly.mkdir(parents=True)
    pd.DataFrame({
        "timestamp_utc": ["2026-04-01T10:00:00Z"],
        "value": [0.0],
        "collection_time_utc": ["2026-04-01T10:20:00Z"],
    }).to_parquet(hourly / "2026.parquet", index=False)
    output = tmp_path / "dashboard"
    status = build_dashboard_actuals(tmp_path / "hourly", output, countries=("ES",), generated_at="2026-04-01T12:00:00Z")
    assert status["rows"] == 0
    assert status["series"] == []
