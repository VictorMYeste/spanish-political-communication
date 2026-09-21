from __future__ import annotations
import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_script(module_name: str, relative_path: str):
    """Load one repository script without requiring ``src`` to be a package."""

    script_path = PROJECT_ROOT / relative_path
    specification = importlib.util.spec_from_file_location(module_name, script_path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


collect_moncloa = load_script("moncloa_pipeline_collect", "src/collect_moncloa.py")
extract_moncloa = load_script("moncloa_pipeline_extract", "src/extract_moncloa_text.py")


def moncloa_source() -> dict:
    """Return the minimum source configuration needed by parser tests."""

    return {
        "source_id": "lamoncloa_president_interventions",
        "source_name": "La Moncloa",
        "publisher": "La Moncloa",
        "publisher_type": "government",
        "adapter": "lamoncloa",
        "expected_actor_id": "pedro_sanchez",
        "actor_verified_by_source_scope": True,
        "allowed_hosts": ["lamoncloa.gob.es"],
        "selectors": {
            "body": ["section#contentArticleBody"],
            "location": [
                "p#ctl00_PlaceHolderMain_DisplayMode_noticia_pLocationYellowContain"
            ],
        },
        "exclude_document_types": ["parliamentary"],
        "monologue_document_types": ["speech", "statement", "article"],
        "full_text_markers": ["discurso", "intervención", "transcripción"],
    }


def test_canonical_urls_and_stable_ids_are_deterministic() -> None:
    """Tracking data must not change the canonical URL or stable identifier."""

    first = collect_moncloa.canonicalize_url(
        "http://WWW.LAMONCLOA.GOB.ES//presidente/intervenciones/pagina.aspx"
        "?utm_source=test&b=2&a=1#fragment",
        allowed_hosts=["lamoncloa.gob.es"],
    )
    second = collect_moncloa.canonicalize_url(
        "https://www.lamoncloa.gob.es/presidente/intervenciones/pagina.aspx?a=1&b=2",
        allowed_hosts=["lamoncloa.gob.es"],
    )

    assert first == second
    assert first == (
        "https://www.lamoncloa.gob.es/presidente/intervenciones/pagina.aspx?a=1&b=2"
    )
    assert extract_moncloa.stable_id("document", first) == extract_moncloa.stable_id(
        "document", second
    )
    assert extract_moncloa.stable_id("document", first) != extract_moncloa.stable_id(
        "document", first + "&c=3"
    )


def test_spanish_dates_are_normalized() -> None:
    """Both pipeline stages should recognize the source's common date formats."""

    assert collect_moncloa.parse_date_hint("14 de septiembre de 2026") == "2026-09-14"
    assert collect_moncloa.parse_date_hint("14/09/2026") == "2026-09-14"
    assert collect_moncloa.parse_date_hint("septiembre 11, 2026") == "2026-09-11"
    assert extract_moncloa.parse_spanish_date("14 de septiembre de 2026") == "2026-09-14"
    assert extract_moncloa.parse_spanish_date("Publicado el 14.09.2026") == "2026-09-14"
    assert extract_moncloa.parse_spanish_date("septiembre 11, 2026") == "2026-09-11"
    assert extract_moncloa.parse_spanish_date("31 de febrero de 2026") == ""


def test_moncloa_callback_and_actor_scope_are_explicit() -> None:
    """Pagination tokens and office-holder dates must not be guessed."""

    token, fragment = collect_moncloa._parse_moncloa_callback(
        "5|abcde<ul><li>page two</li></ul>"
    )
    assert token == "abcde"
    assert "page two" in fragment

    source = {
        "actor_scope_start_date": "2018-06-02",
        "actor_scope_end_date": "2026-12-31",
    }
    assert extract_moncloa.actor_scope_covers("2018-06-01", source) is False
    assert extract_moncloa.actor_scope_covers("2018-06-02", source) is True
    assert extract_moncloa.actor_scope_covers("2026-12-31", source) is True
    assert extract_moncloa.actor_scope_covers("2027-01-01", source) is False


def test_robots_policy_fails_closed_on_rate_limit(tmp_path: Path, monkeypatch) -> None:
    """A temporary robots error must stop collection instead of permitting it."""

    fetcher = collect_moncloa.Fetcher(
        collect_moncloa.requests.Session(),
        tmp_path / "cache",
        delay_seconds=0,
    )

    def rate_limited(*args, **kwargs):
        del args, kwargs
        raise collect_moncloa.FetchError(
            "Too Many Requests",
            "https://www.lamoncloa.gob.es/robots.txt",
            429,
        )

    monkeypatch.setattr(fetcher, "fetch", rate_limited)
    try:
        fetcher._robots_policy(
            "https://www.lamoncloa.gob.es/presidente/intervenciones/",
            ["lamoncloa.gob.es"],
        )
    except collect_moncloa.FetchError as error:
        assert error.status_code == 429
        assert "Could not verify robots.txt" in str(error)
    else:
        raise AssertionError("A 429 robots response should stop collection")


def test_fetch_html_accepts_html_bytes(tmp_path: Path, monkeypatch) -> None:
    """HTML sniffing must work on the byte body returned by the fetcher."""

    fetcher = collect_moncloa.Fetcher(
        collect_moncloa.requests.Session(),
        tmp_path / "cache",
        delay_seconds=0,
    )
    result = collect_moncloa.FetchResult(
        request_url="https://www.lamoncloa.gob.es/page",
        final_url="https://www.lamoncloa.gob.es/page",
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=b"  <!DOCTYPE html><html><body>ok</body></html>",
        encoding="utf-8",
        retrieved_at_utc="2026-09-16T00:00:00+00:00",
        body_sha256="fixture",
        cache_path="fixture.html",
        metadata_path="fixture.meta.json",
        response_headers={},
    )

    monkeypatch.setattr(fetcher, "fetch", lambda *args, **kwargs: result)
    returned = fetcher.fetch_html(
        result.request_url,
        source_id="lamoncloa",
        allowed_hosts=["lamoncloa.gob.es"],
    )

    assert returned is result


def test_external_urls_are_not_allowed_by_the_extractor() -> None:
    """Canonical metadata or redirects cannot move a record off-source."""

    source = {"allowed_hosts": ["www.lamoncloa.gob.es"]}
    assert extract_moncloa.allowed_url("https://www.lamoncloa.gob.es/page", source)
    assert not extract_moncloa.allowed_url("https://example.org/page", source)


def test_moncloa_html_extraction_preserves_spanish_accents() -> None:
    """Matching may fold accents internally, but collected text must not."""

    html = """
    <html><head>
      <meta property="og:url" content="https://www.lamoncloa.gob.es/presidente/intervenciones/Paginas/2026/entrevista.aspx">
    </head><body>
      <h1 id="ctl00_PlaceHolderMain_DisplayMode_noticia_h1Title">Entrevista al presidente</h1>
      <div id="noticia_sitedate"><span class="date">14 de septiembre de 2026</span></div>
      <p id="ctl00_PlaceHolderMain_DisplayMode_noticia_pLocationYellowContain">Madrid</p>
      <section id="contentArticleBody">
        <p>Pedro Sánchez habló de España.</p>
        <p>La pregunta citó a Alberto Núñez Feijóo y Santiago Abascal.</p>
      </section>
    </body></html>
    """

    page = extract_moncloa.extract_page(
        html,
        "https://www.lamoncloa.gob.es/presidente/intervenciones/Paginas/2026/entrevista.aspx",
        moncloa_source(),
    )

    assert page.publication_date == "2026-09-14"
    assert page.location == "Madrid"
    assert "Sánchez" in page.body_text
    assert "España" in page.body_text
    assert "Feijóo" in page.body_text
    assert "Nunez" not in page.body_text


def test_interview_split_keeps_only_responses_and_accepts_source_typo() -> None:
    """Pregunta turns are context, not politician utterances for the corpus."""

    answers, found_markers = extract_moncloa.split_interview_answers(
        [
            "Pregunta: ¿Qué piensa de la situación?",
            "Respuesta: España necesita una respuesta clara.",
            "Esta frase continúa la primera respuesta.",
            "El Gran Wyoming: ¿Puede concretar esa propuesta?",
            "P.- ¿Y sobre Europa?",
            "Repuesta: La cooperación europea es esencial.",
            "R.- También debemos proteger la cohesión social.",
        ]
    )

    assert found_markers is True
    assert len(answers) == 3
    assert answers[0] == (
        "España necesita una respuesta clara. "
        "Esta frase continúa la primera respuesta."
    )
    assert answers[1] == "La cooperación europea es esencial."
    assert answers[2] == "También debemos proteger la cohesión social."
    assert all("Qué piensa" not in answer for answer in answers)
    assert all("Puede concretar" not in answer for answer in answers)
    assert all("Europa?" not in answer for answer in answers)


def test_verified_moncloa_speech_becomes_one_monologue_segment() -> None:
    """A labelled official speech can be kept intact as one politician turn."""

    text = (
        "Buenos días. España necesita cooperación, cohesión social y una "
        "respuesta democrática compartida. " * 4
    ).strip()
    page = extract_moncloa.PageExtraction(
        title="Discurso del presidente del Gobierno",
        title_source="fixture",
        publication_date="2026-09-14",
        date_source="fixture",
        location="Madrid",
        canonical_url="https://www.lamoncloa.gob.es/discurso.aspx",
        body_text=text,
        body_blocks=[text],
        body_selector="fixture",
        has_video=False,
        jsonld={},
    )
    inventory = {
        "document_type": extract_moncloa.classify_document_type(page.title, moncloa_source()),
        "actor_verification": "verified_by_source_scope",
        "actor_name": "Pedro Sánchez",
    }

    status, reason, segments = extract_moncloa.determine_status_and_segments(
        page, inventory, moncloa_source(), min_text_chars=100
    )

    assert status == "included"
    assert reason == "verified_full_text"
    assert segments == [(text, "explicit_full_text")]


def test_party_press_release_is_not_automatically_included() -> None:
    """Third-person party news must remain for review, not become a leader turn."""

    text = (
        "Alberto Núñez Feijóo ha asegurado que presentará nuevas propuestas. "
        "El presidente del partido explicó el contexto de la reunión. "
        "Alberto Núñez Feijóo ha afirmado que continuará trabajando. " * 4
    ).strip()
    page = extract_moncloa.PageExtraction(
        title="Feijóo presenta nuevas propuestas",
        title_source="fixture",
        publication_date="2026-09-14",
        date_source="fixture",
        location="",
        canonical_url="https://www.pp.es/actualidad/nota.aspx",
        body_text=text,
        body_blocks=[text],
        body_selector="fixture",
        has_video=False,
        jsonld={},
    )
    source = {"adapter": "pp", "full_text_markers": []}
    inventory = {
        "document_type": "press_release",
        "actor_verification": "verified_in_title",
        "actor_name": "Alberto Núñez Feijóo",
    }

    status, reason, segments = extract_moncloa.determine_status_and_segments(
        page, inventory, source, min_text_chars=100
    )

    assert status == "review"
    assert reason == "third_person_press_release"
    assert segments == []


def test_candidate_store_deduplicates_and_csv_remains_utf8(tmp_path: Path) -> None:
    """One canonical page should produce one row even with tracking variants."""

    politicians = {
        "pedro_sanchez": {
            "actor_id": "pedro_sanchez",
            "display_name": "Pedro Sánchez",
            "party": "PSOE",
            "marker_aliases": ["Sánchez"],
        }
    }
    source = {
        "source_id": "lamoncloa",
        "source_name": "La Moncloa",
        "publisher": "La Moncloa",
        "publisher_type": "government",
        "expected_actor_id": "pedro_sanchez",
        "allowed_hosts": ["lamoncloa.gob.es"],
        "detail_url_pattern": r"/presidente/intervenciones/",
        "legal_notice_url": "https://www.lamoncloa.gob.es/Paginas/es_AvisoLegal.aspx",
        "redistribution_status": "internal_research_only",
    }
    store = collect_moncloa.CandidateStore(
        politicians=politicians,
        tracking_parameters=[],
        start=None,
        end=None,
        max_candidates_per_source=None,
    )
    stats = collect_moncloa.SourceStats("lamoncloa")
    first_added = store.add(
        source,
        stats,
        url="https://www.lamoncloa.gob.es/presidente/intervenciones/pagina.aspx?utm_source=x",
        title_hint="Entrevista a Pedro Sánchez sobre España",
        date_hint="14 de septiembre de 2026",
        discovery_method="fixture",
    )
    duplicate_added = store.add(
        source,
        stats,
        url="http://www.lamoncloa.gob.es/presidente/intervenciones/pagina.aspx#inicio",
        title_hint="Entrevista a Pedro Sánchez sobre España",
        date_hint="14/09/2026",
        discovery_method="fixture",
    )

    assert first_added is True
    assert duplicate_added is False
    assert len(store.rows) == 1
    assert stats.duplicates_skipped == 1
    row = next(iter(store.rows.values()))
    expected_id = "moncloa_" + collect_moncloa.sha256(
        "lamoncloa\nhttps://www.lamoncloa.gob.es/presidente/intervenciones/pagina.aspx"
    )[:24]
    assert row["candidate_id"] == expected_id

    output_path = tmp_path / "moncloa_candidates.csv"
    collect_moncloa.write_csv(output_path, [row], collect_moncloa.CANDIDATE_FIELDS)
    saved = output_path.read_text(encoding="utf-8")
    assert "Pedro Sánchez" in saved
    assert "España" in saved


def test_extraction_summary_reports_statuses_duplicates_and_date_range() -> None:
    """Summary counts should be justified directly by the produced rows."""

    arguments = Namespace(
        inventory=Path("moncloa_candidates.csv"),
        source=[],
        start="",
        end="",
        max_documents=None,
        min_text_chars=280,
        offline=True,
        refresh=False,
    )
    inventory = [
        {
            "document_status": "included",
            "status_reason": "verified_official_monologue",
            "source_id": "lamoncloa",
            "document_type": "speech",
            "publication_date": "2026-09-14",
            "is_exact_duplicate": False,
            "cache_hit": True,
        },
        {
            "document_status": "review",
            "status_reason": "exact_duplicate",
            "source_id": "lamoncloa",
            "document_type": "speech",
            "publication_date": "2026-09-15",
            "is_exact_duplicate": True,
            "cache_hit": True,
        },
    ]
    segments = [{"actor_id": "pedro_sanchez", "source_id": "lamoncloa"}]

    summary = extract_moncloa.build_summary(
        arguments,
        total_candidates=2,
        selected_candidates=2,
        inventory=inventory,
        segments=segments,
        output_files={"summary": "moncloa_summary.json"},
    )

    assert summary["documents_total"] == 2
    assert summary["documents_by_status"] == {"included": 1, "review": 1}
    assert summary["exact_duplicates"] == 1
    assert summary["segments_total"] == 1
    assert summary["date_range"] == {"start": "2026-09-14", "end": "2026-09-15"}


def test_extractor_main_writes_six_outputs_offline(
    tmp_path: Path, monkeypatch
) -> None:
    """A synthetic Moncloa interview should complete the full second stage."""

    sources_path = tmp_path / "moncloa_sources.json"
    politicians_path = tmp_path / "politicians.json"
    inventory_path = tmp_path / "moncloa_candidates.csv"
    output_dir = tmp_path / "processed"
    cache_dir = tmp_path / "cache"
    url = "https://www.lamoncloa.gob.es/presidente/intervenciones/Paginas/2026/interview.aspx"

    source = moncloa_source()
    source.update(
        {
            "source_name": "La Moncloa - Intervenciones del presidente",
            "legal_notice_url": "https://www.lamoncloa.gob.es/Paginas/es_AvisoLegal.aspx",
            "redistribution_status": "internal_research_only_pending_review",
            "actor_scope_start_date": "2018-06-02",
        }
    )
    sources_path.write_text(
        json.dumps(
            {
                "default_start_date": "2023-07-23",
                "default_delay_seconds": 0,
                "sources": [source],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    politicians_path.write_text(
        json.dumps(
            [
                {
                    "actor_id": "pedro_sanchez",
                    "display_name": "Pedro Sánchez",
                    "party": "PSOE",
                    "marker_aliases": ["Sánchez"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    collect_moncloa.write_csv(
        inventory_path,
        [
            {
                "candidate_id": "moncloa_fixture",
                "source_id": source["source_id"],
                "expected_actor_id": "pedro_sanchez",
                "expected_actor_name": "Pedro Sánchez",
                "party": "PSOE",
                "title_hint": "Entrevista al presidente del Gobierno",
                "date_hint": "2026-09-14",
                "url": url,
                "canonical_url": url,
                "content_type_hint": "html",
            }
        ],
        collect_moncloa.CANDIDATE_FIELDS,
    )
    html = """
    <html><head><meta property="og:url" content="%s"></head><body>
      <h1 id="ctl00_PlaceHolderMain_DisplayMode_noticia_h1Title">Entrevista al presidente del Gobierno</h1>
      <div id="noticia_sitedate"><span class="date">14 de septiembre de 2026</span></div>
      <section id="contentArticleBody">
        <p>Respuesta: España necesita cooperación, cohesión social y una respuesta democrática compartida.</p>
        <p>El Gran Wyoming: ¿Puede concretar esa propuesta?</p>
        <p>Respuesta: Debemos proteger la convivencia y reforzar las instituciones.</p>
      </section>
    </body></html>
    """ % url

    def fake_fetch(self, source_id, requested_url, allowed_hosts):
        del self, allowed_hosts
        return extract_moncloa.FetchResult(
            html=html,
            requested_url=requested_url,
            final_url=requested_url,
            retrieved_at_utc="2026-09-16T00:00:00+00:00",
            http_status=200,
            cache_hit=True,
            cache_path=f"{source_id}/fixture.html",
            raw_sha256=extract_moncloa.sha256_text(html),
        )

    monkeypatch.setattr(extract_moncloa.PoliteFetcher, "fetch", fake_fetch)
    result = extract_moncloa.main(
        [
            "--inventory",
            str(inventory_path),
            "--sources-config",
            str(sources_path),
            "--politicians-config",
            str(politicians_path),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--min-text-chars",
            "20",
        ]
    )

    assert result == 0
    expected = {
        "moncloa_inventory.csv",
        "moncloa_documents_internal.csv",
        "moncloa_segments_internal.csv",
        "moncloa_stimuli.csv",
        "moncloa_extraction_audit.csv",
        "moncloa_summary.json",
    }
    assert expected == {path.name for path in output_dir.iterdir()}
    segments = (output_dir / "moncloa_segments_internal.csv").read_text(encoding="utf-8")
    assert "España necesita cooperación" in segments
    assert "Debemos proteger la convivencia" in segments
    assert "Puede concretar" not in segments
