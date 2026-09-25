"""A scripted OpenID provider served through respx: discovery, JWKS and the
token endpoint. ``nonce`` and ``claims`` are set by the test between the
login redirect and the callback."""

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import respx
from httpx import Response
from joserfc import jwt
from joserfc.jwk import RSAKey

ISSUER = "https://idp.test"
CLIENT_ID = "cra-client"
CLIENT_SECRET = "cra-secret"
# the settings that turn institutional sign-in on against this provider
SETTINGS = {
    "oidc_issuer": ISSUER,
    "oidc_client_id": CLIENT_ID,
    "oidc_client_secret": CLIENT_SECRET,
    "oidc_redirect_uri": "https://cra.test/auth/callback",
    "auth_admin_contact": "admin@cra.test",
}


async def start_login(client, idp: "MockIdp") -> dict[str, list[str]]:
    """GET /auth/login and hand back the state the provider expects."""
    response = await client.get("/auth/login")
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    idp.nonce = query["nonce"][0]
    return query


async def institution_sign_in(client, idp: "MockIdp"):
    """The whole round trip; returns the callback's response."""
    query = await start_login(client, idp)
    return await client.get(f"/auth/callback?code=c&state={query['state'][0]}")


class MockIdp:
    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.nonce = ""
        self.claims: dict[str, Any] = {
            "sub": "pairwise-1",
            "email": "Ada@Example.org",
            "given_name": "Ada",
            "family_name": "Lovelace",
            "schac_home_organization": "tum.de",
            "organization_name": "Technische Universität München",
        }
        self.token_requests: list[dict[str, str]] = []

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "end_session_endpoint": f"{ISSUER}/logout",
        }

    def id_token(self, **overrides: Any) -> str:
        now = int(time.time())
        payload = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
            **self.claims,
            **overrides,
        }
        return jwt.encode({"alg": "RS256", "kid": "k1"}, payload, self.key)

    def install(self, router: respx.Router, **token_overrides: Any) -> None:
        router.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(200, json=self.discovery())
        )
        router.get(f"{ISSUER}/jwks").mock(
            return_value=Response(200, json={"keys": [self.key.as_dict(private=False)]})
        )

        def token(request):
            self.token_requests.append(dict(request.headers))
            return Response(
                200,
                json={
                    "access_token": "at",
                    "token_type": "Bearer",
                    "id_token": self.id_token(**token_overrides),
                },
            )

        router.post(f"{ISSUER}/token").mock(side_effect=token)
