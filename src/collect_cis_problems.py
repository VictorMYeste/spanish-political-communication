#!/usr/bin/env python3
"""Collect the "main problems of Spain" table from the CIS monthly barometers.

For every month in the window of config/cis_topic_selection.json, finds the
barometer study page on cis.es, downloads its results PDF (marginales) and
parses the question on the three main problems of Spain. Writes one row per
barometer and CIS category with the first, second, third and TOTAL
percentages. Months without a barometer (usually August) are reported and
skipped.

Downloaded PDFs are kept in data/raw/cis/ and reused on later runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "cis_topic_selection.json"
DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "cis"
DEFAULT_OUT = ROOT / "data" / "processed" / "cis_problems_monthly.csv"
BASE_URL = "https://www.cis.es"
USER_AGENT = "Mozilla/5.0 (academic research; spanish-political-communication)"

MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

OUT_FIELDS = [
    "study_id",
    "year",
    "month",
    "category",
    "first_pct",
    "second_pct",
    "third_pct",
    "total_pct",
    "n",
    "pdf_url",
    "pdf_file",
]

QUESTION_RE = re.compile(r"principal problema que existe actualmente en Espa", re.IGNORECASE)
NUMBER = r"(\d+,\d+|-)"
ROW_RE = re.compile(rf"^(.*?)\s*{NUMBER}\s+{NUMBER}\s+{NUMBER}\s+{NUMBER}\s*$")
N_RE = re.compile(r"^\(N\)\s*\(?([\d.]+)\)?")
# Column headers and page headers. They reset the label buffer, so they must
# match whole lines: some category labels start with "Problemas".
HEADER_RE = re.compile(
    r"^((primer|segundo|tercer)( problema)?|problema( total)?|total|\[recod\.\]( total)?"
    r"|cis|p[áa]g\.? ?\d+)$|^(\(respuesta|\(multi|pregunta|estudio n)",
    re.IGNORECASE,
)


def month_range(first: str, last: str) -> List[Tuple[int, int]]:
    """Return (year, month) pairs from first to last, both 'YYYY-MM'."""
    year, month = (int(part) for part in first.split("-"))
    end = tuple(int(part) for part in last.split("-"))
    months = []
    while (year, month) <= end:
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def study_page_url(template: str, year: int, month: int) -> str:
    return template.format(month=MONTHS_ES[month - 1], year=year)


def parse_study_page(html: str, page_url: str = "") -> Tuple[Optional[str], Optional[str]]:
    """Return the study number and the URL of its results PDF, if present."""
    match = re.search(r"Estudio\s+n[ºo°]\s*(\d{4})", html) or re.search(
        r"avance-de-resultados-del-estudio-(\d{4})", page_url
    )
    if not match:
        return None, None
    study_id = match.group(1)
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(rf"/es{study_id}mar", href, re.IGNORECASE):
            href = href.replace("&amp;", "&")
            return study_id, href if href.startswith("http") else BASE_URL + href
    return study_id, None


def find_preview_url(homepage_html: str, year: int, month: int) -> Optional[str]:
    """Find the results-preview page of a barometer linked from the CIS homepage.

    The newest barometer is published as an "avance de resultados" before its
    study page exists.
    """
    pattern = rf"avance-de-resultados-del-estudio-\d{{4}}-bar(?:ó|%C3%B3)metro-de-{MONTHS_ES[month - 1]}-{year}"
    for href in re.findall(r'href="([^"]+)"', homepage_html):
        if re.search(pattern, href, re.IGNORECASE):
            return href if href.startswith("http") else BASE_URL + href
    return None


def to_float(value: str) -> float:
    return 0.0 if value == "-" else float(value.replace(",", "."))


def parse_problem_table(text: str) -> Tuple[List[Dict[str, object]], Optional[int]]:
    """Parse the main-problems table from the text of a results PDF.

    Long category labels wrap over several lines, so lines without numbers are
    buffered and prefixed to the next row. Column headers reset the buffer.
    """
    match = QUESTION_RE.search(text)
    if not match:
        raise ValueError("main-problems question not found")
    lines = text[match.end():].splitlines()[1:]
    rows: List[Dict[str, object]] = []
    buffer: List[str] = []
    n_value: Optional[int] = None
    for raw_line in lines:
        line = " ".join(raw_line.split())
        if not line:
            continue
        n_match = N_RE.match(line)
        if n_match:
            n_value = int(n_match.group(1).replace(".", ""))
            break
        if rows and line.lower().startswith("pregunta"):
            break
        row_match = ROW_RE.match(line)
        if row_match:
            label = " ".join(buffer + [row_match.group(1)]).strip()
            buffer = []
            first, second, third, total = (to_float(row_match.group(i)) for i in range(2, 6))
            rows.append(
                {
                    "category": label,
                    "first_pct": first,
                    "second_pct": second,
                    "third_pct": third,
                    "total_pct": total,
                }
            )
        elif HEADER_RE.match(line) or "MULTIRRESPUESTA" in line.upper():
            buffer = []
        else:
            buffer.append(line)
    if not rows:
        raise ValueError("main-problems table is empty")
    if n_value is None:
        # Some barometers print the table transposed (categories as columns),
        # which this parser does not read.
        raise ValueError("end of table (N) not found, layout not supported")
    return rows, n_value


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def fetch(session, url: str):
    response = session.get(url, timeout=60, headers={"User-Agent": USER_AGENT})
    return response


def collect(config: Dict, raw_dir: Path, sleep: float) -> Tuple[List[Dict[str, object]], List[str]]:
    import requests

    session = requests.Session()
    raw_dir.mkdir(parents=True, exist_ok=True)
    window = config["window"]
    template = config["source"]["study_page"]
    out_rows: List[Dict[str, object]] = []
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
        study_id, pdf_url = parse_study_page(response.text, page_url)
        if not study_id or not pdf_url:
            missing.append(f"{label}: no results PDF on {page_url}")
            continue
        pdf_path = raw_dir / f"es{study_id}mar.pdf"
        if not pdf_path.exists():
            pdf_response = fetch(session, pdf_url)
            time.sleep(sleep)
            if pdf_response.status_code != 200 or not pdf_response.content.startswith(b"%PDF"):
                missing.append(f"{label}: PDF download failed ({pdf_response.status_code})")
                continue
            pdf_path.write_bytes(pdf_response.content)
        try:
            rows, n_value = parse_problem_table(pdf_text(pdf_path))
        except ValueError as exc:
            missing.append(f"{label}: study {study_id}: {exc}")
            continue
        for row in rows:
            out_rows.append(
                {
                    "study_id": study_id,
                    "year": year,
                    "month": month,
                    **row,
                    "n": n_value if n_value is not None else "",
                    "pdf_url": pdf_url,
                    "pdf_file": str(pdf_path.relative_to(ROOT)) if pdf_path.is_relative_to(ROOT) else str(pdf_path),
                }
            )
        print(f"{label}: study {study_id}, {len(rows)} categories, N={n_value}")
    return out_rows, missing


def write_csv(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows, missing = collect(config, args.raw_dir, args.sleep)
    write_csv(args.out, rows)
    barometers = len({row["study_id"] for row in rows})
    print(f"\nWrote {len(rows)} rows from {barometers} barometers to {args.out}")
    if missing:
        print("Skipped months:")
        for item in missing:
            print(f"  {item}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
