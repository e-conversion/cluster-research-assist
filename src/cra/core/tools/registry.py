"""The tool registry.

One tool is one function with a declared tier. The registry derives its schema
from the signature, so the description the model reads and the code that runs
cannot drift apart, and it is the only thing that decides who may call what:
a caller at the public tier is not offered an internal tool and cannot reach
one by name.

It is ours rather than the MCP SDK's on purpose. The prototype read a private
FastMCP attribute to list its tools, which is what pinned it to one version of
that library.
"""

import asyncio
import importlib
import inspect
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from cra.config.settings import Settings
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.tiers import Tier

log = logging.getLogger(__name__)

SPEC_ATTR = "_cra_tool"
MODULE_PACKAGE = "cra.core.tools"


class RegistryError(Exception):
    pass


class ToolError(Exception):
    """A tool refused the arguments it was given."""


@dataclass(frozen=True)
class ToolContext:
    """What a tool is allowed to touch. A tool never reads global state to find
    out who is calling."""

    indexes: Indexes
    settings: Settings
    tier: Tier = Tier.INTERNAL
    http: Any = None
    caller: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: Tier
    description: str
    parameters: dict[str, Any]
    function: Callable[..., Any]
    # the model the parameters were derived from; every call is checked
    # against it, so a bound in the schema is a bound the tool can rely on
    arguments: type[BaseModel]

    @property
    def schema(self) -> dict[str, Any]:
        """The OpenAI function-calling shape, which the MCP surface also uses."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def derive_model(function: Callable[..., Any]) -> type[BaseModel]:
    """The argument model from the signature, skipping the context argument.

    Annotated types carry the per-argument descriptions, so the model is told
    what each one means without a second place to keep in sync. Unknown
    arguments are refused: a call the signature cannot take is a bad call.
    """
    signature = inspect.signature(function)
    hints = getattr(function, "__annotations__", {})
    fields: dict[str, Any] = {}
    for name, parameter in list(signature.parameters.items())[1:]:
        if name not in hints:
            raise RegistryError(f"{function.__name__}: argument {name!r} has no type")
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[name] = (hints[name], default)
    return create_model(  # type: ignore[call-overload,no-any-return]
        f"{function.__name__}_arguments",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def derive_parameters(function: Callable[..., Any]) -> dict[str, Any]:
    """A JSON schema from the signature, as the model-facing description."""
    return schema_of(derive_model(function))


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    # the model refuses extras at call time; saying so in the schema would
    # only push some endpoints into strict mode
    schema.pop("additionalProperties", None)
    for entry in schema.get("properties", {}).values():
        entry.pop("title", None)
    schema.setdefault("properties", {})
    return schema


def bad_arguments(exc: ValidationError) -> str:
    """One line per problem, in the words the schema uses."""
    problems = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"]) or "arguments"
        problems.append(f"{where}: {error['msg']}")
    return "; ".join(problems)


def tool(
    *, tier: Tier, name: str = "", description: str = ""
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a function a tool. The tier is required: a tool whose reach
    nobody decided must not exist."""

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        text = description or inspect.cleandoc(function.__doc__ or "")
        if not text:
            raise RegistryError(f"{function.__name__}: a tool needs a description")
        arguments = derive_model(function)
        spec = ToolSpec(
            name=name or function.__name__,
            tier=tier,
            description=text,
            parameters=schema_of(arguments),
            function=function,
            arguments=arguments,
        )
        setattr(function, SPEC_ATTR, spec)
        return function

    return decorate


@dataclass
class Registry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(sorted(self._tools.values(), key=lambda s: s.name))

    def register(self, function: Callable[..., Any]) -> ToolSpec:
        spec: ToolSpec | None = getattr(function, SPEC_ATTR, None)
        if spec is None:
            raise RegistryError(f"{function.__name__} is not a tool")
        if spec.name in self._tools:
            raise RegistryError(f"two tools are called {spec.name!r}")
        self._tools[spec.name] = spec
        return spec

    def add_module(self, module: ModuleType) -> list[ToolSpec]:
        found = [
            member
            for _, member in vars(module).items()
            if callable(member) and hasattr(member, SPEC_ATTR)
        ]
        return [self.register(f) for f in found]

    def specs(self, tier: Tier = Tier.INTERNAL) -> list[ToolSpec]:
        return [spec for spec in self if tier.allows(spec.tier)]

    def schemas(self, tier: Tier = Tier.INTERNAL) -> list[dict[str, Any]]:
        return [spec.schema for spec in self.specs(tier)]

    def get(self, name: str, tier: Tier = Tier.INTERNAL) -> ToolSpec | None:
        """The tool, or None when it does not exist or is out of reach. The two
        are deliberately the same answer: a caller learns nothing by asking."""
        spec = self._tools.get(name)
        return spec if spec is not None and tier.allows(spec.tier) else None

    async def call(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
        spec = self.get(name, ctx.tier)
        if spec is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            checked = spec.arguments.model_validate(arguments)
        except ValidationError as exc:
            return {"error": f"Bad arguments for {name}: {bad_arguments(exc)}"}
        values = {
            field: getattr(checked, field) for field in type(checked).model_fields
        }
        try:
            if inspect.iscoroutinefunction(spec.function):
                return await spec.function(ctx, **values)
            # the lexical and dense searches hold the loop otherwise
            return await asyncio.to_thread(spec.function, ctx, **values)
        except ToolError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            log.exception("tool failed", extra={"fields": {"tool": name}})
            return {"error": f"{name} failed: {type(exc).__name__}: {exc}"}


def load(
    settings: Settings, indexes: Indexes, modules: list[str] | None = None
) -> Registry:
    """Import each configured module and let it decide what to register."""
    registry = Registry()
    for name in modules if modules is not None else settings.tool_modules:
        try:
            module = importlib.import_module(f"{MODULE_PACKAGE}.{name}")
        except ModuleNotFoundError as exc:
            raise RegistryError(f"no tool module named {name!r}") from exc
        setup = getattr(module, "setup", None)
        if setup is None:
            raise RegistryError(f"tool module {name!r} has no setup()")
        setup(registry, settings, indexes)
    log.info(
        "tools loaded",
        extra={"fields": {"count": len(registry), "names": [s.name for s in registry]}},
    )
    return registry
