"""The app on the toy library, with a model that answers slowly from a script.

Usage: python tests/browser/serve.py PORT WORKDIR

Run as its own process by the browser tests, so a page can stream an answer
the way it does in production: two tool rounds, then a long markdown answer a
word at a time. Signs in as ``alice`` with ``PASSWORD``.
"""

import asyncio
import json
import sys
from pathlib import Path

from argon2 import PasswordHasher
from hypercorn.asyncio import serve
from hypercorn.config import Config

from cra.app.auth import local
from cra.app.web import route_chat
from cra.app.web.factory import create_app
from cra.config.settings import Settings

PASSWORD = "correct horse battery"
TOY_LIBRARY = Path(__file__).resolve().parents[1] / "data" / "library"

# Long enough to scroll on a phone, with the blocks that are wider than one:
# a table and a code line.
ANSWER = (
    "## Long-range electrostatics\n\n"
    "| Paper | Year | Method | Long-range scheme |\n|---|---|---|---|\n"
    "| MACE-POLAR-1 | 2026 | equivariant MPNN | polarisable charges |\n\n"
    "```python\ncalc = MACECalculator(model_paths='mace-polar-1.model', device='cuda', "
    "default_dtype='float64')\n```\n\n"
    + "\n\n".join(
        f"Paragraph {i}: a cutoff-based network misses the 1/r tail, so these papers "
        "add it back as an Ewald sum or as learned charges."
        for i in range(1, 30)
    )
)
WORD_S = 0.03


def _chunk(delta):
    return {"choices": [{"index": 0, "delta": delta}]}


def _tool(name, arguments, call_id):
    fragment = {
        "index": 0,
        "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }
    return _chunk({"tool_calls": [fragment]})


class _Stream:
    def __init__(self, chunks, delay):
        self._chunks, self._delay = chunks, delay

    async def __aiter__(self):
        for chunk in self._chunks:
            await asyncio.sleep(self._delay)
            yield chunk

    async def close(self):
        pass


class ScriptedClient:
    """Stands in for ChatClient: the round is read off the tool results so far."""

    def __init__(self, *_):
        pass

    async def stream(self, request):
        done = sum(1 for m in request["messages"] if m.get("role") == "tool")
        if done == 0:
            return _Stream([_tool("library_status", "{}", "c1")], 0.2)
        if done == 1:
            query = json.dumps({"query": "long-range electrostatics"})
            return _Stream([_tool("search_papers", query, "c2")], 0.2)
        return _Stream(
            [_chunk({"content": w + " "}) for w in ANSWER.split(" ")], WORD_S
        )


async def _title(*_):
    return "Long-range electrostatics"


def main(port: int, workdir: Path) -> None:
    # as monkeypatch does it, for a process pytest does not run
    setattr(route_chat, "ChatClient", ScriptedClient)  # noqa: B010
    route_chat.title_.suggest = _title
    # argon2 at its real cost would make the one sign-in per test slow
    local.HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    settings = Settings(
        _env_file=None,
        library_path=TOY_LIBRARY,
        history_url=f"sqlite+aiosqlite:///{workdir}/cra.sqlite",
        history_auto_migrate=True,
        cookie_secure=False,
        log_dir=workdir / "logs",
        query_encoder=False,
        llm_model="model-a",
        llm_api_key="k",
        llm_models=["model-a", "model-b"],
    )
    app = create_app(settings)

    @app.before_serving
    async def account():
        repo = app.extensions["cra"].repo
        if await repo.get_credential_by_username("alice") is None:
            user = await local.create_user(repo, "alice", "Alice Example", "", "user")
            await repo.set_password_hash(user.id, await local.hash_password(PASSWORD))

    config = Config()
    config.bind = [f"127.0.0.1:{port}"]
    config.accesslog = None
    asyncio.run(serve(app, config))  # type: ignore[arg-type]


if __name__ == "__main__":
    main(int(sys.argv[1]), Path(sys.argv[2]))
