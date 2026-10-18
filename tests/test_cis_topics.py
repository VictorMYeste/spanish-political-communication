import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


collect = load_module("collect_cis_problems", ROOT / "src" / "collect_cis_problems.py")
select = load_module("select_cis_topics", ROOT / "src" / "select_cis_topics.py")

TABLE_TEXT = """Pregunta 13
¿Cuál es, a su juicio, el principal problema que existe actualmente en España? ¿Y el segundo? ¿Y el tercero?
(RESPUESTA ESPONTÁNEA). (MULTIRRESPUESTA).
Primer problema
[recod.]
Segundo problema
[recod.]
Tercer problema
[recod.] TOTAL
La inmigración 9,4 12,8 8,2 30,4
La crisis económica, los problemas de índole económica 6,5 8,2 5,1 19,8
CIS
Estudio nº3474. BARÓMETRO DE SEPTIEMBRE 2024
Pág 6
Primer
problema
Segundo
problema
Tercer
problema TOTAL
Problemas psicológicos derivados de la pandemia (preocupaciones, soledad, tristeza,
desamparo, etc.)
- - 0,1 0,1
(N) (4.027)
Pregunta 14
"""


def config(**overrides):
    base = {
        "top_k": 2,
        "label_aliases": {"El desempleo": "El paro"},
        "exclusions": [
            {"pattern": "problemas politicos en general", "group": "political"},
            {"pattern": "^n\\.s\\.$", "group": "non_substantive"},
        ],
        "mapping": {
            "household_costs": ["^la vivienda$", "tarifas energeticas"],
            "economy_employment": ["crisis economica", "^el paro$"],
        },
    }
    base.update(overrides)
    return base


def test_parse_problem_table_handles_headers_wrapping_and_dashes():
    rows, n_value = collect.parse_problem_table(TABLE_TEXT)

    assert n_value == 4027
    assert [row["category"] for row in rows] == [
        "La inmigración",
        "La crisis económica, los problemas de índole económica",
        "Problemas psicológicos derivados de la pandemia (preocupaciones, soledad, tristeza, desamparo, etc.)",
    ]
    assert rows[0]["total_pct"] == 30.4
    assert rows[2]["first_pct"] == 0.0 and rows[2]["total_pct"] == 0.1


def test_parse_problem_table_rejects_transposed_layout():
    text = (
        "¿Cuál es, a su juicio, el principal problema que existe actualmente en España?\n"
        "La crisis\neconómica\nEl paro\n"
        "Primer problema 18,6 15,0\n"
        "TOTAL 39,3 33,9\n"
        "La vivienda 1,0 1,0 1,0 3,0\n"
        "Pregunta 6\n"
    )
    with pytest.raises(ValueError, match="layout not supported"):
        collect.parse_problem_table(text)


def test_parse_study_page_finds_study_and_results_pdf():
    html = (
        '<p>Estudio nº 3359</p>'
        '<a href="https://www.cis.es/documents/20117/1/cues3359pdf.pdf">q</a>'
        '<a href="/documents/20117/1/es3359marpdf.pdf/abc?version=1.0&amp;t=1">m</a>'
    )
    study_id, pdf_url = collect.parse_study_page(html)

    assert study_id == "3359"
    assert pdf_url == "https://www.cis.es/documents/20117/1/es3359marpdf.pdf/abc?version=1.0&t=1"


def test_preview_page_supplies_study_number_and_is_found_on_homepage():
    homepage = '<a href="https://www.cis.es/en/w/avance-de-resultados-del-estudio-3582-bar%C3%B3metro-de-octubre-2026-">x</a>'
    preview_url = collect.find_preview_url(homepage, 2026, 10)
    study_id, pdf_url = collect.parse_study_page('<a href="/documents/d/guest/es3582mar_A-pdf">m</a>', preview_url)

    assert preview_url.endswith("octubre-2026-")
    assert collect.find_preview_url(homepage, 2026, 9) is None
    assert (study_id, pdf_url) == ("3582", "https://www.cis.es/documents/d/guest/es3582mar_A-pdf")


def test_month_range_crosses_years():
    assert collect.month_range("2022-11", "2023-02") == [(2022, 11), (2022, 12), (2023, 1), (2023, 2)]


def test_classifier_applies_aliases_exclusions_and_mapping():
    classifier = select.Classifier(config())

    assert classifier.classify("Los problemas políticos en general") == ("excluded", "political")
    assert classifier.classify("N.S.") == ("excluded", "non_substantive")
    assert classifier.classify("La vivienda") == ("topic", "household_costs")
    assert classifier.classify("La ocupación de viviendas") == ("standalone", "La ocupación de viviendas")
    assert classifier.classify("El desempleo") == ("topic", "economy_employment")
    assert classifier.classify("La subida de tarifas energéticas, crisis energética") == ("topic", "household_costs")


def test_rank_topics_sums_within_and_averages_with_zeros():
    rows = [
        {"study_id": "1", "year": "2024", "month": "1", "category": "La vivienda", "total_pct": "10"},
        {"study_id": "1", "year": "2024", "month": "1", "category": "La subida de tarifas energéticas", "total_pct": "4"},
        {"study_id": "1", "year": "2024", "month": "1", "category": "El paro", "total_pct": "20"},
        {"study_id": "1", "year": "2024", "month": "1", "category": "La sanidad", "total_pct": "9"},
        {"study_id": "1", "year": "2024", "month": "1", "category": "Los problemas políticos en general", "total_pct": "50"},
        {"study_id": "2", "year": "2024", "month": "2", "category": "La vivienda", "total_pct": "20"},
        {"study_id": "2", "year": "2024", "month": "2", "category": "El paro", "total_pct": "10"},
    ]
    ranking, audit = select.rank_topics(rows, config())
    by_topic = {item["topic"]: item for item in ranking}

    assert by_topic["household_costs"]["mean_pct"] == 17.0
    assert by_topic["household_costs"]["peak_pct"] == 20.0
    assert by_topic["economy_employment"]["mean_pct"] == 15.0
    assert by_topic["La sanidad"]["mean_pct"] == 4.5
    assert [item["topic"] for item in ranking if item["selected"]] == ["household_costs", "economy_employment"]
    assert by_topic["[excluded: political]"]["rank"] == ""
    assert {row["category"] for row in audit} == {row["category"] for row in rows}


def test_project_config_patterns_compile_and_match_known_labels():
    project = json.loads((ROOT / "config" / "cis_topic_selection.json").read_text(encoding="utf-8"))
    classifier = select.Classifier(project)

    assert project["top_k"] == 5
    assert project["min_original_posts_per_leader"] == 10
    assert classifier.classify("La corrupción y el fraude") == ("topic", "corruption")
    assert classifier.classify("Crisis con Marruecos. Fronteras de Ceuta y Melilla") == ("topic", "immigration_border")
    assert classifier.classify("El Gobierno y partidos o políticos/as concretos/as") == ("excluded", "political")
    assert classifier.classify("Las hipotecas") == ("standalone", "Las hipotecas")
