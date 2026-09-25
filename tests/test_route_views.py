"""The publication map, and placing a DOI the library may not have."""

import httpx
import pytest
import respx
from conftest import make_settings, sign_in
from fakes import FakeEncoder
from library_builder import write_library

from cra.app.web.factory import create_app
from cra.core.retrieval.indexes import Indexes

CROSSREF = "https://api.crossref.org"
OUTSIDE = "10.1038/s41586-021-03819-2"
WORK_URL = f"{CROSSREF}/works/10.1038%2Fs41586-021-03819-2"
ALEX_URL = f"https://api.openalex.org/works/doi:{OUTSIDE}"
INSIDE = "10.1000/beta"


def crossref_ok(abstract="<jats:p>Perovskite films for solar cells.</jats:p>"):
    return httpx.Response(
        200,
        json={
            "message": {
                "DOI": OUTSIDE,
                "title": ["A paper from elsewhere"],
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "container-title": ["Nature"],
                "issued": {"date-parts": [[2021]]},
                "abstract": abstract,
            }
        },
    )


@pytest.fixture
async def app(tmp_path):
    """A library whose vectors and encoder agree, so placement is possible.

    The shared toy library carries real 384-dimension vectors, which the fake
    encoder cannot match; this builder writes the 8-dimension pair instead.
    """
    root = tmp_path / "lib"
    write_library(root)
    made = create_app(make_settings(tmp_path, library_path=root))
    async with made.test_app():
        ctx = made.extensions["cra"]
        ctx.indexes = Indexes.build(ctx.library, FakeEncoder())
        yield made


@pytest.fixture
async def client(app):
    made = app.test_client()
    await sign_in(app, made)
    return made


async def lookup(client, doi):
    response = await client.post("/api/publication-map/lookup", json={"doi": doi})
    return response.status_code, await response.get_json()


# ---------- the map payload ----------


async def test_every_point_carries_a_citation_and_its_surnames(client):
    body = await (await client.get("/api/publication-map")).get_json()
    assert body["available"]
    point = next(p for p in body["points"] if p["doi"] == INSIDE)
    assert point["cite"]
    assert point["cite"] != INSIDE
    assert point["au"]
    assert {"doi", "title", "year", "x", "y", "cluster", "color"} <= point.keys()


# ---------- a paper the library already has ----------


@respx.mock
async def test_a_library_paper_is_answered_without_asking_anyone(client):
    route = respx.get(WORK_URL)
    status, body = await lookup(client, INSIDE)
    assert status == 200
    assert body["in_library"] is True
    assert body["source"] == "library"
    assert not route.called
    assert body["charged"] is False


async def test_a_library_paper_keeps_the_position_the_map_shows(client):
    """The one test that would catch the projection being mirrored."""
    payload = await (await client.get("/api/publication-map")).get_json()
    shown = next(p for p in payload["points"] if p["doi"] == INSIDE)
    _, body = await lookup(client, INSIDE)
    assert (body["point"]["x"], body["point"]["y"]) == (shown["x"], shown["y"])


async def test_a_doi_is_recognised_however_it_was_pasted(client):
    for written in (f"https://doi.org/{INSIDE}", f"doi:{INSIDE}", INSIDE.upper()):
        status, body = await lookup(client, written)
        assert status == 200, written
        assert body["point"]["doi"] == INSIDE


# ---------- a paper from outside ----------


@respx.mock
async def test_an_outside_paper_is_placed_among_its_neighbours(client):
    respx.get(WORK_URL).mock(return_value=crossref_ok())
    status, body = await lookup(client, OUTSIDE)
    assert status == 200
    assert body["in_library"] is False
    assert body["has_abstract"] is True
    assert body["neighbours"]
    assert body["point"]["cite"].startswith("Lovelace, ")


@respx.mock
async def test_the_placed_point_sits_among_the_neighbours_it_was_placed_from(client):
    """Mirrored coordinates would put it outside their span."""
    respx.get(WORK_URL).mock(return_value=crossref_ok())
    _, body = await lookup(client, OUTSIDE)
    ys = [n["y"] for n in body["neighbours"]]
    xs = [n["x"] for n in body["neighbours"]]
    assert min(ys) <= body["point"]["y"] <= max(ys)
    assert min(xs) <= body["point"]["x"] <= max(xs)


