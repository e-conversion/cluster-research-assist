"""Stats for nerds: what the service is built from and how it is used.

Usage comes from the answers stored in the history, which already carry the
model, the time taken and every tool call; nothing is counted twice or kept
anywhere else. Only totals leave this route, never a question or an answer.
"""

from collections import Counter, defaultdict
from typing import Any

from quart import Blueprint, current_app

from cra import __version__
from cra.core.library import manifest as manifest_

bp = Blueprint("stats", __name__)

# the most-called tools are enough to see what the assistant leans on
TOP_TOOLS = 12
# (label, available key, count key or None)
STAGES = (
    ("papers", "papers", "papers"),
    ("abstracts", "abstracts", "abstracts"),
    ("full texts", "fulltexts", "fulltexts"),
    ("group profiles", "pis", "pis"),
    ("embeddings", "embeddings", "embeddings"),
    ("collaboration graph (edges)", "graph", "graph_edges"),
    ("publication map", "map", "map_points"),
    ("proposal", "proposal", None),
)


def _ctx():
    return current_app.extensions["cra"]


def usage(answers: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    """Totals over stored answers: ``(meta, user_id)`` pairs."""
    tool_calls: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    tool_ms: defaultdict[str, int] = defaultdict(int)
    models: Counter[str] = Counter()
    elapsed = 0.0
    errors = 0
    for meta, _ in answers:
        models[str(meta.get("model") or "unknown")] += 1
        elapsed += float(meta.get("elapsed") or 0)
        errors += bool(meta.get("error"))
        for call in meta.get("tools") or []:
            name = str(call.get("name", "?"))
            tool_calls[name] += 1
            tool_errors[name] += not call.get("ok", True)
            tool_ms[name] += int(call.get("ms") or 0)
    turns = len(answers)
    return {
        "totals": {
            "turns": turns,
            "people": len({user for _, user in answers}),
            "error_turns": errors,
            "avg_latency_ms": round(elapsed * 1000 / turns) if turns else 0,
        },
        "tools": [
            {
                "name": name,
                "calls": calls,
                "errors": tool_errors[name],
                "avg_ms": round(tool_ms[name] / calls),
            }
            for name, calls in tool_calls.most_common(TOP_TOOLS)
        ],
        "models": [{"name": n, "turns": t} for n, t in models.most_common()],
    }


def _pipeline(library: Any) -> list[dict[str, Any]]:
    if library is None:
        return []
    available, counts = library.available, library.counts
    return [
        {
            "stage": label,
            "available": available.get(flag, False),
            "entries": counts.get(count) if count else None,
        }
        for label, flag, count in STAGES
    ]


@bp.get("/api/stats")
async def stats() -> dict[str, Any]:
    ctx = _ctx()
    summary = usage(await ctx.repo.answer_meta())
    manifest = (manifest_.read(ctx.library.path) if ctx.library else None) or {}
    return {
        "build": {
            "version": __version__,
            "library_built_at": manifest.get("built_at"),
            "embedding_model": manifest.get("embedding_model"),
        },
        "provider": ctx.settings.llm_provider,
        "default_model": ctx.policy["llm_model"] or "chosen automatically",
        "pipeline": _pipeline(ctx.library),
        "usage": {
            **summary["totals"],
            "conversations": await ctx.repo.count_conversations(),
            "feedback": await ctx.repo.count_feedback(),
        },
        "tools": summary["tools"],
        "models": summary["models"],
    }
