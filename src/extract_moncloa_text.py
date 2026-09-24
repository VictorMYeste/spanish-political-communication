#!/usr/bin/env python3
"""Extract auditable text records from La Moncloa pages.

The script stores the complete cleaned text
only in an internal table and creates downstream segments only when the source,
politician and speaker boundaries can be verified with reasonable confidence.
It does not download audio/video, run ASR, or bypass access controls.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# A version is written to the summary so that later collections can identify
# which extraction rules produced them.
PIPELINE_VERSION = "1.1.0"

MONCLOA_SOURCE_ID = "lamoncloa_president_interventions"

# The user agent identifies an academic/research collector.  An institutional
# contact can be appended with --contact-email.
USER_AGENT_NAME = "SpanishPoliticalCommunicationResearchBot/1.0"

# Network and cache defaults.  Requests are sequential and separated by a
# delay to avoid placing unnecessary load on the source websites.
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_MIN_TEXT_CHARS = 280
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_REDIRECTS = 5

# Tracking parameters do not identify a different document and are removed
# when constructing canonical URLs and stable identifiers.
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}

# Spanish month names used by official and party websites.
SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Standard labels found in Spanish interview transcripts.  "Repuesta" is a
# recurring source typo and is intentionally accepted.  The original Spanish
# text itself is never de-accented or otherwise rewritten.
SPEAKER_MARKER_RE = re.compile(
    r"(?<!\w)(?:(?P<long>Pregunta|Respuesta|Repuesta)\s*"
    r"(?::|\.\s*[-–—]|[-–—])|(?P<short>[PR])\s*"
    r"(?::|\.\s*[-–—]|[-–—]))\s*",
    flags=re.IGNORECASE,
)

# Some La Moncloa interviews label journalists by name instead of using
# ``Pregunta:`` (for example, ``El Gran Wyoming:``).  Such a label must end the
# preceding answer; otherwise the journalist's next question would be appended
# to the politician's text.
NAMED_SPEAKER_HEADER_RE = re.compile(
    r"^(?P<label>[^:\n]{2,80}?)\s*(?::|\.\s*[-–—])\s*(?P<content>.*)$"
)
LOWERCASE_NAME_WORDS = {
    "a",
    "al",
    "de",
    "del",
    "e",
    "el",
    "en",
    "i",
    "la",
    "las",
    "los",
    "y",
}

# Titles matching these patterns are parliamentary interventions.  They are
# excluded here because the repository has a separate Congreso pipeline.
PARLIAMENTARY_TITLE_RE = re.compile(
    r"\b(congreso de los diputados|pleno del congreso|senado|"
    r"sesion de control|debate de investidura|comparecencia ante (?:el )?congreso)\b",
    flags=re.IGNORECASE,
)

# Common non-content elements removed before body text is extracted.
DEFAULT_REMOVE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "form",
    "button",
    "aside",
    ".cookie",
    ".cookies",
    ".share",
    ".sharing",
    ".social",
    ".related",
    ".newsletter",
    ".breadcrumb",
]

# CSV schemas are fixed and explicit to keep outputs comparable across runs.
INVENTORY_FIELDS = [
    "document_id",
    "candidate_id",
    "source_id",
    "source_name",
    "publisher",
    "publisher_type",
    "actor_id",
    "actor_name",
    "party",
    "actor_verification",
    "document_type",
    "document_status",
    "status_reason",
    "title",
    "title_source",
    "publication_date",
    "date_source",
    "location",
    "language",
    "canonical_url",
    "source_url",
    "requested_url",
    "final_url",
    "discovery_url",
    "discovery_method",
    "discovery_page",
    "discovered_at_utc",
    "listing_cache_path",
    "listing_sha256",
    "retrieved_at_utc",
    "adapter",
    "body_selector",
    "character_count",
    "word_count",
    "segment_count",
    "content_sha256",
    "raw_sha256",
    "is_exact_duplicate",
    "duplicate_of_document_id",
    "manual_review_required",
    "cache_hit",
    "cache_path",
    "http_status",
    "legal_notice_url",
    "required_attribution",
    "redistribution_status",
    "third_party_material_possible",
]

DOCUMENT_FIELDS = INVENTORY_FIELDS + ["text", "text_scope"]

SEGMENT_FIELDS = [
    "segment_id",
    "message_id",
    "bundle_id",
    "document_id",
    "candidate_id",
    "actor_id",
    "actor_name",
    "party",
    "publication_date",
    "source_id",
    "source_name",
    "source_type",
    "document_type",
    "segment_index",
    "speaker_role",
    "text",
    "character_count",
    "word_count",
    "canonical_url",
    "content_sha256",
    "extraction_method",
    "redistribution_status",
    "manual_review_required",
    "keep_for_prompt",
]

# This matches the downstream persona-stimulus schema already used by the
# project.  All new rows remain disabled until substantive manual review.
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

AUDIT_FIELDS = [
    "candidate_id",
    "document_id",
    "source_id",
    "canonical_url",
    "status",
    "reason",
    "actor_verification",
    "document_type",
    "publication_date",
    "character_count",
    "segment_count",
    "cache_hit",
    "http_status",
    "error_type",
    "error_message",
]


@dataclass
class FetchResult:
    """HTML and provenance returned by a cached HTTP request."""

    html: str
    requested_url: str
    final_url: str
    retrieved_at_utc: str
    http_status: int
    cache_hit: bool
    cache_path: str
    raw_sha256: str


@dataclass
class PageExtraction:
    """Normalized metadata and body extracted from one HTML page."""

    title: str
    title_source: str
    publication_date: str
    date_source: str
    location: str
    canonical_url: str
    body_text: str
    body_blocks: List[str]
    body_selector: str
    has_video: bool
    jsonld: Dict[str, Any]


class PoliteFetcher:
    """Fetch pages sequentially while keeping an auditable local cache."""

    def __init__(
        self,
        cache_dir: Path,
        delay_seconds: float,
        contact_email: str = "",
        offline: bool = False,
        refresh: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.delay_seconds = max(0.0, delay_seconds)
        self.offline = offline
        self.refresh = refresh
        self.last_request_time = 0.0
        self.robots: Dict[str, RobotFileParser] = {}
        self.user_agent = USER_AGENT_NAME
        if contact_email:
            self.user_agent += f" (contact: {contact_email.strip()})"

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es,en;q=0.7",
            }
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _paths(self, source_id: str, url: str) -> Tuple[Path, Path]:
        """Return deterministic HTML and metadata cache paths."""

        key = sha256_text(canonicalize_url(url))
        source_part = safe_filename(source_id or "unknown_source")
        html_path = self.cache_dir / source_part / f"{key}.html"
        return html_path, html_path.with_suffix(".json")

    def _read_cache(self, source_id: str, url: str) -> Optional[FetchResult]:
        """Read a cached response when both body and sidecar are present."""

        html_path, meta_path = self._paths(source_id, url)
        if not html_path.exists() or not meta_path.exists():
            return None
        try:
            metadata = load_json(meta_path)
            # Read the exact UTF-8 bytes written to disk.  Text-mode I/O can
            # translate newlines on Windows and make a valid cache entry look
            # different from the text that was originally checksummed.
            cached_bytes = html_path.read_bytes()
            html = cached_bytes.decode("utf-8")
            expected_cache_hash = str(metadata.get("cache_text_sha256") or "")
            if (
                expected_cache_hash
                and hashlib.sha256(cached_bytes).hexdigest() != expected_cache_hash
            ):
                # Older Windows runs wrote the decoded page in text mode.
                # Recover the two newline transformations that this may have
                # introduced, and migrate a matching entry to byte-mode cache.
                compatibility_candidates = [
                    html.replace("\r\r\n", "\r\n"),
                    html.replace("\r\n", "\n"),
                    html.replace("\r\r\n", "\r\n").replace("\r\n", "\n"),
                ]
                recovered = next(
                    (
                        candidate
                        for candidate in compatibility_candidates
                        if sha256_text(candidate) == expected_cache_hash
                    ),
                    None,
                )
                if recovered is None:
                    raise ValueError("cached HTML checksum mismatch")
                html = recovered
                atomic_write_text(html_path, html)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            # An interrupted or old incompatible cache entry is treated as a
            # cache miss.  A normal online run will download and replace it.
            return None
        return FetchResult(
            html=html,
            requested_url=str(metadata.get("requested_url") or url),
            final_url=str(metadata.get("final_url") or url),
            retrieved_at_utc=str(metadata.get("retrieved_at_utc") or ""),
            http_status=int(metadata.get("http_status") or 200),
            cache_hit=True,
            cache_path=str(Path(source_id or "unknown_source") / html_path.name),
            raw_sha256=str(metadata.get("raw_sha256") or sha256_text(html)),
        )

    def _wait(self) -> None:
        """Enforce the configured delay between live requests."""

        elapsed = time.monotonic() - self.last_request_time
        remaining = self.delay_seconds - elapsed
        if self.last_request_time and remaining > 0:
            time.sleep(remaining)

    def _robots_allowed(self, url: str, allowed_hosts: Sequence[str]) -> bool:
        """Check robots.txt and fail closed when its policy cannot be verified."""

        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robots:
            return self.robots[origin].can_fetch(USER_AGENT_NAME, url)

        parser = RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        parser.set_url(robots_url)
        try:
            redirects = 0
            while True:
                self._wait()
                response = self.session.get(
                    robots_url,
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
                self.last_request_time = time.monotonic()
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = normalize_space(response.headers.get("Location"))
                    response.close()
                    redirects += 1
                    if not location or redirects > MAX_REDIRECTS:
                        raise PermissionError(f"Could not verify robots.txt for {origin}")
                    robots_url = canonicalize_url(location, robots_url)
                    if not url_allowed_for_hosts(robots_url, allowed_hosts):
                        raise PermissionError(
                            f"robots.txt redirected outside the allowed hosts: {robots_url}"
                        )
                    continue
                if response.status_code in {404, 410}:
                    parser.parse([])
                elif response.status_code != 200:
                    raise PermissionError(
                        f"Could not verify robots.txt for {origin}: HTTP {response.status_code}"
                    )
                else:
                    parser.parse(response.text.splitlines())
                response.close()
                break
        except requests.RequestException as exc:
            raise PermissionError(
                f"Could not verify robots.txt for {origin}: {exc}"
            ) from exc
        self.robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    def fetch(
        self,
        source_id: str,
        url: str,
        allowed_hosts: Sequence[str],
    ) -> FetchResult:
        """Return cached HTML or safely download one public page.

        Redirects are followed manually so a source page cannot send the
        collector to an unconfigured domain or to a path disallowed by that
        host's robots policy.
        """

        cached = self._read_cache(source_id, url)
        if cached is not None and (self.offline or not self.refresh):
            if not url_allowed_for_hosts(cached.final_url, allowed_hosts):
                raise PermissionError(
                    f"Cached redirect target is outside the allowed hosts: {cached.final_url}"
                )
            return cached
        if self.offline:
            raise FileNotFoundError(f"No cached copy is available for {url}")
        current_url = url
        redirects = 0
        while True:
            if not url_allowed_for_hosts(current_url, allowed_hosts):
                raise PermissionError(f"Redirect target is outside the allowed hosts: {current_url}")
            if not self._robots_allowed(current_url, allowed_hosts):
                raise PermissionError(f"robots.txt does not allow collection of {current_url}")

            self._wait()
            response = self.session.get(
                current_url,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            self.last_request_time = time.monotonic()
            if response.status_code in {301, 302, 303, 307, 308}:
                location = normalize_space(response.headers.get("Location"))
                response.close()
                if not location:
                    raise ValueError("Redirect response has no Location header")
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise ValueError("Too many redirects")
                current_url = canonicalize_url(location, current_url)
                continue
            break

        response.raise_for_status()
        content_length = normalize_space(response.headers.get("Content-Length"))
        if content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
            response.close()
            raise ValueError(f"Response exceeds the {MAX_RESPONSE_BYTES}-byte safety limit")
        chunks: List[bytes] = []
        downloaded = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > MAX_RESPONSE_BYTES:
                response.close()
                raise ValueError(f"Response exceeds the {MAX_RESPONSE_BYTES}-byte safety limit")
            chunks.append(chunk)
        raw_body = b"".join(chunks)
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type and "xhtml" not in content_type:
            response.close()
            raise ValueError(f"Expected HTML but received {content_type or 'unknown type'}")

        encoding = response.encoding or ""
        if not encoding:
            header_match = re.search(
                br"charset\s*=\s*['\"]?([^'\"\s/>;]+)", raw_body[:4096], re.I
            )
            encoding = (
                header_match.group(1).decode("ascii", errors="ignore")
                if header_match
                else "utf-8"
            )
        try:
            html = raw_body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            html = raw_body.decode("utf-8", errors="replace")
        retrieved_at = utc_now()
        html_path, meta_path = self._paths(source_id, url)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        raw_hash = hashlib.sha256(raw_body).hexdigest()
        atomic_write_text(html_path, html)
        atomic_write_json(
            meta_path,
            {
                "requested_url": url,
                "final_url": response.url,
                "retrieved_at_utc": retrieved_at,
                "http_status": response.status_code,
                "raw_sha256": raw_hash,
                "cache_text_sha256": sha256_text(html),
                "content_type": content_type,
            },
        )
        final_url = canonicalize_url(response.url or current_url)
        response.close()
        return FetchResult(
            html=html,
            requested_url=url,
            final_url=final_url,
            retrieved_at_utc=retrieved_at,
            http_status=response.status_code,
            cache_hit=False,
            cache_path=str(Path(source_id or "unknown_source") / html_path.name),
            raw_sha256=raw_hash,
        )


def utc_now() -> str:
    """Return the current UTC timestamp in a stable ISO representation."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_space(value: Any) -> str:
    """Collapse whitespace without altering accents or punctuation."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_multiline(value: Any) -> str:
    """Normalize each line while preserving meaningful paragraph breaks."""

    lines = [normalize_space(line) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def fold_for_matching(value: Any) -> str:
    """Create an accent-free lowercase copy used only for matching rules."""

    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def sha256_text(value: str) -> str:
    """Calculate a SHA-256 hexadecimal digest for UTF-8 text."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any, length: int = 20) -> str:
    """Build a stable identifier from source-controlled input values."""

    joined = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}_{sha256_text(joined)[:length]}"


