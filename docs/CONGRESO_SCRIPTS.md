# Congreso de los Diputados scripts

This document explains the scripts used to collect and prepare parliamentary interventions from the official Congreso de los Diputados sources.

The pipeline currently targets:

* Pedro Sánchez (PSOE)
* Alberto Núñez Feijóo (PP)
* Santiago Abascal (VOX)

The politicians and their official Congreso names are defined in `config/politicians.json`.

## Pipeline overview

Run the scripts in this order:

1. `collect_congreso.py` downloads and filters the official Open Data catalogue.
2. `extract_congreso_text.py` downloads the full parliamentary documents and extracts the target speakers' interventions.

Install the required packages before running the pipeline:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 1. Collect the intervention inventory

`src/collect_congreso.py` works with the official chronological Open Data JSON published by the Congreso.

It:

* discovers the most recent official JSON snapshot;
* downloads and stores the original snapshot;
* calculates its SHA-256 checksum;
* selects records belonging to the politicians in `config/politicians.json`;
* optionally filters records by date;
* removes duplicate intervention records;
* preserves source and provenance information.

Run it with:

```bash
python src/collect_congreso.py \
  --config config/politicians.json \
  --output-dir data \
  --contact-email YOUR_INSTITUTIONAL_EMAIL
```

The contact email is optional. When provided, it is included only in the HTTP `User-Agent` so that the academic requester can be identified if the website administrators need to make contact. It is not written to the corpus files.

### Outputs

The script creates:

* `data/raw/congreso/IntervencionesCronologicamente_*.json`: original official snapshot;
* `data/processed/congreso_interventions.csv`: deduplicated intervention inventory used by the next script;
* `data/processed/congreso_intervention_links.csv`: detailed source and provenance records;
* `data/processed/congreso_summary.json`: counts, date ranges, checksum and collection summary.

### Useful options

* `--start YYYY-MM-DD`: include records on or after this date.
* `--end YYYY-MM-DD`: include records on or before this date.
* `--snapshot PATH`: process an existing local official JSON instead of downloading the latest one.
* `--snapshot-url URL`: record the provenance URL associated with a local snapshot.
* `--force-download`: download the snapshot again even when the file already exists.

## 2. Extract the full speaker interventions

`src/extract_congreso_text.py` reads `congreso_interventions.csv` and processes the linked full-text pages from the official *Diario de Sesiones*.

It:

* downloads each full-text HTML document once and stores it in a local cache;
* reuses cached documents in later runs;
* detects formal speaker headers;
* identifies politicians using all components of their official surnames;
* rejects references to politicians inside ordinary prose;
* rejects interventions by other deputies who share one surname;
* removes very short segments according to the selected threshold;
* creates stable message identifiers;
* records extraction diagnostics and possible count differences.

Run it with:

```bash
python src/extract_congreso_text.py \
  --inventory data/processed/congreso_interventions.csv \
  --config config/politicians.json \
  --output-dir data/processed \
  --cache-dir data/cache/congreso_text \
  --contact-email YOUR_INSTITUTIONAL_EMAIL
```

### Outputs

The script creates:

* `data/processed/congreso_segments_internal.csv`: complete research table containing speaker identity, party, text and provenance;
* `data/processed/congreso_stimuli.csv`: pseudonymised table prepared for later annotation and persona experiments;
* `data/processed/congreso_extraction_summary.json`: extraction counts, errors and quality-control diagnostics;
* `data/cache/congreso_text/*.html`: cached official full-text pages.

The stimulus table is not anonymous. It removes the explicit actor fields, but the original text can still be searched or attributed to its speaker.

### Useful options

* `--max-documents 3`: process only three documents for a quick test.
* `--min-chars 80`: set the minimum intervention length. The default is 80 characters.
* `--delay 1`: wait between new page downloads. The default is one second.

To preserve every formal speaker turn, including very short replies, use `--min-chars 0`. The default threshold is more suitable when the corpus is intended for substantive language analysis.

## Quality-control messages

During extraction, each document is labelled `count-match` or `count-mismatch`.

* `count-match` means that the number of extracted speaker turns agrees with the number of relevant Open Data records.
* `count-mismatch` is a warning, not an automatic extraction failure. The Open Data catalogue and the full parliamentary transcript can represent interventions at different levels of detail.

Review `congreso_extraction_summary.json` after each complete run. In particular, check:

* `documents_failed`;
* `documents_without_target_segments`;
* `documents_with_count_mismatch`;
* `rejected_nonformal_target_like_headers`;
* `unmatched_formal_surname_collision_headers`.

The extraction script returns a non-zero exit code when at least one document fails.

## Automated tests

The test file checks the main collection and extraction rules with small artificial examples. It does not download any data.

Run the tests with:

```bash
python -m pip install pytest
python -m pytest
```

A successful run should report that all tests passed.

## Reproducibility and data handling

Keep the scripts, configuration file, summaries and source identifiers with the research project. The raw snapshot and cached HTML pages make a run easier to reproduce, but they should only be stored or redistributed in accordance with the applicable Congreso reuse conditions and the project's data-management plan.

`congreso_stimuli.csv` should be treated as pseudonymised research data because exact quotations remain searchable.
