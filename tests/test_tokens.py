"""Bearer tokens for the outward MCP endpoint."""

import pytest

from cra.app.auth import tokens

SECRET = "a-deployment-secret"
NOW = 1_700_000_000


def test_a_token_carries_its_subject_and_its_end():
    token = tokens.issue(SECRET, "a-colleague", days=2, now=NOW)
    claims = tokens.verify(SECRET, token, now=NOW)
    assert claims is not None
    assert claims.subject == "a-colleague"
    assert claims.expires_at == NOW + 2 * tokens.DAY_S


def test_it_is_over_when_it_expires():
    token = tokens.issue(SECRET, "a-colleague", days=1, now=NOW)
    assert tokens.verify(SECRET, token, now=NOW + tokens.DAY_S - 1) is not None
    assert tokens.verify(SECRET, token, now=NOW + tokens.DAY_S) is None


def test_another_deployment_cannot_mint_one():
    token = tokens.issue("another-secret", "a-colleague", days=1, now=NOW)
    assert tokens.verify(SECRET, token, now=NOW) is None


@pytest.mark.parametrize(
    "broken",
    [
        "",
        "not-a-token",
        "cra1.payload",
        "cra2.{payload}.{signature}",
        "{version}.{payload}.",
        "{version}.{payload}.AAAA",
        "{version}.YWJj.{signature}",
    ],
)
def test_anything_but_a_signed_token_is_refused(broken):
    version, payload, signature = tokens.issue(SECRET, "x", now=NOW).split(".")
    token = broken.format(version=version, payload=payload, signature=signature)
    assert tokens.verify(SECRET, token, now=NOW) is None


def test_without_a_secret_nothing_verifies():
    """A deployment that configured no secret must not accept anything."""
    token = tokens.issue(SECRET, "a-colleague", now=NOW)
    assert tokens.verify("", token, now=NOW) is None


@pytest.mark.parametrize(
    ("secret", "subject", "days", "complaint"),
    [
        ("", "x", 1, "secret"),
        (SECRET, "  ", 1, "subject"),
        (SECRET, "x", 0, "at least one day"),
    ],
    ids=["no secret", "no subject", "no time"],
)
def test_a_token_that_makes_no_sense_is_not_minted(secret, subject, days, complaint):
    with pytest.raises(ValueError, match=complaint):
        tokens.issue(secret, subject, days=days)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer  abc ", "abc"),
        ("Basic abc", ""),
        ("abc", ""),
        ("", ""),
    ],
)
def test_the_token_is_read_from_the_authorization_header(header, expected):
    assert tokens.bearer(header) == expected
