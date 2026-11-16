"""Build the derived library artifacts.

Everything expensive is computed here, at bundle-build time, not while serving.
The 2-D projection alone costs about 25 seconds on a thousand papers, most of
it importing umap, so `umap` and `scikit-learn` are build-time dependencies
(the ``build`` extra) that the serving image never installs.
"""

import hashlib
import logging
import math
import re
import warnings
from collections import Counter
from itertools import pairwise
from typing import Any

import numpy as np

from cra.core.library.text import fold

MIN_CLUSTERS, MAX_CLUSTERS, DEFAULT_CLUSTERS = 2, 20, 8
TERMS_PER_LABEL = 3
# A word fewer than this share of a group's papers contain does not describe
# the group: it is how "MoS2" came to name a group four in five of whose
# papers never mention it.
MIN_COVERAGE = 0.1
# The papers a group is shown by, to a reader of the build and to the model
# that names it: the ones nearest its centre.
CENTRAL_PAPERS = 8
PROJECTION = {"n_neighbors": 15, "min_dist": 0.1, "metric": "cosine", "seed": 42}
# four decimals on a projection whose span is a few dozen units keeps the file
# small and is far below the visual resolution of the map
COORD_DECIMALS = 4

_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Words most abstracts in any field use; on their own they name nothing.
_FILLERS = frozenset(
    {
        "using", "based", "study", "studies", "new", "novel", "high", "low", "via",
        "effect", "effects", "properties", "performance", "approach", "analysis",
        "result", "results", "show", "shown", "present", "work", "different",
        "significant", "significantly", "demonstrate", "reveal", "provide",
    }
)  # fmt: skip

log = logging.getLogger(__name__)


def cluster_counts(n_papers: int) -> list[int]:
    return list(range(MIN_CLUSTERS, min(MAX_CLUSTERS, n_papers) + 1))


def project(vectors: np.ndarray, params: dict[str, Any] | None = None) -> np.ndarray:
    import umap

    params = {**PROJECTION, **(params or {})}
    reducer = umap.UMAP(
        n_components=2,
        # n_neighbors must stay below the sample count for tiny corpora
        n_neighbors=min(int(params["n_neighbors"]), max(2, len(vectors) - 1)),
        min_dist=float(params["min_dist"]),
        metric=str(params["metric"]),
        random_state=int(params["seed"]),
    )
    with warnings.catch_warnings():
        # a seed disables umap's parallelism; a bundle that rebuilds identically
        # is worth more here than a few seconds of build time
        warnings.filterwarnings("ignore", message="n_jobs value .* overridden")
        return np.asarray(reducer.fit_transform(vectors), dtype=np.float64)


def nest(vectors: np.ndarray, counts: list[int]) -> np.ndarray:
    """Group memberships for every count, one column per count.

    All are cut from one Ward tree, so each count splits exactly one group of
    the count before and leaves the others as they were. Clustering every
    count afresh regrouped papers across the whole map at each step of the
    slider (adjusted Rand 0.66 to 0.78 between neighbouring counts on the
    e-conversion library), and a name could not follow its papers.
    """
    from scipy.cluster.hierarchy import cut_tree, linkage

    return np.asarray(cut_tree(linkage(vectors, method="ward"), n_clusters=counts))


