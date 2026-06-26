from __future__ import annotations

import argparse
import csv
import os
import subprocess
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a data-branch commit that removes files already copied to PSSD, "
            "without relying on a large external-drive working tree."
        )
    )
    parser.add_argument("--base-ref", default="origin/data")
    parser.add_argument(
        "--archive-commit",
        required=True,
        help="Commit containing data/pssd_archive_manifest.csv for the copied batch.",
    )
    parser.add_argument("--message", default="data: stage old snapshots on PSSD")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--push-ref", default="data")
    parser.add_argument("--push", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_ref = args.base_ref
    base_commit = git("rev-parse", f"{base_ref}^{{commit}}")
    archive_manifest = git("show", f"{args.archive_commit}:data/pssd_archive_manifest.csv")
    archive_rows = read_csv_text(archive_manifest)
    archived_paths = {row["github_path"] for row in archive_rows if row.get("github_path")}
    if not archived_paths:
        raise SystemExit("Archive manifest did not contain any github_path values.")

    active_manifest = git("show", f"{base_ref}:data/update_manifest.csv")
    active_rows = read_csv_text(active_manifest)
    active_fieldnames = csv_fieldnames(active_manifest)
    remaining_rows = [
        row for row in active_rows if str(row.get("path", "")).lstrip("/") not in archived_paths
    ]
    removed_rows = len(active_rows) - len(remaining_rows)
    if removed_rows != len(archived_paths):
        print(
            "Warning: archived path count does not exactly match active manifest removals: "
            f"archived_paths={len(archived_paths)}, removed_rows={removed_rows}"
        )

    try:
        existing_pssd_manifest = git("show", f"{base_ref}:data/pssd_archive_manifest.csv")
    except subprocess.CalledProcessError:
        existing_pssd_rows = []
        pssd_fieldnames = csv_fieldnames(archive_manifest)
    else:
        existing_pssd_rows = read_csv_text(existing_pssd_manifest)
        pssd_fieldnames = csv_fieldnames(existing_pssd_manifest)

    existing_keys = {
        (row.get("github_path"), row.get("archived_at_utc"))
        for row in existing_pssd_rows
    }
    new_pssd_rows = [
        row
        for row in archive_rows
        if (row.get("github_path"), row.get("archived_at_utc")) not in existing_keys
    ]
    combined_pssd_rows = [*existing_pssd_rows, *new_pssd_rows]

    with tempfile.TemporaryDirectory(prefix="entsoe-pssd-prune-") as tmpdir:
        tmp = Path(tmpdir)
        active_path = tmp / "update_manifest.csv"
        pssd_path = tmp / "pssd_archive_manifest.csv"
        index_path = tmp / "index"
        write_csv(active_path, active_fieldnames, remaining_rows)
        write_csv(pssd_path, pssd_fieldnames, combined_pssd_rows)

        env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
        git_env(env, "read-tree", base_ref)
        remove_paths(env, sorted(archived_paths))
        add_file_to_index(env, active_path, "data/update_manifest.csv")
        add_file_to_index(env, pssd_path, "data/pssd_archive_manifest.csv")

        tree = git_env(env, "write-tree")
        commit = git("commit-tree", tree, "-p", base_commit, "-m", args.message)

    print(f"Base commit: {base_commit}")
    print(f"Archive commit: {args.archive_commit}")
    print(f"Archived paths: {len(archived_paths)}")
    print(f"Rows removed from active manifest: {removed_rows}")
    print(f"PSSD archive rows added: {len(new_pssd_rows)}")
    print(f"New prune commit: {commit}")

    if args.push:
        git("push", args.remote, f"{commit}:refs/heads/{args.push_ref}")
        print(f"Pushed {commit} to {args.remote}/{args.push_ref}")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def git_env(env: dict[str, str], *args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, env=env).strip()


def read_csv_text(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(text.splitlines()))


def csv_fieldnames(text: str) -> list[str]:
    reader = csv.reader(text.splitlines())
    return next(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def remove_paths(env: dict[str, str], paths: list[str]) -> None:
    process = subprocess.run(
        ["git", "update-index", "--force-remove", "--stdin"],
        input="\n".join(paths) + "\n",
        text=True,
        env=env,
        check=True,
    )
    if process.returncode != 0:
        raise SystemExit(process.returncode)


def add_file_to_index(env: dict[str, str], source: Path, repo_path: str) -> None:
    blob = git("hash-object", "-w", str(source))
    git_env(env, "update-index", "--add", "--cacheinfo", f"100644,{blob},{repo_path}")


if __name__ == "__main__":
    main()
