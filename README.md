# ENTSO-E Real-Time Data Collector

Reusable notebooks, fetch jobs, CSV storage, and a dashboard for monitoring ENTSO-E data updates for Belgium, France, and Germany.

The project currently collects:

- Actual electricity load
- Day-ahead forecasted electricity load
- Actual solar generation
- Forecasted solar generation
- Actual onshore wind generation
- Forecasted onshore wind generation
- Actual offshore wind generation
- Forecasted offshore wind generation
- Day-ahead electricity prices
- Imbalance electricity prices

## Countries

The public country labels are `BE`, `FR`, and `DE`. Internally, `DE` is mapped to the current ENTSO-E bidding-zone code `DE_LU`, which is the standard Germany/Luxembourg bidding zone used by `entsoe-py`.

## Data Windows

- Load and price data start at `2020-12-01`.
- Renewable generation data start at `2022-01-01`.
- The historical backfill is kept in `data/raw`.
- Operational updates fetch only the latest 31 days.
- Every update run ends at the latest available timestamp at runtime.
- The scheduler runs every 15 minutes and stores a new timestamped snapshot.

The fetch cadence is 15 minutes, but the saved data preserve ENTSO-E's native resolution. Many ENTSO-E series are hourly, 30-minute, 15-minute, or irregular depending on market area and data item.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For notebook editing, install Jupyter locally as an extra:

```bash
pip install jupyter ipykernel
```

The local `.env` file already contains the API key you provided. It is ignored by Git. For another machine, copy `.env.example` to `.env` and set `ENTSOE_API_KEY`.

## Fetch One Operational Snapshot

```bash
python scripts/fetch_once.py
```

By default this fetches the latest 31 days and writes immutable collection
snapshots to `data/updates`. It does not merge into the historical backfill files.

For full historical backfills, ENTSO-E may return temporary `503 Service Unavailable`
responses for specific windows. The collector retries those windows and then splits
them into smaller chunks when needed. These controls live in `.env`:

```bash
ENTSOE_MAX_RETRIES=3
ENTSOE_RETRY_SLEEP_SECONDS=5.0
ENTSOE_MIN_CHUNK_HOURS=24
ENTSOE_REQUEST_TIMEOUT_SECONDS=60
```

## Run Every 15 Minutes

```bash
python scripts/run_scheduler.py
```

For production on GitHub, use the included workflow:

```text
.github/workflows/collect-entsoe.yml
```

It runs every 15 minutes, fetches the latest 31 days, writes timestamped
snapshot CSVs, updates the manifest, and commits the new data back to GitHub.
See [docs/deployment.md](docs/deployment.md).

If the organization keeps the default `GITHUB_TOKEN` read-only, add a
`DATA_PUSH_TOKEN` repository secret with repository contents read/write access.

## Dashboard

```bash
streamlit run dashboard/app.py
```

The dashboard shows the latest collection, failures, record counts, snapshot
coverage, and preview charts. In public deployment it can run without the API
key and read only the committed GitHub data.

## Upload Snapshots To Mango

For larger transfers, use the Python iRODS upload helper instead of the Mango
web upload button. It walks a local folder, recreates the same structure in
Mango, skips files that already exist with the same size, and records upload
results in `data/mango_upload_manifest.csv`.

Install the Mango/iRODS dependency:

```bash
pip install -r requirements.txt
```

Set up an `irods_environment.json` file from Mango, then pass it with
`--env-file`. If the file is stored at `~/.irods/irods_environment.json`, the
script will find it automatically.

For recurring uploads, prefer the Mango project machine account, usually the
project `<name>_ingress` account. For machine-account uploads, set
`IRODS_PASSWORD` to the retrieved machine-account password/token. For personal
interactive uploads only, add `--authenticate` to start Mango's browser-based
authentication flow through `mango_auth`.

First run a small dry run:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --local-root /Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates/FR \
  --remote-root /set/home/Transparency_plus/ingress/entsoe-realtime-data-archive/data-branch-work/data/updates/France \
  --limit 10 \
  --dry-run
```

Then upload one country:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --local-root /Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates/FR \
  --remote-root /set/home/Transparency_plus/ingress/entsoe-realtime-data-archive/data-branch-work/data/updates/France
```

After that, upload the full `updates` tree:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --local-root /Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates \
  --remote-root /set/home/Transparency_plus/ingress/entsoe-realtime-data-archive/data-branch-work/data/updates
```

Optional metadata can be attached to uploaded data objects:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --metadata project=entsoe-realtime-data \
  --metadata source=ENTSO-E
```

## Daily Mango Archive Workflow

The workflow `.github/workflows/archive-to-mango.yml` archives old operational
snapshot files from the GitHub `data` branch to Mango. The default policy is:

- keep the latest 14 days on GitHub
- move older snapshot CSV files to Mango
- map country folders as `BE -> Belgium`, `FR -> France`, and `DE -> Germany`
- prune files from GitHub only after upload and size verification succeed
- append archive results to `data/mango_upload_manifest.csv`

It is scheduled with GitHub cron:

```text
0 2 * * *
```

GitHub cron is UTC. This is 04:00 in Brussels during summer time and 03:00 in
Brussels during winter time. If exact 04:00 Brussels time is required all year,
adjust the cron seasonally.

For GitHub Actions automation, configure these repository secrets:

```text
DATA_PUSH_TOKEN
MANGO_IRODS_ENVIRONMENT_JSON
MANGO_IRODS_PASSWORD
```

`MANGO_IRODS_ENVIRONMENT_JSON` should contain the Mango
`irods_environment.json` content for `Transparency_plus_ingress`.
`MANGO_IRODS_PASSWORD` should contain the corresponding machine-account
password/token. Personal KU Leuven authenticator login is suitable for local
uploads, but not for unattended GitHub Actions.

## CSV Layout

Historical CSV files are kept by country, variable, and year:

```text
data/raw/
  BE/
    actual_load/
      2020.csv
      2021.csv
    day_ahead_price/
      2020.csv
  FR/
  DE/
```

Operational update snapshots are written by country, variable, collection year,
collection month, and collection timestamp:

```text
data/updates/
  BE/
    actual_load/
      2026/
        05/
          20260520T081500Z.csv
```

Each update CSV includes the collection time:

```text
collection_time_utc,collection_time_local,run_id,timestamp_utc,timestamp_local,value,unit,country,entsoe_area,variable,source,updated_at_utc
```

For multi-column ENTSO-E responses, the column name is stored in `source`.

`data/update_manifest.csv` stores one lightweight metadata row per snapshot file
so the dashboard can stay fast even when many large update CSVs exist.

## Notebooks

- `notebooks/01_backfill_entsoe_data.ipynb`: readable backfill workflow.
- `notebooks/02_dashboard_data_checks.ipynb`: quick checks for stored CSVs.

The notebooks call the same reusable code as the scheduler, so exploratory work and production refreshes stay aligned.
