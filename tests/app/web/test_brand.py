"""A deployment's brand: which of its files are served, what falls back to
the package's own, and what a broken brand does to startup."""

import json

import pytest
from conftest import make_settings

from cra.app.web.brand import DEFAULT_DIR, Brand, BrandError
from cra.app.web.factory import create_app

OWN_LOGO = '<svg xmlns="http://www.w3.org/2000/svg"><title>own</title></svg>'
PIPELINE = {
    "intro": "From sources to tools.",
    "stages": [{"key": "src", "label": "Sources"}, {"key": "tool", "label": "Tools"}],
    "nodes": [
        {"id": "csv", "stage": "src", "name": "papers.csv"},
        {"id": "search", "stage": "tool", "name": "search_papers"},
    ],
    "edges": [["csv", "search"]],
}


def write_brand(root, **files: str):
    root.mkdir(exist_ok=True)
    for name, content in files.items():
        (root / name).write_text(content)
    return root


@pytest.fixture
async def branded(tmp_path):
    """An app whose brand has a light square logo, a manifest, and nothing else."""
    brand = write_brand(
        tmp_path / "brand",
        **{
            "logo-square-light.svg": OWN_LOGO,
            "brand.json": json.dumps(
                {
                    "examples": ["Only this?"],
                    "institutions": [{"key": "TUM", "label": "TUM"}],
                    "pipeline": PIPELINE,
                }
            ),
        },
    )
    app = create_app(make_settings(tmp_path, brand_dir=brand))
    async with app.test_app():
        yield app


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("logo-square-light.svg", OWN_LOGO),
        # the brand's own light logo beats the package's dark one
        ("logo-square-dark.svg", OWN_LOGO),
        ("favicon.svg", OWN_LOGO),
        ("theme.css", (DEFAULT_DIR / "theme.css").read_text()),
    ],
)
async def test_a_brand_file_comes_from_the_brand_or_else_the_package(
    branded, name, expected
):
    response = await branded.test_client().get(f"/brand/{name}")
    assert response.status_code == 200
    assert await response.get_data(as_text=True) == expected


@pytest.mark.parametrize("name", ["logo-wide-light.svg", "brand.py", "settings.py"])
async def test_a_file_neither_brand_offers_is_not_found(branded, name):
    assert (await branded.test_client().get(f"/brand/{name}")).status_code == 404


async def test_the_pages_read_the_manifest_without_its_examples(branded):
    manifest = await (await branded.test_client().get("/brand/brand.json")).get_json()
    assert manifest["institutions"] == [{"key": "TUM", "label": "TUM", "name": ""}]
    assert manifest["pipeline"]["edges"] == [["csv", "search"]]
    assert "examples" not in manifest


async def test_the_landing_page_offers_the_brands_own_examples(branded):
    config = await (await branded.test_client().get("/api/config")).get_json()
    assert config["examples"] == ["Only this?"]


@pytest.mark.parametrize(
    ("wide", "header"),
    [
        (True, "brand/logo-wide-light.svg"),
        (False, '<span class="brand-name">Atlas</span>'),
    ],
    ids=["wide logo carries the name", "square mark beside the name"],
)
async def test_the_header_shows_the_name_only_without_a_wide_logo(
    tmp_path, wide, header
):
    files = {"logo-wide-light.svg": OWN_LOGO} if wide else {}
    brand = write_brand(tmp_path / "brand", **files)
    app = create_app(
        make_settings(tmp_path, brand_dir=brand, cluster_display_name="Atlas")
    )
    async with app.test_app():
        page = await (await app.test_client().get("/")).get_data(as_text=True)
    assert "<title>Atlas</title>" in page
    assert header in page


@pytest.mark.parametrize("pipeline", [None, PIPELINE], ids=["without", "with"])
async def test_the_pipeline_map_is_offered_only_when_described(tmp_path, pipeline):
    brand = write_brand(
        tmp_path / "brand", **{"brand.json": json.dumps({"pipeline": pipeline})}
    )
    app = create_app(make_settings(tmp_path, brand_dir=brand))
    async with app.test_app():
        page = await (await app.test_client().get("/")).get_data(as_text=True)
    assert ('id="pipeline-box"' in page) == (pipeline is not None)


@pytest.mark.parametrize(
    "manifest",
    [
        "{not json",
        json.dumps({"colour": "red"}),
        json.dumps({"pipeline": {**PIPELINE, "edges": [["csv", "nowhere"]]}}),
        json.dumps(
            {
                "pipeline": {
                    **PIPELINE,
                    "nodes": [{"id": "x", "stage": "?", "name": "x"}],
                }
            }
        ),
    ],
    ids=["malformed", "unknown key", "edge to no node", "node in no stage"],
)
def test_a_broken_manifest_stops_the_server_from_starting(tmp_path, manifest):
    brand = write_brand(tmp_path / "brand", **{"brand.json": manifest})
    with pytest.raises(BrandError, match="brand.json"):
        create_app(make_settings(tmp_path, brand_dir=brand))


def test_the_version_follows_the_files_so_browsers_refetch(tmp_path):
    brand = write_brand(tmp_path / "brand", **{"theme.css": ":root {}"})
    before = Brand.load(brand).version
    (brand / "theme.css").write_text(":root { --accent: red; }")
    assert Brand.load(brand).version != before
