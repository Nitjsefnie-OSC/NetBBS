"""
Tests for the local per-node "new day" chat announcement --
`netbbs.net.daybreak`. Pure-function pieces (message
formatting, midnight scheduling math, the "which channels are
occupied" filter) are tested directly; `announce_new_day` and
`run_daybreak_announcer` are driven through a real `ChatHub`/`Database`
the same way other chat integration tests in this suite are, with
`now`/`sleep` injected on the announcer loop so nothing here actually
waits in real time (same shape as `netbbs.net.throttle.LoginThrottle`'s
own `clock` injection).
"""

from __future__ import annotations

import asyncio
import datetime

import pytest
from zoneinfo import ZoneInfo

from netbbs.auth.users import create_user
from netbbs.chat.channels import create_channel
from netbbs.chat.hub import ChatHub, ParticipantId
from netbbs.chat.scrollback import get_scrollback, record_message
from netbbs.net.chat_flow import _TimestampedNotice, _render_scrollback_message
from netbbs.net.daybreak import (
    _channels_with_participants,
    _next_offset_change,
    _ordinal_suffix,
    _seconds_until_next_local_midnight,
    announce_new_day,
    format_clock_change_message,
    format_daybreak_message,
    run_daybreak_announcer,
)
from netbbs.storage.database import Database
from netbbs.timeutil import set_display_timezone


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture
def alice(db):
    return create_user(db, "alice", password="hunter2", user_level=10)


# -- format_daybreak_message / _ordinal_suffix -------------------------


def test_message_matches_thiesis_own_worked_example():
    text = format_daybreak_message(datetime.date(2029, 4, 3))
    assert text == (
        "Good morning, chatters! A new day has just begun: where this node "
        "lives, it is now Tuesday, April 3rd, 2029."
    )


@pytest.mark.parametrize(
    "day,suffix",
    [(1, "st"), (2, "nd"), (3, "rd"), (4, "th"), (11, "th"), (12, "th"), (13, "th"),
     (21, "st"), (22, "nd"), (23, "rd"), (24, "th"), (31, "st")],
)
def test_ordinal_suffix(day, suffix):
    assert _ordinal_suffix(day) == suffix


# -- _seconds_until_next_local_midnight ---------------------------------


