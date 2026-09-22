"""Build the derived corpus artifacts.

Everything expensive is computed here, at bundle-build time, not while serving.
The 2-D projection alone costs about 25 seconds on a thousand papers, most of
it importing umap, so `umap` and `scikit-learn` are build-time dependencies
(the ``build`` extra) that the serving image never installs.
"""

import logging
import warnings
from typing import Any

import numpy as np

MIN_CLUSTERS, MAX_CLUSTERS, DEFAULT_CLUSTERS = 2, 20, 8
TERMS_PER_LABEL = 3
PROJECTION = {"n_neighbors": 15, "min_dist": 0.1, "metric": "cosine", "seed": 42}
# four decimals on a projection whose span is a few dozen units keeps the file
# small and is far below the visual resolution of the map
COORD_DECIMALS = 4

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


def label_clusters(
    titles: list[str], assignments: np.ndarray, n_clusters: int
) -> list[str]:
    """Top TF-IDF terms of each cluster's concatenated titles.

    TF-IDF rather than raw counts, so corpus-wide fillers such as "properties"
    do not label every cluster; sublinear_tf damps one verbose title.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer

    docs = [""] * n_clusters
    for title, c in zip(titles, assignments, strict=True):
        docs[c] += " " + title
    try:
        tfidf = TfidfVectorizer(stop_words="english", sublinear_tf=True, min_df=1)
        matrix = tfidf.fit_transform(docs)
    except ValueError:  # every title was a stop word, or there are no titles
        return [f"cluster {c + 1}" for c in range(n_clusters)]
    vocab = np.array(tfidf.get_feature_names_out())
    labels = []
    for c in range(n_clusters):
        row = matrix[c].toarray().ravel()
        top = vocab[np.argsort(-row)[:TERMS_PER_LABEL]]
        labels.append(
            " / ".join(t for t in top if row[vocab.tolist().index(t)] > 0)
            or f"cluster {c + 1}"
        )
    return labels


def build_map(
    dois: tuple[str, ...],
    vectors: np.ndarray,
    titles: dict[str, str],
    model: str = "",
) -> dict[str, Any]:
    """Projection plus every clustering the UI can ask for, ready to serve."""
    from sklearn.cluster import KMeans

    xy = project(vectors)
    ordered_titles = [titles.get(d, "") for d in dois]
    clusters = {}
    for k in cluster_counts(len(dois)):
        assignments = KMeans(
            n_clusters=k, n_init=10, random_state=PROJECTION["seed"]
        ).fit_predict(vectors)
        clusters[str(k)] = {
            "assignments": [int(a) for a in assignments],
            "labels": label_clusters(ordered_titles, assignments, k),
        }
        log.info("clustered", extra={"fields": {"k": k}})
    return {
        "projection": "umap",
        "params": PROJECTION,
        "model": model,
        "dois": list(dois),
        "x": [round(float(v), COORD_DECIMALS) for v in xy[:, 0]],
        "y": [round(float(v), COORD_DECIMALS) for v in xy[:, 1]],
        "clusters": clusters,
    }
