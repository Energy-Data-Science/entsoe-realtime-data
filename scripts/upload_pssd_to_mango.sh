#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${IRODS_PASSWORD:-}" ]]; then
  echo "IRODS_PASSWORD is not set in this terminal."
  echo "Run: read -s IRODS_PASSWORD && export IRODS_PASSWORD"
  exit 1
fi

LOCAL_BASE="/Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates"
REMOTE_BASE="${MANGO_REMOTE_BASE:-/set/home/Transparency_plus/ingress/updates}"
ENV_FILE="config/irods_environment.json"
MANIFEST="data/mango_upload_manifest.csv"
LIMIT_ARGS=""

if [[ -n "${UPLOAD_LIMIT:-}" ]]; then
  LIMIT_ARGS="--limit ${UPLOAD_LIMIT}"
fi

for country in Belgium Germany France Netherlands Denmark_DK1 Denmark_DK2; do
  echo
  echo "Uploading ${country} snapshots to Mango..."
  python scripts/upload_to_mango.py \
    --env-file "${ENV_FILE}" \
    --local-root "${LOCAL_BASE}/${country}" \
    --remote-root "${REMOTE_BASE}/${country}" \
    --manifest "${MANIFEST}" \
    --skip-uploaded-manifest "${MANIFEST}" \
    ${LIMIT_ARGS} \
    --include-existing-in-manifest
done

echo
echo "PSSD-to-Mango upload finished."