def test_seconds_until_midnight_just_before():
    tz = ZoneInfo("UTC")
    now = datetime.datetime(2029, 4, 3, 23, 59, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 60.0


def test_seconds_until_midnight_just_after():
    tz = ZoneInfo("UTC")
    now = datetime.datetime(2029, 4, 3, 0, 0, 1, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 86399.0


def test_seconds_until_midnight_is_always_positive_at_midnight_itself():
    """At exactly midnight, the *next* midnight is a full day away, not
    zero -- this function is only ever called right after waking from a
    sleep that already landed on the previous midnight."""
    tz = ZoneInfo("UTC")
    now = datetime.datetime(2029, 4, 3, 0, 0, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 86400.0


# -- GitHub issue #47: DST transitions must not be treated as flat 24h ------


def test_seconds_until_midnight_is_23_hours_on_spring_forward():
    """Europe/Berlin, 2026-03-29: clocks jump 02:00 -> 03:00 CEST, so
    local midnight to local midnight is only 23 real hours -- a naive
    wall-clock subtraction (or one that mishandles two aware datetimes
    sharing one `tzinfo` object) would instead return a flat 24h,
    making the announcement fire an hour late."""
    tz = ZoneInfo("Europe/Berlin")
    now = datetime.datetime(2026, 3, 29, 0, 0, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 23 * 3600.0


def test_seconds_until_midnight_is_25_hours_on_fall_back():
    """Europe/Berlin, 2026-10-25: clocks fall back 03:00 -> 02:00 CEST,
    so local midnight to local midnight is 25 real hours -- the same
    bug in the other direction would fire the announcement an hour
    early."""
    tz = ZoneInfo("Europe/Berlin")
    now = datetime.datetime(2026, 10, 25, 0, 0, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 25 * 3600.0


def test_seconds_until_midnight_partway_through_spring_forward_day():
    """Same transition day as above, but starting before the 02:00 ->
    03:00 jump rather than exactly at local midnight -- the naive
    same-clock-time delta (00:00 next day minus 01:00, i.e. 23h) is
    still wrong; the real remaining time is 22h because the missing
    hour is still ahead of this point in the day."""
    tz = ZoneInfo("Europe/Berlin")
    now = datetime.datetime(2026, 3, 29, 1, 0, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 22 * 3600.0


def test_seconds_until_midnight_partway_through_fall_back_day():
    """Before the 03:00 -> 02:00 repeat -- the naive delta (00:00 next
    day minus 01:00) would say 23h; the real remaining time is 24h
    because the extra hour is still ahead of this point in the day."""
    tz = ZoneInfo("Europe/Berlin")
    now = datetime.datetime(2026, 10, 25, 1, 0, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 24 * 3600.0


def test_seconds_until_midnight_ordinary_day_still_24_hours():
    tz = ZoneInfo("Europe/Berlin")
    now = datetime.datetime(2026, 6, 15, 0, 0, 0, tzinfo=tz)
    assert _seconds_until_next_local_midnight(now) == 86400.0


# -- _channels_with_participants -----------------------------------------


def test_channels_with_participants_excludes_empty_channels(db, alice):
    occupied = create_channel(db, "occupied", creator=alice)
    create_channel(db, "empty", creator=alice)
    hub = ChatHub()
    hub.join(occupied.name, ParticipantId(username="alice", session_key=1))

    result = _channels_with_participants(db, hub)

    assert [c.name for c in result] == ["occupied"]


def test_channels_with_participants_is_empty_when_no_one_is_anywhere(db, alice):
    create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    assert _channels_with_participants(db, hub) == []


# -- announce_new_day -----------------------------------------------------


def test_announce_new_day_broadcasts_only_to_occupied_channels(db, alice):
    occupied = create_channel(db, "occupied", creator=alice)
    create_channel(db, "empty", creator=alice)
    hub = ChatHub()
    participant = ParticipantId(username="alice", session_key=1)
    queue = hub.join(occupied.name, participant)

    asyncio.run(announce_new_day(db, hub, local_date=datetime.date(2029, 4, 3)))

    assert queue.qsize() == 1
    notice = queue.get_nowait()
    assert isinstance(notice, _TimestampedNotice)
    assert "April 3rd, 2029" in notice.text


def test_announce_new_day_records_to_scrollback(db, alice):
    channel = create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    hub.join(channel.name, ParticipantId(username="alice", session_key=1))

    asyncio.run(announce_new_day(db, hub, local_date=datetime.date(2029, 4, 3)))

    scrollback = get_scrollback(db, channel)
    assert len(scrollback) == 1
    assert scrollback[0].kind == "daybreak"
    assert "April 3rd, 2029" in scrollback[0].body


def test_announce_new_day_is_a_no_op_with_no_channels_at_all(db):
    hub = ChatHub()
    asyncio.run(announce_new_day(db, hub, local_date=datetime.date(2029, 4, 3)))  # must not raise


def test_announce_new_day_defaults_to_the_node_configured_timezone(db, alice):
    """No `local_date` override -- resolves via `get_node_timezone` and
    the real wall clock, same as every other display-facing feature in
    this codebase. Doesn't pin an exact date (that depends on whenever
    this test happens to run) -- just confirms the no-override branch
    exercises `get_node_timezone` and produces a real announcement
    rather than crashing or silently doing nothing."""
    set_display_timezone(db, "UTC")
    channel = create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    hub.join(channel.name, ParticipantId(username="alice", session_key=1))

    asyncio.run(announce_new_day(db, hub))

    scrollback = get_scrollback(db, channel)
    assert len(scrollback) == 1
    assert scrollback[0].kind == "daybreak"
    assert "Good morning, chatters!" in scrollback[0].body


# -- run_daybreak_announcer (sleep/now injected -- no real waiting) -------


def test_announcer_sleeps_until_midnight_then_announces(db, alice):
    """
    `now` is fixed (23:59 UTC, one minute to midnight) for every call --
    it doesn't need to advance, since only the *first* loop iteration's
    outcome is being observed here. What makes this test terminate
    deterministically is `fake_sleep`: it returns immediately the first
    time (letting the loop reach `announce_new_day` once), then parks
    forever on an un-set `asyncio.Event` every time after -- a real
    suspension point the scheduler can actually interleave around,
    unlike a `fake_sleep` that always returns immediately. Without
    that, `run_daybreak_announcer`'s `while True` loop never contains a
    genuine `await` suspension at all (an `asyncio.Queue.put()` that
    still has room doesn't suspend either), so it starves every other
    task in the event loop -- including this test's own polling
    coroutine and even `task.cancel()` itself, since cancellation only
    takes effect at the next suspension point -- discovered the hard
    way via a real hang, not reasoned out in advance.
    """
    channel = create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    queue = hub.join(channel.name, ParticipantId(username="alice", session_key=1))

    sleep_calls: list[float] = []
    parked = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) > 1:
            await parked.wait()

    def fake_now() -> datetime.datetime:
        return datetime.datetime(2029, 4, 3, 23, 59, 0, tzinfo=datetime.timezone.utc)

    async def scenario():
        task = asyncio.create_task(
            run_daybreak_announcer(db, hub, now=fake_now, sleep=fake_sleep)
        )
        notice = await asyncio.wait_for(queue.get(), timeout=2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return notice

    notice = asyncio.run(scenario())

    assert sleep_calls[0] == 60.0
    assert "April 4th, 2029" in notice.text  # midnight has now passed into the 4th


def test_announcer_sleep_call_reflects_dst_shortened_day(db, alice):
    """GitHub issue #47, end-to-end through the actual scheduling loop:
    on Europe/Berlin's spring-forward day, the announcer sleeps the 23
    real hours to the next midnight, not a flat 24 -- since issue #1354
    in two parts, waking at 02:00 CET to say the clocks went forward."""
    set_display_timezone(db, "Europe/Berlin")
    channel = create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    queue = hub.join(channel.name, ParticipantId(username="alice", session_key=1))

    sleep_calls: list[float] = []
    parked = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) > 2:
            await parked.wait()

    def fake_now() -> datetime.datetime:
        # 2026-03-29 00:00 CET (Europe/Berlin), expressed in UTC.
        return datetime.datetime(2026, 3, 28, 23, 0, 0, tzinfo=datetime.timezone.utc)

    async def scenario():
        task = asyncio.create_task(
            run_daybreak_announcer(db, hub, now=fake_now, sleep=fake_sleep)
        )
        notices = [await asyncio.wait_for(queue.get(), timeout=2) for _ in range(2)]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return notices

    clock, day = asyncio.run(scenario())

    assert sleep_calls[:2] == [2 * 3600.0, 21 * 3600.0]
    assert sum(sleep_calls[:2]) == 23 * 3600.0
    assert "Clocks just went forward an hour" in clock.text and "03:00 CEST" in clock.text
    assert "March 30th, 2026" in day.text


def test_announcer_stops_cleanly_on_cancellation_while_sleeping(db):
    """Cancellation while sleeping (the common case -- a real day is a
    long wait) must propagate cleanly with no special handling needed
    in `run_daybreak_announcer` itself. `db` must be real (not a bare
    stand-in) -- `get_node_timezone(db)` runs at the top of *every*
    loop iteration, before the sleep, so the loop reaches real
    database access before it ever reaches the (hanging) sleep call;
    `hub` is never actually touched, since cancellation lands before
    `announce_new_day` would ever run."""

    async def scenario():
        never_returns = asyncio.get_event_loop().create_future()

        async def hanging_sleep(seconds: float) -> None:
            await never_returns

        task = asyncio.create_task(
            run_daybreak_announcer(
                db, ChatHub(),
                now=lambda: datetime.datetime(2029, 4, 3, tzinfo=datetime.timezone.utc),
                sleep=hanging_sleep,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


# -- record_message validation --------------------------------------------


def test_record_message_requires_body_for_daybreak_kind(db, alice):
    channel = create_channel(db, "lobby", creator=alice)
    with pytest.raises(ValueError, match="body is required"):
        record_message(db, channel, kind="daybreak", author_label="system")


# -- scrollback replay rendering -------------------------------------------


def test_scrollback_replay_renders_daybreak_with_no_author(db, alice):
    channel = create_channel(db, "lobby", creator=alice)
    recorded = record_message(
        db, channel, kind="daybreak", author_label="system",
        body="Good morning, chatters! A new day has just begun.",
    )
    rendered = _render_scrollback_message(db, channel, alice, recorded)
    assert "Good morning, chatters!" in rendered
    assert "system" not in rendered
    assert "<" not in rendered  # no "<author>" tag, unlike an ordinary message


# -- issue #1354: the new year, and the clocks changing -----------------------


def test_january_first_announces_a_new_year():
    assert format_daybreak_message(datetime.date(2027, 1, 1)) == (
        "Happy New Year, chatters! A new year has just begun: where this node lives, "
        "it is now Friday, January 1st, 2027."
    )
    assert format_daybreak_message(datetime.date(2027, 1, 2)).startswith("Good morning, chatters!")


_UTC = datetime.timezone.utc


@pytest.mark.parametrize(
    ("zone", "start", "end", "change", "text"),
    [
        ("Europe/Berlin", (2026, 3, 28, 23), (2026, 3, 29, 22), (2026, 3, 29, 1),
         "Clocks just went forward an hour: where this node lives, it is now 03:00 CEST."),
        ("Europe/Berlin", (2026, 10, 24, 22), (2026, 10, 25, 23), (2026, 10, 25, 1),
         "Clocks just went back an hour: where this node lives, it is now 02:00 CET."),
        ("America/New_York", (2026, 3, 8, 5), (2026, 3, 9, 4), (2026, 3, 8, 7),
         "Clocks just went forward an hour: where this node lives, it is now 03:00 EDT."),
        ("America/New_York", (2026, 11, 1, 4), (2026, 11, 2, 5), (2026, 11, 1, 6),
         "Clocks just went back an hour: where this node lives, it is now 01:00 EST."),
    ],
)
def test_the_clock_change_is_found_to_the_second_and_described(zone, start, end, change, text):
    tz = ZoneInfo(zone)
    start_utc = datetime.datetime(*start, tzinfo=_UTC)
    found = _next_offset_change(start_utc, datetime.datetime(*end, tzinfo=_UTC), tz)
    assert found == datetime.datetime(*change, tzinfo=_UTC)
    before = start_utc.astimezone(tz).utcoffset()
    assert format_clock_change_message(before, found.astimezone(tz)) == text


def test_a_zone_without_daylight_saving_never_changes():
    tz = ZoneInfo("Asia/Tokyo")
    start = datetime.datetime(2026, 3, 1, tzinfo=_UTC)
    assert _next_offset_change(start, start + datetime.timedelta(days=1), tz) is None


def test_a_half_hour_change_says_so():
    tz = ZoneInfo("Australia/Lord_Howe")  # +10:30 standard, +11 in summer
    start = datetime.datetime(2026, 10, 3, 12, tzinfo=_UTC)
    found = _next_offset_change(start, start + datetime.timedelta(days=1), tz)
    assert found is not None
    message = format_clock_change_message(start.astimezone(tz).utcoffset(), found.astimezone(tz))
    assert message.startswith("Clocks just went forward half an hour")


def test_a_sleep_that_wakes_early_never_announces_the_same_midnight_twice(db, alice):
    channel = create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    queue = hub.join(channel.name, ParticipantId(username="alice", session_key=1))
    sleep_calls: list[float] = []
    parked = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) > 1:
            await parked.wait()

    def fake_now() -> datetime.datetime:
        # Never advances: every wake looks like a hair before midnight.
        return datetime.datetime(2029, 4, 3, 23, 59, 59, tzinfo=_UTC)

    async def scenario():
        task = asyncio.create_task(run_daybreak_announcer(db, hub, now=fake_now, sleep=fake_sleep))
        notice = await asyncio.wait_for(queue.get(), timeout=2)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return notice

    notice = asyncio.run(scenario())
    assert "April 4th" in notice.text
    assert queue.empty()
    assert sleep_calls[1] == 24 * 3600.0  # the next midnight, not the one just announced


def test_a_clock_change_at_midnight_still_announces_the_day_first(db, alice):
    # Claude review of #1355: Santiago springs forward at 00:00 local, and
    # that midnight's message was lost to the clock change.
    set_display_timezone(db, "America/Santiago")
    channel = create_channel(db, "lobby", creator=alice)
    hub = ChatHub()
    queue = hub.join(channel.name, ParticipantId(username="alice", session_key=1))
    sleep_calls: list[float] = []
    parked = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) > 1:
            await parked.wait()

    def fake_now() -> datetime.datetime:
        # 2026-09-05 23:00 -04 in Santiago; at 00:00 the clocks jump to 01:00.
        return datetime.datetime(2026, 9, 6, 3, 0, 0, tzinfo=_UTC)

    async def scenario():
        task = asyncio.create_task(run_daybreak_announcer(db, hub, now=fake_now, sleep=fake_sleep))
        notices = [await asyncio.wait_for(queue.get(), timeout=2) for _ in range(2)]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return notices

    day, clock = asyncio.run(scenario())
    assert sleep_calls[0] == 3600.0
    assert "September 6th, 2026" in day.text
    assert "Clocks just went forward an hour" in clock.text and "01:00" in clock.text