def vocabularies(texts: list[str]) -> list[set[str]]:
    """Each paper's words and adjacent word pairs, plurals folded onto singulars."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    stop = ENGLISH_STOP_WORDS | _FILLERS
    tokenised = [
        [
            w
            for w in _WORD.findall(fold(t))
            if len(w) > 2 and not w.isdigit() and w not in stop
        ]
        for t in texts
    ]
    seen = {w for words in tokenised for w in words}

    def singular(word: str) -> str:
        stem = word[:-1]
        return (
            stem
            if word.endswith("s") and not word.endswith("ss") and stem in seen
            else word
        )

    out = []
    for words in tokenised:
        words = [singular(w) for w in words]
        out.append(set(words) | {f"{a} {b}" for a, b in pairwise(words)})
    return out


def describe(
    members: list[int], vocab: list[set[str]], library_share: dict[str, float]
) -> tuple[list[str], float]:
    """The terms most of a group's papers share and few others use, and the
    share of its papers that hold at least one of them.

    A term scores its share in the group times the log of how much more common
    it is there than in the library: common within the group, rare outside.
    """
    size = len(members)
    counts = Counter(term for i in members for term in vocab[i])
    scored = []
    for term, count in counts.items():
        share = count / size
        base = library_share[term]
        if share >= MIN_COVERAGE and share > base:
            scored.append((share * math.log(share / base), term))
    chosen: list[str] = []
    for _, term in sorted(scored, reverse=True):
        # "co2" beside "co2 reduction" says nothing twice
        if any(set(term.split()) & set(other.split()) for other in chosen):
            continue
        chosen.append(term)
        if len(chosen) == TERMS_PER_LABEL:
            break
    covered = sum(1 for i in members if vocab[i] & set(chosen))
    return chosen, round(covered / size, 2)


def relabel(payload: dict[str, Any]) -> None:
    """Each clustering's labels from its groups' names, unique within it."""
    groups = {g["id"]: g for g in payload["groups"]}
    for entry in payload["clusters"].values():
        labels: list[str] = []
        for group_id in entry["groups"]:
            group = groups[group_id]
            name = group["name"]
            if name in labels and group["terms"]:
                name = f"{name} ({group['terms'][0]})"
            if name in labels:
                name = f"{name} {len(labels) + 1}"
            labels.append(name)
        entry["labels"] = labels


def fingerprint(dois: list[str]) -> str:
    """Names a group by its papers, so a rebuild can tell an unchanged one."""
    return hashlib.sha256("\n".join(sorted(dois)).encode()).hexdigest()[:16]


def build_map(
    dois: tuple[str, ...],
    vectors: np.ndarray,
    titles: dict[str, str],
    model: str = "",
    abstracts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Projection plus every clustering the UI can ask for, ready to serve.

    The clusterings are the levels of one tree. Every group in it is stored
    once, under ``groups``, with the terms that describe it and the papers
    nearest its centre; a clustering lists its groups by colour slot, and a
    group keeps its slot until it splits, when its larger half keeps it.
    ``centrality`` places each paper within its group: 0 at the centre, 100
    the farthest member.
    """
    abstracts = abstracts or {}
    xy = project(vectors)
    counts = cluster_counts(len(dois))
    cuts = nest(vectors, counts)
    vocab = vocabularies([f"{titles.get(d, '')}. {abstracts.get(d, '')}" for d in dois])
    library_share = {
        term: count / len(dois)
        for term, count in Counter(t for terms in vocab for t in terms).items()
    }

    groups: list[dict[str, Any]] = []
    members_of: list[frozenset[int]] = []
    rank: list[dict[int, int]] = []  # per group: paper -> centrality

    def add(members: frozenset[int], parent: int | None) -> int:
        ordered = sorted(members)
        centre = vectors[ordered].mean(axis=0)
        distance = np.linalg.norm(vectors[ordered] - centre, axis=1)
        order = np.argsort(distance, kind="stable")
        span = max(len(ordered) - 1, 1)
        index = len(groups)
        rank.append({ordered[i]: round(100 * r / span) for r, i in enumerate(order)})
        members_of.append(members)
        terms, coverage = describe(ordered, vocab, library_share)
        groups.append(
            {
                "id": f"g{index}",
                "parent": None if parent is None else f"g{parent}",
                "size": len(ordered),
                "terms": terms,
                "coverage": coverage,
                "central": [dois[ordered[i]] for i in order[:CENTRAL_PAPERS]],
                "fingerprint": fingerprint([dois[i] for i in ordered]),
                "name": " / ".join(terms) or f"group {index + 1}",
                "named_by": "terms",
            }
        )
        return index

    clusters = {}
    slots: list[int] = []  # group per colour slot
    for column, k in enumerate(counts):
        cut = [
            frozenset(np.flatnonzero(cuts[:, column] == c).tolist()) for c in range(k)
        ]
        if not slots:
            slots = [add(m, None) for m in sorted(cut, key=len, reverse=True)]
        else:
            current = set(cut)
            known = set(members_of)
            for slot, parent in enumerate(list(slots)):
                if members_of[parent] in current:
                    continue
                halves = sorted(
                    (m for m in cut if m not in known and m <= members_of[parent]),
                    key=len,
                    reverse=True,
                )
                # the larger half keeps the parent's slot, so most papers keep their colour
                slots[slot] = add(halves[0], parent)
                slots.extend(add(m, parent) for m in halves[1:])
        position = {members_of[g]: s for s, g in enumerate(slots)}
        assignment: np.ndarray = np.empty(len(dois), dtype=int)
        for m in cut:
            assignment[sorted(m)] = position[m]
        clusters[str(k)] = {
            "assignments": assignment.tolist(),
            "groups": [f"g{g}" for g in slots],
            "centrality": [rank[slots[a]][i] for i, a in enumerate(assignment)],
        }
        log.info("clustered", extra={"fields": {"k": k}})
    payload = {
        "projection": "umap",
        "params": PROJECTION,
        "clustering": "ward",
        "model": model,
        "dois": list(dois),
        "x": [round(float(v), COORD_DECIMALS) for v in xy[:, 0]],
        "y": [round(float(v), COORD_DECIMALS) for v in xy[:, 1]],
        "groups": groups,
        "clusters": clusters,
    }
    relabel(payload)
    return payload
