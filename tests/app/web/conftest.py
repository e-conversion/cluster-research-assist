"""An app whose one external source is the toy MCP server."""

import pytest
from conftest import make_settings
from fakes import ELAB, Toy, make_host

from cra.app.web.factory import create_app


@pytest.fixture
def toy():
    return Toy()


@pytest.fixture
async def connected_app(tmp_path, toy):
    """An app whose one source is the toy server."""
    settings = make_settings(
        tmp_path,
        mcp_elab_url=ELAB.url,
        mcp_elab_register_url=ELAB.register_url,
        mcp_elab_base_url=ELAB.default_base_url,
    )
    app = create_app(settings)
    async with app.test_app():
        app.extensions["cra"].remote = make_host(toy)
        yield app
        await app.extensions["cra"].remote.aclose()
