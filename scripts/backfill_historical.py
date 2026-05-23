from __future__ import annotations

import logging
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from entsoe_realtime.jobs import run_refresh
from entsoe_realtime.config import load_settings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = replace(load_settings(), fetch_mode="full", storage_mode="merge")
    status = run_refresh(settings=settings)
    print(
        f"Finished historical backfill {status['run_id']}: "
        f"{status['ok_items']} ok, {status['error_items']} errors, "
        f"{status['historical_total_rows']} historical rows."
    )
