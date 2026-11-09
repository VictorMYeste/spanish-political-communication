import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

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


micro = load_module("collect_cis_microdata", ROOT / "src" / "collect_cis_microdata.py")

PAGE = """<html><h1>Estudio nº 3571. Barómetro de julio 2026</h1>
<script type="application/ld+json">{"distribution":[
{"contentUrl":"https://www.cis.es/documents/20117/1/cues3571.pdf","@type":"DataDownload","name":"Cuestionario"},
{"name":"Fichero datos","@type":"DataDownload","contentUrl":"https://www.cis.es/documents/20117/1/MD3571.zip"},
{"contentUrl":"https://www.cis.es/documents/20117/1/es3571mar.pdf","@type":"DataDownload","name":"Resultados PDF"}
]}</script></html>"""


def zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_data_file_url_is_read_from_the_structured_data():
    assert micro.parse_data_file_url(PAGE) == "https://www.cis.es/documents/20117/1/MD3571.zip"
    relative = '{"contentUrl":"/documents/20117/2/MD3359.zip","@type":"DataDownload","name":"Otro nombre"}'
    assert micro.parse_data_file_url(relative) == "https://www.cis.es/documents/20117/2/MD3359.zip"
    assert micro.parse_data_file_url(PAGE.replace("Fichero datos", "x").replace("MD3571.zip", "x.pdf")) is None


def test_only_the_study_sav_file_is_extracted(tmp_path):
    archive = tmp_path / "MD3571.zip"
    archive.write_bytes(zip_bytes({"ES3571": b"ascii", "3571.sav": b"spss", "codigo3571.pdf": b"pdf"}))
    micro.extract_sav(archive, "3571", tmp_path / "3571.sav")
    assert (tmp_path / "3571.sav").read_bytes() == b"spss"

    archive.write_bytes(zip_bytes({"a.sav": b"1", "b.sav": b"2"}))
    with pytest.raises(ValueError, match="expected one .sav"):
        micro.extract_sav(archive, "3571", tmp_path / "out.sav")
    archive.write_bytes(zip_bytes({"ES3571": b"ascii"}))
    with pytest.raises(ValueError, match="none"):
        micro.extract_sav(archive, "3571", tmp_path / "out.sav")


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        status, body = self.responses.get(url, (404, b""))
        text = body.decode() if isinstance(body, bytes) and not body.startswith(b"PK") else ""
        return SimpleNamespace(status_code=status, text=text, content=body)


def test_collect_downloads_each_barometer_once_and_reports_missing_months(tmp_path):
    config = {"window": {"first_month": "2026-07", "last_month": "2026-08"},
              "source": {"study_page": "https://www.cis.es/es/estudios/barometro-de-{month}-{year}"}}
    responses = {
        "https://www.cis.es/es/estudios/barometro-de-julio-2026": (200, PAGE.encode()),
        "https://www.cis.es/documents/20117/1/MD3571.zip": (200, zip_bytes({"3571.sav": b"spss"})),
        "https://www.cis.es/es/": (200, b"<html></html>"),
    }
    session = FakeSession(responses)
    rows, missing = micro.collect(config, tmp_path, sleep=0, session=session)
    assert [row["study_id"] for row in rows] == ["3571"]
    assert (tmp_path / "3571.sav").read_bytes() == b"spss"
    assert missing == ["2026-08: no barometer page (404)"]

    session = FakeSession(responses)
    rows_again, _ = micro.collect(config, tmp_path, sleep=0, session=session)
    assert "https://www.cis.es/documents/20117/1/MD3571.zip" not in session.requested
    assert rows_again[0]["zip_sha256"] == rows[0]["zip_sha256"]
