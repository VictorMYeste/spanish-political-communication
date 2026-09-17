#!/usr/bin/env python3
"""Extract target-speaker turns from official Congreso full-text pages.

It uses strict, full-surname matching against formal speaker headers.
It avoids assigning speeches by other deputies named Sanchez to Pedro Sanchez,
and avoids treating prose such as "El senor Feijoo ha dicho: ..." as a speaker
header.

Stimuli are pseudonymised, not anonymous: exact speech text is searchable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

# Finds the labels that introduce a speaker turn in the official transcript.
RAW_SPEAKER_MARKER_RE = re.compile(
    r"(?im)^(?:el|la)\s+(?:senor|señor|senora|señora)\s+([^\n:]{1,220}?):\s*"
)
# Finds standalone page-number lines that should be removed from the text.
PAGE_LINE_RE = re.compile(r"(?im)^\s*pagina\s+\d+\s*$|^\s*página\s+\d+\s*$")
# Finds repeated spaces and tabs inside a line.
SPACE_RE = re.compile(r"[ \t]+")
# Finds three or more consecutive line breaks.
BLANKS_RE = re.compile(r"\n{3,}")
# Finds lowercase words after accents and punctuation have been removed.
WORD_RE = re.compile(r"[a-z]+")

# Columns written to the detailed internal segments CSV file.
INTERNAL_FIELDS = [
    "message_id",
    "bundle_id",
    "actor_id",
    "actor_name",
    "party",
    "date",
    "turn_index",
    "segment_index",
    "speaker_header",
    "text",
    "author_role",
    "source_type",
    "source_cve",
    "text_url",
    "pdf_url",
    "retrieved_at_utc",
]

# Columns written to the pseudonymised stimuli CSV file.
STIMULUS_FIELDS = [
    "tweet_id",
    "message_id",
    "bundle_id",
    "text_anon",
    "date",
    "author_role",
    "source_type",
    "topic",
    "keep_for_prompt",
    "is_morally_relevant",
    "dominant_moral_dimension",
    "moral_max",
    "isDeleted",
]


@dataclass(frozen=True)
class TargetSegment:
    """Store one extracted speech segment and its speaker information."""
    politician: Dict[str, object]
    speaker_header: str
    text: str
    turn_index: int


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def accentfold(value: object) -> str:
    """Convert text to lowercase and remove accents."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()


def compact_letters(value: object) -> str:
    """Keep only lowercase letters in normalized text."""
    return re.sub(r"[^a-z]", "", accentfold(value))


