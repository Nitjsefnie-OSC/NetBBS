"""
Where a session is: the trail of screens that led to the one on screen
(issue #1246).

Every screen's breadcrumb is built here from that trail, never typed at
the call site. The literal paths it replaced had drifted apart (284 call
sites, 60-odd spellings, a third with no path at all), and a screen
reached from two places could only be right for one of them.

A screen that leads to further screens names itself while it runs, with
`@located("Outbox")` or `with entered(session, name):` for a name only
known at run time (a board, a user). Its own frame is drawn with
`own=True` (ancestors only, its title last); anything drawn inside it --
a picker, an editor, a detail screen -- draws as its child. A screen that
leads nowhere names nothing and draws as a child of whatever opened it.
The node's name is always the first segment.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from netbbs.net.session import Session

F = TypeVar("F", bound=Callable[..., Any])


@contextmanager
def entered(session: Session, name: str) -> Iterator[None]:
    """Add `name` to the trail while the block runs, and restore the trail
    on every way out, an exception or a cancellation included."""
    before = _trail(session)
    session.location = (*before, name)
    try:
        yield
    finally:
        session.location = before


def located(name: str) -> Callable[[F], F]:
    """Decorate an async screen whose first argument is the session, so it
    runs `entered(session, name)`."""

    def decorate(screen: F) -> F:
        @functools.wraps(screen)
        async def run(session: Session, *args: Any, **kwargs: Any) -> Any:
            with entered(session, name):
                return await screen(session, *args, **kwargs)

        return run  # type: ignore[return-value]

    return decorate


def crumbs(session: Session, *, own: bool = False) -> tuple[str, ...]:
    """The breadcrumb for a screen drawn now: the node's name, then the
    trail. `own` is for the frame of the screen that last named itself,
    which leaves its own name off (it is the title)."""
    trail = _trail(session)
    return (session.node_display_name, *(trail[:-1] if own else trail))


def _trail(session: Session) -> tuple[str, ...]:
    # A stand-in session that is not a `Session` subclass (most test fakes)
    # has no class default to fall back on.
    return getattr(session, "location", ())
