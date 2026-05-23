from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from entsoe_realtime.jobs import run_refresh


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    status = run_refresh()
    print(
        f"Finished run {status['run_id']}: "
        f"{status['ok_items']} ok, {status['error_items']} errors, "
        f"{status['total_rows']} stored rows."
    )

