from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def main() -> None:
    manifest_path = Path(os.getenv("ENTSOE_UPDATE_MANIFEST", "data/update_manifest.csv"))
    min_interval_minutes = int(os.getenv("ENTSOE_MIN_COLLECTION_INTERVAL_MINUTES", "13"))

    if not manifest_path.exists():
        write_output(True, "manifest_missing")
        return

    manifest = pd.read_csv(manifest_path, usecols=["collection_time_utc"])
    if manifest.empty:
        write_output(True, "manifest_empty")
        return

    collection_times = pd.to_datetime(
        manifest["collection_time_utc"],
        utc=True,
        format="mixed",
        errors="coerce",
    ).dropna()
    if collection_times.empty:
        write_output(True, "manifest_has_no_parseable_collection_times")
        return

    latest = collection_times.max()
    age_minutes = (pd.Timestamp.utcnow() - latest).total_seconds() / 60
    should_collect = age_minutes >= min_interval_minutes
    reason = f"latest_collection_age_minutes={age_minutes:.1f}"
    write_output(should_collect, reason)


def write_output(should_collect: bool, reason: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    lines = [
        f"should_collect={'true' if should_collect else 'false'}",
        f"reason={reason}",
    ]
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
