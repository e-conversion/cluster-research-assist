"""The map as the browser receives it."""

import json

from library_builder import write_library

from cra.app.viz.palette import colour
from cra.app.viz.publication_map import payload
from cra.core.library.library import Library


def nested(tmp_path):
    directory = write_library(tmp_path / "library")
    path = directory / "publication_map.json"
    raw = json.loads(path.read_text())
    raw["groups"] = [
        {"id": "g0", "size": 1, "terms": ["perovskite"], "coverage": 1.0},
        {"id": "g1", "size": 2, "terms": ["copper"], "coverage": 1.0},
    ]
    raw["clusters"]["2"].update(groups=["g0", "g1"], centrality=[0, 0, 100])
    path.write_text(json.dumps(raw))
    return Library.load(directory, verify=False)


def test_a_group_is_coloured_by_its_slot_and_says_what_it_shares(tmp_path):
    body = payload(nested(tmp_path), 2)
    assert body["legend"] == [
        {
            "cluster": "perovskite",
            "color": colour(0),
            "hint": "Common words: perovskite (in 100% of its 1 papers)",
        },
        {
            "cluster": "copper",
            "color": colour(1),
            "hint": "Common words: copper (in 100% of its 2 papers)",
        },
    ]


def test_only_a_paper_on_its_groups_edge_is_marked(tmp_path):
    points = {p["doi"]: p for p in payload(nested(tmp_path), 2)["points"]}
    assert points["10.1000/gamma"]["edge"] is True
    assert "edge" not in points["10.1000/alpha"]


def test_a_map_built_before_groups_were_nested_still_serves(tmp_path):
    library = Library.load(write_library(tmp_path / "library"))
    body = payload(library, 3)
    assert [e["cluster"] for e in body["legend"]] == ["perovskite", "copper", "battery"]
    assert all(e["hint"] == "" for e in body["legend"])
    assert not any("edge" in p for p in body["points"])
