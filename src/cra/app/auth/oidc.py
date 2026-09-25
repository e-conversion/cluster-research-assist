"""OpenID Connect login (Authorization Code + PKCE) against a confidential
client such as the DFN-AAI OIDC proxy.

Endpoints come from the discovery document, never from configuration. ID-token
signature and claim checks are joserfc's; the HTTP calls go through the
application's client so the whole flow can be exercised against a mocked
provider.
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from cra.app.auth.binding import Claims, resolve_login
from cra.app.auth.principal import LoginDenied, LoginOutcome, SessionState
from cra.app.history.repository import Repository
from cra.config.settings import Settings

log = logging.getLogger(__name__)

# ID tokens carry second precision and clocks drift a little
CLOCK_LEEWAY_S = 60
JWKS_MIN_REFRESH_S = 60
SIGNING_ALGORITHMS = ["RS256", "PS256", "ES256"]
# denials that a verified identity may answer by asking for an account; a
# mismatched organisation or a disabled account may not
REQUESTABLE = frozenset({LoginDenied.NOT_REGISTERED, LoginDenied.NO_EMAIL})


def code_challenge(verifier: str) -> str:
    """PKCE S256: base64url(sha256(verifier)) without padding (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcProvider:
    def __init__(
        self, settings: Settings, repo: Repository, http: httpx.AsyncClient
    ) -> None:
        self._issuer = settings.oidc_issuer.rstrip("/")
        self._client_id = settings.oidc_client_id
        self._client_secret = settings.oidc_client_secret.get_secret_value()
        self._redirect_uri = settings.oidc_redirect_uri
        self._scopes = settings.oidc_scopes
        self._admins = {a.strip().lower() for a in settings.auth_admins}
        self._organizations = list(settings.auth_home_organizations)
        self._repo = repo
        self._http = http
        self._discovery: dict[str, Any] = {}
        self._jwks: KeySet | None = None
        self._jwks_fetched_at = 0.0

    async def start(self) -> None:
        response = await self._http.get(
            self._issuer + "/.well-known/openid-configuration"
        )
        response.raise_for_status()
        # kept only once it has passed, or a rejected document would be used
        # by the next sign-in
        discovery = response.json()
        if discovery.get("issuer") != self._issuer:
            raise ValueError(
                f"discovery issuer {discovery.get('issuer')!r} != configured {self._issuer!r}"
            )
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if key not in discovery:
                raise ValueError(f"discovery document lacks {key}")
        self._discovery = discovery

    async def login(self, session: SessionState) -> str:
        if not self._discovery:
            await self.start()
        verifier = secrets.token_urlsafe(48)
        pending = {
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "verifier": verifier,
        }
        session.data["oidc"] = pending
        query = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": self._scopes,
            "state": pending["state"],
            "nonce": pending["nonce"],
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{self._discovery['authorization_endpoint']}?{urlencode(query)}"

    async def callback(
        self, session: SessionState, args: dict[str, str]
    ) -> LoginOutcome:
        pending = session.data.pop("oidc", None)
        if not pending or not secrets.compare_digest(
            args.get("state", ""), pending["state"]
        ):
            log.warning("oidc callback with unknown or mismatched state")
            return LoginOutcome(denied=LoginDenied.FAILED)
        if "error" in args:
            log.warning(
                "oidc provider error", extra={"fields": {"error": args["error"]}}
            )
            return LoginOutcome(denied=LoginDenied.FAILED)
        try:
            token = await self._exchange_code(args.get("code", ""), pending["verifier"])
            claims = await self._validate_id_token(token["id_token"], pending["nonce"])
        except (httpx.HTTPError, JoseError, KeyError, ValueError):
            log.exception("oidc token exchange or validation failed")
            return LoginOutcome(denied=LoginDenied.FAILED)
        outcome = await resolve_login(self._repo, claims, self._organizations)
        outcome.grants_admin = outcome.bound and outcome.email in self._admins
        if outcome.denied in REQUESTABLE:
            outcome.identity = asdict(claims)
        return outcome

    def logout_url(self, post_logout_uri: str) -> str | None:
        endpoint = self._discovery.get("end_session_endpoint")
        if not endpoint:
            return None
        return f"{endpoint}?{urlencode({'post_logout_redirect_uri': post_logout_uri})}"

    async def _exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        response = await self._http.post(
            self._discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "code_verifier": verifier,
            },
            auth=(self._client_id, self._client_secret),
        )
        response.raise_for_status()
        return response.json()

    async def _validate_id_token(self, id_token: str, nonce: str) -> Claims:
        if self._jwks is None:
            await self._load_jwks()
        try:
            payload = self._decode(id_token, nonce)
        except JoseError:
            # the provider may have rotated its keys since we fetched them; a
            # stream of forged tokens must not turn into a stream of fetches
            if time.monotonic() - self._jwks_fetched_at < JWKS_MIN_REFRESH_S:
                raise
            await self._load_jwks()
            payload = self._decode(id_token, nonce)
        return Claims(
            issuer=self._issuer,
            sub=payload["sub"],
            email=payload.get("email", ""),
            given_name=payload.get("given_name", ""),
            family_name=payload.get("family_name", ""),
            home_organization=payload.get("schac_home_organization", ""),
            organization_name=payload.get("organization_name", ""),
        )

    async def _load_jwks(self) -> None:
        response = await self._http.get(self._discovery["jwks_uri"])
        response.raise_for_status()
        self._jwks = KeySet.import_key_set(response.json())
        self._jwks_fetched_at = time.monotonic()

    def _decode(self, id_token: str, nonce: str) -> dict[str, Any]:
        if self._jwks is None:
            raise ValueError("JWKS not loaded")
        token = jwt.decode(id_token, self._jwks, algorithms=SIGNING_ALGORITHMS)
        JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY_S,
            iss={"essential": True, "value": self._issuer},
            aud={"essential": True, "value": self._client_id},
            nonce={"essential": True, "value": nonce},
            sub={"essential": True},
            exp={"essential": True},
        ).validate(token.claims)
        return token.claims
