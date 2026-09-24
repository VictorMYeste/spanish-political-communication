from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import collect_pp_media as collector  # noqa: E402
import extract_pp_documents as extractor  # noqa: E402


def sample_config() -> dict:
    return {
        "source_id": "pp_feijoo_official",
        "actor_id": "alberto_nunez_feijoo",
        "archive_url": "https://www.pp.es/persona/alberto-nunez-feijoo/",
        "ajax_url": "https://www.pp.es/ajax-endpoint/",
        "robots_url": "https://www.pp.es/robots.txt",
        "allowed_page_hosts": ["www.pp.es", "pp.es"],
        "allowed_document_hosts": ["www.pp.es", "pp.es"],
        "allowed_direct_media_hosts": [],
        "external_platform_hosts": ["www.youtube.com", "youtube.com", "youtu.be"],
        "start_date": "2023-07-23",
        "delay_seconds": 0,
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 1,
        "max_page_response_bytes": 1_000_000,
        "max_single_download_bytes": 2_000_000,
        "max_storage_bytes": 100_000_000_000,
        "minimum_free_disk_bytes": 200 * 1024**3,
        "max_redirects": 5,
        "user_agent": "ResearchTest/1.0",
        "document_relevance_keywords": ["feijóo", "intervención", "discurso"],
    }


SAMPLE_HTML = """
<html><body>
  <div class="news-items ajax-video">
    <div class="item">
      <div class="wrapper-image">
        <a href="https://www.pp.es/actualidad/videos/feijoo-forum/">
          <img data-src="https://img.youtube.com/vi/AbC123_XyZ/0.jpg">
        </a>
      </div>
      <div class="item-content">
        <h4><a href="https://www.pp.es/actualidad/videos/feijoo-forum/">Feijóo interviene en el Fórum</a></h4>
        <div class="bottom-content">
          <div class="when-where"><span>Bilbao</span></div>
          <div class="time-date">
            <div class="date"><span>04 MAR, 2026</span></div>
            <a href="https://www.pp.es/actualidad/articulos/noticia-relacionada/"><span>Leer</span></a>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="news-items ajax-documento">
    <a class="documents-item" href="https://www.pp.es/wp-content/uploads/2026/03/26.03.04-Intervencion-Feijoo.pdf">
      <h4>Intervención de Alberto Núñez Feijóo en Bilbao</h4>
      <div class="file-info">PDF - 200Kb</div>
    </a>
    <a class="documents-item" href="https://evil.example/file.pdf"><h4>Discurso falso</h4></a>
  </div>
  <div class="row-load-more load-more-post"><a>CARGAR MÁS</a></div>
</body></html>
"""


def test_config_uses_requested_storage_limits():
    config = json.loads((ROOT / "config" / "pp_media_source.json").read_text(encoding="utf-8"))
    assert config["max_storage_bytes"] == 100_000_000_000
    assert config["minimum_free_disk_bytes"] == 200 * 1024**3
    assert config["allowed_direct_media_hosts"] == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("04 MAR, 2026", "2026-03-04"),
        ("26 SEPT, 2023", "2023-09-26"),
        ("7 ENERO, 2025", "2025-01-07"),
        ("sin fecha", ""),
    ],
)
def test_parse_pp_date(raw, expected):
    assert collector.parse_pp_date(raw) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Feijóo ofrece una rueda de prensa", "press_conference"),
        ("Entrevista a Alberto Núñez Feijóo", "interview"),
        ("Discurso de investidura de Alberto Núñez Feijóo", "parliamentary_speech"),
        ("Feijóo interviene en un acto", "speech"),
    ],
)
def test_classify_event(title, expected):
    assert collector.classify_event(title) == expected


def test_youtube_id_is_recovered_from_official_thumbnail():
    url = collector.youtube_url_from_value("https://img.youtube.com/vi/AbC123_XyZ/0.jpg")
    assert url == "https://www.youtube.com/watch?v=AbC123_XyZ"


def test_youtube_embed_is_canonicalized():
    url = collector.youtube_url_from_value("https://www.youtube.com/embed/abc987?rel=0")
    assert url == "https://www.youtube.com/watch?v=abc987"


