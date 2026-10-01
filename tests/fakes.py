"""Stand-ins for the parts that would otherwise need a model or a network."""

import asyncio
import contextlib
import hashlib
from types import SimpleNamespace

import httpx
import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import ToolAnnotations

from cra.assistant.mcpclient.host import RemoteHost
from cra.assistant.mcpclient.pool import RemotePool
from cra.core.connectors.sources import Source


class FakeEncoder:
    """Deterministic vectors from the text itself.

    Not an embedding in any useful sense, but stable, fast and normalised, so
    everything above it can be tested without loading a model. Repeated words
    move the vector, which is enough for "this query is closer to that text".
    """

    def __init__(self, dimension: int = 8, name: str = "fake") -> None:
        self.dimension = dimension
        self.name = name

    def encode(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for word in text.lower().split():
            digest = hashlib.sha256(word.encode()).digest()
            vector[digest[0] % self.dimension] += 1.0
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector


class Chunk:
    """A streamed piece shaped like the SDK's, built by the helpers below."""

    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


class Delta:
    def __init__(self, **fields):
        self.content = fields.get("content")
        self.tool_calls = fields.get("tool_calls")
        self.reasoning = fields.get("reasoning")
        self.reasoning_content = fields.get("reasoning_content")


class Choice:
    def __init__(self, delta):
        self.delta = delta
        self.finish_reason = None


class Usage:
    def __init__(self, prompt, completion, total):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class ToolCallFragment:
    def __init__(self, index=None, id=None, name=None, arguments=""):
        if index is not None:
            self.index = index
        self.id = id
        self.function = SimpleNamespace(name=name, arguments=arguments)


def text_chunk(text: str) -> Chunk:
    return Chunk([Choice(Delta(content=text))])


def reasoning_chunk(text: str, inline: bool = False) -> Chunk:
    key = "reasoning_content" if not inline else "reasoning"
    return Chunk([Choice(Delta(**{key: text}))])


def tool_chunk(index=None, id=None, name=None, arguments="") -> Chunk:
    return Chunk(
        [Choice(Delta(tool_calls=[ToolCallFragment(index, id, name, arguments)]))]
    )


def usage_chunk(prompt: int, completion: int, total: int) -> Chunk:
    return Chunk([], Usage(prompt, completion, total))


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        async def generate():
            for chunk in self._chunks:
                yield chunk

        return generate()

    async def close(self):
        self.closed = True


class FakeOpenAI:
    """One chunk list per model round, consumed in order.

    An entry that is an exception is raised by the call instead, which is how a
    failing endpoint is scripted.
    """

    def __init__(self, rounds, fail_stream_options: Exception | None = None):
        self._rounds = list(rounds)
        self._fail_stream_options = fail_stream_options
        self.calls: list[dict] = []
        self.streams: list[FakeStream] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        if "stream_options" in kwargs and self._fail_stream_options is not None:
            raise self._fail_stream_options
        self.calls.append(kwargs)
        if not self._rounds:
            raise AssertionError("more rounds were requested than were scripted")
        script = self._rounds.pop(0)
        if isinstance(script, Exception):
            raise script
        stream = FakeStream(script)
        self.streams.append(stream)
        return stream


def crossref_message(**overrides):
    base = {
        "DOI": "10.1038/S41586-021-03819-2",
        "title": ["Highly accurate protein structure prediction"],
        "author": [{"given": "John", "family": "Jumper"}, {"family": "Evans"}],
        "container-title": ["Nature"],
        "issued": {"date-parts": [[2021, 7, 15]]},
        "abstract": "<jats:p>We present a method.</jats:p>",
    }
    return {**base, **overrides}


def crossref_response(**overrides):
    return httpx.Response(200, json={"message": crossref_message(**overrides)})


ELAB = Source(
    kind="elab",
    label="eLabFTW",
    key_label="eLabFTW API key",
    url="https://proxy.invalid/el/mcp",
    register_url="https://proxy.invalid/el/register",
    default_base_url="https://eln.invalid",
)


class Toy:
    """An MCP server behind a transport that checks the token in the URL."""

    def __init__(self, token: str = "good") -> None:
        self.token = token
        self.down = False
        self.connections = 0
        self.server = MCPServer("toy")

        @self.server.tool(annotations=ToolAnnotations(read_only_hint=True))
        def echo(text: str) -> str:
            """Say it back."""
            return f"echo: {text}"

        @self.server.tool()
        def count_items() -> int:
            """How many items there are."""
            return 7

        # what the proxies really look like: writes with no annotation, told
        # apart by the verb, and one that declares itself despite its name
        @self.server.tool()
        def create_item(title: str) -> str:
            """Add an item."""
            return f"created {title}"

        @self.server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def lookup_and_mark(item: int) -> str:
            """Marks an item as seen."""
            return f"marked {item}"

    @contextlib.asynccontextmanager
    async def transport(self, url: str):
        self.connections += 1
        if self.down:
            raise ConnectionError("connection refused")
        if url.partition("token=")[2] != self.token:
            raise RuntimeError("HTTP 401 Unauthorized")
        async with create_client_server_memory_streams() as (client, server):
            low = self.server._lowlevel_server
            task = asyncio.create_task(
                low.run(server[0], server[1], low.create_initialization_options())
            )
            try:
                yield client
            finally:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task


def make_host(toy: Toy, idle_s: float = 600.0, allow_write: bool = False) -> RemoteHost:
    return RemoteHost(
        {"elab": ELAB}, RemotePool(idle_s, toy.transport), allow_write=allow_write
    )
