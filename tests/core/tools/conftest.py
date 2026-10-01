"""Every tool module, registered over the small library the builder writes."""

import httpx
import pytest
from conftest import make_settings
from fakes import FakeEncoder
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import ToolContext, load


@pytest.fixture
def indexes(tmp_path):
    return Indexes.build(Library.load(write_library(tmp_path / "lib")), FakeEncoder())


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def registry(settings, indexes):
    return load(settings, indexes)


@pytest.fixture
def ctx(indexes, settings):
    return ToolContext(indexes=indexes, settings=settings, http=httpx.AsyncClient())
