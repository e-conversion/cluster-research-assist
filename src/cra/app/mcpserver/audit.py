"""One line per outward call.

The endpoint is the part of the service that answers strangers, so what it was
asked and what it answered is worth keeping whether or not anything went wrong.
Arguments are counted, not written down: they are someone else's query.
"""

import logging

log = logging.getLogger("cra.mcp.audit")


def call(*, caller: str, tool: str, ok: bool, ms: int, arguments: int) -> None:
    log.info(
        "mcp call",
        extra={
            "fields": {
                "caller": caller,
                "tool": tool,
                "ok": ok,
                "duration_ms": ms,
                "arguments": arguments,
            }
        },
    )


def refused(*, caller: str, reason: str) -> None:
    log.warning("mcp refused", extra={"fields": {"caller": caller, "reason": reason}})
