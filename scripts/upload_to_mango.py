from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience dependency
    load_dotenv = None

try:
    from irods.session import iRODSSession
except ImportError:  # pragma: no cover - handled in main
    iRODSSession = None


DEFAULT_LOCAL_ROOT = (
    "/Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates"
)
DEFAULT_MANIFEST = "data/mango_upload_manifest.csv"
DEFAULT_IRODS_ENV_FILE = "~/.irods/irods_environment.json"


@dataclass
class UploadResult:
    local_path: Path
    remote_path: str
    size_bytes: int
    status: str
    message: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload ENTSO-E snapshot files to a Mango/iRODS collection."
    )
    parser.add_argument(
        "--local-root",
        default=os.getenv("MANGO_LOCAL_ROOT", DEFAULT_LOCAL_ROOT),
        help="Local directory to upload recursively.",
    )
    parser.add_argument(
        "--remote-root",
        default=os.getenv("MANGO_REMOTE_ROOT"),
        required=os.getenv("MANGO_REMOTE_ROOT") is None,
        help="Target Mango/iRODS collection, for example /set/home/PROJECT/data/updates.",
    )
    parser.add_argument(
        "--manifest",
        default=os.getenv("MANGO_UPLOAD_MANIFEST", DEFAULT_MANIFEST),
        help="Local CSV manifest recording upload results.",
    )
    parser.add_argument(
        "--pattern",
        default=os.getenv("MANGO_UPLOAD_PATTERN", "*.csv"),
        help="File pattern under local root. Default: *.csv",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without creating Mango collections or uploading files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite remote files that already exist.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Upload at most this many files. Useful for testing.",
    )
    parser.add_argument(
        "--env-file",
        default=os.getenv("IRODS_ENVIRONMENT_FILE"),
        help=(
            "Path to irods_environment.json. If omitted, the script uses "
            "~/.irods/irods_environment.json when present, then falls back to env vars."
        ),
    )
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help=(
            "Run Mango interactive authentication with mango_auth.iinit before "
            "opening the iRODS session."
        ),
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Metadata AVU to set on uploaded objects. Can be repeated.",
    )
    parser.add_argument(
        "--include-existing-in-manifest",
        action="store_true",
        help="Also write skipped existing files to the upload manifest.",
    )
    return parser.parse_args()


def resolve_env_file(args: argparse.Namespace) -> Path | None:
    if args.env_file:
        return Path(args.env_file).expanduser()
    default_env = Path(DEFAULT_IRODS_ENV_FILE).expanduser()
    if default_env.exists():
        return default_env
    return None


def load_irods_env(env_file: Path | None) -> dict:
    if env_file is None:
        return {}
    if not env_file.exists():
        raise SystemExit(f"iRODS environment file does not exist: {env_file}")
    with env_file.open(encoding="utf-8") as source:
        return json.load(source)