def clean_page_text(value: str) -> str:
    """Normalize spacing and remove page-number lines from a transcript."""
    value = value.replace("\xa0", " ").replace("\r", "\n")
    value = PAGE_LINE_RE.sub("", value)
    lines = [SPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return BLANKS_RE.sub("\n\n", "\n".join(lines)).strip()


def full_text_from_html(html: str) -> str:
    """Extract and clean the official transcript text from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("div.textoIntegro p.textoCompleto") or soup.select_one(
        "p.textoCompleto"
    )
    if node is None:
        raise ValueError("Could not find the official full-text container.")
    return clean_page_text(node.get_text(separator="\n"))


def is_formal_speaker_header(header: str) -> bool:
    """Return True only for the uppercase label used for actual speaker turns.

    Official labels have an uppercase core, optionally followed by a mixed-case
    parenthesis, for example ``PRESIDENTE DEL GOBIERNO (Sanchez ...)``. Prose
    mentions have lowercase letters in the core and are rejected.
    """

    header = SPACE_RE.sub(" ", header).strip()
    if not header or len(header) > 180:
        return False
    core = header.split("(", 1)[0].strip()
    letters = [char for char in core if char.isalpha()]
    return bool(letters) and all(not char.islower() for char in letters)


def surname_components(politician: Dict[str, object]) -> Tuple[str, ...]:
    """Build strict surname components from ``congreso_name``.

    ``Sanchez Perez-Castejon, Pedro`` becomes
    ``("sanchez", "perez", "castejon")``. Requiring every component accepts
    harmless punctuation/order variants while rejecting other Sanchez surnames.
    """

    congreso_name = str(politician.get("congreso_name") or "")
    surname_part = congreso_name.split(",", 1)[0]
    components = tuple(token for token in WORD_RE.findall(accentfold(surname_part)) if len(token) > 1)
    if not components:
        raise ValueError(
            f"Missing usable congreso_name for actor {politician.get('actor_id')!r}."
        )
    return components


def identify_actor_strict(
    header: str, politicians: List[Dict[str, object]]
) -> Optional[Dict[str, object]]:
    """Match a speaker header to a politician using all surname components."""
    header_compact = compact_letters(header)
    candidates: List[Tuple[int, Dict[str, object]]] = []
    for politician in politicians:
        components = surname_components(politician)
        if all(component in header_compact for component in components):
            candidates.append((sum(map(len, components)), politician))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def header_mentions_target(
    header: str, politicians: List[Dict[str, object]]
) -> bool:
    """Flag ambiguous headers/mentions for the QA summary only."""

    header_compact = compact_letters(header)
    for politician in politicians:
        aliases = politician.get("marker_aliases") or [politician.get("display_name", "")]
        for alias in aliases:
            alias_compact = compact_letters(alias)
            if alias_compact and alias_compact in header_compact:
                return True
    return False


def extract_target_segments(
    page_text: str,
    politicians: List[Dict[str, object]],
    min_chars: int = 80,
) -> Tuple[List[TargetSegment], Dict[str, object]]:
    """Extract target-politician turns and return parsing diagnostics."""
    raw_markers = list(RAW_SPEAKER_MARKER_RE.finditer(page_text))
    formal_markers = [
        marker for marker in raw_markers if is_formal_speaker_header(marker.group(1))
    ]

    rejected_target_like = Counter(
        SPACE_RE.sub(" ", marker.group(1)).strip()
        for marker in raw_markers
        if not is_formal_speaker_header(marker.group(1))
        and header_mentions_target(marker.group(1), politicians)
    )
    unmatched_formal_collisions: Counter[str] = Counter()
    segments: List[TargetSegment] = []

    for turn_index, marker in enumerate(formal_markers, start=1):
        header = SPACE_RE.sub(" ", marker.group(1)).strip()
        politician = identify_actor_strict(header, politicians)
        if politician is None:
            if header_mentions_target(header, politicians):
                unmatched_formal_collisions[header] += 1
            continue

        end = (
            formal_markers[turn_index].start()
            if turn_index < len(formal_markers)
            else len(page_text)
        )
        text = clean_page_text(page_text[marker.end() : end])
        if len(text) < min_chars:
            continue
        segments.append(
            TargetSegment(
                politician=politician,
                speaker_header=header,
                text=text,
                turn_index=turn_index,
            )
        )

    diagnostics: Dict[str, object] = {
        "raw_speaker_candidates": len(raw_markers),
        "formal_speaker_turns": len(formal_markers),
        "rejected_nonformal_target_like": dict(sorted(rejected_target_like.items())),
        "unmatched_formal_surname_collisions": dict(
            sorted(unmatched_formal_collisions.items())
        ),
    }
    return segments, diagnostics


def document_id(url: str) -> str:
    """Create a stable document identifier from its official URL."""
    query = parse_qs(urlsplit(url).query)
    raw = next(iter(query.get("_intervenciones_id_texto", [])), "")
    if raw:
        cleaned = raw.strip("().").replace(".CODI", "")
        return re.sub(r"[^A-Za-z0-9_-]+", "-", cleaned).strip("-")
    return "doc-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def stable_message_id(
    doc_id: str, actor_id: str, turn_index: int, text: str
) -> str:
    """Create a stable identifier for an extracted speech segment."""
    seed = f"{doc_id}|{actor_id}|{turn_index}|{text[:200]}"
    return "cg1_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def load_json(path: Path) -> List[Dict[str, object]]:
    """Load and validate the target politicians configuration."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON list.")
    for politician in payload:
        surname_components(politician)
    return payload


