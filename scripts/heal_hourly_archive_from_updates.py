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
        description=(
            "Replay recent 15-minute ENTSO-E update snapshots into the durable hourly archive. "
            "This heals archive gaps when later ENTSO-E publications contain values that were "
            "missing from earlier collection runs."
        )
    )
    parser.add_argument("--update-dir", default="data/updates")
    parser.add_argument("--hourly-dir", default="data/hourly")
    parser.add_argument("--manifest", default="data/update_manifest.csv")
    parser.add_argument("--recent-days", type=int, default=45)
    parser.add_argument("--countries", default=None, help="Optional comma-separated country filter.")
    parser.add_argument("--variables", default=None, help="Optional comma-separated variable filter.")
    parser.add_argument("--run-id", default="hourly-archive-heal")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    update_dir = (PROJECT_ROOT / args.update_dir).resolve()
    hourly_dir = (PROJECT_ROOT / args.hourly_dir).resolve()
    manifest_path = (PROJECT_ROOT / args.manifest).resolve()
    settings = replace(load_settings(require_api_key=False), update_dir=update_dir, hourly_dir=hourly_dir)

    countries = selected_values(args.countries)
    variables = selected_values(args.variables) or VARIABLES
    snapshots = recent_snapshots(
        manifest_path,
        update_dir=update_dir,
        recent_days=args.recent_days,
        countries=countries,
        variables=variables,
    )

    processed_files = 0
    processed_rows = 0
    touched_outputs: set[str] = set()
    for snapshot in snapshots:
        path = snapshot["path"]
        if not path.exists():
            logger.debug("Skipping missing sparse-checkout snapshot: %s", path)
            continue
        try:
            frame = pd.read_csv(path)
        except Exception as exc:
            logger.warning("Skipping unreadable snapshot %s: %s", path, exc)
            continue
        if frame.empty:
            continue
        collection_time_utc = pd.to_datetime(snapshot["collection_time_utc"], utc=True, errors="coerce")
        if pd.isna(collection_time_utc):
            collection_time_utc = infer_collection_time(frame)
        run_id = str(snapshot.get("run_id") or args.run_id)
        counts = save_hourly_archive(frame, settings, collection_time_utc, run_id)
        processed_files += 1
        processed_rows += len(frame)
        touched_outputs.update(counts)

    print(
        "Hourly archive heal complete: "
        f"{processed_files} snapshot file(s), {processed_rows} row(s), "
        f"{len(touched_outputs)} hourly parquet file(s) touched."
    )


def selected_values(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def recent_snapshots(
    manifest_path: Path,
    *,
    update_dir: Path,
    recent_days: int,
    countries: set[str] | None,
    variables: set[str] | None,
) -> list[dict[str, object]]:
    if not manifest_path.exists():
        return snapshots_from_files(update_dir, countries=countries, variables=variables)
    manifest = pd.read_csv(manifest_path)
    required = {"collection_time_utc", "path"}
    if not required.issubset(manifest.columns):
        return snapshots_from_files(update_dir, countries=countries, variables=variables)

    collection_time = pd.to_datetime(manifest["collection_time_utc"], utc=True, errors="coerce")
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=recent_days)
    keep = collection_time >= cutoff
    if countries is not None and "country" in manifest.columns:
        keep &= manifest["country"].isin(countries)
    if variables is not None and "variable" in manifest.columns:
        keep &= manifest["variable"].isin(variables)

    rows = manifest.loc[keep].copy()
    if rows.empty:
        return []
    rows["collection_time_sort"] = collection_time.loc[rows.index]
    rows = rows.sort_values(["collection_time_sort", "path"])
    snapshots: list[dict[str, object]] = []
    for row in rows.to_dict("records"):
        path = Path(str(row["path"]))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        snapshots.append(
            {
                "path": path,
                "collection_time_utc": row.get("collection_time_utc"),
                "run_id": row.get("run_id"),
            }
        )
    return snapshots


def snapshots_from_files(
    update_dir: Path,
    *,
    countries: set[str] | None,
    variables: set[str] | None,
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for path in sorted(update_dir.glob("*/*/*/*/*.csv")):
        country = path.parts[-5]
        variable = path.parts[-4]
        if countries is not None and country not in countries:
            continue
        if variables is not None and variable not in variables:
            continue
        snapshots.append({"path": path, "collection_time_utc": path.stem, "run_id": "hourly-archive-heal"})
    return snapshots


def infer_collection_time(frame: pd.DataFrame) -> pd.Timestamp:
    if "collection_time_utc" in frame.columns:
        collected = pd.to_datetime(frame["collection_time_utc"], utc=True, errors="coerce").dropna()
        if not collected.empty:
            return collected.max()
    if "updated_at_utc" in frame.columns:
        updated = pd.to_datetime(frame["updated_at_utc"], utc=True, errors="coerce").dropna()
        if not updated.empty:
            return updated.max()
    return pd.Timestamp.utcnow()


if __name__ == "__main__":
    main()
