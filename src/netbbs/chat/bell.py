"""
Audible chat bells (design doc §16, "Issue #1302 — audible chat bells").

A caller in another window should notice when chat needs them. Each caller
picks one of three levels, kept through `netbbs.user_preferences` the way
`netbbs.chat.timestamps` keeps its own:

- `off`: nothing rings;
- `private` (the default): a native `/msg` or `/private`, a Link direct
  message, an MRC private line and a `/dm` invitation ring;
- `mentions`: those, and a line that names the caller -- their username or
  their current alias, as a whole word, in any case, with or without `@`.

Whether a delivered item rings is an explicit flag set by whoever built it,
never a guess from its class. The bell itself is `\\x07` written on its own
after the line, never part of it, so it is never in a transcript or a
replay; and this module's `BellLimiter` bounds how often it can sound.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable

from netbbs.auth.users import User
from netbbs.storage.database import Database
from netbbs.user_preferences import get_user_preference, set_user_preference

_PREFERENCE_KEY = "chat_bell"

BELL_OFF = "off"
BELL_PRIVATE = "private"
BELL_MENTIONS = "mentions"
BELL_LEVELS = (BELL_OFF, BELL_PRIVATE, BELL_MENTIONS)
DEFAULT_BELL_LEVEL = BELL_PRIVATE

#: What each level means, in the words `/bell` and the Profile show.
BELL_LEVEL_LABELS = {
    BELL_OFF: "off",
    BELL_PRIVATE: "private messages",
    BELL_MENTIONS: "private messages and mentions",
}

#: The bell itself: written on its own, after the line it is for.
BEL = "\x07"

# Rate (design doc): a bucket of 3 bells per session, refilled by 1 every
# 10 seconds, and at most one bell per sender every 30 seconds.
BUCKET_SIZE = 3
REFILL_SECONDS = 10.0
SENDER_INTERVAL_SECONDS = 30.0
#: How many senders a session remembers. A sender evicted early can ring
#: again before its 30 seconds are up, but the bucket still bounds the
#: whole session; the table itself never grows past this.
SENDER_TABLE_LIMIT = 64


def bell_level(db: Database, user: User) -> str:
    value = get_user_preference(db, user, _PREFERENCE_KEY, default=DEFAULT_BELL_LEVEL)
    return value if value in BELL_LEVELS else DEFAULT_BELL_LEVEL


def set_bell_level(db: Database, user: User, level: str) -> None:
    if level not in BELL_LEVELS:
        raise ValueError(f"Chat bell level must be one of {', '.join(BELL_LEVELS)}, got {level!r}")
    set_user_preference(db, user, _PREFERENCE_KEY, level)


def rings_for_private(level: str) -> bool:
    return level in (BELL_PRIVATE, BELL_MENTIONS)


def rings_for_mentions(level: str) -> bool:
    return level == BELL_MENTIONS


def mentions_any(text: str, names: Iterable[str | None]) -> bool:
    """Whether `text` names any of `names` as a whole word, in any case.

    "Whole word" means no letter, digit or underscore touches the name on
    either side, so `@bob`, `bob:` and `Bob,` all name bob and `bobby` does
    not. An `@` in front is just another non-word character."""
    for name in names:
        if not name or not name.strip():
            continue
        pattern = rf"(?<!\w){re.escape(name.strip())}(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


class BellLimiter:
    """One session's bell budget: a bucket of `BUCKET_SIZE`, refilled by one
    every `REFILL_SECONDS`, and one bell per sender every
    `SENDER_INTERVAL_SECONDS`, tracked in a table of at most
    `SENDER_TABLE_LIMIT` senders."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tokens = float(BUCKET_SIZE)
        self._refilled_at = clock()
        self._senders: OrderedDict[str, float] = OrderedDict()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._refilled_at)
        self._tokens = min(float(BUCKET_SIZE), self._tokens + elapsed / REFILL_SECONDS)
        self._refilled_at = now

    def allow(self, sender: str) -> bool:
        """Whether a bell from `sender` may sound now; if so, it is spent."""
        now = self._clock()
        self._refill(now)
        last = self._senders.get(sender)
        if last is not None and now - last < SENDER_INTERVAL_SECONDS:
            return False
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        self._senders.pop(sender, None)
        self._senders[sender] = now
        while len(self._senders) > SENDER_TABLE_LIMIT:
            self._senders.popitem(last=False)
        return True

    @property
    def tracked_senders(self) -> int:
        return len(self._senders)


def session_bell_limiter(session: object) -> BellLimiter:
    """The limiter kept on `session`, made on first use: one budget per
    connection, across every chat screen it visits."""
    limiter = getattr(session, "chat_bell_limiter", None)
    if limiter is None:
        limiter = BellLimiter()
        setattr(session, "chat_bell_limiter", limiter)
    return limiter
