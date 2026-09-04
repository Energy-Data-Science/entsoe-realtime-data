# ENTSO-E Real-Time Data Pipeline Developer Notes

This document explains the structure and code path for the ENTSO-E real-time
data collection project. It is intended for developers who will maintain the
GitHub repository, GitHub Actions workflows, Streamlit dashboard, and Mango
archiving process.

## Purpose

The project collects electricity load, renewable generation, and price data
from the ENTSO-E Transparency Platform. The operational collector stores a new
snapshot roughly every 15 minutes. Each snapshot is marked with the collection
time so that later analysis can compare what ENTSO-E published at different
moments.

The same repository also supports historical backfills and archive movement
from GitHub to Mango/iRODS when GitHub storage becomes too large.

## Branch And Data Model

The repository uses two main branches:

- `main`: source code, notebooks, configuration, dashboard code, and GitHub
  Actions workflows.
- `data`: generated data files and small status files used by the dashboard.

GitHub Actions normally checks out `main`, creates a separate worktree for the
`data` branch, fetches the latest ENTSO-E data, then commits only the generated
data outputs back to the `data` branch.

## Main Folders

| Path | Purpose |
| --- | --- |
| `.github/workflows/` | Scheduled and manual GitHub Actions workflows for collection and archiving. |
| `config/` | Runtime configuration files, including variable definitions and iRODS environment templates. Do not commit real secrets. |
| `dashboard/` | Streamlit dashboard code for monitoring recent collections and stored snapshots. |
| `data/raw/` | Historical year-level CSV files used for backfills and model context. This folder belongs mainly to the `data` branch. |
| `data/updates/` | Short-lived 15-minute snapshot CSV files. Older files are moved to PSSD/Mango. |
| `data/hourly/` | Durable hourly Parquet archive when the hourly archive extension is enabled. |
| `docs/` | Developer and deployment documentation. |
| `notebooks/` | Notebook-friendly examples for fetching and inspecting the data. |
| `scripts/` | Command-line entry points for fetching, scheduling, backfilling, archiving, and Mango upload. |
| `src/entsoe_realtime/` | Core Python package for configuration, ENTSO-E API calls, orchestration, and storage. |

## Core Python Files

| File | Responsibility |
| --- | --- |
| `src/entsoe_realtime/config.py` | Defines supported countries, variables, API settings, storage paths, retry settings, and environment-variable defaults. |
| `src/entsoe_realtime/client.py` | Wraps `entsoe-py` API calls, chunks time windows, retries transient ENTSO-E errors, and normalizes responses into a common table format. |
| `src/entsoe_realtime/jobs.py` | Main orchestration layer. It loops through countries and variables, fetches data, writes outputs, records warnings/errors, and updates dashboard status files. |
| `src/entsoe_realtime/storage.py` | Writes snapshot CSVs, historical CSVs, manifests, run history, status JSON, progress JSON, and optionally durable hourly Parquet files. |

## Main Scripts

| Script | When To Use |
| --- | --- |
| `scripts/fetch_once.py` | Runs one ENTSO-E refresh using the current environment settings. This is the main operational entry point. |
| `scripts/run_scheduler.py` | Runs a local recurring collector for testing outside GitHub Actions. |
| `scripts/backfill_historical.py` | Fetches historical data and stores merged year-level files in `data/raw/`. |
| `scripts/should_collect.py` | Checks `data/update_manifest.csv` and decides whether a new snapshot is due. |
| `scripts/plan_mango_archive.py` | Plans which snapshot files are older than the retention window and should be archived. |
| `scripts/archive_old_snapshots_to_mango.py` | Uploads archive candidates to Mango and prepares pruning from GitHub. |
| `scripts/upload_to_mango.py` | Generic iRODS/Mango uploader with manifest-based de-duplication. |
| `scripts/upload_pssd_to_mango.sh` | Convenience wrapper for uploading PSSD-staged country folders to Mango. |

## Data Source

The source is the ENTSO-E Transparency Platform API, accessed through
`EntsoePandasClient` from `entsoe-py`.

The API token must be supplied as:

- local development: `.env` with `ENTSOE_API_KEY=...`
- GitHub Actions: repository secret `ENTSOE_API_KEY`

Do not hard-code the API key in source files, notebooks, documentation, or
workflow YAML.

## Countries And Bidding Zones

Supported collection zones are configured in `COUNTRY_TO_ENTSOE_AREA` in
`src/entsoe_realtime/config.py`.

| Project code | ENTSO-E area code | Notes |
| --- | --- | --- |
| `BE` | `BE` | Belgium |
| `FR` | `FR` | France |
| `DE` | `DE_LU` | Germany/Luxembourg bidding zone |
| `NL` | `NL` | Netherlands |
| `DK1` | `DK_1` | Denmark bidding zone 1 |
| `DK2` | `DK_2` | Denmark bidding zone 2 |
| `ES` | `ES` | Spain |
| `PT` | `PT` | Portugal |

When adding another country, update the mapping in `config.py`, update any
workflow `ENTSOE_COUNTRIES` environment variables, and check the dashboard
country selector.

## Variables

