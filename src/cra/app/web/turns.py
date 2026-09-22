"""One turn at a time per session, and a way out when one will not stop.

Cancelling is cooperative: a turn notices between chunks and between tool
calls. A turn stuck in a slow tool would therefore lock someone out of their
own session, so a new question displaces the one sitting there instead of being
refused. Epochs stop a displaced turn from claiming the session back.
"""

import asyncio
from dataclasses import dataclass, field

# How long a displaced turn is given to notice and hand over.
HANDOVER_S = 1.5


@dataclass
class TurnSlot:
    epoch: int = 0
    busy: bool = False
    cancel: asyncio.Event | None = field(default=None, repr=False)

    async def start(self, handover_s: float = HANDOVER_S) -> tuple[int, asyncio.Event]:
        """Claim the slot, displacing whoever holds it."""
        if self.busy and self.cancel is not None:
            self.cancel.set()
            for _ in range(int(handover_s * 20)):
                await asyncio.sleep(0.05)
                if not self.busy:
                    break
        self.epoch += 1
        self.busy = True
        self.cancel = asyncio.Event()
        return self.epoch, self.cancel

    def finish(self, epoch: int) -> bool:
        """Release the slot, unless someone else has taken it since."""
        if epoch != self.epoch:
            return False
        self.busy = False
        self.cancel = None
        return True

    def stop(self) -> bool:
        """Ask the running turn to stop. It keeps the slot until it does."""
        if self.cancel is None:
            return False
        self.cancel.set()
        return True

    def abandon(self) -> bool:
        """Free the slot outright, for starting a new conversation."""
        stopped = self.stop()
        self.busy = False
        self.epoch += 1
        return stopped


class TurnSlots:
    """One slot per session, in this process. A session belongs to one browser,
    so it never needs to be shared between workers."""

    def __init__(self) -> None:
        self._slots: dict[str, TurnSlot] = {}

    def of(self, session_id: str) -> TurnSlot:
        return self._slots.setdefault(session_id, TurnSlot())

    def forget(self, session_id: str) -> None:
        self._slots.pop(session_id, None)