def safe_filename(value: str) -> str:
    """Convert a source identifier to a safe cache-directory name."""

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "source"


def canonicalize_url(url: str, base_url: str = "") -> str:
    """Resolve a public URL and remove fragments and tracking parameters."""

    absolute = urljoin(base_url, normalize_space(url)) if base_url else normalize_space(url)
    parts = urlsplit(absolute)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    if parts.username or parts.password:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    if port not in {None, 80, 443}:
        return ""
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        low_key = key.lower()
        if low_key.startswith("utm_") or low_key in TRACKING_QUERY_KEYS:
            continue
        query.append((key, value))
    query.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    host = parts.hostname.lower().rstrip(".")
    netloc = f"{host}:443" if port == 443 else host
    return urlunsplit(
        ("https", netloc, path, urlencode(query, doseq=True), "")
    )


def url_allowed_for_hosts(url: str, allowed_hosts: Sequence[str]) -> bool:
    """Return whether an HTTP(S) URL belongs to an explicit host allow-list."""

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    try:
        if parts.port not in {None, 80, 443}:
            return False
    except ValueError:
        return False
    hostname = parts.hostname.lower().rstrip(".")
    hosts = {str(host).lower().lstrip(".").rstrip(".") for host in allowed_hosts}
    return bool(hosts) and any(
        hostname == host or hostname.endswith(f".{host}") for host in hosts
    )


