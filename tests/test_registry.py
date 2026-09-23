"""The registry: schema derivation, tiers, dispatch and startup validation."""

from typing import Annotated

import pytest
from conftest import make_settings
from fakes import FakeEncoder
from library_builder import write_library
from pydantic import Field

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import (
    Registry,
    RegistryError,
    ToolContext,
    ToolError,
    derive_parameters,
    load,
    tool,
)
from cra.core.tools.tiers import Tier


@pytest.fixture
def indexes(tmp_path):
    return Indexes.build(Library.load(write_library(tmp_path / "lib")), FakeEncoder())


@pytest.fixture
def ctx(indexes, tmp_path):
    return ToolContext(indexes=indexes, settings=make_settings(tmp_path))


def test_the_schema_comes_from_the_signature():
    @tool(tier=Tier.PUBLIC)
    def example(
        ctx: ToolContext,
        query: Annotated[str, Field(description="What to look for.")],
        limit: int = 5,
        exact: bool = False,
    ) -> dict:
        """A description."""
        return {}

    spec = example._cra_tool
    assert spec.name == "example"
    assert spec.description == "A description."
    assert spec.parameters == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for."},
            "limit": {"type": "integer", "default": 5},
            "exact": {"type": "boolean", "default": False},
        },
        "required": ["query"],
    }
    assert spec.schema["function"]["name"] == "example"


def test_an_untyped_argument_is_refused():
    def example(ctx: ToolContext, query) -> dict:
        """A description."""
        return {}

    with pytest.raises(RegistryError, match="has no type"):
        derive_parameters(example)


def test_a_tool_without_a_description_is_refused():
    with pytest.raises(RegistryError, match="needs a description"):

        @tool(tier=Tier.PUBLIC)
        def example(ctx: ToolContext) -> dict:
            return {}


def test_two_tools_with_one_name_are_refused():
    @tool(tier=Tier.PUBLIC, name="same")
    def first(ctx: ToolContext) -> dict:
        """One."""
        return {}

    @tool(tier=Tier.PUBLIC, name="same")
    def second(ctx: ToolContext) -> dict:
        """Two."""
        return {}

    registry = Registry()
    registry.register(first)
    with pytest.raises(RegistryError, match="two tools are called"):
        registry.register(second)


def _undecorated(ctx: ToolContext) -> dict:
    """Nothing declared this one's tier, so it is not a tool."""
    return {}


def test_registering_something_that_is_not_a_tool_is_refused():
    with pytest.raises(RegistryError, match="not a tool"):
        Registry().register(_undecorated)


@pytest.fixture
def two_tiers():
    @tool(tier=Tier.PUBLIC, name="open")
    def public_tool(ctx: ToolContext) -> dict:
        """Open."""
        return {"seen": "public"}

    @tool(tier=Tier.INTERNAL, name="closed")
    async def internal_tool(ctx: ToolContext) -> dict:
        """Closed."""
        return {"seen": "internal"}

    registry = Registry()
    registry.register(public_tool)
    registry.register(internal_tool)
    return registry


def test_a_public_caller_is_not_offered_internal_tools(two_tiers):
    assert [s.name for s in two_tiers.specs(Tier.PUBLIC)] == ["open"]
    assert [s.name for s in two_tiers.specs(Tier.INTERNAL)] == ["closed", "open"]
    assert len(two_tiers.schemas(Tier.PUBLIC)) == 1


async def test_a_public_caller_cannot_reach_an_internal_tool_by_name(two_tiers, ctx):
    public = ToolContext(indexes=ctx.indexes, settings=ctx.settings, tier=Tier.PUBLIC)
    assert two_tiers.get("closed", Tier.PUBLIC) is None
    # the same shape of answer as a tool that does not exist: asking reveals
    # nothing about whether the tool is there
    refused = await two_tiers.call("closed", {}, public)
    assert refused == {"error": "Unknown tool: closed"}
    assert await two_tiers.call("nonsense", {}, public) == {
        "error": "Unknown tool: nonsense"
    }
    assert await two_tiers.call("closed", {}, ctx) == {"seen": "internal"}


async def test_both_synchronous_and_asynchronous_tools_run(two_tiers, ctx):
    assert await two_tiers.call("open", {}, ctx) == {"seen": "public"}
    assert await two_tiers.call("closed", {}, ctx) == {"seen": "internal"}


