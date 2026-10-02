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

## 2. Collect posts

`src/collect_x.py` counts first and downloads afterwards, so the cost is known before anything is collected.

```bash
python src/collect_x.py --counts-only   # how many posts and what it would cost
python src/collect_x.py                 # counts, asks for confirmation, then downloads
```

The script reads `config/x_accounts.json` and `config/x_collection.json` and, for each actor and month:

1. requests full-archive counts for `from:<x_user_id> -is:retweet` ($0.010 per request) and prints the totals per actor and the expected download cost;
2. after confirmation, runs full-archive search with the fields in `x_collection.json`, including `note_tweet`, without which long posts are truncated;
3. writes each response page untouched to `data/raw/x/<actor_id>/<YYYY-MM>.jsonl`;
4. logs every request in `data/raw/x/manifest.jsonl` and marks each finished month, so an interrupted run resumes where it stopped;
5. reports any month whose collected total differs from its count.

Useful options:

* `--counts-only`: count and estimate, download nothing.
* `--skip-counts`: download straight away, without counting first. Saves $0.010 per month on repeat runs, for example the weekly updates, at the price of having no estimate beforehand and no check of collected totals against counts. Combine it with `--max-cost`.
* `--yes`: skip the confirmation, for scheduled runs.
* `--max-cost 50`: stop before the estimate, or the running cost of the download, goes above that many dollars. A month interrupted this way is not marked complete and is collected again on the next run, so its pages are paid for twice unless the rerun happens on the same UTC day.
* `--actor pedro_sanchez`: one actor; repeatable.
* `--start 2023-08-17` / `--end 2024-01-01`: override the configured window.
* `--force`: collect months that are already marked complete again (they are charged again unless collected the same UTC day).

Retweets are excluded by the query, so they are never downloaded or paid for. Replies and quote posts are collected and marked with `post_type`. Since `expand_referenced_posts` is enabled, the quoted or replied-to post is requested alongside, so short posts such as "Vergonzoso." keep their context; each referenced post is an extra read.

## 3. Build the tables

```bash
python src/build_x_tables.py
```

Reads the raw pages, removes duplicates and writes, with the columns in [DATA_DICTIONARY.md](DATA_DICTIONARY.md):

* `data/processed/x_posts_internal.csv`: full provenance, including the text of referenced posts;
* `data/processed/x_stimuli.csv`: same columns as the Congreso stimuli table;
* `data/processed/x_build_summary.json`: counts by actor and post type, duplicates, date range, and references whose text is missing.

`text_anon` holds the original text at this stage; anonymization is applied in a later preprocessing step, which also decides whether referenced-post context reaches the prompts and sets `keep_for_prompt`.

The results of the first full collection, including its quality control, are in [X_RESULTS.md](X_RESULTS.md).

## Data handling

* Deleted or protected posts are not returned by search. Record the collection date and treat the corpus as the set of posts public at that date. In the first run, 8 of 10,871 counted posts (0.07%) had been deleted between counting and downloading.
* Stored pages are read by splitting on newlines only, because post text can contain U+2028, which Python's `str.splitlines()` would treat as a line break.
* Raw responses and processed tables contain full text and are excluded from version control.
* Only post IDs and derived labels may be shared, following the X developer terms and the project's data-management plan.