def test_archive_parser_separates_video_and_official_document():
    media, documents, load_more = collector.parse_archive_html(
        SAMPLE_HTML, sample_config()["archive_url"], sample_config()
    )
    assert len(media) == 1
    assert media[0]["date"] == "2026-03-04"
    assert media[0]["location"] == "Bilbao"
    assert media[0]["media_url"] == "https://www.youtube.com/watch?v=AbC123_XyZ"
    assert media[0]["download_status"] == "not_permitted_platform"
    assert media[0]["event_type"] == "speech"
    assert len(documents) == 1
    assert documents[0]["date_hint"] == "2026-03-04"
    assert documents[0]["relevant_to_actor"] == "True"
    assert load_more is True


def test_public_ajax_filters_are_read_from_the_page_form():
    html = """
    <form id="form-news"><input name="per_page" value="6"><input name="types" value="post-video">
      <input name="persona" value="40002"></form>
    <a class="button-rect" data-type="video" data-form="form-news">CARGAR MÁS</a>
    """
    assert collector.extract_ajax_filters(html, "video") == "per_page=6&types=post-video&persona=40002"


def test_ajax_stream_stops_when_endpoint_reports_last_page():
    class FakeClient:
        def ajax_page(self, page, item_type, filters):
            assert (page, item_type, filters) == (2, "video", "persona=40002")
            return {"html": SAMPLE_HTML, "load_more": False}

    media, documents, attempted, collected, reason, errors = collector.collect_ajax_stream(
        FakeClient(), sample_config(), "video", "persona=40002", date(2023, 7, 23), None
    )
    assert len(media) == 1
    assert documents == []
    assert (attempted, collected, reason, errors) == (1, 1, "pagination_completed", [])


def test_document_date_falls_back_to_upload_month():
    assert (
        collector.infer_document_date("https://www.pp.es/wp-content/uploads/2025/11/speech.pdf")
        == "2025-11"
    )


def test_month_date_hint_is_filtered_safely():
    assert collector.within_date_range("2023-07", date(2023, 7, 23), None)
    assert not collector.within_date_range("2023-06", date(2023, 7, 23), None)


def test_detail_parser_finds_youtube_iframe():
    media_url, media_kind = collector.parse_detail_html(
        '<iframe src="https://www.youtube.com/embed/VIDEO99"></iframe>',
        "https://www.pp.es/actualidad/videos/example/",
    )
    assert media_url == "https://www.youtube.com/watch?v=VIDEO99"
    assert media_kind == "platform_video"


def test_deduplicate_merges_missing_fields():
    rows = [
        {"detail_url": "https://example/a", "title": "A", "date": ""},
        {"detail_url": "https://example/a", "title": "", "date": "2025-01-02"},
    ]
    merged = collector.deduplicate(rows, "detail_url")
    assert merged == [{"detail_url": "https://example/a", "title": "A", "date": "2025-01-02"}]


def test_saved_pages_are_naturally_sorted_and_limited(tmp_path):
    for name in ("pp_page_10.html", "pp_page_2.html", "pp_page_1.html"):
        (tmp_path / name).write_text("<html></html>", encoding="utf-8")
    pages = collector.find_saved_pages(tmp_path, 2)
    assert [path.name for path in pages] == ["pp_page_1.html", "pp_page_2.html"]


def test_offline_main_writes_both_inventories(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(sample_config()), encoding="utf-8")
    pages = tmp_path / "saved"
    pages.mkdir()
    (pages / "pp_page_1.html").write_text(SAMPLE_HTML, encoding="utf-8")
    output = tmp_path / "data"
    exit_code = collector.main(
        [
            "--source-config",
            str(config_path),
            "--output-dir",
            str(output),
            "--archive-html-dir",
            str(pages),
        ]
    )
    assert exit_code == 0
    with (output / "processed" / "pp_media_candidates.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        assert len(list(csv.DictReader(handle))) == 1
    with (output / "processed" / "pp_official_documents.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        assert len(list(csv.DictReader(handle))) == 1
    summary = json.loads(
        (output / "processed" / "pp_media_collection_summary.json").read_text(encoding="utf-8")
    )
    assert summary["collection_mode"] == "offline_html"
    assert summary["youtube_or_platform_media"] == 1
    assert summary["relevant_official_documents"] == 1


def test_inventory_paths_cannot_escape_output_directory(tmp_path):
    with pytest.raises(ValueError):
        extractor.contained_path(tmp_path, "../secret.pdf")
