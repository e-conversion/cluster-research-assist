"""Searching NOMAD, the public materials-data repository."""

from typing import Annotated, Any

from pydantic import Field

from cra.config.settings import Settings
from cra.core.connectors import nomad_client
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext, ToolError, tool
from cra.core.tools.tiers import Tier


@tool(tier=Tier.PUBLIC)
async def search_nomad(
    ctx: ToolContext,
    elements: Annotated[
        str,
        Field(
            description="Comma-separated element symbols that must all occur, e.g. 'Ti,O'."
        ),
    ] = "",
    formula: Annotated[
        str, Field(description="Reduced formula in alphabetical order, e.g. 'O3SrTi'.")
    ] = "",
    author: Annotated[
        str, Field(description="Name of whoever deposited the data.")
    ] = "",
    text: Annotated[
        str, Field(description="Free-text search across the entries.")
    ] = "",
    limit: Annotated[
        int, Field(description="How many entries to return.", ge=1, le=50)
    ] = 5,
) -> dict[str, Any]:
    """Search NOMAD for computed or measured materials data. This is data
    outside the cluster, not the data behind a cluster paper, and the entries
    returned are a sample of total_matches, which is the number that matters."""
    if ctx.http is None:
        raise ToolError("No HTTP client is available for external requests.")
    return await nomad_client.search(
        ctx.http,
        ctx.settings.nomad_base_url,
        ctx.settings.nomad_gui_url,
        elements=elements,
        formula=formula,
        author=author,
        text=text,
        limit=limit,
    )


def setup(registry: Registry, settings: Settings, indexes: Indexes) -> None:
    if not settings.nomad_base_url:
        return
    registry.register(search_nomad)
