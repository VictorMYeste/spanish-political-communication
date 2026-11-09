#!/usr/bin/env python3
"""Download the microdata of the CIS monthly barometers (RQ4).

For every month in the window of config/cis_topic_selection.json (the window of
the X posts and of the topic selection), finds the barometer study page on
cis.es, reads the link to its data file ("Fichero datos") from the page's
structured data and downloads the zip. Only the SPSS file is extracted from the
zip, as it is the one format that every wave includes. Files already downloaded
are reused. Writes a manifest with one row per barometer; months without a
barometer or without a data file are reported and skipped.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from collect_cis_problems import (
    BASE_URL,
    DEFAULT_CONFIG,
    ROOT,
    fetch,
    find_preview_url,
    month_range,
    parse_study_page,
    study_page_url,
)

DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "cis_microdata"
MANIFEST_FIELDS = ["study_id", "year", "month", "page_url", "data_url", "zip_file", "zip_sha256", "sav_file"]
DOWNLOAD_RE = re.compile(r'\{[^{}]*"@type"\s*:\s*"DataDownload"[^{}]*\}')
DATA_ZIP_RE = re.compile(r"/md\d{4}[^/]*\.zip$", re.IGNORECASE)


def parse_data_file_url(html: str) -> Optional[str]:
    """Return the URL of the study's data file, listed as a DataDownload in the page's JSON-LD."""
    for match in DOWNLOAD_RE.finditer(html):
        try:
            item = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        url = str(item.get("contentUrl", ""))
        name = str(item.get("name", "")).strip().lower()
        if name.startswith("fichero de datos") or name.startswith("fichero datos") or DATA_ZIP_RE.search(url):
            return url if url.startswith("http") else BASE_URL + url
    return None


def extract_sav(zip_path: Path, study_id: str, out_path: Path) -> None:
    """Write the study's SPSS file from the zip to out_path."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".sav")]
        exact = [name for name in names if Path(name).name.lower() == f"{study_id}.sav"]
        chosen = exact or names
        if len(chosen) != 1:
            raise ValueError(f"{zip_path.name}: expected one .sav file, found {names or 'none'}")
        out_path.write_bytes(archive.read(chosen[0]))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def collect(config: Dict, raw_dir: Path, sleep: float, session=None) -> Tuple[List[Dict[str, object]], List[str]]:
    if session is None:
        import requests

        session = requests.Session()
    raw_dir.mkdir(parents=True, exist_ok=True)
    window = config["window"]
    template = config["source"]["study_page"]
    rows: List[Dict[str, object]] = []
    missing: List[str] = []
    homepage_html: Optional[str] = None
    for year, month in month_range(window["first_month"], window["last_month"]):
        label = f"{year}-{month:02d}"
        page_url = study_page_url(template, year, month)
        response = fetch(session, page_url)
        time.sleep(sleep)
        if response.status_code != 200:
            if homepage_html is None:
                homepage_html = fetch(session, BASE_URL + "/es/").text
            preview_url = find_preview_url(homepage_html, year, month)
            if preview_url:
                page_url = preview_url
                response = fetch(session, page_url)
                time.sleep(sleep)
        if response.status_code != 200:
            missing.append(f"{label}: no barometer page ({response.status_code})")
            continue
        study_id, _ = parse_study_page(response.text, page_url)
        data_url = parse_data_file_url(response.text)
        if not study_id or not data_url:
            missing.append(f"{label}: no data file on {page_url}")
            continue
        zip_path = raw_dir / f"MD{study_id}.zip"
        if not zip_path.exists():
            data_response = fetch(session, data_url)
            time.sleep(sleep)
            if data_response.status_code != 200 or not data_response.content.startswith(b"PK"):
                missing.append(f"{label}: study {study_id}: data file download failed ({data_response.status_code})")
                continue
            zip_path.write_bytes(data_response.content)
        sav_path = raw_dir / f"{study_id}.sav"
        if not sav_path.exists():
            try:
                extract_sav(zip_path, study_id, sav_path)
            except (ValueError, zipfile.BadZipFile) as exc:
                missing.append(f"{label}: study {study_id}: {exc}")
                continue
        rows.append({
            "study_id": study_id, "year": year, "month": month, "page_url": page_url, "data_url": data_url,
            "zip_file": relative(zip_path), "zip_sha256": sha256(zip_path), "sav_file": relative(sav_path),
        })
        print(f"{label}: study {study_id}")
    return rows, missing


def write_manifest(path: Path, rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--manifest", type=Path, default=None, help="default: <raw-dir>/manifest.csv")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows, missing = collect(config, args.raw_dir, args.sleep)
    manifest = args.manifest or args.raw_dir / "manifest.csv"
    write_manifest(manifest, rows)
    print(f"\nWrote {len(rows)} barometers to {manifest}")
    if missing:
        print("Skipped months:")
        for item in missing:
            print(f"  {item}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
