from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from upload_to_mango import (
    authenticate_if_requested,
    ensure_collection,
    get_session,
    load_irods_env,
    parse_metadata,
    remote_size,
    resolve_env_file,
    set_metadata,
)


DEFAULT_COUNTRY_MAP = "BE=Belgium,FR=France,DE=Germany"
DEFAULT_ARCHIVE_MANIFEST = "data/mango_upload_manifest.csv"


@dataclass
class ArchiveItem:
    index: int
    manifest_row: dict
    local_path: Path
    github_path: str
    remote_path: str
    size_bytes: int
    status: str = "pending"
    message: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive GitHub snapshot files older than a retention window to Mango."
    )
    parser.add_argument("--data-root", default="../entsoe-data")
    parser.add_argument("--remote-root", default=os.getenv("MANGO_REMOTE_ROOT"), required=os.getenv("MANGO_REMOTE_ROOT") is None)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--manifest", default="data/update_manifest.csv")
    parser.add_argument("--archive-manifest", default=DEFAULT_ARCHIVE_MANIFEST)
    parser.add_argument("--country-map", default=os.getenv("MANGO_COUNTRY_MAP", DEFAULT_COUNTRY_MAP))
    parser.add_argument("--env-file", default=os.getenv("IRODS_ENVIRONMENT_FILE"))
    parser.add_argument("--authenticate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_root = Path(args.data_root).resolve()
    manifest_path = data_root / args.manifest
    archive_manifest_path = data_root / args.archive_manifest
    remote_root = args.remote_root.rstrip("/")
    country_map = parse_country_map(args.country_map)
    metadata = parse_metadata(args.metadata)

    if not manifest_path.exists():
        print(f"No active manifest found: {manifest_path}")
        return

    manifest = pd.read_csv(manifest_path)
    items = select_archive_items(
        manifest=manifest,
        data_root=data_root,
        remote_root=remote_root,
        country_map=country_map,
        retention_days=args.retention_days,
        limit=args.limit,
    )
    if not items:
        print(f"No snapshot files older than {args.retention_days} days.")
        return

    print(f"Files selected for Mango archival: {len(items)}")
    print(f"Remote root: {remote_root}")
    if args.dry_run:
        for item in items:
            print(f"would_archive: {item.github_path} -> {item.remote_path}")
        return

    env_file = resolve_env_file(args)
    irods_env = load_irods_env(env_file)
    authenticate_if_requested(args, irods_env)

    errors = 0
    with get_session(args) as session:
        for number, item in enumerate(items, start=1):
            item.status, item.message = archive_one(session, item, metadata)
            print(f"[{number}/{len(items)}] {item.status}: {item.github_path} -> {item.remote_path}")
            if item.message:
                print(f"  {item.message}")
            if item.status not in {"uploaded", "skipped_existing"}:
                errors += 1

    if errors:
        print(f"Archive failed for {errors} files. GitHub data was not pruned.")
        write_archive_manifest(archive_manifest_path, items)
        sys.exit(1)

    prune_archived_files(items)
    remaining_manifest = manifest.drop(index=[item.index for item in items]).reset_index(drop=True)
    remaining_manifest.to_csv(manifest_path, index=False)
    write_archive_manifest(archive_manifest_path, items)
    print(f"Pruned {len(items)} files from GitHub data branch.")
    print(f"Updated active manifest: {manifest_path}")
    print(f"Updated Mango archive manifest: {archive_manifest_path}")


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
    remote_root: str,
    country_map: dict[str, str],
    retention_days: int,
    limit: int | None,
) -> list[ArchiveItem]:
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
        remote_path = map_remote_path(github_path, remote_root, country, remote_country)
        items.append(
            ArchiveItem(
                index=int(index),
                manifest_row=row.to_dict(),
                local_path=local_path,
                github_path=github_path,
                remote_path=remote_path,
                size_bytes=local_path.stat().st_size,
            )
        )
    return items


def map_remote_path(github_path: str, remote_root: str, country: str, remote_country: str) -> str:
    prefix = f"data/updates/{country}/"
    if github_path.startswith(prefix):
        return f"{remote_root}/{remote_country}/{github_path[len(prefix):]}"
    return f"{remote_root}/{github_path}"


def archive_one(session, item: ArchiveItem, metadata: list[tuple[str, str, str]]) -> tuple[str, str]:
    existing_size = remote_size(session, item.remote_path)
    if existing_size is not None:
        if existing_size == item.size_bytes:
            return "skipped_existing", ""
        return "error_size_mismatch", f"remote_size={existing_size}; local_size={item.size_bytes}"

    ensure_collection(session, item.remote_path.rsplit("/", 1)[0])
    session.data_objects.put(str(item.local_path), item.remote_path)
    uploaded_size = remote_size(session, item.remote_path)
    if uploaded_size != item.size_bytes:
        return "error_verify_size", f"remote_size={uploaded_size}; local_size={item.size_bytes}"
    set_metadata(session, item.remote_path, metadata)
    return "uploaded", ""


def prune_archived_files(items: list[ArchiveItem]) -> None:
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


def write_archive_manifest(path: Path, items: list[ArchiveItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    fieldnames = [
        "archived_at_utc",
        "status",
        "message",
        "size_bytes",
        "github_path",
        "mango_path",
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
                    "mango_path": item.remote_path,
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
