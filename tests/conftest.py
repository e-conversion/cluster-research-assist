import os

import pytest


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch, tmp_path):
    """No stray CRA_ variables or .env from the developer's shell reach a test."""
    for key in list(os.environ):
        if key.startswith("CRA_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
