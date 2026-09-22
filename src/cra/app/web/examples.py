"""Questions offered on an empty page.

They are the ones worth asking of this assistant rather than of a search box:
each needs several tools, or the library as a whole, to answer.
"""

import random

QUESTIONS = (
    "How many publications does the library contain?",
    "Which groups work with electronic structure theory?",
    "What is the research focus of Prof. Rinke's group?",
    "Which paper has the most co-authors from within the cluster?",
    "How many papers appeared in Nature journals?",
    "Which groups work on topics similar to Prof. Rinke, judging by their abstracts?",
    "What are the main open scientific challenges across the cluster's papers?",
    "Which papers should someone new read to understand interfaces in energy conversion?",
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
    "I have a model that predicts battery degradation. Who could test it on real devices?",
    "Who works on perovskite stability, and what have they found?",
)
SHOWN = 4


def some(count: int = SHOWN) -> list[str]:
    """A different few each time, so the page does not always suggest the same."""
    return random.sample(QUESTIONS, min(count, len(QUESTIONS)))
