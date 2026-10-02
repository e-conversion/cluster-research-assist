"""Which routes answer whom."""

import re

import pytest
from conftest import sign_in

# Every route, classified on purpose. A new route fails this test until it is
# listed, which is what keeps "public by default" from becoming an oversight.
PUBLIC_ROUTES = {
    "/",
    "/brand/<name>",
    "/api/health",
    "/api/config",
    "/auth/login",
    "/auth/callback",
    "/auth/logout",
    "/auth/password",
    "/auth/set-password",
    "/auth/password-link",
    # these two also need the verified identity of a sign-in without an account
    "/api/access-requests",
    "/api/access-requests/me",
    # an MCP client's sign-in starts here, before its person has signed in
    "/oauth/consent",
}


USER_ROUTES = {
    "/api/session",
    "/api/feedback",
    "/api/session/model",
    "/api/session/params",
    "/api/chat",
    "/api/chat/stop",
    "/api/chat/reset",
    "/api/session/connect/<kind>",
    "/api/session/register/<kind>",
    "/api/conversations",
    "/api/conversations/<conversation_id>",
    "/api/conversations/<conversation_id>/open",
    "/api/publication-map",
    "/api/publication-map/lookup",
    "/api/collaboration-graph",
    "/api/tokens",
    "/api/tokens/<token_id>",
    "/api/me",
    "/api/me/export",
    "/api/me/password",
    "/api/stats",
    "/oauth/consent/answer",
}


ADMIN_ROUTES = {
    "/admin",
    "/api/admin/people",
    "/api/admin/users/<user_id>",
    "/api/admin/users/<user_id>/password-reset",
    "/api/admin/local-users",
    "/api/admin/access-requests",
    "/api/admin/access-requests/<request_id>",
    "/api/admin/access-requests/<request_id>/approve",
    "/api/admin/access-requests/<request_id>/reject",
    "/api/admin/emails",
    "/api/admin/emails/<path:email>",
    "/api/admin/feedback",
    "/api/admin/feedback/export",
    "/api/admin/server",
    "/api/admin/logs",
    "/api/admin/logs/download",
    "/api/admin/feedback/<int:feedback_id>",
    "/api/admin/policy",
    "/api/admin/policy/<key>",
    "/api/admin/library",
    "/api/admin/library/<version>/activate",
    "/api/admin/tokens",
    "/api/admin/tokens/<token_id>",
}


def test_every_route_is_classified(app):
    routes = {r.rule for r in app.url_map.iter_rules() if r.endpoint != "static"}
    assert routes == PUBLIC_ROUTES | USER_ROUTES | ADMIN_ROUTES


async def test_anonymous_visitors_reach_every_public_route(app, client):
    for template in sorted(PUBLIC_ROUTES):
        rule, method = as_request(app, template)
        response = await client.open(rule, method=method, json={})
        # the route may still say no; the guard in front of it must not
        body = await response.get_json(silent=True) or {}
        assert body.get("error") != "login_required", template


def as_request(app, template: str) -> tuple[str, str]:
    """A callable path and a supported method for a route template."""
    rule = next(r for r in app.url_map.iter_rules() if r.rule == template)
    method = next(m for m in ("GET", "POST", "PUT", "DELETE") if m in rule.methods)
    # a placeholder the route's converter accepts, or it 404s before the guard
    path = re.sub(r"<int:[^>]+>", "1", template)
    return re.sub(r"<[^>]+>", "placeholder", path), method


@pytest.mark.parametrize("template", sorted(ADMIN_ROUTES))
async def test_admin_routes_refuse_anonymous_and_ordinary_users(app, client, template):
    rule, method = as_request(app, template)
    anonymous = await client.open(rule, method=method)
    assert anonymous.status_code == 401
    assert (await anonymous.get_json())["login_url"] == "/"

    await sign_in(app, client)
    signed_in = await client.open(rule, method=method)
    assert signed_in.status_code == 403
    assert (await signed_in.get_json())["error"] == "admin_required"


async def test_a_browser_asking_for_a_page_is_sent_to_sign_in(client):
    response = await client.get("/admin", headers={"Accept": "text/html"})
    assert response.status_code == 302
    assert response.headers["location"] == "/"


async def test_signing_in_raises_the_tier(app, client):
    await sign_in(app, client)
    body = await (await client.get("/api/session")).get_json()
    assert (body["role"], body["tier"], body["signed_in"]) == ("user", "internal", True)
    assert body["is_admin"] is False
