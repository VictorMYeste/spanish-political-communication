# Spanish Political Communication

Persona-conditioned moral and cultural reception of political messages by the leaders of the main Spanish national parties.

Status: corpus design and data acquisition (September 2026).

## Overview

This project extends the design of our previous study on Trump's posts (Findings of EMNLP 2026; [code](https://github.com/VictorMYeste/trump-moral-audiences-pipeline)) to three Spanish party leaders in the XV Legislature:

* Pedro Sánchez (PSOE)
* Alberto Núñez Feijóo (PP)
* Santiago Abascal (VOX)

Theory-grounded synthetic personas, based on Inglehart–Welzel cultural values and Moral Foundations Theory, evaluate topic-specific bundles of the leaders' messages. They return structured judgments: stance, agreement, legitimacy, offensiveness and endorsement. The results are evaluated at three levels: internal differentiation, explanatory structure and external plausibility against Spanish public-opinion data from the Centro de Investigaciones Sociológicas (CIS).

The multi-actor setting adds questions a single-actor study could not address: whether reception follows the message or the speaker, and whether it changes across X posts, parliamentary interventions and media interviews. The general election is expected in early 2027.

The full study design, research questions and open decisions are in [docs/PROPOSAL.md](docs/PROPOSAL.md).

## Research questions

* **RQ1.** Do personas selected for different topic-conditioned positions differ in their reception of the same leader's messages?
* **RQ2.** Which demographic, cultural and moral persona attributes characterise supportive, rejecting and neutral responses?
* **RQ3.** Do synthetic response distributions align with CIS public-opinion distributions?
* **RQ4.** How much of the reception depends on the speaker rather than the message?
* **RQ5.** Does reception differ between X, parliament and media interviews?

## Sources

| Source | Status | Documentation |
| --- | --- | --- |
| X posts | Access check available; collection pending pay-per-use enrolment | [docs/X_SCRIPTS.md](docs/X_SCRIPTS.md) |
| Congreso de los Diputados interventions | Collection and text extraction pipeline available | [docs/CONGRESO_SCRIPTS.md](docs/CONGRESO_SCRIPTS.md), [docs/CONGRESO_RESULTS.md](docs/CONGRESO_RESULTS.md) |
| Media interviews | Planned as a secondary corpus for robustness checks | [docs/PROPOSAL.md](docs/PROPOSAL.md) |

All sources share the actor identifiers in `config/politicians.json` and the table formats in [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

## Repository layout

```
config/   politicians.json    actor registry shared by all sources
          x_accounts.json     X handles and user IDs, joined by actor_id
          x_collection.json   X collection window, query and fields
src/      check_x_access.py, collect_x.py, build_x_tables.py
          collect_congreso.py, extract_congreso_text.py
tests/    pytest tests, no network access
docs/     proposal, data dictionary and per-source documentation
data/     generated locally and never committed (raw/, processed/, cache/)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

X credentials are only needed for the X scripts. Copy `.env.example` to `.env` and fill in `X_BEARER_TOKEN`; `.env` is ignored by git.

## X

Posts written by the leaders are collected with full-archive search, excluding retweets. The collector counts the matching posts and prints the cost before downloading anything.

```bash
python src/check_x_access.py --write-ids   # check access, resolve account IDs
python src/collect_x.py --counts-only      # how many posts and what they would cost
python src/collect_x.py                    # counts, confirms, then downloads
python src/build_x_tables.py               # internal and stimuli tables
```

Costs, options and data handling are described in [docs/X_SCRIPTS.md](docs/X_SCRIPTS.md).

## Congreso de los Diputados

The pipeline downloads the official Open Data chronological JSON snapshot, verifies it with a SHA-256 checksum, filters the target politicians and extracts their speaker turns from the linked *Diario de Sesiones* pages.

```bash
python src/collect_congreso.py \
  --config config/politicians.json \
  --output-dir data \
  --contact-email YOUR_INSTITUTIONAL_EMAIL

python src/extract_congreso_text.py \
  --inventory data/processed/congreso_interventions.csv \
  --config config/politicians.json \
  --output-dir data/processed \
  --cache-dir data/cache/congreso_text \
  --contact-email YOUR_INSTITUTIONAL_EMAIL
```

For a small end-to-end test, add `--max-documents 3` to the second command. `keep_for_prompt` defaults to `false` pending sampling and manual quality checks.

Recheck the official source and reuse conditions at collection time:

* [Congreso Open Data — interventions](https://www.congreso.es/es/opendata/intervenciones)
* [Congreso legal and reuse notice](https://www.congreso.es/es/cem/aviso-legal)

## Data handling and ethics

* The repository contains code, configuration and documentation only. Collected data, caches and credentials are never committed.
* Each source produces an internal table with full provenance and a stimuli table for the persona experiments. Stimuli are pseudonymised, not anonymous, because the original text remains searchable.
* X data is collected only through the official API. Only post IDs and derived labels may be shared.
* Congreso material is used under its reuse conditions. Interview material is processed for research and not redistributed.
* Politicians' public statements are still personal data under the GDPR. They are processed for academic, non-commercial research only.
* Persona positions are selection criteria, not claims about real voters or party electorates.

## Contributing

* Create one branch per task from an up-to-date `main` and open a pull request; the other team member reviews before merging.
* Keep changes to shared files (`README.md`, `.gitignore`, `requirements.txt`, `config/politicians.json`, `docs/PROPOSAL.md`, `docs/DATA_DICTIONARY.md`) in small, separate pull requests.
* Run `python -m pytest` before opening a pull request.

## Citation

A citation for this project will be added when a preprint is available. Previous study:

> Candida Maria Greco, Víctor Yeste, Andrea Tagarelli, and Paolo Rosso. 2026. "My own morality, my own mind": Persona-Conditioned Moral and Cultural Reception on Trump Messages. In *Findings of the Association for Computational Linguistics: EMNLP 2026*.

## License

The code and documentation in this repository are released under the [Apache License 2.0](LICENSE). Source data are not included and remain subject to their own terms.
