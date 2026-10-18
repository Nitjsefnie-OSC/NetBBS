"""Scheduled SysOp announcements in chat (issue #1380): the calendar, the
Save-time limits, the node's task at a real SQLite and `ChatHub` boundary,
the Link carriage after a Linked approval, and the console editor."""

from __future__ import annotations

import asyncio
import datetime
import json

import pytest

from netbbs.auth.users import create_user
from netbbs.chat.announcements import (
    GRACE,
    AnnouncementError,
    Recurrence,
    get_announcement,
    latest_slot,
    list_announcements,
    next_slot,
    nth_weekday,
    save_announcement,
    take_due,
)
from netbbs.chat.channels import create_channel, delete_channel
from netbbs.chat.hub import ChatHub, ParticipantId
from netbbs.chat.scrollback import get_scrollback, record_message
from netbbs.link.channels import link_channel
from netbbs.link.events import ChannelMessage
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.realtime_channels import _snapshot_entries
from netbbs.net.chat_announcements import AnnouncementLink, run_announcement_scheduler
from netbbs.net.chat_flow import _TimestampedNotice
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import set_display_timezone

UTC = datetime.timezone.utc
BERLIN = __import__("zoneinfo").ZoneInfo("Europe/Berlin")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    set_display_timezone(database, "Europe/Berlin")
    yield database
    database.close()


@pytest.fixture
def lane(db):
    database_lane = DatabaseLane(db.path)
    yield database_lane
    database_lane.close()


@pytest.fixture
def sysop(db):
    return create_user(db, "inkwell", password="hunter2", user_level=255)


@pytest.fixture
def channel(db, sysop):
    return create_channel(db, "main", creator=sysop)


def utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


def weekly(*days, hour=19, minute=0):
    return Recurrence(repeat="weekly", hour=hour, minute=minute, weekdays=frozenset(days))


# -- calendar ---------------------------------------------------------------


def test_nth_weekday_first_last_and_a_missing_fifth():
    assert nth_weekday(2026, 10, 1, 4) == datetime.date(2026, 10, 2)  # first Friday
    assert nth_weekday(2026, 10, 4, 4) == datetime.date(2026, 10, 23)
    assert nth_weekday(2026, 10, -1, 4) == datetime.date(2026, 10, 30)  # last Friday, a fifth one
    assert nth_weekday(2026, 11, -1, 4) == datetime.date(2026, 11, 27)  # last Friday, the fourth
    assert nth_weekday(2026, 12, -1, 3) == datetime.date(2026, 12, 31)  # December rolls the year
    assert nth_weekday(2026, 11, 5, 4) is None


def test_fifth_week_is_refused_on_save(db, sysop, channel):
    with pytest.raises(AnnouncementError, match="first, second, third, fourth or last"):
        save_announcement(db, actor=sysop, channel_name="main", text="x",
                          recurrence=Recurrence(repeat="monthly", month_week=5, weekday=4))


def test_next_slot_for_each_repeat():
    after = utc(2026, 10, 18, 12, 0)  # a Sunday, 14:00 in Berlin
    assert next_slot(Recurrence(repeat="daily"), after, BERLIN) == utc(2026, 10, 18, 17, 0)
    assert next_slot(weekly(0, 3), after, BERLIN) == utc(2026, 10, 19, 17, 0)  # Monday
    monthly = Recurrence(repeat="monthly", month_week=1, weekday=4)
    assert next_slot(monthly, after, BERLIN) == utc(2026, 11, 6, 18, 0)  # first Friday, CET by then
    once = Recurrence(repeat="once", date=datetime.date(2026, 10, 18), hour=13)
    assert next_slot(once, after, BERLIN) is None  # 13:00 already passed


def test_dst_gap_and_repeat_follow_the_backup_scheduler():
    # 02:30 does not exist on 2026-03-29 in Berlin: said at 03:30 CEST.
    spring = Recurrence(repeat="daily", hour=2, minute=30)
    gap = next_slot(spring, utc(2026, 3, 28, 12, 0), BERLIN)
    assert gap == utc(2026, 3, 29, 1, 30)
    assert gap.astimezone(BERLIN).strftime("%H:%M %Z") == "03:30 CEST"
    assert next_slot(spring, gap, BERLIN) == utc(2026, 3, 30, 0, 30)
    # 02:30 happens twice on 2026-10-25: said the first time round, once.
    autumn = Recurrence(repeat="daily", hour=2, minute=30)
    first = next_slot(autumn, utc(2026, 10, 24, 12, 0), BERLIN)
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_slot(autumn, first, BERLIN) == utc(2026, 10, 26, 1, 30)
    assert latest_slot(autumn, utc(2026, 10, 25, 1, 45), BERLIN) == first


