"""What a person chose for their session: which model, and how it answers.

Both are stored on the session row, so a reload keeps them and a second
browser does not inherit them.
"""

from typing import Any

from quart import Blueprint, current_app, g, request

from cra.assistant.llm import params as params_
from cra.assistant.llm.selection import available, resolve

bp = Blueprint("preferences", __name__)


def _ctx():
    return current_app.extensions["cra"]


async def selection(ctx: Any, session: Any) -> dict[str, Any]:
    """The model this session would use, and the routing that goes with it."""
    data = session.data if session else {}
    choice = await resolve(
        ctx.settings,
        ctx.catalogue,
        ctx.http,
        wanted=str(data.get("model", "")),
        offered_models=ctx.policy["llm_models"],
    )
    values = params_.effective(ctx.settings, data.get("params"))
    route = values["provider_sort"]
    return {
        "model": choice.model,
        "auto_model": choice.automatic,
        "sort": route,
        "route_label": dict(
            zip(params_.ROUTE_VALUES, (r["label"] for r in params_.ROUTES), strict=True)
        ).get(route, route),
        "params": values,
    }


@bp.post("/api/session/model")
async def set_model() -> Any:
    ctx = _ctx()
    body = await request.get_json(silent=True) or {}
    wanted = str(body.get("model", "")).strip()
    choices = await available(
        ctx.settings, ctx.policy["llm_models"], ctx.catalogue, ctx.http
    )
    if wanted and wanted not in choices:
        return {"error": f"Unknown model: {wanted}"}, 400

    data = dict(g.session.data)
    data["model"] = wanted
    if "sort" in body:
        try:
            params = dict(data.get("params") or {})
            params["provider_sort"] = params_.normalise("provider_sort", body["sort"])
            data["params"] = params
        except params_.ParamError as exc:
            return {"error": str(exc)}, 400
    g.session.data = data
    await ctx.sessions.save(g.session)
    return await selection(ctx, g.session)


@bp.post("/api/session/params")
async def set_params() -> Any:
    ctx = _ctx()
    body = await request.get_json(silent=True) or {}
    wanted = body.get("params")
    if not isinstance(wanted, dict):
        return {"error": "send the fields as an object under 'params'"}, 400

    stored = dict(g.session.data.get("params") or {})
    defaults = params_.defaults(ctx.settings)
    for key, value in wanted.items():
        if key not in params_.BY_KEY:
            return {"error": f"Unknown parameter: {key}"}, 400
        try:
            normalised = params_.normalise(key, value)
        except params_.ParamError as exc:
            return {"error": str(exc)}, 400
        # only what differs from the deployment's own values is worth keeping
        if normalised == defaults[key]:
            stored.pop(key, None)
        else:
            stored[key] = normalised

    data = dict(g.session.data)
    data["params"] = stored
    g.session.data = data
    await ctx.sessions.save(g.session)
    return await selection(ctx, g.session)


@bp.delete("/api/session/params")
async def clear_params() -> Any:
    ctx = _ctx()
    data = dict(g.session.data)
    data.pop("params", None)
    g.session.data = data
    await ctx.sessions.save(g.session)
    return await selection(ctx, g.session)