Variables are defined in `VARIABLES` in `src/entsoe_realtime/config.py`.

| Variable | Start date | Unit | Fetcher | ENTSO-E PSR type |
| --- | --- | --- | --- | --- |
| `actual_load` | `2020-12-01` | MW | `query_load` | n/a |
| `forecast_load` | `2020-12-01` | MW | `query_load_forecast` | n/a |
| `actual_solar_generation` | `2022-01-01` | MW | `query_generation` | `B16` |
| `forecast_solar_generation` | `2022-01-01` | MW | `query_wind_and_solar_forecast` | `B16` |
| `actual_onshore_wind_generation` | `2022-01-01` | MW | `query_generation` | `B19` |
| `forecast_onshore_wind_generation` | `2022-01-01` | MW | `query_wind_and_solar_forecast` | `B19` |
| `actual_offshore_wind_generation` | `2022-01-01` | MW | `query_generation` | `B18` |
| `forecast_offshore_wind_generation` | `2022-01-01` | MW | `query_wind_and_solar_forecast` | `B18` |
| `day_ahead_price` | `2020-12-01` | EUR/MWh | `query_day_ahead_prices` | n/a |
| `imbalance_price` | `2020-12-01` | EUR/MWh | `query_imbalance_prices` | n/a |

For renewable variables, the PSR type is important:

- `B16`: solar
- `B18`: offshore wind
- `B19`: onshore wind

An `n/a` value in the ENTSO-E PSR type column does not mean the data is
unavailable. It means the variable does not require a generation technology
filter. Load and price data are fetched through their own ENTSO-E API methods:

| Variable type | Fetching method |
| --- | --- |
| actual load | `query_load(...)` |
| forecast load | `query_load_forecast(...)` |
| day-ahead price | `query_day_ahead_prices(...)` |
| imbalance price | `query_imbalance_prices(...)` |

Only generation variables need a PSR type because the request must specify
which generation technology is being queried.

Some country-variable combinations may have no published data. For example,
Spain offshore wind forecast has previously returned no ENTSO-E records. In
that case the run records a warning and no snapshot file is written.

## Fetch Modes

The collector has two main modes controlled by environment variables:

- `ENTSOE_FETCH_MODE=recent`: fetches only the latest window, usually 31 days.
- `ENTSOE_FETCH_MODE=full`: fetches from the configured variable start date.

For actual variables and imbalance prices, the query window ends at the current
collection time. For forecast variables and day-ahead prices, the collector
extends the query window beyond the collection time so every snapshot preserves
the TSO values available for future delivery timestamps. This forward delivery
window is controlled by `ENTSOE_FORECAST_HORIZON_HOURS`, with a default of 48
hours. In the stored data, `collection_time_utc` is the time we queried the API;
`timestamp_utc` is the delivery or forecast target time.

Storage mode is controlled separately:

- `ENTSOE_STORAGE_MODE=snapshot`: writes one timestamped snapshot per
  collection run.
- `ENTSOE_STORAGE_MODE=merge`: merges records into historical year CSV files.

The operational GitHub workflow uses recent snapshot mode. Historical backfill
uses full merge mode.

## GitHub Actions Frequency

`collect-entsoe.yml` is scheduled every 5 minutes, but it does not always fetch
new data. It first runs `scripts/should_collect.py`, which checks the newest
`collection_time_utc` in `data/update_manifest.csv`. A new collection is due
when the latest snapshot is at least `ENTSOE_MIN_COLLECTION_INTERVAL_MINUTES`
old. The current default is 13 minutes, giving an effective cadence close to
the ENTSO-E 15-minute publication interval.

`continuous-collector.yml` is a longer-running fallback collector. It wakes
every 5 minutes inside a 4-hour GitHub Actions job and uses the same
`should_collect.py` guard before fetching.

Country-level parallelism is controlled by `ENTSOE_COUNTRY_WORKERS`. A value of
`1` keeps the old sequential behavior; the GitHub workflows can set this to the
number of configured bidding zones to fetch countries in parallel while each
country still processes its variables sequentially.

`archive-to-mango.yml` archives data older than the configured retention window,
normally 14 days, from GitHub to Mango.

## Snapshot Storage Layout

Operational snapshots are stored as CSV files under:

```text
data/updates/{country}/{variable}/{collection_year}/{collection_month}/{collection_time_utc}.csv
```

Example:

```text
data/updates/BE/actual_load/2026/06/20260628T004314Z.csv
```

Each snapshot file includes:

| Column | Meaning |
| --- | --- |
| `collection_time_utc` | UTC time when the fetch run started. |
| `collection_time_local` | Same collection time in `ENTSOE_TIMEZONE`, usually Europe/Brussels. |
| `run_id` | UUID for the full collection run. |
| `timestamp_utc` | Data timestamp in UTC. |
| `timestamp_local` | Data timestamp in local timezone. |
| `value` | Numeric measurement or forecast value. |
| `unit` | MW or EUR/MWh. |
| `country` | Project country code such as `BE`, `FR`, or `DK1`. |
| `entsoe_area` | ENTSO-E bidding zone code used in the API request. |
| `variable` | Project variable name. |
| `source` | Source column or series name returned by ENTSO-E. |
| `updated_at_utc` | Time when the row was normalized by the collector. |

