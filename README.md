# sven28M

Personal daily monitor for Bittensor subnet 28 (netuid 28). At 00:15 UTC, GitHub
Actions reads the TaoStats neuron API, ranks the top 30, and commits an append-only
snapshot to this repository. The static dashboard is in `docs/`.

## What is recorded

Each row in `data/snapshots.csv` contains the UTC timestamp, rank, UID, coldkey,
hotkey, alpha stake, TAO balance, incentive, emission, and changes from the
previous observed snapshot. `data/latest.json` drives the ranking page and
`data/stats.json` retains history and aggregates for every UID that has ever
entered the top 30.

Token amounts are displayed in token units, not raw chain units: TaoStats
documents `alpha_stake`, `root_stake`, and `emission` as RAO decimal strings,
so the collector divides them by 1,000,000,000. `tao_balance` is populated
from the metagraph row's documented `root_stake` (the neuron's TAO stake);
TaoStats does not expose a coldkey free-wallet balance on this endpoint.
Incentive is already a normalized 0–1 score and is not divided. The initial
raw sample snapshot has been migrated; legacy rows that had no root-stake
field remain null rather than inventing a balance.

Ranking is **miners only**, matching TaoStats' Miners tab: a row is a miner
when `validator_trust == 0`; when that field is missing, the collector falls
back to `dividends == 0`. This deliberately keeps UID 157 even when its
`validator_permit` is true, because TaoStats' Miners tab shows it with VTrust
0. Owner identity, `is_owner_hotkey`, child-key status, coldkey, and stake size
are not exclusions. Results are sorted by descending emission and then
incentive. The collector fetches additional metagraph pages before applying
the filter, so top-30 means 30 eligible miners.
The exact role is configured as `role: miners` in `config.yaml` and the rule is
also stored in `latest.json`.

TaoStats documents `emission` as a per-tempo emission amount, not a daily
total; the dashboard labels it `Emission / tempo`. The separate
`daily_mining_alpha_as_tao` field is not used for ranking.

No `TAOSTATS_API_KEY` was available during this migration, so no new
authenticated three-UID API comparison could honestly be performed. The
conversion was checked against the prior committed raw values for UIDs 3, 154,
and 72: dividing alpha/emission by 1e9 matches the migrated token values.
TaoStats documentation confirms `root_stake` is the available TAO stake field.
The next authenticated run applies the same validator-permit rule to regenerate
the data; a zero-miner result fails without writing files.

## Setup (beginner friendly)

1. Create or fork this repository. In **Settings > Secrets and variables >
   Actions**, add a repository secret named `TAOSTATS_API_KEY`. Never put the
   key in a file.
2. Optional Google Sheets sync: create a Google service account, enable the
   Sheets API, copy its JSON into the `GOOGLE_SERVICE_ACCOUNT_JSON` secret, and
   share spreadsheet
   `1DVRl_AuAYm4uX9VLbEL5gcTSF6Fus46Fuzo5rdDj6Wo` with the service-account email
   as Editor. The tabs `Latest`, `History`, and `UID Stats` are created/updated.
   If the secret is absent, the job logs a clean skip.
3. In **Settings > Pages**, choose **GitHub Actions** as the source. The Pages
   workflow will publish `docs/`.
4. Open **Actions > Daily subnet 28 snapshot**, click **Run workflow**, and
   trigger the first run. The scheduled run then happens every day.

### Dashboard settings

Open the gear icon on the dashboard or visit `docs/settings.html`. Create a
GitHub fine-grained token scoped to this repository with exactly these
repository permissions: **Contents: Read and write**, **Secrets: Read and
write**, and **Actions: Read and write**. The page uses the token only for
`api.github.com`: it encrypts the TaoStats key in the browser before updating
the Actions secret, commits `config.yaml`, and dispatches the daily workflow.
It never displays, logs, or stores the TaoStats key.

The GitHub token itself is stored in `sessionStorage` by default, or
`localStorage` when “Remember on this device” is checked. This is convenient
but means any JavaScript running on this origin could read a remembered token;
use a short expiration, repository-only scope, and forget the token when done.

Edit `config.yaml` to change `top_n` or add favorite UIDs (one `- 123` per
line). Favorites are retained in the aggregate data and highlighted when they
appear in the ranking.

## API, limits, and backfill

The collector uses the current documented endpoint
`https://api.taostats.io/v1/subnets/metagraph?netuid=28&limit=256` and orders by
emission descending. The API key is sent exactly as the docs specify:
`Authorization: <key>` (no `Bearer` prefix). It also sends a normal User-Agent
and `Accept: application/json`. `TAOSTATS_NEURONS_ENDPOINT` and
`TAOSTATS_BASE_URL` can override this if TaoStats changes its endpoint.
Requests have a timeout, exponential backoff for 429/5xx, and include a
truncated, key-redacted provider response body in failures. The exact quota
depends on the TaoStats free plan and can change; one normal daily run makes
one paginated request, so it stays well below typical free quotas. Manual
`workflow_dispatch` has a `backfill_days` input; those requests use the
documented `/v1/subnets/metagraph/history` endpoint with
`timestamp_start`/`timestamp_end` Unix-second filters, rather than adding an
unsupported parameter to the live endpoint. If the plan cannot serve history,
the run fails clearly without overwriting existing data.

## Development

```bash
python -m pip install -e '.[test]'
pytest -q
python -m sven28m_monitor.collector
```

Tests use mocked normalized API data and do not require a TaoStats key. Sample
data is included so the dashboard renders immediately. The collector is
deliberately dependency-light; Google sync dependencies are listed in
`requirements.txt` and are only needed for that optional feature.