def load_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_text(path: Path, text: str) -> None:
    """Write a text file atomically so interrupted runs do not corrupt cache."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    # Byte-mode output preserves newlines exactly on every operating system.
    temporary.write_bytes(text.encode("utf-8"))
    temporary.replace(path)


def atomic_write_json(path: Path, value: Any) -> None:
    """Write indented UTF-8 JSON atomically."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def as_list(value: Any) -> List[str]:
    """Normalize a scalar or list-valued configuration option."""

    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [normalize_space(item) for item in value if normalize_space(item)]
    text = normalize_space(value)
    return [text] if text else []


def load_source_config(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load and index source definitions by source_id."""

    raw = load_json(path)
    items = raw.get("sources", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("moncloa source configuration must contain a 'sources' list")
    indexed: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        source_id = normalize_space(item.get("source_id"))
        if not source_id:
            raise ValueError("Every moncloa source requires a source_id")
        if source_id in indexed:
            raise ValueError(f"Duplicate moncloa source_id: {source_id}")
        indexed[source_id] = item
    return indexed


def load_politicians(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load politician definitions while accepting the project's common schemas."""

    raw = load_json(path)
    if isinstance(raw, dict) and isinstance(raw.get("politicians"), list):
        items = raw["politicians"]
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", key)
                items.append(item)
    else:
        items = []

    politicians: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        actor_id = normalize_space(
            item.get("id") or item.get("actor_id") or item.get("politician_id")
        )
        if not actor_id:
            continue
        politicians[actor_id] = item
    if not politicians:
        raise ValueError("No politicians were found in the politician configuration")
    return politicians


def politician_name(item: Dict[str, Any], fallback: str = "") -> str:
    """Return the preferred human-readable politician name."""

    return normalize_space(
        item.get("display_name")
        or item.get("name")
        or item.get("full_name")
        or fallback
    )


def politician_aliases(item: Dict[str, Any]) -> List[str]:
    """Collect configured name variants used only for actor verification."""

    aliases: List[str] = []
    for key in (
        "display_name",
        "name",
        "full_name",
        "congreso_name",
        "surname",
        "marker_aliases",
        "aliases",
    ):
        aliases.extend(as_list(item.get(key)))
    result: List[str] = []
    seen = set()
    for alias in aliases:
        folded = fold_for_matching(alias)
        if len(folded) >= 4 and folded not in seen:
            result.append(alias)
            seen.add(folded)
    return result


def selector_values(source: Dict[str, Any], name: str) -> List[str]:
    """Return selectors from either the nested or legacy flat config form."""

    selectors = source.get("selectors")
    values: Any = None
    if isinstance(selectors, dict):
        values = selectors.get(name)
    if values is None:
        values = source.get(f"{name}_selectors")
    if values is None and name == "body":
        values = source.get("content_selectors")
    return as_list(values)


def first_selected_text(
    soup: BeautifulSoup, selectors: Sequence[str]
) -> Tuple[str, str]:
    """Return text from the first matching CSS selector."""

    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node:
            text = normalize_space(node.get_text(" ", strip=True))
            if text:
                return text, selector
    return "", ""


def meta_content(soup: BeautifulSoup, *keys: str) -> Tuple[str, str]:
    """Read a meta element by property, name, or itemprop."""

    for key in keys:
        for attribute in ("property", "name", "itemprop"):
            node = soup.find("meta", attrs={attribute: key})
            if node and normalize_space(node.get("content")):
                return normalize_space(node.get("content")), f"meta:{attribute}={key}"
    return "", ""


def iter_json_objects(value: Any) -> Iterable[Dict[str, Any]]:
    """Yield all dictionaries nested in JSON-LD arrays and @graph objects."""

    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def extract_article_jsonld(soup: BeautifulSoup) -> Dict[str, Any]:
    """Return the most article-like JSON-LD object on the page."""

    candidates: List[Dict[str, Any]] = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = node.string or node.get_text()
        if not normalize_space(raw):
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in iter_json_objects(value):
            item_type = item.get("@type", "")
            types = item_type if isinstance(item_type, list) else [item_type]
            folded = {fold_for_matching(kind) for kind in types}
            if folded & {
                "article",
                "newsarticle",
                "reportagenewsarticle",
                "interview",
                "blogposting",
            }:
                candidates.append(item)
    if not candidates:
        return {}
    return max(
        candidates,
        key=lambda item: len(normalize_space(item.get("articleBody")))
        + len(normalize_space(item.get("headline"))),
    )


def parse_spanish_date(value: Any) -> str:
    """Parse common ISO, numeric and Spanish-language publication dates."""

    text = normalize_space(value)
    if not text:
        return ""

    # ISO timestamps and dates are frequent in meta and JSON-LD fields.
    iso_match = re.search(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", text)
    if iso_match:
        candidate = iso_match.group(0)
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            pass

    # Numeric Spanish dates normally use day/month/year ordering.
    numeric_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})\b", text)
    if numeric_match:
        day, month, year = (int(part) for part in numeric_match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    folded = fold_for_matching(text)
    month_names = "|".join(sorted(SPANISH_MONTHS, key=len, reverse=True))
    word_match = re.search(
        rf"\b(\d{{1,2}})\s+(?:de\s+)?({month_names})\s+(?:de\s+)?((?:19|20)\d{{2}})\b",
        folded,
    )
    if word_match:
        day = int(word_match.group(1))
        month = SPANISH_MONTHS[word_match.group(2)]
        year = int(word_match.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    # Some official-party templates render dates as "septiembre 11, 2026".
    month_first = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})\s*,?\s*((?:19|20)\d{{2}})\b",
        folded,
    )
    if month_first:
        try:
            return date(
                int(month_first.group(3)),
                SPANISH_MONTHS[month_first.group(1)],
                int(month_first.group(2)),
            ).isoformat()
        except ValueError:
            pass

    # RFC dates may be present in HTTP-style metadata.
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def date_from_url(url: str) -> str:
    """Extract YYYYMMDD or YYYY/MM/DD date patterns from a source URL."""

    match = re.search(r"(?<!\d)((?:19|20)\d{2})[/-]?(\d{2})[/-]?(\d{2})(?!\d)", url)
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


