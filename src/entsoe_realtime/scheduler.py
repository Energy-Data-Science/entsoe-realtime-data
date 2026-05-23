from __future__ import annotations

import logging
import time

import schedule

from entsoe_realtime.jobs import run_refresh


def run_forever() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler("logs/entsoe_fetcher.log"),
            logging.StreamHandler(),
        ],
    )

    run_refresh()
    schedule.every(15).minutes.do(run_refresh)

    while True:
        schedule.run_pending()
        time.sleep(1)

