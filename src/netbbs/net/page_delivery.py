"""Telling online SysOps and staff that a caller paged (decision 1377).

The page itself is stored first (`netbbs.staff_pages`); this only says so to
whoever is online, following the mail-arrival rule of issue #823
(`netbbs.net.mail_arrivals`) exactly:

- Chat and the SysOp's live monitor install `Session.pinned_notice_hook`;
  the line goes through it at once (`ActiveSessionRegistry.notify_one`),
  and rings chat's bell at the private-message level.
- Every other screen gets the line queued (`netbbs.net.notices.announce`)
  for the next screen it draws; an idle main menu is woken to redraw at
  once, with its "page waiting" line.
- A door or an editor draws no notices, so the line waits until the person
  leaves it. Nothing is ever written into a door's screen.

The node log and the Monitor's event tail record the caller and the place,
never the reason (decision 0761: "places, never content").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from netbbs.net.mail_arrivals import arrival_event
from netbbs.net.notices import announce
from netbbs.net.session_registry import ActiveSessionRegistry
from netbbs.rendering import WARNING_COLOR, colored, sanitize_text

_logger = logging.getLogger(__name__)

#: How a recipient heard: shown at once, queued for their next screen, or
#: waiting until they leave a door.
NOW, NEXT_SCREEN, IN_DOOR = "now", "next screen", "in a door"
_RANK = {NOW: 0, NEXT_SCREEN: 1, IN_DOOR: 2}


@dataclass(frozen=True)
class Recipient:
    username: str
    #: Where they work the queue: "SysOp" or "Staff".
    console: str
    unicode_style: bool


def page_notice(caller: str, place: str, reason: str, *, console: str, unicode_style: bool) -> str:
    """`! Page from Nib (Chat › Lobby): ... -- SysOp → Pages`, plain text."""
    arrow = "→" if unicode_style else "->"
    shown_place = place if unicode_style else place.replace("›", ">")
    return (
        f"! Page from {sanitize_text(caller)} ({sanitize_text(shown_place)}): {sanitize_text(reason)} "
        f"-- {console} {arrow} Pages"
    )


def online(registry: ActiveSessionRegistry | None, recipients: list[Recipient]) -> list[Recipient]:
    """The recipients with a live session on this node."""
    if registry is None:
        return []
    return [person for person in recipients if registry.sessions_for_username(person.username)]


async def deliver_page(
    registry: ActiveSessionRegistry | None, recipients: list[Recipient], *, caller: str, place: str, reason: str,
) -> dict[str, str]:
    """Tell every online recipient, on every session they have. Returns how
    each one heard (`NOW`, `NEXT_SCREEN`, `IN_DOOR`), by username, at its
    best across their sessions; an offline recipient is absent."""
    if registry is not None:
        registry.note_event(f"page: {caller} ({place})")
    _logger.info("page: %s (%s)", caller, place)
    heard: dict[str, str] = {}
    if registry is None:
        return heard
    for person in recipients:
        text = page_notice(caller, place, reason, console=person.console, unicode_style=person.unicode_style)
        for session in registry.sessions_for_username(person.username):
            hook = getattr(session, "pinned_notice_hook", None)
            if getattr(session, "door_active", False):
                announce(session, text, color=WARNING_COLOR)
                how = IN_DOOR
            elif hook is not None and await registry.notify_one(
                session, colored(text, fg_color=WARNING_COLOR), ring=f"page:{caller}",
            ):
                how = NOW
            else:
                announce(session, text, color=WARNING_COLOR)
                # The main menu races this against its key read and redraws
                # (`netbbs.net.mail_arrivals`), so an idle menu shows the
                # notice and its "page waiting" line at once.
                event = arrival_event(session)
                if event is not None:
                    event.set()
                how = NEXT_SCREEN
            best = heard.get(person.username)
            if best is None or _RANK[how] < _RANK[best]:
                heard[person.username] = how
    return heard
