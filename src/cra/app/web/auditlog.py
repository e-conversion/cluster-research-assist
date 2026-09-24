"""Who changed what about whom.

Access decisions are the one thing the log has to be able to answer for
afterwards: an admin changing an account, and anyone minting or revoking a
token or deleting their own account.
"""

import logging
from typing import Any

from quart import g

log = logging.getLogger(__name__)


def record(action: str, **fields: Any) -> None:
    log.info(
        "audit",
        extra={"fields": {"action": action, "by": g.principal.user_id, **fields}},
    )
