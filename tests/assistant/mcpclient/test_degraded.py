"""What a person is told when an external source fails."""

import pytest


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("HTTP 500 for https://proxy/el/mcp?token=SECRET-TOKEN&x=1"),
        ConnectionError("cannot reach https://proxy/el/mcp?token=SECRET-TOKEN"),
    ],
)
def test_errors_never_carry_the_token(exc):
    from cra.assistant.mcpclient.degraded import friendly_error

    text = friendly_error(exc)
    assert "SECRET-TOKEN" not in text
    assert "token=***" in text or "did not answer" in text
