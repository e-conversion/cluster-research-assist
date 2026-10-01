"""The settings an administrator may change at runtime."""

import pytest
from conftest import make_settings

from cra.app.policy import Policy, PolicyError


def test_policy_rejects_unknown_keys_outside_the_registry(tmp_path):
    policy = Policy(make_settings(tmp_path))
    with pytest.raises(PolicyError, match="unknown setting"):
        policy.set("nope", 1)
