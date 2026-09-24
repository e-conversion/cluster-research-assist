"""Questions offered on an empty page.

They are the ones worth asking of this assistant rather than of a search box:
each needs several tools, or the library as a whole, to answer. A deployment
replaces them with its own in its brand's ``brand.json``; these name no one,
so they fit any cluster.
"""

import random
from collections.abc import Sequence

QUESTIONS = (
    "How many publications does the library contain?",
    "Which groups work with electronic structure theory?",
    "Which paper has the most co-authors from within the cluster?",
    "How many papers appeared in Nature journals?",
    "What are the main open scientific challenges across the cluster's papers?",
    "Which papers apply machine learning to experimental data, and to which techniques?",
    "Which two groups are the most complementary, one producing what the other could use?",
    "Which topics appear in the group descriptions but have few corresponding papers?",
    "Which experimental groups have published together with theory groups?",
    "How has the share of machine-learning papers changed over the years?",
    "Which topics appear in papers before 2020 but not since, and the other way round?",
    "Has collaboration within the cluster become denser over time?",
    "Who bridges otherwise disconnected groups?",
    "Are there groups that collaborate only internally?",
    "Who has collaborated with the most different groups?",
)
SHOWN = 4


def some(questions: Sequence[str] = QUESTIONS, count: int = SHOWN) -> list[str]:
    """A different few each time, so the page does not always suggest the same."""
    return random.sample(list(questions), min(count, len(questions)))