def extract_body(
    soup: BeautifulSoup,
    source: Dict[str, Any],
    adapter: str,
    jsonld: Dict[str, Any],
) -> Tuple[str, List[str], str]:
    """Extract body paragraphs with source-specific selectors and fallbacks."""

    selectors = selector_values(source, "body")
    if adapter == "lamoncloa":
        selectors = [
            "section#contentArticleBody[property='articleBody']",
            "section#contentArticleBody",
            "[property='articleBody']",
        ] + selectors
    elif adapter == "pp":
        selectors += [
            "article .entry-content",
            ".single-post .entry-content",
            ".post-content",
            "article",
        ]
    elif adapter == "vox":
        selectors += [
            "article .entry-content",
            ".content-noticia",
            ".post-content",
            "article",
        ]
    selectors += ["[itemprop='articleBody']", "article", "main"]

    selected: Optional[Tag] = None
    used_selector = ""
    seen_selectors = set()
    for selector in selectors:
        if not selector or selector in seen_selectors:
            continue
        seen_selectors.add(selector)
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if isinstance(node, Tag):
            selected = node
            used_selector = selector
            break

    if selected is None:
        article_body = jsonld.get("articleBody") if jsonld else ""
        text = normalize_multiline(BeautifulSoup(str(article_body or ""), "html.parser").get_text("\n"))
        return text, [text] if text else [], "jsonld:articleBody" if text else ""

    # Work on a copy so metadata extraction from the original soup is not
    # affected by removing navigation or related-content elements.
    body_soup = BeautifulSoup(str(selected), "html.parser")
    remove_selectors = DEFAULT_REMOVE_SELECTORS + selector_values(source, "remove")
    for selector in remove_selectors:
        try:
            nodes = body_soup.select(selector)
        except Exception:
            continue
        for node in nodes:
            node.decompose()

    blocks: List[str] = []
    block_tags = body_soup.find_all(["p", "h2", "h3", "h4", "li", "blockquote"])
    for node in block_tags:
        # Skip a nested block when its parent will already be represented.
        if node.name == "li" and node.find_parent("li"):
            continue
        text = normalize_space(node.get_text(" ", strip=True))
        if not text or is_boilerplate(text):
            continue
        if not blocks or blocks[-1] != text:
            blocks.append(text)
    if not blocks:
        text = normalize_multiline(body_soup.get_text("\n", strip=True))
        blocks = [line for line in text.splitlines() if not is_boilerplate(line)]

    return "\n\n".join(blocks), blocks, used_selector


def is_boilerplate(text: str) -> bool:
    """Identify short navigation, sharing and cookie-control fragments."""

    folded = fold_for_matching(text)
    if len(folded) > 180:
        return False
    phrases = (
        "compartir en",
        "comparte esta noticia",
        "aceptar cookies",
        "politica de cookies",
        "suscribete",
        "volver al listado",
        "mas informacion",
        "menu principal",
        "redes sociales",
    )
    return any(phrase in folded for phrase in phrases)


def extract_page(
    html: str,
    fetched_url: str,
    source: Dict[str, Any],
    candidate: Optional[Dict[str, str]] = None,
) -> PageExtraction:
    """Extract title, date, location, canonical URL and cleaned body text."""

    candidate = candidate or {}
    soup = BeautifulSoup(html, "html.parser")
    adapter = fold_for_matching(source.get("adapter") or "generic")
    jsonld = extract_article_jsonld(soup)

    title_selectors = selector_values(source, "title")
    if adapter == "lamoncloa":
        title_selectors = ["h1#ctl00_PlaceHolderMain_DisplayMode_noticia_h1Title"] + title_selectors
    title_selectors += ["article h1", "main h1", "h1"]
    title, title_source = first_selected_text(soup, title_selectors)
    if not title:
        title = normalize_space(jsonld.get("headline"))
        title_source = "jsonld:headline" if title else ""
    if not title:
        title, title_source = meta_content(soup, "og:title", "twitter:title")
    if not title:
        title = normalize_space(candidate.get("title_hint"))
        title_source = "candidate:title_hint" if title else ""

    date_text = ""
    date_source = ""
    date_selectors = selector_values(source, "date")
    if adapter == "lamoncloa":
        date_selectors = ["#noticia_sitedate span.date", "#noticia_sitedate"] + date_selectors
    date_text, date_selector = first_selected_text(soup, date_selectors)
    publication_date = parse_spanish_date(date_text)
    if publication_date:
        date_source = f"selector:{date_selector}"
    if not publication_date:
        date_text, meta_source = meta_content(
            soup,
            "article:published_time",
            "datePublished",
            "date",
            "DC.date",
        )
        publication_date = parse_spanish_date(date_text)
        if publication_date:
            date_source = meta_source
    if not publication_date:
        publication_date = parse_spanish_date(jsonld.get("datePublished"))
        if publication_date:
            date_source = "jsonld:datePublished"
    if not publication_date:
        time_node = soup.find("time")
        if time_node:
            publication_date = parse_spanish_date(
                time_node.get("datetime") or time_node.get_text(" ", strip=True)
            )
            if publication_date:
                date_source = "element:time"
    if not publication_date:
        publication_date = parse_spanish_date(candidate.get("date_hint"))
        if publication_date:
            date_source = "candidate:date_hint"
    if not publication_date:
        publication_date = date_from_url(fetched_url)
        if publication_date:
            date_source = "url"

    location_selectors = selector_values(source, "location")
    if adapter == "lamoncloa":
        location_selectors = [
            "p#news-location",
            "p[id$='news-location']",
            "p[id*='news-location']",
            "[class*='news-location']",
        ] + location_selectors
    location, _ = first_selected_text(soup, location_selectors)

    # La Moncloa publishes its preferred URL in og:url.  Other sites may use
    # either Open Graph or a conventional canonical link.
    canonical_value, _ = meta_content(soup, "og:url")
    if not canonical_value:
        canonical_node = soup.find("link", rel=lambda value: value and "canonical" in value)
        canonical_value = normalize_space(canonical_node.get("href")) if canonical_node else ""
    if not canonical_value:
        entity = jsonld.get("mainEntityOfPage") if jsonld else ""
        if isinstance(entity, dict):
            canonical_value = normalize_space(entity.get("@id") or entity.get("url"))
        else:
            canonical_value = normalize_space(entity)
    canonical_url = canonicalize_url(canonical_value or fetched_url, fetched_url)

    body_text, body_blocks, body_selector = extract_body(soup, source, adapter, jsonld)
    has_video = bool(
        soup.find("video")
        or soup.find("iframe", src=re.compile(r"youtube|youtu\.be|vimeo|rtve", re.I))
    )
    return PageExtraction(
        title=title,
        title_source=title_source,
        publication_date=publication_date,
        date_source=date_source,
        location=location,
        canonical_url=canonical_url,
        body_text=body_text,
        body_blocks=body_blocks,
        body_selector=body_selector,
        has_video=has_video,
        jsonld=jsonld,
    )


def classify_document_type(title: str, source: Dict[str, Any]) -> str:
    """Classify a page using conservative title-based Spanish rules."""

    folded = fold_for_matching(title)
    if PARLIAMENTARY_TITLE_RE.search(folded):
        return "parliamentary"
    if re.search(r"\bentrevista\b", folded):
        return "interview"
    if re.search(r"\b(rueda de prensa|press conference)\b", folded):
        return "press_conference"
    if re.search(r"\b(articulo|tribuna|carta abierta|opinion)\b", folded):
        return "article"
    if re.search(r"\b(declaracion|mensaje institucional|comunicado)\b", folded):
        return "statement"
    if re.search(
        r"\b(discurso|intervencion|alocucion|mitin|clausura|inauguracion|comparecencia)\b",
        folded,
    ):
        return "speech"
    configured = normalize_space(source.get("default_document_type"))
    if configured:
        return configured
    if fold_for_matching(source.get("adapter")) in {"pp", "vox"}:
        return "press_release"
    return "other"


