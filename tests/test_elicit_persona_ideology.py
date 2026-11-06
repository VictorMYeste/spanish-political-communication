import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

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


ideology = load_module("elicit_persona_ideology", ROOT / "src" / "elicit_persona_ideology.py")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# A stand-in for Candida's reaction script: the same three names the script uses.
FAKE_REACTIONS_SCRIPT = '''
import hashlib, re
TOPICS = ["economy_employment", "corruption"]
PERSONAS = [
    ("1", "economy_employment", "Persona one."),
    ("1", "corruption", "Persona one."),
    ("2", "corruption", "Persona two."),
]
def load_mft_responses_lookup(path):
    return {}
def load_personas(path, framework, topics, max_rank, mft_responses_lookup=None):
    return [
        {"persona_id": pid, "topic": topic, "persona_text": text,
         "persona_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        for pid, topic, text in PERSONAS
    ]
def safe_slug(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
'''


def test_prompt_anchors_the_scale_to_spain_only_in_the_spain_frame():
    spain = ideology.build_prompt("Persona text.", "spain")
    generic = ideology.build_prompt("Persona text.", "generic")
    assert spain.startswith("[PERSONA PROFILE]\nPersona text.\n\n[SURVEY QUESTION]\n")
    assert "politics in Spain" in spain and "of Spanish politics" in spain
    assert "Spain" not in generic and "Spanish" not in generic
    for prompt in (spain, generic):
        assert '"the furthest to the left"' in prompt
        assert prompt.endswith('{"left_right_self_placement": "integer from 1 to 10"}')
        # Parties and leaders are the RQ4 outcome and must not appear.
        for name in ("PSOE", "PP", "VOX", "Sánchez", "Feijóo", "Abascal"):
            assert name not in prompt


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"left_right_self_placement": 3}', 3),
        ('{"left_right_self_placement": "7"}', 7),
        ('{"left_right_self_placement": 10.0}', 10),
        ('Answer: {"left_right_self_placement": 1} done', 1),
    ],
)
def test_parse_valid_placements(text, expected):
    assert ideology.parse_placement(text) == (expected, None)


@pytest.mark.parametrize(
    "text, reason",
    [
        ("", "empty_output"),
        ("I would rather not say.", "no_json_object"),
        ('{"left_right": 4}', "missing_left_right_self_placement"),
        ('{"left_right_self_placement": 0}', "out_of_range"),
        ('{"left_right_self_placement": 11}', "out_of_range"),
        ('{"left_right_self_placement": 5.5}', "out_of_range"),
        ('{"left_right_self_placement": "centre"}', "not_an_integer"),
        ('{"left_right_self_placement": null}', "not_an_integer"),
        ('{"left_right_self_placement": true}', "not_an_integer"),
    ],
)
def test_parse_rejects_anything_but_an_integer_from_1_to_10(text, reason):
    placement, error = ideology.parse_placement(text)
    assert placement is None and error.startswith(reason)


def test_provisional_groups():
    assert [ideology.group_of(v) for v in (1, 4, 5, 6, 7, 10)] == ["left", "left", "centre", "centre", "right", "right"]


def fake_module(rows):
    return SimpleNamespace(
        TOPICS=["economy_employment", "corruption"],
        load_mft_responses_lookup=lambda path: {},
        load_personas=lambda *args, **kwargs: rows,
    )


def row(pid, topic, text):
    return {"persona_id": pid, "topic": topic, "persona_text": text, "persona_sha256": sha(text)}


def test_a_persona_in_several_topics_is_asked_once():
    module = fake_module([row("1", "economy_employment", "A"), row("1", "corruption", "A"), row("2", "corruption", "B")])
    personas = ideology.collect_personas(module, "iw", Path("unused.csv"), None)
    assert [(p["persona_id"], p["topics"]) for p in personas] == [("1", ["economy_employment", "corruption"]), ("2", ["corruption"])]


