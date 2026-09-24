#!/usr/bin/env python3
"""Discover official La Moncloa pages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag


# Version included in the User-Agent and the collection summary.
SCRIPT_VERSION = "0.2.0"

# This project revision intentionally limits the generic moncloa pipeline to the
# official La Moncloa archive. Party-site collectors are outside this dataset.
MONCLOA_SOURCE_ID = "lamoncloa_president_interventions"

# A short crawler name makes the purpose of the requests clear to publishers.
USER_AGENT_NAME = "academic-spanish-political-communication"

# Temporary failures that may succeed after a short pause.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Redirects are followed manually so every destination can be validated first.
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

# These parameters normally identify a visit, not the underlying document.
DEFAULT_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}

# A listing page should be small.  The limit also protects against accidental
# downloads of videos or very large files during discovery.
DEFAULT_MAX_RESPONSE_BYTES = 15 * 1024 * 1024

# Fields written to the candidate inventory.  The inventory contains no full
# transcripts, so it is safe to inspect before running the extraction stage.
CANDIDATE_FIELDS = [
    "candidate_id",
    "source_id",
    "source_name",
    "publisher",
    "publisher_type",
    "expected_actor_id",
    "expected_actor_name",
    "party",
    "title_hint",
    "date_hint",
    "url",
    "canonical_url",
    "content_type_hint",
    "discovery_method",
    "discovery_url",
    "discovery_page",
    "discovered_at_utc",
    "listing_cache_path",
    "listing_sha256",
    "legal_notice_url",
    "redistribution_status",
    "default_document_status",
]

# Default selectors for the official La Moncloa archive.
MONCLOA_RESULT_SELECTOR = "li.advanced-new"
MONCLOA_LINK_SELECTOR = "p.title-advanced-news > a"
MONCLOA_DATE_SELECTOR = "p.sitedate > span.date"
MONCLOA_CALLBACK_ID = (
    "ctl00$PlaceHolderMain$DisplayMode$sumarioPaginado$Summary"
)

# ASP.NET callback links contain strings such as DoListItems('1;2').
MONCLOA_CALLBACK_RE = re.compile(
    r"DoListItems\(\s*['\"](?P<argument>[^'\"]+)['\"]\s*\)",
    re.IGNORECASE,
)

# Spanish month names are used only to standardise date hints.  Accents remain
# untouched in titles and in the text downloaded later by the extractor.
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


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_space(value: object) -> str:
    """Collapse repeated whitespace while preserving letters and accents."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sha256(value: object) -> str:
    """Return a SHA-256 digest for bytes or UTF-8 text."""
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: object) -> str:
    """Named alias used when the caller wants to document that input is text."""
    return sha256(value)