def split_interview_answers(blocks: Sequence[str]) -> Tuple[List[str], bool]:
    """Return only answer turns marked Respuesta/Repuesta/R.- from a transcript.

    Unlabelled paragraphs immediately following a labelled answer are treated
    as continuations until the next explicit question or answer marker.
    """

    turns: List[Tuple[str, str]] = []
    current_role = ""
    current_chunks: List[str] = []
    found_marker = False

    def flush() -> None:
        nonlocal current_chunks
        text = normalize_space(" ".join(current_chunks))
        if current_role and text:
            turns.append((current_role, text))
        current_chunks = []

    def named_speaker_header(text: str) -> Optional[Tuple[str, str]]:
        """Recognize a conservative title-cased human-name speaker label."""

        match = NAMED_SPEAKER_HEADER_RE.match(text)
        if not match:
            return None
        label = normalize_space(match.group("label"))
        words = label.split()
        if not 1 <= len(words) <= 8:
            return None
        significant = 0
        for word in words:
            stripped = word.strip(".,;()[]{}¿?¡!\"'“”«»")
            if not stripped:
                continue
            if fold_for_matching(stripped) in LOWERCASE_NAME_WORDS:
                continue
            first_alpha = next((char for char in stripped if char.isalpha()), "")
            if not first_alpha or not (first_alpha.isupper() or stripped.isupper()):
                return None
            significant += 1
        if significant == 0:
            return None
        return label, normalize_space(match.group("content"))

    for block in blocks:
        text = normalize_space(block)
        if not text:
            continue
        matches = list(SPEAKER_MARKER_RE.finditer(text))
        if not matches:
            named_header = named_speaker_header(text)
            if named_header:
                flush()
                found_marker = True
                current_role = "other_speaker"
                if named_header[1]:
                    current_chunks.append(named_header[1])
                continue
            if current_role:
                current_chunks.append(text)
            continue

        found_marker = True
        prefix = normalize_space(text[: matches[0].start()])
        if prefix and current_role:
            current_chunks.append(prefix)
        for index, match in enumerate(matches):
            flush()
            label = fold_for_matching(match.group("long") or match.group("short"))
            current_role = "answer" if label in {"respuesta", "repuesta", "r"} else "question"
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = normalize_space(text[match.end() : end])
            if content:
                current_chunks.append(content)
    flush()

    answers = [text for role, text in turns if role == "answer"]
    return answers, found_marker


def contains_full_text_marker(
    title: str, body_text: str, source: Dict[str, Any]
) -> bool:
    """Check explicit configured phrases indicating a complete transcript/text."""

    markers = as_list(source.get("full_text_markers"))
    haystack = fold_for_matching(f"{title}\n{body_text[:4000]}")
    for marker in markers:
        if marker.startswith("re:"):
            try:
                if re.search(marker[3:], haystack, flags=re.IGNORECASE):
                    return True
            except re.error:
                continue
        elif fold_for_matching(marker) in haystack:
            return True
    return False


def verify_actor(
    expected_actor_id: str,
    politicians: Dict[str, Dict[str, Any]],
    title: str,
    body_text: str,
    source: Dict[str, Any],
) -> Tuple[str, str, str, str]:
    """Verify the expected actor and return id, name, party and method."""

    adapter = fold_for_matching(source.get("adapter") or "generic")
    source_actor = normalize_space(source.get("expected_actor_id"))
    actor_id = expected_actor_id or source_actor
    if not actor_id or actor_id not in politicians:
        return actor_id, "", "", "not_verified"

    actor = politicians[actor_id]
    name = politician_name(actor, actor_id)
    party = normalize_space(actor.get("party"))

    # The official president archive is scoped to the office holder.  This is
    # stronger evidence than a mention inside an unrelated press release.
    scope_verified = source.get("actor_verified_by_source_scope")
    if scope_verified is None:
        scope_verified = adapter == "lamoncloa"
    if bool(scope_verified) and actor_id == source_actor:
        return actor_id, name, party, "verified_by_source_scope"

    title_folded = fold_for_matching(title)
    body_folded = fold_for_matching(body_text[:8000])
    aliases = politician_aliases(actor)
    for alias in aliases:
        folded = fold_for_matching(alias)
        if folded and folded in title_folded:
            return actor_id, name, party, "verified_in_title"
    for alias in aliases:
        folded = fold_for_matching(alias)
        # Body verification requires either a full multi-word name or repeated
        # occurrences, reducing surname collisions in broad party feeds.
        if not folded:
            continue
        occurrences = body_folded.count(folded)
        if (" " in folded and occurrences >= 1) or occurrences >= 2:
            return actor_id, name, party, "verified_in_body"
    return actor_id, name, party, "not_verified"


def looks_third_person_press_release(text: str, actor_name: str) -> bool:
    """Detect party-news prose that reports on, rather than transcribes, an actor."""

    folded = fold_for_matching(text[:6000])
    names = [fold_for_matching(actor_name)]
    if actor_name:
        names.append(fold_for_matching(actor_name.split()[-1]))
    verbs = (
        "ha afirmado",
        "ha asegurado",
        "ha senalado",
        "ha explicado",
        "ha denunciado",
        "ha recordado",
        "ha dicho",
        "afirma que",
        "asegura que",
    )
    hits = 0
    for name in names:
        if not name:
            continue
        for verb in verbs:
            hits += len(re.findall(rf"\b{re.escape(name)}\b.{{0,45}}\b{re.escape(verb)}\b", folded))
    return hits >= 2


def looks_quote_only(text: str) -> bool:
    """Flag short items dominated by selected quotations rather than full text."""

    if len(text) > 2500:
        return False
    quote_characters = sum(text.count(char) for char in ('"', "“", "”", "«", "»"))
    folded = fold_for_matching(text)
    framing_hits = sum(
        folded.count(phrase)
        for phrase in ("ha afirmado", "ha asegurado", "ha dicho", "segun", "declaro")
    )
    return quote_characters >= 4 and framing_hits >= 1


def select_source_ids(values: Sequence[str]) -> set[str]:
    """Expand repeated or comma-separated --source options."""

    result: set[str] = set()
    for value in values:
        result.update(item.strip() for item in value.split(",") if item.strip())
    return result


def date_in_range(value: str, start: str, end: str) -> bool:
    """Check an ISO date against optional inclusive boundaries."""

    if not value:
        return True
    return (not start or value >= start) and (not end or value <= end)


def actor_scope_covers(publication_date: str, source: Dict[str, Any]) -> bool:
    """Check whether the configured office-holder scope covers a document date."""

    if not publication_date:
        return True
    start = normalize_space(source.get("actor_scope_start_date"))
    end = normalize_space(source.get("actor_scope_end_date"))
    return (not start or publication_date >= start) and (
        not end or publication_date <= end
    )


def validate_iso_date(value: str, option: str) -> str:
    """Validate one CLI date and return its normalized ISO value."""

    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{option} must use YYYY-MM-DD") from exc


def allowed_url(url: str, source: Dict[str, Any]) -> bool:
    """Ensure a candidate uses HTTP(S) and an allowed source hostname."""

    if not url_allowed_for_hosts(url, as_list(source.get("allowed_hosts"))):
        return False
    pattern = normalize_space(source.get("detail_url_pattern"))
    if not pattern:
        return True
    try:
        return re.search(pattern, url) is not None
    except re.error:
        return False


def reason_from_exception(exc: Exception) -> str:
    """Map common fetch failures to stable audit reason labels."""

    if isinstance(exc, FileNotFoundError):
        return "offline_cache_miss"
    if isinstance(exc, PermissionError):
        return "access_blocked"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code in {401, 403, 429}:
            return "access_blocked"
        return "http_error"
    if isinstance(exc, requests.RequestException):
        return "network_error"
    if isinstance(exc, ValueError):
        return "invalid_response"
    return "parse_error"


def blank_inventory_row(candidate: Dict[str, str], source: Dict[str, Any]) -> Dict[str, Any]:
    """Create an empty, schema-complete record for one candidate."""

    row: Dict[str, Any] = {field: "" for field in INVENTORY_FIELDS}
    row.update(
        {
            "candidate_id": candidate.get("candidate_id", ""),
            "source_id": candidate.get("source_id", ""),
            "source_name": candidate.get("source_name") or source.get("source_name", ""),
            "publisher": candidate.get("publisher") or source.get("publisher", ""),
            "publisher_type": candidate.get("publisher_type") or source.get("publisher_type", ""),
            "language": source.get("language", "es"),
            "source_url": candidate.get("canonical_url") or candidate.get("url", ""),
            "requested_url": candidate.get("canonical_url") or candidate.get("url", ""),
            "discovery_url": candidate.get("discovery_url", ""),
            "discovery_method": candidate.get("discovery_method", ""),
            "discovery_page": candidate.get("discovery_page", ""),
            "discovered_at_utc": candidate.get("discovered_at_utc", ""),
            "listing_cache_path": candidate.get("listing_cache_path", ""),
            "listing_sha256": candidate.get("listing_sha256", ""),
            "adapter": source.get("adapter", "generic"),
            "manual_review_required": True,
            "legal_notice_url": candidate.get("legal_notice_url")
            or source.get("legal_notice_url", ""),
            "required_attribution": source.get("required_attribution", ""),
            "redistribution_status": candidate.get("redistribution_status")
            or source.get("redistribution_status", "internal_research_only"),
        }
    )
    return row