# -- Save-time limits ---------------------------------------------------------


NOW = utc(2026, 10, 18, 12, 0)


def test_at_most_eight_per_channel(db, sysop, channel):
    for hour in range(8):
        save_announcement(db, actor=sysop, channel_name="main", text=f"n{hour}",
                          recurrence=weekly(0, hour=hour * 2), now=NOW)
    with pytest.raises(AnnouncementError, match="already has 8"):
        save_announcement(db, actor=sysop, channel_name="main", text="nine",
                          recurrence=weekly(0, hour=20), now=NOW)
    other = create_channel(db, "other", creator=sysop)
    save_announcement(db, actor=sysop, channel_name=other.name, text="fine", recurrence=weekly(0), now=NOW)


@pytest.mark.parametrize("existing,candidate,refused", [
    (Recurrence(repeat="daily", hour=19), weekly(4, hour=19, minute=30), True),
    (weekly(0, hour=19), weekly(1, hour=19, minute=30), False),
    (weekly(0, hour=23, minute=30), weekly(1, hour=0, minute=10), True),  # across midnight
    (weekly(0, hour=19), weekly(0, hour=20), False),  # exactly an hour is enough
    (Recurrence(repeat="monthly", month_week=4, weekday=4), Recurrence(repeat="monthly", month_week=-1, weekday=4,
                                                                         minute=30), True),
    (Recurrence(repeat="monthly", month_week=1, weekday=4), Recurrence(repeat="monthly", month_week=-1, weekday=4,
                                                                         minute=30), False),
    (Recurrence(repeat="monthly", month_week=1, weekday=4), weekly(4, minute=59), True),
    (Recurrence(repeat="once", date=datetime.date(2026, 10, 18), hour=13),
     Recurrence(repeat="daily", hour=13, minute=10), False),  # the once is already past
])
def test_rules_in_one_channel_stay_an_hour_apart(db, sysop, channel, existing, candidate, refused):
    if existing.repeat == "once":
        save_announcement(db, actor=sysop, channel_name="main", text="old", recurrence=existing,
                          now=utc(2026, 10, 18, 8, 0))
    else:
        save_announcement(db, actor=sysop, channel_name="main", text="old", recurrence=existing, now=NOW)
    if refused:
        with pytest.raises(AnnouncementError, match="at least an hour apart"):
            save_announcement(db, actor=sysop, channel_name="main", text="new", recurrence=candidate, now=NOW)
    else:
        save_announcement(db, actor=sysop, channel_name="main", text="new", recurrence=candidate, now=NOW)


def test_editing_a_rule_is_not_spaced_against_itself(db, sysop, channel):
    saved = save_announcement(db, actor=sysop, channel_name="main", text="a", recurrence=weekly(4), now=NOW)
    edited = save_announcement(db, actor=sysop, channel_name="main", text="b", recurrence=weekly(4, minute=15),
                               announcement_id=saved.id, now=NOW)
    assert edited.id == saved.id and edited.text == "b" and len(list_announcements(db)) == 1


def test_save_refuses_bad_input(db, sysop, channel):
    caller = create_user(db, "nib", password="hunter2", user_level=10)
    with pytest.raises(AnnouncementError, match="SysOp"):
        save_announcement(db, actor=caller, channel_name="main", text="x", recurrence=weekly(4), now=NOW)
    with pytest.raises(AnnouncementError, match="160"):
        save_announcement(db, actor=sysop, channel_name="main", text="x" * 161, recurrence=weekly(4), now=NOW)
    with pytest.raises(AnnouncementError, match="weekday"):
        save_announcement(db, actor=sysop, channel_name="main", text="x", recurrence=weekly(), now=NOW)
    with pytest.raises(AnnouncementError, match="passed"):
        save_announcement(db, actor=sysop, channel_name="main", text="x", now=NOW,
                          recurrence=Recurrence(repeat="once", date=datetime.date(2026, 10, 18), hour=9))
    with pytest.raises(AnnouncementError, match="a year ahead"):
        save_announcement(db, actor=sysop, channel_name="main", text="x", now=NOW,
                          recurrence=Recurrence(repeat="once", date=datetime.date(2028, 1, 1)))
    with pytest.raises(AnnouncementError, match="not a Linked channel"):
        save_announcement(db, actor=sysop, channel_name="main", text="x", recurrence=weekly(4), linked=True, now=NOW)
    assert list_announcements(db) == []


