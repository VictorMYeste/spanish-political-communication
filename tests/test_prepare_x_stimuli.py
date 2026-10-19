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


prepare = load_module("prepare_x_stimuli", ROOT / "src" / "prepare_x_stimuli.py")

SANCHEZ = "pedro_sanchez"
FEIJOO = "alberto_nunez_feijoo"
ABASCAL = "santiago_abascal"
QUALITY = {"min_chars": 40, "min_alpha_tokens": 7}


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "config" / "x_stimulus_filters.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def markers(config):
    return prepare.RoleMarkers(config["role_markers"])


def test_clean_decodes_entities_and_removes_urls():
    assert prepare.clean("Paro &amp; precios   https://t.co/abc\n hoy") == "Paro & precios hoy"


def test_quality_reasons():
    long_text = "Hay que bajar los impuestos a las familias que más lo necesitan"
    assert prepare.quality_reasons("¡Gracias Talayuela! #Extremadura", QUALITY) == ["too_short", "too_few_words"]
    assert prepare.quality_reasons(long_text, QUALITY) == []


@pytest.fixture(scope="module")
def anonymizer(config):
    return prepare.Anonymizer(config["anonymization"])


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Pedro Sánchez y su Gobierno", "[POLITICAL_ACTOR] y su Gobierno"),
        ("SANCHEZ miente y el sanchismo también", "[POLITICAL_ACTOR] miente y el [POLITICAL_ACTOR] también"),
        ("Alberto Núñez Feijóo y Abascal", "[POLITICAL_ACTOR] y [POLITICAL_ACTOR]"),
        ("Gracias @sanchezcastejon y @Santi_ABASCAL", "Gracias [POLITICAL_ACTOR] y [POLITICAL_ACTOR]"),
        ("El PP, el PSOE y Vox votan con el Partido Popular Europeo", "El [PARTY], el [PARTY] y [PARTY] votan con el [PARTY]"),
        ("Con @ppopular, @PSOE_M y @vox_es", "Con [PARTY], [PARTY] y [PARTY]"),
        ("Hoy en el @Congreso_Es con @JMilei", "Hoy en el Congreso de los Diputados con [USER]"),
        ("Vota #SentidoComún #EsElMomento #VotaPP #FeijóoPresidente", "Vota [CAMPAIGN_TAG] [CAMPAIGN_TAG] [CAMPAIGN_TAG] [CAMPAIGN_TAG]"),
        ("Toda la ayuda tras la #DANA y en la #COP28", "Toda la ayuda tras la #DANA y en la #COP28"),
        ("Pedro Rollán y Ayuso, en Madrid", "Pedro Rollán y Ayuso, en Madrid"),
    ],
)
def test_anonymize(anonymizer, text, expected):
    assert anonymizer.anonymize(text) == expected


def test_anonymization_reasons(anonymizer):
    assert anonymizer.reasons("Hoy [POLITICAL_ACTOR] ha vuelto a mentir a todos los españoles sin pudor") == []
    # A misspelling escapes the name patterns; the leak check catches it.
    assert anonymizer.reasons("Hoy Sanchéz ha vuelto a mentir a todos los españoles, otra vez") == ["identity_leak"]
    assert anonymizer.reasons("[POLITICAL_ACTOR], [PARTY], [USER] y [USER] con [CAMPAIGN_TAG] hoy aquí") == ["anonymization_degraded"]


@pytest.mark.parametrize(
    "author, text, groups, implausible",
    [
        (SANCHEZ, "El Gobierno de España aprueba hoy el reconocimiento de Palestina", ["government_voice"], [FEIJOO, ABASCAL]),
        (FEIJOO, "El Gobierno los sube 3.000 millones con una recaudación récord", ["against_government"], [SANCHEZ]),
        (SANCHEZ, "Que la derecha y la ultraderecha frenen ya este atropello", ["against_right"], [FEIJOO, ABASCAL]),
        (ABASCAL, "El Pacto Verde de populares y socialistas en Bruselas", ["against_pp"], [FEIJOO]),
        (ABASCAL, "Hay que detener a la izquierda globalista", ["against_left"], [SANCHEZ]),
        (ABASCAL, "Nos han robado las fronteras. ¡DEPORTACIONES Y DIMISIONES YA!", ["against_government"], [SANCHEZ]),
        (ABASCAL, "Hoy he podido decirle a Sánchez lo que piensa una mayoría", ["against_government"], [SANCHEZ]),
        (SANCHEZ, "Encuentro previo al Consejo Europeo con el canciller alemán", ["government_voice"], [FEIJOO, ABASCAL]),
        (ABASCAL, "En Budapest con el primer ministro de Hungría", [], []),
        (FEIJOO, "Fin al agotamiento del gobierno autonómico", [], []),
        (FEIJOO, "Un socialista coherente y un hombre de fiar", [], []),
        (SANCHEZ, "Moción de censura contra el alcalde", [], []),
    ],
)
def test_role_markers(markers, author, text, groups, implausible):
    hits = markers.match(text, author)
    assert list(hits) == groups
    assert sorted({a for hit in hits.values() for a in hit["implausible_under"]}) == sorted(implausible)


def test_prepare_cleans_text_and_keeps_other_columns(config):
    internal = [
        {"message_id": "x_1", "actor_id": SANCHEZ, "text": "El Gobierno de España sube el salario mínimo a las familias https://t.co/a"},
        {"message_id": "x_2", "actor_id": FEIJOO, "text": "Ánimo, Pedro."},
    ]
    stimuli = [
        {"message_id": "x_1", "date": "2024-01-01", "topic": "economy_employment", "text_anon": "raw", "keep_for_prompt": "False"},
        {"message_id": "x_2", "date": "2024-01-02", "topic": "other", "text_anon": "raw", "keep_for_prompt": "False"},
    ]
    out, qa = prepare.prepare(stimuli, internal, config)

    assert out[0]["text_anon"] == "El Gobierno de España sube el salario mínimo a las familias"
    assert out[0]["keep_for_prompt"] == "False"
    assert qa[0]["role_groups"] == "government_voice"
    assert qa[0]["implausible_under"] == f"{FEIJOO}|{ABASCAL}"
    assert qa[0]["exclude_reason"] == ""
    assert qa[1]["exclude_reason"] == "too_short|too_few_words|anonymization_degraded"
    with pytest.raises(ValueError):
        prepare.prepare([{"message_id": "x_3", "date": "", "topic": "other"}], internal, config)
