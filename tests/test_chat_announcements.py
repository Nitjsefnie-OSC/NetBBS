"""Scheduled SysOp announcements in chat (issue #1380): the calendar, the
Save-time limits, the node's task at a real SQLite and `ChatHub` boundary,
the Link carriage after a channel's approval (the signed event's optional
`announcement` marker, the live frame and snapshot key), and the console."""

from __future__ import annotations

import asyncio
import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from netbbs.activity import record_channel_seen, unread_channel_count
from netbbs.auth.users import create_user
from netbbs.chat.announcements import (
    GRACE,
    AnnouncementError,
    Recurrence,
    channel_approved,
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
from netbbs.link.channels import link_channel, materialize_carried_channel_message
from netbbs.link.events import CHAT_ANNOUNCEMENTS_CAPABILITY, LINK_CAPABILITIES, ChannelMessage, build_channel_message
from netbbs.link.node_identity import bootstrap_node_identity
from netbbs.link.protocol import (
    LinkProtocolError,
    RealtimeFrame,
    build_channel_message_frame,
    build_scrollback_snapshot_frame,
    validate_realtime_frame_payload,
)
from netbbs.link.realtime_channels import _snapshot_entries
from netbbs.net.chat_announcements import AnnouncementLink, run_announcement_scheduler
from netbbs.net.chat_flow import _render_channel_message, _TimestampedNotice
from netbbs.rendering import strip_ansi
from netbbs.storage.database import Database
from netbbs.storage.execution import DatabaseLane
from netbbs.timeutil import set_display_timezone

UTC = datetime.timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=UTC)  # a Sunday, 14:00 in Berlin


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


@pytest.fixture
def identity(tmp_path):
    return bootstrap_node_identity(tmp_path / "identity")


def utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


def weekly(*days, hour=19, minute=0):
    return Recurrence(repeat="weekly", hour=hour, minute=minute, weekdays=frozenset(days))


def daily(hour=19, minute=0):
    return Recurrence(repeat="daily", hour=hour, minute=minute)


def save(db, sysop, channel, *, text="x", recurrence=None, now=NOW, **kwargs):
    return save_announcement(db, actor=sysop, channel=channel, text=text, recurrence=recurrence or daily(),
                             now=now, **kwargs)


# -- calendar ---------------------------------------------------------------


def test_nth_weekday_first_to_fourth():
    assert nth_weekday(2026, 10, 1, 4) == datetime.date(2026, 10, 2)  # first Friday
    assert nth_weekday(2026, 10, 4, 4) == datetime.date(2026, 10, 23)
    assert nth_weekday(2026, 2, 4, 5) == datetime.date(2026, 2, 28)  # every month has four


@pytest.mark.parametrize("month_week", [5, -1, 0])
def test_only_first_to_fourth_are_saved(db, sysop, channel, month_week):
    with pytest.raises(AnnouncementError, match="first, second, third or fourth"):
        save(db, sysop, channel, recurrence=Recurrence(repeat="monthly", month_week=month_week, weekday=4))


def test_next_slot_for_each_repeat():
    assert next_slot(daily(), NOW, BERLIN) == utc(2026, 10, 18, 17, 0)
    assert next_slot(weekly(0, 3), NOW, BERLIN) == utc(2026, 10, 19, 17, 0)  # Monday
    monthly = Recurrence(repeat="monthly", month_week=1, weekday=4)
    assert next_slot(monthly, NOW, BERLIN) == utc(2026, 11, 6, 18, 0)  # first Friday, CET by then
    once = Recurrence(repeat="once", date=datetime.date(2026, 10, 18), hour=13)
    assert next_slot(once, NOW, BERLIN) is None  # 13:00 already passed