def audit_from_inventory(
    row: Dict[str, Any], error_type: str = "", error_message: str = ""
) -> Dict[str, Any]:
    """Create a compact audit record from a document inventory row."""

    return {
        "candidate_id": row.get("candidate_id", ""),
        "document_id": row.get("document_id", ""),
        "source_id": row.get("source_id", ""),
        "canonical_url": row.get("canonical_url") or row.get("source_url", ""),
        "status": row.get("document_status", ""),
        "reason": row.get("status_reason", ""),
        "actor_verification": row.get("actor_verification", ""),
        "document_type": row.get("document_type", ""),
        "publication_date": row.get("publication_date", ""),
        "character_count": row.get("character_count", ""),
        "segment_count": row.get("segment_count", ""),
        "cache_hit": row.get("cache_hit", ""),
        "http_status": row.get("http_status", ""),
        "error_type": error_type,
        "error_message": normalize_space(error_message)[:1000],
    }


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    """Write deterministic UTF-8 CSV with an explicit field order."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_candidates(path: Path) -> List[Dict[str, str]]:
    """Read and minimally validate the candidate inventory."""

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    for number, row in enumerate(rows, start=2):
        if not normalize_space(row.get("source_id")):
            raise ValueError(f"Missing source_id in {path} at row {number}")
        if not normalize_space(row.get("canonical_url") or row.get("url")):
            raise ValueError(f"Missing canonical_url/url in {path} at row {number}")
    return rows


def build_segment(
    inventory_row: Dict[str, Any], text: str, index: int, method: str
) -> Dict[str, Any]:
    """Create one provenance-rich segment from a verified politician turn."""

    cleaned = normalize_space(text)
    document_id = str(inventory_row["document_id"])
    segment_id = stable_id("moncloa_seg", document_id, index, cleaned)
    bundle_id = stable_id("moncloa_bundle", document_id, length=16)
    return {
        "segment_id": segment_id,
        "message_id": segment_id,
        "bundle_id": bundle_id,
        "document_id": document_id,
        "candidate_id": inventory_row.get("candidate_id", ""),
        "actor_id": inventory_row.get("actor_id", ""),
        "actor_name": inventory_row.get("actor_name", ""),
        "party": inventory_row.get("party", ""),
        "publication_date": inventory_row.get("publication_date", ""),
        "source_id": inventory_row.get("source_id", ""),
        "source_name": inventory_row.get("source_name", ""),
        "source_type": "moncloa",
        "document_type": inventory_row.get("document_type", ""),
        "segment_index": index,
        "speaker_role": "politician",
        "text": cleaned,
        "character_count": len(cleaned),
        "word_count": len(cleaned.split()),
        "canonical_url": inventory_row.get("canonical_url", ""),
        "content_sha256": sha256_text(cleaned),
        "extraction_method": method,
        "redistribution_status": inventory_row.get("redistribution_status", ""),
        "manual_review_required": True,
        "keep_for_prompt": False,
    }


def segment_to_stimulus(segment: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one internal segment to the disabled downstream schema."""

    return {
        # The downstream schema retains its historical column name even for
        # non-X material.  Congreso uses the stable message identifier here,
        # so moncloa records do the same for compatibility.
        "tweet_id": segment["message_id"],
        "message_id": segment["message_id"],
        "bundle_id": segment["bundle_id"],
        "text_anon": segment["text"],
        "date": segment["publication_date"],
        "author_role": "politician",
        "source_type": "moncloa",
        "topic": "",
        "keep_for_prompt": False,
        "is_morally_relevant": "",
        "dominant_moral_dimension": "",
        "moral_max": "",
        "isDeleted": False,
    }


def determine_status_and_segments(
    extraction: PageExtraction,
    inventory_row: Dict[str, Any],
    source: Dict[str, Any],
    min_text_chars: int,
) -> Tuple[str, str, List[Tuple[str, str]]]:
    """Apply conservative inclusion rules and return status, reason, segments."""

    adapter = fold_for_matching(source.get("adapter") or "generic")
    document_type = str(inventory_row.get("document_type") or "other")
    actor_verified = inventory_row.get("actor_verification") != "not_verified"
    text = extraction.body_text
    excluded_types = {fold_for_matching(value) for value in as_list(source.get("exclude_document_types"))}
    excluded_types.add("parliamentary")

    if fold_for_matching(document_type) in excluded_types:
        return "excluded", "parliamentary", []
    if not text:
        if extraction.has_video:
            return "excluded", "video_only", []
        return "review", "no_full_text", []
    if len(text) < min_text_chars:
        if extraction.has_video:
            return "excluded", "video_only", []
        return "review", "text_too_short", []
    if not actor_verified:
        return "review", "actor_not_verified", []
    if not extraction.publication_date:
        return "review", "missing_date", []

    answers, has_markers = split_interview_answers(extraction.body_blocks)
    answers = [answer for answer in answers if len(answer) >= 40]
    full_text_marker = contains_full_text_marker(extraction.title, text, source)

    if adapter == "lamoncloa":
        if document_type == "interview":
            if has_markers and answers:
                return "included", "verified_answer_turns", [
                    (answer, "labelled_answer_turn") for answer in answers
                ]
            return "review", "mixed_speakers", []

        # Press conferences often contain several officials.  Generic
        # Pregunta/Respuesta labels are not enough to prove which official
        # supplied each answer, so these pages always remain for review.
        if document_type == "press_conference":
            return "review", "mixed_speakers", []

        # A government archive establishes provenance, but not by itself that
        # every paragraph is the president's speech.  Require an explicit
        # transcript/full-text marker and no competing speaker labels.
        if (
            document_type in {"speech", "statement", "article"}
            and full_text_marker
            and not has_markers
        ):
            return "included", "verified_full_text", [(text, "explicit_full_text")]
        return "review", "document_structure_not_verified", []

    # Party sites normally publish journalistic press releases with selected
    # quotations.  Automatic segments require an explicit full-text marker in
    # the source configuration and independent actor verification.
    if adapter in {"pp", "vox"}:
        if not full_text_marker:
            if looks_third_person_press_release(text, inventory_row.get("actor_name", "")):
                return "review", "third_person_press_release", []
            if looks_quote_only(text):
                return "review", "quote_only", []
            return "review", "no_verified_full_text", []
        if looks_third_person_press_release(text, inventory_row.get("actor_name", "")):
            return "review", "third_person_press_release", []
        if document_type in {"interview", "press_conference"}:
            if has_markers and answers:
                return "included", "verified_answer_turns", [
                    (answer, "labelled_answer_turn") for answer in answers
                ]
            return "review", "mixed_speakers", []
        if has_markers:
            return "review", "mixed_speakers", []
        return "included", "verified_full_text", [(text, "explicit_full_text")]

    # Generic sources default to review unless their configuration explicitly
    # marks the page as a complete text and verifies the actor.
    if full_text_marker and not has_markers:
        return "included", "verified_full_text", [(text, "explicit_full_text")]
    return "review", normalize_space(source.get("default_review_reason")) or "manual_review_required", []


