"""Colours for the map.

Tableau 20, saturated hues first, so a map with few clusters gets the most
distinguishable ones.
"""

PALETTE = tuple(
    tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))
    for colour in (
        "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
        "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#79706E",
        "#9ECAE9", "#FFBF79", "#88D27A", "#FF9D98", "#83BCB6",
        "#F2CF5B", "#D6A5C9", "#D67195", "#D8B5A5", "#BAB0AC",
    )
)  # fmt: skip


def colour(index: int) -> list[int]:
    return list(PALETTE[index % len(PALETTE)])
