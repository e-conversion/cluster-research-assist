"""Stand-ins for the eLabFTW and DataTagger MCP proxies, with their latency.

Every token is accepted; the read tools answer after a pause like the real
proxies' upstream calls.

    python developer/loadtest/mock_sources.py elab --port 8801
    python developer/loadtest/mock_sources.py dt --port 8802
"""

import argparse
import asyncio
import random

from mcp.server.mcpserver import MCPServer

UPSTREAM_S = (0.2, 0.8)


def elab(server: MCPServer) -> None:
    @server.tool()
    async def list_experiments(query: str = "", limit: int = 10) -> list[dict]:
        """Search or list experiments."""
        await asyncio.sleep(random.uniform(*UPSTREAM_S))
        return [
            {"id": n, "title": f"{query or 'Experiment'} run {n}", "date": "2026-09-01"}
            for n in range(min(limit, 10))
        ]

    @server.tool()
    async def get_experiment(experiment_id: int) -> dict:
        """Fetch one experiment."""
        await asyncio.sleep(random.uniform(*UPSTREAM_S))
        return {"id": experiment_id, "title": "TGA of MOF-5", "body": "…" * 200}


def dt(server: MCPServer) -> None:
    @server.tool()
    async def list_projects() -> list[dict]:
        """List projects."""
        await asyncio.sleep(random.uniform(*UPSTREAM_S))
        return [{"id": n, "name": f"Project {n}"} for n in range(5)]

    @server.tool()
    async def list_datasets(folder_id: int | None = None) -> list[dict]:  # noqa: ARG001 -- the real tool's signature
        """List datasets."""
        await asyncio.sleep(random.uniform(*UPSTREAM_S))
        return [{"id": n, "name": f"Dataset {n}"} for n in range(8)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("kind", choices=("elab", "dt"))
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = MCPServer(args.kind)
    {"elab": elab, "dt": dt}[args.kind](server)
    server.run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
