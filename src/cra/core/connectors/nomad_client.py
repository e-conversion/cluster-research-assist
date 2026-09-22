"""The public NOMAD repository of computed and measured materials data.

Data that is external to the cluster: entries here are not the data behind a
cluster paper, and there is no link between the two. Unauthenticated, so the
search sees public entries only.
"""

from typing import Any

import httpx

TIMEOUT = httpx.Timeout(45.0, connect=10.0)
PAGE_LIMIT = 50
FIELDS = [
    "entry_id",
    "upload_name",
    "references",
    "authors.name",
    "results.material.chemical_formula_reduced",
    "results.material.elements",
    "results.method.simulation.program_name",
    "entry_type",
    "upload_create_time",
]
# titles a depositor's name may carry, which NOMAD stores without them
TITLES = ("prof", "dr", "professor", "phd", "mr", "mrs", "ms")


def strip_titles(name: str) -> str:
    parts = [
        p for p in name.replace(".", " ").split() if p.lower().strip(".") not in TITLES
    ]
    return " ".join(parts)


def build_query(
    elements: str = "", formula: str = "", author: str = "", text: str = ""
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    chosen = [e.strip() for e in elements.split(",") if e.strip()]
    if chosen:
        query["results.material.elements"] = {"all": chosen}
    if formula.strip():
        query["results.material.chemical_formula_reduced"] = formula.strip()
    if author.strip():
        query["authors.name"] = strip_titles(author.strip())
    if text.strip():
        query["text_search_contents"] = text.strip()
    return query


async def search(
    client: httpx.AsyncClient,
    base_url: str,
    gui_url: str,
    *,
    elements: str = "",
    formula: str = "",
    author: str = "",
    text: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    query = build_query(elements, formula, author, text)
    if not query:
        return {"error": "Give at least one of elements, formula, author or text."}
    body = {
        "query": query,
        "pagination": {
            "page_size": max(1, min(limit, PAGE_LIMIT)),
            # relevance only means something for a text search
            "order_by": "_score" if text.strip() else "upload_create_time",
            "order": "desc",
        },
        "required": {"include": FIELDS},
    }
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/entries/query", json=body, timeout=TIMEOUT
        )
    except httpx.HTTPError as exc:
        return {"error": f"NOMAD is not reachable: {exc}"}
    if response.status_code == 422:
        return {
            "error": "NOMAD rejected the query; check the formula or element symbols."
        }
    if response.status_code != 200:
        return {"error": f"NOMAD answered with HTTP {response.status_code}."}

    payload = response.json()
    entries = []
    for entry in payload.get("data", []):
        material = (entry.get("results") or {}).get("material") or {}
        entries.append(
            {
                "entry_id": entry.get("entry_id"),
                "formula": material.get("chemical_formula_reduced"),
                "elements": material.get("elements", []),
                "authors": [a.get("name") for a in entry.get("authors", [])],
                "upload_name": entry.get("upload_name"),
                "entry_type": entry.get("entry_type"),
                "created": entry.get("upload_create_time"),
                "url": gui_url.format(entry.get("entry_id")) if gui_url else None,
            }
        )
    return {
        "total_matches": (payload.get("pagination") or {}).get("total", len(entries)),
        "returned": len(entries),
        "entries": entries,
    }
