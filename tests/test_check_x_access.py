import importlib.util
import json
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


check = load_module("check_x_access", ROOT / "src" / "check_x_access.py")


def test_classify_success_and_credentials():
    assert check.classify_response(200, {"data": []}) == "ok"
    assert check.classify_response(401, {"title": "Unauthorized"}) == "invalid-credentials"


def test_classify_enrolment_and_billing_errors():
    not_enrolled = {
        "title": "Client Forbidden",
        "reason": "client-not-enrolled",
        "type": "https://api.x.com/2/problems/client-forbidden",
    }
    assert check.classify_response(403, not_enrolled) == "not-enrolled"
    assert check.classify_response(402, {"title": "Payment Required"}) == "no-credits"
    assert check.classify_response(403, {"detail": "Insufficient credits"}) == "no-credits"
    assert check.classify_response(403, {"title": "Forbidden"}) == "forbidden"


def test_classify_limits():
    capped = {"title": "UsageCapExceeded", "type": "https://api.x.com/2/problems/usage-capped"}
    assert check.classify_response(429, capped) == "usage-capped"
    assert check.classify_response(429, {"title": "Too Many Requests"}) == "rate-limited"
    assert check.classify_response(500, {}) == "unexpected"


def test_load_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\n\nX_BEARER_TOKEN='abc=def'\nX_API_KEY=key\nBROKEN\n", encoding="utf-8")
    assert check.load_env_file(env) == {"X_BEARER_TOKEN": "abc=def", "X_API_KEY": "key"}
    assert check.load_env_file(tmp_path / "missing.env") == {}


def test_redact_hides_credentials():
    text = "client_id=abcdefgh12345 detail"
    assert check.redact(text, ["abcdefgh12345", "short"]) == "client_id=<redacted> detail"


def test_write_user_ids(tmp_path):
    accounts = tmp_path / "x_accounts.json"
    accounts.write_text(
        json.dumps(
            [
                {"actor_id": "pedro_sanchez", "x_handle": "sanchezcastejon", "x_user_id": None},
                {"actor_id": "santiago_abascal", "x_handle": "Santi_ABASCAL", "x_user_id": None},
            ]
        ),
        encoding="utf-8",
    )
    users = [{"username": "SanchezCastejon", "id": "123"}]
    assert check.write_user_ids(accounts, users) == 1
    stored = json.loads(accounts.read_text(encoding="utf-8"))
    assert stored[0]["x_user_id"] == "123"
    assert stored[1]["x_user_id"] is None