def sha256_file(path: Path) -> str:
    """Calculate the SHA-256 checksum of a file without loading it at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: object) -> None:
    """Write JSON atomically so an interrupted run cannot leave half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def write_csv(path: Path, rows: Iterable[Mapping[str, object]], fields: Sequence[str]) -> None:
    """Write a UTF-8 CSV atomically using a fixed, documented column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    temporary.replace(path)


def build_session(contact_email: Optional[str] = None) -> requests.Session:
    """Create a session that identifies this academic collection script.

    The contact email is optional.  When supplied, it lets a site administrator
    contact the research team if the collector causes a problem.
    """
    contact = f"; contact={contact_email}" if contact_email else ""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": f"{USER_AGENT_NAME}/{SCRIPT_VERSION}{contact}",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
            "Accept-Language": "es,en;q=0.5",
        }
    )
    return session


def _normalise_host(host: str) -> str:
    """Return a lowercase IDNA host without a trailing dot."""
    return host.rstrip(".").encode("idna").decode("ascii").casefold()


def host_is_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
    """Check a host against an explicit host or parent-domain allow-list."""
    candidate = _normalise_host(host)
    for value in allowed_hosts:
        allowed = _normalise_host(str(value).strip())
        if candidate == allowed or candidate.endswith("." + allowed):
            return True
    return False


def canonicalize_url(
    value: object,
    base_url: Optional[str] = None,
    allowed_hosts: Optional[Sequence[str]] = None,
    tracking_parameters: Optional[Sequence[str]] = None,
) -> str:
    """Return a stable HTTPS URL or raise ``ValueError`` when it is unsafe.

    Relative links are resolved against ``base_url``.  Tracking parameters are
    removed, meaningful query parameters are sorted, and fragments are dropped.
    The function never accepts credentials or a non-HTTP URL.
    """
    raw = html.unescape(normalize_space(value))
    if not raw:
        raise ValueError("The URL is empty.")
    absolute = urljoin(base_url or "", raw)
    split = urlsplit(absolute)
    if split.scheme.casefold() not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {split.scheme or '(missing)'}")
    if split.username or split.password:
        raise ValueError("URLs containing credentials are not accepted.")
    if not split.hostname:
        raise ValueError("The URL has no host.")

    host = _normalise_host(split.hostname)
    if allowed_hosts and not host_is_allowed(host, allowed_hosts):
        raise ValueError(f"Host is not allowed for this source: {host}")

    try:
        port = split.port
    except ValueError as error:
        raise ValueError("The URL contains an invalid port.") from error
    if port not in {None, 80, 443}:
        raise ValueError(f"Non-standard ports are not accepted: {port}")

    # Official pages are expected to support HTTPS.  Converting here prevents
    # an accidental initial clear-text request when an old page uses http://.
    scheme = "https"
    netloc = host
    if port == 443:
        netloc = f"{host}:443"

    configured_tracking = {str(item).casefold() for item in tracking_parameters or []}
    parameters: List[Tuple[str, str]] = []
    for key, item_value in parse_qsl(split.query, keep_blank_values=True):
        lowered = key.casefold()
        if (
            lowered.startswith("utm_")
            or lowered in DEFAULT_TRACKING_PARAMETERS
            or lowered in configured_tracking
        ):
            continue
        parameters.append((key, item_value))
    parameters.sort(key=lambda pair: (pair[0].casefold(), pair[1]))

    path = re.sub(r"/{2,}", "/", split.path or "/")
    return urlunsplit((scheme, netloc, path, urlencode(parameters, doseq=True), ""))


def _decode_body(body: bytes, content_type: str, suggested_encoding: str = "") -> Tuple[str, str]:
    """Decode an HTML/JSON response using its declared encoding when possible."""
    encoding = suggested_encoding.strip()
    if not encoding:
        match = re.search(r"charset\s*=\s*['\"]?([^;'\"\s]+)", content_type, re.I)
        if match:
            encoding = match.group(1)
    if not encoding:
        prefix = body[:4096].decode("ascii", errors="ignore")
        match = re.search(r"charset\s*=\s*['\"]?([^;'\"\s/>]+)", prefix, re.I)
        if match:
            encoding = match.group(1)
    for candidate in (encoding, "utf-8", "windows-1252"):
        if not candidate:
            continue
        try:
            return body.decode(candidate), candidate
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8"


@dataclass
class FetchResult:
    """A response loaded either from the official site or the local cache."""

    request_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    encoding: str
    retrieved_at_utc: str
    body_sha256: str
    cache_path: str
    metadata_path: str
    response_headers: Dict[str, str]
    from_cache: bool = False

    @property
    def text(self) -> str:
        """Return the already downloaded body as decoded text."""
        return _decode_body(self.body, self.content_type, self.encoding)[0]

    def json(self) -> Any:
        """Parse the response body as JSON."""
        return json.loads(self.text)


class FetchError(RuntimeError):
    """A controlled download failure with optional HTTP status information."""

    def __init__(self, message: str, url: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class Fetcher:
    """Polite HTTP client with host validation, caching, and provenance.

    ``cache_dir`` normally points to ``data/cache/moncloa`` and
    ``provenance_root`` to ``data``.  Paths written to CSV files are then
    relative to the data directory rather than tied to one computer.
    """

    def __init__(
        self,
        session: requests.Session,
        cache_dir: Path,
        *,
        provenance_root: Optional[Path] = None,
        delay_seconds: float = 2.0,
        offline: bool = False,
        refresh: bool = False,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        timeout: Tuple[float, float] = (15.0, 90.0),
        max_attempts: int = 3,
    ) -> None:
        self.session = session
        self.cache_dir = cache_dir
        self.provenance_root = provenance_root
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.offline = offline
        self.refresh = refresh
        self.max_response_bytes = int(max_response_bytes)
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self._last_request_by_host: Dict[str, float] = {}
        self._robots: Dict[str, RobotFileParser] = {}
        self._robots_delays: Dict[str, float] = {}
        self.manifest_records: List[Dict[str, object]] = []

    def _display_path(self, path: Path) -> str:
        """Prefer a portable path relative to the output data directory."""
        if self.provenance_root is not None:
            try:
                return path.resolve().relative_to(self.provenance_root.resolve()).as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def _cache_identity(
        self,
        method: str,
        url: str,
        data: Optional[Mapping[str, object]],
    ) -> Tuple[str, str]:
        """Return a cache key and a digest of any POST payload."""
        serialised_data = ""
        if data is not None:
            serialised_data = json.dumps(
                sorted((str(key), str(value)) for key, value in data.items()),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        payload_digest = sha256(serialised_data) if serialised_data else ""
        key = sha256(f"{method.upper()}\n{url}\n{payload_digest}")
        return key, payload_digest

    def _cache_paths(self, source_id: str, key: str) -> Tuple[Path, Path]:
        """Return the body prefix and metadata path for one cached request."""
        safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_id).strip("_") or "source"
        directory = self.cache_dir / safe_source
        return directory / key, directory / f"{key}.meta.json"

    def _load_cache(self, metadata_path: Path) -> Optional[FetchResult]:
        """Load and verify one cache entry; return ``None`` if it is incomplete."""
        if not metadata_path.exists():
            return None
        try:
            metadata = load_json(metadata_path)
            body_path = metadata_path.parent / str(metadata["body_filename"])
            body = body_path.read_bytes()
            if sha256(body) != metadata["body_sha256"]:
                raise ValueError("checksum mismatch")
            result = FetchResult(
                request_url=str(metadata["request_url"]),
                final_url=str(metadata["final_url"]),
                status_code=int(metadata["status_code"]),
                content_type=str(metadata.get("content_type", "")),
                body=body,
                encoding=str(metadata.get("encoding", "")),
                retrieved_at_utc=str(metadata["retrieved_at_utc"]),
                body_sha256=str(metadata["body_sha256"]),
                cache_path=self._display_path(body_path),
                metadata_path=self._display_path(metadata_path),
                response_headers={
                    str(key): str(value)
                    for key, value in dict(metadata.get("response_headers", {})).items()
                },
                from_cache=True,
            )
            self._record_manifest(result, cache_status="hit")
            return result
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            if self.offline:
                raise FetchError(
                    f"Cached response is invalid: {metadata_path} ({error})"
                ) from error
            return None

    @staticmethod
    def _extension(content_type: str, body: bytes) -> str:
        """Choose a readable cache extension from the response type."""
        lowered = content_type.casefold()
        stripped = body.lstrip()
        if "json" in lowered or stripped.startswith((b"{", b"[")):
            return ".json"
        if "html" in lowered or stripped.startswith((b"<!doctype", b"<html", b"<HTML")):
            return ".html"
        if "pdf" in lowered or stripped.startswith(b"%PDF"):
            return ".pdf"
        if lowered.startswith("text/"):
            return ".txt"
        return ".bin"

    def _store_cache(
        self,
        source_id: str,
        key: str,
        payload_digest: str,
        method: str,
        request_url: str,
        final_url: str,
        status_code: int,
        headers: Mapping[str, str],
        body: bytes,
        retrieved_at: str,
    ) -> FetchResult:
        """Store response bytes and a small provenance sidecar atomically."""
        prefix, metadata_path = self._cache_paths(source_id, key)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        headers_by_lower = {str(name).casefold(): value for name, value in headers.items()}
        content_type = str(headers_by_lower.get("content-type", ""))
        extension = self._extension(content_type, body)
        body_path = prefix.with_suffix(extension)
        body_digest = sha256(body)
        text, encoding = _decode_body(body, content_type)
        del text  # Decoding here validates/records the encoding; bytes remain canonical.

        temporary_body = body_path.with_suffix(body_path.suffix + ".tmp")
        temporary_body.write_bytes(body)
        temporary_body.replace(body_path)

        selected_headers: Dict[str, str] = {}
        for key_name in (
            "Content-Type",
            "Content-Length",
            "ETag",
            "Last-Modified",
            "X-WP-Total",
            "X-WP-TotalPages",
        ):
            value = headers_by_lower.get(key_name.casefold())
            if value is not None:
                selected_headers[key_name] = str(value)
        metadata = {
            "request_method": method.upper(),
            "request_url": request_url,
            "request_payload_sha256": payload_digest,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "encoding": encoding,
            "retrieved_at_utc": retrieved_at,
            "body_filename": body_path.name,
            "body_sha256": body_digest,
            "response_headers": selected_headers,
        }
        write_json(metadata_path, metadata)
        result = FetchResult(
            request_url=request_url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            body=body,
            encoding=encoding,
            retrieved_at_utc=retrieved_at,
            body_sha256=body_digest,
            cache_path=self._display_path(body_path),
            metadata_path=self._display_path(metadata_path),
            response_headers=selected_headers,
            from_cache=False,
        )
        self._record_manifest(result, cache_status="written")
        return result

    def _record_manifest(self, result: FetchResult, cache_status: str) -> None:
        """Add one successful response to the run-level provenance manifest."""
        self.manifest_records.append(
            {
                "request_url": result.request_url,
                "final_url": result.final_url,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "retrieved_at_utc": result.retrieved_at_utc,
                "body_sha256": result.body_sha256,
                "cache_path": result.cache_path,
                "metadata_path": result.metadata_path,
                "cache_status": cache_status,
            }
        )

    def _record_error(self, url: str, message: str, status_code: Optional[int]) -> None:
        """Keep failed requests visible in the run-level manifest."""
        self.manifest_records.append(
            {
                "request_url": url,
                "status_code": status_code,
                "error": message,
                "recorded_at_utc": utc_now(),
                "cache_status": "error",
            }
        )

    def _wait_for_host(self, host: str, extra_delay: float = 0.0) -> None:
        """Wait long enough to respect the configured and robots crawl delays."""
        required = max(self.delay_seconds, extra_delay)
        previous = self._last_request_by_host.get(host)
        if previous is not None:
            remaining = required - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)

    @staticmethod
    def _retry_after_seconds(value: str) -> float:
        """Parse Retry-After seconds or an HTTP date, capped at one minute."""
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return min(60.0, max(0.0, float(text)))
        except ValueError:
            try:
                moment = parsedate_to_datetime(text)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                return min(60.0, max(0.0, (moment - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return 0.0

    def _read_limited_response(self, response: requests.Response) -> bytes:
        """Read a streamed response while enforcing the maximum byte limit."""
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > self.max_response_bytes:
            raise FetchError(
                f"Response is larger than the {self.max_response_bytes}-byte limit.",
                response.url,
                response.status_code,
            )
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > self.max_response_bytes:
                raise FetchError(
                    f"Response exceeded the {self.max_response_bytes}-byte limit.",
                    response.url,
                    response.status_code,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _robots_policy(self, url: str, allowed_hosts: Sequence[str]) -> RobotFileParser:
        """Load and cache the site's robots policy using the same safe client."""
        split = urlsplit(url)
        origin = f"https://{split.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser = RobotFileParser()
        robots_url = f"{origin}/robots.txt"
        try:
            result = self.fetch(
                robots_url,
                source_id=f"robots_{split.hostname}",
                allowed_hosts=allowed_hosts,
                check_robots=False,
            )
            parser.set_url(robots_url)
            parser.parse(result.text.splitlines())
        except FetchError as error:
            if error.status_code in {401, 403}:
                # A forbidden robots file is treated conservatively.
                parser.parse(["User-agent: *", "Disallow: /"])
            elif error.status_code in {404, 410}:
                # Only an explicitly missing robots file means that no rules exist.
                parser.parse([])
            else:
                # Temporary blocks (for example 429), server failures, and network
                # errors are ambiguous.  Stop instead of assuming permission.
                raise FetchError(
                    f"Could not verify robots.txt for {origin}: {error}",
                    robots_url,
                    error.status_code,
                ) from error

        crawl_delay = parser.crawl_delay(USER_AGENT_NAME) or parser.crawl_delay("*")
        if crawl_delay is not None:
            self._robots_delays[split.hostname or ""] = float(crawl_delay)
        self._robots[origin] = parser
        return parser

    def fetch(
        self,
        url: str,
        *,
        source_id: str,
        allowed_hosts: Sequence[str],
        method: str = "GET",
        data: Optional[Mapping[str, object]] = None,
        headers: Optional[Mapping[str, str]] = None,
        check_robots: bool = True,
    ) -> FetchResult:
        """Fetch one allowed URL, or reuse its verified cache entry.

        Redirects are handled one at a time and validated before the next
        request.  POST payload values are not copied into provenance files;
        only their digest is stored.
        """
        if not allowed_hosts:
            raise FetchError("The source has no allowed_hosts; refusing the request.", url)
        current_url = canonicalize_url(url, allowed_hosts=allowed_hosts)
        current_method = method.upper()
        current_data = data
        key, payload_digest = self._cache_identity(current_method, current_url, current_data)
        _, metadata_path = self._cache_paths(source_id, key)

        if not self.refresh:
            cached = self._load_cache(metadata_path)
            if cached is not None:
                try:
                    canonicalize_url(cached.final_url, allowed_hosts=allowed_hosts)
                except ValueError as error:
                    raise FetchError(
                        f"Cached final URL is outside the source allow-list: {error}",
                        cached.final_url,
                    ) from error
                return cached
        if self.offline:
            raise FetchError(f"No cached response is available in offline mode: {current_url}", current_url)

        request_url = current_url
        redirects = 0
        attempt = 0
        while True:
            split = urlsplit(current_url)
            host = split.hostname or ""
            if check_robots:
                policy = self._robots_policy(current_url, allowed_hosts)
                user_agent = self.session.headers.get("User-Agent", USER_AGENT_NAME)
                if not policy.can_fetch(user_agent, current_url):
                    message = f"robots.txt does not permit this URL: {current_url}"
                    self._record_error(current_url, message, None)
                    raise FetchError(message, current_url)
            robots_delay = self._robots_delays.get(host, 0.0)
            self._wait_for_host(host, robots_delay)
            attempt += 1
            response: Optional[requests.Response] = None
            try:
                response = self.session.request(
                    current_method,
                    current_url,
                    data=current_data,
                    headers=dict(headers or {}),
                    stream=True,
                    allow_redirects=False,
                    timeout=self.timeout,
                )
                self._last_request_by_host[host] = time.monotonic()

                if response.status_code in REDIRECT_STATUS_CODES:
                    location = response.headers.get("Location", "")
                    response.close()
                    if not location:
                        raise FetchError("Redirect response has no Location header.", current_url)
                    redirects += 1
                    if redirects > 5:
                        raise FetchError("Too many redirects.", current_url)
                    # Validation occurs before any request reaches the new host.
                    current_url = canonicalize_url(
                        location,
                        base_url=current_url,
                        allowed_hosts=allowed_hosts,
                    )
                    if response.status_code == 303 or (
                        response.status_code in {301, 302} and current_method == "POST"
                    ):
                        current_method = "GET"
                        current_data = None
                    attempt = 0
                    continue

                if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_attempts:
                    retry_after = self._retry_after_seconds(response.headers.get("Retry-After", ""))
                    response.close()
                    time.sleep(max(retry_after, min(8.0, 2.0 ** (attempt - 1))))
                    continue

                if response.status_code >= 400:
                    status = response.status_code
                    response.close()
                    message = f"HTTP {status} while requesting {current_url}"
                    self._record_error(current_url, message, status)
                    raise FetchError(message, current_url, status)

                response_status = response.status_code
                body = self._read_limited_response(response)
                retrieved_at = utc_now()
                final_url = canonicalize_url(
                    response.url or current_url,
                    allowed_hosts=allowed_hosts,
                )
                response_headers = dict(response.headers)
                response.close()
                return self._store_cache(
                    source_id=source_id,
                    key=key,
                    payload_digest=payload_digest,
                    method=method,
                    request_url=request_url,
                    final_url=final_url,
                    status_code=response_status,
                    headers=response_headers,
                    body=body,
                    retrieved_at=retrieved_at,
                )
            except FetchError:
                if response is not None:
                    response.close()
                raise
            except requests.RequestException as error:
                if response is not None:
                    response.close()
                if attempt < self.max_attempts:
                    time.sleep(min(8.0, 2.0 ** (attempt - 1)))
                    continue
                message = f"Network error while requesting {current_url}: {error}"
                self._record_error(current_url, message, None)
                raise FetchError(message, current_url) from error

    def fetch_html(
        self,
        url: str,
        *,
        source_id: str,
        allowed_hosts: Sequence[str],
        method: str = "GET",
        data: Optional[Mapping[str, object]] = None,
        headers: Optional[Mapping[str, str]] = None,
        check_robots: bool = True,
    ) -> FetchResult:
        """Fetch a page and reject obvious non-HTML responses."""
        result = self.fetch(
            url,
            source_id=source_id,
            allowed_hosts=allowed_hosts,
            method=method,
            data=data,
            headers=headers,
            check_robots=check_robots,
        )
        content_type = result.content_type.casefold()
        # ``body`` is bytes here; byte strings support ``lower()``, not
        # ``casefold()``.  ASCII HTML markers are sufficient for this check.
        beginning = result.body.lstrip()[:200].lower()
        if "html" not in content_type and not beginning.startswith((b"<!doctype", b"<html")):
            raise FetchError(
                f"Expected HTML but received {result.content_type or 'an unknown content type'}.",
                result.final_url,
                result.status_code,
            )
        return result

    def write_manifest(self, path: Path) -> None:
        """Write the response-level provenance collected during this run."""
        write_json(
            path,
            {
                "generated_at_utc": utc_now(),
                "script_version": SCRIPT_VERSION,
                "requests": self.manifest_records,
            },
        )