@respx.mock
async def test_a_missing_abstract_is_reported_rather_than_hidden(client):
    respx.get(WORK_URL).mock(return_value=crossref_ok(abstract=None))
    respx.get(ALEX_URL).mock(return_value=httpx.Response(404))
    status, body = await lookup(client, OUTSIDE)
    assert status == 200
    assert body["has_abstract"] is False


@respx.mock
async def test_openalex_fills_an_abstract_crossref_lacks(client):
    respx.get(WORK_URL).mock(return_value=crossref_ok(abstract=None))
    respx.get(ALEX_URL).mock(
        return_value=httpx.Response(
            200, json={"abstract_inverted_index": {"Solar": [0], "cells": [1]}}
        )
    )
    status, body = await lookup(client, OUTSIDE)
    assert status == 200
    assert body["has_abstract"] is True
    assert body["source"] == "crossref+openalex"


# ---------- failures ----------


@pytest.mark.parametrize(
    "doi", ["", "not-a-doi", "10.x/bad", "https://example.org/thing", "10." + "9" * 300]
)
async def test_a_doi_that_is_not_one_is_refused_before_anything_is_sent(client, doi):
    status, body = await lookup(client, doi)
    assert status == 400
    assert body["reason"] == "invalid_doi"


@respx.mock
async def test_an_unknown_doi_says_so(client):
    respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    respx.get(ALEX_URL).mock(return_value=httpx.Response(404))
    status, body = await lookup(client, OUTSIDE)
    assert status == 404
    assert body["reason"] == "not_found"


@respx.mock
async def test_an_unreachable_source_is_a_bad_gateway(client):
    respx.get(WORK_URL).mock(side_effect=httpx.ConnectError("no route"))
    respx.get(ALEX_URL).mock(side_effect=httpx.ConnectError("no route"))
    status, body = await lookup(client, OUTSIDE)
    assert status == 502
    assert body["reason"] == "unavailable"


@respx.mock
async def test_a_record_with_no_words_cannot_be_placed(client):
    respx.get(WORK_URL).mock(
        return_value=httpx.Response(200, json={"message": {"DOI": OUTSIDE}})
    )
    respx.get(ALEX_URL).mock(return_value=httpx.Response(404))
    status, body = await lookup(client, OUTSIDE)
    assert status == 422
    assert body["reason"] == "no_text"


@respx.mock
async def test_an_upstream_throttle_is_not_reported_as_our_own_limit(client):
    respx.get(WORK_URL).mock(return_value=httpx.Response(429))
    respx.get(ALEX_URL).mock(return_value=httpx.Response(429))
    status, body = await lookup(client, OUTSIDE)
    assert status == 503
    assert body["reason"] == "rate_limited"


@respx.mock
async def test_our_own_daily_limit_is_a_429_that_says_so(app):
    app.extensions["cra"].policy.set("user_lookup_daily_limit", 1)
    client = app.test_client()
    await sign_in(app, client)
    respx.get(WORK_URL).mock(return_value=crossref_ok())
    assert (await lookup(client, OUTSIDE))[0] == 200
    status, body = await lookup(client, "10.1234/second")
    assert status == 429
    assert "limit" in body["error"]
    assert body["retry_after"] > 0


@respx.mock
async def test_the_allowance_is_not_spent_on_papers_we_already_have(app):
    app.extensions["cra"].policy.set("user_lookup_daily_limit", 1)
    client = app.test_client()
    await sign_in(app, client)
    respx.get(WORK_URL).mock(return_value=crossref_ok())
    for _ in range(3):
        assert (await lookup(client, INSIDE))[0] == 200
    # The one allowed outside lookup is still available.
    assert (await lookup(client, OUTSIDE))[0] == 200


async def test_lookup_needs_a_signed_in_caller(app):
    anonymous = app.test_client()
    response = await anonymous.post(
        "/api/publication-map/lookup", json={"doi": OUTSIDE}
    )
    assert response.status_code == 401


async def test_a_deployment_without_an_encoder_says_what_is_missing(tmp_path):
    root = tmp_path / "lib"
    write_library(root)
    made = create_app(make_settings(tmp_path, library_path=root))
    async with made.test_app():
        client = made.test_client()
        await sign_in(made, client)
        status, body = await lookup(client, OUTSIDE)
    assert status == 503
    assert body["reason"] == "semantic_unavailable"
