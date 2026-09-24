"""Where a paper from outside the library would sit on the map.

The projection was fitted when the library was built and the reducer was not
kept, so there is no way to transform a new vector into the two-dimensional
space. What is left is interpolation: find the papers the new one is closest
to in the full embedding space, and place it among them.

That is an honest answer only if the neighbourhood is itself coherent. When
the nearest papers straddle two distant clusters, their centroid falls in the
empty gap between the clouds, which is the one place a reader would certainly
misread. The radius guard below exists for that case: it keeps the estimate
inside the nearest paper's own neighbourhood, and in the worst case collapses
onto that paper rather than inventing a position between clusters.
"""

from dataclasses import dataclass

import numpy as np

from cra.core.library.records import PublicationMap, normalise_doi
from cra.core.retrieval.dense import DenseIndex

K = 15
# The local scale is the distance to a handful of immediate neighbours, which
# is what "how far apart are papers around here" means. A larger rank reaches
# out of the neighbourhood, and on a small library spans the whole map, which
# would widen the radius below until it admitted everything and guarded
# nothing. The quarter-of-the-library clamp keeps that true for tiny maps.
LOCAL_SCALE_RANK = 8
RADIUS_FACTOR = 2.5
# bge-family cosines for related papers crowd into roughly 0.55-0.95, so a
# plain score weighting would be near-uniform and would drag the point toward
# the centroid of everything nearby. Subtracting the best score and going
# through an exponential restores the contrast the raw numbers lack.
TEMPERATURE = 0.02
# Above this the query is the same paper, not a neighbour of it.
DUPLICATE_AT = 0.98


@dataclass(frozen=True)
class Neighbour:
    doi: str
    score: float
    x: float
    y: float


@dataclass(frozen=True)
class Placement:
    """A position in *stored* map space; the viz layer owns the y flip."""

    x: float
    y: float
    confidence: float
    used: int
    duplicate_of: str | None
    neighbours: tuple[Neighbour, ...]


def coordinates(stored: PublicationMap) -> dict[str, tuple[float, float]]:
    return {doi: (stored.x[i], stored.y[i]) for i, doi in enumerate(stored.dois)}


def _local_scale(stored: PublicationMap, x: float, y: float) -> float:
    """Distance to the ``LOCAL_SCALE_RANK``-th nearest point on the map.

    UMAP layouts are wildly uneven in density, so a fixed radius would be too
    tight in a sparse region and useless in a crowded one.
    """
    xs = np.asarray(stored.x, dtype=np.float64)
    ys = np.asarray(stored.y, dtype=np.float64)
    distances = np.hypot(xs - x, ys - y)
    rank = min(LOCAL_SCALE_RANK, max(1, distances.size // 4), distances.size - 1)
    if rank < 1:
        return 1.0
    scale = float(np.partition(distances, rank)[rank])
    return scale or 1.0


def place(
    vector: np.ndarray,
    dense: DenseIndex,
    stored: PublicationMap,
    *,
    k: int = K,
    radius_factor: float = RADIUS_FACTOR,
    temperature: float = TEMPERATURE,
) -> Placement | None:
    """Estimate a position for ``vector``, or None when nothing maps."""
    positions = coordinates(stored)
    if not positions:
        return None
    # Over-fetch: a DOI may carry an embedding without being on the map.
    ranked = dense.search(vector, min(max(k * 3, k), len(dense)))
    kept = [
        Neighbour(h.doi, float(h.score), *positions[h.doi])
        for h in ranked
        if h.doi in positions
    ][:k]
    if not kept:
        return None

    anchor = kept[0]
    if anchor.score >= DUPLICATE_AT:
        # Effectively the same paper. Sitting exactly on top of it would hide
        # the marker, so nudge it by a fraction of the local spacing.
        offset = 0.15 * _local_scale(stored, anchor.x, anchor.y)
        return Placement(
            x=anchor.x + offset,
            y=anchor.y + offset,
            confidence=anchor.score,
            used=1,
            duplicate_of=anchor.doi,
            neighbours=tuple(kept),
        )

    radius = radius_factor * _local_scale(stored, anchor.x, anchor.y)
    near = [n for n in kept if np.hypot(n.x - anchor.x, n.y - anchor.y) <= radius]
    weights = np.exp(
        (np.array([n.score for n in near]) - anchor.score) / max(temperature, 1e-6)
    )
    total = float(weights.sum())
    if total <= 0:
        return Placement(anchor.x, anchor.y, anchor.score, 1, None, tuple(kept))
    x = float(np.dot(weights, [n.x for n in near]) / total)
    y = float(np.dot(weights, [n.y for n in near]) / total)
    return Placement(x, y, anchor.score, len(near), None, tuple(kept))


def place_known(
    doi: str, dense: DenseIndex, stored: PublicationMap, *, k: int = 8
) -> Placement | None:
    """The stored position of a library paper, plus its nearest neighbours."""
    doi = normalise_doi(doi)
    positions = coordinates(stored)
    if doi not in positions:
        return None
    x, y = positions[doi]
    hits = dense.similar(doi, k) or []
    neighbours = tuple(
        Neighbour(h.doi, float(h.score), *positions[h.doi])
        for h in hits
        if h.doi in positions
    )
    return Placement(x, y, 1.0, len(neighbours), None, neighbours)