def test_dst_gap_and_repeat_follow_the_backup_scheduler():
    # 02:30 does not exist on 2026-03-29 in Berlin: said at its pre-transition
    # reading, which the new clock shows as 03:30.
    spring = daily(2, 30)
    gap = next_slot(spring, utc(2026, 3, 28, 12, 0), BERLIN)
    assert gap == utc(2026, 3, 29, 1, 30)
    assert gap.astimezone(BERLIN).strftime("%H:%M %Z") == "03:30 CEST"
    assert next_slot(spring, gap, BERLIN) == utc(2026, 3, 30, 0, 30)
    # 02:30 happens twice on 2026-10-25: said the first time round, once.
    autumn = daily(2, 30)
    first = next_slot(autumn, utc(2026, 10, 24, 12, 0), BERLIN)
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_slot(autumn, first, BERLIN) == utc(2026, 10, 26, 1, 30)
    assert latest_slot(autumn, utc(2026, 10, 25, 1, 45), BERLIN) == first


# -- Save-time limits ---------------------------------------------------------


def test_at_most_eight_per_channel(db, sysop, channel):
    for hour in range(8):
        save(db, sysop, channel, text=f"n{hour}", recurrence=weekly(0, hour=hour * 2))
    with pytest.raises(AnnouncementError, match="already has 8"):
        save(db, sysop, channel, text="nine", recurrence=weekly(0, hour=20, minute=30))
    other = create_channel(db, "other", creator=sysop)
    save(db, sysop, other, text="fine", recurrence=weekly(0))


@pytest.mark.parametrize("existing,candidate,refused", [
    (daily(19), weekly(4, minute=30), True),
    (weekly(0), weekly(1, minute=30), True),  # whatever their days
    (Recurrence(repeat="monthly", month_week=1, weekday=4), weekly(2, minute=59), True),
    (weekly(0, hour=23, minute=30), weekly(1, hour=0, minute=10), True),  # round the clock
    (weekly(0), weekly(0, hour=20), False),  # exactly an hour is enough
    (daily(0, 30), daily(23, 30), False),
])
def test_times_of_day_stay_an_hour_apart(db, sysop, channel, existing, candidate, refused):
    save(db, sysop, channel, text="old", recurrence=existing)
    if refused:
        with pytest.raises(AnnouncementError, match="at least an hour apart"):
            save(db, sysop, channel, text="new", recurrence=candidate)
    else:
        save(db, sysop, channel, text="new", recurrence=candidate)


def test_a_once_already_said_holds_no_time(db, sysop, channel):
    save(db, sysop, channel, recurrence=Recurrence(repeat="once", date=datetime.date(2026, 10, 18), hour=13),
         now=utc(2026, 10, 18, 8, 0))
    save(db, sysop, channel, recurrence=daily(13, 10))


def test_editing_a_rule_is_not_spaced_against_itself(db, sysop, channel):
    saved = save(db, sysop, channel, text="a", recurrence=weekly(4))
    edited = save(db, sysop, channel, text="b", recurrence=weekly(4, minute=15), announcement_id=saved.id)
    assert edited.id == saved.id and edited.text == "b" and len(list_announcements(db)) == 1


def test_save_refuses_bad_input(db, sysop, channel):
    caller = create_user(db, "nib", password="hunter2", user_level=10)
    with pytest.raises(AnnouncementError, match="SysOp"):
        save(db, caller, channel)
    with pytest.raises(AnnouncementError, match="160"):
        save(db, sysop, channel, text="x" * 161)
    with pytest.raises(AnnouncementError, match="weekday"):
        save(db, sysop, channel, recurrence=weekly())
    with pytest.raises(AnnouncementError, match="passed"):
        save(db, sysop, channel, recurrence=Recurrence(repeat="once", date=datetime.date(2026, 10, 18), hour=9))
    with pytest.raises(AnnouncementError, match="a year ahead"):
        save(db, sysop, channel, recurrence=Recurrence(repeat="once", date=datetime.date(2028, 1, 1)))
    with pytest.raises(AnnouncementError, match="not a Linked channel"):
        save(db, sysop, channel, approve_linked=True)
    assert list_announcements(db) == []


def test_deleting_the_channel_deletes_its_rules_and_approval(db, sysop, channel, identity):
    link_channel(db, channel, node_identity=identity)
    save(db, sysop, channel, approve_linked=True)
    delete_channel(db, channel, deleted_by=sysop)
    assert list_announcements(db) == [] and not channel_approved(db, channel.id)


