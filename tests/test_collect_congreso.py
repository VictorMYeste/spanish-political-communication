import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


collect = load_module("collect_congreso", ROOT / "src" / "collect_congreso.py")
extract = load_module("extract_congreso_text", ROOT / "src" / "extract_congreso_text.py")


POLITICIANS = [
    {
        "actor_id": "pedro_sanchez",
        "display_name": "Pedro Sánchez",
        "congreso_name": "Sánchez Pérez-Castejón, Pedro",
        "marker_aliases": ["Sánchez Pérez-Castejón", "Sánchez"],
        "party": "PSOE",
    },
    {
        "actor_id": "alberto_nunez_feijoo",
        "display_name": "Alberto Núñez Feijóo",
        "congreso_name": "Núñez Feijóo, Alberto",
        "marker_aliases": ["Núñez Feijóo", "Feijóo"],
        "party": "PP",
    },
]


def test_normalize_speaker_removes_group_suffix():
    assert collect.normalize_speaker("Sánchez Pérez-Castejón, Pedro (GS)") == collect.normalize_speaker(
        "Sánchez Pérez-Castejón, Pedro"
    )


def test_select_records_filters_and_deduplicates():
    base = {
        "ORADOR": "Sánchez Pérez-Castejón, Pedro (GS)",
        "SESION": "15/11/2023",
        "INICIOINTERVENCION": "12:00",
        "FININTERVENCION": "12:10",
        "ENLACEDIFERIDO": "https://example.test/intervention/1",
        "ENLACETEXTOINTEGRO": "https://example.test/text/1",
    }
    rows, provenance, summary = collect.select_records(
        [base, dict(base), {**base, "ORADOR": "Someone Else"}],
        POLITICIANS,
        "https://example.test/source.json",
        "abc123",
        "2026-09-14T00:00:00+00:00",
    )
    assert len(rows) == 1
    assert len(provenance) == 2
    assert rows[0]["actor_id"] == "pedro_sanchez"
    assert summary["total_source_rows"] == 3
    assert summary["matched_rows_before_deduplication"]["pedro_sanchez"] == 2


def test_extracts_only_target_speaker_turns():
    page = """
El señor SÁNCHEZ PÉREZ-CASTEJÓN (presidente del Gobierno): Este es un discurso suficientemente largo para superar el umbral mínimo de la prueba sin problemas.
La señora PRESIDENTA: Muchas gracias, señor Sánchez. Continúa la sesión plenaria.
El señor NÚÑEZ FEIJÓO: Esta es otra intervención suficientemente larga para que el extractor pueda conservarla correctamente.
""".strip()
    segments, diagnostics = extract.extract_target_segments(
        page, POLITICIANS, min_chars=30
    )

    assert [s.politician["actor_id"] for s in segments] == [
        "pedro_sanchez",
        "alberto_nunez_feijoo",
    ]
    assert all("PRESIDENTA" not in s.text for s in segments)


def test_document_id_from_official_query():
    url = (
        "https://www.congreso.es/es/busqueda-de-intervenciones?"
        "_intervenciones_id_texto=(DSCD-15-PL-7.CODI.)"
    )
    assert extract.document_id(url) == "DSCD-15-PL-7"
