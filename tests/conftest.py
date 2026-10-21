from pathlib import Path
import pytest
from backend.app.config import Config
from backend.app.db import Base,Database

@pytest.fixture
def db(tmp_path):
    config=Config(tmp_path)
    result=Database(config)
    Base.metadata.create_all(result.engine)
    result.initialize()
    yield result
    result.engine.dispose()

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args,**kwargs):raise AssertionError("Network access forbidden in unit tests")
    import requests
    from curl_cffi import requests as curl
    monkeypatch.setattr(requests.Session,"request",blocked)
    monkeypatch.setattr(curl.Session,"request",blocked)