def fetch_html(fetcher: Fetcher, url: str, **kwargs: object) -> FetchResult:
    """Small public wrapper that the extraction script can import."""
    return fetcher.fetch_html(url, **kwargs)  # type: ignore[arg-type]


def _plain_text_from_html(value: object) -> str:
    """Convert a short HTML title into plain text."""
    return normalize_space(BeautifulSoup(html.unescape(str(value or "")), "html.parser").get_text(" "))


def parse_date_hint(value: object) -> str:
    """Convert common official-site date formats to YYYY-MM-DD when possible."""
    text = normalize_space(value)
    if not text:
        return ""
    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            pass
    for pattern in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    spanish = re.search(
        r"\b(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+de\s+(\d{4})\b",
        text,
        re.IGNORECASE,
    )
    if spanish:
        month_name = _matching_text(spanish.group(2))
        month = SPANISH_MONTHS.get(month_name)
        if month:
            try:
                return date(int(spanish.group(3)), month, int(spanish.group(1))).isoformat()
            except ValueError:
                pass
    # Some party templates use an English-style order with a Spanish month,
    # for example "septiembre 11, 2026".
    folded = _matching_text(text)
    month_names = "|".join(sorted(SPANISH_MONTHS, key=len, reverse=True))
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
    compact = re.search(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)", text)
    if compact:
        try:
            return date(
                int(compact.group(1)),
                int(compact.group(2)),
                int(compact.group(3)),
            ).isoformat()
        except ValueError:
            pass
    return ""


