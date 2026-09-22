"""What a caller is allowed to see.

The tier travels with the tool and with the caller; the registry compares the
two. Enforcement lives there and never in a prompt: a tool a caller may not
reach is absent from the list it is offered, and calling it by name fails.
"""

from enum import StrEnum


class Tier(StrEnum):
    # paper metadata, abstracts, PI profiles, the collaboration graph, the map
    PUBLIC = "public"
    # full texts verbatim and the cluster proposal
    INTERNAL = "internal"

    def allows(self, required: "Tier") -> bool:
        return required is Tier.PUBLIC or self is Tier.INTERNAL
