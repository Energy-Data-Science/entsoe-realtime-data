from __future__ import annotations

import argparse
import csv
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DEFAULT_COUNTRY_MAP = "BE=Belgium,FR=France,DE=Germany"
DEFAULT_PSSD_ROOT = (
    "/Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates"
)


@dataclass
class PssdArchiveItem:
    index: int
    manifest_row: dict
    local_path: Path
    github_path: str
    pssd_path: Path
    size_bytes: int
    status: str = "pending"
    message: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy GitHub data-branch snapshot files older than a retention window "
            "to a PSSD archive folder, optionally pruning the copied files from "
            "the checked-out data branch."
        )
    )
    parser.add_argument("--data-root", default="../entsoe-data")
    parser.add_argument("--pssd-root", default=DEFAULT_PSSD_ROOT)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--manifest", default="data/update_manifest.csv")
    parser.add_argument("--country-map", default=DEFAULT_COUNTRY_MAP)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune-source",
        action="store_true",
        help=(
            "After all selected files are copied or already present on PSSD with "
            "matching size, delete them from the data branch worktree and update "
            "data/update_manifest.csv."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing PSSD files instead of skipping matching files.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print one progress line every N files. Use 1 to print every file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_root = Path(args.data_root).resolve()
    pssd_root = Path(args.pssd_root).resolve()
    manifest_path = data_root / args.manifest
    country_map = parse_country_map(args.country_map)

    if not manifest_path.exists():
        raise SystemExit(f"No active manifest found: {manifest_path}")

    manifest = pd.read_csv(manifest_path)
    items = select_archive_items(
        manifest=manifest,
        data_root=data_root,
        pssd_root=pssd_root,
        country_map=country_map,
        retention_days=args.retention_days,
        limit=args.limit,
    )
    if not items:
        print(f"No snapshot files older than {args.retention_days} days.")
        return

    print(f"Files selected for PSSD archival: {len(items)}")
    print(f"PSSD root: {pssd_root}")
    if args.dry_run:
        for item in items:
            print(f"would_copy: {item.github_path} -> {item.pssd_path}")
        return

    errors = 0
    for number, item in enumerate(items, start=1):
        item.status, item.message = copy_one(item, overwrite=args.overwrite)
        should_print = (
            number == 1
            or number == len(items)
            or args.progress_every <= 1
            or number % args.progress_every == 0
            or item.status not in {"copied", "skipped_existing"}
        )
        if should_print:
            print(f"[{number}/{len(items)}] {item.status}: {item.github_path} -> {item.pssd_path}")
        if item.message:
            print(f"  {item.message}")
        if item.status not in {"copied", "skipped_existing"}:
            errors += 1

    local_manifest_path = pssd_root.parent / "pssd_archive_manifest.csv"
    write_pssd_manifest(local_manifest_path, items)
    print(f"Updated PSSD archive manifest: {local_manifest_path}")

    if errors:
        print(f"PSSD archive failed for {errors} files. GitHub data was not pruned.")
        sys.exit(1)

    if not args.prune_source:
        print("Source pruning disabled; GitHub data branch worktree was left unchanged.")
        return

    prune_archived_files(items)
    remaining_manifest = manifest.drop(index=[item.index for item in items]).reset_index(drop=True)
    remaining_manifest.to_csv(manifest_path, index=False)
    source_manifest_path = data_root / "data/pssd_archive_manifest.csv"
    write_pssd_manifest(source_manifest_path, items)
    print(f"Pruned {len(items)} files from data branch worktree.")
    print(f"Updated active manifest: {manifest_path}")
    print(f"Updated data-branch PSSD archive manifest: {source_manifest_path}")


def parse_country_map(raw: str) -> dict[str, str]:
    mapping = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise SystemExit(f"Invalid country map item {item!r}; use CODE=Name.")
        code, name = item.split("=", 1)
        mapping[code.strip()] = name.strip()
    return mapping


def select_archive_items(
    manifest: pd.DataFrame,
    data_root: Path,
    pssd_root: Path,
    country_map: dict[str, str],
    retention_days: int,
    limit: int | None,
) -> list[PssdArchiveItem]:
    required = {"collection_time_utc", "path", "country"}
    missing = required.difference(manifest.columns)
    if missing:
        raise SystemExit("Manifest is missing required columns: " + ", ".join(sorted(missing)))

    collection_times = pd.to_datetime(
        manifest["collection_time_utc"],
        utc=True,
        format="mixed",
        errors="coerce",
    )
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=retention_days)
    candidates = manifest.loc[collection_times < cutoff].copy()
    candidates = candidates.dropna(subset=["path"])
    candidates = candidates.sort_values(["collection_time_utc", "country", "variable", "path"])
    if limit is not None:
        candidates = candidates.head(limit)

    items = []
    for index, row in candidates.iterrows():
        github_path = str(row["path"]).lstrip("/")
        local_path = data_root / github_path
        if not local_path.exists():
            print(f"Skipping missing local file: {local_path}")
            continue
        country = str(row["country"])
        remote_country = country_map.get(country, country)
        pssd_path = map_pssd_path(github_path, pssd_root, country, remote_country)
        items.append(
            PssdArchiveItem(
                index=int(index),
                manifest_row=row.to_dict(),
                local_path=local_path,
                github_path=github_path,
                pssd_path=pssd_path,
                size_bytes=local_path.stat().st_size,
            )
        )
    return items