# -- due slots ----------------------------------------------------------------


def test_a_slot_is_said_once_and_a_missed_one_is_skipped(db, sysop, channel):
    rule = save(db, sysop, channel)
    slot = utc(2026, 10, 18, 17, 0)
    assert take_due(db, slot - datetime.timedelta(seconds=1)) == []
    assert [a.id for a in take_due(db, slot + datetime.timedelta(seconds=2))] == [rule.id]
    assert take_due(db, slot + datetime.timedelta(seconds=30)) == []
    # The node was down across the next day's slot: skipped, not said late.
    late = utc(2026, 10, 19, 17, 0) + GRACE + datetime.timedelta(seconds=1)
    assert take_due(db, late) == []
    assert get_announcement(db, rule.id).handled_until == late


def test_saving_never_fires_for_a_slot_already_past(db, sysop, channel):
    save(db, sysop, channel, recurrence=daily(13, 59))
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
        self.sent.append((channel.name, message.body, message.announcement))


def _run_once(lane, hub, at, link=lambda: None):
    with pytest.raises(_Stop):
        asyncio.run(run_announcement_scheduler(lane, hub, link=link, now=lambda: at, sleep=_stop))


SLOT = utc(2026, 10, 18, 17, 0, 1)


def test_said_as_a_local_notice_into_an_empty_channel_too(db, lane, sysop, channel):
    occupied = create_channel(db, "occupied", creator=sysop)
    save(db, sysop, channel, text="Net meeting tonight at 20:00.")
    save(db, sysop, occupied, text="Hello there.")
    hub = ChatHub()
    queue = hub.join(occupied.name, ParticipantId(username="nib", session_key=1))

    _run_once(lane, hub, SLOT)

    [line] = get_scrollback(db, channel)
    assert (line.kind, line.author_label, line.body) == ("daybreak", "system",
                                                          "Announcement: Net meeting tonight at 20:00.")
    notice = queue.get_nowait()
    assert isinstance(notice, _TimestampedNotice) and "Announcement: Hello there." in notice.text
    assert [m.kind for m in get_scrollback(db, occupied)] == ["daybreak"]


def test_a_linked_channel_without_approval_keeps_it_local(db, lane, sysop, channel, identity):
    link_channel(db, channel, node_identity=identity)
    save(db, sysop, channel, text="Local only.")
    bridge = _Bridge()
    _run_once(lane, ChatHub(), SLOT,
              link=lambda: AnnouncementLink(node_identity=lambda: identity, realtime_bridge=bridge))

    [line] = get_scrollback(db, channel)
    assert line.kind == "daybreak" and line.link_event_json is None
    assert bridge.sent == []
    # Nor does a subscriber's scrollback-on-join snapshot carry it.
    record_message(db, channel, kind="message", author_label="inkwell", body="hello")
    assert [entry["kind"] for entry in _snapshot_entries(db, channel)] == ["message"]


def test_an_approved_channel_carries_it_signed_by_the_saving_sysop(db, lane, sysop, channel, identity):
    link_channel(db, channel, node_identity=identity)
    save(db, sysop, channel, text="Net meeting tonight.", approve_linked=True)
    nib = create_user(db, "nib", password="hunter2", user_level=10)
    record_channel_seen(db, nib, channel, record_message(db, channel, kind="message", author_label="nib", body="hi"))
    bridge = _Bridge()
    hub = ChatHub()
    queue = hub.join(channel.name, ParticipantId(username="nib", session_key=1))
    _run_once(lane, hub, SLOT, link=lambda: AnnouncementLink(node_identity=lambda: identity, realtime_bridge=bridge))

    line = get_scrollback(db, channel)[-1]
    assert (line.kind, line.author_label, line.body, line.announcement) == (
        "message", "inkwell", "Announcement: Net meeting tonight.", True)
    event = ChannelMessage.from_dict(json.loads(line.link_event_json))
    assert event.payload["announcement"] is True
    assert event.payload["body"] == "Announcement: Net meeting tonight."
    assert event.payload["author"]["local_user_id"] == "inkwell"
    assert bridge.sent == [("main", "Announcement: Net meeting tonight.", True)]
    assert queue.get_nowait().id == line.id
    # Here it reads as the notice it is, not as the SysOp chatting, and it is
    # not unread activity.
    assert strip_ansi(_render_channel_message(db, channel, nib, line)).endswith("Announcement: Net meeting tonight.")
    assert unread_channel_count(db, nib, channel) == 0
    # Its snapshot entry carries the marker (stripped for a peer without it).
    marks = [e.get("announcement") for e in _snapshot_entries(db, channel)]
    assert marks == [None, True]


