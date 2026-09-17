#!/usr/bin/env python3
"""Build a reproducible inventory of selected Congreso interventions.

The script only uses the official chronological Open Data JSON. It discovers
the dated download URL from the stable landing page, stores the raw snapshot
with a SHA-256 digest, filters locally, and writes a small deduplicated CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests

try:
    import ijson
except ImportError:  # pragma: no cover - exercised only in minimal installs
    ijson = None

# Official page containing the current Open Data download link.
LANDING_URL = "https://www.congreso.es/es/opendata/intervenciones"
# Official legal notice describing the conditions for reusing the data.
LICENSE_URL = "https://www.congreso.es/es/cem/aviso-legal"
# Pattern used to find chronological JSON download links in the official page.
JSON_LINK_RE = re.compile(
    r"href=[\"'](?P<href>[^\"']*IntervencionesCronologicamente__\d+\.json)[\"']",
    re.IGNORECASE,
)
# Pattern used to extract the unique intervention ID from a video URL.
UTTERANCE_ID_RE = re.compile(r"https://app\.congreso\.es/v1/(\d+)I", re.IGNORECASE)

# Columns written to the deduplicated interventions CSV file.
OUTPUT_FIELDS = [
    "source_record_key",
    "actor_id",
    "actor_name",
    "party",
    "date",
    "start_time",
    "end_time",
    "legislature",
    "chamber",
    "body",
    "speaker_role",
    "phase",
    "intervention_type",
    "initiative_subjects_json",
    "initiative_types_json",
    "case_numbers_json",
    "initiative_authors_json",
    "text_url",
    "text_anchor",
    "pdf_url",
    "video_page_url",
    "video_download_url",
    "source_snapshot_url",
    "source_snapshot_sha256",
    "retrieved_at_utc",
    "license_url",
]

# Columns written to the detailed provenance CSV file.
PROVENANCE_FIELDS = [
    "source_record_key",
    "actor_id",
    "actor_name",
    "party",
    "raw_speaker",
    "date",
    "start_time",
    "end_time",
    "legislature",
    "chamber",
    "body",
    "speaker_role",
    "phase",
    "intervention_type",
    "super_initiative_type",
    "initiative_type",
    "initiative_subject",
    "case_number",
    "initiative_author",
    "text_url",
    "text_anchor",
    "pdf_url",
    "video_page_url",
    "video_download_url",
    "source_snapshot_url",
    "source_snapshot_sha256",
    "retrieved_at_utc",
    "license_url",
]


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    """Calculate the SHA-256 checksum of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_session(contact_email: Optional[str] = None) -> requests.Session:
    """Create an HTTP session that identifies the research project."""
    session = requests.Session()
    contact = f" (contact: {contact_email})" if contact_email else ""
    session.headers.update(
        {
            "User-Agent": f"academic-congreso-corpus/0.1{contact}",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.5",
        }
    )
    return session


def discover_latest_json(session: requests.Session, landing_url: str) -> str:
    """Find the most recent chronological JSON snapshot on the Open Data page."""
    response = session.get(landing_url, timeout=(15, 60))
    response.raise_for_status()
    matches = [match.group("href") for match in JSON_LINK_RE.finditer(response.text)]
    if not matches:
        raise RuntimeError(
            "The official page did not expose a chronological JSON link. "
            "Check whether the Congreso page structure changed."
        )
    # The timestamp is embedded in the filename, so lexical max is the newest.
    return urljoin(landing_url, max(matches))