def map_pssd_path(github_path: str, pssd_root: Path, country: str, remote_country: str) -> Path:
    prefix = f"data/updates/{country}/"
    if github_path.startswith(prefix):
        return pssd_root / remote_country / github_path[len(prefix) :]
    return pssd_root / github_path


def copy_one(item: PssdArchiveItem, overwrite: bool) -> tuple[str, str]:
    if item.pssd_path.exists() and not overwrite:
        existing_size = item.pssd_path.stat().st_size
        if existing_size == item.size_bytes:
            return "skipped_existing", ""
        return "error_size_mismatch", (
            f"pssd_size={existing_size}; source_size={item.size_bytes}"
        )

    item.pssd_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(item.local_path, item.pssd_path)
    copied_size = item.pssd_path.stat().st_size
    if copied_size != item.size_bytes:
        return "error_verify_size", (
            f"pssd_size={copied_size}; source_size={item.size_bytes}"
        )
    return "copied", ""


def prune_archived_files(items: list[PssdArchiveItem]) -> None:
    for item in items:
        item.local_path.unlink()
        prune_empty_parents(item.local_path.parent)


def prune_empty_parents(path: Path) -> None:
    while path.name and path != path.parent:
        try:
            path.rmdir()
        except OSError:
            return
        if path.name == "updates":
            return
        path = path.parent


def write_pssd_manifest(path: Path, items: list[PssdArchiveItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    fieldnames = [
        "archived_at_utc",
        "status",
        "message",
        "size_bytes",
        "github_path",
        "pssd_path",
        "collection_time_utc",
        "run_id",
        "country",
        "variable",
        "rows",
        "window_start_utc",
        "window_end_utc",
    ]
    archived_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for item in items:
            row = item.manifest_row
            writer.writerow(
                {
                    "archived_at_utc": archived_at,
                    "status": item.status,
                    "message": item.message,
                    "size_bytes": item.size_bytes,
                    "github_path": item.github_path,
                    "pssd_path": str(item.pssd_path),
                    "collection_time_utc": row.get("collection_time_utc"),
                    "run_id": row.get("run_id"),
                    "country": row.get("country"),
                    "variable": row.get("variable"),
                    "rows": row.get("rows"),
                    "window_start_utc": row.get("window_start_utc"),
                    "window_end_utc": row.get("window_end_utc"),
                }
            )


if __name__ == "__main__":
    main()