@pytest.mark.parametrize("why", ["no link", "demoted", "deleted"])
def test_the_carriage_lapses_to_a_local_line(db, lane, sysop, channel, identity, why):
    link_channel(db, channel, node_identity=identity)
    save(db, sysop, channel, approve_linked=True)
    link = lambda: AnnouncementLink(node_identity=lambda: identity)  # noqa: E731
    if why == "no link":
        link = lambda: None  # noqa: E731
    elif why == "demoted":
        db.connection.execute("UPDATE users SET user_level = 10 WHERE id = ?", (sysop.id,))
        db.connection.commit()
    else:
        db.connection.execute("UPDATE chat_announcements SET saved_by = NULL")
        db.connection.commit()
    _run_once(lane, ChatHub(), SLOT, link=link)
    [line] = get_scrollback(db, channel)
    assert line.kind == "daybreak" and line.link_event_json is None and not line.announcement


def test_the_task_sleeps_until_the_next_slot_at_most_its_poll(db, lane, sysop, channel):
    save(db, sysop, channel, recurrence=daily(14), now=NOW - datetime.timedelta(hours=2))
    waits = []

    async def record(seconds):
        waits.append(seconds)
        raise _Stop

    for at in (utc(2026, 10, 18, 11, 59, 50), NOW - datetime.timedelta(hours=1)):
        with pytest.raises(_Stop):
            asyncio.run(run_announcement_scheduler(lane, ChatHub(), now=lambda: at, sleep=record))
    assert waits == [10.0, 30.0]


# -- received from another node -----------------------------------------------


def _carried(db, channel, identity, tmp_path, *, announcement, user="quill"):
    remote = bootstrap_node_identity(tmp_path / "remote")
    link_channel(db, channel, node_identity=identity)
    event = build_channel_message(
        signing_identity=remote.signing_key, home_node_fingerprint=remote.fingerprint, local_user_id=user,
        channel_id=channel.channel_id, body="Announcement: Tournament at 21:00.",
        created_at="2026-10-18T17:00:00Z", announcement=announcement,
    )
    return materialize_carried_channel_message(db, event, sender_fingerprint=remote.fingerprint)


def test_a_carried_announcement_names_its_node(db, sysop, channel, identity, tmp_path):
    stored = _carried(db, channel, identity, tmp_path, announcement=True)
    assert stored.announcement
    shown = strip_ansi(_render_channel_message(db, channel, sysop, stored))
    assert "Announcement from " in shown and shown.endswith(": Tournament at 21:00.")
    assert "<" not in shown


@pytest.mark.parametrize("user,announcement", [("quill", False), ("Baud.bot", True)])
def test_without_the_marker_or_from_a_bot_it_is_a_plain_line(db, sysop, channel, identity, tmp_path, user,
                                                               announcement):
    stored = _carried(db, channel, identity, tmp_path, announcement=announcement, user=user)
    assert not stored.announcement
    assert "Announcement: Tournament" in strip_ansi(_render_channel_message(db, channel, sysop, stored))


