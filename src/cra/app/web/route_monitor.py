"""The admin console's Server info and Logs tabs.

Server info is polled every few seconds by every open console, so each
response is cheap: the probes read a few small files, the database answers
counts, and the model credit is asked for at most once a minute.
"""

import asyncio
import logging
from collections import Counter
from datetime import timedelta
from typing import Any

import httpx
from quart import Blueprint, Response, current_app, request

from cra import __version__
from cra.app import logreader
from cra.app.history.repository import utcnow
from cra.app.web.access import requires_admin

log = logging.getLogger(__name__)

bp = Blueprint("monitor", __name__)

CREDIT_TIMEOUT_S = 10.0
ACTIVE_WINDOWS = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "24h": timedelta(days=1),
}
USAGE_HOURS = 24


def _ctx():
    return current_app.extensions["cra"]


async def _credit(ctx: Any) -> dict[str, Any] | None:
    """What the OpenRouter key may still spend; None for another provider or
    when OpenRouter does not answer (the page then says it is unknown)."""
    if "openrouter.ai" not in ctx.settings.llm_base_url:
        return None
    fresh, cached = ctx.monitor.cached_credit()
    if fresh:
        return cached
    key = ctx.settings.llm_api_key.get_secret_value()
    credit: dict[str, Any] | None = None
    try:
        response = await ctx.http.get(
            ctx.settings.llm_base_url.rstrip("/") + "/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=CREDIT_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        credit = {
            k: data.get(k) for k in ("limit", "limit_remaining", "usage", "usage_daily")
        }
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.warning(
            "the model credit is unavailable", extra={"fields": {"error": str(exc)}}
        )
    ctx.monitor.remember_credit(credit)
    return credit


def usage(answers: list[tuple[Any, dict[str, Any]]], now: Any) -> dict[str, Any]:
    """Answers, failures and tokens per hour over the last day, and totals."""
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=USAGE_HOURS - 1
    )
    hours = [
        {
            "hour": (start + timedelta(hours=n)).isoformat() + "Z",
            "answers": 0,
            "failed": 0,
            "prompt": 0,
            "completion": 0,
        }
        for n in range(USAGE_HOURS)
    ]
    failures: Counter[str] = Counter()
    for created, meta in answers:
        slot = int((created - start).total_seconds() // 3600)
        if not 0 <= slot < USAGE_HOURS:
            continue
        bucket = hours[slot]
        bucket["answers"] += 1
        error = meta.get("error")
        # stopping, or answering after the tool budget, is not a failure
        if error and error not in ("cancelled", "tool_call_limit_reached",
                                   "tool_calls_fruitless"):  # fmt: skip
            bucket["failed"] += 1
            failures[str(error)] += 1
        tokens = meta.get("usage") or {}
        bucket["prompt"] += int(tokens.get("prompt") or 0)
        bucket["completion"] += int(tokens.get("completion") or 0)
    totals = {
        key: sum(h[key] for h in hours)
        for key in ("answers", "failed", "prompt", "completion")
    }
    counted = [m for _, m in answers if (m.get("usage") or {}).get("total")]
    totals["avg_tokens_per_answer"] = (
        round(sum(int(m["usage"]["total"]) for m in counted) / len(counted))
        if counted
        else None
    )
    return {"hours": hours, "totals": totals, "failures": dict(failures.most_common())}


def _pool(ctx: Any) -> dict[str, int] | None:
    pool = ctx.engine.pool
    try:
        # overflow() counts down from zero while the pool is not yet full
        return {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": max(0, pool.overflow()),
        }
    except AttributeError:
        return None


@bp.get("/api/admin/server")
@requires_admin
async def server() -> dict[str, Any]:
    ctx = _ctx()
    now = utcnow()
    paths = {"logs": ctx.settings.log_dir, "library": ctx.settings.library_path}
    system, database, counts, answers, credit = await asyncio.gather(
        asyncio.to_thread(ctx.monitor.system, paths),
        ctx.repo.database_info(),
        ctx.repo.table_counts(),
        ctx.repo.answers_since(now - timedelta(hours=USAGE_HOURS)),
        _credit(ctx),
    )
    active = {
        name: await ctx.repo.active_users(now - window)
        for name, window in ACTIVE_WINDOWS.items()
    }
    return {
        "time": now.isoformat() + "Z",
        "system": system,
        "app": {
            "version": __version__,
            "turns_running": ctx.turns.running(),
            "background_tasks": len(ctx.background),
            "source_connections": ctx.remote.open_connections(),
            "active_users": active,
        },
        "database": {**database, "pool": _pool(ctx), "rows": counts},
        "llm": {
            "provider": ctx.settings.llm_provider,
            "default_model": ctx.policy["llm_model"] or "chosen automatically",
            "credit": credit,
        },
        "usage": usage(answers, now),
    }


def _query() -> logreader.Query:
    args = request.args
    wanted = {
        level.strip().upper()
        for level in args.get("levels", "").split(",")
        if level.strip()
    }
    try:
        skip = max(0, int(args.get("skip", 0)))
        limit = int(args.get("limit", 50))
    except ValueError:
        skip, limit = 0, 50
    return logreader.Query(
        levels=frozenset(wanted & set(logreader.LEVELS)) or frozenset(logreader.LEVELS),
        text=args.get("q", "").strip()[:200],
        before=args.get("before") or None,
        skip=skip,
        after=args.get("after") or None,
        limit=limit,
    )


@bp.get("/api/admin/logs")
@requires_admin
async def logs() -> dict[str, Any]:
    ctx = _ctx()
    page = await asyncio.to_thread(logreader.read, ctx.settings.log_dir, _query())
    return {**page, "levels": list(logreader.LEVELS)}


@bp.get("/api/admin/logs/download")
@requires_admin
async def download_logs() -> Response:
    """Every log file, oldest first, as one file."""
    ctx = _ctx()

    def concatenated() -> bytes:
        files = reversed(logreader.log_files(ctx.settings.log_dir))
        return b"".join(path.read_bytes() for path in files)

    stamp = utcnow().strftime("%Y%m%dT%H%MZ")
    return Response(
        await asyncio.to_thread(concatenated),
        content_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="cra-{stamp}.log"'},
    )