def build_summary(
    args: argparse.Namespace,
    total_candidates: int,
    selected_candidates: int,
    inventory: Sequence[Dict[str, Any]],
    segments: Sequence[Dict[str, Any]],
    output_files: Dict[str, str],
) -> Dict[str, Any]:
    """Build a machine-readable summary from the generated records."""

    dates = sorted(
        str(row["publication_date"])
        for row in inventory
        if row.get("publication_date")
    )
    return {
        "pipeline": "moncloa_text_extraction",
        "pipeline_version": PIPELINE_VERSION,
        "generated_at_utc": utc_now(),
        "input_inventory": str(args.inventory),
        "total_candidates_read": total_candidates,
        "candidates_selected": selected_candidates,
        "documents_total": len(inventory),
        "documents_by_status": dict(sorted(Counter(str(row.get("document_status")) for row in inventory).items())),
        "documents_by_reason": dict(sorted(Counter(str(row.get("status_reason")) for row in inventory).items())),
        "documents_by_source": dict(sorted(Counter(str(row.get("source_id")) for row in inventory).items())),
        "documents_by_type": dict(sorted(Counter(str(row.get("document_type")) for row in inventory).items())),
        "segments_total": len(segments),
        "segments_by_actor": dict(sorted(Counter(str(row.get("actor_id")) for row in segments).items())),
        "segments_by_source": dict(sorted(Counter(str(row.get("source_id")) for row in segments).items())),
        "stimuli_total": len(segments),
        "exact_duplicates": sum(bool(row.get("is_exact_duplicate")) for row in inventory),
        "cache_hits": sum(bool(row.get("cache_hit")) for row in inventory),
        "live_fetches": sum(row.get("cache_hit") is False for row in inventory),
        "date_range": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
        },
        "filters": {
            "sources": sorted(select_source_ids(args.source or [])),
            "start": args.start or None,
            "end": args.end or None,
            "max_documents": args.max_documents,
            "min_text_chars": args.min_text_chars,
            "offline": args.offline,
            "refresh": args.refresh,
        },
        "output_files": output_files,
        "notes": [
            "Full source text is stored only in moncloa_documents_internal.csv.",
            "All moncloa_stimuli.csv rows have keep_for_prompt=false pending manual review.",
            "No audio/video or automatic speech recognition is used by this pipeline.",
        ],
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description="Extract auditable text from official La Moncloa pages."
    )
    parser.add_argument("--inventory", type=Path, required=True, help="moncloa_candidates.csv")
    parser.add_argument(
        "--sources-config", type=Path, required=True, help="JSON definitions for moncloa sources"
    )
    parser.add_argument(
        "--politicians-config", type=Path, required=True, help="Project politician JSON"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Directory for processed CSV/JSON outputs"
    )
    parser.add_argument(
        "--cache-dir", type=Path, required=True, help="Private cache for downloaded HTML"
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help=(
            "Only process a source_id; repeat or separate multiple IDs with commas. "
            f"The default is {MONCLOA_SOURCE_ID}."
        ),
    )
    parser.add_argument("--start", default="", help="Inclusive publication start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="", help="Inclusive publication end date (YYYY-MM-DD)")
    parser.add_argument("--max-documents", type=int, default=None, help="Limit fetched documents")
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Seconds between live requests (otherwise read from the source config)",
    )
    parser.add_argument("--contact-email", default="", help="Optional institutional crawler contact")
    parser.add_argument("--offline", action="store_true", help="Use cache only; make no requests")
    parser.add_argument("--refresh", action="store_true", help="Refresh existing cached pages")
    parser.add_argument(
        "--min-text-chars",
        type=int,
        default=DEFAULT_MIN_TEXT_CHARS,
        help="Minimum cleaned body length for automatic inclusion",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run extraction, write outputs, and print the final summary."""

    args = parse_args(argv)
    if args.offline and args.refresh:
        raise SystemExit("--offline and --refresh cannot be used together")
    if args.max_documents is not None and args.max_documents < 1:
        raise SystemExit("--max-documents must be at least 1")
    if args.min_text_chars < 1:
        raise SystemExit("--min-text-chars must be at least 1")
    try:
        moncloa_config = load_json(args.sources_config)
        sources = load_source_config(args.sources_config)
        politicians = load_politicians(args.politicians_config)
        candidates = read_candidates(args.inventory)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Configuration/input error: {exc}") from exc

    # Collection-wide defaults live beside the source definitions.  Explicit
    # CLI values always take precedence, which makes one-off reruns easy.
    if isinstance(moncloa_config, dict):
        if not args.start:
            args.start = moncloa_config.get("default_start_date") or ""
        if not args.end:
            args.end = moncloa_config.get("default_end_date") or ""
        if args.delay is None:
            args.delay = float(
                moncloa_config.get("default_delay_seconds", DEFAULT_DELAY_SECONDS)
            )
    elif args.delay is None:
        args.delay = DEFAULT_DELAY_SECONDS

    try:
        args.start = validate_iso_date(args.start, "--start")
        args.end = validate_iso_date(args.end, "--end")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.start and args.end and args.start > args.end:
        raise SystemExit("--start cannot be later than --end")
    if args.delay < 0:
        raise SystemExit("--delay cannot be negative")

    if not args.source:
        args.source = [MONCLOA_SOURCE_ID]
    requested_sources = select_source_ids(args.source)
    unknown_sources = requested_sources - set(sources)
    if unknown_sources:
        raise SystemExit(f"Unknown --source value(s): {', '.join(sorted(unknown_sources))}")

    fetcher = PoliteFetcher(
        cache_dir=args.cache_dir,
        delay_seconds=args.delay,
        contact_email=args.contact_email,
        offline=args.offline,
        refresh=args.refresh,
    )

    inventory_rows: List[Dict[str, Any]] = []
    document_rows: List[Dict[str, Any]] = []
    segment_rows: List[Dict[str, Any]] = []
    audit_rows: List[Dict[str, Any]] = []
    content_seen: Dict[str, Tuple[str, str]] = {}
    fetched_count = 0
    selected_count = 0

    # Sorting by source and canonical URL makes output stable even if discovery
    # order changes between website snapshots.
    candidates = sorted(
        candidates,
        key=lambda row: (
            normalize_space(row.get("source_id")),
            canonicalize_url(row.get("canonical_url") or row.get("url") or ""),
        ),
    )

    for candidate in candidates:
        source_id = normalize_space(candidate.get("source_id"))
        if requested_sources and source_id not in requested_sources:
            continue
        source = sources.get(source_id)
        if source is None:
            row = blank_inventory_row(candidate, {})
            row.update(document_status="failed", status_reason="unknown_source")
            inventory_rows.append(row)
            document_rows.append({**row, "text": "", "text_scope": ""})
            audit_rows.append(audit_from_inventory(row, "ConfigurationError", "Unknown source_id"))
            continue

        url = canonicalize_url(candidate.get("canonical_url") or candidate.get("url") or "")
        candidate["candidate_id"] = normalize_space(candidate.get("candidate_id")) or stable_id(
            "moncloa_candidate", source_id, url
        )
        row = blank_inventory_row(candidate, source)
        row["source_url"] = url
        row["canonical_url"] = url
        row["document_id"] = stable_id("moncloa_doc", source_id, url)
        row["publication_date"] = parse_spanish_date(candidate.get("date_hint"))
        row["date_source"] = "candidate:date_hint" if row["publication_date"] else ""
        row["title"] = normalize_space(candidate.get("title_hint"))
        row["document_type"] = classify_document_type(row["title"], source)

        is_pdf = (
            fold_for_matching(candidate.get("content_type_hint")) == "pdf"
            or urlsplit(url).path.lower().endswith(".pdf")
        )
        if is_pdf and url_allowed_for_hosts(
            url, as_list(source.get("allowed_hosts"))
        ):
            row.update(document_status="excluded", status_reason="unsupported_format_pdf")
            inventory_rows.append(row)
            document_rows.append({**row, "text": "", "text_scope": ""})
            audit_rows.append(audit_from_inventory(row))
            continue
        if not allowed_url(url, source):
            row.update(document_status="failed", status_reason="invalid_or_disallowed_url")
            inventory_rows.append(row)
            document_rows.append({**row, "text": "", "text_scope": ""})
            audit_rows.append(
                audit_from_inventory(row, "URLValidationError", "URL is not allowed by source config")
            )
            continue
        if row["publication_date"] and not date_in_range(
            row["publication_date"], args.start, args.end
        ):
            row.update(document_status="excluded", status_reason="outside_date_range")
            inventory_rows.append(row)
            document_rows.append({**row, "text": "", "text_scope": ""})
            audit_rows.append(audit_from_inventory(row))
            continue
        if args.max_documents is not None and fetched_count >= args.max_documents:
            break

        selected_count += 1
        fetched_count += 1
        print(f"[{fetched_count}] {source_id}: {url}", flush=True)
        try:
            fetched = fetcher.fetch(
                source_id,
                url,
                as_list(source.get("allowed_hosts")),
            )
            if not allowed_url(fetched.final_url, source):
                raise PermissionError(
                    f"Final URL is outside the source allow-list: {fetched.final_url}"
                )
            extraction = extract_page(fetched.html, fetched.final_url, source, candidate)
        except Exception as exc:  # One page must not abort the complete corpus run.
            reason = reason_from_exception(exc)
            row.update(document_status="failed", status_reason=reason)
            inventory_rows.append(row)
            document_rows.append({**row, "text": "", "text_scope": ""})
            audit_rows.append(audit_from_inventory(row, type(exc).__name__, str(exc)))
            print(
                f"  failed: {reason} ({type(exc).__name__}: {exc})",
                file=sys.stderr,
                flush=True,
            )
            continue

        canonical = extraction.canonical_url or canonicalize_url(fetched.final_url)
        if not allowed_url(canonical, source):
            # Do not let untrusted page metadata move a record outside its
            # configured official source.  The fetched URL remains auditable.
            canonical = canonicalize_url(fetched.final_url)
        # Keep document identifiers stable using the source's canonical URL.
        row["canonical_url"] = canonical
        row["document_id"] = stable_id("moncloa_doc", source_id, canonical)
        row["requested_url"] = fetched.requested_url
        row["final_url"] = fetched.final_url
        row["retrieved_at_utc"] = fetched.retrieved_at_utc
        row["cache_hit"] = fetched.cache_hit
        row["cache_path"] = fetched.cache_path
        row["http_status"] = fetched.http_status
        row["title"] = extraction.title
        row["title_source"] = extraction.title_source
        row["publication_date"] = extraction.publication_date
        row["date_source"] = extraction.date_source
        row["location"] = extraction.location
        row["body_selector"] = extraction.body_selector
        row["document_type"] = classify_document_type(extraction.title, source)
        row["character_count"] = len(extraction.body_text)
        row["word_count"] = len(extraction.body_text.split())
        row["content_sha256"] = sha256_text(extraction.body_text) if extraction.body_text else ""
        row["raw_sha256"] = fetched.raw_sha256
        row["third_party_material_possible"] = row["document_type"] in {
            "interview",
            "article",
            "press_conference",
        }

        expected_actor_id = normalize_space(
            candidate.get("expected_actor_id") or source.get("expected_actor_id")
        )
        actor_id, actor_name, party, verification = verify_actor(
            expected_actor_id,
            politicians,
            extraction.title,
            extraction.body_text,
            source,
        )
        row["actor_id"] = actor_id
        row["actor_name"] = actor_name or normalize_space(candidate.get("expected_actor_name"))
        row["party"] = party or normalize_space(candidate.get("party"))
        row["actor_verification"] = verification

        actor_in_scope = actor_scope_covers(extraction.publication_date, source)
        if not actor_in_scope:
            row["actor_verification"] = "outside_configured_actor_scope"

        if extraction.publication_date and not date_in_range(
            extraction.publication_date, args.start, args.end
        ):
            status, reason, proposed_segments = "excluded", "outside_date_range", []
        elif not actor_in_scope:
            status, reason, proposed_segments = (
                "excluded",
                "actor_outside_source_scope",
                [],
            )
        else:
            status, reason, proposed_segments = determine_status_and_segments(
                extraction, row, source, args.min_text_chars
            )

        content_hash = row["content_sha256"]
        if content_hash and content_hash in content_seen:
            previous_document_id, previous_status = content_seen[content_hash]
            row["is_exact_duplicate"] = True
            row["duplicate_of_document_id"] = previous_document_id
            if status == "included" and previous_status == "included":
                status, reason, proposed_segments = "review", "exact_duplicate", []
            elif status == "included" and previous_status != "included":
                # Prefer the first high-confidence source over an earlier
                # review/excluded copy of the same text.
                content_seen[content_hash] = (row["document_id"], status)
        elif content_hash:
            content_seen[content_hash] = (row["document_id"], status)

        row["document_status"] = status
        row["status_reason"] = reason
        row["manual_review_required"] = True

        document_segments: List[Dict[str, Any]] = []
        if status == "included":
            for index, (segment_text, method) in enumerate(proposed_segments, start=1):
                document_segments.append(build_segment(row, segment_text, index, method))
        row["segment_count"] = len(document_segments)

        inventory_rows.append(dict(row))
        document_rows.append(
            {
                **row,
                "text": extraction.body_text,
                "text_scope": normalize_space(source.get("text_scope"))
                or "cleaned_page_body_internal_only",
            }
        )
        segment_rows.extend(document_segments)
        audit_rows.append(audit_from_inventory(row))
        print(
            f"  {status}: {reason}; {len(document_segments)} segment(s)",
            flush=True,
        )

    inventory_rows.sort(key=lambda row: (str(row.get("source_id")), str(row.get("canonical_url"))))
    document_rows.sort(key=lambda row: (str(row.get("source_id")), str(row.get("canonical_url"))))
    segment_rows.sort(key=lambda row: (str(row.get("document_id")), int(row.get("segment_index") or 0)))
    audit_rows.sort(key=lambda row: (str(row.get("source_id")), str(row.get("canonical_url"))))
    stimulus_rows = [segment_to_stimulus(row) for row in segment_rows]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "inventory": args.output_dir / "moncloa_inventory.csv",
        "documents_internal": args.output_dir / "moncloa_documents_internal.csv",
        "segments_internal": args.output_dir / "moncloa_segments_internal.csv",
        "stimuli": args.output_dir / "moncloa_stimuli.csv",
        "audit": args.output_dir / "moncloa_extraction_audit.csv",
        "summary": args.output_dir / "moncloa_summary.json",
    }
    write_csv(paths["inventory"], inventory_rows, INVENTORY_FIELDS)
    write_csv(paths["documents_internal"], document_rows, DOCUMENT_FIELDS)
    write_csv(paths["segments_internal"], segment_rows, SEGMENT_FIELDS)
    write_csv(paths["stimuli"], stimulus_rows, STIMULUS_FIELDS)
    write_csv(paths["audit"], audit_rows, AUDIT_FIELDS)

    output_files = {key: path.name for key, path in paths.items()}
    summary = build_summary(
        args,
        total_candidates=len(candidates),
        selected_candidates=selected_count,
        inventory=inventory_rows,
        segments=segment_rows,
        output_files=output_files,
    )
    with paths["summary"].open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