def test_older_nodes_get_no_live_or_snapshot_key_they_would_refuse():
    assert CHAT_ANNOUNCEMENTS_CAPABILITY in LINK_CAPABILITIES
    marked = build_channel_message_frame("c", "inkwell", "inkwell", "Announcement: x", "2026-10-18T17:00:00Z",
                                         announcement=True)
    assert marked.payload["announcement"] is True
    assert "announcement" not in build_channel_message_frame("c", "u", "u", "b", "2026-10-18T17:00:00Z").payload
    bad = RealtimeFrame(type="channel_message", message_id="m1", payload={**marked.payload, "announcement": False})
    with pytest.raises(LinkProtocolError):
        validate_realtime_frame_payload(bad)
    entry = {
        "kind": "message", "author_label": "inkwell@n", "author_node_fingerprint": "n", "author_user_id": "inkwell",
        "content_id": None, "body": "Announcement: x", "body_truncated": False,
        "created_at": "2026-10-18T17:00:00Z",
    }
    build_scrollback_snapshot_frame("c", "r", [{**entry, "announcement": True}])
    with pytest.raises(LinkProtocolError):
        build_scrollback_snapshot_frame("c", "r", [{**entry, "kind": "daybreak", "announcement": True,
                                                    "author_node_fingerprint": None, "author_user_id": None}])


# -- console ------------------------------------------------------------------


def test_console_creates_a_weekly_rule(db, lane, sysop, channel):
    from netbbs.net.admin_announcements import announcement_editor
    from tests.test_bot_settings_ui import Screen

    session = Screen(["0", "1", "s"], lines=["Net meeting tonight at 20:00 in #main."])
    saved = asyncio.run(announcement_editor(session, lane, sysop, channel))
    assert saved is not None
    assert (saved.channel_name, saved.text, saved.recurrence.describe(), saved.saved_by_name) == (
        "main", "Net meeting tonight at 20:00 in #main.", "weekly on Fri at 19:00", "inkwell")
    assert "Next: " in session.plain


def test_console_approval_for_other_nodes_is_confirmed_on_save(db, lane, sysop, channel, identity):
    from netbbs.net.admin_announcements import announcement_editor
    from tests.test_bot_settings_ui import Screen

    link_channel(db, channel, node_identity=identity)
    declined = Screen(["0", "1", "0", "8", "s", "n", "b", "y"], lines=["Hi."])
    assert asyncio.run(announcement_editor(declined, lane, sysop, channel)) is None
    assert "approval for other nodes was not granted" in declined.plain
    assert list_announcements(db) == [] and not channel_approved(db, channel.id)

    granted = Screen(["0", "1", "0", "8", "s", "y"], lines=["Hi."])
    saved = asyncio.run(announcement_editor(granted, lane, sysop, channel))
    assert saved.channel_approved and channel_approved(db, channel.id)


class _Peer:
    def __init__(self, fingerprint):
        self.remote_fingerprint = fingerprint
        self.frames = []

    async def send(self, frame):
        self.frames.append(frame)


def test_the_live_line_and_snapshot_carry_the_marker_only_to_peers_that_take_it(db, lane, sysop, channel, identity):
    from netbbs.chat.presence import PresenceRegistry
    from netbbs.link.realtime_channels import LiveChannelBridge
    from netbbs.link.transport import LinkRealtimeSessionRegistry

    link_channel(db, channel, node_identity=identity)
    line = record_message(db, channel, kind="message", author_label="inkwell", body="Announcement: x",
                          announcement=True)
    newer, older = _Peer("newer"), _Peer("older")
    bridge = LiveChannelBridge(
        hub=ChatHub(), lane=lane, presence=PresenceRegistry(),
        registry=LinkRealtimeSessionRegistry(own_fingerprint=identity.fingerprint),
        announcement_capable=lambda fingerprint: fingerprint == "newer",
    )

    async def subscribers(_channel):
        return [newer, older]

    bridge._live_subscribers = subscribers

    async def run():
        await bridge.broadcast_local_message_live(channel, line)
        for peer in (newer, older):
            await bridge._send_scrollback_snapshot(peer, channel, request_id="r1")

    asyncio.run(run())
    assert newer.frames[0].payload.get("announcement") is True
    assert "announcement" not in older.frames[0].payload
    assert newer.frames[1].payload["entries"][-1].get("announcement") is True
    assert "announcement" not in older.frames[1].payload["entries"][-1]
