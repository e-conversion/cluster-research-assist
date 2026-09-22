"""What this deployment actually holds, so the model can say so."""

from typing import Any

from cra.config.settings import Settings
from cra.core.library import manifest as manifest_
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext, tool
from cra.core.tools.tiers import Tier


@tool(tier=Tier.PUBLIC)
def library_status(ctx: ToolContext) -> dict[str, Any]:
    """What the library contains and which capabilities are available. Use it
    when asked what you can see, or before claiming something is missing."""
    indexes = ctx.indexes
    library = indexes.library
    manifest = manifest_.read(library.path) or {}
    return {
        "cluster": ctx.settings.cluster_display_name,
        "counts": library.counts,
        "available": library.available,
        "semantic_search": indexes.semantic_ready,
        "embedding_model": library.embeddings.model if library.embeddings else "",
        "built_at": manifest.get("built_at", ""),
        "tier": str(ctx.tier),
    }


def setup(registry: Registry, settings: Settings, indexes: Indexes) -> None:
    registry.register(library_status)