async def test_a_tool_that_raises_becomes_an_error_not_a_crash(ctx):
    @tool(tier=Tier.PUBLIC, name="boom")
    def explode(ctx: ToolContext, mode: str) -> dict:
        """Explodes."""
        if mode == "refuse":
            raise ToolError("that is not a thing")
        raise RuntimeError("unexpected")

    registry = Registry()
    registry.register(explode)
    assert await registry.call("boom", {"mode": "refuse"}, ctx) == {
        "error": "that is not a thing"
    }
    assert "RuntimeError" in (await registry.call("boom", {"mode": "x"}, ctx))["error"]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, "'query' is required (What to look for.)"),
        ({"query": "x", "wrong": 1}, "'wrong' is not an argument of example"),
        ({"query": "x", "limit": 99}, "'limit': Input should be less than or equal"),
        ({"query": "x", "limit": "many"}, "'limit': Input should be a valid integer"),
    ],
    ids=["missing", "unknown", "out of range", "wrong type"],
)
async def test_a_wrong_call_is_told_what_is_wrong(ctx, arguments, expected):
    """A model that is told which argument is missing fixes it in one round;
    one that is told 'bad arguments' tries the same call again."""

    @tool(tier=Tier.PUBLIC)
    def example(
        ctx: ToolContext,
        query: Annotated[str, Field(description="What to look for.")],
        limit: Annotated[int, Field(le=50)] = 5,
    ) -> dict:
        """A description."""
        return {"query": query, "limit": limit}

    registry = Registry()
    registry.register(example)
    result = await registry.call("example", arguments, ctx)
    assert expected in result["error"]
    assert result["error"].endswith("Call it again with the arguments fixed.")


async def test_arguments_are_coerced_and_defaults_come_from_the_function(ctx):
    @tool(tier=Tier.PUBLIC)
    def example(ctx: ToolContext, query: str, limit: int = 5) -> dict:
        """A description."""
        return {"query": query, "limit": limit}

    registry = Registry()
    registry.register(example)
    assert await registry.call("example", {"query": "x", "limit": "7"}, ctx) == {
        "query": "x",
        "limit": 7,
    }
    assert await registry.call("example", {"query": "x"}, ctx) == {
        "query": "x",
        "limit": 5,
    }


def test_an_unknown_module_is_a_startup_error(indexes, tmp_path):
    with pytest.raises(RegistryError, match="no tool module named 'nope'"):
        load(make_settings(tmp_path), indexes, ["nope"])


def test_a_module_without_setup_is_a_startup_error(indexes, tmp_path):
    with pytest.raises(RegistryError, match="has no setup"):
        load(make_settings(tmp_path), indexes, ["views"])


def test_the_configured_modules_decide_what_is_loaded(indexes, tmp_path):
    settings = make_settings(tmp_path)
    assert [s.name for s in load(settings, indexes, ["status"])] == ["library_status"]
    assert len(load(settings, indexes, [])) == 0


@pytest.fixture
def bounded():
    from typing import Annotated

    from pydantic import Field

    @tool(tier=Tier.PUBLIC, name="listing")
    def listing(
        ctx: ToolContext, limit: Annotated[int, Field(ge=1, le=50)] = 5
    ) -> dict:
        """Lists things."""
        return {"limit": limit}

    registry = Registry()
    registry.register(listing)
    return registry


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"limit": 10**9}, "less than or equal to 50"),
        ({"limit": 0}, "greater than or equal to 1"),
        ({"limit": "many"}, "integer"),
        ({"limit": 5, "offset": 3}, "offset"),
    ],
    ids=["above bound", "below bound", "wrong type", "unknown argument"],
)
async def test_arguments_outside_the_schema_are_refused(
    bounded, ctx, arguments, problem
):
    """The bound in the schema is a bound the tool can rely on, not advice."""
    result = await bounded.call("listing", arguments, ctx)
    assert result["error"].startswith("listing was not called correctly")
    assert problem in result["error"]


async def test_arguments_inside_the_schema_arrive_typed(bounded, ctx):
    assert await bounded.call("listing", {"limit": "7"}, ctx) == {"limit": 7}
    assert await bounded.call("listing", {}, ctx) == {"limit": 5}


def test_the_schema_does_not_switch_endpoints_into_strict_mode(bounded):
    spec = next(iter(bounded))
    assert "additionalProperties" not in spec.parameters
    assert spec.parameters["properties"]["limit"]["maximum"] == 50
