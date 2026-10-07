# ENTSO-E Real-Time Data Collector

Reusable notebooks, fetch jobs, CSV storage, and a dashboard for monitoring ENTSO-E data updates for Belgium, France, Germany, the Netherlands, Denmark, Spain, and Portugal.

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

The public country labels are `BE`, `FR`, `DE`, `NL`, `DK1`, `DK2`, `ES`, and `PT`. Internally, `DE` is mapped to the current ENTSO-E bidding-zone code `DE_LU`, while Denmark is collected as the two ENTSO-E bidding zones `DK_1` and `DK_2`. Germany imbalance prices are a special case: ENTSO-E uses `DE_LU` before `2022-06-22` and `DE` from `2022-06-22` onward.

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

## Upload Snapshots To ManGO

The transfer uses the Python iRODS client because ManGO is backed by iRODS.
The uploader recreates the local directory structure, skips matching remote
objects, verifies uploaded sizes, and records results in
`data/mango_upload_manifest.csv`.

### Network and runner requirements

The automated job does **not** run on a GitHub-hosted computer. The workflow is
assigned to a self-hosted runner with these labels:

```yaml
runs-on: [self-hosted, macOS, X64, mango]
```

Consequently, all of the following must remain true for the job to run:

1. The runner computer is powered on and awake.
2. The GitHub Actions runner process or service is running.
3. The runner has Internet access to GitHub.
4. The runner can resolve and connect to `set.irods.icts.kuleuven.be:1247`.
5. When required by the runner's location and network policy, the runner is
   connected to the KU Leuven network or KU Leuven VPN.

If the runner is a personal Mac outside the KU Leuven network, this means that
the Mac and its KU Leuven VPN connection must stay active for the entire job.
Closing the laptop or disconnecting VPN can interrupt an upload. A self-hosted
runner installed on an always-on ESAT server is preferable for scheduled jobs;
in that case the laptop and laptop VPN are not part of the data path.

KU Leuven references:

