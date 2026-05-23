from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

import pandas as pd

from entsoe_realtime.client import EntsoeFetcher, make_time_window
from entsoe_realtime.config import VARIABLES, Settings, load_settings
from entsoe_realtime.storage import (
    append_run_history,
    collect_file_summary,
    collect_snapshot_summary,
    save_variable_frame,
    write_progress,
    write_status,
)


logger = logging.getLogger(__name__)


def sanitize_error_message(message: str, settings: Settings) -> str:
    message = message.replace(settings.api_key, "<redacted>")
    return re.sub(r"securityToken=[^&\\s'\")]+", "securityToken=<redacted>", message)


def run_refresh(
    countries: tuple[str, ...] | None = None,
    variables: tuple[str, ...] | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or load_settings()
    countries = countries or settings.countries
    variable_names = variables or tuple(VARIABLES)

    run_id = str(uuid4())
    run_started = pd.Timestamp.utcnow()
    collection_time_utc = run_started

    def progress_callback(progress: dict) -> None:
        write_progress({"run_id": run_id, **progress}, settings)

    fetcher = EntsoeFetcher(settings, progress_callback=progress_callback)
    history: list[dict] = []

    logger.info("Starting ENTSO-E refresh %s", run_id)

    for country in countries:
        for variable_name in variable_names:
            spec = VARIABLES[variable_name]
            start, end = make_time_window(spec, settings)
            item_started = time.time()
            event = {
                "run_id": run_id,
                "country": country,
                "variable": variable_name,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "started_at_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            try:
                frame = fetcher.fetch_variable(country, spec, start, end)
                save_counts = save_variable_frame(frame, settings, collection_time_utc, run_id)
                item_warnings = list(fetcher.warnings)
                if frame.empty:
                    item_warnings.append("No records fetched; no snapshot file was written.")
                warnings = "; ".join(item_warnings)
                event.update(
                    {
                        "status": "warning" if warnings else "ok",
                        "records_fetched": len(frame),
                        "files_touched": len(save_counts),
                        "message": sanitize_error_message(warnings, settings),
                    }
                )
                if warnings:
                    logger.warning("%s %s: %s rows with skipped windows", country, variable_name, len(frame))
                else:
                    logger.info("%s %s: %s rows", country, variable_name, len(frame))
            except Exception as exc:
                event.update(
                    {
                        "status": "error",
                        "records_fetched": 0,
                        "files_touched": 0,
                        "message": sanitize_error_message(str(exc), settings),
                    }
                )
                logger.error(
                    "Failed %s %s: %s",
                    country,
                    variable_name,
                    sanitize_error_message(str(exc), settings),
                )

            event["duration_seconds"] = round(time.time() - item_started, 2)
            history.append(event)
            append_run_history([event], settings)

    historical_summary = collect_file_summary(settings.data_dir)
    snapshot_summary = collect_snapshot_summary(settings.update_dir, settings.update_manifest)
    active_summary = snapshot_summary if settings.storage_mode == "snapshot" else historical_summary
    status = {
        "run_id": run_id,
        "mode": settings.fetch_mode,
        "storage_mode": settings.storage_mode,
        "collection_time_utc": collection_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_at_utc": run_started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "countries": list(countries),
        "variables": list(variable_names),
        "ok_items": sum(item["status"] in {"ok", "warning"} for item in history),
        "warning_items": sum(item["status"] == "warning" for item in history),
        "error_items": sum(item["status"] == "error" for item in history),
        "file_count": int(len(active_summary)),
        "total_rows": int(active_summary["rows"].sum()) if not active_summary.empty else 0,
        "historical_file_count": int(len(historical_summary)),
        "historical_total_rows": int(historical_summary["rows"].sum()) if not historical_summary.empty else 0,
        "snapshot_file_count": int(len(snapshot_summary)),
        "snapshot_total_rows": int(snapshot_summary["rows"].sum()) if not snapshot_summary.empty else 0,
        "history": history[-20:],
    }
    write_status(status, settings)
    write_progress(
        {
            "run_id": run_id,
            "status": "run_complete",
            "updated_at_utc": status["finished_at_utc"],
            "collection_time_utc": status["collection_time_utc"],
            "ok_items": status["ok_items"],
            "warning_items": status["warning_items"],
            "error_items": status["error_items"],
        },
        settings,
    )
    return status
