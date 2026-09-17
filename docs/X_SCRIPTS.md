# X scripts

This document explains the scripts used to collect posts from the X accounts of the target politicians with the official X API v2.

The accounts are defined in `config/x_accounts.json` and linked to `config/politicians.json` through `actor_id`. Collection settings are in `config/x_collection.json`.

## Access and costs

The X API is pay-per-use (prepaid credits); there is no free or academic read tier. Prices when this document was written:

| Operation | Price |
| --- | --- |
| Post read | $0.005 per post |
| User read | $0.010 per user |
| Full-archive counts | $0.010 per request |

The same resource requested again within the same UTC day is not charged again. Pay-per-use accounts are capped at 3 million post reads per month, and a spending limit can be set in the developer console. Check [the current pricing](https://docs.x.com/x-api/getting-started/pricing) before large runs.

Full-archive search (`GET /2/tweets/search/all`) is available on pay-per-use: 500 posts per page, 300 requests per 15 minutes, 1 request per second. The user timeline endpoint is not used because it only returns the 3,200 most recent posts.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `X_BEARER_TOKEN` in `.env`. Only the bearer token is needed for public data. `.env` must never be committed.

In the developer console (console.x.com):

1. The app must be attached to a Project in the **Production** environment.
2. The Project must be on **Pay-Per-Use** with some credits.
3. Set a spending limit per billing cycle.
4. After changing the plan, regenerate the bearer token and update `.env`.

## 1. Check access

`src/check_x_access.py` checks whether the credentials can read public data. By default it makes one user-lookup request for the configured accounts, which costs $0.010 per account when it succeeds and nothing when it is rejected.

```bash
python src/check_x_access.py
```

Useful options:

* `--write-ids`: store the resolved user IDs in `config/x_accounts.json`.
* `--probe-archive`: also make one full-archive counts request for the first account ($0.010), covering the last 30 days.
* `--env-file PATH`: read credentials from another file.

The script prints the HTTP status, the access status, rate-limit headers and error details, with credentials redacted. It returns exit code 0 only when every request succeeds.

| Access status | Meaning | Action |
| --- | --- | --- |
| `ok` | Reads work | Run with `--write-ids`, then `--probe-archive` |
| `invalid-credentials` | Bearer token rejected | Regenerate the token and update `.env` |
| `not-enrolled` | App not enabled for v2 reads (`client-not-enrolled`) | Attach the app to a Project, switch it to Pay-Per-Use, add credits, regenerate the token |
| `no-credits` | Enrolled but cannot pay | Add credits or raise the spending limit |
| `usage-capped` | Monthly cap or spending limit reached | Wait for the next cycle or raise the limit |
| `rate-limited` | Too many requests | Wait for `x-rate-limit-reset` |

## 2. Collect posts (planned)

`src/collect_x.py` will:

1. read `config/x_accounts.json` and `config/x_collection.json`;
2. request monthly counts (`/2/tweets/counts/all`) per actor and print the expected cost before collecting;
3. run full-archive search month by month with `from:<x_user_id> -is:retweet` and the fields in `x_collection.json`, including `note_tweet`, without which long posts are truncated;
4. store each response page untouched in `data/raw/x/<actor_id>/<YYYY-MM>.jsonl` and log every request in `data/raw/x/manifest.jsonl`;
5. skip months that are already complete, so interrupted runs can resume;
6. compare collected totals with the counts and report gaps.

A later script will build `data/processed/x_posts_internal.csv` and `data/processed/x_stimuli.csv` with the columns in `docs/DATA_DICTIONARY.md`.

Replies and quote posts are collected and marked with `post_type`; whether they enter the stimuli is decided at preprocessing. Expanding referenced posts (`expand_referenced_posts`) is off by default because each expanded post may be billed as an extra read.

## Data handling

* Deleted or protected posts are not returned by search. Record the collection date and treat the corpus as the set of posts public at that date.
* Raw responses and processed tables contain full text and are excluded from version control.
* Only post IDs and derived labels may be shared, following the X developer terms and the project's data-management plan.
