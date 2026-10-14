from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List recent ENTSO-E update snapshot paths from update_manifest.csv for sparse checkout."
    )
    parser.add_argument("--manifest", default="data/update_manifest.csv")
    parser.add_argument("--recent-days", type=int, default=45)
    parser.add_argument("--countries", default=None, help="Optional comma-separated country filter.")
    parser.add_argument("--variables", default=None, help="Optional comma-separated variable filter.")
    parser.add_argument("--output", default=None, help="Optional output text file; otherwise print to stdout.")
    return parser.parse_args()


def selected_values(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def main() -> None:
    args = parse_args()
    manifest_path = Path(args.manifest)
    paths = recent_update_paths(
        manifest_path,
        recent_days=args.recent_days,
        countries=selected_values(args.countries),
        variables=selected_values(args.variables),
    )
    payload = "\n".join(paths)
    if payload:
        payload += "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


def recent_update_paths(
    manifest_path: Path,
    *,
    recent_days: int,
    countries: set[str] | None = None,
    variables: set[str] | None = None,
) -> list[str]:
    if not manifest_path.exists():
        return []
    manifest = pd.read_csv(manifest_path)
    required = {"collection_time_utc", "path"}
    if not required.issubset(manifest.columns):
        return []

    collection_time = pd.to_datetime(manifest["collection_time_utc"], utc=True, errors="coerce")
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=recent_days)
    keep = collection_time >= cutoff
    if countries is not None and "country" in manifest.columns:
        keep &= manifest["country"].isin(countries)
    if variables is not None and "variable" in manifest.columns:
        keep &= manifest["variable"].isin(variables)

    paths = (
        manifest.loc[keep, "path"]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    return [path if path.startswith("/") else f"/{path}" for path in paths]


if __name__ == "__main__":
    main()
