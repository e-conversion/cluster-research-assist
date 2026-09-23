"""The external MCP servers a user may attach their own account to.

Each source is described once: how it is labelled, how its token travels, and
what the registration form has to ask for. Whether a source exists at all is a
deployment decision -- one without a configured URL is never offered, so an
instance that has neither runs with no connector UI at all.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from cra.config.settings import Settings


@dataclass(frozen=True)
class Source:
    kind: str
    label: str
    key_label: str
    url: str
    register_url: str
    default_base_url: str
    profiles: tuple[tuple[str, str], ...] = ()

    @property
    def prefix(self) -> str:
        """What the model sees in front of every tool this source offers."""
        return f"{self.kind}_"

    def authorised(self, token: str) -> str:
        """The endpoint with the token attached.

        elabmcp-proxy reads the token from the query string only (a header is
        ignored); datatagger-proxy takes either. The query works for both, so
        there is one code path rather than one per source.
        """
        separator = "&" if "?" in self.url else "?"
        return f"{self.url}{separator}token={quote(token, safe='')}"

    def public(self) -> dict[str, Any]:
        """What the browser may know. Internal URLs stay on this side: the
        register endpoint is often only reachable from within the stack."""
        return {
            "label": self.label,
            "key_label": self.key_label,
            "base_url": self.default_base_url,
            "profiles": [{"value": v, "label": label} for v, label in self.profiles],
        }


# Registration profiles are the proxy's own vocabulary; the first is the default.
ELAB_PROFILES = (
    ("h", "Hybrid (recommended)"),
    ("r", "Read-only"),
    ("f", "Full"),
)


def configured(settings: Settings) -> dict[str, Source]:
    """The sources this deployment can actually reach, in display order."""
    found: dict[str, Source] = {}
    if settings.mcp_elab_url:
        found["elab"] = Source(
            kind="elab",
            label="eLabFTW",
            key_label="eLabFTW API key",
            url=settings.mcp_elab_url,
            register_url=settings.mcp_elab_register_url,
            default_base_url=settings.mcp_elab_base_url,
            profiles=ELAB_PROFILES,
        )
    if settings.mcp_datatagger_url:
        found["dt"] = Source(
            kind="dt",
            label="DataTagger",
            key_label="DataTagger API token",
            url=settings.mcp_datatagger_url,
            register_url=settings.mcp_datatagger_register_url,
            default_base_url=settings.mcp_datatagger_base_url,
        )
    return found
