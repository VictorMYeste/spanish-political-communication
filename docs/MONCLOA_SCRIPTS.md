# La Moncloa scripts

This document describes the dedicated pipeline for official Pedro Sánchez
intervention pages published by La Moncloa. It is independent from the Congreso and X pipelines.

The pipeline accepts only the configured La Moncloa archive, does not download
audio/video, and does not perform automatic speech recognition.

## Files

- `src/collect_moncloa.py`: discovers pages in the official monthly archive.
- `src/extract_moncloa_text.py`: downloads candidate pages and extracts text.
- `config/moncloa_source.json`: contains the single allowed source.
- `config/politicians.json`: existing project configuration used to identify
  `pedro_sanchez`.
- `tests/test_moncloa_pipeline.py`: offline tests with synthetic HTML.

## Requirements

Run the commands from the repository root after activating the virtual
environment:

```bash
python -m pip install -r requirements.txt
python -m pytest tests/test_moncloa_pipeline.py
```

No live website is contacted by the tests.

## 1. Discover candidate pages

A small test run:

```bash
python src/collect_moncloa.py \
  --source-config config/moncloa_source.json \
  --politicians-config config/politicians.json \
  --output-dir data \
  --max-pages 3
```

Because this test intentionally stops after three pages, the collection summary
may report `pagination_completed=false` with
`stop_reason=max_pages_per_source`. This is expected for the limited test.

The complete run:

```bash
python src/collect_moncloa.py \
  --source-config config/moncloa_source.json \
  --politicians-config config/politicians.json \
  --output-dir data
```

The configured default start date is 2023-07-23. It can be overridden with
`--start YYYY-MM-DD`; an optional inclusive end date can be supplied with
`--end YYYY-MM-DD`.

Collection creates:

- `data/processed/moncloa_candidates.csv`
- `data/processed/moncloa_collection_summary.json`
- `data/raw/moncloa/moncloa_fetch_manifest.json`
- cached responses under `data/cache/moncloa/`

The candidate CSV contains metadata and URLs, not the full intervention text.

## 2. Extract and classify text

A three-document check:

```bash
python src/extract_moncloa_text.py \
  --inventory data/processed/moncloa_candidates.csv \
  --source-config config/moncloa_source.json \
  --politicians-config config/politicians.json \
  --output-dir data/processed \
  --cache-dir data/cache/moncloa_text \
  --max-documents 3
```

Remove `--max-documents 3` for the complete run.

The extractor creates:

| File | Purpose |
|---|---|
| `moncloa_inventory.csv` | One auditable record per processed candidate |
| `moncloa_documents_internal.csv` | Cleaned full page text for internal research |
| `moncloa_segments_internal.csv` | Verified Pedro Sánchez answer or monologue segments |
| `moncloa_stimuli.csv` | Downstream table; every row starts with `keep_for_prompt=false` |
| `moncloa_extraction_audit.csv` | Status, reason, HTTP and parsing information |
| `moncloa_summary.json` | Counts derived from the generated rows |

The extractor is deliberately conservative:

- labelled interview answers may be included;
- interviewer questions are not stored as politician segments;
- press conferences with ambiguous speakers remain `review`;
- parliamentary pages are excluded because they belong to the Congreso
  pipeline;
- short, missing, or structurally ambiguous texts remain `review`;
- full text stays in the internal file and should not be committed.

## Interrupted runs

Both stages keep HTTP responses in their cache. Re-running the same command
reuses valid cached responses. The output CSV and JSON files are rebuilt from
the available results, so an interrupted run can be started again safely.
Use `--refresh` only when a new download is required, and `--offline` when
the run must use cache without making network requests.

## Version control

Commit the scripts, configuration, tests and documentation. Generated files
remain under `data/`, which is already excluded by the repository `.gitignore`.
Do not commit caches, downloaded HTML, internal full-text tables or generated
CSV/JSON outputs.

```gitignore
data/
```