def _matching_text(value: object) -> str:
    """Create an accent-insensitive form used only for matching, never storage."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    return normalize_space(re.sub(r"[^a-z0-9]+", " ", without_marks.casefold()))


def _load_politicians(path: Path) -> Dict[str, Dict[str, object]]:
    """Load the existing politician configuration keyed by actor_id."""
    payload = load_json(path)
    if isinstance(payload, dict):
        records = payload.get("politicians", payload.get("actors", []))
    else:
        records = payload
    if not isinstance(records, list):
        raise ValueError("politicians.json must contain a list of politicians.")
    politicians: Dict[str, Dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict) or not normalize_space(record.get("actor_id")):
            raise ValueError("Every politician must have an actor_id.")
        actor_id = normalize_space(record["actor_id"])
        politicians[actor_id] = dict(record)
    return politicians


def _actor_terms(actor: Mapping[str, object]) -> List[str]:
    """Return display-name and alias forms suitable for conservative matching."""
    values: List[object] = [actor.get("display_name", "")]
    aliases = actor.get("marker_aliases", [])
    if isinstance(aliases, list):
        values.extend(aliases)
    terms = [_matching_text(value) for value in values]
    return sorted({term for term in terms if term}, key=len, reverse=True)


def _hint_mentions_actor(title: str, actor: Mapping[str, object]) -> bool:
    """Check whether a listing title explicitly names the expected politician."""
    normalised = f" {_matching_text(title)} "
    return any(f" {term} " in normalised for term in _actor_terms(actor))


def _date_in_range(date_hint: str, start: Optional[date], end: Optional[date]) -> bool:
    """Apply a date filter when a candidate has a parseable date hint."""
    if not date_hint:
        return True
    try:
        value = date.fromisoformat(date_hint)
    except ValueError:
        return True
    return not ((start and value < start) or (end and value > end))


def _content_type_hint(url: str) -> str:
    """Mark PDFs so the later HTML extractor will not try to parse them."""
    return "pdf" if urlsplit(url).path.casefold().endswith(".pdf") else "html"


@dataclass
class SourceStats:
    """Counters and explicit stop information for one configured source."""

    source_id: str
    pages_attempted: int = 0
    pages_collected: int = 0
    candidates_seen: int = 0
    candidates_written: int = 0
    duplicates_skipped: int = 0
    rejected_links: int = 0
    pagination_completed: bool = True
    stop_reason: str = "completed"
    errors: List[str] = field(default_factory=list)

    def error(self, message: object) -> None:
        """Record a concise error without interrupting unrelated sources."""
        text = normalize_space(message)
        if text:
            self.errors.append(text)

    def as_dict(self) -> Dict[str, object]:
        """Return a JSON-serialisable representation."""
        return {
            "source_id": self.source_id,
            "pages_attempted": self.pages_attempted,
            "pages_collected": self.pages_collected,
            "candidates_seen": self.candidates_seen,
            "candidates_written": self.candidates_written,
            "duplicates_skipped": self.duplicates_skipped,
            "rejected_links": self.rejected_links,
            "pagination_completed": self.pagination_completed,
            "stop_reason": self.stop_reason,
            "errors": self.errors,
        }


class CandidateStore:
    """Validate, filter, and deduplicate candidates before writing the CSV."""

    def __init__(
        self,
        politicians: Mapping[str, Mapping[str, object]],
        tracking_parameters: Sequence[str],
        start: Optional[date],
        end: Optional[date],
        max_candidates_per_source: Optional[int],
    ) -> None:
        self.politicians = politicians
        self.tracking_parameters = tracking_parameters
        self.start = start
        self.end = end
        self.max_candidates_per_source = max_candidates_per_source
        self.rows: Dict[Tuple[str, str], Dict[str, object]] = {}
        self.count_by_source: Counter[str] = Counter()

    def source_is_full(self, source_id: str) -> bool:
        """Return whether the optional per-source candidate limit was reached."""
        return bool(
            self.max_candidates_per_source is not None
            and self.count_by_source[source_id] >= self.max_candidates_per_source
        )

    def add(
        self,
        source: Mapping[str, object],
        stats: SourceStats,
        *,
        url: object,
        title_hint: object = "",
        date_hint: object = "",
        discovery_method: str,
        discovery_url: str = "",
        discovery_page: object = "",
        listing_result: Optional[FetchResult] = None,
        actor_id_override: str = "",
        allow_seed_pdf: bool = False,
    ) -> bool:
        """Add one candidate after all source, actor, URL, and date checks."""
        source_id = normalize_space(source.get("source_id"))
        stats.candidates_seen += 1
        if self.source_is_full(source_id):
            stats.pagination_completed = False
            stats.stop_reason = "max_candidates_per_source"
            return False

        allowed_hosts = source.get("allowed_hosts", [])
        if not isinstance(allowed_hosts, list) or not allowed_hosts:
            stats.rejected_links += 1
            stats.error("Source has no allowed_hosts.")
            return False
        try:
            canonical = canonicalize_url(
                url,
                base_url=discovery_url or None,
                allowed_hosts=[str(item) for item in allowed_hosts],
                tracking_parameters=self.tracking_parameters,
            )
        except ValueError as error:
            stats.rejected_links += 1
            stats.error(f"Rejected link {normalize_space(url)}: {error}")
            return False

        pattern = normalize_space(source.get("detail_url_pattern"))
        is_pdf = _content_type_hint(canonical) == "pdf"
        if pattern:
            try:
                matches_pattern = re.search(pattern, canonical, re.IGNORECASE) is not None
            except re.error as error:
                raise ValueError(f"Invalid detail_url_pattern for {source_id}: {error}") from error
            if not matches_pattern and not (allow_seed_pdf and is_pdf):
                stats.rejected_links += 1
                return False

        actor_id = actor_id_override or normalize_space(source.get("expected_actor_id"))
        actor = self.politicians.get(actor_id)
        if actor is None:
            stats.rejected_links += 1
            stats.error(f"Unknown expected_actor_id: {actor_id or '(empty)'}")
            return False

        title = _plain_text_from_html(title_hint)
        if bool(source.get("require_actor_in_hint")) and not _hint_mentions_actor(title, actor):
            stats.rejected_links += 1
            return False
        standard_date = parse_date_hint(date_hint) or parse_date_hint(canonical)
        if not _date_in_range(standard_date, self.start, self.end):
            stats.rejected_links += 1
            return False

        key = (source_id, canonical)
        if key in self.rows:
            stats.duplicates_skipped += 1
            return False

        discovered_at = listing_result.retrieved_at_utc if listing_result else utc_now()
        row: Dict[str, object] = {
            "candidate_id": "moncloa_" + sha256(f"{source_id}\n{canonical}")[:24],
            "source_id": source_id,
            "source_name": normalize_space(source.get("source_name")),
            "publisher": normalize_space(source.get("publisher")),
            "publisher_type": normalize_space(source.get("publisher_type")),
            "expected_actor_id": actor_id,
            "expected_actor_name": normalize_space(actor.get("display_name")),
            "party": normalize_space(actor.get("party")),
            "title_hint": title,
            "date_hint": standard_date,
            "url": normalize_space(url),
            "canonical_url": canonical,
            "content_type_hint": _content_type_hint(canonical),
            "discovery_method": discovery_method,
            "discovery_url": discovery_url,
            "discovery_page": discovery_page,
            "discovered_at_utc": discovered_at,
            "listing_cache_path": listing_result.cache_path if listing_result else "",
            "listing_sha256": listing_result.body_sha256 if listing_result else "",
            "legal_notice_url": normalize_space(source.get("legal_notice_url")),
            "redistribution_status": normalize_space(source.get("redistribution_status")),
            "default_document_status": normalize_space(
                source.get("default_document_status") or source.get("default_status", "review")
            ),
        }
        self.rows[key] = row
        self.count_by_source[source_id] += 1
        stats.candidates_written += 1
        return True


def _find_control(soup: BeautifulSoup, suffix: str, tag_name: str) -> Optional[Tag]:
    """Find an ASP.NET control whose id ends with a stable local name."""
    lowered_suffix = suffix.casefold()
    for tag in soup.find_all(tag_name):
        identifier = str(tag.get("id", ""))
        name = str(tag.get("name", ""))
        if identifier.casefold().endswith(lowered_suffix) or name.casefold().endswith(lowered_suffix):
            return tag
    return None


def _hidden_fields(soup: BeautifulSoup) -> Dict[str, str]:
    """Extract named hidden fields required for an ASP.NET postback."""
    fields: Dict[str, str] = {}
    for tag in soup.select("input[type='hidden'][name]"):
        fields[str(tag.get("name"))] = str(tag.get("value", ""))
    return fields


def _control_name(tag: Optional[Tag], fallback: str) -> str:
    """Use the page's full ASP.NET name, falling back to a known default."""
    if tag is None:
        return fallback
    return str(tag.get("name") or fallback)