def test_deleting_the_channel_deletes_its_rules(db, sysop, channel):
    save_announcement(db, actor=sysop, channel_name="main", text="x", recurrence=weekly(4), now=NOW)
    delete_channel(db, channel, deleted_by=sysop)
    assert list_announcements(db) == []


# -- due slots ----------------------------------------------------------------


def test_a_slot_is_said_once_and_a_missed_one_is_skipped(db, sysop, channel):
    rule = save_announcement(db, actor=sysop, channel_name="main", text="x",
                             recurrence=Recurrence(repeat="daily", hour=19), now=NOW)
    slot = utc(2026, 10, 18, 17, 0)
    assert take_due(db, slot - datetime.timedelta(seconds=1)) == []
    assert [a.id for a in take_due(db, slot + datetime.timedelta(seconds=2))] == [rule.id]
    assert take_due(db, slot + datetime.timedelta(seconds=30)) == []
    # The node was down across the next day's slot: skipped, not said late.
    late = utc(2026, 10, 19, 17, 0) + GRACE + datetime.timedelta(seconds=1)
    assert take_due(db, late) == []
    assert get_announcement(db, rule.id).handled_until == late


def test_saving_never_fires_for_a_slot_already_past(db, sysop, channel):
    save_announcement(db, actor=sysop, channel_name="main", text="x",
                      recurrence=Recurrence(repeat="daily", hour=13, minute=59), now=NOW)
    assert take_due(db, NOW + datetime.timedelta(seconds=10)) == []


# -- the node's task ----------------------------------------------------------


class _Stop(Exception):
    pass


async def _stop(seconds):
    raise _Stop


class _Bridge:
    def __init__(self):
        self.sent = []

    async def broadcast_local_message_live(self, channel, message):
        self.sent.append((channel.name, message.body))


def _run_once(lane, hub, at, link=lambda: None):
    with pytest.raises(_Stop):
        asyncio.run(run_announcement_scheduler(lane, hub, link=link, now=lambda: at, sleep=_stop))


def test_said_as_a_local_notice_into_an_empty_channel_too(db, lane, sysop, channel):
    occupied = create_channel(db, "occupied", creator=sysop)
    save_announcement(db, actor=sysop, channel_name="main", text="Net meeting tonight at 20:00.",
                      recurrence=Recurrence(repeat="daily", hour=19), now=NOW)
    save_announcement(db, actor=sysop, channel_name="occupied", text="Hello there.",
                      recurrence=Recurrence(repeat="daily", hour=19), now=NOW)
    hub = ChatHub()
    queue = hub.join(occupied.name, ParticipantId(username="nib", session_key=1))

    _run_once(lane, hub, utc(2026, 10, 18, 17, 0, 1))

    [line] = get_scrollback(db, channel)
    assert (line.kind, line.author_label, line.body) == ("daybreak", "system",
                                                          "Announcement: Net meeting tonight at 20:00.")
    notice = queue.get_nowait()
    assert isinstance(notice, _TimestampedNotice) and "Announcement: Hello there." in notice.text
    assert [m.kind for m in get_scrollback(db, occupied)] == ["daybreak"]


def test_a_linked_channel_without_approval_keeps_it_local(db, lane, sysop, channel, tmp_path):
    identity = bootstrap_node_identity(tmp_path / "identity")
    link_channel(db, channel, node_identity=identity)
    save_announcement(db, actor=sysop, channel_name="main", text="Local only.",
                      recurrence=Recurrence(repeat="daily", hour=19), now=NOW)
    bridge = _Bridge()
    _run_once(lane, ChatHub(), utc(2026, 10, 18, 17, 0, 1),
              link=lambda: AnnouncementLink(node_identity=lambda: identity, realtime_bridge=bridge))

    [line] = get_scrollback(db, channel)
    assert line.kind == "daybreak" and line.link_event_json is None
    assert bridge.sent == []
    # Nor does a subscriber's scrollback-on-join snapshot carry it.
    record_message(db, channel, kind="message", author_label="inkwell", body="hello")
    assert [entry["kind"] for entry in _snapshot_entries(db, channel)] == ["message"]


