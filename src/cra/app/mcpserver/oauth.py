"""Signing in to the outward MCP endpoint (OAuth 2.1), for the clients that
cannot be handed a fixed token: Claude's and ChatGPT's connectors.

The protocol (metadata documents, PKCE, client authentication, error shapes)
is the MCP SDK's; this module stores what the SDK hands over and decides who
gets what. A client registers itself and sends its person to
``/oauth/authorize``, which leads to the consent page
(``cra.app.web.route_oauth``). An approval there leaves a one-time code that
the client redeems at ``/oauth/token`` for a grant: a row among the person's
tokens, listed and revocable next to the ones they made by hand, checked by
the same ``tokens.verify`` on every call.
"""

import ipaddress
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import (
    MetadataHandler,
    ProtectedResourceMetadataHandler,
)
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.server.transport_security import (
    DEFAULT_MAX_REQUEST_BODY_SIZE,
    RequestBodyLimitMiddleware,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Router, request_response

from cra.app.auth import tokens
from cra.app.history.repository import Repository, utcnow
from cra.config.settings import LOOPBACK_HOSTS, Settings

# Clients renew an access token without the person; a short life bounds what
# a leaked one is worth. A grant lives as long as a default hand-made token,
# counted from its last renewal, so a connector in use never asks again.
ACCESS_LIFETIME = timedelta(hours=1)
GRANT_LIFETIME = timedelta(days=tokens.DEFAULT_DAYS)
# long enough to sign in at one's institution first
REQUEST_LIFETIME = timedelta(minutes=15)
CODE_LIFETIME = timedelta(minutes=5)
REFRESH_PREFIX = "crr1_"
# A client document (Client ID Metadata Document draft) is a few hundred
# bytes; the draft suggests refusing anything past 5 kB. It is fetched again
# after a day, so a client's changes reach this server without a restart.
DOCUMENT_MAX_BYTES = 5 * 1024
DOCUMENT_TTL = timedelta(days=1)
AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]
# schemes that would make the redirect run in, or read from, the browser
UNSAFE_SCHEMES = {"javascript", "data", "file", "blob", "about", "vbscript"}


@dataclass(frozen=True)
class Paths:
    """Every address the sign-in involves, from one place, since the client
    compares them character by character."""

    origin: str
    base: str
    mcp: str

    @classmethod
    def of(cls, settings: Settings) -> "Paths":
        return cls(settings.public_url, settings.base_path, settings.mcp_server_path)

    @property
    def issuer(self) -> str:
        return self.origin + self.base

    @property
    def resource(self) -> str:
        return self.origin + self.base + self.mcp

    @property
    def resource_metadata(self) -> str:
        # RFC 9728 §3.1: the well-known part goes between host and path
        return f"{self.origin}{RESOURCE_METADATA}{self.base}{self.mcp}"

    def endpoint(self, name: str) -> str:
        return f"{self.base}/oauth/{name}"

    @property
    def consent(self) -> str:
        return self.endpoint("consent")


RESOURCE_METADATA = "/.well-known/oauth-protected-resource"
SERVER_METADATA = "/.well-known/oauth-authorization-server"


def _timestamp(when: datetime) -> int:
    # the database hands back naive UTC; a naive timestamp() would read it as local
    return int(when.replace(tzinfo=UTC).timestamp())


def redirect_allowed(uri: str, https_hosts: list[str]) -> bool:
    """Where a client may send a person back with a code: an https host the
    deployment trusts, the person's own machine, or an app's own scheme.

    A code is only worth anything together with the PKCE verifier the client
    kept, so this is about not handing codes to sites that pose as a client.
    """
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    if parts.fragment:
        return False
    if scheme == "https":
        return trusted_host(parts.hostname or "", https_hosts)
    if scheme == "http":
        return _loopback(parts.hostname or "")
    # an app-specific scheme (RFC 8252 §7.1): reverse-domain, like cursor://
    return bool(scheme) and scheme not in UNSAFE_SCHEMES


