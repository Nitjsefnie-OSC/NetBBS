"""
Local per-node "new day" chat announcement (design doc).

Once per local midnight (the node's configured display timezone --
`netbbs.timeutil.get_node_timezone`), broadcasts a short system message
into every channel that currently has at least one live participant --
on January 1st a New Year one (issue #1354), and, at the moment that
timezone's clocks go forward or back, one saying so --
deliberately *not* into empty channels, so a node with dozens of
dormant channels doesn't spam scrollback nobody is even present to
read.

Deliberately, permanently local-only: NetBBS Link (Phase 3, not
started -- see CLAUDE.md/design doc §15) has no concept of "this
node's local midnight" that would mean anything coherent federated
across nodes sitting in different time zones, so this never crosses a
Link channel's node boundary. That question was raised and answered
explicitly in the design doc before any of this was built, not an
oversight discovered later.

Lives under `netbbs.net`, not `netbbs.chat`, specifically so it can
reuse `netbbs.net.chat_flow`'s existing `_TimestampedNotice` broadcast
envelope -- the same one join/leave/chat messages already use to reach
`_chat_loop`'s `receive_loop` -- without a circular import:
`netbbs.chat` is `chat_flow`'s own dependency, never the reverse.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Awaitable, Callable

from netbbs.chat import Channel, ChatHub, list_channels, record_message
from netbbs.net.chat_flow import _TimestampedNotice
from netbbs.rendering import MUTED_COLOR, colored, sanitize_text
from netbbs.storage.database import Database
from netbbs.timeutil import get_node_timezone

_logger = logging.getLogger(__name__)

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}


def _ordinal_suffix(day: int) -> str:
    # 11th/12th/13th are the standard English exception to the
    # last-digit rule (not "11st"/"12nd"/"13rd").
    if 11 <= day % 100 <= 13:
        return "th"
    return _ORDINAL_SUFFIXES.get(day % 10, "th")


def format_daybreak_message(local_date: datetime.date) -> str:
    """The announcement text for `local_date` -- e.g. "Good morning,
    chatters! A new day has just begun: where this node lives, it is
    now Monday, April 3rd, 2029." (Thiesi's own example, matched
    exactly). January 1st says a new year has begun instead (issue
    #1354)."""
    weekday_and_month = local_date.strftime("%A, %B")
    day = local_date.day
    when = f"where this node lives, it is now {weekday_and_month} {day}{_ordinal_suffix(day)}, {local_date.year}."
    if (local_date.month, local_date.day) == (1, 1):
        return f"Happy New Year, chatters! A new year has just begun: {when}"
    return f"Good morning, chatters! A new day has just begun: {when}"


def _amount(seconds: int) -> str:
    if seconds == 3600:
        return "an hour"
    if seconds == 1800:
        return "half an hour"
    hours, minutes = divmod(seconds // 60, 60)
    if not minutes:
        return f"{hours} hours"
    return f"{seconds // 60} minutes"


def format_clock_change_message(before: datetime.timedelta, after_local: datetime.datetime) -> str:
    """The announcement for the node's clocks changing from UTC offset
    `before` to `after_local`'s, at `after_local` -- e.g. "Clocks just
    went forward an hour: where this node lives, it is now 03:00 CEST."
    Direction and amount come from the offsets and the abbreviation from
    the tz data; no "summer time" words, which tzdata reverses for a few
    zones (issue #1354)."""
    after = after_local.utcoffset() or datetime.timedelta(0)
    change = int((after - before).total_seconds())
    direction = "forward" if change > 0 else "back"
    zone = after_local.tzname() or ""
    return (
        f"Clocks just went {direction} {_amount(abs(change))}: where this node lives, "
        f"it is now {after_local:%H:%M}{f' {zone}' if zone else ''}."
    )


def _next_offset_change(
    start: datetime.datetime, end: datetime.datetime, tz: datetime.tzinfo,
) -> datetime.datetime | None:
    """The first moment after `start` and no later than `end` (both UTC)
    at which `tz`'s UTC offset differs from its offset at `start`, to the
    second, or `None`. A zone changes its offset at most once in the day
    this is asked about, so comparing the two ends is enough to know."""
    before = start.astimezone(tz).utcoffset()
    if end.astimezone(tz).utcoffset() == before:
        return None
    low, high = int(start.timestamp()), int(end.timestamp())
    while high - low > 1:
        middle = (low + high) // 2
        moment = datetime.datetime.fromtimestamp(middle, datetime.timezone.utc)
        if moment.astimezone(tz).utcoffset() == before:
            low = middle
        else:
            high = middle
    return datetime.datetime.fromtimestamp(high, datetime.timezone.utc)


def _seconds_until_next_local_midnight(current_local: datetime.datetime) -> float:
    """
    `current_local` must already be timezone-aware, in the timezone to
    schedule against.

    Constructs the target midnight directly with that same `tzinfo`
    (rather than just adding a fixed 24-hour `timedelta`) so the
    *target* is the correct wall-clock date across a DST transition --
    `zoneinfo.ZoneInfo` resolves the correct UTC offset for that target
    date when the `datetime` is constructed, not whatever offset was in
    effect a fixed number of seconds ago.

    The elapsed-time calculation itself (GitHub issue #47) then
    converts both ends to UTC before subtracting, rather than
    subtracting the two aware datetimes directly. `next_midnight` and
    `current_local` carry the exact same `tzinfo` *object* (`ZoneInfo`
    instances compare identical when constructed from the same key),
    and CPython's `datetime.__sub__` special-cases that: when both
    operands share one `tzinfo` object, it assumes their UTC offsets
    are equal and subtracts naive wall-clock fields directly, skipping
    `utcoffset()` entirely. That assumption fails exactly on a DST
    transition day, silently returning a flat 24 hours for a day that
    is really 23 (spring-forward) or 25 (fall-back) hours long.
    Explicit `astimezone(UTC)` on both sides forces the real elapsed
    duration to be used instead.
    """
    next_day = current_local.date() + datetime.timedelta(days=1)
    next_midnight = datetime.datetime(
        next_day.year, next_day.month, next_day.day, tzinfo=current_local.tzinfo
    )
    return (
        next_midnight.astimezone(datetime.timezone.utc)
        - current_local.astimezone(datetime.timezone.utc)
    ).total_seconds()


def _channels_with_participants(db: Database, hub: ChatHub) -> list[Channel]:
    """Every channel currently hosting at least one live participant --
    `ChatHub` exposes no "every channel with someone in it" query
    directly, so this cross-references every existing channel
    (`list_channels`) against `hub.participant_count` per name, the
    same pattern `netbbs.net.chat_flow._channel_names_for_user` already
    uses for its own per-channel hub lookups."""
    return [channel for channel in list_channels(db) if hub.participant_count(channel.name) > 0]


async def announce_new_day(db: Database, hub: ChatHub, *, local_date: datetime.date | None = None) -> None:
    """
    Broadcast (and record to scrollback) one daybreak announcement to
    every currently-occupied channel.

    `local_date` is normally left to resolve itself from
    `get_node_timezone` -- overridable so a test can pass a fixed date
    without needing a real node-config round trip. `record_message`
    stores the raw announcement text (not pre-colored), matching every
    other kind's convention -- sanitizing/coloring happens at each
    render site independently (here for the live broadcast,
    `netbbs.net.chat_flow._render_scrollback_message`'s own `"daybreak"`
    branch for replay), not baked into the stored row.
    """
    if local_date is None:
        local_date = datetime.datetime.now(get_node_timezone(db)).date()
    await _announce(db, hub, format_daybreak_message(local_date))


async def announce_clock_change(
    db: Database, hub: ChatHub, *, before: datetime.timedelta, after_local: datetime.datetime,
) -> None:
    """Broadcast and record that the node's clocks just changed (issue
    #1354), the same way and to the same channels as the daybreak."""
    await _announce(db, hub, format_clock_change_message(before, after_local))


async def _announce(db: Database, hub: ChatHub, text: str) -> None:
    rendered = colored(sanitize_text(text), fg_color=MUTED_COLOR)
    for channel in _channels_with_participants(db, hub):
        recorded = record_message(db, channel, kind="daybreak", author_label="system", body=text)
        await hub.broadcast(channel.name, _TimestampedNotice(rendered, recorded.created_at))


async def run_daybreak_announcer(
    db: Database,
    hub: ChatHub,
    *,
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """
    Runs for the node's lifetime -- constructed in `netbbs.__main__.run`
    alongside `ChatHub` itself, cancelled in that function's existing
    `finally` block the same way every listener is already stopped
    there. Sleeps until the next local midnight, announces, repeats;
    cancellation while sleeping (the common case, since a real day is a
    long wait) propagates immediately and cleanly through `asyncio.
    sleep`, needing no explicit `except asyncio.CancelledError` here.

    `now`/`sleep` are injectable so a test can drive this without a
    real wait -- the same dependency-injection shape
    `netbbs.net.throttle.LoginThrottle`'s own `clock` parameter already
    uses, for the identical reason.

    The date `announce_new_day` announces is computed here, from the
    same `current_local` just used to schedule the sleep, and passed
    through explicitly (`local_date=...`) rather than left for
    `announce_new_day` to re-resolve via its own fresh `now()` call
    after waking -- in production the two would almost always agree
    anyway (the sleep just landed on that exact moment), but re-
    querying real wall-clock time a second time is both an unnecessary
    second source of truth and untestable with a fake `now` that
    doesn't actually advance.
    """
    # The moment last announced: a sleep that wakes a hair early must not
    # find the same midnight, or clock change, ahead of it again.
    floor: datetime.datetime | None = None
    while True:
        # Issue #1059: a database error (a lock held by another connection,
        # most likely) costs one announcement, not the rest of the uptime.
        try:
            tz = get_node_timezone(db)
            current = now().astimezone(datetime.timezone.utc)
            if floor is not None and current < floor:
                current = floor
            current_local = current.astimezone(tz)
        except Exception:
            _logger.exception("Daybreak announcer could not read the node's time zone; trying again in a minute")
            await sleep(60)
            continue
        until_midnight = _seconds_until_next_local_midnight(current_local)
        midnight = current + datetime.timedelta(seconds=until_midnight)
        # Issue #1354: the clocks changing before then is announced first.
        change = _next_offset_change(current, midnight, tz)
        # A zone that changes at midnight itself (Santiago, Havana) still has
        # that midnight announced, the change right after it (Claude review
        # of #1355: the day's message was lost).
        at_midnight = change is not None and change >= midnight
        if change is not None and not at_midnight:
            await sleep((change - current).total_seconds())
            floor = change
            try:
                await announce_clock_change(
                    db, hub, before=current_local.utcoffset() or datetime.timedelta(0),
                    after_local=change.astimezone(tz),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("Clock change announcement for %s failed", change)
            continue
        await sleep(until_midnight)
        floor = midnight
        next_local_date = current_local.date() + datetime.timedelta(days=1)
        try:
            await announce_new_day(db, hub, local_date=next_local_date)
            if at_midnight:
                await announce_clock_change(
                    db, hub, before=current_local.utcoffset() or datetime.timedelta(0),
                    after_local=midnight.astimezone(tz),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("Daybreak announcement for %s failed", next_local_date)
