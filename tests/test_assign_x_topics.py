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


assign = load_module("assign_x_topics", ROOT / "src" / "assign_x_topics.py")

SPEC = {
    "exclude_phrases": ["cordon sanitario"],
    "topics": [
        {"topic": "household_costs", "groups": {"housing": ["vivienda(s)?"], "prices": ["inflacion"]}},
        {"topic": "health", "groups": {"health": ["sanitari[oa]s?", "sanidad"]}},
    ],
}


@pytest.fixture(scope="module")
def project():
    spec = json.loads((ROOT / "config" / "x_topic_keywords.json").read_text(encoding="utf-8"))
    return assign.Registry(spec)


def post(message_id, actor, text, post_type="original"):
    return {
        "message_id": message_id,
        "post_id": message_id,
        "actor_id": actor,
        "post_type": post_type,
        "date": "2024-01-01",
        "text": text,
    }


def test_normalize_removes_urls_accents_and_case():
    assert assign.normalize("La INFLACIÓN https://t.co/abc  sube") == "la inflacion sube"


def test_assign_exactly_one_several_or_none():
    registry = assign.Registry(SPEC)
    rows = assign.label_posts(
        [
            post("1", "a", "La vivienda y la inflación"),
            post("2", "a", "Vivienda y sanidad"),
            post("3", "a", "Buenos días"),
            post("4", "a", "Un cordón sanitario contra ellos"),
        ],
        registry,
    )

    assert [row["topic"] for row in rows] == ["household_costs", "review_needed", "other", "other"]
    assert rows[0]["topic_groups"] == "household_costs:housing|household_costs:prices"
    assert rows[0]["matched_terms"] == "inflacion; vivienda"
    assert rows[1]["topic_candidates"] == "household_costs|health"


def test_coverage_counts_original_posts_only():
    labelled = [
        {"topic": "health", "actor_id": "a", "post_type": "original"},
        {"topic": "health", "actor_id": "b", "post_type": "original"},
        {"topic": "health", "actor_id": "b", "post_type": "quote"},
        {"topic": "other", "actor_id": "a", "post_type": "original"},
    ]
    rows = {row["topic"]: row for row in assign.coverage(labelled, ["health"], ["a", "b"], 1)}

    assert rows["health"]["b"] == 1
    assert rows["health"]["kept"] is True
    assert {row["topic"]: row for row in assign.coverage(labelled, ["health"], ["a", "b"], 2)}["health"]["kept"] is False
    assert "kept" not in rows["other"]


def test_spotcheck_sample_is_reproducible_and_original_only():
    posts = [post(str(i), "a", "vivienda") for i in range(30)] + [post("q", "a", "vivienda", "quote")]
    registry = assign.Registry(SPEC)
    labelled = assign.label_posts(posts, registry)
    first = assign.spotcheck_sample(labelled, posts, registry.names, 5, seed=1)
    second = assign.spotcheck_sample(labelled, posts, registry.names, 5, seed=1)

    assert first == second
    assert len(first) == 5
    assert "q" not in {row["message_id"] for row in first}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hay que recuperar el control de nuestras fronteras", "immigration_border"),
        ("Gracias a todos los que nos siguen fuera de nuestras fronteras", "other"),
        ("Vamos a bajar el precio de la luz", "household_costs"),
        ("La pobreza energética no es una opción", "household_costs"),
        ("Han hipotecado el futuro de nuestros hijos", "other"),
        ("Viviendas afectadas por la DANA", "other"),
        ("El paro baja por quinto mes consecutivo", "economy_employment"),
        ("Reunión con los presidentes de las comunidades autónomas", "other"),
        ("Gracias por la gente hospitalaria de Aragón", "other"),
        ("Un cordón sanitario contra VOX", "other"),
        ("Más inversión en atención primaria", "health"),
        ("La trama de Koldo y Ábalos", "corruption"),
        ("Más empleo y mejores viviendas", "review_needed"),
    ],
)
def test_project_registry_examples(project, text, expected):
    assert assign.assign(project.match(text)) == expected