def authenticate_if_requested(args: argparse.Namespace, irods_env: dict) -> None:
    if not args.authenticate:
        return
    try:
        from mango_auth import iinit
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: mango_auth. Install with `pip install mango_auth`."
        ) from exc

    user = irods_env.get("irods_user_name") or os.getenv("IRODS_USER")
    zone = irods_env.get("irods_zone_name") or os.getenv("IRODS_ZONE")
    host = irods_env.get("irods_host") or os.getenv("IRODS_HOST")
    missing = [
        name
        for name, value in {
            "irods_user_name/IRODS_USER": user,
            "irods_zone_name/IRODS_ZONE": zone,
            "irods_host/IRODS_HOST": host,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(
            "Cannot authenticate because these settings are missing: "
            + ", ".join(missing)
        )

    iinit(user, zone, host)


def get_session(args: argparse.Namespace):
    if iRODSSession is None:
        raise SystemExit(
            "Missing dependency: python-irodsclient. Install with "
            "`pip install python-irodsclient` or `pip install -r requirements.txt`."
        )

    env_file = resolve_env_file(args)
    if env_file is not None:
        password = os.getenv("IRODS_PASSWORD")
        if password:
            kwargs = load_irods_env(env_file)
            kwargs["password"] = password
            return iRODSSession(**kwargs)
        return iRODSSession(irods_env_file=str(env_file))

    host = os.getenv("IRODS_HOST")
    port = os.getenv("IRODS_PORT", "1247")
    user = os.getenv("IRODS_USER")
    password = os.getenv("IRODS_PASSWORD")
    zone = os.getenv("IRODS_ZONE")
    auth_scheme = os.getenv("IRODS_AUTHENTICATION_SCHEME")

    missing = [
        name
        for name, value in {
            "IRODS_HOST": host,
            "IRODS_USER": user,
            "IRODS_PASSWORD": password,
            "IRODS_ZONE": zone,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing iRODS connection settings: "
            + ", ".join(missing)
            + ". Set them in .env or pass --env-file."
        )

    kwargs = {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "zone": zone,
    }
    if auth_scheme:
        kwargs["authentication_scheme"] = auth_scheme
    return iRODSSession(**kwargs)


def iter_files(root: Path, pattern: str, limit: int | None = None) -> Iterable[Path]:
    count = 0
    for path in sorted(root.rglob(pattern)):
        if not path.is_file():
            continue
        yield path
        count += 1
        if limit is not None and count >= limit:
            return


def remote_path_for(local_file: Path, local_root: Path, remote_root: str) -> str:
    relative = local_file.relative_to(local_root).as_posix()
    return f"{remote_root.rstrip('/')}/{relative}"


def collection_for(remote_path: str) -> str:
    return remote_path.rsplit("/", 1)[0]


def ensure_collection(session, remote_collection: str) -> None:
    if session.collections.exists(remote_collection):
        return
    try:
        session.collections.create(remote_collection, recurse=True)
    except TypeError:
        create_collection_chain(session, remote_collection)


def create_collection_chain(session, remote_collection: str) -> None:
    parts = [part for part in remote_collection.split("/") if part]
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        if not session.collections.exists(current):
            session.collections.create(current)


def remote_size(session, remote_path: str) -> int | None:
    if not session.data_objects.exists(remote_path):
        return None
    data_object = session.data_objects.get(remote_path)
    return int(getattr(data_object, "size", 0))


def parse_metadata(items: list[str]) -> list[tuple[str, str, str]]:
    metadata = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid metadata item {item!r}; use KEY=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SystemExit(f"Invalid metadata item {item!r}; key is empty.")
        metadata.append((key, value, ""))
    return metadata


def set_metadata(session, remote_path: str, metadata: list[tuple[str, str, str]]) -> None:
    if not metadata:
        return
    data_object = session.data_objects.get(remote_path)
    for key, value, units in metadata:
        data_object.metadata.set(key, value, units)


def upload_file(
    session,
    local_file: Path,
    local_root: Path,
    remote_root: str,
    overwrite: bool,
    dry_run: bool,
    metadata: list[tuple[str, str, str]],
) -> UploadResult:
    remote_path = remote_path_for(local_file, local_root, remote_root)
    size_bytes = local_file.stat().st_size
    existing_size = None if dry_run else remote_size(session, remote_path)

    if existing_size is not None and not overwrite:
        if existing_size == size_bytes:
            return UploadResult(local_file, remote_path, size_bytes, "skipped_existing")
        return UploadResult(
            local_file,
            remote_path,
            size_bytes,
            "skipped_size_mismatch",
            f"remote_size={existing_size}",
        )

    if dry_run:
        status = "would_overwrite" if existing_size is not None else "would_upload"
        return UploadResult(local_file, remote_path, size_bytes, status)

    ensure_collection(session, collection_for(remote_path))
    options = {"force": True} if overwrite else {}
    session.data_objects.put(str(local_file), remote_path, **options)
    set_metadata(session, remote_path, metadata)
    return UploadResult(local_file, remote_path, size_bytes, "uploaded")


def append_manifest(manifest_path: Path, results: list[UploadResult]) -> None:
    if not results:
        return
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not manifest_path.exists()
    with manifest_path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "uploaded_at_utc",
                "status",
                "size_bytes",
                "local_path",
                "remote_path",
                "message",
            ],
        )
        if write_header:
            writer.writeheader()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for result in results:
            writer.writerow(
                {
                    "uploaded_at_utc": timestamp,
                    "status": result.status,
                    "size_bytes": result.size_bytes,
                    "local_path": str(result.local_path),
                    "remote_path": result.remote_path,
                    "message": result.message,
                }
            )


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()

    args = parse_args()
    local_root = Path(args.local_root).expanduser().resolve()
    remote_root = args.remote_root.rstrip("/")
    manifest_path = Path(args.manifest)
    metadata = parse_metadata(args.metadata)
    env_file = resolve_env_file(args)
    irods_env = load_irods_env(env_file)

    if not local_root.exists():
        raise SystemExit(f"Local root does not exist: {local_root}")
    if not local_root.is_dir():
        raise SystemExit(f"Local root is not a directory: {local_root}")

    files = list(iter_files(local_root, args.pattern, args.limit))
    if not files:
        print(f"No files matched {args.pattern!r} under {local_root}")
        return

    print(f"Local root:  {local_root}")
    print(f"Remote root: {remote_root}")
    print(f"Files found: {len(files)}")
    if args.dry_run:
        print("Dry run: no files will be uploaded.")
    elif env_file is not None:
        print(f"iRODS env:   {env_file}")

    results: list[UploadResult] = []
    if not args.dry_run:
        authenticate_if_requested(args, irods_env)
    session_context = nullcontext(None) if args.dry_run else get_session(args)
    with session_context as session:
        for index, local_file in enumerate(files, start=1):
            try:
                result = upload_file(
                    session,
                    local_file,
                    local_root,
                    remote_root,
                    args.overwrite,
                    args.dry_run,
                    metadata,
                )
            except Exception as exc:  # noqa: BLE001 - keep batch uploads running
                result = UploadResult(
                    local_file=local_file,
                    remote_path=remote_path_for(local_file, local_root, remote_root),
                    size_bytes=local_file.stat().st_size,
                    status="error",
                    message=str(exc),
                )
            results.append(result)
            print(
                f"[{index}/{len(files)}] {result.status}: "
                f"{result.local_path} -> {result.remote_path}"
            )
            if result.message:
                print(f"  {result.message}")

    manifest_results = [
        result
        for result in results
        if args.include_existing_in_manifest
        or result.status not in {"skipped_existing", "would_upload", "would_overwrite"}
    ]
    if not args.dry_run:
        append_manifest(manifest_path, manifest_results)
        print(f"Manifest updated: {manifest_path}")

    uploaded = sum(1 for result in results if result.status == "uploaded")
    skipped = sum(1 for result in results if result.status.startswith("skipped"))
    errors = sum(1 for result in results if result.status == "error")
    print(f"Done: {uploaded} uploaded, {skipped} skipped, {errors} errors.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
