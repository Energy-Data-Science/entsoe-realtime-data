# Deployment

This project is ready for three deployment pieces:

1. GitHub repository under `Energy-Data-Science`.
2. GitHub Actions scheduled collection every 15 minutes.
3. Public Streamlit dashboard reading the committed snapshot data.

## 1. Create The GitHub Repository

Create a repository in the organization:

```text
https://github.com/Energy-Data-Science/entsoe-realtime-data
```

Then push this local project:

```bash
git remote add origin git@github.com:Energy-Data-Science/entsoe-realtime-data.git
git branch -M main
git add .
git commit -m "Initial ENTSO-E real-time data collector"
git push -u origin main
```

Use HTTPS instead of SSH if that is how your GitHub account is configured:

```bash
git remote add origin https://github.com/Energy-Data-Science/entsoe-realtime-data.git
```

## 2. Add GitHub Secrets

In the GitHub repository, open:

```text
Settings > Secrets and variables > Actions > New repository secret
```

Add:

```text
ENTSOE_API_KEY
```

Use the ENTSO-E token as the value. Do not commit `.env`.

If the organization prevents repositories from enabling write permissions for
the default GitHub Actions token, also add a repository secret:

```text
DATA_PUSH_TOKEN
```

Create this as a GitHub fine-grained personal access token with access to this
repository and `Contents: Read and write`. The workflow uses this token only for
checking out the repository and pushing newly collected CSV snapshots.

## 3. Enable Scheduled Collection

The workflow is:

```text
.github/workflows/collect-entsoe.yml
```

It can be started manually from the GitHub Actions tab with `Run workflow`.
It also runs on this schedule:

```text
7,22,37,52 * * * *
```

GitHub schedules are interpreted in UTC. The workflow still runs every 15
minutes, but uses offset minutes rather than `0,15,30,45` to reduce the chance
of GitHub scheduler delays or dropped jobs during common high-load times. The
workflow fetches the latest 31 days, writes timestamped CSV snapshots under
`data/updates`, updates `data/update_manifest.csv`, then commits those changes
back to the repository.

## 4. Publish The Dashboard

Recommended deployment: Streamlit Community Cloud.

Create a new app from the GitHub repository with:

```text
Repository: Energy-Data-Science/entsoe-realtime-data
Branch: main
Main file path: dashboard/app.py
```

The public dashboard does not need the ENTSO-E API key. It reads committed CSV
snapshots and the manifest from the repository.

If you later want the public dashboard to allow manual fetches, add
`ENTSOE_API_KEY` as a Streamlit app secret. For a public app, keeping the
dashboard read-only is safer.

## 5. Data Location

Operational snapshots:

```text
data/updates/{country}/{variable}/{year}/{month}/{collection_time_utc}.csv
```

Manifest:

```text
data/update_manifest.csv
```

Latest run state:

```text
data/status.json
data/progress.json
data/run_history.csv
```

Historical backfill files in `data/raw` remain ignored by Git by default because
they are large and already collected locally.

## Notes

Committing CSV snapshots to GitHub is convenient for transparency and a public
dashboard, but repository size will grow over time. If the repository becomes
too large, move snapshots to object storage or Git LFS and keep only a compact
manifest in Git.