- [KU Leuven network FAQ: SSL VPN provides access to internal KU Leuven servers](https://admin.kuleuven.be/icts/english/servicedesk/FAQs-EN/FAQ-network)
- [KU Leuven MFA login before starting VPN](https://admin.kuleuven.be/icts/servicepunt/lokaleondersteuning/temp/mfaloginvpn)
- [KU Leuven network facilities: VPN and SSH jump-server access](https://admin.kuleuven.be/icts/services/netwerkfaciliteiten)

GitHub reference:

- [Adding self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)

### Step 1: establish the KU Leuven network path

When working off campus, connect the runner computer to the KU Leuven VPN
before starting an upload:

1. Visit [vpn.kuleuven.be](https://vpn.kuleuven.be/).
2. Authenticate with the KU Leuven account used for VPN access.
3. Start the VPN connection with the ICTS-supported client and the VPN zone
   assigned by ICTS or the local IT administrator.
4. Complete the additional MFA confirmation when requested.
5. Confirm that the iRODS endpoint is reachable:

```bash
nc -vz set.irods.icts.kuleuven.be 1247
```

A successful TCP test only confirms network reachability; ManGO credentials
are still required. If the test fails, verify VPN status before investigating
the Python uploader.

### Step 2: obtain the ManGO ingress machine account

Use a machine account for unattended uploads. According to the official
[ManGO workflow automation documentation](https://rdm-docs.icts.kuleuven.be/mango/features/automation.html),
`<project>_ingress` is intended for writes and `<project>_egress` for reads.
This project uploads with:

```text
Transparency_plus_ingress
```

1. Log in to the [ManGO portal](https://mango.kuleuven.be/).
2. Open the `Transparency_plus` project page.
3. Find the machine-account section at the bottom of the project page.
4. Activate `Transparency_plus_ingress` if it is not active.
5. Select **Retrieve API token**.
6. Save the displayed token in a password manager. Retrieving a new token
   invalidates the previous token.
7. Save the displayed iRODS environment JSON as
   `config/irods_environment.json`, or obtain the connection settings from the
   ManGO **How to connect** page.

Do not commit the token, `.irodsA`, `.env`, or any generated credentials. The
environment JSON contains connection settings and the account name; the token
must remain in a secret store.

Official references:

- [ManGO workflow automation and machine accounts](https://rdm-docs.icts.kuleuven.be/mango/features/automation.html)
- [ManGO user access and the How to connect page](https://rdm-docs.icts.kuleuven.be/mango/basics/user_access.html)
- [Python-iRODSClient authentication and sessions](https://rdm-docs.icts.kuleuven.be/mango/clients/python_client.html)

### Step 3: install and test the Python client locally

Create an isolated environment and install the repository dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Load the machine token without placing it directly in shell history:

```bash
read -s "IRODS_PASSWORD?ManGO ingress token: "
export IRODS_PASSWORD
echo
```

Test authentication and read access to the ingress collection:

```bash
python - <<'PY'
import json
import os
from irods.session import iRODSSession

with open("config/irods_environment.json", encoding="utf-8") as source:
    settings = json.load(source)

with iRODSSession(**settings, password=os.environ["IRODS_PASSWORD"]) as session:
    collection = session.collections.get(
        "/set/home/Transparency_plus/ingress"
    )
    print("Connected to", collection.path)
PY
```

For a personal interactive session instead of a machine account, follow the
official [Python client authentication instructions](https://rdm-docs.icts.kuleuven.be/mango/clients/python_client.html)
and invoke the uploader with `--authenticate`. Personal authentication expires
and is therefore unsuitable for a scheduled runner.

### Step 4: run a manual dry run and small upload

First inspect ten planned files without contacting ManGO or changing data:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --local-root /Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates/France \
  --remote-root /set/home/Transparency_plus/ingress/updates/France \
  --limit 10 \
  --dry-run
```

Then upload and verify a small batch:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --local-root /Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates/France \
  --remote-root /set/home/Transparency_plus/ingress/updates/France \
  --limit 10 \
  --metadata project=entsoe-realtime-data \
  --metadata source=ENTSO-E
```

Inspect `data/mango_upload_manifest.csv` and the destination in the ManGO
portal before attempting the complete tree. To upload the complete tree:

```bash
python scripts/upload_to_mango.py \
  --env-file config/irods_environment.json \
  --local-root /Volumes/PSSD/1_entsoe-realtime-data-archive/data-branch-work/data/updates \
  --remote-root /set/home/Transparency_plus/ingress/updates \
  --metadata project=entsoe-realtime-data \
  --metadata source=ENTSO-E
```

### Step 5: configure the self-hosted GitHub Actions runner

In the GitHub repository:

1. Open **Settings > Actions > Runners**.
2. Select **New self-hosted runner** and choose macOS with the runner's
   architecture.
3. Run the installation and registration commands generated by GitHub on the
   computer that will perform the upload.
4. Add the custom `mango` label to that runner.
5. Install the runner as a background service if it must survive logout or
   reboot, following the commands shown by GitHub for the installed runner.
6. Verify that the runner is shown as **Idle** before triggering the workflow.

GitHub's registration token and commands are generated for the repository and
expire; use the current commands from the repository settings rather than
copying an old registration command from another machine.

### Step 6: configure GitHub secrets

Open **Settings > Secrets and variables > Actions** and create:

```text
DATA_PUSH_TOKEN
MANGO_IRODS_ENVIRONMENT_JSON
MANGO_IRODS_PASSWORD
```

- `DATA_PUSH_TOKEN` allows the workflow to update the `data` branch.
- `MANGO_IRODS_ENVIRONMENT_JSON` contains the complete JSON object for
  `Transparency_plus_ingress`, not a path to a file.
- `MANGO_IRODS_PASSWORD` contains the ingress machine-account token.

References:

- [GitHub: using secrets in Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions)
- [GitHub: managing personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

### Step 7: test the GitHub workflow

1. Confirm that the runner is **Idle** and its KU Leuven network path is active.
2. Open **Actions > Archive old ENTSO-E snapshots to Mango**.
3. Select **Run workflow**.
4. First use `retention_days=14`, `limit=10`, and `dry_run=true`.
5. Inspect the planner output. A dry run does not test ManGO authentication.
6. Run again with `limit=10` and `dry_run=false` to test the complete upload,
   verification, manifest, pruning, commit, and push sequence.
7. Confirm the objects in ManGO, inspect `data/mango_upload_manifest.csv`, and
   verify that the workflow completed before removing the limit.

If a workflow remains **Queued / Waiting for a runner**, the self-hosted runner
is offline or its labels do not match. If the preflight step cannot reach
`set.irods.icts.kuleuven.be:1247`, restore the KU Leuven network or VPN path.
An authentication error indicates an invalidated token or mismatched
environment JSON rather than a VPN problem.

## Daily ManGO Archive Workflow

The workflow `.github/workflows/archive-to-mango.yml` archives old operational
snapshot files from the GitHub `data` branch to Mango. The default policy is:

- keep the latest 14 days on GitHub
- move older snapshot CSV files to Mango
- map country folders as `BE -> Belgium`, `FR -> France`, `DE -> Germany`, `NL -> Netherlands`, `DK1 -> Denmark_DK1`, `DK2 -> Denmark_DK2`, `ES -> Spain`, and `PT -> Portugal`
- prune files from GitHub only after upload and size verification succeed
- append archive results to `data/mango_upload_manifest.csv`

It is scheduled with GitHub cron:

```text
0 2 * * *
```

GitHub cron is UTC. This is 04:00 in Brussels during summer time and 03:00 in
Brussels during winter time. If exact 04:00 Brussels time is required all year,
adjust the cron seasonally.

The schedule is only effective while a matching self-hosted runner is online.
GitHub will queue a scheduled job when the runner, computer, or required
network path is unavailable. For reliable daily operation, run the worker on
an always-on ESAT-managed machine or monitor the personal runner, power, sleep,
VPN, and runner-service state.

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
  NL/
  DK1/
  DK2/
  ES/
  PT/
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

Each successful collection also publishes a compact 90-day dashboard artifact:

```text
data/dashboard/actuals_recent.parquet
data/dashboard/actuals_status.json
```

The Parquet file contains hourly actuals for load, solar, onshore wind, and
offshore wind in BE, FR, and DE. The status file records the generation time and
the latest available timestamp for every zone/target pair. These files let
latency-sensitive dashboards retrieve all comparison actuals with one small
download instead of reading each annual archive independently.

## Notebooks

- `notebooks/01_backfill_entsoe_data.ipynb`: readable backfill workflow.
- `notebooks/02_dashboard_data_checks.ipynb`: quick checks for stored CSVs.

The notebooks call the same reusable code as the scheduler, so exploratory work and production refreshes stay aligned.
