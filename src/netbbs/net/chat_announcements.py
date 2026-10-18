"""The node's task that says scheduled SysOp announcements (issue #1380).

`netbbs.chat.announcements` holds the rules and their slots; this says them.
It lives under `netbbs.net` for the same reason `netbbs.net.daybreak` does:
it reuses `chat_flow`'s `_TimestampedNotice` broadcast envelope.

- **Local.** The line is recorded as kind `daybreak` (muted, no speaker) and
  broadcast to everyone in the channel, recorded even when no one is there.
- **Linked, with approval.** The line is an ordinary message by the
  approving SysOp's account, queued as a signed `channel_message` and
  pushed live to subscribed peers, exactly as a line the SysOp typed. Every
  node, old or new, shows it as that SysOp speaking; nothing new crosses
  Link. Without approval, without Link running, or once that account is no
  longer a usable SysOp, it falls back to the local line.

The rules are re-read on every wake, so a change from the SysOp console (or
the standalone admin CLI, another process) is noticed within
`MAX_SLEEP_SECONDS`.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from netbbs.auth.users import get_user_by_id, is_usable_sysop
from netbbs.chat.announcements import Announcement, channel_is_linkable, earliest_next_slot, take_due
from netbbs.chat.channels import Channel, _row_to_channel
from netbbs.chat.hub import ChatHub
from netbbs.chat.scrollback import ChannelMessage, record_message
from netbbs.net.chat_flow import _TimestampedNotice
from netbbs.rendering import MUTED_COLOR, colored, sanitize_text
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane

_logger = logging.getLogger(__name__)

#: The longest the task sleeps before reading the rules again.
MAX_SLEEP_SECONDS = 30.0


@dataclass(frozen=True)
class AnnouncementLink:
    """What a Linked line needs: the signing identity (read when used, so a
    key rotation is honoured) and the live bridge, if Link runs."""

    node_identity: Callable[[], Any]
    realtime_bridge: Any = None


@dataclass(frozen=True)
class _Said:
    channel: Channel
    message: ChannelMessage
    linked: bool


def _say(db: Database, announcement: Announcement, node_identity) -> _Said | None:
    """Record one announcement; `None` if its channel has gone or is hidden."""
    row = db.connection.execute(
        "SELECT * FROM channels WHERE id = ? AND link_hidden_at IS NULL", (announcement.channel_id,),
    ).fetchone()
    if row is None:
        return None
    channel = _row_to_channel(row)
    author = None
    if announcement.linked and node_identity is not None and channel_is_linkable(db, channel):
        candidate = get_user_by_id(db, announcement.linked_author_id)
        if candidate is not None and is_usable_sysop(candidate):
            author = candidate
        else:
            _logger.warning(
                "announcement #%s in #%s stays on this node: its Linked approval's account is no "
                "longer a usable SysOp", announcement.id, channel.name,
            )
    elif announcement.linked and node_identity is None:
        _logger.info("announcement #%s in #%s stays on this node: Link is not running",
                     announcement.id, channel.name)
    if author is None:
        message = record_message(db, channel, kind="daybreak", author_label="system", body=announcement.body)
        return _Said(channel, message, linked=False)
    from netbbs.link.channels import queue_channel_message_if_linked

    message = record_message(
        db, channel, kind="message", author_label=author.username, author_fingerprint=author.fingerprint,
        body=announcement.body, author_user_id=author.id,
    )
    queue_channel_message_if_linked(db, message, channel, node_identity=node_identity)
    return _Said(channel, message, linked=True)


def _say_due(db: Database, now: datetime.datetime, node_identity) -> list[_Said]:
    said = []
    for announcement in take_due(db, now):
        try:
            result = _say(db, announcement, node_identity)
        except Exception:
            # One rule's failure costs that one line, not the others due now.
            _logger.exception("announcement #%s could not be said", announcement.id)
            continue
        if result is not None:
            said.append(result)
    return said


async def _deliver(hub: ChatHub, said: _Said, bridge) -> None:
    if said.linked:
        # Rendered per recipient, as a caller's own line is.
        await hub.broadcast(said.channel.name, said.message)
        if bridge is not None:
            await bridge.broadcast_local_message_live(said.channel, said.message)
        return
    rendered = colored(sanitize_text(said.message.body or ""), fg_color=MUTED_COLOR)
    await hub.broadcast(said.channel.name, _TimestampedNotice(rendered, said.message.created_at))


async def run_announcement_scheduler(
    lane: DatabaseLane,
    hub: ChatHub,
    *,
    link: Callable[[], AnnouncementLink | None] = lambda: None,
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_sleep: float = MAX_SLEEP_SECONDS,
) -> None:
    """Runs for the node's lifetime, owned and cancelled by `__main__.run`
    like the daybreak task. Each wake says whatever is due and sleeps until
    the next slot, or `max_sleep`, whichever is sooner. Nothing is caught up:
    a node that was down at a slot skips it (`take_due`)."""
    while True:
        current = now().astimezone(datetime.timezone.utc)
        try:
            handle = link()
            identity = handle.node_identity() if handle is not None else None
            said = await lane.run(_say_due, current, identity)
            for entry in said:
                try:
                    await _deliver(hub, entry, handle.realtime_bridge if handle is not None else None)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.exception("announcement in #%s was recorded but not delivered live", entry.channel.name)
            upcoming = await lane.run(earliest_next_slot, current)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Issue #1059's rule for daybreak: a database error costs one
            # pass, not the rest of the uptime.
            _logger.exception("Announcement scheduler pass failed; trying again shortly")
            upcoming = None
        wait = max_sleep
        if upcoming is not None:
            wait = min(max_sleep, max(0.5, (upcoming - now().astimezone(datetime.timezone.utc)).total_seconds()))
        await sleep(wait)
