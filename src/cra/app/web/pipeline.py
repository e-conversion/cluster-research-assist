"""The pipeline map: what the assistant is built from, as this deployment runs it.

The map describes the package, so the package owns it: ``pipeline.json`` beside
this module, checked by a test against the registered tools and the library's
files. Serving it leaves out the tools this deployment did not register and the
parts its configuration switches off, and puts the served library's counts in.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cra.config.settings import Settings

PATH = Path(__file__).resolve().parent / "pipeline.json"
TOOLS_STAGE = "tools"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Stage(_Strict):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)


class Node(_Strict):
    id: str = Field(min_length=1)
    stage: str
    name: str = Field(min_length=1)
    summary: str = ""
    detail: str = ""
    group: str | None = None
    # a key of Library.counts, shown after the summary
    count: str | None = None
    unit: str = "entries"
    # a part the configuration can switch off; see CONDITIONS
    when: str | None = None


class Pipeline(_Strict):
    intro: str = ""
    stages: list[Stage] = Field(min_length=1)
    nodes: list[Node]
    edges: list[tuple[str, str]] = []

    @model_validator(mode="after")
    def _references_resolve(self) -> "Pipeline":
        stages = {s.key for s in self.stages}
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("pipeline node ids must be unique")
        if strays := sorted({n.stage for n in self.nodes} - stages):
            raise ValueError(f"pipeline nodes name unknown stages: {strays}")
        if strays := sorted({end for edge in self.edges for end in edge} - set(ids)):
            raise ValueError(f"pipeline edges name unknown nodes: {strays}")
        if strays := sorted({n.when for n in self.nodes if n.when} - set(CONDITIONS)):
            raise ValueError(f"pipeline nodes name unknown conditions: {strays}")
        return self


CONDITIONS = {
    "doi_lookup": lambda s: bool(s.crossref_base_url),
    "elab": lambda s: bool(s.mcp_elab_url),
    "datatagger": lambda s: bool(s.mcp_datatagger_url),
    "nomad_remote": lambda s: bool(s.mcp_nomad_url),
    "remote": lambda s: bool(s.mcp_elab_url or s.mcp_datatagger_url or s.mcp_nomad_url),
    "mcp_server": lambda s: s.mcp_server_enabled,
}


@cache
def load() -> Pipeline:
    return Pipeline.model_validate(json.loads(PATH.read_text(encoding="utf-8")))


def tool_ids(pipeline: Pipeline) -> set[str]:
    """The nodes that stand for one of the package's own tools; the remote
    ones (``elab_*`` and the like) are not registered here."""
    return {n.id for n in pipeline.nodes if n.stage == TOOLS_STAGE and not n.when}


def served(
    settings: Settings,
    tools: set[str],
    counts: dict[str, int] | None,
    pipeline: Pipeline | None = None,
) -> dict[str, Any]:
    """The map as the page draws it, for a deployment offering ``tools``."""
    pipeline = pipeline or load()
    own = tool_ids(pipeline)
    words = {
        "{website}": settings.cluster_website or "the cluster's website",
        "{provider}": settings.llm_provider,
    }

    def fill(text: str) -> str:
        for placeholder, value in words.items():
            text = text.replace(placeholder, value)
        return text

    nodes = []
    for node in pipeline.nodes:
        if node.id in own and node.id not in tools:
            continue
        if node.when and not CONDITIONS[node.when](settings):
            continue
        summary = fill(node.summary)
        if node.count and counts is not None:
            entries = f"{counts.get(node.count, 0):,} {node.unit}"
            summary = f"{summary} · {entries}" if summary else entries
        nodes.append(
            {
                "id": node.id,
                "stage": node.stage,
                "group": node.group,
                "name": node.name,
                "summary": summary,
                "detail": fill(node.detail),
            }
        )
    kept = {n["id"] for n in nodes}
    return {
        "intro": pipeline.intro,
        "stages": [s.model_dump() for s in pipeline.stages],
        "nodes": nodes,
        "edges": [[a, b] for a, b in pipeline.edges if a in kept and b in kept],
    }
