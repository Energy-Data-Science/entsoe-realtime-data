from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from entsoe_realtime.config import VARIABLES, load_settings
from entsoe_realtime.storage import save_hourly_archive


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the durable hourly parquet archive from historical raw CSV files."
    )
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--hourly-dir", default="data/hourly")
    parser.add_argument(
        "--countries",
        default=None,
        help="Comma-separated country codes. Defaults to all folders under raw-dir.",
    )
    parser.add_argument(
        "--variables",
        default=None,
        help="Comma-separated variable names. Defaults to all configured variables found under raw-dir.",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Comma-separated years to build, for example 2025,2026. Defaults to all raw years.",
    )
    parser.add_argument("--run-id", default="historical-hourly-bootstrap")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    raw_dir = (PROJECT_ROOT / args.raw_dir).resolve()
    hourly_dir = (PROJECT_ROOT / args.hourly_dir).resolve()
    settings = replace(load_settings(require_api_key=False), hourly_dir=hourly_dir)

    countries = selected_values(args.countries, [path.name for path in raw_dir.iterdir() if path.is_dir()])
    variables = selected_values(args.variables, VARIABLES)
    years = selected_values(args.years, None)

    files = iter_raw_files(raw_dir, countries, variables, years)
    total_files = 0
    total_rows = 0
    for path in files:
        frame = pd.read_csv(path)
        if frame.empty:
            logger.info("Skipping empty raw file: %s", path)
            continue
        collection_time_utc = infer_collection_time(frame)
        counts = save_hourly_archive(frame, settings, collection_time_utc, args.run_id)
        rows_written = sum(counts.values())
        total_files += 1
        total_rows += len(frame)
        logger.info("Merged %s raw rows from %s into %s hourly rows", len(frame), path, rows_written)

    print(
        f"Finished hourly archive build: {total_files} raw files, "
        f"{total_rows} raw rows processed, output={hourly_dir}"
    )


def selected_values(raw: str | None, default) -> set[str] | None:
    if raw is None:
        return set(default) if default is not None else None
    return {item.strip() for item in raw.split(",") if item.strip()}


def iter_raw_files(
    raw_dir: Path,
    countries: set[str] | None,
    variables: set[str] | None,
    years: set[str] | None,
):
    for path in sorted(raw_dir.glob("*/*/*.csv")):
        country = path.parts[-3]
        variable = path.parts[-2]
        year = path.stem
        if countries is not None and country not in countries:
            continue
        if variables is not None and variable not in variables:
            continue
        if years is not None and year not in years:
            continue
        yield path


def infer_collection_time(frame: pd.DataFrame) -> pd.Timestamp:
    if "updated_at_utc" in frame.columns:
        updated = pd.to_datetime(frame["updated_at_utc"], utc=True, errors="coerce").dropna()
        if not updated.empty:
            return updated.max()
    return pd.Timestamp.utcnow()


if __name__ == "__main__":
    main()
