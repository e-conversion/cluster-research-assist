"""One HTTP client per test; respx intercepts what the connectors send."""

import httpx
import pytest


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as made:
        yield made
