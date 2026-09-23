# PP audiovisual and official-document pipeline

This pipeline tests the official Partido Popular website for material by Alberto
Núñez Feijóo from 23 July 2023 onward.

It deliberately separates three things:

1. official PP video metadata;
2. third-party platform links, normally YouTube, which are **not downloaded**;
3. PDF speeches/interventions hosted by `pp.es`, which may be downloaded and
   converted to text.

The PP case is therefore not identical to VOX: the official Feijóo page exposes
YouTube video identifiers, but it also publishes some official PDF speeches and
interventions that can provide usable text without diarisation.

## Files

- `config/pp_media_source.json`: source, host allow-lists, dates and storage limits.
- `src/collect_pp_media.py`: builds the media and document inventories.
- `src/extract_pp_documents.py`: extracts text from downloaded PP PDFs.
- `tests/test_pp_media_pipeline.py`: offline unit tests.

## Safety and storage policy

The collector:

- checks `robots.txt` before online page collection;
- waits between requests and retries only temporary HTTP failures;
- accepts redirects only when every destination remains on an allowed host;
- limits response sizes;
- never bypasses authentication, DRM, bot protection or HTTP 403 responses;
- never downloads YouTube or other platform-hosted media;
- caps PP raw/intermediate storage at 100 GB and preserves 200 GiB of free disk.

## 1. Install and test

From the project root:

```bash
python -m pip install -r requirements-pp-media.txt
python -m pytest -q tests/test_pp_media_pipeline.py
```

If a globally installed pytest plugin causes an option conflict, isolate the
project tests for that command only:

PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q tests/test_pp_media_pipeline.py
Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
```

Bash:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_pp_media_pipeline.py
```

## 2. Run the small online test

```bash
python src/collect_pp_media.py \
  --source-config config/pp_media_source.json \
  --output-dir data_pp_test \
  --max-pages 2
```

PowerShell uses backticks instead of backslashes:

```powershell
python src/collect_pp_media.py `
  --source-config config/pp_media_source.json `
  --output-dir data_pp_test `
  --max-pages 2
```

Outputs:

- `data_pp_test/processed/pp_media_candidates.csv`
- `data_pp_test/processed/pp_official_documents.csv`
- `data_pp_test/processed/pp_media_collection_summary.json`

The collector sends the same public, read-only request used by the PP page's
JavaScript `CARGAR MÁS` button. `--max-pages 2` means two pages for the video
stream and two pages for the document stream (the shared first page is fetched
once). The summary should report `max_pages_per_stream`; that is expected for
this small test and means the test inventory is intentionally incomplete.

Useful PowerShell checks:

```powershell
Get-Content .\data_pp_test\processed\pp_media_collection_summary.json

Import-Csv .\data_pp_test\processed\pp_media_candidates.csv |
  Select-Object date,title,event_type,media_host,discovery_status |
  Format-Table -AutoSize

Import-Csv .\data_pp_test\processed\pp_official_documents.csv |
  Select-Object date_hint,title,document_kind,relevant_to_actor,download_status |
  Format-Table -AutoSize
```

Expected interpretation:

- `external_platform_metadata_only` / `not_permitted_platform` means that the
  official PP page points to YouTube, so no video was downloaded;
- `direct_media_found` would mean a direct audio/video URL is on an explicitly
  allowed host (the supplied configuration starts with no such hosts);
- rows in `pp_official_documents.csv` are PP-hosted PDF candidates.

## 3. Run the complete date-bounded collection

When the two-page test looks correct, remove `--max-pages`:

```powershell
python src/collect_pp_media.py `
  --source-config config/pp_media_source.json `
  --output-dir data_pp_full `
  --start 2023-07-23
```

The video and document streams stop independently when the requested start date
is reached or the archive ends. A successful full run reports
`requested_date_range_complete`. It still downloads no video.

## 4. Browser-saved fallback, only if online collection is blocked

Open this official page in a normal browser:

`https://www.pp.es/persona/alberto-nunez-feijoo/`

If the online run returns HTTP 403, open the page in a normal browser. In
`Noticias relacionadas`, select `VÍDEOS` and click `CARGAR MÁS` until the visible
dates pass 23 July 2023. In `Documentos relacionados`, do the same for the PDF
list. Save the expanded page as `pp_page_1.html` in the project root.

Then run the offline parser, which makes no page request to `pp.es`:

```powershell
python src/collect_pp_media.py `
  --source-config config/pp_media_source.json `
  --output-dir data_pp_test `
  --archive-html-dir .
```

If the browser produces several snapshots, name them `pp_page_1.html`,
`pp_page_2.html`, etc. The collector merges and deduplicates them.

Check the date coverage:

```powershell
$pp = Import-Csv .\data_pp_test\processed\pp_media_candidates.csv
$pp | Measure-Object -Property date -Minimum -Maximum
```

## 5. Download only the official PP PDFs

For a normal online full run, add `--download-documents`:

```powershell
python src/collect_pp_media.py `
  --source-config config/pp_media_source.json `
  --output-dir data_pp_full `
  --start 2023-07-23 `
  --download-documents
```

If the archive itself required the browser-saved fallback, use:

```powershell
python src/collect_pp_media.py `
  --source-config config/pp_media_source.json `
  --output-dir data_pp_full `
  --archive-html-dir . `
  --download-documents
```

PDFs are stored under `data_pp_full/raw/pp/documents/`. No YouTube video is
downloaded by this command.

Extract their text:

```powershell
python src/extract_pp_documents.py --output-dir data_pp_full
```

The outputs are:

- one UTF-8 text file per document under
  `data_pp_full/processed/pp_documents_text/`;
- `data_pp_full/processed/pp_document_pages.csv`;
- `data_pp_full/processed/pp_document_extraction_summary.json`.

Pages with very little extractable text are labelled `possibly_needs_ocr`; the
script does not silently run OCR.
