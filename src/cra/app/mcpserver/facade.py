"""The registry, offered as an MCP server.

Tools are not re-declared here: the same registry the chat uses answers
``tools/list`` and ``tools/call``, asked at the public tier. That is the whole
point of the tier living in the registry -- an outward caller is refused by the
same code that decides what the model may call, not by a second list that can
drift, and never by a prompt.
"""

import json
import logging
import time
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from cra import __version__
from cra.app.mcpserver import audit
from cra.core.tools.tiers import Tier

log = logging.getLogger(__name__)

NAME = "cluster-research-assistant"
TIER = Tier.PUBLIC
INSTRUCTIONS = (
    "Search and read the cluster's published research: papers, abstracts, "
    "principal investigators, their collaborations and full-text search. "
    "Full texts and internal documents are not served here."
)


def build(ctx: Any) -> Server:
    """A lowlevel MCP server backed by ``ctx.registry``."""

    # the handler signature is the SDK's; neither argument says anything a
    # listing at one fixed tier needs
    async def on_list_tools(
        request: Any,  # noqa: ARG001
        params: Any = None,  # noqa: ARG001
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[_tool(spec) for spec in ctx.registry.specs(TIER)]
        )

    async def on_call_tool(request: Any, params: Any) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        started = time.perf_counter()
        result = await ctx.registry.call(params.name, arguments, ctx.tool_context(TIER))
        failed = isinstance(result, dict) and "error" in result
        audit.call(
            caller=caller(request),
            tool=params.name,
            ok=not failed,
            ms=round((time.perf_counter() - started) * 1000),
            arguments=len(arguments),
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_text(result))],
            is_error=failed,
        )

    return Server(
        NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def caller(request: Any) -> str:
    """Who asked, for the audit line: the token's subject, else the address."""
    http = getattr(request, "request", None)
    subject = getattr(http, "headers", {}).get("x-cra-subject", "") if http else ""
    if subject:
        return subject
    client = getattr(http, "client", None)
    return getattr(client, "host", "") or "anonymous"


def _tool(spec: Any) -> types.Tool:
    return types.Tool(
        name=spec.name, description=spec.description, input_schema=spec.parameters
    )


def _text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)
