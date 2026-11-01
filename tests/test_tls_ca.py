from pathlib import Path
import certifi
import pytest
from backend.app import tls_ca


@pytest.fixture
def roots(monkeypatch,tmp_path):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE",raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE",raising=False)
    monkeypatch.setattr(tls_ca,"short_path",lambda p:None)
    monkeypatch.setattr(tls_ca.tempfile,"gettempdir",lambda:str(tmp_path))
    return tmp_path


def test_unicode_ca_path_is_loaded_from_ascii_copy_with_verification_preserved(roots,monkeypatch):
    data=Path(certifi.where()).read_bytes()
    source=roots/"Новая папка"/"certifi"/"cacert.pem"
    source.parent.mkdir(parents=True);source.write_bytes(data)
    monkeypatch.setattr(tls_ca.certifi,"where",lambda:str(source))
    result=tls_ca.ca_bundle_path()
    assert result.isascii() and result!=str(source)
    assert Path(result).read_bytes()==data
    assert tls_ca.ca_bundle_path()==result


def test_custom_ca_bundle_is_respected(roots,monkeypatch):
    source=roots/"custom.pem";source.write_bytes(Path(certifi.where()).read_bytes())
    monkeypatch.setenv("REQUESTS_CA_BUNDLE",str(source))
    assert tls_ca.ca_bundle_path()==str(source.resolve())


def test_missing_or_broken_bundle_fails_without_disabling_verification(roots,monkeypatch):
    source=roots/"missing.pem";monkeypatch.setenv("CURL_CA_BUNDLE",str(source))
    with pytest.raises(RuntimeError,match="Не найден"):tls_ca.ca_bundle_path()
    source.write_text("invalid")
    with pytest.raises(RuntimeError,match="повреждён"):tls_ca.ca_bundle_path()
