# X collection results

This document reports the first full collection, as produced by
`src/collect_x.py` and `src/build_x_tables.py`.

## Run

| Field | Value |
| --- | --- |
| Query | `from:<x_user_id> -is:retweet` (retweets excluded) |
| Window | 2022-04-01 to 2026-10-02, in 165 monthly slices |
| Endpoints | full-archive counts and full-archive search, with `referenced_tweets.id` expansions |
| Collected at (UTC) | 2026-10-02 |
| Cost | $72.65 ($1.65 counts, 11,028 post reads, 3,171 referenced-post reads) |
| Raw data | 20 MB in `data/raw/x/<actor_id>/<YYYY-MM>.jsonl` |

## Posts by actor

| Actor ID | Counted | Collected (unique) | Share |
| --- | ---: | ---: | ---: |
| `pedro_sanchez` | 3,903 | 3,902 | 99.97% |
| `alberto_nunez_feijoo` | 3,518 | 3,515 | 99.91% |
| `santiago_abascal` | 3,450 | 3,446 | 99.88% |
| **Total** | **10,871** | **10,863** | **99.93%** |

Their counters, which include retweets, stood at 35,970, 12,871 and 49,715
posts respectively, so most of their activity is retweeting and is not part
of this corpus.

## Posts by type

| Type | Posts |
| --- | ---: |
| Original | 7,625 |
| Quote | 2,532 |
| Reply | 706 |

3,032 referenced posts (the quoted or replied-to post) were stored with their
text. 36 references have no stored text, usually because the referenced post
is no longer available.

## Quality control

* 158 of the 165 monthly slices collected exactly as many posts as the counts
  endpoint reported.
* Seven slices are one post short, eight posts in total (0.07%): `pedro_sanchez`
  2026-09, `alberto_nunez_feijoo` 2025-08, 2026-08 and 2026-09,
  `santiago_abascal` 2023-10, 2024-05 and 2025-11 (two short). Posts deleted
  between counting and downloading cannot be retrieved, which is the expected
  behaviour and the reason the collection date is recorded.
* The corpus is therefore the set of posts that were still public on
  2026-10-02.

## Two API behaviours worth knowing

* **A `next_token` is returned even on the last page.** Following it returns
  the last post again, which produced one duplicate per monthly slice (165 in
  total) in this run. They are removed when the tables are built, and the
  collector now stops as soon as a page adds no new post, so later runs do not
  pay for them.
* **Post text can contain U+2028**, the Unicode line separator. Python's
  `str.splitlines()` treats it as a line break, which split stored JSON pages
  in two and silently dropped 236 posts from the first build. The readers now
  split on newlines only. The stored data was never affected.
