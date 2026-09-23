"""Trading an upstream API key for an MCP token.

The proxies in front of eLabFTW and DataTagger hand out personal, signed tokens
through an HTML form. Driving that form from here means the user registers
without leaving the app, and the key they type is sent to the proxy and to
nowhere else: only the token that comes back is kept.
"""

import logging
import re

import httpx

from cra.core.connectors.sources import Source

log = logging.getLogger(__name__)

REGISTER_TIMEOUT_S = 20.0

# The proxy answers with a page whose only machine-readable part is the MCP URL
# it tells the user to use; the token is in its query string.
TOKEN = re.compile(r"[?&]token=([A-Za-z0-9._\-]+)")

MESSAGES = {
    400: "Registration was rejected — check the address of your instance.",
    401: "The service rejected that API key.",
    403: "The key is valid but has no access.",
    404: "The registration service answered 404 — it is misconfigured.",
    500: "The registration service is misconfigured.",
}
FAILED = "Registration failed — check the address and the key."


class RegistrationError(Exception):
    """Carries a message meant for the person who filled in the form."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def register(
    http: httpx.AsyncClient,
    source: Source,
    *,
    base_url: str,
    api_key: str,
    profile: str = "",
) -> str:
    """Register one API key upstream and return the MCP token it yields."""
    if not source.register_url:
        raise RegistrationError(f"{source.label} has no registration service.", 503)
    base_url = (base_url or source.default_base_url).strip().rstrip("/")
    api_key = api_key.strip()
    if not base_url or not api_key:
        raise RegistrationError("The address and the API key are both required.")

    form = {"api_key": api_key, "base_url": base_url, "validated": "1"}
    if source.profiles:
        allowed = {value for value, _ in source.profiles}
        form["profile"] = profile if profile in allowed else source.profiles[0][0]

    # The first step, and only the first, checks the key against the upstream
    # API. The second mints the token without looking at it again, so skipping
    # it would happily hand out a token for a typo.
    await _post(http, source, {**form, "validated": "0"})
    body = await _post(http, source, form)
    match = TOKEN.search(body)
    if match is None:
        raise RegistrationError(FAILED)
    return match.group(1)


async def _post(http: httpx.AsyncClient, source: Source, form: dict[str, str]) -> str:
    try:
        response = await http.post(
            source.register_url,
            data=form,
            timeout=REGISTER_TIMEOUT_S,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        log.warning(
            "registration service unreachable",
            extra={"fields": {"source": source.kind, "error": type(exc).__name__}},
        )
        raise RegistrationError(
            f"The registration service is unreachable ({type(exc).__name__}).", 502
        ) from None
    if response.status_code >= 400:
        raise RegistrationError(MESSAGES.get(response.status_code, FAILED))
    return response.text
