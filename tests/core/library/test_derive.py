"""The map's groups: one tree, described by the words their papers share."""

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("sklearn")

from cra.core.library import derive

TOPICS = [
    "perovskite solar cell",
    "copper co2 reduction",
    "lithium battery",
    "dna origami",
]


@pytest.fixture
def built(monkeypatch):
    """Four well-separated topics of ten papers each; umap is not needed here."""
    monkeypatch.setattr(derive, "project", lambda v, *_: np.zeros((len(v), 2)))
    rng = np.random.default_rng(0)
    centres = rng.normal(size=(len(TOPICS), 16)) * 5
    vectors = np.vstack([c + rng.normal(scale=0.3, size=(10, 16)) for c in centres])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    dois = tuple(f"10.1000/{t}.{i}" for t in range(len(TOPICS)) for i in range(10))
    titles = {
        d: f"{TOPICS[int(d.split('/')[1].split('.')[0])]} study {i}"
        for i, d in enumerate(dois)
    }
    return dois, derive.build_map(
        dois, vectors, titles, abstracts={d: "We report results." for d in dois}
    )


def partition(payload, k):
    entry = payload["clusters"][str(k)]
    return {
        frozenset(i for i, a in enumerate(entry["assignments"]) if a == slot)
        for slot in range(k)
    }


def test_each_step_splits_one_group_and_leaves_the_others(built):
    _, payload = built
    counts = sorted(int(k) for k in payload["clusters"])
    for k in counts[:-1]:
        before, after = partition(payload, k), partition(payload, k + 1)
        kept = before & after
        assert len(kept) == k - 1
        (split,) = before - kept
        assert len(after - kept) == 2
        assert frozenset().union(*(after - kept)) == split


def test_a_group_keeps_its_slot_until_it_splits(built):
    _, payload = built
    parent = {g["id"]: g["parent"] for g in payload["groups"]}
    counts = sorted(int(k) for k in payload["clusters"])
    for k in counts[:-1]:
        now, then = (
            payload["clusters"][str(k)]["groups"],
            payload["clusters"][str(k + 1)]["groups"],
        )
        for slot, group in enumerate(now):
            assert then[slot] == group or parent[then[slot]] == group


def test_every_group_is_stored_once(built):
    _, payload = built
    ids = [g["id"] for g in payload["groups"]]
    assert len(ids) == len(set(ids)) == 2 * (len(payload["clusters"]))


def test_a_topic_is_named_by_the_words_all_its_papers_share(built):
    _, payload = built
    groups = {g["id"]: g for g in payload["groups"]}
    entry = payload["clusters"]["4"]
    for slot, group_id in enumerate(entry["groups"]):
        group = groups[group_id]
        assert group["coverage"] == 1.0
        assert entry["labels"][slot] == group["name"] == " / ".join(group["terms"])
        assert any(
            term in TOPICS[0] + TOPICS[1] + TOPICS[2] + TOPICS[3]
            for term in group["terms"]
        )
        assert "study" not in group["terms"]  # in every paper, so it names none


def test_centrality_runs_from_the_centre_to_the_edge(built):
    _, payload = built
    entry = payload["clusters"]["4"]
    for slot in range(4):
        ranks = [
            r
            for r, a in zip(entry["centrality"], entry["assignments"], strict=True)
            if a == slot
        ]
        assert min(ranks) == 0
        assert max(ranks) == 100


def test_the_papers_shown_for_a_group_are_its_own(built):
    _, payload = built
    entry = payload["clusters"]["4"]
    dois = payload["dois"]
    for slot, group_id in enumerate(entry["groups"]):
        group = next(g for g in payload["groups"] if g["id"] == group_id)
        members = {dois[i] for i, a in enumerate(entry["assignments"]) if a == slot}
        assert set(group["central"]) <= members
        assert len(group["central"]) == min(derive.CENTRAL_PAPERS, len(members))


def test_a_library_smaller_than_the_slider_gets_one_paper_per_group_at_the_top(
    monkeypatch,
):
    monkeypatch.setattr(derive, "project", lambda v, *_: np.zeros((len(v), 2)))
    vectors = np.random.default_rng(1).normal(size=(6, 16))
    dois = tuple(f"10.1000/small.{i}" for i in range(6))
    payload = derive.build_map(dois, vectors, {d: f"study {d}" for d in dois})
    assert list(payload["clusters"]) == [str(k) for k in range(2, 7)]
    for k in range(2, 7):
        groups = partition(payload, k)
        assert len(groups) == k
        assert all(groups)
    assert all(g["size"] for g in payload["groups"])


def test_a_rare_word_and_a_repeated_one_do_not_describe_a_group():
    vocab = [{"co2", "co2 reduction", "copper"} for _ in range(19)] + [
        {"co2", "zeolite"}
    ]
    vocab += [{"lithium"} for _ in range(20)]
    share = {t: sum(t in v for v in vocab) / len(vocab) for t in set().union(*vocab)}
    terms, coverage = derive.describe(list(range(20)), vocab, share)
    assert "zeolite" not in terms  # one paper in twenty
    assert not ({"co2", "co2 reduction"} <= set(terms))
    assert coverage == 1.0


def test_names_stay_unique_within_a_clustering():
    payload = {
        "groups": [
            {"id": "g0", "name": "Solar cells", "terms": ["perovskite"]},
            {"id": "g1", "name": "Solar cells", "terms": ["organic"]},
        ],
        "clusters": {"2": {"groups": ["g0", "g1"]}},
    }
    derive.relabel(payload)
    assert payload["clusters"]["2"]["labels"] == [
        "Solar cells",
        "Solar cells (organic)",
    ]
