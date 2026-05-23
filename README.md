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
