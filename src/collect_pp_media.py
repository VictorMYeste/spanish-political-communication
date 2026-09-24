#!/usr/bin/env python3
"""Inventory Feijóo videos and official documents published by pp.es.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import time
import unicodedata
import urllib.robotparser
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SCRIPT_VERSION = "1.1.0"
TEMPORARY_HTTP_STATUS = (429, 500, 502, 503, 504)
MEDIA_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

MEDIA_FIELDS = [
    "candidate_id",
    "source_id",
    "actor_id",
    "date",
    "title",
    "location",
    "event_type",
    "archive_url",
    "detail_url",
    "related_article_url",
    "media_url",
    "media_host",
    "media_kind",
    "discovery_status",
    "download_status",
    "local_path",
    "bytes",
    "sha256",
    "error",
]

DOCUMENT_FIELDS = [
    "document_id",
    "source_id",
    "actor_id",
    "date_hint",
    "title",
    "document_kind",
    "document_url",
    "file_info",
    "relevant_to_actor",
    "download_status",
    "local_path",
    "bytes",
    "sha256",
    "error",
]

SPANISH_MONTHS = {
    "ene": 1,
    "enero": 1,
    "feb": 2,
    "febrero": 2,
    "mar": 3,
    "marzo": 3,
    "abr": 4,
    "abril": 4,
    "may": 5,
    "mayo": 5,
    "jun": 6,
    "junio": 6,
    "jul": 7,
    "julio": 7,
    "ago": 8,
    "agosto": 8,
    "sep": 9,
    "sept": 9,
    "septiembre": 9,
    "oct": 10,
    "octubre": 10,
    "nov": 11,
    "noviembre": 11,
    "dic": 12,
    "diciembre": 12,
}


class CollectionError(RuntimeError):
    """Expected, reportable collection failure."""


class AccessBlocked(CollectionError):
    """The remote host explicitly rejected the request."""


@dataclass
class FetchResult:
    body: bytes
    final_url: str
    headers: dict[str, str]
    status_code: int


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip().lower()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def canonical_url(url: str, base_url: str = "") -> str:
    absolute = urljoin(base_url, url.strip())
    parsed = urlparse(absolute)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", parsed.query, ""))


def stable_id(prefix: str, url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def parse_iso_date(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CollectionError(f"Invalid {label} date {value!r}; expected YYYY-MM-DD") from exc


def parse_pp_date(text: str | None) -> str:
    normalized = normalize_text(text).replace(",", " ")
    match = re.search(
        r"(?<!\d)(\d{1,2})\s+"
        r"(ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|"
        r"jul(?:io)?|ago(?:sto)?|sep(?:t(?:iembre)?)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?)"
        r"\s+(20\d{2})(?!\d)",
        normalized,
    )
    if not match:
        return ""
    day, month_name, year = match.groups()
    month = SPANISH_MONTHS.get(month_name)
    if not month:
        return ""
    try:
        return date(int(year), month, int(day)).isoformat()
    except ValueError:
        return ""


def infer_document_date(url: str) -> str:
    path = unquote(urlparse(url).path)
    filename = Path(path).name
    full = re.search(r"(?<!\d)(20\d{2})[._-]+(\d{1,2})[._-]+(\d{1,2})(?!\d)", filename)
    if full:
        year, month, day = map(int, full.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    short = re.search(r"(?<!\d)(\d{2})[._-]+(\d{1,2})[._-]+(\d{1,2})(?!\d)", filename)
    if short:
        yy, month, day = map(int, short.groups())
        try:
            return date(2000 + yy, month, day).isoformat()
        except ValueError:
            pass
    folder = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/", path)
    if folder:
        return f"{folder.group(1)}-{folder.group(2)}"
    return ""


def within_date_range(value: str, start: date | None, end: date | None) -> bool:
    if not value:
        return True
    try:
        current = date.fromisoformat(value)
        return (start is None or current >= start) and (end is None or current <= end)
    except ValueError:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            month_start = date.fromisoformat(value + "-01")
            if month_start.month == 12:
                next_month = date(month_start.year + 1, 1, 1)
            else:
                next_month = date(month_start.year, month_start.month + 1, 1)
            month_end = date.fromordinal(next_month.toordinal() - 1)
            return not ((start and month_end < start) or (end and month_start > end))
    return True


def page_reaches_start(rows: list[dict[str, Any]], field: str, start: date | None) -> bool:
    if start is None:
        return False
    dated: list[date] = []
    for row in rows:
        value = str(row.get(field, ""))
        try:
            dated.append(date.fromisoformat(value))
        except ValueError:
            if re.fullmatch(r"\d{4}-\d{2}", value):
                dated.append(date.fromisoformat(value + "-01"))
    return bool(dated) and min(dated) <= start


def classify_event(title: str) -> str:
    value = normalize_text(title)
    rules = (
        ("interview", ("entrevista", "en conversacion", "podcast")),
        ("press_conference", ("rueda de prensa", "atiende a los medios", "comparece", "declaraciones a los medios")),
        ("parliamentary_speech", ("congreso", "senado", "sesion de control", "investidura", "debate parlamentario")),
        ("rally", ("mitin", "acto electoral", "campana electoral", "cierre de campana")),
        ("speech", ("interviene", "intervencion", "discurso", "clausura", "participa", "foro")),
    )
    for label, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return label
    return "other"


def classify_document(title: str) -> str:
    value = normalize_text(title)
    if "intervencion" in value:
        return "intervention"
    if "discurso" in value:
        return "speech"
    if "comparecencia" in value:
        return "press_conference"
    if "comunicado" in value or "declaracion" in value:
        return "statement"
    return "other"


def youtube_url_from_value(value: str | None) -> str:
    if not value:
        return ""
    decoded = unquote(value)
    match = re.search(r"(?:img\.youtube\.com|i\.ytimg\.com)/vi(?:_webp)?/([^/?#]+)/", decoded)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    parsed = urlparse(decoded)
    host = (parsed.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        embed = re.match(r"/(?:embed|shorts|live)/([^/?#]+)", parsed.path)
        if embed:
            return f"https://www.youtube.com/watch?v={embed.group(1)}"
        if parsed.path == "/watch":
            return canonical_url(decoded)
    if host == "youtu.be" and parsed.path.strip("/"):
        return f"https://www.youtube.com/watch?v={parsed.path.strip('/').split('/')[0]}"
    return ""


def direct_media_url_from_item(item: Any, base_url: str) -> str:
    for tag in item.select("video[src], audio[src], source[src], a[href]"):
        value = tag.get("src") or tag.get("href") or ""
        absolute = canonical_url(value, base_url)
        if re.search(r"\.(?:mp3|m4a|ogg|wav|flac|mp4|webm|mov)(?:\?|$)", absolute, re.I):
            return absolute
    return ""


def parse_archive_html(
    html: str,
    archive_url: str,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    soup = BeautifulSoup(html, "html.parser")
    source_id = config["source_id"]
    actor_id = config["actor_id"]
    media_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []

    items = soup.select(".news-items.ajax-video .item")
    if not items:
        seen_containers: set[int] = set()
        for link in soup.select('a[href*="/actualidad/videos/"]'):
            href = canonical_url(link.get("href", ""), archive_url)
            if href.rstrip("/") == "https://www.pp.es/actualidad/videos":
                continue
            container = link.find_parent(class_=re.compile(r"(?:^|\s)item(?:\s|$)")) or link.parent
            if id(container) not in seen_containers:
                items.append(container)
                seen_containers.add(id(container))

    for item in items:
        title_link = item.select_one('h4 a[href*="/actualidad/videos/"]')
        if title_link is None:
            title_link = item.select_one('a[href*="/actualidad/videos/"]')
        if title_link is None:
            continue
        detail_url = canonical_url(title_link.get("href", ""), archive_url)
        if not detail_url or detail_url.rstrip("/") == "https://www.pp.es/actualidad/videos":
            continue
        title_node = item.select_one("h4")
        title = clean_text(title_node.get_text(" ", strip=True) if title_node else title_link.get_text(" ", strip=True))
        date_node = item.select_one(".date")
        date_value = parse_pp_date(date_node.get_text(" ", strip=True) if date_node else item.get_text(" ", strip=True))
        location_node = item.select_one(".when-where")
        location = clean_text(location_node.get_text(" ", strip=True) if location_node else "")
        related = ""
        for link in item.select('.time-date a[href*="/actualidad/articulos/"]'):
            related = canonical_url(link.get("href", ""), archive_url)
            break

        media_url = ""
        for image in item.select("img"):
            for attr in ("data-src", "src", "data-lazy-src"):
                media_url = youtube_url_from_value(image.get(attr))
                if media_url:
                    break
            if media_url:
                break
        if not media_url:
            media_url = direct_media_url_from_item(item, archive_url)

        media_host = host_of(media_url)
        external = set(config.get("external_platform_hosts", []))
        direct = set(config.get("allowed_direct_media_hosts", []))
        if media_host in external:
            status = "external_platform_metadata_only"
            media_kind = "platform_video"
            download_status = "not_permitted_platform"
        elif media_host in direct:
            status = "direct_media_found"
            media_kind = "direct_media"
            download_status = "not_requested"
        elif media_url:
            status = "unapproved_media_host"
            media_kind = "unknown"
            download_status = "not_permitted_host"
        else:
            status = "detail_page_required"
            media_kind = "unknown"
            download_status = "not_available"

        media_rows.append(
            {
                "candidate_id": stable_id("pp_media", detail_url),
                "source_id": source_id,
                "actor_id": actor_id,
                "date": date_value,
                "title": title,
                "location": location,
                "event_type": classify_event(title),
                "archive_url": archive_url,
                "detail_url": detail_url,
                "related_article_url": related,
                "media_url": media_url,
                "media_host": media_host,
                "media_kind": media_kind,
                "discovery_status": status,
                "download_status": download_status,
                "local_path": "",
                "bytes": "",
                "sha256": "",
                "error": "",
            }
        )

    document_links = soup.select("a.documents-item[href]")
    if not document_links:
        document_links = soup.select('a[href$=".pdf"], a[href*=".pdf?"]')
    keywords = [normalize_text(x) for x in config.get("document_relevance_keywords", [])]
    allowed_document_hosts = set(config.get("allowed_document_hosts", []))
    for link in document_links:
        document_url = canonical_url(link.get("href", ""), archive_url)
        if not document_url or host_of(document_url) not in allowed_document_hosts:
            continue
        title_node = link.select_one("h4")
        title = clean_text(title_node.get_text(" ", strip=True) if title_node else link.get_text(" ", strip=True))
        info_node = link.select_one(".file-info")
        file_info = clean_text(info_node.get_text(" ", strip=True) if info_node else "")
        relevant = any(keyword in normalize_text(title) for keyword in keywords)
        document_rows.append(
            {
                "document_id": stable_id("pp_doc", document_url),
                "source_id": source_id,
                "actor_id": actor_id,
                "date_hint": infer_document_date(document_url),
                "title": title,
                "document_kind": classify_document(title),
                "document_url": document_url,
                "file_info": file_info,
                "relevant_to_actor": str(relevant),
                "download_status": "not_requested",
                "local_path": "",
                "bytes": "",
                "sha256": "",
                "error": "",
            }
        )

    has_load_more = bool(soup.select_one(".load-more-post"))
    return media_rows, document_rows, has_load_more


def extract_ajax_filters(html: str, item_type: str) -> str:
    """Return the form serialization used by PP's public CARGAR MÁS control."""
    soup = BeautifulSoup(html, "html.parser")
    button = soup.select_one(f'a.button-rect[data-type="{item_type}"][data-form]')
    if button is None:
        return ""
    form_id = button.get("data-form", "")
    form = soup.find("form", id=form_id)
    if form is None:
        return ""
    values: list[tuple[str, str]] = []
    for field in form.select("input[name], select[name]"):
        name = field.get("name", "")
        if not name or field.has_attr("disabled"):
            continue
        if field.name == "select":
            selected = field.select("option[selected]") or field.select("option")[:1]
            values.extend((name, option.get("value", "")) for option in selected)
            continue
        input_type = (field.get("type") or "text").lower()
        if input_type in {"checkbox", "radio"} and not field.has_attr("checked"):
            continue
        values.append((name, field.get("value", "")))
    return urlencode(values)


