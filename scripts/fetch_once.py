from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from entsoe_realtime.jobs import run_refresh
from entsoe_realtime.dashboard_artifact import build_dashboard_actuals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    variables_env = os.getenv("ENTSOE_VARIABLES", "").strip()
    variables = tuple(item.strip() for item in variables_env.split(",") if item.strip()) or None
    status = run_refresh(variables=variables)
    artifact_status = build_dashboard_actuals(
        PROJECT_ROOT / os.getenv("ENTSOE_HOURLY_DIR", "data/hourly"),
        PROJECT_ROOT / os.getenv("ENTSOE_DASHBOARD_DIR", "data/dashboard"),
        lookback_days=int(os.getenv("ENTSOE_DASHBOARD_LOOKBACK_DAYS", "90")),
        generated_at=status["finished_at_utc"],
    )
    print(
        f"Finished run {status['run_id']}: "
        f"{status['ok_items']} ok, {status['error_items']} errors, "
        f"{status['total_rows']} stored rows."
    )
    print(
        f"Published dashboard actuals: {artifact_status['rows']} rows across "
        f"{len(artifact_status['series'])} series."
    )