def test_a_linked_approval_says_it_as_the_sysop_on_every_node(db, lane, sysop, channel, tmp_path):
    identity = bootstrap_node_identity(tmp_path / "identity")
    link_channel(db, channel, node_identity=identity)
    save_announcement(db, actor=sysop, channel_name="main", text="Net meeting tonight.",
                      recurrence=Recurrence(repeat="daily", hour=19), linked=True, now=NOW)
    bridge = _Bridge()
    hub = ChatHub()
    queue = hub.join(channel.name, ParticipantId(username="nib", session_key=1))
    _run_once(lane, hub, utc(2026, 10, 18, 17, 0, 1),
              link=lambda: AnnouncementLink(node_identity=lambda: identity, realtime_bridge=bridge))

    [line] = get_scrollback(db, channel)
    assert (line.kind, line.author_label, line.body) == ("message", "inkwell", "Announcement: Net meeting tonight.")
    # An ordinary signed channel_message by the SysOp's account: what any
    # node, however old, already shows as that SysOp speaking.
    event = ChannelMessage.from_dict(json.loads(line.link_event_json))
    assert event.payload["body"] == "Announcement: Net meeting tonight."
    assert event.payload["author"] == {"kind": "node_vouched_user", "home_node_fingerprint": identity.fingerprint,
                                       "local_user_id": "inkwell"}
    assert bridge.sent == [("main", "Announcement: Net meeting tonight.")]
    assert queue.get_nowait().id == line.id


@pytest.mark.parametrize("why", ["no link", "demoted"])
def test_a_linked_approval_falls_back_to_local(db, lane, sysop, channel, tmp_path, why):
    identity = bootstrap_node_identity(tmp_path / "identity")
    link_channel(db, channel, node_identity=identity)
    save_announcement(db, actor=sysop, channel_name="main", text="x",
                      recurrence=Recurrence(repeat="daily", hour=19), linked=True, now=NOW)
    link = lambda: AnnouncementLink(node_identity=lambda: identity)  # noqa: E731
    if why == "no link":
        link = lambda: None  # noqa: E731
    else:
        db.connection.execute("UPDATE users SET user_level = 10 WHERE id = ?", (sysop.id,))
        db.connection.commit()
    _run_once(lane, ChatHub(), utc(2026, 10, 18, 17, 0, 1), link=link)
    [line] = get_scrollback(db, channel)
    assert line.kind == "daybreak" and line.link_event_json is None


def test_the_task_sleeps_until_the_next_slot_at_most_its_poll(db, lane, sysop, channel):
    save_announcement(db, actor=sysop, channel_name="main", text="x",
                      recurrence=Recurrence(repeat="daily", hour=14), now=NOW - datetime.timedelta(hours=2))
    waits = []

    async def record(seconds):
        waits.append(seconds)
        raise _Stop

    for at in (utc(2026, 10, 18, 11, 59, 50), NOW - datetime.timedelta(hours=1)):
        with pytest.raises(_Stop):
            asyncio.run(run_announcement_scheduler(lane, ChatHub(), now=lambda: at, sleep=record))
    assert waits == [10.0, 30.0]


# -- console ------------------------------------------------------------------


def test_console_creates_a_weekly_rule(db, lane, sysop, channel):
    from netbbs.net.admin_announcements import announcement_editor
    from tests.test_bot_settings_ui import Screen

    session = Screen(["0", "1", "0", "1", "0", "2", "s"], lines=["Net meeting tonight at 20:00 in #main."])
    saved = asyncio.run(announcement_editor(session, lane, sysop))
    assert saved is not None
    assert (saved.channel_name, saved.text, saved.recurrence.describe(), saved.linked) == (
        "main", "Net meeting tonight at 20:00 in #main.", "weekly on Fri at 19:00", False)
    assert "Next: " in session.plain


def test_console_linked_approval_is_confirmed_on_save(db, lane, sysop, channel, tmp_path):
    from netbbs.net.admin_announcements import announcement_editor
    from tests.test_bot_settings_ui import Screen

    link_channel(db, channel, node_identity=bootstrap_node_identity(tmp_path / "identity"))
    declined = Screen(["0", "1", "0", "1", "0", "2", "0", "9", "s", "n", "b", "y"], lines=["Hi."])
    assert asyncio.run(announcement_editor(declined, lane, sysop)) is None
    assert "Linked approval was not granted" in declined.plain
    assert list_announcements(db) == []

    granted = Screen(["0", "1", "0", "1", "0", "2", "0", "9", "s", "y"], lines=["Hi."])
    saved = asyncio.run(announcement_editor(granted, lane, sysop))
    assert saved.linked and saved.linked_author_name == "inkwell"
