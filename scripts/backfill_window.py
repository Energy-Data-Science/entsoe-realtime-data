from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from entsoe_realtime.client import EntsoeFetcher
from entsoe_realtime.config import VARIABLES, load_settings
from entsoe_realtime.dashboard_artifact import build_dashboard_actuals
from entsoe_realtime.storage import save_hourly_archive


DEFAULT_VARIABLES = (
    "actual_load",
    "forecast_load",
    "actual_solar_generation",
    "forecast_solar_generation",
    "actual_onshore_wind_generation",
    "forecast_onshore_wind_generation",
    "actual_offshore_wind_generation",
    "forecast_offshore_wind_generation",
)


def values(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill a bounded ENTSO-E window into the hourly archive.")
    parser.add_argument("--start", required=True, help="Inclusive date or timestamp")
    parser.add_argument("--end", required=True, help="Exclusive date or timestamp")
    parser.add_argument("--countries", default="NL,DK1,DK2,ES,PT")
    parser.add_argument("--variables", default=",".join(DEFAULT_VARIABLES))
    parser.add_argument("--hourly-dir", type=Path, default=Path("data/hourly"))
    parser.add_argument("--dashboard-dir", type=Path, default=Path("data/dashboard"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = replace(load_settings(), hourly_dir=args.hourly_dir.resolve())
    start = pd.Timestamp(args.start, tz=settings.timezone)
    end = pd.Timestamp(args.end, tz=settings.timezone)
    if end <= start:
        raise SystemExit("--end must be after --start")

    run_id = f"bounded-backfill-{uuid4()}"
    collected_at = pd.Timestamp.now(tz="UTC")
    fetcher = EntsoeFetcher(settings)
    report: list[dict[str, object]] = []
    for country in values(args.countries):
        for variable in values(args.variables):
            spec = VARIABLES[variable]
            try:
                frame = fetcher.fetch_variable(country, spec, start, end)
                counts = save_hourly_archive(frame, settings, collected_at, run_id)
                report.append({"country": country, "variable": variable, "status": "ok", "fetched_rows": len(frame), "hourly_rows": sum(counts.values())})
            except Exception as exc:
                logging.exception("Failed %s %s", country, variable)
                report.append({"country": country, "variable": variable, "status": "error", "message": str(exc)})

    artifact = build_dashboard_actuals(args.hourly_dir.resolve(), args.dashboard_dir.resolve())
    output = {"run_id": run_id, "start": start.isoformat(), "end": end.isoformat(), "items": report, "dashboard": artifact}
    print(json.dumps(output, indent=2, default=str))
    if any(item["status"] == "error" for item in report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