def parse_detail_html(html: str, detail_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for selector, attr in (("iframe[src]", "src"), ("video[src]", "src"), ("audio[src]", "src"), ("source[src]", "src")):
        for node in soup.select(selector):
            raw = node.get(attr, "")
            youtube = youtube_url_from_value(raw)
            if youtube:
                return youtube, "platform_video"
            absolute = canonical_url(raw, detail_url)
            if absolute:
                return absolute, "direct_media"
    direct = direct_media_url_from_item(soup, detail_url)
    if direct:
        return direct, "direct_media"
    return "", "unknown"


def deduplicate(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_key = str(row.get(key, ""))
        if not row_key:
            continue
        if row_key not in merged:
            merged[row_key] = row
            continue
        previous = merged[row_key]
        for field, value in row.items():
            if not previous.get(field) and value:
                previous[field] = value
    return list(merged.values())


def directory_size(paths: Iterable[Path]) -> int:
    total = 0
    seen: set[Path] = set()
    for root in paths:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    total += path.stat().st_size
                    seen.add(resolved)
    return total


class PoliteClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.delay = float(config.get("delay_seconds", 2.0))
        self.timeout = (
            float(config.get("connect_timeout_seconds", 10)),
            float(config.get("read_timeout_seconds", 45)),
        )
        self.last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config["user_agent"], "Accept-Language": "es,en;q=0.7"})
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=1.0,
            status_forcelist=TEMPORARY_HTTP_STATUS,
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self._robots: urllib.robotparser.RobotFileParser | None = None

    def _wait(self) -> None:
        remaining = self.delay - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)

    def _fetch(
        self,
        url: str,
        allowed_hosts: set[str],
        max_bytes: int,
        method: str = "GET",
        data: dict[str, str] | None = None,
    ) -> FetchResult:
        current = canonical_url(url)
        current_method = method.upper()
        current_data = data
        max_redirects = int(self.config.get("max_redirects", 5))
        for _ in range(max_redirects + 1):
            if urlparse(current).scheme != "https" or host_of(current) not in allowed_hosts:
                raise CollectionError(f"URL outside the allow-list: {current}")
            self._wait()
            try:
                response = self.session.request(
                    current_method,
                    current,
                    data=current_data,
                    timeout=self.timeout,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise CollectionError(f"Request failed for {current}: {exc}") from exc
            finally:
                self.last_request = time.monotonic()
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise CollectionError(f"Redirect without Location from {current}")
                current = canonical_url(location, current)
                if response.status_code == 303 or (
                    current_method == "POST" and response.status_code in {301, 302}
                ):
                    current_method = "GET"
                    current_data = None
                continue
            if response.status_code in {401, 403}:
                response.close()
                raise AccessBlocked(f"Access blocked with HTTP {response.status_code}: {current}")
            if response.status_code >= 400:
                status = response.status_code
                response.close()
                raise CollectionError(f"HTTP {status} while requesting {current}")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                response.close()
                raise CollectionError(f"Response exceeds configured size limit: {current}")
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    response.close()
                    raise CollectionError(f"Response exceeded configured size limit: {current}")
                chunks.append(chunk)
            result = FetchResult(
                body=b"".join(chunks),
                final_url=current,
                headers={k.lower(): v for k, v in response.headers.items()},
                status_code=response.status_code,
            )
            response.close()
            return result
        raise CollectionError(f"Too many redirects while requesting {url}")

    def load_robots(self) -> None:
        robots_url = self.config["robots_url"]
        allowed_hosts = set(self.config["allowed_page_hosts"])
        try:
            result = self._fetch(robots_url, allowed_hosts, 1024 * 1024)
        except AccessBlocked:
            raise CollectionError("robots.txt access was denied; online collection stopped")
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
        self._robots = parser

    def page(self, url: str) -> FetchResult:
        if self._robots is None:
            self.load_robots()
        if self._robots and not self._robots.can_fetch(self.config["user_agent"], url):
            raise CollectionError(f"robots.txt does not permit collection of {url}")
        return self._fetch(
            url,
            set(self.config["allowed_page_hosts"]),
            int(self.config["max_page_response_bytes"]),
        )

    def ajax_page(self, page: int, item_type: str, filters: str) -> dict[str, Any]:
        url = self.config["ajax_url"]
        if self._robots is None:
            self.load_robots()
        if self._robots and not self._robots.can_fetch(self.config["user_agent"], url):
            raise CollectionError(f"robots.txt does not permit collection of {url}")
        result = self._fetch(
            url,
            set(self.config["allowed_page_hosts"]),
            int(self.config["max_page_response_bytes"]),
            method="POST",
            data={
                "action": "pp_news_items",
                "page": str(page),
                "type": item_type,
                "filters": filters,
            },
        )
        try:
            payload = json.loads(result.body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectionError(f"Invalid JSON from PP pagination endpoint for {item_type} page {page}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("html"), str):
            raise CollectionError(f"Unexpected PP pagination response for {item_type} page {page}")
        return payload

    def document(self, url: str) -> FetchResult:
        return self._fetch(
            url,
            set(self.config["allowed_document_hosts"]),
            int(self.config["max_single_download_bytes"]),
        )

    def direct_media(self, url: str) -> FetchResult:
        return self._fetch(
            url,
            set(self.config.get("allowed_direct_media_hosts", [])),
            int(self.config["max_single_download_bytes"]),
        )


def storage_allows(output_dir: Path, config: dict[str, Any], incoming: int) -> tuple[bool, str]:
    used = directory_size((output_dir / "raw" / "pp", output_dir / "interim" / "pp"))
    if used + incoming > int(config["max_storage_bytes"]):
        return False, "50 GiB PP storage limit would be exceeded"
    output_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output_dir).free
    if free - incoming < int(config["minimum_free_disk_bytes"]):
        return False, "minimum free-disk reserve would be crossed"
    return True, ""


def write_download(path: Path, body: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(body)
    temporary.replace(path)
    return len(body), hashlib.sha256(body).hexdigest()


def download_documents(
    rows: list[dict[str, Any]], client: PoliteClient, output_dir: Path, config: dict[str, Any], refresh: bool
) -> None:
    for row in rows:
        if row["relevant_to_actor"] != "True":
            row["download_status"] = "skipped_not_relevant"
            continue
        path = output_dir / "raw" / "pp" / "documents" / f"{row['document_id']}.pdf"
        if path.exists() and not refresh:
            body = path.read_bytes()
            row.update(
                download_status="cached",
                local_path=str(path.relative_to(output_dir)),
                bytes=str(len(body)),
                sha256=hashlib.sha256(body).hexdigest(),
            )
            continue
        try:
            result = client.document(row["document_url"])
            content_type = result.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != "application/pdf" and not result.body.startswith(b"%PDF"):
                raise CollectionError(f"Expected PDF but received {content_type or 'unknown content type'}")
            allowed, reason = storage_allows(output_dir, config, len(result.body))
            if not allowed:
                row.update(download_status="storage_limit", error=reason)
                continue
            size, digest = write_download(path, result.body)
            row.update(
                download_status="downloaded",
                local_path=str(path.relative_to(output_dir)),
                bytes=str(size),
                sha256=digest,
            )
        except CollectionError as exc:
            row.update(download_status="error", error=str(exc))


def download_direct_media(
    rows: list[dict[str, Any]], client: PoliteClient, output_dir: Path, config: dict[str, Any], refresh: bool
) -> None:
    allowed_hosts = set(config.get("allowed_direct_media_hosts", []))
    for row in rows:
        if not row["media_url"] or row["media_host"] not in allowed_hosts:
            continue
        try:
            result = client.direct_media(row["media_url"])
            content_type = result.headers.get("content-type", "").split(";", 1)[0].lower()
            if not (content_type.startswith("audio/") or content_type.startswith("video/")):
                raise CollectionError(f"Expected audio/video but received {content_type or 'unknown content type'}")
            extension = MEDIA_EXTENSIONS.get(content_type, Path(urlparse(result.final_url).path).suffix or ".bin")
            path = output_dir / "raw" / "pp" / "media" / f"{row['candidate_id']}{extension}"
            if path.exists() and not refresh:
                body = path.read_bytes()
                row.update(
                    download_status="cached",
                    local_path=str(path.relative_to(output_dir)),
                    bytes=str(len(body)),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                continue
            allowed, reason = storage_allows(output_dir, config, len(result.body))
            if not allowed:
                row.update(download_status="storage_limit", error=reason)
                continue
            size, digest = write_download(path, result.body)
            row.update(
                download_status="downloaded",
                local_path=str(path.relative_to(output_dir)),
                bytes=str(size),
                sha256=digest,
            )
        except CollectionError as exc:
            row.update(download_status="error", error=str(exc))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def natural_key(path: Path) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def find_saved_pages(directory: Path, max_pages: int | None) -> list[Path]:
    pages = sorted(directory.glob("pp_page_*.html"), key=natural_key)
    if not pages:
        single = directory / "pp_feijoo.html"
        if single.exists():
            pages = [single]
    if not pages:
        raise CollectionError(
            f"No saved PP pages found in {directory}. Expected pp_page_1.html (or pp_feijoo.html)."
        )
    return pages[:max_pages] if max_pages else pages


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError(f"Cannot read source config {path}: {exc}") from exc
    required = {
        "source_id",
        "actor_id",
        "archive_url",
        "ajax_url",
        "robots_url",
        "allowed_page_hosts",
        "allowed_document_hosts",
        "max_storage_bytes",
        "minimum_free_disk_bytes",
        "user_agent",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise CollectionError(f"Missing config keys: {', '.join(missing)}")
    return config


def collect_ajax_stream(
    client: PoliteClient,
    config: dict[str, Any],
    item_type: str,
    filters: str,
    start: date | None,
    max_pages: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, str, list[str]]:
    """Collect page 2 onward from the public endpoint used by CARGAR MÁS."""
    media_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    pages_attempted = 0
    pages_collected = 0
    seen: set[str] = set()
    page = 2
    if max_pages is not None and max_pages <= 1:
        return media_rows, document_rows, 0, 0, "max_pages_per_stream", errors
    while page <= 10_000:
        if max_pages is not None and page > max_pages:
            return media_rows, document_rows, pages_attempted, pages_collected, "max_pages_per_stream", errors
        pages_attempted += 1
        try:
            payload = client.ajax_page(page, item_type, filters)
        except CollectionError as exc:
            errors.append(f"{item_type} page {page}: {exc}")
            return media_rows, document_rows, pages_attempted, pages_collected, "ajax_error", errors
        html = payload.get("html", "")
        parsed_media, parsed_documents, _ = parse_archive_html(html, config["archive_url"], config)
        stream_rows = parsed_media if item_type == "video" else parsed_documents
        key_field = "detail_url" if item_type == "video" else "document_url"
        new_rows = [row for row in stream_rows if row.get(key_field) and row[key_field] not in seen]
        if not new_rows:
            return media_rows, document_rows, pages_attempted, pages_collected, "empty_or_repeated_page", errors
        seen.update(str(row[key_field]) for row in new_rows)
        if item_type == "video":
            media_rows.extend(new_rows)
            date_field = "date"
        else:
            document_rows.extend(new_rows)
            date_field = "date_hint"
        pages_collected += 1
        if page_reaches_start(new_rows, date_field, start):
            return media_rows, document_rows, pages_attempted, pages_collected, "start_date_reached", errors
        if not payload.get("load_more"):
            return media_rows, document_rows, pages_attempted, pages_collected, "pagination_completed", errors
        page += 1
    errors.append(f"{item_type}: safety page limit reached")
    return media_rows, document_rows, pages_attempted, pages_collected, "safety_page_limit", errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory official PP videos for Alberto Núñez Feijóo and PP-hosted PDF documents. "
            "YouTube links are recorded as metadata only."
        )
    )
    parser.add_argument("--source-config", required=True)
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--start", help="Inclusive YYYY-MM-DD; defaults to the source config")
    parser.add_argument("--end", help="Inclusive YYYY-MM-DD")
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Limit pages per online video/document stream, or saved HTML snapshots in offline mode",
    )
    parser.add_argument(
        "--archive-html-dir",
        help=(
            "Offline mode: read pp_page_1.html, pp_page_2.html, ... (or pp_feijoo.html) "
            "and make no requests to pp.es"
        ),
    )
    parser.add_argument("--download-documents", action="store_true", help="Download relevant PP-hosted PDFs")
    parser.add_argument("--download-media", action="store_true", help="Download only allow-listed direct media; never YouTube")
    parser.add_argument("--refresh", action="store_true", help="Replace already downloaded permitted files")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.source_config)
    output_dir = Path(args.output_dir).resolve()
    try:
        config = load_config(config_path)
        start = parse_iso_date(args.start or config.get("start_date"), "start")
        end = parse_iso_date(args.end, "end")
        if start and end and end < start:
            raise CollectionError("End date precedes start date")
        if args.max_pages is not None and args.max_pages < 1:
            raise CollectionError("--max-pages must be at least 1")
    except CollectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    all_media: list[dict[str, Any]] = []
    all_documents: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    pages_attempted = 0
    pages_collected = 0
    saw_load_more = False
    video_pages_collected = 0
    document_pages_collected = 0
    video_stop_reason = "not_started"
    document_stop_reason = "not_started"
    client: PoliteClient | None = None

    if args.archive_html_dir:
        collection_mode = "offline_html"
        try:
            saved_pages = find_saved_pages(Path(args.archive_html_dir), args.max_pages)
            for path in saved_pages:
                pages_attempted += 1
                size = path.stat().st_size
                if size > int(config["max_page_response_bytes"]):
                    errors.append(f"{path.name}: saved HTML exceeds configured response-size limit")
                    continue
                html = path.read_text(encoding="utf-8", errors="replace")
                media, documents, load_more = parse_archive_html(html, config["archive_url"], config)
                all_media.extend(media)
                all_documents.extend(documents)
                saw_load_more = saw_load_more or load_more
                pages_collected += 1
                video_pages_collected += 1
                document_pages_collected += 1
            video_stop_reason = "offline_html_processed"
            document_stop_reason = "offline_html_processed"
        except (CollectionError, OSError) as exc:
            errors.append(str(exc))
    else:
        collection_mode = "online_ajax"
        client = PoliteClient(config)
        pages_attempted = 1
        try:
            result = client.page(config["archive_url"])
            html = result.body.decode("utf-8", errors="replace")
            media, documents, load_more = parse_archive_html(html, result.final_url, config)
            all_media.extend(media)
            all_documents.extend(documents)
            saw_load_more = load_more
            pages_collected = 1
            video_pages_collected = 1
            document_pages_collected = 1

            video_filters = extract_ajax_filters(html, "video")
            document_filters = extract_ajax_filters(html, "documento")

            if page_reaches_start(media, "date", start):
                video_stop_reason = "start_date_reached"
            elif video_filters:
                extra_media, _, attempted, collected, reason, stream_errors = collect_ajax_stream(
                    client, config, "video", video_filters, start, args.max_pages
                )
                all_media.extend(extra_media)
                pages_attempted += attempted
                pages_collected += collected
                video_pages_collected += collected
                video_stop_reason = reason
                errors.extend(stream_errors)
            else:
                video_stop_reason = "ajax_parameters_not_found"
                warnings.append("Could not identify the public CARGAR MÁS parameters for PP videos.")

            if page_reaches_start(documents, "date_hint", start):
                document_stop_reason = "start_date_reached"
            elif document_filters:
                _, extra_documents, attempted, collected, reason, stream_errors = collect_ajax_stream(
                    client, config, "documento", document_filters, start, args.max_pages
                )
                all_documents.extend(extra_documents)
                pages_attempted += attempted
                pages_collected += collected
                document_pages_collected += collected
                document_stop_reason = reason
                errors.extend(stream_errors)
            else:
                document_stop_reason = "ajax_parameters_not_found"
                warnings.append("Could not identify the public CARGAR MÁS parameters for PP documents.")
        except CollectionError as exc:
            errors.append(str(exc))

    media_rows = deduplicate(all_media, "detail_url")
    document_rows = deduplicate(all_documents, "document_url")
    media_rows = [row for row in media_rows if within_date_range(row["date"], start, end)]
    document_rows = [row for row in document_rows if within_date_range(row["date_hint"], start, end)]

    if client is not None:
        for row in media_rows:
            if row["media_url"] or not row["detail_url"]:
                continue
            try:
                detail = client.page(row["detail_url"])
                media_url, media_kind = parse_detail_html(
                    detail.body.decode("utf-8", errors="replace"), detail.final_url
                )
                row["media_url"] = media_url
                row["media_host"] = host_of(media_url)
                row["media_kind"] = media_kind
                if row["media_host"] in set(config.get("external_platform_hosts", [])):
                    row["discovery_status"] = "external_platform_metadata_only"
                    row["download_status"] = "not_permitted_platform"
                elif row["media_host"] in set(config.get("allowed_direct_media_hosts", [])):
                    row["discovery_status"] = "direct_media_found"
                    row["download_status"] = "not_requested"
                elif media_url:
                    row["discovery_status"] = "unapproved_media_host"
                    row["download_status"] = "not_permitted_host"
                else:
                    row["discovery_status"] = "no_media_found"
            except CollectionError as exc:
                row["error"] = str(exc)
                row["discovery_status"] = "detail_fetch_error"

    if args.download_documents or args.download_media:
        if client is None:
            client = PoliteClient(config)
            try:
                client.load_robots()
            except CollectionError as exc:
                errors.append(str(exc))
                client = None
        if client is not None and args.download_documents:
            download_documents(document_rows, client, output_dir, config, args.refresh)
        if client is not None and args.download_media:
            download_direct_media(media_rows, client, output_dir, config, args.refresh)

    media_rows.sort(key=lambda row: (row["date"], row["title"]), reverse=True)
    document_rows.sort(key=lambda row: (row["date_hint"], row["title"]), reverse=True)
    processed = output_dir / "processed"
    media_csv = processed / "pp_media_candidates.csv"
    documents_csv = processed / "pp_official_documents.csv"
    summary_path = processed / "pp_media_collection_summary.json"
    write_csv(media_csv, media_rows, MEDIA_FIELDS)
    write_csv(documents_csv, document_rows, DOCUMENT_FIELDS)

    downloaded_documents = sum(row["download_status"] in {"downloaded", "cached"} for row in document_rows)
    downloaded_media = sum(row["download_status"] in {"downloaded", "cached"} for row in media_rows)
    platform_media = sum(row["discovery_status"] == "external_platform_metadata_only" for row in media_rows)
    direct_media = sum(row["discovery_status"] == "direct_media_found" for row in media_rows)
    relevant_documents = sum(row["relevant_to_actor"] == "True" for row in document_rows)
    dates = [row["date"] for row in media_rows if row["date"]]
    complete_reasons = {"start_date_reached", "pagination_completed"}
    requested_range_complete = (
        collection_mode == "online_ajax"
        and video_stop_reason in complete_reasons
        and document_stop_reason in complete_reasons
    )
    if pages_collected == 0:
        stop_reason = "collection_error"
    elif collection_mode == "offline_html":
        stop_reason = "offline_html_processed"
    elif "max_pages_per_stream" in {video_stop_reason, document_stop_reason}:
        stop_reason = "max_pages_per_stream"
    elif requested_range_complete:
        stop_reason = "requested_date_range_complete"
    else:
        stop_reason = "pagination_incomplete"
    summary = {
        "pipeline": "pp_media_collection",
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": utc_now(),
        "collection_mode": collection_mode,
        "archive_html_dir": str(Path(args.archive_html_dir).resolve()) if args.archive_html_dir else None,
        "media_candidate_count": len(media_rows),
        "youtube_or_platform_media": platform_media,
        "direct_media_found": direct_media,
        "media_downloaded_or_cached": downloaded_media,
        "official_document_count": len(document_rows),
        "relevant_official_documents": relevant_documents,
        "documents_downloaded_or_cached": downloaded_documents,
        "storage_used_bytes": directory_size((output_dir / "raw" / "pp", output_dir / "interim" / "pp")),
        "max_storage_bytes": int(config["max_storage_bytes"]),
        "minimum_free_disk_bytes": int(config["minimum_free_disk_bytes"]),
        "start_date_filter": start.isoformat() if start else None,
        "end_date_filter": end.isoformat() if end else None,
        "earliest_media_date": min(dates) if dates else None,
        "latest_media_date": max(dates) if dates else None,
        "pages_attempted": pages_attempted,
        "pages_collected": pages_collected,
        "video_pages_collected": video_pages_collected,
        "document_pages_collected": document_pages_collected,
        "video_stop_reason": video_stop_reason,
        "document_stop_reason": document_stop_reason,
        "dynamic_load_more_present": saw_load_more,
        "pagination_completed": requested_range_complete,
        "stop_reason": stop_reason,
        "errors": errors,
        "warnings": warnings,
        "media_inventory": str(media_csv.relative_to(output_dir)),
        "document_inventory": str(documents_csv.relative_to(output_dir)),
        "source_config": str(config_path),
        "source_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }
    processed.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(media_rows)} media candidates to {media_csv}")
    print(f"Wrote {len(document_rows)} official documents to {documents_csv}")
    print(f"Wrote collection summary to {summary_path}")
    if not requested_range_complete:
        print("Collection may be incomplete. See pp_media_collection_summary.json.")
    if errors:
        print("Collection encountered errors; see the summary.", file=sys.stderr)
    return 2 if pages_collected == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