def trusted_host(host: str, hosts: list[str]) -> bool:
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def _loopback(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Access(AccessToken):
    grant_id: str


class Refresh(RefreshToken):
    grant_id: str


class Provider:
    """The storage behind the SDK's handlers (its
    ``OAuthAuthorizationServerProvider`` protocol)."""

    def __init__(
        self, repo: Repository, settings: Settings, http: httpx.AsyncClient
    ) -> None:
        self._repo = repo
        self._http = http
        self._paths = Paths.of(settings)
        self._hosts = [
            h.strip().lower() for h in settings.mcp_oauth_client_hosts if h.strip()
        ]

    # ---- clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id.startswith("https://"):
            return await self._published(client_id)
        row = await self._repo.get_oauth_client(client_id)
        return OAuthClientInformationFull.model_validate(row.info) if row else None

    async def _published(self, url: str) -> OAuthClientInformationFull | None:
        """A client that names itself by the address of its own description,
        which is how Claude and ChatGPT connect unless told otherwise.

        Fetched only from the trusted hosts: anything else would let whoever
        calls /oauth/authorize make this server fetch an address of their
        choosing. A document that cannot be fetched now falls back to the
        last good copy.
        """
        parts = urlsplit(url)
        if parts.fragment or not trusted_host(parts.hostname or "", self._hosts):
            return None
        row = await self._repo.get_oauth_client(url)
        cached = OAuthClientInformationFull.model_validate(row.info) if row else None
        if row is not None and utcnow() - row.created_at < DOCUMENT_TTL:
            return cached
        document = await self._fetch(url)
        if document is None:
            return cached
        try:
            client = OAuthClientInformationFull.model_validate(document)
        except ValidationError:
            return None
        # this server holds no keys to check a signed client assertion, so a
        # document client is a public one, kept honest by PKCE
        if (
            client.client_id != url
            or client.client_secret is not None
            or client.token_endpoint_auth_method not in (None, "none")
            or not all(
                redirect_allowed(str(u), self._hosts)
                for u in client.redirect_uris or []
            )
        ):
            return None
        client.token_endpoint_auth_method = "none"
        await self._repo.put_oauth_client(
            url, client.model_dump(mode="json", exclude_none=True)
        )
        return client

    async def _fetch(self, url: str) -> dict[str, Any] | None:
        try:
            async with self._http.stream(
                "GET",
                url,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    return None
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > DOCUMENT_MAX_BYTES:
                        return None
            document = json.loads(body)
        except (httpx.HTTPError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not redirect_allowed(str(uri), self._hosts):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"{uri} is not a place this server sends people back to",
                )
        # A registered secret is kept as issued: the SDK compares it as such.
        # It names a client, not a person; nothing is granted without both a
        # person's approval and the PKCE verifier.
        await self._repo.add_oauth_client(
            client_info.client_id or "",
            client_info.model_dump(mode="json", exclude_none=True),
        )

    # ---- the person's answer

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if (
            params.resource is not None
            and params.resource.rstrip("/") != self._paths.resource
        ):
            raise AuthorizeError(
                "invalid_target", "tokens here are for this server's MCP endpoint only"
            )
        handle = secrets.token_urlsafe(32)
        await self._repo.add_oauth_request(
            tokens.hash_value(handle),
            client.client_id or "",
            params.model_dump(mode="json"),
            utcnow() + REQUEST_LIFETIME,
        )
        return f"{self._paths.origin}{self._paths.consent}?{urlencode({'request': handle})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = await self._repo.get_oauth_code(tokens.hash_value(authorization_code))
        if row is None or row.client_id != client.client_id or row.user_id is None:
            return None
        params = AuthorizationParams.model_validate(row.params)
        return AuthorizationCode(
            code=authorization_code,
            scopes=params.scopes or [],
            expires_at=_timestamp(row.expires_at),
            client_id=row.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=row.user_id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not await self._repo.take_oauth_code(
            tokens.hash_value(authorization_code.code)
        ):
            raise TokenError("invalid_grant", "the code has already been used")
        user = await self._repo.get_user(authorization_code.subject or "")
        if user is None or not user.is_active:
            raise TokenError("invalid_grant", "the account is not active")
        access, refresh = _values()
        now = utcnow()
        await self._repo.create_token(
            user.id,
            client_label(client),
            tokens.hash_value(access),
            now + GRANT_LIFETIME,
            client_id=client.client_id,
            refresh_hash=tokens.hash_value(refresh),
            access_expires_at=now + ACCESS_LIFETIME,
        )
        return _response(access, refresh, authorization_code.scopes)

    # ---- renewal

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Refresh | None:
        if not refresh_token.startswith(REFRESH_PREFIX):
            return None
        found = await self._repo.get_token_by_refresh_hash(
            tokens.hash_value(refresh_token)
        )
        if found is None:
            return None
        row, owner_active = found
        if (
            row.client_id != client.client_id
            or row.revoked_at is not None
            or row.expires_at <= utcnow()
            or not owner_active
        ):
            return None
        return Refresh(
            token=refresh_token,
            client_id=row.client_id or "",
            scopes=[],
            expires_at=_timestamp(row.expires_at),
            subject=row.user_id,
            grant_id=row.id,
        )

    async def exchange_refresh_token(
        self,
        # load_refresh_token already matched the grant to this client
        client: OAuthClientInformationFull,  # noqa: ARG002
        refresh_token: Refresh,
        scopes: list[str],
    ) -> OAuthToken:
        access, refresh = _values()
        now = utcnow()
        renewed = await self._repo.renew_grant(
            refresh_token.grant_id,
            tokens.hash_value(refresh_token.token),
            token_hash=tokens.hash_value(access),
            refresh_hash=tokens.hash_value(refresh),
            access_expires_at=now + ACCESS_LIFETIME,
            expires_at=now + GRANT_LIFETIME,
        )
        if not renewed:
            raise TokenError("invalid_grant", "the refresh token has already been used")
        return _response(access, refresh, scopes)

    # ---- use and revocation

    async def load_access_token(self, token: str) -> Access | None:
        row = await tokens.verify(self._repo, token)
        if row is None or row.client_id is None:
            return None
        return Access(
            token=token,
            client_id=row.client_id,
            scopes=[],
            expires_at=_timestamp(row.access_expires_at)
            if row.access_expires_at
            else None,
            resource=self._paths.resource,
            subject=row.user_id,
            grant_id=row.id,
        )

    async def revoke_token(self, token: Access | Refresh) -> None:
        await self._repo.revoke_token(token.grant_id)


def _values() -> tuple[str, str]:
    return tokens.PREFIX + secrets.token_urlsafe(
        32
    ), REFRESH_PREFIX + secrets.token_urlsafe(32)


def _response(access: str, refresh: str, scopes: list[str]) -> OAuthToken:
    return OAuthToken(
        access_token=access,
        expires_in=int(ACCESS_LIFETIME.total_seconds()),
        refresh_token=refresh,
        scope=" ".join(scopes) or None,
    )


def client_label(client: OAuthClientInformationFull) -> str:
    """What the person's token list calls the grant: the name the client gave
    itself, or failing that where it sends people back to."""
    name = (client.client_name or "").strip()
    if not name and client.redirect_uris:
        name = urlsplit(str(client.redirect_uris[0])).hostname or ""
    return (name or "MCP client")[: tokens.LABEL_MAX]


# ---------- the consent page's side ----------


@dataclass(frozen=True)
class Pending:
    """A request as the consent page shows it."""

    client_name: str
    return_host: str


async def pending(repo: Repository, handle: str) -> Pending | None:
    row = await repo.get_oauth_request(tokens.hash_value(handle))
    if row is None:
        return None
    client = await repo.get_oauth_client(row.client_id)
    if client is None:
        return None
    info = OAuthClientInformationFull.model_validate(client.info)
    params = AuthorizationParams.model_validate(row.params)
    parts = urlsplit(str(params.redirect_uri))
    return Pending(
        client_name=client_label(info),
        # an app's own scheme has no host; the scheme is then what identifies it
        return_host=parts.hostname or f"{parts.scheme}:",
    )


async def answer(
    repo: Repository, handle: str, user_id: str, approve: bool
) -> str | None:
    """Where to send the person next, or None for a request that is gone.

    The code goes back to the redirect address the SDK checked against the
    client's registration when the request came in.
    """
    request_hash = tokens.hash_value(handle)
    row = await repo.get_oauth_request(request_hash)
    if row is None:
        return None
    params = AuthorizationParams.model_validate(row.params)
    if not approve:
        await repo.delete_oauth_request(request_hash)
        return construct_redirect_uri(
            str(params.redirect_uri), error="access_denied", state=params.state
        )
    code = secrets.token_urlsafe(32)
    if not await repo.approve_oauth_request(
        request_hash, user_id, tokens.hash_value(code), utcnow() + CODE_LIFETIME
    ):
        return None
    return construct_redirect_uri(
        str(params.redirect_uri), code=code, state=params.state
    )


# ---------- routes ----------


def _cors(app: Any, methods: list[str]) -> Any:
    # the metadata and token endpoints are fetched by browser-based clients
    # too (the MCP Inspector); there is no cookie to protect on any of them
    return CORSMiddleware(
        app=app,
        allow_origins="*",
        allow_methods=methods,
        allow_headers=["mcp-protocol-version"],
    )


def _limited(handler: Any) -> Any:
    return RequestBodyLimitMiddleware(
        request_response(handler), DEFAULT_MAX_REQUEST_BODY_SIZE
    )


def server_metadata(paths: Paths) -> OAuthMetadata:
    # built from strings: a URL object would add a slash to a path-less issuer,
    # and the client compares the issuer exactly
    return OAuthMetadata.model_validate(
        {
            "issuer": paths.issuer,
            "authorization_endpoint": paths.origin + paths.endpoint("authorize"),
            "token_endpoint": paths.origin + paths.endpoint("token"),
            "registration_endpoint": paths.origin + paths.endpoint("register"),
            "revocation_endpoint": paths.origin + paths.endpoint("revoke"),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": AUTH_METHODS,
            "revocation_endpoint_auth_methods_supported": AUTH_METHODS,
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True,
        }
    )


def resource_metadata(paths: Paths, name: str) -> ProtectedResourceMetadata:
    return ProtectedResourceMetadata.model_validate(
        {
            "resource": paths.resource,
            "authorization_servers": [paths.issuer],
            "resource_name": name,
        }
    )


def router(provider: Provider, settings: Settings) -> Router:
    paths = Paths.of(settings)
    authenticator = ClientAuthenticator(provider)
    resource = ProtectedResourceMetadataHandler(
        resource_metadata(paths, settings.cluster_display_name)
    ).handle
    get = ["GET", "OPTIONS"]
    post = ["POST", "OPTIONS"]
    return Router(
        routes=[
            Route(
                f"{RESOURCE_METADATA}{paths.base}{paths.mcp}",
                _cors(request_response(resource), get),
                methods=get,
            ),
            Route(
                f"{SERVER_METADATA}{paths.base}",
                _cors(
                    request_response(MetadataHandler(server_metadata(paths)).handle),
                    get,
                ),
                methods=get,
            ),
            # a browser is sent here, so no CORS
            Route(
                paths.endpoint("authorize"),
                _limited(AuthorizationHandler(provider).handle),
                methods=["GET", "POST"],
            ),
            Route(
                paths.endpoint("token"),
                _cors(_limited(TokenHandler(provider, authenticator).handle), post),
                methods=post,
            ),
            Route(
                paths.endpoint("register"),
                _cors(
                    _limited(
                        RegistrationHandler(
                            provider, ClientRegistrationOptions(enabled=True)
                        ).handle
                    ),
                    post,
                ),
                methods=post,
            ),
            Route(
                paths.endpoint("revoke"),
                _cors(
                    _limited(RevocationHandler(provider, authenticator).handle), post
                ),
                methods=post,
            ),
        ]
    )


def served_paths(settings: Settings) -> frozenset[str]:
    """The paths the router answers, so the dispatcher can tell them apart
    from the web app's own ``/oauth/consent``."""
    paths = Paths.of(settings)
    return frozenset(
        {
            f"{RESOURCE_METADATA}{paths.base}{paths.mcp}",
            f"{SERVER_METADATA}{paths.base}",
            *(paths.endpoint(n) for n in ("authorize", "token", "register", "revoke")),
        }
    )