def download_snapshot(
    session: requests.Session,
    url: str,
    raw_dir: Path,
    force: bool = False,
) -> Tuple[Path, str, str]:
    """Download or reuse a snapshot and return its path, checksum, and time."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(urlsplit(url).path).name
    destination = raw_dir / filename
    retrieved_at = utc_now()

    if destination.exists() and not force:
        return destination, sha256_file(destination), retrieved_at

    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    with session.get(url, stream=True, timeout=(15, 300)) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
    partial.replace(destination)
    return destination, digest.hexdigest(), retrieved_at


def iter_records(path: Path) -> Iterator[Dict[str, object]]:
    """Yield JSON records, streaming them when ijson is available."""
    if ijson is not None:
        with path.open("rb") as handle:
            yield from ijson.items(handle, "item")
        return

    print(
        "[WARN] ijson is not installed; loading the complete JSON in memory.",
        file=sys.stderr,
    )
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Expected a top-level JSON array.")
    yield from payload


def normalize_speaker(value: object) -> str:
    """Normalize a speaker name so it can be matched reliably."""
    text = str(value or "").strip()
    # Congreso appends the parliamentary group, e.g. " (GS)" or " (GP)".
    return re.sub(r"\s+\([^()]+\)\s*$", "", text).casefold()


def parse_congreso_date(value: object) -> Optional[str]:
    """Convert a Congreso date from DD/MM/YYYY to YYYY-MM-DD."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def strip_fragment(url: object) -> str:
    """Remove the fragment from a URL."""
    return str(url or "").split("#", 1)[0]


def get_fragment(url: object) -> str:
    """Return the fragment of a URL, if present."""
    text = str(url or "")
    return text.split("#", 1)[1] if "#" in text else ""


def stable_record_key(record: Dict[str, object]) -> str:
    """Create a stable identifier for a source record."""
    deferred_url = str(record.get("ENLACEDIFERIDO") or "").strip()
    match = UTTERANCE_ID_RE.search(deferred_url)
    if match:
        return f"congreso:utterance:{match.group(1)}"

    for field in ("ENLACEDIFERIDO", "ENLACEDESCARGADIRECTA"):
        value = str(record.get(field) or "").strip()
        if value:
            return f"{field.lower()}:{value}"

    fallback = "|".join(
        str(record.get(field) or "").strip()
        for field in (
            "ORADOR",
            "SESION",
            "INICIOINTERVENCION",
            "FININTERVENCION",
            "ENLACEPDF",
        )
    )
    return "fallback:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()


