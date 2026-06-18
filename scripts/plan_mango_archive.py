from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd


METADATA_PATTERNS = [
    "/data/update_manifest.csv",
    "/data/status.json",
    "/data/run_history.csv",
    "/data/progress.json",
    "/data/mango_upload_manifest.csv",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan which data-branch snapshot files should be checked out for Mango archival."
    )
    parser.add_argument("--data-root", default="../entsoe-data")
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--manifest", default="data/update_manifest.csv")
    parser.add_argument("--output", default="../mango_archive_sparse_checkout.txt")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_root = Path(args.data_root)
    manifest_path = data_root / args.manifest
    output_path = Path(args.output)

    if not manifest_path.exists():
        write_sparse_checkout(output_path, [])
        write_github_output(False, 0)
        print(f"No manifest found: {manifest_path}")
        return

    manifest = pd.read_csv(manifest_path)
    if manifest.empty or "collection_time_utc" not in manifest.columns or "path" not in manifest.columns:
        write_sparse_checkout(output_path, [])
        write_github_output(False, 0)
        print("Manifest has no archiveable rows.")
        return

    collection_times = pd.to_datetime(
        manifest["collection_time_utc"],
        utc=True,
        format="mixed",
        errors="coerce",
    )
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=args.retention_days)
    candidates = manifest.loc[collection_times < cutoff].copy()
    candidates = candidates.dropna(subset=["path"])
    candidates = candidates.sort_values(["collection_time_utc", "country", "variable", "path"])
    if args.limit is not None:
        candidates = candidates.head(args.limit)

    paths = ["/" + str(path).lstrip("/") for path in candidates["path"].drop_duplicates()]
    write_sparse_checkout(output_path, paths)
    write_github_output(bool(paths), len(paths))

    print(f"Archive cutoff UTC: {cutoff.isoformat()}")
    print(f"Candidate files: {len(paths)}")
    print(f"Sparse checkout plan: {output_path}")


def write_sparse_checkout(path: Path, archive_paths: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [*METADATA_PATTERNS, *archive_paths]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_github_output(has_candidates: bool, candidate_count: int) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"has_candidates={'true' if has_candidates else 'false'}\n")
        output.write(f"candidate_count={candidate_count}\n")


if __name__ == "__main__":
    main()