def test_a_persona_with_two_texts_is_an_error():
    module = fake_module([row("1", "economy_employment", "A"), row("1", "corruption", "A changed")])
    with pytest.raises(ValueError, match="different texts"):
        ideology.collect_personas(module, "iw", Path("unused.csv"), None)


def write_reactions(root, framework, pairs):
    folder = root / "run" / "model" / framework / "corruption" / "anonymous"
    folder.mkdir(parents=True)
    with (folder / "responses_active.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["persona_id", "persona_sha256", "agreement"])
        for pid, digest in pairs:
            writer.writerow([pid, digest, 3])


def test_check_against_reactions(tmp_path):
    personas = [row("1", "corruption", "A"), row("2", "corruption", "B")]
    write_reactions(tmp_path / "same", "iw", [("1", sha("A")), ("2", sha("B"))])
    assert ideology.check_against_reactions(personas, "iw", tmp_path / "same") == set()

    write_reactions(tmp_path / "other", "iw", [("1", sha("A")), ("2", sha("B, as in the reactions"))])
    with pytest.raises(ValueError, match=r"1 of 2 iw persona texts differ"):
        ideology.check_against_reactions(personas, "iw", tmp_path / "other")
    assert ideology.check_against_reactions(personas, "iw", tmp_path / "other", allow_mismatch=True) == {"2"}

    write_reactions(tmp_path / "absent", "iw", [("1", sha("A")), ("3", sha("C"))])
    with pytest.raises(ValueError, match="not in --personas"):
        ideology.check_against_reactions(personas, "iw", tmp_path / "absent")


class FakeOpenAI:
    """Answers 3 for persona one; the first call for persona two fails once."""

    calls = []

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        prompt = kwargs["messages"][1]["content"]
        FakeOpenAI.calls.append(prompt)
        if "Persona two." in prompt and sum("Persona two." in c for c in FakeOpenAI.calls) == 1:
            content = "not json"
        else:
            content = json.dumps({"left_right_self_placement": 3 if "Persona one." in prompt else 8})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_run_writes_answers_and_resumes(tmp_path, monkeypatch, capsys):
    script = tmp_path / "reactions.py"
    script.write_text(FAKE_REACTIONS_SCRIPT, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(ideology.time, "sleep", lambda seconds: None)
    FakeOpenAI.calls = []
    args = ["--framework", "iw", "--reactions-script", str(script), "--personas", str(tmp_path / "p.csv"),
            "--output-dir", str(tmp_path / "out"), "--model", "openai/gpt-oss-20b"]

    assert ideology.main(args) == 0
    assert len(FakeOpenAI.calls) == 3  # persona two needed a second attempt
    assert all("politics in Spain" in prompt for prompt in FakeOpenAI.calls)
    with (tmp_path / "out" / "openai_gpt-oss-20b" / "iw_spain.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["persona_id"], r["left_right_self_placement"], r["attempts"], r["topics"]) for r in rows] == [
        ("1", "3", "1", "economy_employment;corruption"),
        ("2", "8", "2", "corruption"),
    ]
    assert "left 1 (50%)  centre 0 (0%)  right 1 (50%)" in capsys.readouterr().out

    FakeOpenAI.calls = []
    assert ideology.main(args) == 0
    assert FakeOpenAI.calls == []  # both answered already

    with pytest.raises(RuntimeError, match="other settings"):
        ideology.main(args + ["--temperature", "0.5"])


def test_dry_run_makes_no_calls(tmp_path, monkeypatch, capsys):
    script = tmp_path / "reactions.py"
    script.write_text(FAKE_REACTIONS_SCRIPT, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "openai", None)  # importing it would fail
    args = ["--framework", "iw", "--frame", "generic", "--reactions-script", str(script),
            "--personas", str(tmp_path / "p.csv"), "--output-dir", str(tmp_path / "out"), "--dry-run"]
    assert ideology.main(args) == 0
    out = capsys.readouterr().out
    assert "calls to make: 2" in out and "Persona one." in out and "Spain" not in out
    assert not (tmp_path / "out").exists()