def _parse_int_field(soup: BeautifulSoup, suffix: str) -> Optional[int]:
    """Read an integer from a hidden field identified by its id/name suffix."""
    tag = _find_control(soup, suffix, "input")
    if tag is None:
        return None
    match = re.search(r"\d+", str(tag.get("value", "")))
    return int(match.group(0)) if match else None


def _moncloa_callback_arguments(soup: BeautifulSoup) -> Dict[int, str]:
    """Map visible page numbers to their exact DoListItems callback arguments."""
    arguments: Dict[int, str] = {}
    for tag in soup.find_all(onclick=True):
        match = MONCLOA_CALLBACK_RE.search(str(tag.get("onclick", "")))
        if not match:
            continue
        argument = match.group("argument")
        final_number = re.search(r"(\d+)\s*$", argument)
        if final_number:
            arguments[int(final_number.group(1))] = argument
    return arguments


def _parse_moncloa_callback(text: str) -> Tuple[str, str]:
    """Return the updated EVENTVALIDATION token and HTML callback fragment.

    La Moncloa returns ``N|TOKENHTML`` where N is the exact number of
    characters in the new ``__EVENTVALIDATION`` token.  There is no separator
    between that fixed-length token and the HTML fragment.  The page's
    original ``__VIEWSTATE`` must remain unchanged.
    """
    match = re.match(r"^(\d+)\|", text)
    if not match:
        raise ValueError("Callback response has no length prefix.")
    length = int(match.group(1))
    token_start = match.end()
    token_end = token_start + length
    if token_end > len(text):
        raise ValueError("Callback EVENTVALIDATION token has an invalid length.")
    token = text[token_start:token_end]
    fragment = text[token_end:]
    if not token or not normalize_space(fragment):
        raise ValueError("Callback response is incomplete.")
    return token, fragment


def _moncloa_candidates(
    soup: BeautifulSoup,
    source: Mapping[str, object],
) -> List[Tuple[str, str, str]]:
    """Extract URL, title, and date hints from one La Moncloa result page."""
    discovery = source.get("discovery", {})
    config = discovery if isinstance(discovery, dict) else {}
    item_selector = str(config.get("result_selector", MONCLOA_RESULT_SELECTOR))
    link_selector = str(config.get("link_selector", MONCLOA_LINK_SELECTOR))
    date_selector = str(config.get("date_selector", MONCLOA_DATE_SELECTOR))
    candidates: List[Tuple[str, str, str]] = []
    for item in soup.select(item_selector):
        link = item.select_one(link_selector)
        if link is None or not link.get("href"):
            continue
        date_tag = item.select_one(date_selector)
        candidates.append(
            (
                str(link.get("href")),
                normalize_space(link.get_text(" ")),
                normalize_space(date_tag.get_text(" ")) if date_tag else "",
            )
        )
    return candidates


def _month_floor(value: date) -> date:
    """Return the first day of a date's month."""
    return value.replace(day=1)


def _parse_month(value: object) -> Optional[date]:
    """Parse YYYY-MM or YYYYMM as the first day of that month."""
    text = normalize_space(value)
    if not text:
        return None
    for pattern in ("%Y-%m", "%Y%m"):
        try:
            return datetime.strptime(text, pattern).date().replace(day=1)
        except ValueError:
            pass
    raise ValueError(f"Invalid month {text!r}; expected YYYY-MM.")


def _iter_months(start: date, end: date) -> Iterable[date]:
    """Yield every month from start through end, inclusive."""
    current = _month_floor(start)
    final = _month_floor(end)
    while current <= final:
        yield current
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def _mark_source_fetch_error(stats: SourceStats, error: FetchError) -> None:
    """Translate an HTTP failure into an explicit, machine-readable stop reason."""
    stats.error(error)
    stats.pagination_completed = False
    if error.status_code == 403:
        stats.stop_reason = "access_blocked_http_403"
    elif "robots.txt" in str(error):
        stats.stop_reason = "robots_disallowed_or_unavailable"
    elif "offline mode" in str(error):
        stats.stop_reason = "offline_cache_miss"
    else:
        stats.stop_reason = "fetch_error"


def discover_moncloa(
    source: Mapping[str, object],
    actor: Mapping[str, object],
    fetcher: Fetcher,
    store: CandidateStore,
    stats: SourceStats,
    *,
    start: Optional[date],
    end: Optional[date],
    max_pages: Optional[int],
) -> None:
    """Discover La Moncloa entries through its official ASP.NET archive.

    The archive serves five results per page.  Filtering uses a normal ASP.NET
    POST.  Later pages use a callback response whose length prefix updates
    ``__EVENTVALIDATION``; ``__VIEWSTATE`` deliberately remains unchanged.
    """
    del actor  # The archive itself is tied to source.expected_actor_id.
    discovery = source.get("discovery", {})
    if not isinstance(discovery, dict):
        raise ValueError("La Moncloa discovery must be a JSON object.")
    archive_url = normalize_space(discovery.get("url") or discovery.get("url_template"))
    if not archive_url:
        raise ValueError("La Moncloa discovery requires discovery.url.")
    allowed_hosts = [str(item) for item in source.get("allowed_hosts", [])]
    source_id = normalize_space(source.get("source_id"))

    configured_start = _parse_month(discovery.get("start_month"))
    configured_end = _parse_month(discovery.get("end_month"))
    month_start = _month_floor(start) if start else configured_start
    month_end = _month_floor(end) if end else (configured_end or _month_floor(date.today()))
    if configured_start and month_start:
        month_start = max(month_start, configured_start)
    if configured_end:
        month_end = min(month_end, configured_end)
    if month_start is None:
        raise ValueError(
            f"{source_id}: set --start or discovery.start_month to avoid an unbounded archive crawl."
        )
    if month_start > month_end:
        stats.stop_reason = "empty_date_range"
        return

    pages_used = 0
    for month in _iter_months(month_start, month_end):
        if store.source_is_full(source_id):
            stats.pagination_completed = False
            stats.stop_reason = "max_candidates_per_source"
            return
        if max_pages is not None and pages_used >= max_pages:
            stats.pagination_completed = False
            stats.stop_reason = "max_pages_per_source"
            return

        # Start every month from the complete form so hidden ASP.NET state is
        # never guessed or copied from an unrelated month.
        try:
            bootstrap = fetcher.fetch_html(
                archive_url,
                source_id=source_id,
                allowed_hosts=allowed_hosts,
            )
            bootstrap_soup = BeautifulSoup(bootstrap.body, "html.parser")
            form = bootstrap_soup.find("form")
            post_url = urljoin(archive_url, str(form.get("action", ""))) if form else archive_url
            state = _hidden_fields(bootstrap_soup)
            month_control_id = normalize_space(
                discovery.get("month_select_id") or "ddlMonth"
            )
            year_control_id = normalize_space(
                discovery.get("year_select_id") or "ddlYear"
            )
            submit_control_id = normalize_space(
                discovery.get("submit_id") or "btnSearch"
            )
            month_control = _find_control(bootstrap_soup, month_control_id, "select")
            year_control = _find_control(bootstrap_soup, year_control_id, "select")
            search_control = _find_control(bootstrap_soup, submit_control_id, "input")
            if month_control is None or year_control is None or search_control is None:
                raise ValueError("The expected month/year/search controls were not found.")
            month_name = _control_name(month_control, "ctl00$PlaceHolderMain$DisplayMode$ddlMonth")
            year_name = _control_name(year_control, "ctl00$PlaceHolderMain$DisplayMode$ddlYear")
            search_name = _control_name(search_control, "ctl00$PlaceHolderMain$DisplayMode$btnSearch")
            state[month_name] = str(month.month)
            state[year_name] = str(month.year)
            state[search_name] = str(search_control.get("value", "Buscar"))

            stats.pages_attempted += 1
            filtered = fetcher.fetch_html(
                post_url,
                source_id=source_id,
                allowed_hosts=allowed_hosts,
                method="POST",
                data=state,
                headers={"Referer": archive_url},
            )
            pages_used += 1
            stats.pages_collected += 1
            filtered_soup = BeautifulSoup(filtered.body, "html.parser")
            for item_url, item_title, item_date in _moncloa_candidates(filtered_soup, source):
                store.add(
                    source,
                    stats,
                    url=item_url,
                    title_hint=item_title,
                    date_hint=item_date,
                    discovery_method="aspnet_monthly",
                    discovery_url=filtered.final_url,
                    discovery_page=1,
                    listing_result=filtered,
                )

            # The hidden fields returned by the filtered page are the current
            # state.  Preserve its original VIEWSTATE across all callbacks.
            callback_state = _hidden_fields(filtered_soup)
            callback_state.setdefault(month_name, str(month.month))
            callback_state.setdefault(year_name, str(month.year))
            callback_state.pop(search_name, None)
            result_count = _parse_int_field(filtered_soup, "hdnResultsCount")
            last_page = _parse_int_field(filtered_soup, "hdnLastPage")
            if last_page is None and result_count is not None:
                last_page = max(1, math.ceil(result_count / 5))
            if last_page is None:
                # Absence of a next-page callback is enough to prove that page
                # one is complete; otherwise we refuse to invent pagination.
                last_page = 2 if 2 in _moncloa_callback_arguments(filtered_soup) else 1

            current_soup = filtered_soup
            current_page = 1
            while current_page < last_page:
                if store.source_is_full(source_id):
                    stats.pagination_completed = False
                    stats.stop_reason = "max_candidates_per_source"
                    return
                if max_pages is not None and pages_used >= max_pages:
                    stats.pagination_completed = False
                    stats.stop_reason = "max_pages_per_source"
                    return
                next_page = current_page + 1
                arguments = _moncloa_callback_arguments(current_soup)
                callback_argument = arguments.get(next_page)
                if callback_argument is None:
                    stats.pagination_completed = False
                    stats.stop_reason = "pagination_callback_not_found"
                    stats.error(
                        f"{month:%Y-%m}: page {next_page} was indicated but its callback was not found."
                    )
                    break

                callback_data = dict(callback_state)
                callback_data["__CALLBACKID"] = str(
                    discovery.get("callback_id", MONCLOA_CALLBACK_ID)
                )
                callback_data["__CALLBACKPARAM"] = callback_argument
                stats.pages_attempted += 1
                callback = fetcher.fetch(
                    post_url,
                    source_id=source_id,
                    allowed_hosts=allowed_hosts,
                    method="POST",
                    data=callback_data,
                    headers={"Referer": filtered.final_url},
                )
                pages_used += 1
                try:
                    event_validation, fragment = _parse_moncloa_callback(callback.text)
                except ValueError as error:
                    stats.pagination_completed = False
                    stats.stop_reason = "pagination_callback_invalid"
                    stats.error(f"{month:%Y-%m}, page {next_page}: {error}")
                    break
                callback_state["__EVENTVALIDATION"] = event_validation
                # Do not update __VIEWSTATE: the callback prefix is an updated
                # EVENTVALIDATION token, as verified against the live archive.
                current_soup = BeautifulSoup(fragment, "html.parser")
                current_page = next_page
                stats.pages_collected += 1
                for item_url, item_title, item_date in _moncloa_candidates(current_soup, source):
                    store.add(
                        source,
                        stats,
                        url=item_url,
                        title_hint=item_title,
                        date_hint=item_date,
                        discovery_method="aspnet_monthly",
                        discovery_url=callback.final_url,
                        discovery_page=current_page,
                        listing_result=callback,
                    )
        except FetchError as error:
            _mark_source_fetch_error(stats, error)
            return
        except (TypeError, ValueError) as error:
            stats.error(f"{month:%Y-%m}: {error}")
            stats.pagination_completed = False
            stats.stop_reason = "page_structure_changed"
            return


