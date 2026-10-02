import ast
import csv
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = load_module("build_x_tables", ROOT / "src" / "build_x_tables.py")


def literal_list(path, name):
    """Read a module-level list constant without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def write_raw(tmp_path, actor_id, month, pages):
    path = tmp_path / actor_id / f"{month}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(page) for page in pages) + "\n", encoding="utf-8")
    return path


def test_stimulus_fields_match_the_congreso_pipeline():
    congreso = literal_list(ROOT / "src" / "extract_congreso_text.py", "STIMULUS_FIELDS")
    assert build.STIMULUS_FIELDS == congreso


def test_post_text_prefers_the_long_version():
    assert build.post_text({"text": "short"}) == ("short", False)
    assert build.post_text({"text": "trunc…", "note_tweet": {"text": "full text"}}) == ("full text", True)


def test_classify_post_types():
    assert build.classify_post({}) == ("original", None)
    assert build.classify_post({"referenced_tweets": [{"type": "replied_to", "id": "7"}]}) == ("reply", "7")
    assert build.classify_post({"referenced_tweets": [{"type": "quoted", "id": "8"}]}) == ("quote", "8")
    both = {"referenced_tweets": [{"type": "replied_to", "id": "7"}, {"type": "quoted", "id": "8"}]}
    assert build.classify_post(both) == ("quote", "8")


def test_local_date_uses_madrid_time():
    assert build.local_date("2024-06-30T22:30:00.000Z") == "2024-07-01"
    assert build.local_date("2023-12-31T23:30:00.000Z") == "2024-01-01"
    assert build.local_date("2024-03-15T10:00:00.000Z") == "2024-03-15"
    assert build.local_date("") == ""


def test_build_rows_end_to_end(tmp_path):
    page_one = {
        "data": [
            {
                "id": "100",
                "author_id": "1",
                "created_at": "2024-05-02T09:00:00.000Z",
                "text": "Vergonzoso.",
                "lang": "es",
                "conversation_id": "100",
                "referenced_tweets": [{"type": "quoted", "id": "900"}],
                "public_metrics": {"retweet_count": 5, "reply_count": 2, "like_count": 9, "quote_count": 1},
                "edit_history_tweet_ids": ["100"],
            },
            {
                "id": "101",
                "author_id": "1",
                "created_at": "2024-05-01T07:00:00.000Z",
                "text": "truncated…",
                "note_tweet": {"text": "A long post about housing policy."},
            },
        ],
        "includes": {"tweets": [{"id": "900", "author_id": "77", "text": "Quoted statement."}]},
        "meta": {"next_token": "t1"},
    }
    page_two = {"data": [{"id": "100", "author_id": "1", "created_at": "2024-05-02T09:00:00.000Z", "text": "dupe"}]}
    write_raw(tmp_path, "pedro_sanchez", "2024-05", [page_one, page_two])
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps(
            {
                "type": "month_complete",
                "actor_id": "pedro_sanchez",
                "month": "2024-05",
                "finished_at_utc": "2026-09-24T10:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    accounts = [{"actor_id": "pedro_sanchez", "x_handle": "sanchezcastejon", "x_user_id": "1"}]
    politicians = {"pedro_sanchez": {"display_name": "Pedro Sánchez", "party": "PSOE"}}
    rows, summary = build.build_rows(tmp_path, accounts, politicians)

    assert summary["posts"] == 2
    assert summary["duplicates_skipped"] == 1
    assert summary["posts_by_type"] == {"quote": 1, "original": 1}
    assert summary["references_without_text"] == 0
    assert summary["first_date"] == "2024-05-01"

    by_id = {row["post_id"]: row for row in rows}
    quote = by_id["100"]
    assert quote["message_id"] == "x_100"
    assert quote["post_type"] == "quote"
    assert quote["referenced_post_id"] == "900"
    assert quote["referenced_post_text"] == "Quoted statement."
    assert quote["referenced_post_author_id"] == "77"
    assert quote["actor_name"] == "Pedro Sánchez"
    assert quote["party"] == "PSOE"
    assert quote["post_url"] == "https://x.com/sanchezcastejon/status/100"
    assert quote["raw_file"] == "pedro_sanchez/2024-05.jsonl"
    assert quote["retrieved_at_utc"] == "2026-09-24T10:00:00+00:00"
    assert quote["like_count"] == 9

    long_post = by_id["101"]
    assert long_post["text"] == "A long post about housing policy."
    assert long_post["is_long_post"] == "True"
    assert long_post["referenced_post_id"] == ""
    assert [row["date"] for row in rows] == ["2024-05-01", "2024-05-02"]


def test_posts_containing_unicode_line_separators_are_read(tmp_path):
    """U+2028 inside post text must not be treated as a line break."""
    page = {
        "data": [
            {
                "id": "300",
                "author_id": "1",
                "created_at": "2024-05-02T09:00:00.000Z",
                "text": "Primera línea segunda línea",
            },
            {"id": "301", "author_id": "1", "created_at": "2024-05-03T09:00:00.000Z", "text": "Normal"},
        ]
    }
    write_raw(tmp_path, "pedro_sanchez", "2024-05", [page])
    accounts = [{"actor_id": "pedro_sanchez", "x_handle": "sanchezcastejon", "x_user_id": "1"}]
    rows, summary = build.build_rows(tmp_path, accounts, {})
    assert summary["posts"] == 2
    assert " " in rows[0]["text"]


def test_missing_referenced_text_is_counted(tmp_path):
    page = {
        "data": [
            {
                "id": "200",
                "author_id": "2",
                "created_at": "2024-05-02T09:00:00.000Z",
                "text": "Respuesta.",
                "referenced_tweets": [{"type": "replied_to", "id": "999"}],
            }
        ]
    }
    write_raw(tmp_path, "santiago_abascal", "2024-05", [page])
    accounts = [{"actor_id": "santiago_abascal", "x_handle": "Santi_ABASCAL", "x_user_id": "2"}]
    rows, summary = build.build_rows(tmp_path, accounts, {})
    assert summary["references_without_text"] == 1
    assert rows[0]["post_type"] == "reply"
    assert rows[0]["referenced_post_text"] == ""


def test_stimulus_rows_and_csv_columns(tmp_path):
    rows = [
        {
            "message_id": "x_100",
            "post_id": "100",
            "bundle_id": "",
            "text": "Texto",
            "date": "2024-05-02",
            "author_role": "",
            "source_type": "x_post",
        }
    ]
    stimuli = build.stimulus_rows(rows)
    assert list(stimuli[0]) == build.STIMULUS_FIELDS
    assert stimuli[0]["tweet_id"] == "100"
    assert stimuli[0]["text_anon"] == "Texto"
    assert stimuli[0]["keep_for_prompt"] == "False"

    out = tmp_path / "x_stimuli.csv"
    build.write_csv(out, build.STIMULUS_FIELDS, stimuli)
    with out.open(encoding="utf-8") as handle:
        read_back = list(csv.DictReader(handle))
    assert read_back[0]["message_id"] == "x_100"
    assert list(read_back[0]) == build.STIMULUS_FIELDS
