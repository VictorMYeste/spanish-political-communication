#!/usr/bin/env python3
"""Extract text from PP-hosted PDF documents downloaded by collect_pp_media.py."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "1.0.0"
PAGE_FIELDS = [
    "document_id",
    "source_id",
    "actor_id",
    "date_hint",
    "title",
    "document_url",
    "page_number",
    "characters",
    "extraction_status",
    "text",
    "error",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def contained_path(output_dir: Path, relative: str) -> Path:
    candidate = (output_dir / relative).resolve()
    root = output_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Inventory path escapes output directory: {relative}") from exc
    return candidate


def write_text_document(path: Path, row: dict[str, str], page_texts: list[str]) -> None:
    header = [
        f"Title: {row.get('title', '')}",
        f"Date hint: {row.get('date_hint', '')}",
        f"Source: {row.get('document_url', '')}",
        "",
    ]
    content = "\n".join(header)
    for number, text in enumerate(page_texts, start=1):
        content += f"\n===== PAGE {number} =====\n\n{text}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract text from downloaded official PP PDF documents.")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument(
        "--documents-csv",
        help="Defaults to OUTPUT_DIR/processed/pp_official_documents.csv",
    )
    parser.add_argument(
        "--include-non-relevant",
        action="store_true",
        help="Also extract documents not matched by the Feijóo/title keyword filter",
    )
    parser.add_argument(
        "--minimum-page-characters",
        type=int,
        default=30,
        help="Pages below this threshold are flagged as possibly needing OCR",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    inventory = (
        Path(args.documents_csv).resolve()
        if args.documents_csv
        else output_dir / "processed" / "pp_official_documents.csv"
    )
    if args.minimum_page_characters < 0:
        print("Error: --minimum-page-characters cannot be negative", file=sys.stderr)
        return 2
    if not inventory.exists():
        print(f"Error: document inventory not found: {inventory}", file=sys.stderr)
        return 2
    try:
        from pypdf import PdfReader
    except ImportError:
        print("Error: pypdf is required. Install requirements-pp-media.txt.", file=sys.stderr)
        return 2

    with inventory.open("r", newline="", encoding="utf-8-sig") as handle:
        documents = list(csv.DictReader(handle))

    selected = [
        row
        for row in documents
        if row.get("download_status") in {"downloaded", "cached"}
        and (args.include_non_relevant or row.get("relevant_to_actor") == "True")
    ]
    page_rows: list[dict[str, Any]] = []
    document_results: list[dict[str, Any]] = []
    text_dir = output_dir / "processed" / "pp_documents_text"

    for row in selected:
        document_id = row.get("document_id", "")
        result: dict[str, Any] = {
            "document_id": document_id,
            "title": row.get("title", ""),
            "pages": 0,
            "characters": 0,
            "pages_needing_ocr": 0,
            "status": "pending",
            "text_path": "",
            "error": "",
        }
        try:
            pdf_path = contained_path(output_dir, row.get("local_path", ""))
            if not pdf_path.is_file():
                raise FileNotFoundError(f"Downloaded PDF is missing: {pdf_path}")
            reader = PdfReader(str(pdf_path), strict=False)
            page_texts: list[str] = []
            for page_number, page in enumerate(reader.pages, start=1):
                error = ""
                try:
                    text = clean_text(page.extract_text())
                    status = "ok" if len(text) >= args.minimum_page_characters else "possibly_needs_ocr"
                except Exception as exc:  # pypdf raises several backend-specific exceptions
                    text = ""
                    status = "extraction_error"
                    error = str(exc)
                if status != "ok":
                    result["pages_needing_ocr"] += 1
                page_texts.append(text)
                page_rows.append(
                    {
                        "document_id": document_id,
                        "source_id": row.get("source_id", ""),
                        "actor_id": row.get("actor_id", ""),
                        "date_hint": row.get("date_hint", ""),
                        "title": row.get("title", ""),
                        "document_url": row.get("document_url", ""),
                        "page_number": page_number,
                        "characters": len(text),
                        "extraction_status": status,
                        "text": text,
                        "error": error,
                    }
                )
            text_path = text_dir / f"{document_id}.txt"
            write_text_document(text_path, row, page_texts)
            result.update(
                pages=len(page_texts),
                characters=sum(len(text) for text in page_texts),
                status="ok" if any(page_texts) else "no_text_extracted",
                text_path=str(text_path.relative_to(output_dir)),
            )
        except Exception as exc:
            result.update(status="error", error=str(exc))
        document_results.append(result)

    pages_csv = output_dir / "processed" / "pp_document_pages.csv"
    pages_csv.parent.mkdir(parents=True, exist_ok=True)
    with pages_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAGE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(page_rows)

    summary = {
        "pipeline": "pp_document_text_extraction",
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": utc_now(),
        "documents_in_inventory": len(documents),
        "documents_selected": len(selected),
        "documents_extracted": sum(row["status"] == "ok" for row in document_results),
        "documents_without_text": sum(row["status"] == "no_text_extracted" for row in document_results),
        "documents_with_errors": sum(row["status"] == "error" for row in document_results),
        "pages_extracted": len(page_rows),
        "pages_possibly_needing_ocr": sum(
            row["extraction_status"] != "ok" for row in page_rows
        ),
        "page_inventory": str(pages_csv.relative_to(output_dir)),
        "text_directory": str(text_dir.relative_to(output_dir)),
        "documents": document_results,
    }
    summary_path = output_dir / "processed" / "pp_document_extraction_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Extracted {len(page_rows)} PDF pages to {pages_csv}")
    print(f"Wrote plain-text documents under {text_dir}")
    print(f"Wrote extraction summary to {summary_path}")
    return 0 if not any(row["status"] == "error" for row in document_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