def duration_minutes(start: object, end: object) -> Optional[int]:
    """Calculate the duration in minutes between two HH:MM times."""
    try:
        start_time = datetime.strptime(str(start), "%H:%M")
        end_time = datetime.strptime(str(end), "%H:%M")
    except (TypeError, ValueError):
        return None
    minutes = int((end_time - start_time).total_seconds() // 60)
    if minutes < 0:
        minutes += 24 * 60
    return minutes


def load_politicians(path: Path) -> List[Dict[str, object]]:
    """Load and validate the target politicians configuration."""
    with path.open("r", encoding="utf-8") as handle:
        politicians = json.load(handle)
    if not isinstance(politicians, list) or not politicians:
        raise ValueError("Politician config must be a non-empty JSON list.")
    required = {"actor_id", "display_name", "congreso_name", "party"}
    for politician in politicians:
        missing = required.difference(politician)
        if missing:
            raise ValueError(f"Politician config is missing: {sorted(missing)}")
    return politicians


def select_records(
    records: Iterable[Dict[str, object]],
    politicians: List[Dict[str, object]],
    snapshot_url: str,
    snapshot_sha256: str,
    retrieved_at: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], Dict[str, object]]:
    """Filter target speakers and build records, provenance, and a summary."""
    speaker_lookup = {
        normalize_speaker(politician["congreso_name"]): politician
        for politician in politicians
    }
    selected: Dict[Tuple[str, str], Dict[str, str]] = {}
    provenance_rows: List[Dict[str, str]] = []
    total_records = 0
    matched_before_dedup = Counter()
    invalid_dates = 0

    for record in records:
        total_records += 1
        politician = speaker_lookup.get(normalize_speaker(record.get("ORADOR")))
        if politician is None:
            continue

        date_iso = parse_congreso_date(record.get("SESION"))
        if date_iso is None:
            invalid_dates += 1
            continue
        if start_date and date_iso < start_date:
            continue
        if end_date and date_iso > end_date:
            continue

        actor_id = str(politician["actor_id"])
        matched_before_dedup[actor_id] += 1
        record_key = stable_record_key(record)
        dedup_key = (actor_id, record_key)

        provenance_rows.append(
            {
                "source_record_key": record_key,
                "actor_id": actor_id,
                "actor_name": str(politician["display_name"]),
                "party": str(politician["party"]),
                "raw_speaker": str(record.get("ORADOR") or ""),
                "date": date_iso,
                "start_time": str(record.get("INICIOINTERVENCION") or ""),
                "end_time": str(record.get("FININTERVENCION") or ""),
                "legislature": str(record.get("LEGISLATURA") or ""),
                "chamber": "Congreso de los Diputados",
                "body": str(record.get("ORGANO") or ""),
                "speaker_role": str(record.get("CARGOORADOR") or ""),
                "phase": str(record.get("FASE") or ""),
                "intervention_type": str(record.get("TIPOINTERVENCION") or ""),
                "super_initiative_type": str(record.get("SUPERTIPOINICIATIVA") or ""),
                "initiative_type": str(record.get("TIPOINICIATIVA") or ""),
                "initiative_subject": str(record.get("OBJETOINICIATIVA") or ""),
                "case_number": str(record.get("NUMEXPEDIENTE") or ""),
                "initiative_author": str(record.get("AUTOR") or ""),
                "text_url": strip_fragment(record.get("ENLACETEXTOINTEGRO")),
                "text_anchor": get_fragment(record.get("ENLACETEXTOINTEGRO")),
                "pdf_url": strip_fragment(record.get("ENLACEPDF")),
                "video_page_url": str(record.get("ENLACEDIFERIDO") or ""),
                "video_download_url": str(record.get("ENLACEDESCARGADIRECTA") or ""),
                "source_snapshot_url": snapshot_url,
                "source_snapshot_sha256": snapshot_sha256,
                "retrieved_at_utc": retrieved_at,
                "license_url": LICENSE_URL,
            }
        )

        if dedup_key not in selected:
            selected[dedup_key] = {
                "source_record_key": record_key,
                "actor_id": actor_id,
                "actor_name": str(politician["display_name"]),
                "party": str(politician["party"]),
                "date": date_iso,
                "start_time": str(record.get("INICIOINTERVENCION") or ""),
                "end_time": str(record.get("FININTERVENCION") or ""),
                "legislature": str(record.get("LEGISLATURA") or ""),
                "chamber": "Congreso de los Diputados",
                "body": str(record.get("ORGANO") or ""),
                "speaker_role": str(record.get("CARGOORADOR") or ""),
                "phase": str(record.get("FASE") or ""),
                "intervention_type": str(record.get("TIPOINTERVENCION") or ""),
                "initiative_subjects_json": [],
                "initiative_types_json": [],
                "case_numbers_json": [],
                "initiative_authors_json": [],
                "text_url": strip_fragment(record.get("ENLACETEXTOINTEGRO")),
                "text_anchor": get_fragment(record.get("ENLACETEXTOINTEGRO")),
                "pdf_url": strip_fragment(record.get("ENLACEPDF")),
                "video_page_url": str(record.get("ENLACEDIFERIDO") or ""),
                "video_download_url": str(record.get("ENLACEDESCARGADIRECTA") or ""),
                "source_snapshot_url": snapshot_url,
                "source_snapshot_sha256": snapshot_sha256,
                "retrieved_at_utc": retrieved_at,
                "license_url": LICENSE_URL,
            }

        aggregate = selected[dedup_key]
        for output_field, source_field in (
            ("initiative_subjects_json", "OBJETOINICIATIVA"),
            ("initiative_types_json", "TIPOINICIATIVA"),
            ("case_numbers_json", "NUMEXPEDIENTE"),
            ("initiative_authors_json", "AUTOR"),
        ):
            value = str(record.get(source_field) or "").strip()
            if value and value not in aggregate[output_field]:
                aggregate[output_field].append(value)

    output = list(selected.values())
    for row in output:
        for field in (
            "initiative_subjects_json",
            "initiative_types_json",
            "case_numbers_json",
            "initiative_authors_json",
        ):
            row[field] = json.dumps(row[field], ensure_ascii=False)

    output = sorted(
        output,
        key=lambda row: (row["date"], row["start_time"], row["actor_id"], row["source_record_key"]),
    )
    unique_by_actor = Counter(row["actor_id"] for row in output)
    unique_text_documents = len({row["text_url"] for row in output if row["text_url"]})
    duration_by_actor = Counter()
    date_range_by_actor: Dict[str, List[str]] = defaultdict(list)
    for row in output:
        minutes = duration_minutes(row["start_time"], row["end_time"])
        if minutes is not None:
            duration_by_actor[row["actor_id"]] += minutes
        date_range_by_actor[row["actor_id"]].append(row["date"])

    summary: Dict[str, object] = {
        "source_snapshot_url": snapshot_url,
        "source_snapshot_sha256": snapshot_sha256,
        "retrieved_at_utc": retrieved_at,
        "total_source_rows": total_records,
        "invalid_dates_among_matching_rows": invalid_dates,
        "matched_rows_before_deduplication": dict(sorted(matched_before_dedup.items())),
        "unique_interventions": dict(sorted(unique_by_actor.items())),
        "unique_interventions_total": len(output),
        "unique_full_text_documents": unique_text_documents,
        "approximate_duration_hours": {
            actor_id: round(minutes / 60, 2)
            for actor_id, minutes in sorted(duration_by_actor.items())
        },
        "date_range_by_actor": {
            actor_id: {"start": min(dates), "end": max(dates)}
            for actor_id, dates in sorted(date_range_by_actor.items())
        },
        "date_filter": {"start": start_date, "end": end_date},
    }
    provenance_rows.sort(
        key=lambda row: (row["date"], row["start_time"], row["actor_id"], row["source_record_key"])
    )
    return output, provenance_rows, summary


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    """Write the selected intervention records to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def validate_iso_date(value: Optional[str]) -> Optional[str]:
    """Validate an optional command-line date in YYYY-MM-DD format."""
    if value is None:
        return None
    datetime.strptime(value, "%Y-%m-%d")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--landing-url", default=LANDING_URL)
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Process a local official JSON snapshot instead of downloading the current one.",
    )
    parser.add_argument("--snapshot-url", help="Provenance URL for --snapshot.")
    parser.add_argument("--start", type=validate_iso_date)
    parser.add_argument("--end", type=validate_iso_date)
    parser.add_argument("--contact-email")
    parser.add_argument("--force-download", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    politicians = load_politicians(args.config)
    session = build_session(args.contact_email)

    if args.snapshot:
        snapshot_path = args.snapshot.resolve()
        if not snapshot_path.exists():
            raise FileNotFoundError(snapshot_path)
        snapshot_url = args.snapshot_url or "local-official-snapshot"
        snapshot_sha256 = sha256_file(snapshot_path)
        retrieved_at = utc_now()
    else:
        snapshot_url = discover_latest_json(session, args.landing_url)
        snapshot_path, snapshot_sha256, retrieved_at = download_snapshot(
            session,
            snapshot_url,
            args.output_dir / "raw" / "congreso",
            force=args.force_download,
        )

    rows, provenance_rows, summary = select_records(
        iter_records(snapshot_path),
        politicians,
        snapshot_url=snapshot_url,
        snapshot_sha256=snapshot_sha256,
        retrieved_at=retrieved_at,
        start_date=args.start,
        end_date=args.end,
    )
    processed_dir = args.output_dir / "processed"
    write_csv(processed_dir / "congreso_interventions.csv", rows)
    with (processed_dir / "congreso_intervention_links.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=PROVENANCE_FIELDS)
        writer.writeheader()
        writer.writerows(provenance_rows)
    processed_dir.mkdir(parents=True, exist_ok=True)
    with (processed_dir / "congreso_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
