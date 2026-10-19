# Data dictionary

This document defines the tables shared by all sources so that X posts, Congreso interventions and interviews can be combined downstream. Congreso columns follow `src/extract_congreso_text.py`; X columns are proposed and will be fixed when `collect_x.py` is written.

Generated data is never committed. Paths are relative to the repository root.

## Actor registry

Actors share one `actor_id` across sources.

| File | Owner | Content |
| --- | --- | --- |
| `config/politicians.json` | Congreso pipeline | `actor_id`, `display_name`, `congreso_name`, `marker_aliases`, `party` |
| `config/x_accounts.json` | X pipeline | `actor_id`, `x_handle`, `x_user_id` |

`x_user_id` is the stable account identifier. Handles can change, so collection queries use the ID.

## Internal tables

One per source, with full identity and provenance. For research use only.

### Shared core columns

| Column | Description |
| --- | --- |
| `bundle_id` | Bundle identifier, empty until bundling |
| `message_id` | Stable identifier, unique across sources (see prefixes below) |
| `actor_id` | Actor identifier from the registry |
| `actor_name` | Display name |
| `party` | Party at the time of the message |
| `date` | Local date in Europe/Madrid (`YYYY-MM-DD`) |
| `text` | Full original text |
| `author_role` | Institutional role at the time of the message |
| `source_type` | `congress_intervention`, `x_post`, `interview` |
| `retrieved_at_utc` | Collection timestamp (ISO 8601, UTC) |

`message_id` prefixes:

| Source | Prefix | Construction |
| --- | --- | --- |
| Congreso | `cg1_` | SHA-256 of document ID, actor, turn index and text start (`stable_message_id`) |
| X | `x_` | `x_<post_id>` (post IDs are already stable) |

### Congreso-specific columns

`bundle_id`, `turn_index`, `segment_index`, `speaker_header`, `source_cve`, `text_url`, `pdf_url`. See `docs/CONGRESO_SCRIPTS.md`.

### X-specific columns

Output file: `data/processed/x_posts_internal.csv`, written by `src/build_x_tables.py`.

| Column | Description |
| --- | --- |
| `post_id` | X post ID |
| `x_user_id` | Author account ID |
| `created_at_utc` | Post timestamp from the API (UTC) |
| `post_type` | `original`, `reply` or `quote`, derived from `referenced_tweets`; a reply that also quotes counts as `quote` |
| `referenced_post_id` | Replied-to or quoted post ID, if any |
| `referenced_post_author_id` | Author of the referenced post |
| `referenced_post_text` | Text of the referenced post, collected as an expansion so that short posts can be read in context |
| `in_reply_to_user_id` | Author of the replied-to post, if any |
| `conversation_id` | Thread identifier |
| `lang` | Language detected by X |
| `is_long_post` | `True` when the text comes from `note_tweet` |
| `edit_history_post_ids` | Post IDs in the edit history, `;`-separated |
| `retweet_count`, `reply_count`, `like_count`, `quote_count`, `bookmark_count`, `impression_count` | Public metrics at collection time |
| `post_url` | `https://x.com/<handle>/status/<post_id>` |
| `raw_file` | Raw JSONL file the post was read from |

## Stimuli tables

Same columns for every source, compatible with the prompt-ready table of the previous paper. Pseudonymised, not anonymous: exact text remains searchable.

Output files: `data/processed/congreso_stimuli.csv`, `data/processed/x_stimuli.csv`. The X table holds original posts only; quotes and replies stay in the internal table (`docs/PROPOSAL.md`, section 6).

| Column | Description |
| --- | --- |
| `tweet_id` | Message identifier used by the prompting code. X: post ID. Congreso: `message_id` |
| `message_id` | Same as in the internal table |
| `bundle_id` | Bundle identifier, empty until bundling |
| `text_anon` | Text after anonymization. X: cleaned and anonymized by `src/prepare_x_stimuli.py` |
| `date` | Local date (`YYYY-MM-DD`) |
| `author_role` | Role shown to the model |
| `source_type` | As in the internal table |
| `topic` | Canonical topic, `other`, or `review_needed` |
| `keep_for_prompt` | `True` only after quality, topic and anonymization checks. X: `True` for the posts drawn by `src/sample_x_stimuli.py` |
| `is_morally_relevant` | Moral-relevance filter output. X: empty with the next two columns, because the filter is not used (`docs/PROPOSAL.md`, section 6) |
| `dominant_moral_dimension` | Highest-scoring moral foundation |
| `moral_max` | Score of the dominant dimension |
| `isDeleted` | `True` if the message was removed from the source after collection |

## Raw X storage

| Path | Content |
| --- | --- |
| `data/raw/x/<actor_id>/<YYYY-MM>.jsonl` | One API response page per line, untouched |
| `data/raw/x/manifest.jsonl` | One line per request: actor, query, time range, page token, result count, HTTP status, timestamp |

## Release policy

Released: code, configuration, documentation, rule tables, audit summaries, post IDs and derived labels.
Not released: `text`, `text_anon`, raw API responses, Congreso caches, interview material.