def load_inventory(path: Path) -> List[Dict[str, str]]:
    """Load the intervention inventory from a CSV file."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def fetch_cached_html(
    session: requests.Session,
    url: str,
    cache_dir: Path,
    delay: float,
) -> Tuple[str, str]:
    """Return cached HTML or download and store it for future runs."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{document_id(url)}.html"
    if cache_path.exists():
        cached_at = datetime.fromtimestamp(
            cache_path.stat().st_mtime, timezone.utc
        ).replace(microsecond=0).isoformat()
        return cache_path.read_text(encoding="utf-8"), cached_at

    response = session.get(url, timeout=(15, 120))
    response.raise_for_status()
    cache_path.write_text(response.text, encoding="utf-8")
    retrieved_at = utc_now()
    if delay:
        time.sleep(delay)
    return response.text, retrieved_at


def write_csv(
    path: Path, fields: List[str], rows: Iterable[Dict[str, object]]
) -> None:
    """Write rows to a CSV file using the specified columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def merge_counter(target: Counter[str], values: Dict[str, object]) -> None:
    """Add a dictionary of counts to an existing Counter."""
    for key, value in values.items():
        target[key] += int(value)


def build_outputs(
    inventory: List[Dict[str, str]],
    politicians: List[Dict[str, object]],
    session: requests.Session,
    cache_dir: Path,
    delay: float,
    min_chars: int,
    max_documents: Optional[int] = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Dict[str, object]]:
    """Process all documents and build internal, stimulus, and QA outputs."""
    by_url: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in inventory:
        if row.get("text_url"):
            by_url[row["text_url"]].append(row)

    urls = sorted(by_url)
    if max_documents is not None:
        urls = urls[:max_documents]

    internal_rows: List[Dict[str, object]] = []
    errors: List[Dict[str, str]] = []
    documents_with_no_target_segments: List[str] = []
    count_mismatches: List[Dict[str, object]] = []
    rejected_target_like: Counter[str] = Counter()
    formal_collisions: Counter[str] = Counter()
    actor_header_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    formal_turns_seen = 0
    raw_candidates_seen = 0

    for position, url in enumerate(urls, start=1):
        try:
            html, retrieved_at = fetch_cached_html(session, url, cache_dir, delay)
            page_text = full_text_from_html(html)
            segments, diagnostics = extract_target_segments(
                page_text, politicians, min_chars=min_chars
            )
        except Exception as exc:
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
            continue

        formal_turns_seen += int(diagnostics["formal_speaker_turns"])
        raw_candidates_seen += int(diagnostics["raw_speaker_candidates"])
        merge_counter(
            rejected_target_like,
            dict(diagnostics["rejected_nonformal_target_like"]),
        )
        merge_counter(
            formal_collisions,
            dict(diagnostics["unmatched_formal_surname_collisions"]),
        )

        if not segments:
            documents_with_no_target_segments.append(url)
            continue

        doc_id = document_id(url)
        representative = by_url[url][0]
        expected_counts: Counter[str] = Counter(
            metadata_row["actor_id"] for metadata_row in by_url[url]
        )
        extracted_counts: Counter[str] = Counter(
            str(segment.politician["actor_id"]) for segment in segments
        )
        if dict(expected_counts) != dict(extracted_counts):
            count_mismatches.append(
                {
                    "source_cve": doc_id,
                    "expected_from_open_data": dict(sorted(expected_counts.items())),
                    "extracted_from_full_text": dict(sorted(extracted_counts.items())),
                }
            )

        actor_occurrence: Counter[str] = Counter()
        for segment in segments:
            politician = segment.politician
            actor_id = str(politician["actor_id"])
            actor_occurrence[actor_id] += 1
            actor_header_counts[actor_id][segment.speaker_header] += 1
            message_id = stable_message_id(
                doc_id, actor_id, segment.turn_index, segment.text
            )
            internal_rows.append(
                {
                    "message_id": message_id,
                    "bundle_id": doc_id,
                    "actor_id": actor_id,
                    "actor_name": str(politician["display_name"]),
                    "party": str(politician["party"]),
                    "date": representative.get("date", ""),
                    "turn_index": segment.turn_index,
                    "segment_index": actor_occurrence[actor_id],
                    "speaker_header": segment.speaker_header,
                    "text": segment.text,
                    "author_role": "a Spanish political leader speaking in Parliament",
                    "source_type": "congress_intervention",
                    "source_cve": doc_id,
                    "text_url": url,
                    "pdf_url": representative.get("pdf_url", ""),
                    "retrieved_at_utc": retrieved_at,
                }
            )

        qa = "count-match" if dict(expected_counts) == dict(extracted_counts) else "count-mismatch"
        print(
            f"[{position}/{len(urls)}] {doc_id}: "
            f"{len(segments)} target segments ({qa})"
        )

    internal_rows.sort(
        key=lambda row: (
            str(row["date"]),
            str(row["bundle_id"]),
            int(row["turn_index"]),
        )
    )
    stimuli_rows = [
        {
            "tweet_id": row["message_id"],
            "message_id": row["message_id"],
            "bundle_id": row["bundle_id"],
            "text_anon": row["text"],
            "date": row["date"],
            "author_role": row["author_role"],
            "source_type": row["source_type"],
            "topic": "",
            "keep_for_prompt": "False",
            "is_morally_relevant": "",
            "dominant_moral_dimension": "",
            "moral_max": "",
            "isDeleted": "False",
        }
        for row in internal_rows
    ]

    summary: Dict[str, object] = {
        "parser_version": 1,
        "matching_method": "formal uppercase header plus all canonical surname components",
        "documents_considered": len(urls),
        "documents_failed": len(errors),
        "documents_without_target_segments": len(documents_with_no_target_segments),
        "documents_with_count_mismatch": len(count_mismatches),
        "raw_speaker_candidates_seen": raw_candidates_seen,
        "formal_speaker_turns_seen": formal_turns_seen,
        "segments_extracted": len(internal_rows),
        "segments_by_actor": dict(
            sorted(Counter(str(row["actor_id"]) for row in internal_rows).items())
        ),
        "speaker_headers_by_actor": {
            actor_id: dict(sorted(counts.items()))
            for actor_id, counts in sorted(actor_header_counts.items())
        },
        "rejected_nonformal_target_like_headers": dict(
            sorted(rejected_target_like.items())
        ),
        "unmatched_formal_surname_collision_headers": dict(
            sorted(formal_collisions.items())
        ),
        "errors": errors,
        "count_mismatches": count_mismatches,
        "no_target_segment_urls": documents_with_no_target_segments,
        "note": (
            "Stimuli are pseudonymised, not anonymous; exact text remains "
            "searchable. Count mismatches compare Open Data index entries with "
            "all formal turns in the full Diario and are QA warnings, not "
            "automatic extraction failures."
        ),
    }
    return internal_rows, stimuli_rows, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("data/cache/congreso_text")
    )
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--min-chars", type=int, default=80)
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--contact-email")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory = load_inventory(args.inventory)
    politicians = load_json(args.config)
    session = requests.Session()
    contact = f"; contact={args.contact_email}" if args.contact_email else ""
    session.headers.update(
        {"User-Agent": f"academic-congreso-corpus/0.1 ({contact})"}
    )

    internal_rows, stimuli_rows, summary = build_outputs(
        inventory,
        politicians,
        session,
        args.cache_dir,
        delay=max(args.delay, 0.0),
        min_chars=max(args.min_chars, 0),
        max_documents=args.max_documents,
    )
    write_csv(
        args.output_dir / "congreso_segments_internal.csv",
        INTERNAL_FIELDS,
        internal_rows,
    )
    write_csv(
        args.output_dir / "congreso_stimuli.csv",
        STIMULUS_FIELDS,
        stimuli_rows,
    )
    with (
        args.output_dir / "congreso_extraction_summary.json"
    ).open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if summary["documents_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
