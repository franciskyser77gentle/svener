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

Ranking is descending **emission**, the most meaningful current reward measure
for miners. If an API response does not provide emission, the collector falls
back to incentive, then alpha stake. The ranking rule is stored in
`latest.json` so it is visible to the dashboard.

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