## Historical Storage Layout

Historical backfills are stored as merged year-level CSVs:

```text
data/raw/{country}/{variable}/{year}.csv
```

Example:

```text
data/raw/DE/actual_solar_generation/2025.csv
```

The historical files are useful for model training, backfilling, and long
lookback windows. They are not meant to be rewritten every 15 minutes.

## Durable Hourly Archive Extension

For long-term online forecasting, the recommended durable archive layout is:

```text
data/hourly/{zone}/{variable}/{year}.parquet
```

The intended merge rule is:

```text
same timestamp + same variable:
    keep newest collection_time_utc value
```

This makes `data/hourly/` the long-term truth and covariate archive for model
context, evaluation, and future backfills. When this extension is enabled in
the code and workflows, snapshot data can remain short-lived while the hourly
archive continues to grow cleanly across years.

## Manifest And Dashboard Files

The dashboard depends on several small data files committed to the `data`
branch:

| File | Purpose |
| --- | --- |
| `data/update_manifest.csv` | One row per snapshot file, including country, variable, row count, time window, collection time, and path. |
| `data/run_history.csv` | One row per country-variable fetch attempt with status, duration, warnings, and errors. |
| `data/status.json` | Latest run summary used by the dashboard top-level metrics. |
| `data/progress.json` | Current in-progress fetch item, useful while a run is still active. |
| `data/mango_upload_manifest.csv` | Local/archive manifest used to avoid re-uploading files already copied to Mango. |

## ENTSO-E Error Handling

ENTSO-E can intermittently return `429`, `500`, `502`, `503`, or `504` errors,
especially for large historical requests. The client handles this by:

1. chunking requests by `ENTSOE_CHUNK_DAYS`;
2. retrying transient failures up to `ENTSOE_MAX_RETRIES`;
3. splitting persistently failing windows down to `ENTSOE_MIN_CHUNK_HOURS`;
4. recording skipped transient windows as warnings instead of stopping the full
   country-variable run.

This behavior is implemented in `src/entsoe_realtime/client.py`.

## Mango And PSSD Archive

Recent snapshots live on GitHub only for a limited retention window. Older
snapshot data is staged on PSSD and/or uploaded to Mango.

Typical PSSD location:

```text
/Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates
```

Mango upload is handled through iRODS. The machine-account password must be
provided outside Git, usually as `IRODS_PASSWORD` locally or as a GitHub Actions
secret for self-hosted runner workflows.

Country folder names can differ between local project codes and archive folder
names:

| Project code | Archive folder |
| --- | --- |
| `BE` | `Belgium` |
| `FR` | `France` |
| `DE` | `Germany` |
| `NL` | `Netherlands` |
| `DK1` | `Denmark_DK1` |
| `DK2` | `Denmark_DK2` |
| `ES` | `Spain` |
| `PT` | `Portugal` |

The upload scripts compare local files with the upload manifest and remote
Mango object sizes so that already-uploaded data is skipped.

## Approximate Data Volume

The exact daily volume changes with ENTSO-E availability and the country list.
With 8 bidding zones and 10 configured variables, one successful collection run
can produce roughly 80 snapshot files. At a 15-minute cadence this is up to 96
runs per day, or about 7,500 snapshot files per day.

The row count is much larger because each snapshot contains the latest request
window, usually 31 days. Recent observed runs were on the order of tens of
millions of rows per day across all snapshot files. This is why the repository
keeps only recent snapshots on GitHub and archives older files to Mango.

## Adding A New Country

1. Add the project code and ENTSO-E area code in
   `src/entsoe_realtime/config.py`.
2. Add the country code to `ENTSOE_COUNTRIES` in the GitHub workflow YAML files.
3. Update dashboard country controls if they are hard-coded.
4. If using PSSD/Mango archiving, add the country to archive country maps and
   upload shell scripts.
5. Run a limited manual fetch first, then check `data/run_history.csv` for
   warnings or unavailable variables.

## Adding A New Variable

1. Add a `VariableSpec` in `src/entsoe_realtime/config.py`.
2. Implement the matching fetcher branch in
   `src/entsoe_realtime/client.py::_fetch_chunk`.
3. Confirm the normalized output keeps the standard columns.
4. Add dashboard labels or filters if needed.
5. Run a manual `scripts/fetch_once.py` test before enabling it in scheduled
   workflows.

## Developer Maintenance Checklist

- Keep secrets in `.env`, GitHub Secrets, or local terminal environment
  variables only.
- Check GitHub Actions logs for ENTSO-E warnings before assuming missing files
  are a storage bug.
- Keep `data/update_manifest.csv`, `data/status.json`, and `data/run_history.csv`
  small enough for dashboard loading.
- Move old `data/updates/` snapshots to Mango before GitHub repository size
  becomes unstable.
- Verify country codes in both ENTSO-E API settings and dashboard controls after
  adding new zones.
- Prefer Parquet for the durable hourly archive when downstream forecasting
  needs fast reads and multi-year context.