def discover_wordpress_rest(
    source: Mapping[str, object],
    actor: Mapping[str, object],
    fetcher: Fetcher,
    store: CandidateStore,
    stats: SourceStats,
    *,
    max_pages: Optional[int],
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> None:
    """Discover official WordPress posts through a public REST endpoint."""
    del actor
    discovery = source.get("discovery", {})
    if not isinstance(discovery, dict):
        raise ValueError("wordpress_rest discovery must be a JSON object.")
    endpoint = normalize_space(
        discovery.get("endpoint") or discovery.get("url_template") or discovery.get("url")
    )
    if not endpoint:
        raise ValueError("wordpress_rest discovery requires an endpoint.")
    source_id = normalize_space(source.get("source_id"))
    allowed_hosts = [str(item) for item in source.get("allowed_hosts", [])]
    page = 1
    total_pages: Optional[int] = None
    while True:
        if max_pages is not None and page > max_pages:
            stats.pagination_completed = False
            stats.stop_reason = "max_pages_per_source"
            return
        if store.source_is_full(source_id):
            stats.pagination_completed = False
            stats.stop_reason = "max_candidates_per_source"
            return
        page_url = endpoint.format(page=page)
        # WordPress can filter server-side, avoiding hundreds of irrelevant
        # historical pages that CandidateStore would otherwise discard later.
        split = urlsplit(page_url)
        query = parse_qsl(split.query, keep_blank_values=True)
        query_keys = {key.casefold() for key, _ in query}
        if start and "after" not in query_keys:
            query.append(("after", f"{start.isoformat()}T00:00:00"))
        if end and "before" not in query_keys:
            query.append(("before", f"{end.isoformat()}T23:59:59"))
        page_url = urlunsplit(
            (split.scheme, split.netloc, split.path, urlencode(query, doseq=True), "")
        )
        stats.pages_attempted += 1
        try:
            result = fetcher.fetch(
                page_url,
                source_id=source_id,
                allowed_hosts=allowed_hosts,
                headers={"Accept": "application/json"},
            )
            payload = result.json()
        except FetchError as error:
            _mark_source_fetch_error(stats, error)
            return
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            stats.error(f"Page {page}: invalid WordPress JSON ({error})")
            stats.pagination_completed = False
            stats.stop_reason = "invalid_json"
            return
        if not isinstance(payload, list):
            stats.error(f"Page {page}: WordPress response is not a JSON list.")
            stats.pagination_completed = False
            stats.stop_reason = "invalid_json_shape"
            return
        stats.pages_collected += 1
        if not payload:
            stats.stop_reason = "completed_empty_page"
            return
        for item in payload:
            if not isinstance(item, dict):
                continue
            title_value = item.get("title", "")
            if isinstance(title_value, dict):
                title_value = title_value.get("rendered", "")
            store.add(
                source,
                stats,
                url=item.get("link", ""),
                title_hint=title_value,
                date_hint=item.get("date", ""),
                discovery_method="wordpress_rest",
                discovery_url=result.final_url,
                discovery_page=page,
                listing_result=result,
            )
        if total_pages is None:
            raw_total = result.response_headers.get("X-WP-TotalPages", "")
            if raw_total.isdigit():
                total_pages = int(raw_total)
        if total_pages is not None and page >= total_pages:
            return
        page += 1


def _listing_entries(
    soup: BeautifulSoup,
    source: Mapping[str, object],
) -> List[Tuple[str, str, str]]:
    """Extract conservative link/title/date triples from an HTML archive."""
    discovery = source.get("discovery", {})
    config = discovery if isinstance(discovery, dict) else {}
    item_selector = normalize_space(config.get("item_selector", "article"))
    link_selector = normalize_space(config.get("link_selector", "a[href]"))
    date_selector = normalize_space(config.get("date_selector", "time"))
    entries: List[Tuple[str, str, str]] = []
    items = soup.select(item_selector) if item_selector else []
    if not items:
        items = soup.select("article")
    for item in items:
        link = item.select_one(link_selector) if link_selector else item.find("a", href=True)
        if link is None or not link.get("href"):
            continue
        title = normalize_space(link.get("title") or link.get_text(" "))
        date_tag = item.select_one(date_selector) if date_selector else item.find("time")
        date_value = ""
        if date_tag is not None:
            date_value = normalize_space(date_tag.get("datetime") or date_tag.get_text(" "))
        entries.append((str(link.get("href")), title, date_value))
    return entries


def discover_html_listing(
    source: Mapping[str, object],
    actor: Mapping[str, object],
    fetcher: Fetcher,
    store: CandidateStore,
    stats: SourceStats,
    *,
    max_pages: Optional[int],
    start: Optional[date] = None,
) -> None:
    """Discover candidates from a simple paginated official HTML archive.

    This adapter is suitable for the VOX tag archive.  If the site returns 403,
    collection stops and records the block; it never tries alternative headers,
    proxies, or other circumvention techniques.
    """
    del actor
    discovery = source.get("discovery", {})
    if not isinstance(discovery, dict):
        raise ValueError("html_listing discovery must be a JSON object.")
    template = normalize_space(discovery.get("url_template") or discovery.get("url"))
    first_page_url = normalize_space(discovery.get("first_page_url"))
    if not template:
        raise ValueError("html_listing discovery requires url_template or url.")
    source_id = normalize_space(source.get("source_id"))
    allowed_hosts = [str(item) for item in source.get("allowed_hosts", [])]
    page = 1
    previous_urls: Set[str] = set()
    while True:
        if max_pages is not None and page > max_pages:
            stats.pagination_completed = False
            stats.stop_reason = "max_pages_per_source"
            return
        if store.source_is_full(source_id):
            stats.pagination_completed = False
            stats.stop_reason = "max_candidates_per_source"
            return
        page_url = first_page_url if page == 1 and first_page_url else template.format(page=page)
        stats.pages_attempted += 1
        try:
            result = fetcher.fetch_html(
                page_url,
                source_id=source_id,
                allowed_hosts=allowed_hosts,
            )
        except FetchError as error:
            _mark_source_fetch_error(stats, error)
            return
        stats.pages_collected += 1
        soup = BeautifulSoup(result.body, "html.parser")
        entries = _listing_entries(soup, source)
        if not entries:
            stats.stop_reason = "completed_no_entries"
            return
        current_urls = {entry[0] for entry in entries}
        if current_urls == previous_urls:
            stats.pagination_completed = False
            stats.stop_reason = "repeated_listing_page"
            stats.error(f"Page {page} repeated the preceding page; stopped to avoid a loop.")
            return
        previous_urls = current_urls
        for item_url, item_title, item_date in entries:
            store.add(
                source,
                stats,
                url=item_url,
                title_hint=item_title,
                date_hint=item_date,
                discovery_method="html_listing",
                discovery_url=result.final_url,
                discovery_page=page,
                listing_result=result,
            )
        dated_entries = [
            parse_date_hint(item_date) or parse_date_hint(item_url)
            for item_url, _, item_date in entries
        ]
        dated_entries = [value for value in dated_entries if value]
        if start and dated_entries and all(
            date.fromisoformat(value) < start for value in dated_entries
        ):
            stats.stop_reason = "completed_before_start_date"
            return
        next_selector = normalize_space(
            discovery.get("next_selector", "a.next, a.next.page-numbers, link[rel='next']")
        )
        next_link = soup.select_one(next_selector) if next_selector else None
        if next_link is None or not next_link.get("href"):
            # A missing next link proves completion more reliably than blindly
            # requesting page numbers until a server error appears.
            return
        page += 1


def _source_manual_urls(source: Mapping[str, object]) -> List[Dict[str, object]]:
    """Normalise manual seed URLs stored inside the source configuration."""
    values: object = source.get("manual_urls", [])
    discovery = source.get("discovery", {})
    if isinstance(discovery, dict) and discovery.get("seed_urls"):
        values = list(values if isinstance(values, list) else []) + list(discovery["seed_urls"])
    records: List[Dict[str, object]] = []
    if not isinstance(values, list):
        return records
    for value in values:
        if isinstance(value, str):
            records.append({"url": value})
        elif isinstance(value, dict):
            records.append(dict(value))
    return records


def _read_seed_file(path: Optional[Path]) -> List[Dict[str, str]]:
    """Read an optional CSV whose only mandatory column is ``url``."""
    if path is None:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "url" not in reader.fieldnames:
            raise ValueError("The seed CSV must contain a 'url' column.")
        return [dict(row) for row in reader if normalize_space(row.get("url"))]


def _infer_seed_source(
    record: Mapping[str, object],
    sources: Sequence[Mapping[str, object]],
) -> Optional[Mapping[str, object]]:
    """Resolve a seed's source_id, or infer one only when the host is unambiguous."""
    requested = normalize_space(record.get("source_id"))
    if requested:
        matches = [source for source in sources if normalize_space(source.get("source_id")) == requested]
        return matches[0] if len(matches) == 1 else None
    try:
        host = urlsplit(normalize_space(record.get("url"))).hostname or ""
    except ValueError:
        return None
    matches = []
    for source in sources:
        allowed = source.get("allowed_hosts", [])
        if isinstance(allowed, list) and host_is_allowed(host, [str(item) for item in allowed]):
            matches.append(source)
    return matches[0] if len(matches) == 1 else None


def add_manual_seeds(
    sources: Sequence[Mapping[str, object]],
    seed_records: Sequence[Mapping[str, object]],
    store: CandidateStore,
    stats_by_source: MutableMapping[str, SourceStats],
) -> None:
    """Add configuration and CSV seeds before broad automatic discovery."""
    combined: List[Mapping[str, object]] = list(seed_records)
    for source in sources:
        for record in _source_manual_urls(source):
            record.setdefault("source_id", source.get("source_id", ""))
            combined.append(record)
    for record in combined:
        source = _infer_seed_source(record, sources)
        if source is None:
            # There is no safe source allow-list to use, so the URL is skipped.
            continue
        source_id = normalize_space(source.get("source_id"))
        stats = stats_by_source[source_id]
        store.add(
            source,
            stats,
            url=record.get("url", ""),
            title_hint=record.get("title_hint") or record.get("title", ""),
            date_hint=record.get("date_hint") or record.get("date", ""),
            actor_id_override=normalize_space(
                record.get("expected_actor_id") or record.get("actor_id", "")
            ),
            discovery_method="manual_seed",
            discovery_url="",
            discovery_page="",
            listing_result=None,
            allow_seed_pdf=True,
        )


def _parse_date_argument(value: Optional[str], option: str) -> Optional[date]:
    """Parse an optional YYYY-MM-DD command-line value."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{option} must use YYYY-MM-DD.") from error


def _validate_source(source: Mapping[str, object]) -> None:
    """Check the minimum source configuration needed for safe collection."""
    source_id = normalize_space(source.get("source_id"))
    if not source_id:
        raise ValueError("Every moncloa source must have source_id.")
    allowed = source.get("allowed_hosts", [])
    if not isinstance(allowed, list) or not allowed:
        raise ValueError(f"{source_id}: allowed_hosts must be a non-empty list.")
    discovery = source.get("discovery", {})
    if not isinstance(discovery, dict):
        raise ValueError(f"{source_id}: discovery must be an object.")


def _discovery_type(source: Mapping[str, object]) -> str:
    """Read the discovery type while accepting clear legacy aliases."""
    discovery = source.get("discovery", {})
    value = normalize_space(discovery.get("type") if isinstance(discovery, dict) else "")
    value = value.casefold()
    aliases = {
        "monthly_archive": "aspnet_monthly",
        "lamoncloa_monthly": "aspnet_monthly",
        "aspnet": "aspnet_monthly",
        "wordpress": "wordpress_rest",
        "listing": "html_listing",
        "seeds": "manual",
    }
    return aliases.get(value, value or "manual")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Discover official intervention pages from La Moncloa."
    )
    parser.add_argument("--sources-config", type=Path, required=True)
    parser.add_argument("--politicians-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--seed-file", type=Path)
    parser.add_argument("--start", help="Earliest candidate date (YYYY-MM-DD).")
    parser.add_argument("--end", help="Latest candidate date (YYYY-MM-DD).")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help=(
            "Collect only this source_id. The default is "
            f"{MONCLOA_SOURCE_ID}."
        ),
    )
    parser.add_argument("--max-pages-per-source", type=int)
    parser.add_argument("--max-candidates-per-source", type=int)
    parser.add_argument("--delay", type=float, help="Minimum seconds between requests to one host.")
    parser.add_argument("--contact-email", help="Optional institutional contact in the User-Agent.")
    parser.add_argument("--offline", action="store_true", help="Use cache only; make no requests.")
    parser.add_argument("--refresh", action="store_true", help="Redownload pages even if cached.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run discovery and write the candidate inventory plus summary."""
    args = parse_args(argv)
    try:
        if args.offline and args.refresh:
            raise ValueError("--offline and --refresh cannot be used together.")
        if args.max_pages_per_source is not None and args.max_pages_per_source < 1:
            raise ValueError("--max-pages-per-source must be at least 1.")
        if args.max_candidates_per_source is not None and args.max_candidates_per_source < 1:
            raise ValueError("--max-candidates-per-source must be at least 1.")
        if args.delay is not None and args.delay < 0:
            raise ValueError("--delay cannot be negative.")

        config = load_json(args.sources_config)
        if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
            raise ValueError("moncloa_sources.json must contain a top-level sources list.")
        # Command-line dates take precedence.  Project-wide defaults keep a
        # normal run bounded even when the user does not repeat the dates.
        start_value = args.start or normalize_space(config.get("default_start_date")) or None
        end_value = args.end or normalize_space(config.get("default_end_date")) or None
        start = _parse_date_argument(start_value, "--start/default_start_date")
        end = _parse_date_argument(end_value, "--end/default_end_date")
        if start and end and start > end:
            raise ValueError("The start date cannot be later than the end date.")
        politicians = _load_politicians(args.politicians_config)

        all_sources: List[Dict[str, object]] = []
        seen_source_ids: Set[str] = set()
        for value in config["sources"]:
            if not isinstance(value, dict):
                raise ValueError("Every sources entry must be a JSON object.")
            _validate_source(value)
            source_id = normalize_space(value.get("source_id"))
            if source_id in seen_source_ids:
                raise ValueError(f"Duplicate source_id: {source_id}")
            seen_source_ids.add(source_id)
            all_sources.append(dict(value))

        # A run without --source is deliberately Moncloa-only. Keeping the
        # option preserves backwards-compatible, explicit invocations while
        # the supplied configuration exposes no PP or VOX sources.
        requested = set(args.sources or [MONCLOA_SOURCE_ID])
        unknown_requested = requested - seen_source_ids
        if unknown_requested:
            raise ValueError("Unknown --source value(s): " + ", ".join(sorted(unknown_requested)))
        selected = [
            source
            for source in all_sources
            if bool(source.get("enabled", True))
            and (not requested or normalize_space(source.get("source_id")) in requested)
        ]
        if not selected:
            raise ValueError("No enabled moncloa sources matched the request.")
        for source in selected:
            actor_id = normalize_space(source.get("expected_actor_id"))
            if actor_id not in politicians:
                raise ValueError(
                    f"{source['source_id']}: expected_actor_id {actor_id!r} is not in politicians.json."
                )

        tracking = config.get("tracking_query_parameters", [])
        if not isinstance(tracking, list):
            raise ValueError("tracking_query_parameters must be a list.")
        delay = args.delay
        if delay is None:
            delay = float(config.get("default_delay_seconds", 2.0))

        output_dir = args.output_dir
        processed_dir = output_dir / "processed"
        cache_dir = output_dir / "cache" / "moncloa"
        raw_dir = output_dir / "raw" / "moncloa"
        session = build_session(args.contact_email)
        fetcher = Fetcher(
            session,
            cache_dir,
            provenance_root=output_dir,
            delay_seconds=delay,
            offline=args.offline,
            refresh=args.refresh,
            max_response_bytes=int(
                config.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES)
            ),
        )
        store = CandidateStore(
            politicians,
            [str(item) for item in tracking],
            start,
            end,
            args.max_candidates_per_source,
        )
        stats_by_source = {
            normalize_space(source.get("source_id")): SourceStats(
                normalize_space(source.get("source_id"))
            )
            for source in selected
        }

        # Hand-curated URLs are processed first.  They remain useful when a
        # broad listing is unavailable or blocked, and PDFs stay candidates for
        # a future specialised parser rather than being parsed as HTML here.
        seed_records = _read_seed_file(args.seed_file)
        add_manual_seeds(selected, seed_records, store, stats_by_source)

        for source in selected:
            source_id = normalize_space(source.get("source_id"))
            stats = stats_by_source[source_id]
            actor = politicians[normalize_space(source.get("expected_actor_id"))]
            discovery_type = _discovery_type(source)
            try:
                if discovery_type == "aspnet_monthly":
                    discover_moncloa(
                        source,
                        actor,
                        fetcher,
                        store,
                        stats,
                        start=start,
                        end=end,
                        max_pages=args.max_pages_per_source,
                    )
                elif discovery_type == "wordpress_rest":
                    discover_wordpress_rest(
                        source,
                        actor,
                        fetcher,
                        store,
                        stats,
                        max_pages=args.max_pages_per_source,
                        start=start,
                        end=end,
                    )
                elif discovery_type == "html_listing":
                    discover_html_listing(
                        source,
                        actor,
                        fetcher,
                        store,
                        stats,
                        max_pages=args.max_pages_per_source,
                        start=start,
                    )
                elif discovery_type == "manual":
                    stats.stop_reason = "manual_seeds_only"
                else:
                    stats.error(f"Unsupported discovery type: {discovery_type}")
                    stats.pagination_completed = False
                    stats.stop_reason = "unsupported_discovery_type"
            except (TypeError, ValueError) as error:
                stats.error(error)
                stats.pagination_completed = False
                stats.stop_reason = "configuration_or_parse_error"

        rows = sorted(
            store.rows.values(),
            key=lambda row: (
                str(row["source_id"]),
                str(row["date_hint"]),
                str(row["canonical_url"]),
            ),
        )
        inventory_path = processed_dir / "moncloa_candidates.csv"
        summary_path = processed_dir / "moncloa_collection_summary.json"
        manifest_path = raw_dir / "moncloa_fetch_manifest.json"
        write_csv(inventory_path, rows, CANDIDATE_FIELDS)
        fetcher.write_manifest(manifest_path)

        by_actor = Counter(str(row["expected_actor_id"]) for row in rows)
        by_source = Counter(str(row["source_id"]) for row in rows)
        dates = sorted(str(row["date_hint"]) for row in rows if row.get("date_hint"))
        summary = {
            "generated_at_utc": utc_now(),
            "script_version": SCRIPT_VERSION,
            "sources_config": str(args.sources_config),
            "sources_config_sha256": sha256_file(args.sources_config),
            "politicians_config": str(args.politicians_config),
            "politicians_config_sha256": sha256_file(args.politicians_config),
            "seed_file": str(args.seed_file) if args.seed_file else None,
            "seed_file_sha256": sha256_file(args.seed_file) if args.seed_file else None,
            "start_date_filter": start.isoformat() if start else None,
            "end_date_filter": end.isoformat() if end else None,
            "offline": bool(args.offline),
            "refresh": bool(args.refresh),
            "delay_seconds": delay,
            "max_pages_per_source": args.max_pages_per_source,
            "max_candidates_per_source": args.max_candidates_per_source,
            "candidate_count": len(rows),
            "candidates_by_actor": dict(sorted(by_actor.items())),
            "candidates_by_source": dict(sorted(by_source.items())),
            "earliest_candidate_date": dates[0] if dates else None,
            "latest_candidate_date": dates[-1] if dates else None,
            "candidate_inventory": inventory_path.relative_to(output_dir).as_posix(),
            "fetch_manifest": manifest_path.relative_to(output_dir).as_posix(),
            "source_results": {
                source_id: stats_by_source[source_id].as_dict()
                for source_id in sorted(stats_by_source)
            },
        }
        write_json(summary_path, summary)

        print(f"Wrote {len(rows)} candidates to {inventory_path}")
        print(f"Wrote collection summary to {summary_path}")
        incomplete = [
            source_id
            for source_id, stats in stats_by_source.items()
            if not stats.pagination_completed
        ]
        if incomplete:
            print(
                "Collection was incomplete for: " + ", ".join(sorted(incomplete)) + ". "
                "See moncloa_collection_summary.json.",
                file=sys.stderr,
            )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
